"""0.57: every keyboard shortcut, pressed for real in every screen that documents it.

The keys go to the widget that has the keyboard in a shown, active window
(QTest.keyClick), so Qt's own shortcut map, the text boxes' right to a typed
letter and each page's keyPressEvent all take part - and each test looks for
what the user would see, not for a handler having run. An "Ambiguous shortcut"
from Qt (two bindings of one key: neither fires) fails the test it happens in.
"""
import collections

import pytest
from PIL import Image
from PySide6.QtCore import Qt, qInstallMessageHandler
from PySide6.QtGui import QAction, QShortcut
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QFileDialog, QInputDialog, QLineEdit, QMenu, QMessageBox,
)

from lunelis import stacks, turns
from lunelis.importers.scan import add_root, scan_root
from lunelis.settings import Settings
from lunelis.ui import shortcuts
from test_issues_ui_054 import _app, _pump, _shut, _stars

NO = Qt.KeyboardModifier.NoModifier
CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier
ALT = Qt.KeyboardModifier.AltModifier
K = Qt.Key
LEFT = Qt.MouseButton.LeftButton


@pytest.fixture(autouse=True)
def _no_ambiguous_shortcuts():
    said: list[str] = []
    before = qInstallMessageHandler(lambda _mode, _ctx, msg: said.append(msg))
    yield
    qInstallMessageHandler(before)
    assert not [m for m in said if "mbiguous" in m], said


def _lib(tmp_path, n=6, size=(600, 400), clip=False):
    """A shown, active MainWindow on the throwaway catalog with n photos of its
    own (and a short video): (window, photo ids in name order, video id)."""
    _app()
    from lunelis.ui import main_window as mw
    folder = tmp_path / "K"
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        Image.new("RGB", size, (i * 40 % 255, 20, 90)).save(folder / f"k{i}.jpg")
    if clip:
        from test_playback import make_clip
        make_clip(folder / "z_clip.mp4", seconds=4)
    w = mw.MainWindow()
    Settings(w.conn).set("stack_bursts", True)
    rid = add_root(w.conn, folder)
    scan_root(w.conn, rid)
    if clip:
        from lunelis.importers.metadata import extract_pending
        extract_pending(w.conn)
    w.set_filter(mw.Filter())
    mine = {r[0]: r[1] for r in w.conn.execute("SELECT id, filename FROM files WHERE root_id = ?", (rid,))}
    ids = sorted((f for f in mine if mine[f].endswith(".jpg")), key=lambda f: mine[f])
    vid = next((f for f in mine if mine[f].endswith(".mp4")), None)
    assert len(ids) == n and all(w.index.position(f) >= 0 for f in ids)
    w.show()
    w.activateWindow()
    QApplication.processEvents()
    assert QApplication.activeWindow() is w
    return w, ids, vid


def press(key, mods=NO) -> None:
    """The key, to whatever has the keyboard - as the keyboard would send it."""
    target = QApplication.focusWidget()
    assert target is not None, "nothing has the keyboard"
    QTest.keyClick(target, key, mods)
    QApplication.processEvents()


def _select(w, fid) -> None:
    """One photo selected in the Library's grid, the grid holding the keys."""
    w.grid.setFocus()
    w.grid.current = w.grid.anchor = w.index.position(fid)
    w.grid.selected = {fid}
    QApplication.processEvents()
    assert QApplication.focusWidget() is w.grid


def _rating(conn, fid) -> tuple:
    row = conn.execute("SELECT stars, color_label, flag FROM ratings WHERE file_id = ?", (fid,)).fetchone()
    return (row[0] or 0, row[1], row[2]) if row else (0, None, None)


def _wh(canvas):
    return (canvas.pix.width(), canvas.pix.height()) if canvas.pix is not None else None


def _no_dialogs(monkeypatch) -> list[str]:
    """The dialogs the menu keys open are named instead of shown."""
    calls: list[str] = []
    from lunelis.ui import export_dialog, tag_editor
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: calls.append("folder") or ""))
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: calls.append("name") or ("", False)))
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: calls.append("album") or ("", False)))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: calls.append("nothing selected")))
    monkeypatch.setattr(tag_editor.TagDialog, "exec", lambda self: calls.append(f"tags:{len(self.file_ids)}")
                        if hasattr(self, "file_ids") else calls.append("tags"))
    monkeypatch.setattr(export_dialog.ExportDialog, "exec", lambda self: calls.append("export") or 0)
    monkeypatch.setattr(shortcuts.ShortcutSheet, "exec", lambda self: calls.append("sheet:" + self.rows[0][0]))
    return calls


# --- the bindings themselves ---------------------------------------------------------------------

def test_no_key_is_bound_twice_and_every_menu_key_is_on_the_sheet(tmp_path):
    w, ids, _ = _lib(tmp_path, 1)
    try:
        seqs = collections.Counter(s.toString() for a in w.findChildren(QAction) for s in a.shortcuts())
        assert seqs and not {k: n for k, n in seqs.items() if n > 1}
        # ...and nothing a QShortcut holds is a menu key too (each is its own widget's only).
        extra = [(s.key().toString(), type(s.parent()).__name__, s.context()) for s in w.findChildren(QShortcut)]
        assert sorted(k for k, _, _ in extra) == ["Backspace", "Esc", "Esc"]
        assert all(ctx != Qt.ShortcutContext.WindowShortcut and ctx != Qt.ShortcutContext.ApplicationShortcut
                   for _, _, ctx in extra)
        assert not {k for k, _, _ in extra} & set(seqs)
        # A key with an action can be pressed: the action is the window's, or in a menu of its menu bar.
        in_menus = {a for m in w.menuBar().findChildren(QMenu) for a in m.actions()}
        for a in w.findChildren(QAction):
            if a.shortcut().toString():
                assert a in w.actions() or a in in_menus, a.text()
        # The sheet names every one of them, whichever screen it is opened from.
        import re
        for screen in ("Library", "Photo view", "Edit panel", "Map", "Albums", "Create", "Culling"):
            rows = shortcuts.ShortcutSheet(w, screen).rows
            assert rows[0][0].startswith(screen)                       # this screen's keys come first
            listed = {k.strip() for _, keys, _ in rows for k in re.split(r" / |, | or ", keys)}
            digits = {str(n) for n in range(10)}
            missing = set(seqs) - listed - digits
            assert not missing, (screen, missing)
            assert any(k in ("0-5 / 6-9", "0") or k in digits for _, k, _ in rows)
        rows = shortcuts.ShortcutSheet(w, "Library").rows
        assert ("Library", "Backspace") in {(g, k) for g, k, _ in rows}
        assert ("Photo view", "S") in {(g, k) for g, k, _ in rows}
    finally:
        _shut(w)


