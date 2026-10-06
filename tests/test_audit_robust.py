"""0.37.2 (audit LRA-005/006/008/010/027): one odd file never stops a pass
or turns into wrong data."""
import pytest
from PIL import Image

from lunelis.importers import metadata, takeout
from lunelis.raw import thumbnails


def test_a_deeply_nested_takeout_json_is_skipped(tmp_path):
    p = tmp_path / "x.jpg.json"
    p.write_text("[" * 100_000 + "]" * 100_000, encoding="utf-8")
    assert takeout._parse(str(p)) is None


@pytest.mark.filterwarnings("ignore::PIL.Image.DecompressionBombWarning")
def test_an_image_bigger_than_the_cap_is_refused_before_decoding(tmp_path, monkeypatch):
    p = tmp_path / "big.png"
    Image.new("L", (300, 300)).save(p)
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 50_000)
    with pytest.raises(ValueError):
        thumbnails.render(str(p))


def test_a_big_image_still_gets_a_thumbnail(tmp_path, monkeypatch):
    p = tmp_path / "big.png"
    Image.new("RGB", (900, 600), (10, 200, 30)).save(p)
    monkeypatch.setattr(thumbnails, "BIG_PIXELS", 1000)
    assert max(thumbnails.render(str(p)).size) == thumbnails.THUMB_EDGE


@pytest.mark.parametrize("lat, lon, ok", [(51.5, -0.12, True), (999, 10, False), (10, 4.4e9, False),
                                          (0, 0, False), (-90, 180, True), (None, 5, False)])
def test_gps_off_the_globe_is_dropped(lat, lon, ok):
    assert (metadata.valid_gps(lat, lon) == (lat, lon)) is ok


class _T:
    def __init__(self, v):
        self.printable = v
        self.values = v

    def __str__(self):
        return self.printable


@pytest.mark.parametrize("raw, want", [("2024:02:28 10:00:00", "2024-02-28T10:00:00"),
                                       ("2024:02:30 10:00:00", None), ("2024:01:01 99:99:99", None)])
def test_impossible_dates_are_dropped(raw, want):
    assert metadata._datetime({"EXIF DateTimeOriginal": _T(raw)}) == want


def test_resetting_an_unreadable_photo_keeps_its_edited_thumbnail(tmp_path):
    from lunelis.edit import render
    thumbs, edits = tmp_path / "t", tmp_path / "e"
    proxy = render.proxy_path(edits, 7)
    proxy.parent.mkdir(parents=True)
    proxy.write_bytes(b"proxy")
    with pytest.raises(Exception):
        render.clear_outputs(str(tmp_path / "gone.jpg"), 7, None, thumbs, edits)
    assert proxy.exists()


def test_the_recycle_bin_check_and_the_real_recycle(tmp_path):
    # audit LRA-011: the primitive itself, not a mock
    import sys
    from lunelis import paths
    from lunelis.dupes import manage
    assert not paths.has_recycle_bin("//server/share/x.jpg")
    if sys.platform != "win32" or not paths.has_recycle_bin(tmp_path):
        pytest.skip("needs a fixed NTFS drive")
    p = tmp_path / "recycle-me.txt"
    p.write_text("Lunelis test file", encoding="utf-8")
    manage._recycle(str(p))
    assert not p.exists()


def test_files_on_a_share_that_went_away_stay_pending(tmp_path):
    # audit LRA-056
    import shutil
    from lunelis.catalog.schema import open_catalog
    from lunelis.importers.scan import add_root, scan_root
    root = tmp_path / "share"
    root.mkdir()
    Image.new("RGB", (40, 30)).save(root / "a.jpg")
    conn = open_catalog(tmp_path / "cat.db")
    scan_root(conn, add_root(conn, root))
    shutil.rmtree(root)                                   # the NAS drops mid-pass
    r = metadata.extract_pending(conn)
    assert r.offline == 1 and r.failed == 0 and metadata.pending_count(conn) == 1
    t = thumbnails.generate_pending(conn, tmp_path / "thumbs")
    assert t.offline == 1 and t.failed == 0
    assert conn.execute("SELECT thumb_error FROM files").fetchone()[0] is None
    conn.close()
