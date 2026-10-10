"""0.54: the interface issues from the 10 October 2026 backlog (large 3-8,
medium 1 / 4 / 5 / 7-14, small 1-6 / 8 / 10-15 / 17-20)."""
import threading
import time

import pytest
from PIL import Image
from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QKeyEvent, QResizeEvent
from PySide6.QtWidgets import QApplication, QListWidgetItem, QMessageBox, QWidget

from lunelis.catalog.schema import open_catalog
from lunelis.importers.scan import add_root, scan_root
from lunelis.settings import Settings

MAIN = threading.get_ident


def _app():
    return QApplication.instance() or QApplication([])


def _pump(done, seconds: float = 20.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QApplication.processEvents()
        if done():
            return True
        time.sleep(0.01)
    return False


def _photos(folder, n=3, prefix="n"):
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        Image.new("RGB", (60, 40), (i * 60, 20, 0)).save(folder / f"{prefix}{i}.jpg")
    return folder


def _window(tmp_path, n=3):
    """A MainWindow on the throwaway test catalog with n photos of its own, in grid order."""
    _app()
    from lunelis.ui import main_window as mw
    w = mw.MainWindow()
    Settings(w.conn).set("stack_bursts", True)
    rid = add_root(w.conn, _photos(tmp_path / "N", n))
    scan_root(w.conn, rid)
    w.set_filter(mw.Filter())
    mine = {r[0] for r in w.conn.execute("SELECT id FROM files WHERE root_id = ?", (rid,))}
    ids = [w.index.file_id(i) for i in range(len(w.index)) if w.index.file_id(i) in mine]
    assert len(ids) == n
    return w, ids, rid


def _shut(w) -> None:
    if hasattr(w, "bg"):
        w.bg.wait()                               # background loads must not outlive the catalog
    w._quitting = True
    w.close()


def _stars(conn, fid) -> int:
    row = conn.execute("SELECT stars FROM ratings WHERE file_id = ?", (fid,)).fetchone()
    return (row[0] or 0) if row else 0


def _key(w, k) -> None:
    QApplication.sendEvent(w, QKeyEvent(QEvent.Type.KeyPress, k, Qt.KeyboardModifier.NoModifier))


# --- large ------------------------------------------------------------------------------------

def test_thumbnails_after_a_reviewed_shoot_are_made_off_the_windows_thread(tmp_path, monkeypatch):
    # large 3
    from lunelis.raw import thumbnails
    w, ids, _ = _window(tmp_path)
    try:
        seen = []
        monkeypatch.setattr(thumbnails, "generate_pending",
                            lambda conn, cache, only=None, **k: seen.append((threading.get_ident(), conn, list(only))))
        w._start_thumbnails_if_needed()           # these photos have no thumbnail yet
        w.bg.wait()
        assert seen, "nothing was made"
        thread, conn, only = seen[0]
        assert thread != MAIN() and conn is not w.conn and 0 < len(only) <= 500
    finally:
        _shut(w)


def test_reset_edits_looks_up_the_edited_photos_once(tmp_path, monkeypatch):
    # large 4
    from lunelis.edit import store
    w, ids, _ = _window(tmp_path, 6)
    try:
        calls = []
        real = store.edited_ids
        monkeypatch.setattr(store, "edited_ids", lambda conn, fids: calls.append(1) or real(conn, fids))
        w.grid.selected = set(ids)
        w.reset_edits()                           # Photo > Edit > Reset edits
        assert len(calls) == 1 and "None of the selected" in w.status.text()
        w._reset_edits_of(ids)                    # the Edit page's Reset all
        assert len(calls) == 2
    finally:
        _shut(w)


def test_tags_faces_and_edits_start_the_sidecar_writer(tmp_path):
    # large 5
    w, ids, _ = _window(tmp_path)
    try:
        for change in (w.detail.tags_changed.emit, w.detail.faces_changed.emit, w._tags_changed,
                       lambda: w._photo_edited(ids[0]), w.edit_page.view.tags_changed.emit,
                       w.people_page.changed.emit):
            w._xmp_timer.stop()
            change()
            assert w._xmp_timer.isActive(), change
        w._xmp_timer.stop()
    finally:
        _shut(w)


def test_a_photo_outside_the_librarys_list_still_opens(tmp_path):
    # large 6
    from lunelis import stacks
    from lunelis.ui import main_window as mw
    w, ids, _ = _window(tmp_path, 5)
    try:
        w.set_filter(mw.Filter(ids=(ids[0],)))    # a filter is on: the library shows one photo
        assert w.index.position(ids[4]) == -1
        w.open_detail(ids[4])                     # ...as People / On this day do
        assert w.pages.currentWidget() is w.detail and w.detail.info.file_id == ids[4]
        w.reload()                                # a reload behind it (a scan's refresh) changes nothing
        assert w.detail.info.file_id == ids[4] and w._targets() == [ids[4]]
        w.rate(stars=4)                           # the keys act on the photo on screen
        assert _stars(w.conn, ids[4]) == 4 and _stars(w.conn, ids[0]) == 0
        w.close_detail()
        # A frame behind its stack's cover, from the stack tray.
        w.set_filter(mw.Filter())
        st = stacks.make_burst(w.conn, ids[:3])
        w.reload()
        hidden = next(f for f in ids[:3] if w.index.position(f) == -1)
        w.stack_tray.open_photo.emit(hidden)
        assert w.pages.currentWidget() is w.detail and w.detail.info.file_id == hidden
        assert st in w.index.expanded and w.index.position(hidden) >= 0
        w.close_detail()
        # ...and the same frame while a filter leaves its stack out of the library.
        w.set_filter(mw.Filter(ids=(ids[4],)))
        w.open_detail(hidden)
        assert w.detail.info.file_id == hidden and len(w.detail.index) == 3      # its stack's frames
        w.close_detail()
    finally:
        _shut(w)


def test_removing_a_source_runs_off_the_windows_thread(tmp_path, monkeypatch):
    # large 7
    from lunelis.importers import scan
    w, ids, rid = _window(tmp_path)
    try:
        seen = []
        real = scan.remove_root
        monkeypatch.setattr(scan, "remove_root",
                            lambda conn, r: seen.append(threading.get_ident()) or real(conn, r))
        monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
        monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: next(
            b for b in self.buttons() if self.buttonRole(b) == QMessageBox.ButtonRole.DestructiveRole))
        w.remove_source(rid, str(tmp_path / "N"))
        assert w._remove_note is not None         # a note is up while it runs
        w.bg.wait()
        assert seen and seen[0] != MAIN()
        assert w._remove_note is None and "Removed" in w.status.text()
        assert w.conn.execute("SELECT COUNT(*) FROM roots WHERE id = ?", (rid,)).fetchone()[0] == 0
        assert all(w.index.position(f) == -1 for f in ids)
    finally:
        _shut(w)