def test_question_mark_opens_the_sheet_for_the_screen_you_are_on(tmp_path, monkeypatch):
    calls = _no_dialogs(monkeypatch)
    w, ids, _ = _lib(tmp_path, 2)
    try:
        d = w.detail
        _select(w, ids[0])
        press(K.Key_Question)
        assert calls == ["sheet:Library"]
        press(K.Key_Return)
        assert w.pages.currentWidget() is d
        calls.clear(); press(K.Key_Question)
        assert calls == ["sheet:Photo view"]
        press(K.Key_E)
        calls.clear(); press(K.Key_Question)
        assert calls == ["sheet:Edit panel"]
        press(K.Key_E)
        press(K.Key_Escape)
        for page, first in (("Edit", "Edit panel"), ("Map", "Map"), ("Albums", "Albums"), ("Create", "Create"),
                            ("Settings", "Library")):
            w.open_page(page)
            QApplication.processEvents()
            calls.clear(); press(K.Key_Question)
            assert calls == [f"sheet:{first}"], page
        # ...but a ? typed into a text box is a ?.
        w.open_page("Library")
        press(K.Key_F, CTRL)
        assert QApplication.focusWidget() is w.search
        calls.clear(); press(K.Key_Question)
        assert calls == [] and w.search.text() == "?"
        press(K.Key_Escape)
        assert w.search.text() == ""
    finally:
        _shut(w)


# --- Library ---------------------------------------------------------------------------------------

def test_library_rating_label_flag_and_undo_keys(tmp_path):
    w, ids, _ = _lib(tmp_path, 3)
    try:
        a = ids[0]
        _select(w, a)
        for key, n in ((K.Key_1, 1), (K.Key_2, 2), (K.Key_3, 3), (K.Key_4, 4), (K.Key_5, 5), (K.Key_0, 0)):
            press(key)
            assert _rating(w.conn, a)[0] == n
            assert w.index.tile(w.index.position(a)).stars == n          # what the tile draws
            assert ("no stars" if n == 0 else "★" * n) in w.status.text()
        for key, name in ((K.Key_6, "Red"), (K.Key_7, "Yellow"), (K.Key_8, "Green"), (K.Key_9, "Blue")):
            press(key)
            assert _rating(w.conn, a)[1] == name and name in w.status.text()
        press(K.Key_9)                                                    # the same label again takes it off
        assert _rating(w.conn, a)[1] is None
        for key, flag in ((K.Key_P, "pick"), (K.Key_X, "reject"), (K.Key_U, None)):
            press(key)
            assert _rating(w.conn, a)[2] == flag
        press(K.Key_P)
        press(K.Key_Z, CTRL)
        assert _rating(w.conn, a)[2] is None and w.status.text().startswith("Undone")
        press(K.Key_Z, CTRL | SHIFT)
        assert _rating(w.conn, a)[2] == "pick" and w.status.text().startswith("Redone")
        press(K.Key_Z, CTRL)
        press(K.Key_Y, CTRL)
        assert _rating(w.conn, a)[2] == "pick"
        press(K.Key_U)
        # Several selected: the key is on all of them.
        w.grid.selected = set(ids)
        press(K.Key_2)
        assert [_rating(w.conn, f)[0] for f in ids] == [2, 2, 2]
        press(K.Key_Z, CTRL)
        assert [_rating(w.conn, f)[0] for f in ids] == [0, 0, 0]
    finally:
        _shut(w)


def test_library_grid_keys_move_select_and_open(tmp_path):
    w, ids, _ = _lib(tmp_path, 6)
    try:
        g, n = w.grid, len(w.index)
        _select(w, w.index.file_id(0))
        press(K.Key_Right)
        assert g.current == 1 and g.selected == {w.index.file_id(1)}
        press(K.Key_Left)
        assert g.current == 0
        press(K.Key_Down)
        assert g.current == min(n - 1, g.cols)
        press(K.Key_Up)
        assert g.current == max(0, min(n - 1, g.cols) - g.cols)
        press(K.Key_End)
        assert g.current == n - 1
        press(K.Key_Home)
        assert g.current == 0
        press(K.Key_PageDown)
        assert g.current > 0
        press(K.Key_PageUp)
        assert g.current == 0
        press(K.Key_Right, SHIFT)                                          # with Shift: the selection grows
        assert g.selected == {w.index.file_id(0), w.index.file_id(1)}
        press(K.Key_A, CTRL)
        assert len(g.selected) == n and f"{n:,}" in w.count.text() if hasattr(w, "count") else len(g.selected) == n
        press(K.Key_Escape)
        assert g.selected == set()
        press(K.Key_A, CTRL)
        press(K.Key_D, CTRL)
        assert g.selected == set()
        _select(w, w.index.file_id(0))
        press(K.Key_I, CTRL | SHIFT)
        assert len(g.selected) == n - 1 and w.index.file_id(0) not in g.selected
        _select(w, ids[2])
        press(K.Key_Space)                                                 # this photo out of the selection...
        assert g.selected == set()
        press(K.Key_Space)                                                 # ...and in again
        assert g.selected == {ids[2]}
        # The menu key and Shift+F10: the Photo menu, at the photo.
        shown = []
        w.photo_menu.exec = lambda *a: shown.append(a)                     # (taken off again before the window goes)
        try:
            press(K.Key_Menu)
            press(K.Key_F10, SHIFT)
        finally:
            del w.photo_menu.exec
        assert len(shown) == 2
        # Enter opens the photo; Esc comes back to it, selected, the grid holding the keys.
        press(K.Key_Return)
        assert w.pages.currentWidget() is w.detail and w.detail.info.file_id == ids[2]
        press(K.Key_Escape)
        assert w.pages.currentWidget() is g and g.selected == {ids[2]} and QApplication.focusWidget() is g
    finally:
        _shut(w)


