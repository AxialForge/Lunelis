"""HDR and panorama merges."""
import os

import cv2
import numpy as np
import pytest
from PIL import Image, ImageFilter
from PySide6.QtWidgets import QApplication

from lunelis import paths
from lunelis.catalog.schema import open_catalog
from lunelis.edit import merge
from lunelis.importers.scan import add_root, catalog_file, scan_root


def scene():
    yy, xx = np.mgrid[0:300, 0:450]
    s = (0.02 + 3.0 * (xx / 450) ** 3 + 0.3 * ((xx // 23 + yy // 19) % 2)).astype(np.float32)
    return np.repeat(s[..., None], 3, 2)


def texture(w=1200, h=400, seed=5):
    base = (np.random.default_rng(seed).random((h, w, 3)) * 255).astype(np.uint8)
    img = Image.fromarray(base).resize((w // 4, h // 4)).resize((w, h), Image.Resampling.BICUBIC)
    return np.asarray(img.filter(ImageFilter.DETAIL))


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DEFAULT_CATALOG_PATH", tmp_path / "catalog.db")
    root = tmp_path / "Photos"
    (root / "hdr").mkdir(parents=True)
    (root / "pano").mkdir()
    s = scene()
    for i, ev in enumerate((-2, 0, 2)):
        shot = np.clip(np.roll(s * 2 ** ev, (i, -i), axis=(0, 1)), 0, 1) ** (1 / 2.2)
        Image.fromarray((shot * 255).astype(np.uint8)).save(root / "hdr" / f"B{i}.jpg", quality=95)
    t = texture()
    for i, x0 in enumerate((0, 350, 700)):
        Image.fromarray(t[:, x0:x0 + 500]).save(root / "pano" / f"P{i}.jpg", quality=95)
    conn = open_catalog(paths.DEFAULT_CATALOG_PATH)
    scan_root(conn, add_root(conn, root))
    from lunelis.importers.metadata import extract_pending
    extract_pending(conn)                                  # sources are read before anyone merges them
    conn.execute("UPDATE exif SET captured_at = '2024-06-18T13:43:06', camera_make = 'SONY',"
                 " camera_model = 'ILCE-7RM5' WHERE file_id = (SELECT id FROM files WHERE filename = 'B0.jpg')")

    conn.commit()
    yield conn, root, tmp_path
    conn.close()


def ids(conn, *names):
    return [conn.execute("SELECT id FROM files WHERE filename = ?", (n,)).fetchone()[0] for n in names]


def test_hdr_into_the_library(lib):
    conn, root, _ = lib
    src = ids(conn, "B0.jpg", "B1.jpg", "B2.jpg")
    out = str(root / "hdr" / "B0-HDR.tif")
    steps = []
    fid = merge.run(conn, src, merge.MergeOptions("hdr", out), steps.append)
    img = cv2.imread(out, cv2.IMREAD_UNCHANGED)
    assert img.dtype == np.uint16 and img.shape == (300, 450, 3)
    assert (img >= 65000).mean() < 0.01                                  # the bright end isn't clipped
    assert fid is not None and tuple(conn.execute("SELECT kind, source_ids FROM merges WHERE file_id = ?",
                                                  (fid,)).fetchone()) == ("hdr", ",".join(map(str, src)))
    assert tuple(conn.execute("SELECT captured_at, camera_model FROM exif WHERE file_id = ?",
                              (fid,)).fetchone()) == ("2024-06-18T13:43:06", "ILCE-7RM5")
    assert any("Saving" in s for s in steps)
    with pytest.raises(merge.MergeFailed, match="already exists"):
        merge.run(conn, src, merge.MergeOptions("hdr", out))             # never overwrites


def test_panorama_outside_the_library(lib):
    conn, _, tmp = lib
    out = str(tmp / "elsewhere" / "pano.jpg")
    fid = merge.run(conn, ids(conn, "P0.jpg", "P1.jpg", "P2.jpg"), merge.MergeOptions("panorama", out, "jpeg"))
    assert fid is None and os.path.exists(out)                           # saved, not cataloged
    w, h = Image.open(out).size
    assert w > 1000 and h > 300


def test_limits_and_failures(lib):
    conn, root, tmp = lib
    with pytest.raises(merge.MergeFailed, match="2 to"):
        merge.run(conn, ids(conn, "B0.jpg"), merge.MergeOptions("hdr", str(tmp / "x.tif")))
    with pytest.raises(merge.MergeFailed):
        merge.merge_hdr([np.zeros((10, 10, 3), np.float32), np.zeros((12, 10, 3), np.float32)])
    rng = np.random.default_rng(1)
    noise = [rng.random((200, 200, 3)).astype(np.float32) for _ in range(2)]
    with pytest.raises(merge.MergeFailed, match="stitch"):
        merge.merge_panorama(noise)


def test_catalog_file_only_inside_sources(lib):
    conn, root, tmp = lib
    p = root / "new.jpg"
    Image.new("RGB", (10, 10)).save(p)
    fid = catalog_file(conn, p)
    assert fid and catalog_file(conn, p) == fid                          # once
    q = tmp / "outside.jpg"
    Image.new("RGB", (10, 10)).save(q)
    assert catalog_file(conn, q) is None


def test_dialog(lib, monkeypatch):
    QApplication.instance() or QApplication([])
    from lunelis.ui import merge_dialog as md
    conn, root, tmp = lib
    d = md.MergeDialog(conn, "hdr", 3, "B0.jpg")
    assert d.suggested_path().endswith("B0-HDR.tif") and d.go.isEnabled()
    monkeypatch.setattr(md.QFileDialog, "getSaveFileName", lambda *a, **k: (str(tmp / "out" / "x"), ""))
    d._accept()
    assert d.options.path.endswith("x.tif") and d.options.kind == "hdr"
    assert md.MergeDialog(conn, "panorama", 1, "P0.jpg").go.isEnabled() is False
    from lunelis.settings import Settings
    assert Settings(conn).get("merge_last_dir") == str(tmp / "out")