def test_reading_a_folder_for_import_can_be_stopped(tmp_path, monkeypatch):
    # large 8 (and small 17: reading isn't "an import")
    _app()
    from lunelis.importers import metadata
    from lunelis.ui.import_view import ImportView, PreviewWorker
    a, b = _photos(tmp_path / "A", 3, "a"), _photos(tmp_path / "B", 2, "b")
    got = []
    worker = PreviewWorker(str(a))
    worker.done.connect(got.append)
    worker.stop = True
    worker.run()
    assert got == [None]                          # stopped: no list, no error

    gate = threading.Event()
    monkeypatch.setattr(metadata, "read_file", lambda path: gate.wait(10) and {})
    conn = open_catalog(tmp_path / "c.db")
    page = ImportView(conn)
    try:
        page.choose(str(a))
        assert page.busy() and not page.importing() and not page.stop_b.isHidden()
        page.choose(str(b))                       # another source: the first read is stopped
        assert page._worker.stop
        gate.set()
        assert _pump(lambda: page._thread is None and page.source == str(b) and len(page.preview) == 2)
        assert page.stop_b.isHidden() and page.go.isEnabled()
        page.choose(str(a))
        page._stop()                              # the Stop button
        assert _pump(lambda: page._thread is None)
        assert page.source is None and page.preview == [] and "Stopped reading" in page.message.text()
    finally:
        gate.set()
        _pump(lambda: page._thread is None)
        page.bg.wait()
        conn.close()