def test_library_stack_rotate_archive_and_the_keys_that_open_dialogs(tmp_path, monkeypatch):
    calls = _no_dialogs(monkeypatch)
    w, ids, _ = _lib(tmp_path, 6)
    try:
        stacks.make_burst(w.conn, ids[:3])
        w.reload()
        n = len(w.index)
        cover = next(f for f in ids[:3] if w.index.position(f) >= 0)
        _select(w, cover)
        press(K.Key_S)                                                     # the burst's frames, each a tile
        assert len(w.index) == n + 2 and all(w.index.position(f) >= 0 for f in ids[:3])
        press(K.Key_S)
        assert len(w.index) == n and w.grid.selected == {cover}
        _select(w, ids[4])
        press(K.Key_S)
        assert "isn't part of a burst stack" in w.status.text()
        # Rotate: the photo is shown turned (the file isn't touched).
        press(K.Key_BracketRight, CTRL)
        assert turns.get(ids[4]) == 1 and "Turned 1 photo" in w.status.text()
        press(K.Key_BracketLeft, CTRL)
        assert turns.get(ids[4]) == 0
        # Archive: out of the library, and Ctrl+Z brings it back.
        press(K.Key_H, CTRL | SHIFT)
        assert w.index.position(ids[4]) == -1 and "Archived 1 photo" in w.status.text()
        w.grid.setFocus()
        press(K.Key_Z, CTRL)
        assert w.index.position(ids[4]) >= 0
        # The dialogs.
        _select(w, ids[4])
        for key, mods, opened in ((K.Key_O, CTRL, "folder"), (K.Key_T, CTRL, "tags"), (K.Key_E, CTRL, "name"),
                                  (K.Key_A, CTRL | SHIFT, "album"), (K.Key_E, CTRL | SHIFT, "export")):
            calls.clear()
            press(key, mods)
            assert len(calls) == 1 and calls[0].startswith(opened), (opened, calls)
        press(K.Key_F, CTRL | ALT)                                         # no scene model here: it says so
        assert "Find similar needs the scene model" in w.status.text()
        press(K.Key_C, CTRL | SHIFT)
        assert "no edits to copy" in w.status.text()
    finally:
        _shut(w)


def test_backspace_in_the_library_leaves_an_album_for_the_page_it_came_from(tmp_path, monkeypatch):
    from lunelis.albums import model as albums
    from lunelis.ui import albums_view
    from lunelis.ui.albums_view import AlbumTile, NewAlbumTile
    w, ids, _ = _lib(tmp_path, 3)
    aid = albums.create(w.conn, "shortcut audit 057", ids[:2])
    try:
        def tile() -> AlbumTile:
            QApplication.processEvents()
            assert _pump(lambda: any(t.album.name == "shortcut audit 057" for t in w.albums_page.findChildren(AlbumTile)))
            t = next(t for t in w.albums_page.findChildren(AlbumTile) if t.album.name == "shortcut audit 057")
            t.setFocus()
            QApplication.processEvents()
            assert QApplication.focusWidget() is t
            return t
        w.open_page("Albums")
        tile()
        press(K.Key_Return)                                                # Albums: Enter opens the album
        assert w.pages.currentWidget() is w.grid and w.filter.album_id == aid
        assert {w.index.file_id(i) for i in range(len(w.index))} == set(ids[:2])
        assert QApplication.focusWidget() is w.grid or w.grid.isAncestorOf(QApplication.focusWidget())
        w.grid.setFocus()
        press(K.Key_Backspace)                                             # Library: Backspace goes back out
        assert w._page_name == "Albums"
        tile()
        press(K.Key_Space)                                                 # Albums: Space opens it too
        assert w.pages.currentWidget() is w.grid and w.filter.album_id == aid
        w.grid.setFocus()
        press(K.Key_Backspace)
        # The menu key: the album's own menu.
        menus = []

        class Menu(QMenu):
            def exec(self, *a):
                menus.append([x.text() for x in self.actions()])
        monkeypatch.setattr(albums_view, "QMenu", Menu)
        tile()
        press(K.Key_Menu)
        press(K.Key_F10, SHIFT)
        assert len(menus) == 2 and "Remove album" in menus[0]
        # The "New album" tile answers to the same keys.
        asked = []
        monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: asked.append(1) or ("", False)))
        new = w.albums_page.findChildren(NewAlbumTile)[0]
        new.setFocus()
        press(K.Key_Return)
        new.setFocus()
        press(K.Key_Space)
        assert len(asked) == 2
    finally:
        albums.delete(w.conn, aid)
        _shut(w)


