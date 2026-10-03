"""Step 10: video metadata + poster frames, and Google Takeout JSON dates/GPS."""
import json
import os
from datetime import datetime, timezone
from fractions import Fraction

import av
import piexif
import pytest
from PIL import Image

from lunelis.catalog.schema import open_catalog
from lunelis.importers.metadata import extract_pending
from lunelis.importers.scan import add_root, scan_root
from lunelis.importers.takeout import import_root, takeout_roots
from lunelis.raw.thumbnails import generate_pending, render


def make_mp4(path, seconds=2, fps=10, created="2024-10-11T20:05:45Z", size=(160, 96)):
    """A real little MP4: top half red, bottom half blue."""
    with av.open(str(path), "w") as out:
        out.metadata["creation_time"] = created
        s = out.add_stream("mpeg4", rate=fps)
        s.width, s.height, s.pix_fmt = size[0], size[1], "yuv420p"
        img = Image.new("RGB", size, (220, 30, 30))
        img.paste((30, 30, 220), (0, size[1] // 2, size[0], size[1]))
        for i in range(seconds * fps):
            frame = av.VideoFrame.from_image(img)
            frame.pts, frame.time_base = i, Fraction(1, fps)
            for packet in s.encode(frame):
                out.mux(packet)
        for packet in s.encode():
            out.mux(packet)


# --- video ---------------------------------------------------------------------

def test_video_metadata_and_poster_frame(tmp_path):
    root = tmp_path / "Clips"
    root.mkdir()
    make_mp4(root / "C0001.MP4")
    conn = open_catalog(tmp_path / "c.db")
    rid = add_root(conn, root)
    scan_root(conn, rid)
    extract_pending(conn)
    at, off, dur, src, w, h, err = conn.execute(
        "SELECT captured_at, captured_offset, duration_s, date_source, width_px, height_px, read_error"
        " FROM exif").fetchone()
    assert err is None and src == "video" and (w, h) == (160, 96)
    local = datetime(2024, 10, 11, 20, 5, 45, tzinfo=timezone.utc).astimezone()
    assert at == local.replace(tzinfo=None).isoformat(timespec="seconds")   # UTC -> PC local
    assert dur == pytest.approx(2.0, abs=0.3)

    img = render(str(root / "C0001.MP4"))
    assert img.size == (160, 96)
    assert img.getpixel((80, 10))[0] > 150 and img.getpixel((80, 85))[2] > 150   # upright
    r = generate_pending(conn, tmp_path / "thumbs")
    assert (r.made, r.failed) == (1, 0)
    conn.close()


def test_video_without_creation_time_has_no_date(tmp_path):
    p = tmp_path / "x.mp4"
    make_mp4(p, created="1904-01-01T00:00:00Z")                   # QuickTime's "unset" epoch
    from lunelis.importers.video import probe
    with open(p, "rb") as fh:
        assert probe(fh)["captured_at"] is None


# --- Google Takeout ------------------------------------------------------------------

def _jpeg(path, exif_date=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    kw = {}
    if exif_date:
        kw["exif"] = piexif.dump({"0th": {}, "Exif": {piexif.ExifIFD.DateTimeOriginal: exif_date.encode()}})
    Image.new("RGB", (40, 30), (100, 100, 100)).save(path, "JPEG", **kw)


def _json(folder, json_name, title, taken, lat=0.0, lon=0.0, description=""):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / json_name).write_text(json.dumps({
        "title": title, "description": description,
        "photoTakenTime": {"timestamp": str(taken)},
        "geoData": {"latitude": lat, "longitude": lon},
    }), encoding="utf-8")


@pytest.fixture
def takeout(tmp_path):
    root = tmp_path / "Google Takeout 9-12-2026" / "Takeout-merged"
    photos, js = root / "Library/Photos", root / "Library/Photos/_json"
    t2019 = int(datetime(2019, 7, 20, 3, 5, tzinfo=timezone.utc).timestamp())
    t2020 = int(datetime(2020, 1, 15, 17, 0, tzinfo=timezone.utc).timestamp())
    _jpeg(photos / "2019/07/_JCS1890-1.jpg")
    _jpeg(photos / "2019/07/_JCS1890-1(2).jpg")
    _jpeg(photos / "2019/07/IMG_1.JPG")
    _jpeg(photos / "2020/01/IMG_1.JPG")                         # same name, another month
    _jpeg(photos / "2020/01/Snap-edited.jpg")
    _jpeg(photos / "2020/01/Snap.jpg")
    _jpeg(photos / "2020/01/HasExif.jpg", exif_date="2020:01:01 09:00:00")
    _json(js / "Lantern festival", "_JCS1890-1.jpg.supplemental-metadata.json", "_JCS1890-1.jpg", t2019)
    _json(js / "Lantern festival", "_JCS1890-1.jpg.supplemental-metadata(2).json", "_JCS1890-1.jpg",
          t2019 + 60, lat=41.88, lon=-87.63, description="lanterns")
    _json(js / "Photos from 2019", "_JCS1890-1.jpg.supplemental-metadata.json", "_JCS1890-1.jpg", t2019)
    _json(js / "Photos from 2020", "IMG_1.JPG.supplemental-metadata.json", "IMG_1.JPG", t2020)
    _json(js / "Photos from 2020", "Snap.jpg.supplemental-metadata.json", "Snap.jpg", t2020 + 5)
    _json(js / "Photos from 2020", "HasExif.jpg.supplemental-metadata.json", "HasExif.jpg", t2020)
    conn = open_catalog(tmp_path / "c.db")
    rid = add_root(conn, root.parent)
    scan_root(conn, rid)
    extract_pending(conn)
    yield conn, rid, t2019, t2020
    conn.close()


def _exif(conn, rel_end):
    return conn.execute("SELECT e.captured_at, e.date_source, e.gps_lat FROM exif e JOIN files f"
                        " ON f.id = e.file_id WHERE f.rel_path LIKE ?", ("%" + rel_end,)).fetchone()


def _local(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().replace(tzinfo=None).isoformat(timespec="seconds")


def test_takeout_root_is_detected(takeout):
    conn, rid, _, _ = takeout
    assert takeout_roots(conn) == [rid]


def test_takeout_dates_and_gps(takeout):
    conn, rid, t2019, t2020 = takeout
    r = import_root(conn, rid)
    assert r.json_files == 6
    assert tuple(_exif(conn, "2019/07/_JCS1890-1.jpg")) == (_local(t2019), "takeout", None)
    at, src, lat = _exif(conn, "2019/07/_JCS1890-1(2).jpg")               # the (2) JSON
    assert (at, src, lat) == (_local(t2019 + 60), "takeout", 41.88)
    assert _exif(conn, "2020/01/IMG_1.JPG")[0] == _local(t2020)            # month decided it
    assert _exif(conn, "2019/07/IMG_1.JPG")[0] is None                     # no JSON of its own
    assert _exif(conn, "2020/01/Snap-edited.jpg")[0] == _local(t2020 + 5)  # inherits Snap.jpg's
    assert tuple(_exif(conn, "HasExif.jpg"))[:2] == ("2020-01-01T09:00:00", "exif")   # never overridden
    desc = conn.execute("SELECT description FROM takeout_meta t JOIN files f ON f.id = t.file_id"
                        " WHERE f.rel_path LIKE '%(2).jpg'").fetchone()[0]
    assert desc == "lanterns"
    assert import_root(conn, rid).dated == 0                               # idempotent


def test_non_takeout_root_is_not_touched(tmp_path):
    root = tmp_path / "Photos"
    _jpeg(root / "a.jpg")
    conn = open_catalog(tmp_path / "c.db")
    add_root(conn, root)
    assert takeout_roots(conn) == []
    conn.close()


def test_undated_takeout_files_sort_last_not_first(takeout):
    """Takeout files carry the unzip date as mtime; with no real date they
    must sort as undated (last), not as the newest photos."""
    from lunelis.ui.library import LibraryIndex
    conn, rid, _, _ = takeout
    import_root(conn, rid)
    idx = LibraryIndex()
    idx.load(conn, "date_desc")
    names = [conn.execute("SELECT rel_path FROM files WHERE id = ?", (r[0],)).fetchone()[0] for r in idx.rows]
    assert names[-1].endswith("2019/07/IMG_1.JPG")            # the one file with no date at all
    assert idx.rows[-1][7] is None and idx.rows[0][7] is not None
