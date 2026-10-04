"""Editing in the photo view: the Develop panel, crop helpers, saving, caches."""
import time

import pytest
from PIL import Image
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication

from lunelis import paths
from lunelis.catalog.schema import open_catalog
from lunelis.edit import render, store
from lunelis.edit.stack import Stack
from lunelis.importers.scan import add_root, scan_root
from lunelis.raw.thumbnails import generate_pending
from lunelis.ui.develop import fit_aspect, rotate_crop
from lunelis.ui.library import LibraryIndex


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def pump(app, until, timeout=10.0):
    t = time.time()
    while time.time() - t < timeout:
        app.processEvents()
        QThreadPool.globalInstance().waitForDone(20)
        for pool in QApplication.instance().findChildren(QThreadPool):
            pool.waitForDone(20)
        if until():
            return True
    return False


@pytest.fixture
def lib(tmp_path, monkeypatch):
    # A catalog of its own per test (workers open paths.DEFAULT_CATALOG_PATH).
    monkeypatch.setattr(paths, "DEFAULT_CATALOG_PATH", tmp_path / "catalog.db")
    root = tmp_path / "Photos"
    root.mkdir()
    for i, colour in enumerate(((120, 90, 60), (60, 90, 120), (90, 90, 90))):
        Image.new("RGB", (600, 400), colour).save(root / f"IMG_{i}.jpg")
    conn = open_catalog(paths.DEFAULT_CATALOG_PATH)
    scan_root(conn, add_root(conn, root))
    generate_pending(conn, paths.THUMBNAIL_CACHE, workers=1)
    idx = LibraryIndex()
    idx.load(conn, "name")
    yield conn, idx
    conn.close()


def test_crop_helpers():
    assert rotate_crop((0.1, 0.2, 0.5, 0.6), 1) == pytest.approx((0.4, 0.1, 0.8, 0.5))
    assert rotate_crop((0.1, 0.2, 0.5, 0.6), 4) == (0.1, 0.2, 0.5, 0.6)
    x0, y0, x1, y1 = fit_aspect((0.0, 0.0, 1.0, 1.0), 1.0, 1.5)          # square crop of a 3:2 photo
    assert (x1 - x0) * 1.5 == pytest.approx(y1 - y0) and 0 <= x0 and x1 <= 1 and y1 - y0 == pytest.approx(1)


def test_editing_in_the_photo_view(app, lib):
    from lunelis.ui.detail_view import DetailView
    conn, idx = lib
    v = DetailView(conn)
    v.resize(1200, 800)
    saved = []
    v.edited.connect(saved.append)
    v.open(idx, 0)
    fid = idx.file_id(0)
    v.set_editing(True)
    assert v.side.currentWidget() is v.develop
    assert pump(app, lambda: v.edit.has_render)                       # decoded and first render shown
    s = v.develop.sliders["exposure"]
    s.slider.setSliderDown(True)
    s.set_value(1.0, emit=True)                                        # dragging: live, not recorded
    assert v.edit.stack.adjust == {"exposure": 1.0} and len(v.edit.history) == 1
    s.slider.setSliderDown(False)
    s.slider.sliderReleased.emit()                                     # let go: recorded once
    assert len(v.edit.history) == 2
    v.develop.sliders["saturation"].set_value(-100, emit=True)
    v.edit.undo()
    assert v.edit.stack.adjust == {"exposure": 1.0} and v.develop.sliders["saturation"].current() == 0
    v.edit.redo()
    assert v.edit.stack.adjust == {"exposure": 1.0, "saturation": -100}
    v.edit._geometry_action("rotate_right")
    assert v.edit.stack.geometry.rotate == 90
    v.edit.set_before(True)
    assert v.edit.showing_before
    v.edit.set_before(False)
    v.go(1)                                                            # stepping on saves + renders the one left
    assert store.get(conn, fid).adjust == {"exposure": 1.0, "saturation": -100}
    assert v.editing and v.edit.info.file_id == idx.file_id(1)         # edit mode stays on
    assert pump(app, lambda: fid in saved)
    assert render.proxy_path(paths.EDIT_CACHE, fid).exists()
    v.set_editing(False)
    assert v.side.currentWidget() is v.panel and not v.edit.active
    # The second photo was opened for editing but not changed: no edit stored.
    assert store.rev(conn, idx.file_id(1)) == 0


def test_reset_removes_the_edit(app, lib):
    from lunelis.ui.detail_view import DetailView
    conn, idx = lib
    fid = idx.file_id(2)
    store.save(conn, fid, Stack(adjust={"contrast": 40}))
    v = DetailView(conn)
    v.open(idx, 2)
    v.set_editing(True)
    pump(app, lambda: v.edit.session.disp is not None)
    v.develop.reset.emit()
    v.set_editing(False)
    v.edit.out_pool.waitForDone(5000)
    assert store.rev(conn, fid) == 0 and not render.proxy_path(paths.EDIT_CACHE, fid).exists()