def test_typing_in_the_search_and_ask_boxes_is_typing(tmp_path):
    w, ids, _ = _lib(tmp_path, 3)
    try:
        stacks.make_burst(w.conn, ids[:2])
        w.reload()
        cover = next(f for f in ids[:2] if w.index.position(f) >= 0)
        _select(w, cover)
        n, was = len(w.index), _rating(w.conn, cover)
        press(K.Key_F, CTRL)
        assert QApplication.focusWidget() is w.search
        for key in (K.Key_3, K.Key_S, K.Key_P, K.Key_X, K.Key_U, K.Key_7, K.Key_Space, K.Key_E, K.Key_F):
            press(key)
        assert w.search.text() == "3spxu7 ef"
        assert _rating(w.conn, cover) == was and len(w.index) == n       # nothing was rated, no stack opened
        press(K.Key_Escape)                                                # Esc: the search is cleared
        assert w.search.text() == ""
        _pump(lambda: False, 0.35)                                         # the search's own delay
        # Ask (Ctrl+Shift+F) takes the search box's place; Esc puts it back.
        press(K.Key_F, CTRL | SHIFT)
        box = w.ask_bar.box
        assert QApplication.focusWidget() is box and box.isVisible() and not w.search.isVisible()
        for key in (K.Key_S, K.Key_3, K.Key_P):
            press(key)
        assert box.text() == "s3p" and _rating(w.conn, cover) == was and len(w.index) == n
        press(K.Key_Escape)
        assert not box.isVisible() and w.search.isVisible()
    finally:
        _shut(w)


# --- Photo view ------------------------------------------------------------------------------------

def test_photo_view_keys(tmp_path):
    w, ids, _ = _lib(tmp_path, 4, size=(2400, 1600))
    faces_were = Settings(w.conn).get("faces_overlay")
    try:
        d = w.detail
        _select(w, ids[1])
        press(K.Key_Return)
        assert w.pages.currentWidget() is d and QApplication.focusWidget() is d
        pos, n = d.pos, len(d.index)
        assert 0 < pos < n - 1 or n >= 3
        d.go(1)
        press(K.Key_Right)
        assert d.pos == 2 and d.counter.text() == f"3 of {n:,}"
        press(K.Key_Left)
        assert d.pos == 1
        press(K.Key_Space)
        assert d.pos == 2
        press(K.Key_Home)
        assert d.pos == 0
        press(K.Key_End)
        assert d.pos == n - 1
        d.go(d.index.position(ids[1]))
        # Z: 100 % and back to fit.
        assert _pump(lambda: d.canvas.pix is not None and d.canvas.pix.width() > 1000)
        press(K.Key_Z)
        assert d.canvas.scale is not None
        press(K.Key_Z)
        assert d.canvas.scale is None
        # F: the faces and their names.
        on = d.faces_b.isChecked()
        press(K.Key_F)
        assert d.faces_b.isChecked() is not on and d.canvas.show_faces is not on
        press(K.Key_F)
        assert d.faces_b.isChecked() is on
        # Stars, labels, flags: the photo on screen, and its bar shows them.
        press(K.Key_4)
        assert _rating(w.conn, ids[1])[0] == 4 and d.stars.text() == "★★★★☆"
        press(K.Key_8)
        assert d.info.label == "Green"
        press(K.Key_X)
        assert d.info.flag == "reject"
        press(K.Key_Z, CTRL)
        assert d.info.flag is None and w.status.text().startswith("Undone")
        press(K.Key_Y, CTRL)
        assert d.info.flag == "reject"
        press(K.Key_U)
        press(K.Key_8)
        press(K.Key_0)
        assert _rating(w.conn, ids[1]) == (0, None, None) and d.stars.text() == ""
        # Ctrl+A is the grid's: nothing behind the photo gets selected.
        press(K.Key_A, CTRL)
        assert w.grid.selected == {ids[1]}
        # Rotate: the photo on screen turns.
        assert _pump(lambda: _wh(d.canvas) == (2400, 1600))
        press(K.Key_BracketRight, CTRL)
        assert _pump(lambda: _wh(d.canvas) == (1600, 2400))
        press(K.Key_BracketLeft, CTRL)
        assert _pump(lambda: _wh(d.canvas) == (2400, 1600))
        # E: the Edit panel, and E again closes it.
        press(K.Key_E)
        assert d.editing and d.side.currentWidget() is d.develop and d.edit_b.isChecked()
        press(K.Key_E)
        assert not d.editing and d.side.currentWidget() is d.panel
        # Ctrl+Shift+L: the Map, placing this photo, the map holding the keys.
        press(K.Key_L, CTRL | SHIFT)
        assert w._page_name == "Map" and w.map_page._placing == [ids[1]]
        assert QApplication.focusWidget() is w.map_page.canvas
        w.map_page.stop_placing()
        # Backspace and Esc both go back to the library.
        for key in (K.Key_Backspace, K.Key_Escape):
            w.open_page("Library")
            w.open_detail(ids[1])
            QApplication.processEvents()
            press(key)
            assert w.pages.currentWidget() is w.grid and w.grid.selected == {ids[1]}
    finally:
        Settings(w.conn).set("faces_overlay", faces_were)
        _shut(w)


def test_s_in_the_photo_view_shows_in_the_filmstrip(tmp_path):
    w, ids, _ = _lib(tmp_path, 5)
    try:
        d = w.detail
        stacks.make_burst(w.conn, ids[:3])
        w.reload()
        cover = next(f for f in ids[:3] if w.index.position(f) >= 0)
        frame = next(f for f in ids[:3] if f != cover)
        w.open_detail(ids[4])
        QApplication.processEvents()
        n = len(d.index)
        d.go(d.index.position(cover))
        press(K.Key_S)
        assert len(d.index) == n + 2 and d.info.file_id == cover and "3 frames" in w.status.text()
        assert d.counter.text().endswith(f"of {n + 2:,}")
        press(K.Key_S)
        assert len(d.index) == n and d.info.file_id == cover and w.status.text() == "Stack closed"
        press(K.Key_S)
        # Closed from a frame behind the cover: the frame stays (and stays under
        # the keys) - and the status line says so, rather than "Stack closed"
        # over a filmstrip that still shows every frame.
        d.go(d.index.position(frame))
        press(K.Key_S)
        assert d.info.file_id == frame and w.index.position(frame) == -1
        assert "stays open here" in w.status.text()
        press(K.Key_3)
        assert _rating(w.conn, frame)[0] == 3 and _rating(w.conn, cover)[0] == 0
        press(K.Key_0)
        press(K.Key_Escape)
    finally:
        _shut(w)