# --- medium -----------------------------------------------------------------------------------

def test_select_all_leaves_the_hidden_grid_alone_while_a_photo_is_open(tmp_path):
    # medium 1
    w, ids, _ = _window(tmp_path)
    try:
        select_all = next(a for a in w.actions() if a.shortcut().toString() == "Ctrl+A")
        w.open_detail(ids[0])
        select_all.trigger()
        assert w.grid.selected == {ids[0]}
        w.close_detail()
        select_all.trigger()
        assert w.grid.selected >= set(ids)
    finally:
        _shut(w)


@pytest.fixture
def copies(tmp_path):
    """Two sources holding the same three files, found and verified."""
    from lunelis.dupes import detect
    conn = open_catalog(tmp_path / "cat.db")
    roots = []
    for name in ("Main", "Old"):
        d = tmp_path / name
        d.mkdir()
        for seed in (1, 2, 3):
            (d / f"p{seed}.jpg").write_bytes(bytes((seed * 31 + i * 7) % 251 for i in range(4096)) * (60 + seed))
        roots.append(add_root(conn, d))
        scan_root(conn, roots[-1])
    detect.process_folder(conn, roots[0], "")
    for (gid,) in conn.execute("SELECT id FROM duplicate_groups").fetchall():
        detect.verify_group(conn, gid)
    conn.commit()
    yield conn
    conn.close()


def test_setting_one_group_aside_runs_on_a_worker_and_backs_up_to_the_chosen_folder(copies, tmp_path, monkeypatch):
    # medium 4 and 9
    _app()
    from lunelis.ui import dupes_view
    conn = copies
    Settings(conn).set("catalog_backup_dir", str(tmp_path / "my backups"))
    page = dupes_view.DuplicatesView(conn)
    page.refresh()
    page.bg.wait()
    verified = [g for g in page._all if g.verified]
    assert len(verified) == 3
    seen = []
    real = dupes_view.quarantine
    monkeypatch.setattr(dupes_view, "quarantine",
                        lambda c, *a, **k: seen.append(threading.get_ident()) or real(c, *a, **k))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    page._quarantine_one(verified[0])
    page.bg.wait()
    assert seen and seen[0] != MAIN()
    assert conn.execute("SELECT COUNT(*) FROM files WHERE quarantined_at IS NOT NULL").fetchone()[0] == 1
    assert list((tmp_path / "my backups").glob("*.zip")), "the safety snapshot isn't in the chosen backup folder"
    page.bg.wait()


def test_no_page_sends_its_safety_snapshot_to_the_default_folder():
    # medium 9: set-aside (exact and near) and Empty quarantine ask Settings where backups go
    from pathlib import Path
    ui = Path(__file__).resolve().parents[1] / "src" / "lunelis" / "ui"
    for name in ("dupes_view.py", "near_view.py", "quarantine_view.py"):
        src = (ui / name).read_text(encoding="utf-8")
        assert "paths.BACKUP_DIR" not in src and "backup.backup_dir(Settings(conn), paths.DATA_DIR)" in src, name


def test_verifying_groups_in_a_row_verifies_every_one(copies, monkeypatch):
    # medium 13
    _app()
    from lunelis.dupes import detect
    from lunelis.ui import dupes_view
    page = dupes_view.DuplicatesView(copies)
    page.refresh()
    page.bg.wait()
    done = []
    monkeypatch.setattr(detect, "verify_group", lambda conn, gid: done.append(gid) or [])
    for g in page._all:
        page._verify_one(g)                       # A, B and C, without waiting in between
    page.bg.wait()
    assert done == [g.id for g in page._all] and len(done) == 3 and page._verify_queue == []