def test_main_window_marks_the_grid(app, lib, monkeypatch):
    from lunelis.ui import main_window as mw
    conn, _ = lib
    monkeypatch.setattr(mw, "open_catalog", lambda _p: conn)
    w = mw.MainWindow()
    try:
        fid = w.index.file_id(0)
        w.open_detail(fid)
        w.detail.set_editing(True)
        pump(app, lambda: w.detail.edit.session.disp is not None)
        w.detail.develop.sliders["vibrance"].set_value(30, emit=True)
        w.close_detail()                                               # leaving saves
        assert pump(app, lambda: w.index.tile(w.index.position(fid)).edited)
    finally:
        w._quitting = True
        w.close()


def test_filters_in_the_panel(app, lib):
    from lunelis.ui.detail_view import DetailView
    conn, idx = lib
    v = DetailView(conn)
    v.open(idx, 0)
    v.set_editing(True)
    assert pump(app, lambda: v.edit.has_render)
    assert None in v.develop.filter_buttons and "Vivid" in v.develop.filter_buttons
    assert not v.develop.filter_buttons["Vivid"].icon().isNull()     # previewed on this photo
    v.develop.filter_chosen.emit("Warm")
    v.develop.amount_s.set_value(40, emit=True)
    assert v.edit.stack.filter == "Warm" and v.edit.stack.amount == 40
    v.develop.sliders["fade"].set_value(10, emit=True)
    # Save the whole look as a filter: same look, now the named filter at 100 %.
    from unittest import mock
    with mock.patch("lunelis.ui.develop.QInputDialog.getText", return_value=("Evening", True)):
        v.edit._save_filter()
    assert v.edit.stack.filter == "Evening" and v.edit.stack.adjust == {}
    assert "Evening" in v.develop.filter_buttons
    assert store.filter_params(conn, "Evening") == {"temp": 12.0, "tint": 2.0, "vibrance": 4.0, "fade": 10.0}
    v.develop.unpack_filter.emit()
    assert v.edit.stack.filter is None and v.edit.stack.adjust["fade"] == 10
    v.set_editing(False)


def test_deleting_a_used_filter_keeps_the_look(lib):
    conn, idx = lib
    store.save_filter(conn, "Mine", {"exposure": 0.5, "contrast": 20})
    fid = idx.file_id(1)
    store.save(conn, fid, Stack("Mine", 50, {"contrast": 5}))
    assert store.delete_filter(conn, "Mine") == 1
    assert store.get(conn, fid) == Stack(None, 100, {"exposure": 0.25, "contrast": 15.0})


def test_paste_and_reset_across_a_selection(app, lib, monkeypatch):
    from lunelis.ui import main_window as mw
    conn, _ = lib
    monkeypatch.setattr(mw, "open_catalog", lambda _p: conn)
    monkeypatch.setattr(mw.QMessageBox, "question", lambda *a, **k: mw.QMessageBox.StandardButton.Yes)
    w = mw.MainWindow()
    try:
        a, b, c = (w.index.file_id(i) for i in range(3))
        from lunelis.edit.stack import Geometry
        store.save(conn, a, Stack("Vivid", 70, {"exposure": 0.3}, Geometry(rotate=90)))
        store.save(conn, c, Stack(geometry=Geometry(crop=(0.1, 0.1, 0.9, 0.9))))
        w.grid.selected, w.grid.current = {a}, w.index.position(a)
        w.copy_edit()
        w.grid.selected = {b, c}
        w.paste_edit()
        assert store.get(conn, b) == Stack("Vivid", 70, {"exposure": 0.3})              # not a's rotation
        assert store.get(conn, c).geometry.crop == (0.1, 0.1, 0.9, 0.9)                  # c keeps its crop
        assert pump(app, lambda: w._batch_thread is None and w.index.tile(w.index.position(b)).edited)
        assert render.proxy_path(paths.EDIT_CACHE, b).exists()
        w.grid.selected = {a, b, c}
        w.reset_edits()
        assert store.edited_ids(conn, [a, b, c]) == set()
        assert pump(app, lambda: w._batch_thread is None and not render.proxy_path(paths.EDIT_CACHE, b).exists())
    finally:
        w._quitting = True
        w.close()


