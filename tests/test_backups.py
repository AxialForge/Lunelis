"""Phase 2, step 3: backups - incremental, verified, never deleting; verify
passes; restoring missing/damaged files or everything into a new folder."""
import json
import os
import shutil

import pytest

from lunelis.backups import core
from lunelis.catalog.schema import open_catalog
from lunelis.dupes.quarantine import QUARANTINE_DIR
from lunelis.importers.scan import add_root, scan_root
from lunelis.jobs import engine
from lunelis.xmp.sync import root_key


def files_in(folder):
    return sorted(str(p.relative_to(folder)).replace("\\", "/") for p in folder.rglob("*") if p.is_file())


@pytest.fixture
def lib(tmp_path):
    photos, usb = tmp_path / "Photos", tmp_path / "USB"
    for rel, data in (("2026/6-19-2026/a.jpg", b"A" * 5000), ("2026/6-19-2026/b.jpg", b"B" * 7000),
                      ("2025/x.jpg", b"X" * 3000)):
        p = photos / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    (photos / "2026/6-19-2026/a.jpg.xmp").write_text("<xmp/>", encoding="utf-8")
    usb.mkdir()
    conn = open_catalog(tmp_path / "cat.db")
    rid = add_root(conn, photos)
    scan_root(conn, rid)
    yield conn, tmp_path, photos, usb, rid
    conn.close()


def run(conn, sid, verify=False):
    job = core.start(conn, sid, verify=verify)
    return engine.run_job(conn, job)


def test_backup_mirrors_verifies_and_carries_the_catalog(lib):
    conn, tmp, photos, usb, rid = lib
    sid = core.create_set(conn, "USB stick", str(usb), [rid])
    assert core.status(conn, sid).pending_files == 3
    assert run(conn, sid).state == "done"
    key = root_key(rid, str(photos))
    mirror = [p for p in files_in(usb) if not p.startswith("_Lunelis")]
    assert mirror == [f"{key}/2025/x.jpg", f"{key}/2026/6-19-2026/a.jpg", f"{key}/2026/6-19-2026/a.jpg.xmp",
                      f"{key}/2026/6-19-2026/b.jpg"]
    assert (usb / key / "2026/6-19-2026/a.jpg").read_bytes() == b"A" * 5000
    assert list((usb / "_Lunelis/catalog").glob("catalog-*.zip"))
    assert json.loads((usb / "_Lunelis/backup.json").read_text())["name"] == "USB stick"
    st = core.status(conn, sid)
    assert (st.backed_up, st.pending_files) == (3, 0)
    assert core.get_set(conn, sid).status == "Up to date"
    # The first backup also gave every file its integrity baseline.
    assert conn.execute("SELECT COUNT(*) FROM files WHERE content_hash IS NULL").fetchone()[0] == 0


def test_incremental_keeps_old_versions_and_follows_moves(lib):
    conn, tmp, photos, usb, rid = lib
    sid = core.create_set(conn, "USB", str(usb), [rid])
    run(conn, sid)
    key = root_key(rid, str(photos))
    # Unchanged -> nothing copied again.
    before = {p: os.stat(usb / p).st_mtime_ns for p in files_in(usb) if not p.startswith("_Lunelis")}
    run(conn, sid)
    assert {p: os.stat(usb / p).st_mtime_ns for p in before} == before
    # Changed -> new copy, and the previous one is kept.
    (photos / "2025/x.jpg").write_bytes(b"Y" * 3100)
    scan_root(conn, rid)
    run(conn, sid)
    assert (usb / key / "2025/x.jpg").read_bytes() == b"Y" * 3100
    assert [p for p in files_in(usb) if "previous-versions" in p and p.endswith("2025/x.jpg")]
    # Moved inside the library (e.g. a migration) -> renamed inside the backup, not copied.
    fid = conn.execute("SELECT id FROM files WHERE filename = 'b.jpg'").fetchone()[0]
    (photos / "Trip").mkdir()
    os.replace(photos / "2026/6-19-2026/b.jpg", photos / "Trip/b.jpg")
    conn.execute("UPDATE files SET rel_path = 'Trip/b.jpg' WHERE id = ?", (fid,))
    conn.commit()
    run(conn, sid)
    assert (usb / key / "Trip/b.jpg").exists() and not (usb / key / "2026/6-19-2026/b.jpg").exists()
    # Deleted from the library -> still in the backup.
    os.unlink(photos / "Trip/b.jpg")
    scan_root(conn, rid)
    run(conn, sid)
    assert (usb / key / "Trip/b.jpg").exists()


