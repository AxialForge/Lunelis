"""The Quarantine page: listing, kept-copy checks, restore, empty. Temp folders only;
the Recycle Bin call is replaced so nothing lands in the real one."""
import os

import pytest
from PySide6.QtWidgets import QApplication

from lunelis import paths
from lunelis.catalog.schema import open_catalog
from lunelis.dupes import detect, manage
from lunelis.dupes.quarantine import QUARANTINE_DIR, quarantine
from lunelis.importers.scan import add_root, scan_root

from test_dupes import photo


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DEFAULT_CATALOG_PATH", tmp_path / "catalog.db")
    recycled = []
    monkeypatch.setattr(manage, "_recycle", lambda p: (recycled.append(p), os.remove(p)))
    main, old = tmp_path / "Main", tmp_path / "Old"
    (main / "2019").mkdir(parents=True)
    (old / "2019").mkdir(parents=True)
    for name, seed in (("a.jpg", 1), ("b.jpg", 2)):
        (main / "2019" / name).write_bytes(photo(seed))
        (old / "2019" / name).write_bytes(photo(seed))
    conn = open_catalog(paths.DEFAULT_CATALOG_PATH)
    ids = [add_root(conn, main), add_root(conn, old)]
    for rid in ids:
        scan_root(conn, rid)
    detect.process_folder(conn, ids[0], "2019")
    for (gid,) in conn.execute("SELECT id FROM duplicate_groups").fetchall():
        detect.verify_group(conn, gid)
    exact = conn.execute("SELECT id FROM duplicate_groups WHERE method = 'exact'").fetchall()
    for (gid,) in exact:
        old_copy = conn.execute("SELECT f.id FROM duplicate_group_files m JOIN files f ON f.id = m.file_id"
                                " WHERE m.group_id = ? AND f.root_id = ?", (gid, ids[1])).fetchone()[0]
        quarantine(conn, gid, [old_copy])
    yield conn, main, old, recycled
    conn.close()


def test_lists_with_reason_and_kept_copy(lib):
    conn, main, old, _ = lib
    items = manage.entries(conn)
    assert len(items) == 2 and {e.reason for e in items} == {"duplicate"}
    e = next(x for x in items if x.original.endswith("a.jpg"))
    assert e.present and e.kept_ok() and e.kept == os.path.join(str(main), "2019", "a.jpg")
    assert QUARANTINE_DIR in e.now
    drives = manage.summary(items)
    assert sum(n for n, _ in drives.values()) == 2


def test_restore(lib):
    conn, main, old, _ = lib
    e = next(x for x in manage.entries(conn) if x.original.endswith("a.jpg"))
    manage.restore_entry(conn, e)
    assert (old / "2019" / "a.jpg").exists() and len(manage.entries(conn)) == 1


def test_empty_only_with_a_kept_copy(lib, monkeypatch):
    conn, main, old, recycled = lib
    os.remove(main / "2019" / "b.jpg")                              # b's kept copy is gone
    items = manage.entries(conn)
    res = manage.empty(conn, items, backup_dir=paths.BACKUP_DIR)
    assert res.recycled == 1 and len(res.skipped) == 1 and "kept copy" in res.skipped[0][1]
    assert len(recycled) == 1 and recycled[0].endswith("a.jpg")
    left = manage.entries(conn)
    assert len(left) == 1 and left[0].original.endswith("b.jpg") and left[0].present   # b stays safe
    assert conn.execute("SELECT COUNT(*) FROM files WHERE rel_path = '2019/a.jpg'").fetchone()[0] == 1  # only the kept one
    log = conn.execute("SELECT reason, how, kept FROM purged").fetchall()
    assert [tuple(r) for r in log] == [("duplicate", "recycled", os.path.join(str(main), "2019", "a.jpg"))]
    assert not (old / QUARANTINE_DIR / "2019" / "a.jpg").exists()


def test_network_files_are_deleted_not_recycled(lib, monkeypatch):
    conn, *_ , recycled = lib
    monkeypatch.setattr(manage, "_is_network", lambda p: True)
    res = manage.empty(conn, manage.entries(conn))
    assert res.deleted == 2 and not recycled
    assert {r[0] for r in conn.execute("SELECT how FROM purged")} == {"deleted"}
    assert manage.entries(conn) == []


def test_migrated_original(lib, tmp_path):
    conn, main, old, recycled = lib
    new = tmp_path / "Library"
    (new / "2019").mkdir(parents=True)
    (new / "2019" / "c.jpg").write_bytes(photo(3))
    rid = add_root(conn, new)
    scan_root(conn, rid)
    fid = conn.execute("SELECT id FROM files WHERE rel_path = '2019/c.jpg'").fetchone()[0]
    q = old / QUARANTINE_DIR / "migration-1" / "2019" / "c.jpg"
    q.parent.mkdir(parents=True)
    q.write_bytes(photo(3))
    old_root = conn.execute("SELECT id FROM roots WHERE path = ?", (str(old),)).fetchone()[0]
    conn.execute("INSERT INTO migrations (id, target, template, options, state, finished_at)"
                 " VALUES (1, ?, 't', '{}', 'done', '2026-09-27')", (str(new),))
    conn.execute("INSERT INTO migration_items (migration_id, file_id, src_root, src_rel, size, action, state,"
                 " quarantine_path) VALUES (1, ?, ?, '2019/c.jpg', ?, 'move', 'done', ?)",
                 (fid, old_root, len(photo(3)), str(q)))
    conn.commit()
    e = next(x for x in manage.entries(conn) if x.reason == "original")
    assert e.kept == os.path.join(str(new), "2019", "c.jpg") and e.kept_ok()
    manage.restore_entry(conn, e)
    assert (old / "2019" / "c.jpg").exists()
    assert conn.execute("SELECT state FROM migration_items").fetchone()[0] == "restored"


def _settle(page):
    import time
    end = time.time() + 20
    while page._thread is not None and time.time() < end:
        QApplication.processEvents()


def test_page(lib, monkeypatch):
    QApplication.instance() or QApplication([])
    from lunelis.ui import quarantine_view as qv
    conn, main, old, recycled = lib
    page = qv.QuarantineView(conn)
    page.refresh()
    _settle(page)                                                     # loaded off the GUI thread
    assert page.table.rowCount() == 2 and "2 files" in page.summary.text()
    assert page.table.item(0, 5).text().startswith("✓")
    page.reason.setCurrentIndex(page.reason.findData("similar"))
    assert page.table.rowCount() == 0
    page.reason.setCurrentIndex(0)
    page.table.selectRow(0)
    monkeypatch.setattr(qv.QMessageBox, "information", lambda *a, **k: None)
    page.restore_selected()
    _settle(page)
    _settle(page)                                                     # the refresh after restoring
    assert page.table.rowCount() == 1
    dlg = qv.ConfirmEmpty(page.items)
    assert dlg.go.isEnabled()                                        # local only: no extra tick needed
    monkeypatch.setattr(manage, "_is_network", lambda p: True)
    dlg2 = qv.ConfirmEmpty(page.items)
    assert not dlg2.go.isEnabled()
    dlg2.agree.setChecked(True)
    assert dlg2.go.isEnabled()