def test_a_click_on_a_button_leaves_the_keys_with_the_photo(tmp_path):
    w, ids, _ = _lib(tmp_path, 4)
    faces_were = Settings(w.conn).get("faces_overlay")
    try:
        d = w.detail
        w.open_detail(ids[1])
        QApplication.processEvents()
        for name in ("next_b", "prev_b", "faces_b", "rotate_r", "rotate_l", "edit_b"):
            d.go(1)
            QTest.mouseClick(getattr(d, name), LEFT)
            QApplication.processEvents()
            if name == "edit_b":
                assert d.editing
                QTest.mouseClick(d.edit_b, LEFT)
                assert not d.editing
            assert QApplication.focusWidget() is d, name
            at = d.pos
            press(K.Key_Right)                                             # the next photo, not the next button
            assert d.pos == at + 1, name
        if d.faces_b.isChecked() != bool(faces_were):
            press(K.Key_F)
        # The stars and flags in the Info panel are buttons too.
        d.go(1)
        clicked = 0
        for b in d.panel.findChildren(type(d.next_b)):
            if b.isVisible() and b.toolTip().startswith(("3 stars", "Pick")):
                QTest.mouseClick(b, LEFT)
                clicked += 1
                assert QApplication.focusWidget() is d
        assert clicked == 2 and _rating(w.conn, d.info.file_id)[0] == 3 and d.info.flag == "pick"
        press(K.Key_0)
        press(K.Key_U)
        press(K.Key_Left)
        assert d.pos == 0
        press(K.Key_Escape)
    finally:
        Settings(w.conn).set("faces_overlay", faces_were)
        _shut(w)


def test_typing_in_the_photo_views_tag_box_is_typing(tmp_path):
    w, ids, _ = _lib(tmp_path, 4)
    try:
        d = w.detail
        w.open_detail(ids[1])
        QApplication.processEvents()
        box = next(e for e in d.panel.tag_box.findChildren(QLineEdit))
        box.setFocus()
        QApplication.processEvents()
        was, at = _rating(w.conn, ids[1]), d.pos
        for key in (K.Key_S, K.Key_5, K.Key_P, K.Key_X, K.Key_U, K.Key_E, K.Key_Space, K.Key_Z, K.Key_F,
                    K.Key_K, K.Key_Backspace):
            press(key)
        assert box.text() == "s5pxue zf"
        press(K.Key_Home)
        press(K.Key_End)
        press(K.Key_Up)                                                    # not "the photo before"
        press(K.Key_Down)
        assert _rating(w.conn, ids[1]) == was and d.pos == at and not d.editing
        box.clear()
        # Esc leaves the box, not the photo; a second Esc is Back.
        press(K.Key_Escape)
        assert w.pages.currentWidget() is d and QApplication.focusWidget() is d
        press(K.Key_Escape)
        assert w.pages.currentWidget() is w.grid
    finally:
        _shut(w)


# --- Edit panel ------------------------------------------------------------------------------------

def test_edit_panel_keys_in_the_photo_view(tmp_path):
    w, ids, _ = _lib(tmp_path, 4)
    try:
        d = w.detail
        w.open_detail(ids[1])
        QApplication.processEvents()
        press(K.Key_E)
        assert d.editing and _pump(lambda: d.edit.active and d.edit.has_render)
        assert QApplication.focusWidget() is d
        # F is the photo view's: no faces over a photo being edited.
        on = d.faces_b.isChecked()
        press(K.Key_F)
        assert d.faces_b.isChecked() is on
        # Backslash: the original, and back.
        press(K.Key_Backslash)
        assert d.edit.showing_before and d.develop.before_b.isChecked()
        press(K.Key_Backslash)
        assert not d.edit.showing_before and not d.develop.before_b.isChecked()
        # Undo / redo are the edit's while one is open.
        d.edit._adjust("exposure", 0.5, True)
        assert d.edit.stack.adjust == {"exposure": 0.5}
        press(K.Key_Z, CTRL)
        assert d.edit.stack.adjust == {}
        press(K.Key_Z, CTRL | SHIFT)
        assert d.edit.stack.adjust == {"exposure": 0.5}
        press(K.Key_Z, CTRL)
        press(K.Key_Y, CTRL)
        assert d.edit.stack.adjust == {"exposure": 0.5}
        # R: cropping, finished by R, Enter or Esc - and Esc then doesn't leave editing.
        for done in (K.Key_R, K.Key_Return, K.Key_Enter, K.Key_Escape):
            press(K.Key_R)
            assert d.edit.cropping and d.develop.crop_b.isChecked()
            press(done)
            assert not d.edit.cropping and not d.develop.crop_b.isChecked() and d.editing
        # O: the selected mask's overlay; Esc deselects the mask first.
        d.edit.add_mask("linear")
        assert d.edit.mask_index == 0 and d.canvas.mask.show_overlay
        press(K.Key_O)
        assert not d.canvas.mask.show_overlay
        press(K.Key_O)
        assert d.canvas.mask.show_overlay
        press(K.Key_Escape)
        assert d.edit.mask_index == -1 and d.editing
        # The rating keys still work on the photo being edited.
        press(K.Key_3)
        assert _rating(w.conn, ids[1])[0] == 3
        press(K.Key_0)
        # Rotate left / right shows at once, edit open or not.
        was = _wh(d.canvas)
        press(K.Key_BracketRight, CTRL)
        assert _pump(lambda: _wh(d.canvas) == (was[1], was[0]))
        press(K.Key_BracketLeft, CTRL)
        assert _pump(lambda: _wh(d.canvas) == was)
        # Copy the edit, paste it on the next photo.
        press(K.Key_C, CTRL | SHIFT)
        assert "Copied the edit settings" in w.status.text()
        press(K.Key_Right)
        assert d.info.file_id != ids[1] and _pump(lambda: d.edit.active)
        press(K.Key_V, CTRL | SHIFT)
        assert "Pasted the edit settings onto 1 photo" in w.status.text()
        assert _pump(lambda: d.edit.active and d.edit.stack.adjust == {"exposure": 0.5})
        # A number typed into a slider's box is a number: no stars, and its Enter
        # and Esc are the box's (Esc hands the keys back; the panel stays open).
        spin = next(s for s in d.develop.findChildren(QAbstractSpinBox) if s.isVisible() and s.isEnabled())
        spin.setFocus()
        QApplication.processEvents()
        fid = d.info.file_id
        press(K.Key_R)
        press(K.Key_2)
        press(K.Key_Return)
        assert _rating(w.conn, fid)[0] == 0 and not d.edit.cropping and d.editing
        press(K.Key_Escape)
        assert d.editing and QApplication.focusWidget() is d
        # Esc: out of editing, still on the photo.
        press(K.Key_Escape)
        assert not d.editing and w.pages.currentWidget() is d
        press(K.Key_Escape)
        assert w.pages.currentWidget() is w.grid
    finally:
        _shut(w)


