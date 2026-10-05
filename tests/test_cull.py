"""v0.19: culling - full screen, keys, auto-advance, compare with synced zoom."""
import pytest
from PIL import Image
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from lunelis.catalog.schema import open_catalog
from lunelis.importers.scan import add_root, scan_root


def key(w, k):
    w.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, k, Qt.KeyboardModifier.NoModifier))


@pytest.fixture
def shoot(tmp_path):
    QApplication.instance() or QApplication([])
    conn = open_catalog(tmp_path / "c.db")
    root = tmp_path / "Shoot"
    root.mkdir()
    for i in range(6):
        Image.new("RGB", (300, 200), (40 * i, 60, 90)).save(root / f"S{i}.jpg")
    scan_root(conn, add_root(conn, root))
    ids = [r[0] for r in conn.execute("SELECT id FROM files ORDER BY filename")]
    rated = []

    def rate_ids(fids, **change):
        from lunelis.catalog.ratings import set_ratings
        set_ratings(conn, fids, **change)
        rated.append((list(fids), change))
    from lunelis.ui.cull_view import CullView
    view = CullView(conn, ids, rate_ids)
    view.resize(1200, 800)
    return view, ids, rated, conn


def test_keys_rate_and_auto_advance(shoot):
    view, ids, rated, conn = shoot
    key(view, Qt.Key.Key_P)
    assert rated[-1] == ([ids[0]], {"flag": "pick"}) and view.current() == ids[1]   # moved on
    key(view, Qt.Key.Key_X)
    key(view, Qt.Key.Key_4)
    assert rated[-2] == ([ids[1]], {"flag": "reject"}) and rated[-1] == ([ids[2]], {"stars": 4})
    key(view, Qt.Key.Key_A)                                   # auto-advance off
    key(view, Qt.Key.Key_7)
    assert view.current() == ids[3] and rated[-1] == ([ids[3]], {"label": "Yellow"})
    key(view, Qt.Key.Key_Left)
    assert view.current() == ids[2]
    assert "of 6" in view.info.text()


def test_compare_two_to_four_with_zoom_that_moves_together(shoot):
    view, ids, rated, conn = shoot
    key(view, Qt.Key.Key_C)
    assert len(view.canvases) == 2 and view.shown() == ids[:2]
    key(view, Qt.Key.Key_C)
    key(view, Qt.Key.Key_C)
    assert len(view.canvases) == 4 and view.shown() == ids[:4]
    a, b = view.canvases[0], view.canvases[1]
    from PySide6.QtGui import QPixmap
    for c in view.canvases:
        c.resize(400, 300)
        c.show_pixmap(QPixmap(300, 200), True)              # as the previews arrive
    a.zoom_to(a.fit_scale() * 2, QPointF(100, 100))
    view._sync(a)
    assert b.scale is not None and abs(b.scale / b.fit_scale() - a.scale / a.fit_scale()) < 1e-6
    assert (round(b.center.x(), 6), round(b.center.y(), 6)) == (round(a.center.x(), 6), round(a.center.y(), 6))
    key(view, Qt.Key.Key_Tab)
    assert view.current() == ids[1] and view.canvases[1].marked
    key(view, Qt.Key.Key_P)                                   # rates the active one, then the next of the group
    assert rated[-1] == ([ids[1]], {"flag": "pick"}) and view.current() == ids[2]
    key(view, Qt.Key.Key_Right)                               # the group moves along
    assert view.shown() == ids[4:6]
    key(view, Qt.Key.Key_C)
    assert len(view.canvases) == 1


def test_the_window_opens_culling_on_the_selection(tmp_path):
    QApplication.instance() or QApplication([])
    from lunelis.ui import main_window as mw
    w = mw.MainWindow()
    try:
        root = tmp_path / "C"
        root.mkdir()
        for i in range(3):
            Image.new("RGB", (60, 40)).save(root / f"C{i}.jpg")
        rid = add_root(w.conn, root)
        scan_root(w.conn, rid)
        w.reload()
        mine = [r[0] for r in w.conn.execute("SELECT id FROM files WHERE root_id = ? ORDER BY filename", (rid,))]
        w.grid.selected = set(mine[:2])
        w.cull()
        assert sorted(w.cull_view.ids) == sorted(mine[:2])
        key(w.cull_view, Qt.Key.Key_5)
        assert w.conn.execute("SELECT stars FROM ratings WHERE file_id = ?", (w.cull_view.ids[0],)).fetchone()[0] == 5
        w.cull_view.close()
    finally:
        w._quitting = True
        w.close()


def test_the_photo_view_keeps_its_photo_when_the_library_reloads(tmp_path):
    """The filmstrip and the picture must always be the same photo - a background
    reload used to rewrite the list the photo view shared, shifting the strip."""
    QApplication.instance() or QApplication([])
    from lunelis.ui import main_window as mw
    w = mw.MainWindow()
    try:
        root = tmp_path / "R"
        root.mkdir()
        for i in range(5):
            Image.new("RGB", (60, 40), (i * 40, 0, 0)).save(root / f"r{i}.jpg")
        rid = add_root(w.conn, root)
        scan_root(w.conn, rid)
        w.reload()
        mine = [w.index.file_id(i) for i in range(len(w.index)) if w.index.file_id(i) in
                {r[0] for r in w.conn.execute("SELECT id FROM files WHERE root_id = ?", (rid,))}]
        w.open_detail(mine[1])
        d = w.detail
        shown = d.info.file_id
        # The library's list changes under it (a re-sort / new photos), in place.
        w.index.apply(list(reversed(w.index.all_rows)))
        assert d.index.file_id(d.pos) == shown                       # its own copy didn't move
        d.follow(w.index)
        assert d.index.file_id(d.pos) == shown and d.strip.index.rows[d.strip.pos][0] == shown
    finally:
        w._quitting = True
        w.close()
