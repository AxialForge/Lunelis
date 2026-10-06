"""0.37.1 (audit LRA-004): a file moved in the library is never re-pointed in
a backup set at a copy the set didn't make."""
import hashlib
import os

from lunelis.backups import core
from lunelis.catalog.schema import open_catalog
from lunelis.importers.relink import relink
from lunelis.importers.scan import add_root, scan_root
from lunelis.jobs import engine


def _photo(seed, size=100_000):
    block = bytes((seed * 31 + i * 7) % 251 for i in range(4096))
    return (block * (size // 4096 + 1))[:size]


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_a_moved_file_onto_a_stale_backup_name_is_backed_up_and_restorable(tmp_path):
    lib, dest = tmp_path / "Lib", tmp_path / "Backup"
    (lib / "2020").mkdir(parents=True)
    (lib / "2021").mkdir()
    dest.mkdir()
    (lib / "2020/a.jpg").write_bytes(_photo(1))
    (lib / "2021/b.jpg").write_bytes(_photo(2, 120_000))
    conn = open_catalog(tmp_path / "cat.db")
    rid = add_root(conn, lib)
    scan_root(conn, rid)
    sid = core.create_set(conn, "t", str(dest), [rid])
    engine.run_job(conn, core.start(conn, sid))
    rkey = [r for r in os.listdir(dest) if r != "_Lunelis"][0]
    (dest / rkey / "2021/a.jpg").write_bytes(_photo(9))              # a stale file the set doesn't know
    os.replace(lib / "2020/a.jpg", lib / "2021/a.jpg")
    want = _sha(lib / "2021/a.jpg")
    scan_root(conn, rid)
    relink(conn)
    if conn.execute("SELECT id FROM files WHERE filename = 'a.jpg' AND missing_since IS NULL"
                    " AND rel_path = '2021/a.jpg'").fetchone() is None:
        conn.execute("DELETE FROM files WHERE filename = 'a.jpg' AND missing_since IS NULL")
        conn.execute("UPDATE files SET rel_path = '2021/a.jpg', missing_since = NULL WHERE filename = 'a.jpg'")
        conn.commit()
    engine.run_job(conn, core.start(conn, sid))
    assert _sha(dest / rkey / "2021/a.jpg") == want
    assert any(_sha(p) == _sha_bytes(_photo(9)) for p in dest.rglob("*") if p.is_file())   # stale one kept aside
    os.remove(lib / "2021/a.jpg")
    scan_root(conn, rid)
    core.restore_files(conn, sid)
    assert (lib / "2021/a.jpg").exists() and _sha(lib / "2021/a.jpg") == want
    conn.close()


def _sha_bytes(b):
    return hashlib.sha256(b).hexdigest()


def test_clearing_finished_jobs_and_deleting_files_survive_migration_records(tmp_path):
    # audit LRA-057/060: migration 40's triggers
    conn = open_catalog(tmp_path / "cat.db")
    lib = tmp_path / "Lib"
    lib.mkdir()
    (lib / "a.jpg").write_bytes(_photo(3))
    rid = add_root(conn, lib)
    scan_root(conn, rid)
    fid = conn.execute("SELECT id FROM files").fetchone()[0]
    job = engine.create_job(conn, "verify", "t", [(rid, None)])
    conn.execute("UPDATE jobs SET state = 'done' WHERE id = ?", (job,))
    conn.execute("INSERT INTO migrations (id, target, template, options, job_id) VALUES (1, 'T', '{YYYY}', '{}', ?)",
                 (job,))
    conn.execute("INSERT INTO migration_items (migration_id, file_id, src_root, src_rel, size, action)"
                 " VALUES (1, ?, ?, 'a.jpg', 1, 'skip_duplicate')", (fid, rid))
    conn.commit()
    conn.execute("DELETE FROM jobs WHERE state IN ('done', 'cancelled', 'failed')")
    conn.execute("DELETE FROM files WHERE id = ?", (fid,))
    conn.commit()
    assert conn.execute("SELECT job_id FROM migrations").fetchone()[0] is None
    assert conn.execute("SELECT COUNT(*) FROM migration_items").fetchone()[0] == 0
    conn.close()
