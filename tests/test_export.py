"""Exporting photos with their edits."""
import os
import time

import piexif
import pytest
from PIL import Image
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication

from lunelis import paths
from lunelis.catalog.schema import open_catalog
from lunelis.edit import store
from lunelis.edit.export import ExportOptions, export_one, file_name, free_path
from lunelis.edit.stack import Geometry, Stack
from lunelis.importers.scan import add_root, scan_root


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DEFAULT_CATALOG_PATH", tmp_path / "catalog.db")
    root = tmp_path / "Photos"
    root.mkdir()
    Image.new("RGB", (900, 600), (100, 100, 100)).save(root / "IMG_1.jpg")
    Image.new("RGB", (900, 600), (100, 100, 100)).save(root / "IMG_2.jpg")
    conn = open_catalog(paths.DEFAULT_CATALOG_PATH)
    scan_root(conn, add_root(conn, root))
    ids = [r[0] for r in conn.execute("SELECT id FROM files ORDER BY filename")]
    conn.execute("INSERT OR REPLACE INTO exif (file_id, captured_at, camera_make, camera_model, lens, focal_length_mm,"
                 " aperture, shutter_speed, iso, gps_lat, gps_lon) VALUES (?, '2024-06-18T13:43:06', 'SONY',"
                 " 'ILCE-7RM5', 'FE 24-105mm F4 G OSS', 24, 4.0, '1/640', 1000, 41.25, -81.44)", (ids[0],))
    conn.commit()
    yield conn, ids, tmp_path
    conn.close()


def test_names():
    assert file_name("{date}_{name}_{n}", "DSC0001.ARW", "2024-06-18T13:43:06", 7, ".jpg") == "2024-06-18_DSC0001_007.jpg"
    assert file_name('a/b:{name}', "x.jpg", None, 1, ".png") == "a_b_x.png"


def test_never_overwrites(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"x")
    (tmp_path / "a (2).jpg").write_bytes(b"x")
    assert free_path(str(tmp_path), "a.jpg") == os.path.join(str(tmp_path), "a (3).jpg")


def test_export_applies_the_edit_and_metadata(lib):
    conn, (a, b), tmp = lib
    store.save(conn, a, Stack(adjust={"exposure": 1.5}, geometry=Geometry(rotate=90, crop=(0, 0, 1, 0.5))))
    out = tmp / "out"
    p = export_one(conn, a, ExportOptions(str(out)))
    im = Image.open(p)
    assert im.size == (600, 450)                                   # rotated, then the top half kept
    assert sum(im.getpixel((10, 10))) / 3 > 150                     # +1.5 EV: 100 -> ~161
    ex = piexif.load(im.info["exif"])
    assert ex["0th"][piexif.ImageIFD.Model] == b"ILCE-7RM5"
    assert ex["Exif"][piexif.ExifIFD.ExposureTime] == (1, 640)
    assert ex["GPS"][piexif.GPSIFD.GPSLatitudeRef] == b"N"
    assert im.info.get("icc_profile")
    p2 = export_one(conn, a, ExportOptions(str(out), metadata="no_location", long_edge=300))
    im2 = Image.open(p2)
    assert max(im2.size) == 300 and p2.endswith("IMG_1 (2).jpg")
    assert not piexif.load(im2.info["exif"])["GPS"]
    p3 = export_one(conn, b, ExportOptions(str(out), format="png", metadata="none"))
    assert Image.open(p3).size == (900, 600) and "exif" not in Image.open(p3).info
    p4 = export_one(conn, b, ExportOptions(str(out), format="tiff"))
    assert Image.open(p4).format == "TIFF"
    assert os.path.getsize(tmp / "Photos" / "IMG_1.jpg") > 0            # originals untouched


def test_export_worker(lib):
    from lunelis.ui.export_dialog import ExportWorker
    conn, ids, tmp = lib
    w = ExportWorker(ids, ExportOptions(str(tmp / "out2"), pattern="{n}"))
    got = []
    w.done.connect(lambda *r: got.append(r))
    w.run()
    assert got == [(2, 0, [])]
    assert sorted(os.listdir(tmp / "out2")) == ["001.jpg", "002.jpg"]


def test_dialog_remembers(lib):
    QApplication.instance() or QApplication([])
    from lunelis.settings import Settings
    from lunelis.ui.export_dialog import ExportDialog
    conn, _, tmp = lib
    d = ExportDialog(conn, 2)
    d.folder.setText(str(tmp / "x"))
    d.format.setCurrentIndex(d.format.findData("png"))
    d.pattern.setText("")
    d._accept()
    assert d.options is None and "can't be empty" in d.error.text()
    d.pattern.setText("{date}-{name}")
    d._accept()
    assert d.options.format == "png"
    assert Settings(conn).get("export_last")["pattern"] == "{date}-{name}"
    assert ExportDialog(conn, 1).format.currentData() == "png"


def test_tiff_keeps_the_camera(lib):
    conn, (a, _), tmp = lib
    p = export_one(conn, a, ExportOptions(str(tmp / "t"), format="tiff"))
    tags = Image.open(p).tag_v2
    assert tags.get(272) == "ILCE-7RM5" and tags.get(305) == "Lunelis"