def test_edit_page_keys(tmp_path):
    w, ids, _ = _lib(tmp_path, 5)
    try:
        v = w.edit_page.view
        _select(w, ids[4])
        QTest.mouseClick(w._nav["Edit"], LEFT)                             # the sidebar, as a user gets there
        QApplication.processEvents()
        assert w._page_name == "Edit" and v.info.file_id == ids[4]
        assert QApplication.focusWidget() is v and _pump(lambda: v.edit.active and v.edit.has_render)
        press(K.Key_R)
        assert v.edit.cropping
        press(K.Key_R)
        assert not v.edit.cropping
        press(K.Key_Backslash)
        assert v.edit.showing_before
        press(K.Key_Backslash)
        v.edit._adjust("exposure", 0.5, True)
        press(K.Key_Z, CTRL)
        assert v.edit.stack.adjust == {}
        press(K.Key_Y, CTRL)
        assert v.edit.stack.adjust == {"exposure": 0.5}
        press(K.Key_Z, CTRL)
        v.edit.add_mask("radial")
        press(K.Key_O)
        assert not v.canvas.mask.show_overlay
        press(K.Key_Escape)                                                # the mask first...
        assert v.edit.mask_index == -1 and w._page_name == "Edit"
        press(K.Key_Z, CTRL)
        press(K.Key_E)                                                     # the Edit page never leaves editing
        assert v.editing
        press(K.Key_3)
        assert _rating(w.conn, ids[4])[0] == 3 and v.stars.text() == "★★★☆☆"
        press(K.Key_0)
        press(K.Key_A, CTRL)
        assert w.grid.selected == {ids[4]}
        # Rotate: the photo here turns (0.57: only the status line said so) - by
        # the key and by the two buttons beside the counter.
        was = _wh(v.canvas)
        press(K.Key_BracketRight, CTRL)
        assert _pump(lambda: _wh(v.canvas) == (was[1], was[0]))
        QTest.mouseClick(v.rotate_l, LEFT)
        assert _pump(lambda: _wh(v.canvas) == was) and turns.get(ids[4]) == 0
        # Esc and Backspace: back to the page it was opened from.
        press(K.Key_Escape)
        assert w._page_name == "Library"
        w.open_page("Edit")
        QApplication.processEvents()
        press(K.Key_Backspace)
        assert w._page_name == "Library"
    finally:
        _shut(w)


def test_s_on_the_edit_page_opens_the_stack_there_not_in_the_library_behind_it(tmp_path):
    w, ids, _ = _lib(tmp_path, 5)
    try:
        v = w.edit_page.view
        stacks.make_burst(w.conn, ids[:3])
        w.reload()
        lib = len(w.index)
        cover = next(f for f in ids[:3] if w.index.position(f) >= 0)
        frame = next(f for f in ids[:3] if f != cover)
        w.grid.clear_selection()
        w.open_page("Edit")                                                # "What the library shows now"
        QApplication.processEvents()
        assert w.edit_page.source.currentData() == "view"
        v.go(v.index.position(cover))
        n = len(v.index)
        press(K.Key_S)
        assert len(v.index) == n + 2 and v.info.file_id == cover and "3 frames" in w.status.text()
        assert v.counter.text().endswith(f"of {n + 2:,}") and len(w.index) == lib      # the Library is as it was
        v.go(v.index.position(frame))
        press(K.Key_S)                                                     # closed from a frame: its cover
        assert len(v.index) == n and v.info.file_id == cover and w.status.text() == "Stack closed"
        assert len(w.index) == lib and w.edit_page.current() == cover
        press(K.Key_Escape)
        # One selected photo is the whole list: there are no frames to show, and it says so.
        _select(w, cover)
        w.open_page("Edit")
        QApplication.processEvents()
        assert w.edit_page.source.currentData() == "selection" and len(v.index) == 1
        press(K.Key_S)
        assert len(v.index) == 1 and len(w.index) == lib and "Only this frame" in w.status.text()
        press(K.Key_S)
        press(K.Key_Escape)
    finally:
        _shut(w)


# --- Video -----------------------------------------------------------------------------------------