def test_verify_finds_bit_rot(lib):
    conn, tmp, photos, usb, rid = lib
    sid = core.create_set(conn, "USB", str(usb), [rid])
    run(conn, sid)
    key = root_key(rid, str(photos))
    (usb / key / "2025/x.jpg").write_bytes(b"X" * 2999 + b"!")
    assert run(conn, sid, verify=True).state == "done"
    probs = conn.execute("SELECT problem FROM backup_files WHERE problem IS NOT NULL").fetchall()
    assert len(probs) == 1 and "changed" in probs[0][0]
    assert "1 copies need attention" in core.get_set(conn, sid).status


def test_restore_missing_and_damaged_files(lib):
    conn, tmp, photos, usb, rid = lib
    sid = core.create_set(conn, "USB", str(usb), [rid])
    run(conn, sid)
    os.unlink(photos / "2025/x.jpg")                               # lost
    scan_root(conn, rid)
    a = conn.execute("SELECT id FROM files WHERE filename = 'a.jpg'").fetchone()[0]
    (photos / "2026/6-19-2026/a.jpg").write_bytes(b"\0" * 5000)     # zero-filled, same size
    conn.execute("INSERT INTO damaged (file_id, problem) VALUES (?, 'zero_filled')", (a,))
    conn.commit()
    assert len(core.restorable(conn, sid)) == 2
    res = core.restore_files(conn, sid)
    assert (res.restored, res.failed) == (2, 0)
    assert (photos / "2025/x.jpg").read_bytes() == b"X" * 3000
    assert (photos / "2026/6-19-2026/a.jpg").read_bytes() == b"A" * 5000
    # The damaged one was set aside, not overwritten in place.
    assert [p for p in files_in(photos) if p.startswith(QUARANTINE_DIR) and p.endswith("a.jpg")]
    assert conn.execute("SELECT COUNT(*) FROM damaged").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM files WHERE missing_since IS NOT NULL").fetchone()[0] == 0


def test_restore_everything_to_a_new_folder(lib):
    conn, tmp, photos, usb, rid = lib
    sid = core.create_set(conn, "USB", str(usb), [rid])
    run(conn, sid)
    new = tmp / "NewPC"
    res = core.restore_files(conn, sid, to_folder=str(new))
    assert res.restored == 3
    assert len(files_in(new)) == 3


def test_unplugged_drive_waits_and_bad_places_are_refused(lib):
    conn, tmp, photos, usb, rid = lib
    with pytest.raises(core.BackupError):
        core.create_set(conn, "Inside", str(photos / "2026"), [rid])
    sid = core.create_set(conn, "USB", str(usb), [rid])
    shutil.rmtree(usb)
    assert run(conn, sid).state == "waiting"
    assert "Waiting for the backup drive" in core.get_set(conn, sid).status


def test_backups_page_lists_sets_and_their_state(lib):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from lunelis.ui.backups_view import BackupsView
    conn, tmp, photos, usb, rid = lib
    sid = core.create_set(conn, "Blue USB", str(usb), [rid])
    view = BackupsView(conn)
    view.bg.wait()                                     # read on a worker
    assert view.table.rowCount() == 1
    assert view.table.item(0, 3).text() == "0 of 3" and view.table.item(0, 5).text() == "Never"
    run(conn, sid)
    view.refresh()
    view.bg.wait()
    assert view.table.item(0, 2).text() == "Up to date" and view.table.item(0, 4).text() == "Nothing"
