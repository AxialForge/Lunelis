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


def test_thumbnails_bring_their_fingerprint_with_them(tmp_path):
    """0.45: the near-duplicate fingerprint is taken while the thumbnail is
    made, so Comparing photos doesn't read new thumbnails back from disk."""
    from PIL import Image
    from lunelis.catalog.schema import open_catalog
    from lunelis.importers.scan import add_root, scan_root
    from lunelis.raw import thumbnails
    from lunelis.dupes import similar
    src = tmp_path / "S"
    src.mkdir()
    Image.effect_noise((200, 150), 40).convert("RGB").save(src / "a.jpg")
    conn = open_catalog(tmp_path / "c.db")
    scan_root(conn, add_root(conn, src))
    thumbnails.generate_pending(conn, tmp_path / "thumbs")
    ph = conn.execute("SELECT perceptual_hash FROM files").fetchone()[0]
    assert ph and len(ph) == 16
    assert similar.compute_missing(conn, tmp_path / "thumbs") == 0      # nothing left to read back
    conn.close()


def test_dragging_from_an_unselected_photo_draws_a_box(tmp_path):
    # 0.52: a full grid has no empty space to start a box in.
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from test_audit_navigation import _window
    w, ids = _window(tmp_path, 6)
    try:
        w.open_page("Library")
        w.resize(1200, 800)
        g = w.grid
        g.clear_selection()
        a, b = g._tile_rect(0).center(), g._tile_rect(2).center()
        vp = g.viewport()
        QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, a)
        for k in range(1, 11):                                   # a real drag, in steps
            QTest.mouseMove(vp, a + (b - a) * (k / 10))
        QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, b)
        assert {g.index.file_id(i) for i in range(3)} <= g.selected and len(g.selected) >= 3
    finally:
        w._quitting = True
        w.close()


def test_a_selection_box_held_at_the_bottom_scrolls_and_keeps_selecting(tmp_path):
    # 0.53: a box could only select what was on screen.
    from PySide6.QtCore import QPoint
    from test_audit_navigation import _window
    w, ids = _window(tmp_path, 60)
    try:
        w.open_page("Library")
        w.resize(900, 500)
        g = w.grid
        g.set_target_tile(160)
        g.clear_selection()
        bar = g.verticalScrollBar()
        assert bar.maximum() > 0
        g._band_start(QPoint(2, 2))
        bottom = QPoint(g.viewport().width() - 2, g.viewport().height() - 3)
        g._band_move(bottom)
        before = len(g.selected)
        for _ in range(200):                                   # the timer's ticks, run by hand
            g._band_scroll()
        assert bar.value() == bar.maximum()
        assert len(g.selected) > before and len(g.selected) == len(w.index)
        g._band.hide()
        g._band_timer.stop()
    finally:
        w._quitting = True
        w.close()


def test_collapse_into_a_burst_selects_and_shows_the_new_tile(tmp_path):
    from test_audit_navigation import _window
    from lunelis import stacks
    w, ids = _window(tmp_path, 6)
    try:
        w.open_page("Library")
        w.grid.selected = set(ids[1:4])
        w.burst_from_selection()
        st = stacks.stack_of(w.conn, ids[1])
        assert st is not None and set(stacks.members(w.conn, st)) == set(ids[1:4])
        assert w.conn.execute("SELECT kind FROM stacks WHERE id = ?", (st,)).fetchone()[0] == "chosen_burst"
        assert len(w.grid.selected) == 1 and w.index.stack_id(w.grid.current) == st
        stacks.rebuild(w.conn)                                  # the automatic pass leaves it alone
        assert stacks.stack_of(w.conn, ids[1]) == st
    finally:
        w._quitting = True
        w.close()


def test_the_filmstrip_matches_the_photo_and_scrolls_on_its_own(tmp_path):
    # 0.53: squeezed thumbnails, the wheel stepped the photo, a thin outline.
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication
    from test_audit_navigation import _window
    w, ids = _window(tmp_path, 40)
    try:
        w.open_page("Library")
        w.resize(1200, 800)
        w.open_detail(w.index.file_id(10))
        d, s = w.detail, w.detail.strip
        s.resize(900, 90)
        first, per = s._slots()
        assert first <= d.pos < first + per                       # the open photo is on the strip
        x0 = s._x0(per)
        for k in range(min(per, len(d.index) - first)):           # every tile opens the photo it stands for
            x = x0 + k * (s.THUMB + s.GAP) + s.THUMB // 2
            assert s.slot_at(x) == first + k
        target = first + 2
        x = x0 + 2 * (s.THUMB + s.GAP) + 5
        s.picked.emit(s.slot_at(x))
        assert d.pos == target and d.info.file_id == d.index.file_id(target) and s.pos == target
        before = (d.pos, s._slots()[0])
        ev = QWheelEvent(QPointF(450, 40), QPointF(450, 40), QPoint(), QPoint(0, -120), Qt.MouseButton.NoButton,
                         Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(s, ev)
        assert d.pos == before[0] and s._slots()[0] > before[1]   # the strip moved, the photo didn't
        d.go(d.pos + 1)
        assert s.offset == 0                                      # back to the current photo
    finally:
        w._quitting = True
        w.close()


def test_the_filmstrip_marks_videos_formats_stacks_and_edits():
    # 0.53: what kind of media each tile is, as the grid shows it.
    from types import SimpleNamespace
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from lunelis.ui.detail_view import Filmstrip
    from lunelis.ui.thumbcache import ThumbCache
    import tempfile, pathlib
    s = Filmstrip(ThumbCache(pathlib.Path(tempfile.mkdtemp())))
    s.resize(800, 90)
    drawn = []
    for tile in (SimpleNamespace(is_video=True, duration=75.0, stack_size=0, badge="MP4", edited=False),
                 SimpleNamespace(is_video=False, duration=None, stack_size=4, badge="ARW+JPG", edited=True),
                 SimpleNamespace(is_video=False, duration=None, stack_size=0, badge="JPG", edited=False)):
        img = QImage(80, 80, QImage.Format.Format_ARGB32)
        img.fill(0)
        p = QPainter(img)
        s._marks(p, QRect(0, 0, 66, 66), tile)
        p.end()
        drawn.append(sum(img.pixelColor(x, y).alpha() > 0 for x in range(80) for y in range(80)))
    assert drawn[0] > 0 and drawn[1] > drawn[0] * 0.5 and drawn[2] == 0      # a plain JPEG gets no marks