def test_video_keys(tmp_path):
    pytest.importorskip("av")
    from PySide6.QtMultimedia import QMediaPlayer
    Playing = QMediaPlayer.PlaybackState.PlayingState
    w, ids, vid = _lib(tmp_path, 2, clip=True)
    try:
        d = w.detail
        w.open_detail(vid)
        QApplication.processEvents()
        p = d.player
        assert d.info.is_video and p is not None and d.view_stack.currentWidget() is p
        assert _pump(lambda: p.player.duration() > 3000), "the player never read the clip"
        d.setFocus()
        if p.player.playbackState() == Playing:
            p.toggle()
            assert _pump(lambda: p.player.playbackState() != Playing)
        press(K.Key_Space)
        assert _pump(lambda: p.player.playbackState() == Playing)
        press(K.Key_K)
        assert _pump(lambda: p.player.playbackState() != Playing)
        press(K.Key_K)
        assert _pump(lambda: p.player.playbackState() == Playing)
        press(K.Key_Space)
        assert _pump(lambda: p.player.playbackState() != Playing)
        p.player.setPosition(0)
        assert _pump(lambda: p.player.position() < 200)
        press(K.Key_L)                                                     # 5 s on: the end of a 4 s clip
        assert _pump(lambda: p.player.position() > 3000)
        press(K.Key_J)
        assert _pump(lambda: p.player.position() < 200)
        p.player.setPosition(1000)
        assert _pump(lambda: 900 <= p.player.position() <= 1500)
        press(K.Key_I)
        assert p.mark_in is not None and p.in_b.text().startswith("Start:")
        p.player.setPosition(3000)
        assert _pump(lambda: p.player.position() >= 2900)
        press(K.Key_O)
        assert p.mark_out is not None and p.mark_out > p.mark_in and p.save_b.isEnabled()
        # The keys every photo has: stars, and E leaves a video alone.
        press(K.Key_2)
        assert _rating(w.conn, vid)[0] == 2
        press(K.Key_0)
        press(K.Key_E)
        assert not d.editing
        # Left / Right: the photo or video before / after.
        at = d.pos
        press(K.Key_Right if at < len(d.index) - 1 else K.Key_Left)
        assert d.pos != at and not d.info.is_video
        press(K.Key_Left if d.pos > at else K.Key_Right)
        assert d.info.file_id == vid
        press(K.Key_Backspace)
        assert w.pages.currentWidget() is w.grid
    finally:
        _shut(w)


# --- Culling ---------------------------------------------------------------------------------------

def test_cull_keys(tmp_path, monkeypatch):
    calls = _no_dialogs(monkeypatch)
    w, ids, _ = _lib(tmp_path, 5, size=(2400, 1600))
    cv = None
    try:
        d = w.detail
        # From the photo view, with an edit open behind the cull window.
        w.open_detail(ids[2])
        QApplication.processEvents()
        press(K.Key_E)
        assert _pump(lambda: d.edit.active)
        d.edit._adjust("exposure", 0.5, True)
        press(K.Key_K, CTRL)
        cv = w.cull_view
        cv.activateWindow()
        QApplication.processEvents()
        assert cv.isVisible() and QApplication.activeWindow() is cv
        # (every frame of a burst is there to choose between, so it can be more than the grid's tiles)
        assert cv.current() == ids[2] and set(ids) <= set(cv.ids) and len(cv.ids) >= len(w.index)

        def key(k, mods=NO) -> None:
            QTest.keyClick(QApplication.focusWidget() or cv, k, mods)
            QApplication.processEvents()
        cur = cv.current()
        assert cv.auto_advance and "auto-advance on" in cv.info.text()
        key(K.Key_A)
        assert not cv.auto_advance and "auto-advance off" in cv.info.text()
        key(K.Key_3)
        assert _rating(w.conn, cur)[0] == 3 and "★★★" in cv.info.text()
        key(K.Key_P)
        assert "Picked" in cv.info.text()
        key(K.Key_X)
        assert "Rejected" in cv.info.text()
        key(K.Key_U)
        assert _rating(w.conn, cur)[2] is None and "Rejected" not in cv.info.text()
        for k, name in ((K.Key_6, "Red"), (K.Key_7, "Yellow"), (K.Key_8, "Green"), (K.Key_9, "Blue")):
            key(k)
            assert _rating(w.conn, cur)[1] == name and name in cv.info.text()
        # Undo / redo: the rating - not the edit open in the window behind (0.57).
        key(K.Key_Z, CTRL)
        assert _rating(w.conn, cur)[1] == "Green" and "Green" in cv.info.text()
        assert d.edit.stack.adjust == {"exposure": 0.5}
        key(K.Key_Y, CTRL)
        assert _rating(w.conn, cur)[1] == "Blue"
        key(K.Key_Z, CTRL)
        key(K.Key_Z, CTRL | SHIFT)
        assert _rating(w.conn, cur)[1] == "Blue"
        key(K.Key_9)                                                       # the label off again
        key(K.Key_0)
        assert _rating(w.conn, cur) == (0, None, None)
        # Moving, comparing, Tab.
        cv.pos = 1
        cv._show()
        key(K.Key_Right)
        assert cv.pos == 2 and cv.info.text().startswith("3 of")
        key(K.Key_Left)
        assert cv.pos == 1
        key(K.Key_C)
        assert cv.group == 2 and len(cv.canvases) == 2 and cv.canvases[0].marked
        key(K.Key_Tab)
        assert cv.active == 1 and cv.canvases[1].marked and cv.info.text().startswith("3 of")
        key(K.Key_Right)                                                   # the pair moves along
        assert cv.pos == 3
        key(K.Key_Left)
        key(K.Key_C)
        key(K.Key_C)
        assert cv.group == 4 and len(cv.canvases) == 4
        key(K.Key_C)
        assert cv.group == 1 and len(cv.canvases) == 1
        # Z: 100 % and back.
        assert _pump(lambda: cv.canvases[0].pix is not None and cv.canvases[0].pix.width() > 1000)
        key(K.Key_Z)
        assert cv.canvases[0].scale is not None
        key(K.Key_Z)
        assert cv.canvases[0].scale is None
        # Auto-advance: a rating moves on to the next photo.
        key(K.Key_A)
        cur, at = cv.current(), cv.pos
        key(K.Key_4)
        assert _rating(w.conn, cur)[0] == 4 and cv.pos == at + 1
        key(K.Key_Left)
        key(K.Key_A)
        key(K.Key_0)
        assert _rating(w.conn, cur)[0] == 0
        # ? and Esc.
        calls.clear()
        key(K.Key_Question)
        assert calls == ["sheet:Culling (Photo > Cull full screen, Ctrl+K)"]
        key(K.Key_Escape)
        QApplication.processEvents()
        assert not cv.isVisible()
        w.activateWindow()
        QApplication.processEvents()
        d.setFocus()
        press(K.Key_Z, CTRL)                                               # the edit's own undo is still there
        assert d.edit.stack.adjust == {}
        press(K.Key_E)
        press(K.Key_Escape)
    finally:
        if cv is not None:
            cv.close()
        _shut(w)


