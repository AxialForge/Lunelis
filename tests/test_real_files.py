"""0.50: real files on CI - a camera RAW and the real face models.

CI downloads them first (see .github/workflows/python-release.yml):
- LUNELIS_SAMPLE_ARW: a Sony ILCE-7RM3 ARW from raw.pixls.us (CC0), checked
  against its SHA-256;
- LUNELIS_REAL_MODELS=1: the YuNet + SFace face models, the way the app
  downloads them (about 39 MB).
Without them (a quick local run) these tests are skipped.
"""
import hashlib
import os

import numpy as np
import pytest

SAMPLE_SHA = "f1caa23068f7352abf883f249851e0011abc25fc7c24a5251f2e641f9da70761"
ARW = os.environ.get("LUNELIS_SAMPLE_ARW", "")


def _arw_ok() -> bool:
    if not ARW or not os.path.isfile(ARW):
        return False
    h = hashlib.sha256()
    with open(ARW, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest() == SAMPLE_SHA


needs_arw = pytest.mark.skipif(not _arw_ok(), reason="set LUNELIS_SAMPLE_ARW to the raw.pixls.us ILCE-7RM3 sample")
needs_models = pytest.mark.skipif(os.environ.get("LUNELIS_REAL_MODELS") != "1",
                                  reason="set LUNELIS_REAL_MODELS=1 to download the real face models")


@needs_arw
def test_a_real_sony_raw_reads_and_previews(tmp_path):
    import shutil
    from lunelis.catalog.schema import open_catalog
    from lunelis.importers.metadata import extract_pending, read_file
    from lunelis.importers.scan import add_root, scan_root
    from lunelis.raw.thumbnails import generate_pending, render
    row = read_file(ARW)
    assert row["camera_model"] == "ILCE-7RM3" and row["captured_at"]
    img = render(ARW, None)
    assert max(img.size) <= 512 and min(img.size) > 100           # the embedded preview, upright
    root = tmp_path / "Photos"
    root.mkdir()
    shutil.copy(ARW, root / "sample.ARW")
    conn = open_catalog(tmp_path / "c.db")
    scan_root(conn, add_root(conn, root))
    extract_pending(conn)
    assert generate_pending(conn, tmp_path / "thumbs").made == 1
    f = conn.execute("SELECT is_raw, perceptual_hash FROM files").fetchone()
    assert f[0] == 1 and f[1]                                       # a RAW, fingerprinted with its thumbnail
    conn.close()


@needs_arw
def test_a_real_sony_raw_decodes_in_full_for_editing():
    from lunelis.edit import render
    a = render.load_source(ARW, True, render.PROXY_EDGE)
    assert a.ndim == 3 and a.shape[2] == 3 and max(a.shape[:2]) <= render.PROXY_EDGE
    assert np.isfinite(a).all() and 0.01 < float(a.mean()) < 1.0      # scene-linear: slight negatives are normal


@needs_models
def test_the_real_face_models_download_load_and_run():
    from lunelis.recognize import faces
    if not faces.available():
        faces.download()
    assert faces.available()
    be = faces.backend()
    assert be is not None
    assert be.detect(np.zeros((480, 640, 3), np.uint8)) == []      # a blank picture: no faces, no crash
    feat = be.recognizer.feature(np.zeros((112, 112, 3), np.uint8))
    assert feat.size == 128                                          # SFace's fingerprint
