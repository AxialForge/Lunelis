"""0.29 editing suite: heal / clone / red-eye, preset files, virtual copies,
colour management (output profiles, soft proof, monitor) and output sharpening."""
import json
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PIL import Image, ImageCms  # noqa: E402

from lunelis.edit import icc, retouch, store  # noqa: E402
from lunelis.edit.retouch import Spot  # noqa: E402
from lunelis.edit.stack import Stack, dumps, loads  # noqa: E402


def textured(h=200, w=300, seed=1):
    rng = np.random.default_rng(seed)
    base = np.full((h, w, 3), 0.55, np.float32)
    return np.clip(base + rng.normal(0, 0.03, (h, w, 3)), 0, 1).astype(np.float32)


# --- retouch -------------------------------------------------------------------------------------

def test_heal_removes_a_spot():
    a = textured()
    yy, xx = np.mgrid[:200, :300]
    dot = (xx - 150) ** 2 + (yy - 100) ** 2 <= 6 ** 2
    a[dot] = 0.05                                               # a dust spot
    out = retouch.apply(a, (Spot("heal", 0.5, 0.5, 10 / 300),))
    assert abs(out[dot].mean() - 0.55) < 0.05 and a[dot].mean() < 0.1          # gone; input untouched
    far = np.ones_like(dot)
    far[60:140, 100:200] = False
    assert np.allclose(out[far], a[far])                         # nothing else changes


def test_clone_copies_from_the_source_and_red_eye_only_takes_the_red():
    a = textured()
    a[20:40, 20:40] = (0.9, 0.1, 0.1)                            # a red square to copy
    out = retouch.apply(a, (Spot("clone", 0.6, 0.5, 6 / 300, 30 / 300, 30 / 200),))
    assert out[100, 180, 0] > 0.8 and out[100, 180, 1] < 0.2
    eye = textured()
    eye[95:105, 145:155] = (0.8, 0.15, 0.12)                     # red pupil
    eye[90:95, 145:155] = (0.6, 0.6, 0.6)                        # white of the eye nearby
    fixed = retouch.apply(eye, (Spot("redeye", 0.5, 0.5, 15 / 300),))
    assert fixed[100, 150, 0] < 0.3                              # red pulled down
    assert np.allclose(fixed[92, 150], eye[92, 150], atol=1e-6)  # grey untouched


def test_spots_follow_the_crop():
    a = textured(200, 200)
    a[90:110, 90:110] = 0.0
    crop = (0.25, 0.25, 0.75, 0.75)                               # the spot sits at the middle of the crop
    cropped = a[50:150, 50:150]
    out = retouch.apply(cropped, (Spot("heal", 0.5, 0.5, 14 / 200),), crop)
    assert out[45:55, 45:55].mean() > 0.4


def test_spots_in_the_stack_text_and_the_pipeline():
    st = Stack(retouch=(Spot("heal", 0.4, 0.3, 0.01, 0.45, 0.3), Spot("redeye", 0.2, 0.2, 0.005)))
    text = dumps(st)
    assert text == "v=2;spot=heal|0.4,0.3,0.01|0.45,0.3;spot=redeye|0.2,0.2,0.005|"
    assert loads(text) == st and not st.is_identity()
    from lunelis.edit import pipeline
    a = textured()
    a[95:105, 115:125] = 0.0
    out = pipeline.apply(a, Stack(retouch=(Spot("heal", 0.4, 0.5, 0.03),)))
    assert out[95:105, 115:125].mean() > 0.4
    tiled = pipeline.apply_tiled(a.copy(), Stack(retouch=(Spot("heal", 0.4, 0.5, 0.03),)), rows=64)
    assert abs(tiled[95:105, 115:125].mean() / 255 - out[95:105, 115:125].mean()) < 0.02


# --- presets as files ----------------------------------------------------------------------------