# --- Map, Create, everywhere -----------------------------------------------------------------------

def test_map_keys_work_as_soon_as_the_map_is_opened(tmp_path):
    w, ids, _ = _lib(tmp_path, 1)
    try:
        QTest.mouseClick(w._nav["Map"], LEFT)                              # the sidebar, as a user gets there
        QApplication.processEvents()
        c = w.map_page.canvas
        assert w._page_name == "Map" and QApplication.focusWidget() is c
        z = c.z
        press(K.Key_Plus)
        assert c.z == z + 1
        press(K.Key_Equal)
        assert c.z == z + 2
        press(K.Key_Minus)
        assert c.z == z + 1
        press(K.Key_Minus)
        assert c.z == z
        # The photo keys mean nothing here: no photo is rated from the Map.
        w.grid.selected = {ids[0]}
        press(K.Key_5)
        press(K.Key_P)
        press(K.Key_S)
        assert _rating(w.conn, ids[0]) == (0, None, None)
    finally:
        _shut(w)


def test_create_keys(tmp_path):
    w, ids, _ = _lib(tmp_path, 1)
    try:
        cp = w.create_page
        w.open_page("Create")
        QApplication.processEvents()
        for open_key in (K.Key_Return, K.Key_Enter, K.Key_Space):
            card = cp.cards["collage"]
            card.setFocus()
            QApplication.processEvents()
            assert QApplication.focusWidget() is card
            press(open_key)
            assert cp.stack.currentWidget() is cp._scrolls["collage"]
            press(K.Key_Escape)                                            # back to the list of tools
            assert cp.stack.currentIndex() == 0
        QTest.mouseClick(cp.cards["collage"], LEFT)                        # opened with the mouse: Esc still works
        QApplication.processEvents()
        assert cp.stack.currentWidget() is cp._scrolls["collage"]
        press(K.Key_Escape)
        assert cp.stack.currentIndex() == 0
    finally:
        _shut(w)


def test_keys_that_work_everywhere(tmp_path, monkeypatch):
    calls = _no_dialogs(monkeypatch)
    w, ids, _ = _lib(tmp_path, 2)
    compact = bool(Settings(w.conn).get("sidebar_compact"))
    try:
        _select(w, ids[0])
        # Ctrl+B: the sidebar folds to icons, and back.
        press(K.Key_B, CTRL)
        assert w._sidebar_compact is (not compact) and w._nav["Library"].text() == ("" if not compact else "  Library")
        press(K.Key_B, CTRL)
        assert w._sidebar_compact is compact
        # F5: every folder is scanned again. Ctrl+. only while a scan runs.
        started = []
        w.start = lambda roots: started.append(list(roots))                # (taken off again before the window goes)
        try:
            press(K.Key_F5)
        finally:
            del w.start
        assert len(started) == 1 and started[0]
        assert not w.cancel_action.isEnabled() and w.cancel_action.shortcut().toString() == "Ctrl+."
        # The pages.
        press(K.Key_I, CTRL)
        assert w._page_name == "Import" and w.pages.currentWidget() is w.importer
        press(K.Key_Comma, CTRL)
        assert w._page_name == "Settings" and w.pages.currentWidget() is w.settings_page
        # Off the photo pages the photo keys do nothing - no hidden photo is rated,
        # archived or put in an event.
        for key, mods in ((K.Key_5, NO), (K.Key_P, NO), (K.Key_7, NO), (K.Key_S, NO), (K.Key_H, CTRL | SHIFT),
                          (K.Key_A, CTRL), (K.Key_E, CTRL), (K.Key_A, CTRL | SHIFT)):
            press(key, mods)
        assert _rating(w.conn, ids[0]) == (0, None, None) and w.index.position(ids[0]) >= 0
        assert w.grid.selected == {ids[0]} and calls == []
        # Ctrl+J: the jobs window.
        press(K.Key_J, CTRL)
        assert w._jobs_dialog is not None and w._jobs_dialog.isVisible()
        w._jobs_dialog.close()
        w.activateWindow()
        QApplication.processEvents()
        # Ctrl+F from another page: the Library, the search box holding the keys.
        w.open_page("Settings")
        press(K.Key_F, CTRL)
        assert w._page_name == "Library" and QApplication.focusWidget() is w.search
        press(K.Key_Escape)
        # Ctrl+K: culling what the library shows; Ctrl+O: the folder chooser.
        w.grid.setFocus()
        press(K.Key_K, CTRL)
        assert w.cull_view.isVisible() and set(ids) <= set(w.cull_view.ids)
        w.cull_view.close()
        w.activateWindow()
        QApplication.processEvents()
        w.grid.setFocus()
        calls.clear()
        press(K.Key_O, CTRL)
        assert calls == ["folder"]
        # The sidebar's Library hands the keys to the grid (0.57: they stayed on the sidebar).
        w.open_page("Settings")
        QTest.mouseClick(w._nav["Library"], LEFT)
        QApplication.processEvents()
        assert w._page_name == "Library" and QApplication.focusWidget() is w.grid
        w.grid.current = 0
        press(K.Key_End)
        assert w.grid.current == len(w.index) - 1
    finally:
        Settings(w.conn).set("sidebar_compact", compact)
        _shut(w)
