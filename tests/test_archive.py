"""v0.14.0: the Archive - photos out of the library but kept."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PIL import Image  # noqa: E402

from lunelis.albums import archive  # noqa: E402
from lunelis.albums import model as albums  # noqa: E402
from lunelis.catalog.schema import open_catalog  # noqa: E402
from lunelis.importers.scan import add_root, scan_root  # noqa: E402
from lunelis.ui.library import Filter, LibraryIndex  # noqa: E402


@pytest.fixture
def lib(tmp_path):
    root = tmp_path / "Photos"
    root.mkdir()
    for i in range(6):
        Image.effect_noise((64, 48), 40 + i).convert("RGB").save(root / f"IMG_{i:04d}.jpg", "JPEG")
    conn = open_catalog(tmp_path / "cat.db")
    scan_root(conn, add_root(conn, root))
    ids = [r[0] for r in conn.execute("SELECT id FROM files ORDER BY filename")]
    conn.execute("INSERT INTO ratings (file_id, stars) VALUES (?, 5), (?, 5)", (ids[0], ids[1]))
    conn.commit()
    yield conn, ids
    conn.close()


def shown(conn, flt=Filter()):
    idx = LibraryIndex()
    idx.load(conn, filt=flt)
    return {idx.file_id(i) for i in range(len(idx))}


def test_archived_photos_leave_the_library_but_stay_kept(lib):
    conn, ids = lib
    assert archive.archive(conn, ids[:2]) == 2
    assert archive.archive(conn, ids[:2]) == 0                 # already archived
    assert shown(conn) == set(ids[2:])                          # not in the grid
    assert shown(conn, Filter(min_stars=5)) == set()            # nor in filters
    assert shown(conn, Filter(auto="archive")) == set(ids[:2])  # but in the Archive album
    assert archive.archived_count(conn) == 2
    assert archive.split(conn, ids[:3]) == (ids[:2], ids[2:3])

    # An album you made still shows them.
    aid = albums.create(conn, "Keepers", ids[:3])
    assert shown(conn, Filter(album_id=aid)) == set(ids[:3])

    # The automatic albums: an Archive album; Favorites without the archived 5-stars.
    auto = {a.key: a.count for a in albums.auto_albums(conn)}
    assert auto.get("archive") == 2 and "favorites" not in auto

    assert archive.unarchive(conn, ids[:1]) == 1
    assert shown(conn) == set(ids[1:]) - {ids[1]} | {ids[0]}
    # Files on disk are never touched.
    assert conn.execute("SELECT COUNT(*) FROM files WHERE missing_since IS NULL").fetchone()[0] == 6


def test_photo_menu_archives_and_brings_back(lib, monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from lunelis.ui.main_window import MainWindow
    conn, ids = lib
    w = MainWindow()
    try:
        w.conn = conn
        w.reload()
        w.grid.selected = {ids[3]}
        w._update_archive_action()
        assert w.archive_action.text() == "Arc&hive"
        w.toggle_archive()
        assert conn.execute("SELECT archived_at IS NOT NULL FROM files WHERE id = ?", (ids[3],)).fetchone()[0]
        assert 'href="page:Albums"' in w.status.text()
        w.grid.selected = {ids[3]}
        w._update_archive_action()
        assert "Bring back" in w.archive_action.text()
        w.toggle_archive()
        assert conn.execute("SELECT archived_at FROM files WHERE id = ?", (ids[3],)).fetchone()[0] is None
    finally:
        w._quitting = True
        w.close()


def test_library_status_page_tracks_a_scan_and_counts(lib):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from lunelis.ui.main_window import SCAN_STEPS
    from lunelis.ui.status_view import StatusView
    conn, ids = lib
    archive.archive(conn, ids[:1])
    page = StatusView(conn, SCAN_STEPS)
    page.refresh()
    page.bg.wait()                                     # counted on a worker
    figures = {page.glance.itemAtPosition(r, c).widget().text(): page.glance.itemAtPosition(r, c + 1).widget().text()
               for r in range(page.glance.rowCount()) for c in (0, 2) if page.glance.itemAtPosition(r, c)}
    assert figures["Photos & videos"] == "6" and ">1<" in figures["Archived"]   # a link to the Archive
    assert page.sources.rowCount() == 1 and page.sources.item(0, 2).text() == "6"

    page.step(0, 0, 0)
    page.step_text("Scanning… 6 files")
    page.step(2, 3, 6)
    page.step_text("Reading metadata… 3 / 6")
    marks = [row[0].text() for row in page.step_rows]
    assert marks[:3] == ["✓", "✓", "▸"] and page.step_rows[2][3].text() == "Reading metadata… 3 / 6"
    assert page.step_rows[0][3].text() == "Scanning… 6 files"
    assert not page.rescan_b.isEnabled() and page.stop_b.isEnabled()
    page.finished(cancelled=False)
    assert all(row[0].text() == "✓" for row in page.step_rows)
    assert page.rescan_b.isEnabled() and page.state.text().startswith("Up to date")
    page.finished(cancelled=True)
    assert page.state.text().startswith("Stopped")


def test_rating_changes_can_be_undone_and_big_ones_are_confirmed(lib, monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox
    QApplication.instance() or QApplication([])
    from lunelis.ui import main_window as mw
    conn, ids = lib
    w = mw.MainWindow()
    try:
        w.conn = conn
        w.reload()
        w.grid.selected = set(ids)
        w.rate(stars=1)
        w.rate(label="Red")
        stars = lambda: dict(conn.execute("SELECT file_id, stars FROM ratings"))  # noqa: E731
        assert set(stars().values()) == {1}
        w.undo()                                                        # the label goes
        assert conn.execute("SELECT COUNT(*) FROM ratings WHERE color_label = 'Red'").fetchone()[0] == 0
        w.undo()                                                        # then the stars come back
        assert stars()[ids[0]] == 5 and stars()[ids[2]] == 0
        w.undo()
        assert w.status.text() == "Nothing to undo"
        # More than RATE_CONFIRM photos: asked first, and "No" changes nothing.
        monkeypatch.setattr(mw, "RATE_CONFIRM", 3)
        monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
        w.rate(stars=2)
        assert 2 not in stars().values()
    finally:
        w._quitting = True
        w.close()