def test_presets_round_trip_through_a_file(tmp_path):
    from lunelis.catalog.schema import open_catalog
    a = open_catalog(tmp_path / "a.db")
    store.save_filter(a, "Warm evening", {"temp": 25, "vibrance": 10})
    doc = store.export_filters(a)
    assert doc["format"] == "lunelis-presets/1" and doc["presets"] == [
        {"name": "Warm evening", "adjust": {"temp": 25.0, "vibrance": 10.0}}]
    path = tmp_path / "mine.json"
    path.write_text(json.dumps(doc))
    b = open_catalog(tmp_path / "b.db")
    assert store.import_filters(b, json.loads(path.read_text())) == (1, [])
    assert store.filter_params(b, "Warm evening") == {"temp": 25.0, "vibrance": 10.0}
    assert store.import_filters(b, doc) == (0, ["Warm evening"])          # already there
    with pytest.raises(ValueError):
        store.import_filters(b, {"format": "something else"})


# --- virtual copies ------------------------------------------------------------------------------

@pytest.fixture
def lib(tmp_path, monkeypatch):
    from lunelis import paths
    from lunelis.catalog.schema import open_catalog
    from lunelis.importers.scan import add_root, scan_root
    monkeypatch.setattr(paths, "DEFAULT_CATALOG_PATH", tmp_path / "cat.db")
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "data")
    root = tmp_path / "Photos"
    root.mkdir()
    Image.fromarray((textured(240, 320) * 255).astype(np.uint8)).save(root / "a.jpg", quality=95)
    conn = open_catalog(paths.DEFAULT_CATALOG_PATH)
    scan_root(conn, add_root(conn, root))
    fid = conn.execute("SELECT id FROM files").fetchone()[0]
    yield conn, fid, tmp_path
    conn.close()


def test_a_virtual_copy_edits_and_exports_on_its_own(lib):
    from lunelis.edit.export import ExportOptions, export_one
    conn, fid, tmp = lib
    store.save(conn, fid, Stack(adjust={"exposure": 1.0}))
    cid = store.add_copy(conn, fid, Stack(adjust={"exposure": -1.0}))
    assert store.copies_of(conn, fid) == [(cid, "Copy 1")]
    assert store.get(conn, fid).adjust == {"exposure": 1.0}
    opts = ExportOptions(str(tmp / "out"), "png")
    bright = np.asarray(Image.open(export_one(conn, fid, opts))).mean()
    dark = np.asarray(Image.open(export_one(conn, fid, opts, stack=store.get_copy(conn, cid)))).mean()
    assert bright > dark + 40
    assert store.save_copy(conn, cid, Stack(adjust={"exposure": -0.5}))
    assert store.get(conn, fid).adjust == {"exposure": 1.0}                  # the original stays
    store.delete_copy(conn, cid)
    assert store.copies_of(conn, fid) == []


def pump(app, cond, seconds=15):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def test_versions_and_retouch_in_the_edit_panel(lib):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from lunelis.ui.detail_view import DetailView
    from lunelis.ui.library import Filter, LibraryIndex
    conn, fid, tmp = lib
    idx = LibraryIndex()
    idx.load(conn, "name", Filter())
    v = DetailView(conn)
    try:
        v.open(idx, 0)
        v.set_editing(True)
        assert pump(app, lambda: v.edit.session.disp is not None)
        e, panel = v.edit, v.develop
        e._adjust("exposure", 0.7, True)
        panel.copy_new_b.click()                                      # a copy starts from this edit
        assert e.copy_id is not None and panel.copy_box.currentText() == "Copy 1"
        e._adjust("exposure", -0.7, True)
        e.save()
        assert store.get(conn, fid).adjust == {"exposure": 0.7}
        assert store.get_copy(conn, e.copy_id).adjust == {"exposure": -0.7}
        e.switch_copy(None)
        assert e.stack.adjust == {"exposure": 0.7}
        # Retouch: heal a spot, then take it back.
        panel.retouch_buttons["heal"].click()
        assert v.canvas.retouch_kind == "heal"
        e.add_spot(0.5, 0.5, False)
        assert len(e.stack.retouch) == 1 and e.stack.retouch[0].sx is not None    # source chosen once
        assert panel.spot_count.text() == "1 spot"
        panel.spot_undo_b.click()
        assert e.stack.retouch == ()
        panel.retouch_buttons["clone"].click()
        e.add_spot(0.5, 0.5, False)
        assert e.stack.retouch == () and "Alt+click" in panel.status.text()
        e.add_spot(0.2, 0.2, True)
        e.add_spot(0.5, 0.5, False)
        s = e.stack.retouch[0]
        assert s.kind == "clone" and (s.sx, s.sy) == pytest.approx((0.2, 0.2))
    finally:
        v.set_editing(False)
        v.shut()
        v.deleteLater()


