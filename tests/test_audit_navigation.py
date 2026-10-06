"""0.37.3 (audit LRA-066/067/068/069/072/073/074): pages, Back and keys go
where they say."""
from PIL import Image
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from lunelis.importers.scan import add_root, scan_root


def _key(w, k):
    QApplication.sendEvent(w, QKeyEvent(QEvent.Type.KeyPress, k, Qt.KeyboardModifier.NoModifier))


def _window(tmp_path, n=3):
    QApplication.instance() or QApplication([])
    from lunelis.ui import main_window as mw
    w = mw.MainWindow()
    root = tmp_path / "N"
    root.mkdir()
    for i in range(n):
        Image.new("RGB", (60, 40), (i * 60, 20, 0)).save(root / f"n{i}.jpg")
    rid = add_root(w.conn, root)
    scan_root(w.conn, rid)
    w.reload()
    mine = {r[0] for r in w.conn.execute("SELECT id FROM files WHERE root_id = ?", (rid,))}
    ids = [w.index.file_id(i) for i in range(len(w.index)) if w.index.file_id(i) in mine]
    return w, ids


def test_back_from_a_photo_opened_on_people_returns_to_people(tmp_path):
    w, ids = _window(tmp_path)
    try:
        w.open_page("People")
        w.open_detail(ids[0])
        w.close_detail()
        assert w.pages.currentWidget() is w.people_page and w._nav["People"].isChecked()
        w.open_page("Library")
        w.open_detail(ids[0])
        w.close_detail()
        assert w.pages.currentWidget() is w.grid and w._nav["Library"].isChecked()
    finally:
        w._quitting = True
        w.close()


def test_leaving_the_photo_view_by_the_sidebar_closes_its_edit(tmp_path):
    w, ids = _window(tmp_path)
    try:
        w.open_page("Library")
        w.open_detail(ids[0])
        w.detail.set_editing(True)
        w.show_page("Stats")
        assert not w.detail.editing and w._nav["Stats"].isChecked()
    finally:
        w._quitting = True
        w.close()


def test_rating_keys_do_nothing_off_the_photo_pages(tmp_path):
    w, ids = _window(tmp_path)
    try:
        w.open_page("Library")
        w.grid.selected = {ids[0]}
        w.show_page("Stats")
        w.rate(stars=3)
        assert w.conn.execute("SELECT stars FROM ratings WHERE file_id = ?", (ids[0],)).fetchone() in (None, (0,))
        w.show_page("Library")
        w.grid.selected = {ids[0]}
        w.rate(stars=3)
        assert w.conn.execute("SELECT stars FROM ratings WHERE file_id = ?", (ids[0],)).fetchone()[0] == 3
    finally:
        w._quitting = True
        w.close()


def test_esc_leaves_the_edit_page_and_create_starts_at_its_tools(tmp_path):
    w, ids = _window(tmp_path)
    try:
        w.open_page("Library")
        w.grid.selected = {ids[0]}
        w.open_page("Edit")
        _key(w.edit_page.view, Qt.Key.Key_Escape)
        assert w.pages.currentWidget() is w.grid
        w.open_page("Create")
        w.create_page.open_tool("contact")
        assert w.create_page.stack.currentIndex() != 0
        _key(w.create_page, Qt.Key.Key_Escape)
        assert w.create_page.stack.currentIndex() == 0
        w.create_page.open_tool("contact")
        w.open_page("Stats")
        w.open_page("Create")
        assert w.create_page.stack.currentIndex() == 0
    finally:
        w._quitting = True
        w.close()