def test_the_albums_page_counts_smart_albums_on_a_worker(tmp_path, monkeypatch):
    # medium 5
    _app()
    from lunelis.albums import smart
    from lunelis.ui.albums_view import AlbumsView
    conn = open_catalog(tmp_path / "c.db")
    seen = []
    real = smart.smart_albums
    monkeypatch.setattr(smart, "smart_albums", lambda c: seen.append(threading.get_ident()) or real(c))
    page = AlbumsView(conn)
    try:
        page.refresh()
        assert _pump(lambda: page._thread is None and page._smart is not None)
        assert seen and all(t != MAIN() for t in seen)
        assert "Saved rules" in page.smart_help.text()
    finally:
        _pump(lambda: page._thread is None)
        conn.close()


def test_my_look_progress_reaches_the_label_on_the_windows_thread(tmp_path):
    # medium 7
    w, ids, _ = _window(tmp_path)
    try:
        edit = w.detail.edit
        ran = []
        real = edit.panel.status.setText
        edit.panel.status.setText = lambda text: (ran.append(threading.get_ident()), real(text))
        t = threading.Thread(target=lambda: edit._look_signals.progress.emit(3, 10))
        t.start()
        t.join()
        assert ran == []                          # queued, not run on the worker's thread
        QApplication.processEvents()
        assert ran == [MAIN()] and edit.panel.status.text() == "Learning your look… 3 / 10 edits"
        del edit.panel.status.setText
    finally:
        _shut(w)


def test_thumbnail_caches_are_bound_by_size_not_count(tmp_path, monkeypatch):
    # medium 8
    _app()
    from PySide6.QtGui import QImage
    from lunelis.ui import thumbcache
    monkeypatch.setattr(thumbcache, "MIN_KEPT", 2)
    cache = thumbcache.ThumbCache(tmp_path, budget_mb=1)
    cache.set_tile_size(256)                      # 256 x 256 x 4 = 256 KB each: four fit in 1 MB
    for fid in range(12):
        img = QImage(256, 256, QImage.Format.Format_RGB32)
        img.fill(0)
        cache._on_loaded(fid, cache.gen, img)
    assert len(cache._pix) == 4 and list(cache._pix) == [8, 9, 10, 11]
    assert cache.cached_bytes() <= cache.budget
    cache.set_tile_size(128)                      # the old size's stand-ins give way as the new size fills
    for fid in range(40):
        img = QImage(128, 128, QImage.Format.Format_RGB32)
        img.fill(0)
        cache._on_loaded(100 + fid, cache.gen, img)
    assert cache.cached_bytes() <= cache.budget and not cache._stale
    assert thumbcache.GRID_CACHE_MB == 400 and thumbcache.CACHE_MB == 200


def test_updating_the_library_clears_after_stop(tmp_path):
    # medium 10
    from PySide6.QtCore import QThread
    from lunelis.ui import main_window as mw
    w, ids, _ = _window(tmp_path)
    try:
        w._thread, w._worker = QThread(w), mw.LibraryWorker([])
        w._worker._cancel = True                  # Stop was pressed: the damage check never said "Up to date"
        w._scan_state_said = False
        w._scan_step(2, 0, 0)
        assert w.library_state.text() == "Updating the library…"
        w._on_finished()
        assert "Updating the library" not in w.library_state.text()
        assert "Last scanned" in w.library_state.text()
    finally:
        _shut(w)