def test_new_imports_get_the_import_filter(lib):
    from lunelis.settings import Settings
    conn, idx = lib
    fids = [idx.file_id(0), idx.file_id(1)]
    store.save(conn, fids[1], Stack(adjust={"exposure": 1}))                           # already edited: left alone
    Settings(conn).set("import_filter", "Filmic")
    root = conn.execute("SELECT path FROM roots").fetchone()[0]
    imp = conn.execute("INSERT INTO imports (source, template, destination, state) VALUES ('F:\\', 't', ?, 'done')",
                       (root,)).lastrowid
    import os
    for fid in fids:
        rel = conn.execute("SELECT rel_path FROM files WHERE id = ?", (fid,)).fetchone()[0]
        conn.execute("INSERT INTO import_items (import_id, source_rel, size, mtime, state, dest_path)"
                     " VALUES (?, 'x', 1, 0, 'placed', ?)", (imp, os.path.join(root, rel)))
    conn.commit()
    assert store.apply_import_filter(conn) == [fids[0]]
    assert store.get(conn, fids[0]).filter == "Filmic"
    assert store.apply_import_filter(conn) == []                                      # once per import


def test_curve_editor(app, lib):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from lunelis.ui.detail_view import DetailView
    conn, idx = lib
    v = DetailView(conn)
    v.resize(1200, 900)
    v.show()
    v.open(idx, 0)
    v.set_editing(True)
    assert pump(app, lambda: v.edit.has_render)
    ce = v.develop.curves
    assert ce.canvas.hist is not None
    c = ce.canvas
    mid = c._to_screen(0.5, 0.5).toPoint()
    QTest.mousePress(c, Qt.MouseButton.LeftButton, pos=mid)                  # add a point on the diagonal
    QTest.mouseMove(c, c._to_screen(0.5, 0.75).toPoint())
    QTest.mouseRelease(c, Qt.MouseButton.LeftButton, pos=c._to_screen(0.5, 0.75).toPoint())
    pts = v.edit.stack.curves["rgb"]
    assert len(pts) == 3 and pts[1][1] == pytest.approx(0.75, abs=0.02)
    assert len(v.edit.history) == 2                                          # one step for the drag
    QTest.mouseDClick(c, Qt.MouseButton.LeftButton, pos=c._to_screen(*pts[1]).toPoint())
    assert "rgb" not in v.edit.stack.curves                                  # back to a straight line
    ce.set_channel("b")
    ce.changed.emit("b", ((0, 0.1), (1, 1)), True)
    assert v.edit.stack.curves == {"b": ((0.0, 0.1), (1.0, 1.0))}
    v.set_editing(False)
    assert store.get(conn, idx.file_id(0)).curves == {"b": ((0.0, 0.1), (1.0, 1.0))}


def test_masks_in_the_panel(app, lib):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from lunelis.ui.detail_view import DetailView
    conn, idx = lib
    v = DetailView(conn)
    v.resize(1200, 900)
    v.show()
    v.open(idx, 0)
    v.set_editing(True)
    assert pump(app, lambda: v.edit.has_render)
    e, tool = v.edit, v.canvas.mask
    e.add_mask("radial")
    assert e.mask_index == 0 and tool.kind == "radial" and tool.overlay is not None
    v.develop.mask_sliders["exposure"].set_value(1.0, emit=True)
    assert e.stack.masks[0].adjust == {"exposure": 1.0}
    # Drag the radial's right handle outwards.
    h = tool._handles()["rx"].toPoint()
    QTest.mousePress(v.canvas, Qt.MouseButton.LeftButton, pos=h)
    QTest.mouseMove(v.canvas, h + type(h)(40, 0))
    QTest.mouseRelease(v.canvas, Qt.MouseButton.LeftButton, pos=h + type(h)(40, 0))
    assert e.stack.masks[0].shape[2] > 0.22
    # Paint with a brush.
    e.add_mask("brush")
    c = v.canvas._fit_rect().center().toPoint()
    QTest.mousePress(v.canvas, Qt.MouseButton.LeftButton, pos=c)
    QTest.mouseMove(v.canvas, c + type(c)(60, 0))
    QTest.mouseRelease(v.canvas, Qt.MouseButton.LeftButton, pos=c + type(c)(60, 0))
    assert len(e.stack.masks[1].strokes) == 1 and not e.stack.masks[1].strokes[0][3]
    v.develop.mask_inv.setChecked(True)
    assert e.stack.masks[1].invert
    e.undo()
    assert not e.stack.masks[1].invert
    v.develop.mask_delete.emit()
    assert len(e.stack.masks) == 1 and e.mask_index == -1 and tool.kind is None
    from unittest import mock
    from lunelis.ui import develop
    with mock.patch.object(develop.QMessageBox, "question", return_value=develop.QMessageBox.StandardButton.No):
        e.add_mask("subject")                                     # no model, download declined: no mask
    assert len(e.stack.masks) == 1
    v.set_editing(False)
    assert len(store.get(conn, idx.file_id(0)).masks) == 1


