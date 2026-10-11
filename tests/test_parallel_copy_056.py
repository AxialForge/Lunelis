"""0.56: a migration copies several files at once to a network share - one stream left a fast link idle."""
import os
import threading

from lunelis.importing.templates import DEFAULT_TEMPLATE
from lunelis.jobs import engine
from lunelis.migrate import execute, logs, plan
from test_migrate import all_files, lib  # noqa: F401  (fixture)


def _run(conn, target, monkeypatch, workers):
    monkeypatch.setattr(execute, "copy_workers", lambda target, opts: workers)
    roots = [r[0] for r in conn.execute("SELECT id FROM roots ORDER BY id")]
    mid = plan.plan(conn, str(target), DEFAULT_TEMPLATE, plan.Options(roots))
    job = execute.start(conn, mid)
    engine.run_job(conn, job)
    return mid


def test_local_targets_copy_one_at_a_time_and_a_share_several(tmp_path):
    assert execute.copy_workers(str(tmp_path), {}) == 1
    assert execute.copy_workers("//nas/photos", {}) == execute.NETWORK_COPIES
    assert execute.copy_workers(str(tmp_path), {"copy_workers": 3}) == 3


def test_parallel_copies_end_exactly_like_one_at_a_time(lib, monkeypatch):
    conn, tmp, a, b, target, ra, rb, ids = lib
    threads = set()
    real = execute._copy_verified

    def seen(*args):
        threads.add(threading.current_thread().name)
        return real(*args)
    monkeypatch.setattr(execute, "_copy_verified", seen)
    mid = _run(conn, target, monkeypatch, 4)
    assert any(n.startswith("migrate-copy") for n in threads)           # copies were made side by side
    states = dict(conn.execute("SELECT state, COUNT(*) FROM migration_items WHERE migration_id = ? GROUP BY state",
                               (mid,)))
    assert not states.get("failed") and not states.get("planned")
    assert logs.accounted(conn, mid).clean
    assert not [p for p in all_files(target) if ".lunelis-migrating-" in p]
    # every copy is byte for byte its original, and every catalog entry points at an existing file
    for src_root, src_rel, dest_rel, sha in conn.execute(
            "SELECT src_root, src_rel, dest_rel, sha256 FROM migration_items WHERE migration_id = ?"
            " AND action = 'move'", (mid,)):
        root = conn.execute("SELECT path FROM roots WHERE id = ?", (src_root,)).fetchone()[0]
        assert execute._sha256(os.path.join(root, *src_rel.split("/"))) == sha
        assert execute._sha256(os.path.join(str(target), *dest_rel.split("/"))) == sha


def test_a_copy_that_does_not_match_is_removed_and_reported(lib, monkeypatch):
    conn, tmp, a, b, target, ra, rb, ids = lib
    real = execute._sha256
    spoiled = []

    def sha(path, throttle=None):
        if str(target) in path and path.endswith("DSC001.JPG") and not spoiled:
            spoiled.append(path)
            return "0" * 64                                              # the read-back differs once
        return real(path, throttle)
    monkeypatch.setattr(execute, "_sha256", sha)
    mid = _run(conn, target, monkeypatch, 4)
    failed = conn.execute("SELECT error FROM migration_items WHERE migration_id = ? AND state = 'failed'",
                          (mid,)).fetchall()
    assert len(failed) == 1 and "didn't match" in failed[0][0]
    assert spoiled and not os.path.exists(spoiled[0])                    # the bad copy is gone


def test_two_files_wanting_one_place_are_left_to_the_careful_path(lib, monkeypatch):
    conn, tmp, a, b, target, ra, rb, ids = lib
    went = []
    real = execute._copy_verified
    monkeypatch.setattr(execute, "_copy_verified", lambda s, d, *r: went.append(d.lower()) or real(s, d, *r))
    mid = _run(conn, target, monkeypatch, 4)
    assert len(went) == len(set(went))                                   # no place was written by two threads
    assert logs.accounted(conn, mid).clean


def test_a_source_photo_the_catalog_has_elsewhere_is_accounted_for(lib, monkeypatch, tmp_path):
    """0.56.1: 37 Takeout files a library update had recognised in another source blocked Release."""
    import shutil
    conn, tmp, a, b, target, ra, rb, ids = lib
    # One photo of source A is "recognised" in a third source before the plan: its entry points there.
    from lunelis.importers.scan import add_root
    pool = tmp_path / "pool"
    pool.mkdir()
    rc = add_root(conn, str(pool))
    fid, rel = conn.execute("SELECT id, rel_path FROM files WHERE root_id = ? AND rel_path LIKE '%DSC002.ARW'",
                            (ra,)).fetchone()
    shutil.copy2(os.path.join(str(a), *rel.split("/")), pool / "DSC002.ARW")
    conn.execute("UPDATE files SET root_id = ?, rel_path = 'DSC002.ARW' WHERE id = ?", (rc, fid))
    conn.execute("INSERT INTO file_moves (file_id, from_root, from_path, to_root, to_path, method)"
                 " VALUES (?, ?, ?, ?, 'DSC002.ARW', 'exif')", (fid, ra, rel, rc))
    conn.commit()
    mid = plan.plan(conn, str(target), DEFAULT_TEMPLATE, plan.Options([ra, rb]))
    engine.run_job(conn, execute.start(conn, mid))
    rep = logs.accounted(conn, mid)
    assert rep.clean and rep.counts["Left in place - an identical copy is already in your library"] == 1
    # ...but only when it really is the same picture
    (pool / "DSC002.ARW").write_bytes(b"x" * os.path.getsize(pool / "DSC002.ARW"))
    rep = logs.accounted(conn, mid)
    assert not rep.clean and len(rep.unaccounted) == 1