def test_people_page_messages_outlast_the_counts_refresh(tmp_path):
    # medium 11
    _app()
    from lunelis.ui.people_view import PeopleView
    conn = open_catalog(tmp_path / "c.db")
    page = PeopleView(conn)
    try:
        page.refresh()
        page.bg.wait()
        page._done("Named Ann.")
        page.bg.wait()                            # the counts have arrived
        assert page.summary.text().startswith("Named Ann.") and len(page.summary.text()) > len("Named Ann.")
    finally:
        page.bg.wait()
        conn.close()


def test_culling_drops_previews_queued_for_photos_skipped_past(tmp_path):
    # medium 12
    from lunelis.ui.cull_view import CullView
    w, ids, _ = _window(tmp_path, 5)
    try:
        cv = CullView(w.conn, ids, w.rate_ids)
        kept = []
        real = cv.previews.keep_only
        cv.previews.keep_only = lambda wanted: (kept.append(list(wanted)), real(wanted))
        for _ in range(3):
            cv.step(1)
        assert kept[-1] == [ids[3]]
        cv.group = 2
        cv._layout()
        cv._show()
        assert kept[-1] == ids[3:5]
        del cv.previews.keep_only
        cv.previews.pool.waitForDone(20_000)
        QApplication.processEvents()
        cv.close()
    finally:
        _shut(w)


def test_the_edit_page_with_nothing_to_edit_has_no_photo_under_the_keys(tmp_path):
    # medium 14
    w, ids, _ = _window(tmp_path)
    try:
        w.grid.selected = {ids[0]}
        w.open_page("Edit")
        assert w.edit_page.current() == ids[0] and w._targets() == [ids[0]]
        w.edit_page.set_library_context([], w.filter, w.sort.currentData())
        w.edit_page.load()                        # "Selected in the library", and nothing is
        assert w.edit_page.current() is None and not w.edit_page.empty.isHidden()
        assert w._targets() == [] and w._edit_targets() == []
        w.rate(stars=5)
        assert _stars(w.conn, ids[0]) == 0
        w.open_page("Library")
    finally:
        _shut(w)


# --- small ------------------------------------------------------------------------------------

def test_the_add_a_folder_button_is_centred_and_follows_a_resize(tmp_path):
    # small 1
    w, ids, _ = _window(tmp_path)
    try:
        b = w._empty_add_b()
        vp = w.grid.viewport()
        for size in (QSize(900, 500), QSize(1500, 700)):
            vp.resize(size)
            QApplication.sendEvent(vp, QResizeEvent(size, QSize(10, 10)))
            assert abs((b.x() + b.width() / 2) - vp.width() / 2) <= 1, size
            assert b.width() == b.sizeHint().width()
    finally:
        _shut(w)


def test_s_in_the_photo_view_keeps_the_keys_on_the_frame_on_screen(tmp_path):
    # small 2
    from lunelis import stacks
    w, ids, _ = _window(tmp_path, 4)
    try:
        stacks.make_burst(w.conn, ids[:3])
        w.reload()
        cover = next(f for f in ids[:3] if w.index.position(f) >= 0)
        frame = next(f for f in ids[:3] if f != cover)
        w.open_detail(frame)
        w.toggle_stack()                          # S: closes the stack in the library
        assert w.index.position(frame) == -1 and w.grid.selected == {frame}
        w.rate(stars=2)
        assert _stars(w.conn, frame) == 2 and _stars(w.conn, cover) == 0
        w.close_detail()
    finally:
        _shut(w)


def test_set_location_keeps_the_selection(tmp_path):
    # small 3
    w, ids, _ = _window(tmp_path)
    try:
        w.open_detail(ids[0])                     # a photo was looked at earlier
        w.close_detail()
        w.grid.selected = set(ids)
        w.set_location()
        assert w.map_page.canvas.placing and w.map_page._placing == ids or set(w.map_page._placing) == set(ids)
        assert w.grid.selected == set(ids)
        w.map_page.stop_placing()
    finally:
        _shut(w)