# --- colour --------------------------------------------------------------------------------------

def test_made_profiles_match_published_conversions(tmp_path):
    adobe = icc.profile_path("adobe-rgb", tmp_path)
    img = Image.new("RGB", (2, 1))
    img.putpixel((0, 0), (0, 255, 0))
    img.putpixel((1, 0), (255, 0, 0))
    out = ImageCms.profileToProfile(img, ImageCms.createProfile("sRGB"), ImageCms.getOpenProfile(str(adobe)),
                                    outputMode="RGB")
    g, r = out.getpixel((0, 0)), out.getpixel((1, 0))
    assert all(abs(a - b) <= 2 for a, b in zip(g, (144, 255, 60)))         # sRGB green in Adobe RGB
    assert all(abs(a - b) <= 2 for a, b in zip(r, (219, 0, 0)))
    p3 = ImageCms.getOpenProfile(str(icc.profile_path("display-p3", tmp_path)))
    assert "Display P3" in ImageCms.getProfileDescription(p3)


def test_soft_proof_flags_what_a_smaller_space_cant_show(tmp_path, monkeypatch):
    monkeypatch.setitem(icc.SPACES, "narrow", ("Narrow test space", ((0.5, 0.4), (0.33, 0.45), (0.25, 0.25)),
                                               icc.D65, 2.2))
    narrow = str(icc.profile_path("narrow", tmp_path))
    img = Image.new("RGB", (2, 1))
    img.putpixel((0, 0), (0, 255, 0))                                    # far outside
    img.putpixel((1, 0), (128, 128, 128))                                # grey: fine anywhere
    mask = icc.out_of_gamut(img, narrow)
    assert mask[0, 0] and not mask[0, 1]
    shown = icc.proof(img, narrow)
    assert shown.getpixel((0, 0)) == icc.GAMUT_WARNING and shown.getpixel((1, 0)) != icc.GAMUT_WARNING


def test_export_into_a_profile_embeds_it(lib):
    from lunelis.edit.export import ExportOptions, export_one
    conn, fid, tmp = lib
    path = export_one(conn, fid, ExportOptions(str(tmp / "out"), "jpeg", profile="builtin:display-p3"))
    with Image.open(path) as im:
        embedded = ImageCms.ImageCmsProfile(__import__("io").BytesIO(im.info["icc_profile"]))
    assert "Display P3" in ImageCms.getProfileDescription(embedded)


def test_output_sharpening_is_for_the_output_only(lib):
    from lunelis.edit.export import ExportOptions, export_one
    conn, fid, tmp = lib
    soft = np.asarray(Image.open(export_one(conn, fid, ExportOptions(str(tmp / "o"), "png"))).convert("L"), float)
    sharp = np.asarray(Image.open(export_one(conn, fid, ExportOptions(str(tmp / "o"), "png", sharpen="matte",
                                                                      sharpen_amount="high"))).convert("L"), float)

    def detail(a):
        return np.abs(np.diff(a, axis=1)).mean()
    assert detail(sharp) > detail(soft) * 1.1
    assert store.get(conn, fid).is_identity()                           # the edit didn't change


def test_export_presets_in_the_dialog(lib, monkeypatch):
    from PySide6.QtWidgets import QApplication, QInputDialog
    QApplication.instance() or QApplication([])
    from lunelis.settings import Settings
    from lunelis.ui.export_dialog import ExportDialog
    conn, fid, tmp = lib
    d = ExportDialog(conn, 1)
    d.sharpen.setCurrentIndex(d.sharpen.findData("glossy"))
    d.profile.setCurrentIndex(d.profile.findData("builtin:adobe-rgb"))
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Print, glossy", True)))
    d._save_preset()
    saved = Settings(conn).get("export_presets")["Print, glossy"]
    assert saved["sharpen"] == "glossy" and saved["profile"] == "builtin:adobe-rgb"
    d2 = ExportDialog(conn, 1)
    d2.preset.setCurrentIndex(d2.preset.findData("Print, glossy"))
    o = d2._current()
    assert o.sharpen == "glossy" and o.profile == "builtin:adobe-rgb"
