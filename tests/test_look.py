"""0.27 Learn My Look: a small model of your edits, learned on this PC, that
suggests an edit in your style - shown first, applied only on Apply."""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PIL import Image  # noqa: E402

from lunelis.edit import look, store  # noqa: E402
from lunelis.edit.stack import Stack  # noqa: E402


def test_features_tell_bright_from_dark_and_warm_from_cool():
    dark = look.features(np.full((50, 60, 3), 40, np.uint8))
    bright = look.features(np.full((50, 60, 3), 210, np.uint8))
    assert dark[0] < 0.2 < 0.7 < bright[0]
    assert bright[5] == 0 and dark[6] == 0                       # nothing clipped
    warm = look.features(np.dstack([np.full((10, 10), 200), np.full((10, 10), 150), np.full((10, 10), 100)]).astype(np.uint8))
    assert warm[7] > 0 and warm[8] < 0                           # red over green, blue under


def test_a_fixed_habit_is_learned_back():
    rng = np.random.default_rng(2)
    X = rng.random((60, 10)).astype(np.float32)
    # Brighten dark photos: exposure = 1 - 2 * mean brightness; always +20 vibrance; never touch tint.
    targets = {"exposure": 1.0 - 2.0 * X[:, 0], "vibrance": np.full(60, 20.0), "tint": np.zeros(60)}
    m = look.fit(X, targets)
    assert set(m.sliders) == {"exposure", "vibrance"}             # tint: never used, never suggested
    dark = X[0].copy()
    dark[0] = 0.1
    bright = X[0].copy()
    bright[0] = 0.9
    assert m.predict(dark)["exposure"] == pytest.approx(0.8, abs=0.1)
    assert m.predict(bright)["exposure"] == pytest.approx(-0.8, abs=0.1)
    assert m.predict(dark)["vibrance"] == 20


def test_rarely_used_sliders_are_left_alone():
    X = np.random.default_rng(3).random((40, 10))
    y = np.zeros(40)
    y[:5] = 30                                                    # 1 edit in 8
    assert "fade" not in look.fit(X, {"fade": y}).sliders


@pytest.fixture
def lib(tmp_path, monkeypatch):
    from lunelis import paths
    from lunelis.catalog.schema import open_catalog
    from lunelis.importers.scan import add_root, scan_root
    monkeypatch.setattr(paths, "DEFAULT_CATALOG_PATH", tmp_path / "cat.db")
    root = tmp_path / "Photos"
    root.mkdir()
    rng = np.random.default_rng(4)
    levels = np.linspace(40, 220, 20)
    for i, lvl in enumerate(levels):
        noise = rng.integers(-20, 20, (60, 80, 3))
        Image.fromarray(np.clip(lvl + noise, 0, 255).astype(np.uint8)).save(root / f"IMG_{i:02d}.jpg", quality=95)
    conn = open_catalog(paths.DEFAULT_CATALOG_PATH)
    scan_root(conn, add_root(conn, root))
    ids = [r[0] for r in conn.execute("SELECT id FROM files ORDER BY filename")]
    yield conn, ids, levels, tmp_path
    conn.close()


def _edit_like_me(conn, ids, levels, n):
    """My habit: lift dark photos, pull down bright ones; a little contrast always."""
    for fid, lvl in list(zip(ids, levels))[:n]:
        store.save(conn, fid, Stack(adjust={"exposure": round(1.5 - lvl / 85, 2), "contrast": 15}))


def test_too_few_edits_no_suggestion(lib):
    conn, ids, levels, tmp = lib
    _edit_like_me(conn, ids, levels, 5)
    assert look.train(conn, tmp / "data") is None
    assert not look.stale(conn, tmp / "data")


def test_learning_from_the_catalog_and_retraining_when_edits_grow(lib):
    conn, ids, levels, tmp = lib
    _edit_like_me(conn, ids[::-1], levels[::-1], 16)           # learn from 16, keep the darkest 4 for later
    folder = tmp / "data"
    assert look.stale(conn, folder)
    m = look.train(conn, folder)
    assert m is not None and m.n == 16 and set(m.sliders) == {"exposure", "contrast"}
    assert not look.stale(conn, folder) and look.Model.load(folder).n == 16
    dark = look.features_of_file(conn.execute(
        "SELECT r.path || '/' || f.rel_path FROM files f JOIN roots r ON r.id = f.root_id WHERE f.id = ?",
        (ids[0],)).fetchone()[0])
    s = m.predict(dark)
    assert s["exposure"] > 0.6 and s["contrast"] == 15
    _edit_like_me(conn, ids, levels, 4)                         # 4 more edits: a quarter more
    assert look.stale(conn, folder)
    assert look.describe({"exposure": 0.4, "contrast": 15}) == "Exposure +0.4, Contrast +15"


def pump(app, cond, seconds=15):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def test_the_edit_panel_shows_the_suggestion_and_only_apply_changes_the_photo(lib, monkeypatch):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from lunelis import paths
    from lunelis.ui.detail_view import DetailView
    from lunelis.ui.library import Filter, LibraryIndex
    conn, ids, levels, tmp = lib
    monkeypatch.setattr(paths, "DATA_DIR", tmp / "data")
    _edit_like_me(conn, ids[4:], levels[4:], 16)
    idx = LibraryIndex()
    idx.load(conn, "name", Filter())
    v = DetailView(conn)
    try:
        v.open(idx, 0)                                          # the darkest, not edited
        v.set_editing(True)
        assert pump(app, lambda: v.edit.session.disp is not None)
        v.develop.look_b.click()
        assert pump(app, lambda: v.develop.look_box.isVisibleTo(v.develop), 60), v.develop.status.text()
        assert "learned from 16 edits" in v.develop.look_text.text()
        assert v.edit.stack.adjust == {}                         # shown, not applied
        v.develop.look_apply_b.click()
        assert v.edit.stack.adjust.get("exposure", 0) > 0.5 and v.edit.stack.adjust.get("contrast") == 15
        v.edit.undo()
        assert v.edit.stack.adjust == {}
    finally:
        v.set_editing(False)
        v.shut()
        v.deleteLater()


def test_too_few_edits_says_so_in_the_panel(lib, monkeypatch):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from lunelis import paths
    from lunelis.ui.detail_view import DetailView
    from lunelis.ui.library import Filter, LibraryIndex
    conn, ids, levels, tmp = lib
    monkeypatch.setattr(paths, "DATA_DIR", tmp / "data")
    idx = LibraryIndex()
    idx.load(conn, "name", Filter())
    v = DetailView(conn)
    try:
        v.open(idx, 0)
        v.set_editing(True)
        assert pump(app, lambda: v.edit.session.disp is not None)
        v.develop.look_b.click()
        assert pump(app, lambda: "at least 15" in v.develop.status.text())
        assert not v.develop.look_box.isVisibleTo(v.develop)
    finally:
        v.set_editing(False)
        v.shut()
        v.deleteLater()