def test_collapse_into_a_burst_or_a_timelapse_can_be_undone(tmp_path):
    # small 4
    from lunelis import stacks
    w, ids, _ = _window(tmp_path, 6)
    try:
        w.rate_ids([ids[5]], stars=1)             # the action before
        w.grid.selected = set(ids[:3])
        w.burst_from_selection()
        st = stacks.stack_of(w.conn, ids[0])
        assert st is not None and w._history().next_undo() == "collapse into a burst"
        w.undo()
        assert all(stacks.stack_of(w.conn, f) is None for f in ids[:3]) and _stars(w.conn, ids[5]) == 1
        assert all(w.index.position(f) >= 0 for f in ids[:3])
        w.redo()
        assert stacks.members(w.conn, st) == sorted(ids[:3], key=stacks.members(w.conn, st).index)
        assert sorted(stacks.members(w.conn, st)) == sorted(ids[:3])
        # A timelapse of other photos: the tile comes apart again too.
        w.grid.selected = set(ids[3:5])
        w.timelapse_from_selection()
        assert stacks.stack_of(w.conn, ids[3]) is not None
        assert w._history().next_undo() == "collapse into a timelapse"
        w.undo()
        assert stacks.stack_of(w.conn, ids[3]) is None and stacks.stack_of(w.conn, ids[0]) == st
        # Photos already in a stack: collapsing takes that stack apart, which one
        # undo step can't put back - so it isn't offered (and nothing else is undone in its place).
        assert w._stack_undo_state(ids[:2]) is None
    finally:
        _shut(w)


def test_a_scan_result_stays_readable_beside_the_progress(tmp_path):
    # small 5
    w, ids, _ = _window(tmp_path)
    try:
        w._scan_result("Scanned in 1.0s: 3 new, 0 changed, 0 missing")
        w._scan_progress("Reading metadata… 1 / 3")
        text = w.status.text()
        assert text.startswith("Scanned in 1.0s: 3 new") and text.endswith("Reading metadata… 1 / 3")
        w._on_relinked(2)
        w._scan_progress("Making thumbnails… 1 / 3")
        assert w.status.text().startswith("Recognised 2 moved files")
        w._scan_notice.until = 0                  # a few seconds later
        w._scan_progress("Making thumbnails… 2 / 3")
        assert w.status.text() == "Making thumbnails… 2 / 3"
    finally:
        _shut(w)


def test_a_photo_gone_from_the_catalog_takes_the_keys_with_it(tmp_path):
    # small 6
    w, ids, _ = _window(tmp_path)
    try:
        w.open_detail(ids[0])
        assert w.detail.name.text()
        w.conn.execute("DELETE FROM files WHERE id = ?", (ids[1],))
        w.conn.commit()
        w.detail.go(w.detail.pos + 1)             # on to the photo that left
        assert w.detail.info is None and w.detail.name.text() == ""
        assert w.grid.selected == set() and w._targets() == []
        w.rate(stars=5)
        assert _stars(w.conn, ids[0]) == 0
        w.close_detail()
    finally:
        _shut(w)


