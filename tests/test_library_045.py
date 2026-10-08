"""0.45: Library browsing - the wide scrollbar, the stack tray, selecting many."""
from PySide6.QtCore import QEvent, QPointF
from PySide6.QtGui import QEnterEvent
from PySide6.QtWidgets import QApplication


def test_the_scrollbar_widens_while_in_use(tmp_path):
    QApplication.instance() or QApplication([])
    from lunelis.ui.grid import PhotoGrid, WideOnUseBar
    from lunelis.ui.thumbcache import ThumbCache
    g = PhotoGrid(ThumbCache(tmp_path))
    bar = g.verticalScrollBar()
    assert isinstance(bar, WideOnUseBar) and bar.objectName() == "GridScroll"
    assert not bar.property("active")
    bar.enterEvent(QEnterEvent(QPointF(1, 1), QPointF(1, 1), QPointF(1, 1)))
    assert bar.property("active")
    bar.leaveEvent(QEvent(QEvent.Type.Leave))
    assert not bar.property("active")
    g.deleteLater()


def test_selecting_a_stack_shows_its_frames_in_the_tray(tmp_path):
    from test_audit_navigation import _window
    from lunelis import stacks
    w, ids = _window(tmp_path, 4)
    try:
        sid = w.conn.execute("INSERT INTO stacks (kind, cover_file_id, size) VALUES ('burst', ?, 3)", (ids[0],)).lastrowid
        w.conn.executemany("INSERT INTO stack_files (stack_id, file_id, position) VALUES (?, ?, ?)",
                           [(sid, f, n) for n, f in enumerate(ids[:3])])
        w.conn.commit()
        w.reload()
        w.open_page("Library")
        pos = w.index.position(ids[0])
        assert w.index.tile(pos).stack_size == 3
        w.grid.selected, w.grid.current = {ids[0]}, pos
        w.grid.selection_changed.emit(1)
        assert not w.stack_tray.isHidden() and w.stack_tray.strip.count() == 3
        assert "Burst of 3 frames" in w.stack_tray.title.text()
        opened = []
        w.stack_tray.open_photo.disconnect()
        w.stack_tray.open_photo.connect(opened.append)
        w.stack_tray.strip.itemDoubleClicked.emit(w.stack_tray.strip.item(1))
        assert opened == [ids[1]]
        w.grid.selected, w.grid.current = {ids[3]}, w.index.position(ids[3])
        w.grid.selection_changed.emit(1)
        assert w.stack_tray.isHidden()                       # not a stack: the tray goes
        assert stacks.members(w.conn, sid) == ids[:3]
    finally:
        w._quitting = True
        w.close()


def test_select_many_by_box_and_by_menu(tmp_path):
    from PySide6.QtCore import QPoint
    from test_audit_navigation import _window
    w, ids = _window(tmp_path, 6)
    try:
        w.open_page("Library")
        w.resize(1200, 800)
        g = w.grid
        g._band_start(QPoint(0, 0))                                   # a box over the whole view
        g._band_move(QPoint(g.viewport().width() - 1, g.viewport().height() - 1))
        assert set(ids) <= g.selected
        g.select_where(lambda r: r[0] == ids[0])
        assert g.invert_selection() == len(w.index) - 1 and ids[0] not in g.selected
        g.select_where(lambda r: r[0] == ids[0])
        w._select_like("folder")                                       # all six are in one folder
        assert set(ids) <= g.selected
        names = [a.text() for a in w.photo_menu.actions() if a.menu()]
        assert "Se&lect" in names
    finally:
        w._quitting = True
        w.close()
