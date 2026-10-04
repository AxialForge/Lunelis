"""create/batch.py: resize, convert, watermark, rename, strip metadata - to new files."""
import pytest
from PIL import Image

from lunelis.catalog.schema import open_catalog
from lunelis.create import batch, engine
from lunelis.importers.scan import add_root, scan_root


@pytest.fixture
def photos(tmp_path):
    conn = open_catalog(tmp_path / "c.db")
    root = tmp_path / "Photos"
    root.mkdir()
    for i in range(3):
        Image.new("RGB", (1200, 800), (90, 90 + 40 * i, 120)).save(root / f"DSC0{i}.jpg", quality=95)
    rid = add_root(conn, root)
    scan_root(conn, rid)
    conn.execute("INSERT OR REPLACE INTO exif (file_id, camera_make, camera_model, gps_lat, gps_lon, captured_at)"
                 " SELECT id, 'Sony', 'ILCE-7RM5', 41.2, -81.4, '2026-06-19T10:00:00' FROM files")
    conn.commit()
    ids = [r[0] for r in conn.execute("SELECT id FROM files ORDER BY filename")]
    return conn, ids, root, tmp_path / "out"


def test_resize_convert_rename_into_new_files(photos):
    conn, ids, root, out = photos
    opts = batch.BatchOptions(preset=engine.Preset("t", 600, 600, "inside", "webp", 80), pattern="Trip {n} {name}")
    made = batch.run(conn, ids, opts, out)
    assert [p.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for p in made] == \
        ["Trip 001 DSC00.webp", "Trip 002 DSC01.webp", "Trip 003 DSC02.webp"]
    with Image.open(made[0]) as im:
        assert im.size == (600, 400) and im.format == "WEBP"
    assert sorted(p.name for p in root.iterdir()) == ["DSC00.jpg", "DSC01.jpg", "DSC02.jpg"]   # untouched


def test_metadata_all_no_location_and_none(photos):
    conn, ids, root, out = photos
    import piexif
    for meta, has_camera, has_gps in (("all", True, True), ("no_location", True, False), ("none", False, False)):
        p = batch.run(conn, ids[:1], batch.BatchOptions(metadata=meta), out / meta)[0]
        with Image.open(p) as im:
            raw = im.info.get("exif")
        ex = piexif.load(raw) if raw else {"0th": {}, "GPS": {}}
        assert (piexif.ImageIFD.Model in ex["0th"]) == has_camera, meta
        assert bool(ex["GPS"]) == has_gps, meta


def test_watermark_marks_the_chosen_corner_only(photos):
    conn, ids, root, out = photos
    wm = batch.Watermark("© Lunelis", "bottom right", size=8, opacity=100, color="white")
    p = batch.run(conn, ids[:1], batch.BatchOptions(preset=engine.Preset("p", format="png"), watermark=wm), out)[0]
    with Image.open(p) as im:
        im = im.convert("RGB")
        w, h = im.size
        corner = im.crop((w // 2, h * 3 // 4, w, h))
        other = im.crop((0, 0, w // 2, h // 4))
    assert corner.getextrema()[0][1] > 240        # white text there
    assert other.getextrema()[0][1] < 120          # nothing elsewhere


def test_a_photo_that_fails_is_reported_after_the_rest(photos):
    conn, ids, root, out = photos
    (root / "DSC01.jpg").unlink()
    with pytest.raises(batch.BatchError) as e:
        batch.run(conn, ids, batch.BatchOptions(), out)
    assert len(e.value.made) == 2 and e.value.failed[0][0] == "DSC01.jpg"
    with pytest.raises(batch.Cancelled):
        batch.run(conn, ids, batch.BatchOptions(), out, cancelled=lambda: True)