def test_a_face_list_keeps_its_place_and_selection_after_an_answer():
    # small 8
    _app()
    from lunelis.ui.people_view import FaceList

    def items(ids):
        out = []
        for i in ids:
            it = QListWidgetItem(str(i))
            it.setData(Qt.ItemDataRole.UserRole, i)
            it.setSizeHint(QSize(130, 146))
            out.append(it)
        return out
    lst = FaceList()
    lst.resize(420, 300)
    lst.show()
    try:
        lst.fill(items(range(60)))
        QApplication.processEvents()
        bar = lst.verticalScrollBar()
        assert bar.maximum() > 0
        bar.setValue(bar.maximum() // 2)
        place = bar.value()
        for i in (30, 31, 40):
            lst.item(i).setSelected(True)
        lst.fill(items(i for i in range(60) if i != 30))       # face 30 was answered
        assert bar.value() == place and sorted(lst.chosen()) == [31, 40]
        lst.fill(items(range(500, 560)))                        # someone else's faces: from the top
        assert bar.value() == 0 and lst.chosen() == []
    finally:
        lst._timer.stop()
        lst.hide()
        lst.deleteLater()


def test_the_back_button_says_where_back_goes(tmp_path):
    # small 10
    w, ids, _ = _window(tmp_path)
    try:
        w.open_page("People")
        w.open_detail(ids[0])
        assert w.detail.back_b.text().endswith("Back to People")
        w.close_detail()
        w.open_page("Library")
        w.open_detail(ids[0])
        assert w.detail.back_b.text().endswith("Back to Library")
        w.close_detail()
    finally:
        _shut(w)


def test_the_tray_icon_brings_the_window_back_on_the_page_it_was_on(tmp_path):
    # small 11
    from PySide6.QtWidgets import QSystemTrayIcon
    w, ids, _ = _window(tmp_path)
    try:
        w.open_page("Stats")
        w.hide()
        w._tray_activated(QSystemTrayIcon.ActivationReason.Trigger)
        assert w.isVisible() and w.pages.currentWidget() is w.stats_page and w._nav["Stats"].isChecked()
        w._tray_activated(QSystemTrayIcon.ActivationReason.Context)      # a right-click only opens the menu
        assert w.pages.currentWidget() is w.stats_page
        w.hide()
    finally:
        _shut(w)


def test_edit_page_and_stack_tray_icons_follow_a_live_theme_switch(tmp_path):
    # small 12 and 13
    w, ids, _ = _window(tmp_path)
    try:
        def looks():
            return (w.edit_page.view.rotate_l.icon().pixmap(26, 26).toImage(),
                    w.stack_tray.close_b.icon().pixmap(16, 16).toImage())
        w.apply_theme("graphite")
        light = looks()
        w.apply_theme("midnight")
        dark = looks()
        assert light[0] != dark[0] and light[1] != dark[1]
    finally:
        w.apply_theme()
        _shut(w)


def test_album_tiles_drop_the_old_column_stretch_when_the_page_widens():
    # small 14
    _app()
    from lunelis.ui.albums_view import TILE, TileFlow
    flow = TileFlow()
    flow.set_widgets([QWidget() for _ in range(6)])
    flow.resize(2 * (TILE + 18), 600)
    flow._reflow()
    assert flow._cols == 2 and flow.grid.columnStretch(2) == 1
    flow.resize(5 * (TILE + 18), 600)
    flow._reflow()
    assert flow._cols == 5
    assert [flow.grid.columnStretch(c) for c in range(6)] == [0, 0, 0, 0, 0, 1]
    flow.deleteLater()


def test_the_new_album_tile_works_from_the_keyboard():
    # small 15
    _app()
    from lunelis.ui.albums_view import NewAlbumTile
    tile = NewAlbumTile()
    assert tile.focusPolicy() == Qt.FocusPolicy.StrongFocus
    clicks = []
    tile.clicked.connect(lambda: clicks.append(1))
    for k in (Qt.Key.Key_Return, Qt.Key.Key_Space, Qt.Key.Key_A):
        _key(tile, k)
    assert len(clicks) == 2
    tile.deleteLater()


def test_the_quit_question_doesnt_call_reading_a_card_an_import(tmp_path):
    # small 17
    from lunelis.ui.import_view import ImportWorker, PreviewWorker
    w, ids, _ = _window(tmp_path)
    try:
        w.importer._thread, w.importer._worker = object(), PreviewWorker("X:\\")
        assert w.importer.busy() and w._busy_work() == []
        w.importer._worker = ImportWorker(None, "X:\\")
        assert w._busy_work() == ["an import"]
    finally:
        w.importer._thread = w.importer._worker = None
        _shut(w)


def test_takeout_filter_keeps_opened_years_and_albums_open(tmp_path):
    # small 18
    _app()
    from lunelis.ui.takeout_view import TakeoutView
    c = open_catalog(tmp_path / "c.db")
    c.execute("INSERT INTO roots (id, path) VALUES (2, ?)", (str(tmp_path / "Takeout"),))
    files = [(3, "Takeout/Google Photos/Trip to Rome/colosseum.jpg", "2018-05-02T09:00:00"),
             (4, "Takeout/Google Photos/Trip to Rome/colosseum-edited.jpg", "2018-05-02T09:00:00"),
             (5, "Takeout/Google Photos/Photos from 2017/IMG_0001-edited.jpg", "2017-03-01T09:00:00")]
    for fid, rel, taken in files:
        c.execute("INSERT INTO files (id, root_id, rel_path, filename, ext, size_bytes, mtime)"
                  " VALUES (?, 2, ?, ?, 'jpg', 1, 0)", (fid, rel, rel.rsplit("/", 1)[-1]))
        c.execute("INSERT INTO exif (file_id, captured_at) VALUES (?, ?)", (fid, taken))
    c.commit()
    page = TakeoutView(c)
    try:
        page.refresh()
        years = {page.tree.topLevelItem(k).text(0): page.tree.topLevelItem(k)
                 for k in range(page.tree.topLevelItemCount())}
        assert set(years) == {"2018", "2017"}
        years["2018"].setExpanded(True)
        years["2018"].child(0).setExpanded(True)
        page.filter.setCurrentIndex(page.filter.findData("edited"))      # Google's edits
        years = {page.tree.topLevelItem(k).text(0): page.tree.topLevelItem(k)
                 for k in range(page.tree.topLevelItemCount())}
        assert years["2018"].isExpanded() and years["2018"].child(0).isExpanded()
        assert years["2018"].child(0).childCount() == 1 and not years["2017"].isExpanded()
    finally:
        page.deleteLater()
        c.close()


def test_copy_edit_takes_the_focus_photo_only_when_it_is_selected(tmp_path):
    # small 19
    from lunelis.edit import store
    from lunelis.edit.stack import Stack
    w, ids, _ = _window(tmp_path)
    try:
        a, b, c = ids
        store.save(w.conn, a, Stack(adjust={"exposure": -0.5}))
        store.save(w.conn, b, Stack(adjust={"exposure": 0.3}))
        w.grid.selected, w.grid.current = {b, c}, w.index.position(a)      # the focus photo isn't selected
        w.copy_edit()
        assert w._edit_clipboard == Stack(adjust={"exposure": 0.3})         # the first selected, not the focus
        del w._edit_clipboard
        w.grid.selected, w.grid.current = {a, b}, w.index.position(b)      # the focus photo is selected
        w.copy_edit()
        assert w._edit_clipboard == Stack(adjust={"exposure": 0.3})
    finally:
        _shut(w)


def test_no_to_quit_anyway_leaves_the_window_as_it_was(tmp_path, monkeypatch):
    # small 20
    from PySide6.QtCore import QProcess
    w, ids, _ = _window(tmp_path)
    try:
        Settings(w.conn).set("confirm_quit", True)
        asked, quits, started = [], [], []
        monkeypatch.setattr(QMessageBox, "question",
                            lambda *a, **k: asked.append(1) or QMessageBox.StandardButton.No)
        monkeypatch.setattr(QApplication, "quit", staticmethod(lambda: quits.append(1)))
        monkeypatch.setattr(QProcess, "startDetached", staticmethod(lambda *a: started.append(a) or True))
        w._busy_work = lambda: ["an export"]
        w.open_page("Stats")
        w.quit_app()                              # Quit from the tray
        assert asked == [1] and quits == [] and not w._quitting and not getattr(w, "_closed", False)
        assert w.pages.currentWidget() is w.stats_page
        w.restart()                               # a restart from Settings
        assert len(asked) == 2 and started == [] and quits == []
        w.conn.execute("SELECT 1").fetchone()      # the catalog is still open
    finally:
        del w._busy_work
        _shut(w)
