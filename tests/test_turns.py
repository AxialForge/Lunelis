"""0.38: Rotate left / right turns how a photo or video is shown, without editing it."""
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from lunelis import turns
from lunelis.catalog.schema import open_catalog


def _catalog(tmp_path):
    conn = open_catalog(tmp_path / "c.db")
    conn.execute("INSERT INTO roots (id, path) VALUES (1, ?)", (str(tmp_path),))
    conn.execute("INSERT INTO files (id, root_id, rel_path, filename, ext, size_bytes, mtime) VALUES (1, 1, 'a.jpg', 'a.jpg', 'jpg', 1, 0)")
    conn.commit()
    return conn


def test_turns_add_up_and_go_back_to_upright(tmp_path):
    conn = _catalog(tmp_path)
    turns.load(conn)
    turns.turn(conn, [1], 1)
    assert turns.get(1) == 1
    turns.turn(conn, [1], -1)
    assert turns.get(1) == 0 and conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 0
    turns.turn(conn, [1], -1)
    turns.load(conn)                        # survives a restart
    assert turns.get(1) == 3
    img = QImage(40, 20, QImage.Format.Format_RGB888)
    out = turns.apply(img, 1)
    assert (out.width(), out.height()) == (20, 40)
    assert turns.apply(img, 2) is img       # upright: untouched
    turns._map.clear()
    conn.close()


def test_the_photo_view_shows_the_turn_and_hides_face_boxes(tmp_path):
    QApplication.instance() or QApplication([])
    from PySide6.QtGui import QPixmap
    from lunelis.ui.detail_view import PhotoCanvas as Canvas
    conn = _catalog(tmp_path)
    turns.load(conn)
    turns.turn(conn, [1], 1)
    c = Canvas()
    c.turn_id = 1
    c.show_pixmap(QPixmap(60, 30), sharp=True)
    assert (c.pix.width(), c.pix.height()) == (30, 60)
    assert c._turned()
    turns._map.clear()
    conn.close()


def test_rotate_from_the_library_and_the_photo_view(tmp_path):
    from test_audit_navigation import _window
    w, ids = _window(tmp_path, 2)
    try:
        w.grid.selected = {ids[0]}
        w.rotate(1)
        assert turns.get(ids[0]) == 1 and turns.get(ids[1]) == 0
        w.open_detail(ids[1])
        w.rotate(-1)
        assert turns.get(ids[1]) == 3
        assert w.detail.canvas.turn_id == ids[1]
    finally:
        turns._map.clear()
        w._quitting = True
        w.close()