def test_lens_panel(app, lib):
    from lunelis.ui.detail_view import DetailView
    conn, idx = lib
    fid = idx.file_id(0)
    conn.execute("INSERT OR REPLACE INTO exif (file_id, camera_make, camera_model, lens, focal_length_mm, aperture)"
                 " VALUES (?, 'SONY', 'ILCE-7RM5', 'FE 24-105mm F4 G OSS', 24, 4)", (fid,))
    conn.commit()
    v = DetailView(conn)
    v.resize(1200, 900)
    v.open(idx, 0)
    v.set_editing(True)
    assert pump(app, lambda: v.edit.has_render)
    assert "24-105" in v.develop.lens_name.text() and v.develop.lens_profile.isEnabled()
    v.develop.lens_profile.setChecked(True)
    v.develop.lens_sliders["vignette"].set_value(40, emit=True)
    assert v.edit.stack.lens == {"profile": True, "vignette": 40.0}
    rendered = []
    v.edit.session.rendered.connect(lambda img, fast: rendered.append(img))
    assert pump(app, lambda: rendered)
    v.set_editing(False)
    assert store.get(conn, fid).lens == {"profile": True, "vignette": 40.0}


def test_every_edit_button_really_clicks(app, lib):
    """Real clicks, not .emit(): a QPushButton passes `checked` along, which a
    no-argument signal refuses (the Reset crash, 2026-09-27)."""
    from lunelis.ui.detail_view import DetailView
    conn, idx = lib
    v = DetailView(conn)
    v.resize(1200, 900)
    v.open(idx, 0)
    v.set_editing(True)
    assert pump(app, lambda: v.edit.has_render)
    v.develop.sliders["exposure"].set_value(1.0, emit=True)
    v.develop.reset_b.click()
    assert v.edit.stack.is_identity()
    v.develop.auto_b.click()
    assert pump(app, lambda: bool(v.edit.stack.adjust))      # measured on a pool thread
    v.develop.mask_buttons["radial"].click()
    v.develop.mask_del.click()
    assert not v.edit.stack.masks
    from unittest import mock
    from lunelis.ui import develop
    with mock.patch.object(develop.QMessageBox, "information"):
        v.develop.unpack_b.setEnabled(True)
        v.develop.unpack_b.click()
    v.develop.done_b.click()
    assert not v.editing


def test_opening_a_page_keeps_the_window_maximized(app, lib, monkeypatch):
    from lunelis.ui import main_window as mw
    conn, _ = lib
    monkeypatch.setattr(mw, "open_catalog", lambda _p: conn)
    w = mw.MainWindow()
    try:
        w.showMaximized()
        app.processEvents()
        for page in ("Tags", "Albums", "Quarantine", "Library"):
            w.open_page(page)
            app.processEvents()
            assert w.isMaximized(), page
        w.focus_search()
        assert w.isMaximized()
    finally:
        w._quitting = True
        w.close()


def test_slider_number_box_and_double_click(app, lib):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from lunelis.ui.detail_view import DetailView
    conn, idx = lib
    v = DetailView(conn)
    v.resize(1200, 900)
    v.show()
    v.open(idx, 0)
    v.set_editing(True)
    assert pump(app, lambda: v.edit.has_render)
    s = v.develop.sliders["exposure"]
    s.number.setValue(1.25)                                   # typed (applies on Enter / leaving the box)
    assert v.edit.stack.adjust == {"exposure": 1.25} and s.current() == 1.25
    QTest.mouseDClick(s.slider, Qt.MouseButton.LeftButton, pos=s.slider.rect().center())
    assert v.edit.stack.adjust == {} and s.number.value() == 0
    # Sections fold, and the panel remembers it.
    from lunelis.settings import Settings
    v.develop.sections["Light"].header.click()
    assert not v.develop.sections["Light"].body.isVisible()
    assert "Light" in Settings(conn).get("edit_sections_closed")
    v.develop.sections["Light"].header.click()
    assert "Light" not in Settings(conn).get("edit_sections_closed")
    v.set_editing(False)


def test_undo_while_cropping_moves_the_crop_frame_back(app, lib):
    from lunelis.ui.detail_view import DetailView
    conn, idx = lib
    v = DetailView(conn)
    v.resize(1200, 900)
    v.open(idx, 0)
    v.set_editing(True)
    assert pump(app, lambda: v.edit.has_render)
    before = v.edit.stack.geometry.crop
    v.edit.set_crop_mode(True)
    v.edit._crop_moved((0.1, 0.1, 0.9, 0.9), True)
    assert v.edit.canvas.crop is not None
    v.edit.undo()
    assert v.edit.stack.geometry.crop == before and tuple(v.edit.canvas.crop) == tuple(before)
    v.edit.redo()
    assert tuple(v.edit.canvas.crop) == (0.1, 0.1, 0.9, 0.9)
