"""Step 8: jobs engine + byte-identical duplicate detection + quarantine."""
import os
import shutil
from datetime import datetime

import pytest

from lunelis.catalog.schema import open_catalog
from lunelis.dupes import detect
from lunelis.dupes.hashing import SLICE, Throttle, full_hash, sample_hash
from lunelis.dupes.quarantine import QUARANTINE_DIR, QuarantineRefused, quarantine, restore
from lunelis.importers.scan import add_root, scan_root
from lunelis.jobs import engine


def photo(seed: int, size: int | None = None) -> bytes:
    """Deterministic 'photo' bytes: different seeds differ everywhere and,
    like real photos, have different sizes."""
    size = size or 300_000 + seed * 1_000
    block = bytes((seed * 31 + i * 7) % 251 for i in range(4096))
    return (block * (size // 4096 + 1))[:size]


@pytest.fixture
def pools(tmp_path):
    """A main pool and an old pool that overlap, like the real library."""
    main, old = tmp_path / "Main", tmp_path / "Old"
    for d in ("2019/Chicago", "2020/Beach"):
        (main / d).mkdir(parents=True)
        (old / d).mkdir(parents=True)
    (main / "2019/Chicago/a.jpg").write_bytes(photo(1))
    (main / "2019/Chicago/b.jpg").write_bytes(photo(2))
    (main / "2020/Beach/c.jpg").write_bytes(photo(3))
    (old / "2019/Chicago/a.jpg").write_bytes(photo(1))          # identical copy
    (old / "2020/Beach/c-copy.jpg").write_bytes(photo(3))       # identical, renamed
    # Same size as b.jpg and identical in all three sampled slices, but differs
    # between them: the sample says "likely", full verification must say no.
    fake = bytearray(photo(2))
    fake[SLICE + 1000] ^= 0xFF
    (old / "2019/Chicago/b-edited.jpg").write_bytes(bytes(fake))
    conn = open_catalog(tmp_path / "cat.db")
    ids = [add_root(conn, main), add_root(conn, old)]
    for rid in ids:
        scan_root(conn, rid)
    yield conn, main, old, ids
    conn.close()


def _names(conn, group_id):
    return sorted(r[0] for r in conn.execute(
        "SELECT f.rel_path FROM duplicate_group_files m JOIN files f ON f.id = m.file_id"
        " WHERE m.group_id = ?", (group_id,)))


def _groups(conn, method):
    return {gid: _names(conn, gid) for (gid,) in conn.execute(
        "SELECT id FROM duplicate_groups WHERE method = ?", (method,))}


# --- hashing ------------------------------------------------------------------------

def test_sample_hash_reads_little_and_matches_copies(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(photo(5, 5_000_000))
    b.write_bytes(photo(5, 5_000_000))
    ha, read = sample_hash(str(a), 5_000_000)
    assert ha == sample_hash(str(b), 5_000_000)[0] and read == 3 * SLICE
    assert full_hash(str(a))[0] == full_hash(str(b))[0]


def test_throttle_caps_rate():
    t = Throttle(10)                                   # 10 MB/s
    start = datetime.now()
    for _ in range(4):
        t.spend(1_000_000)                             # 4 MB -> ~0.4 s minus burst
    assert (datetime.now() - start).total_seconds() >= 0.1


# --- one folder at a time ------------------------------------------------------------

def test_one_folder_finds_its_copies_anywhere(pools):
    conn, _, _, (main_id, _) = pools
    r = detect.process_folder(conn, main_id, "2019/Chicago")
    groups = _groups(conn, "sampled")
    # Only Chicago was processed, yet its twins in the OTHER pool were hashed
    # and grouped - "when a folder is done, compare against everything".
    assert sorted(groups.values()) == [["2019/Chicago/a.jpg", "2019/Chicago/a.jpg"],
                                       ["2019/Chicago/b-edited.jpg", "2019/Chicago/b.jpg"]]
    assert r.hashed == 4                               # a, b and their two same-size twins
    c = conn.execute("SELECT sample_hash FROM files WHERE rel_path = '2020/Beach/c.jpg'").fetchone()[0]
    assert c is None                                   # Beach wasn't touched yet


def test_unique_sizes_are_never_read(pools, tmp_path):
    conn, main, _, (main_id, _) = pools
    (main / "2020/Beach/unique.jpg").write_bytes(photo(9, 123_457))
    scan_root(conn, main_id)
    detect.process_folder(conn, main_id, "2020/Beach")
    assert conn.execute("SELECT sample_hash FROM files WHERE rel_path LIKE '%unique.jpg'").fetchone()[0] is None


def test_verification_rejects_a_sample_collision(pools):
    conn, _, _, (main_id, _) = pools
    detect.process_folder(conn, main_id, "2019/Chicago")
    exact = []
    for gid in list(_groups(conn, "sampled")):
        exact += detect.verify_group(conn, gid)
    verified = {tuple(_names(conn, g)) for g in exact}
    assert verified == {("2019/Chicago/a.jpg", "2019/Chicago/a.jpg")}   # b vs b-edited rejected


def test_keeper_prefers_root_order_then_not_takeout(pools):
    conn, _, _, (main_id, old_id) = pools
    detect.process_folder(conn, main_id, "2019/Chicago")
    gid = next(g for g, names in _groups(conn, "sampled").items() if names[0].endswith("a.jpg"))
    (exact,) = detect.verify_group(conn, gid)
    keep = detect.suggest_keeper(conn, exact, preferred_roots=[old_id, main_id])
    assert conn.execute("SELECT root_id FROM files WHERE id = ?", (keep,)).fetchone()[0] == old_id


# --- the jobs engine -------------------------------------------------------------------

def test_job_runs_folder_by_folder_and_resumes(pools):
    conn, _, _, (main_id, old_id) = pools
    job = engine.create_job(conn, "duplicates", "Main pool", [(main_id, None)])
    assert conn.execute("SELECT COUNT(*) FROM job_folders WHERE job_id = ?", (job,)).fetchone()[0] == 2

    # Like the app's pause button: a plain flag (should_stop is also polled
    # from hashing threads, so it must not touch the catalog). Status is
    # reported as each folder starts; press pause as the second one begins.
    started, stop = [], [False]

    def on_status(job_id, text):
        started.append(text)
        if len(started) == 2:
            stop[0] = True

    out = engine.run_job(conn, job, should_stop=lambda: stop[0], on_status=on_status)
    assert out.state == "paused"
    assert engine.recover_interrupted(conn) == 0          # paused isn't "interrupted"

    # Simulate a power cut mid-run: state left as 'running'.
    conn.execute("UPDATE jobs SET state = 'running' WHERE id = ?", (job,))
    conn.commit()
    assert engine.recover_interrupted(conn) == 1
    assert engine.next_runnable(conn) == job
    out = engine.run_job(conn, job)
    assert out.state == "done"
    files_done, files_total = conn.execute(
        "SELECT files_done, files_total FROM jobs WHERE id = ?", (job,)).fetchone()
    assert files_done == files_total == 3
    assert len(_groups(conn, "sampled")) == 3             # a, b~b-edited, c~c-copy


def test_job_waits_when_the_share_disappears(pools, tmp_path):
    conn, main, old, (main_id, old_id) = pools
    job = engine.create_job(conn, "duplicates", "Main", [(main_id, None)])
    shutil.move(str(old), str(tmp_path / "unplugged"))    # the twins' share goes away

    real = detect.sample_hash

    def offline_for_old(path, size, throttle=None):
        if "Old" in path:
            err = OSError(53, "The network path was not found")
            err.winerror = 53
            raise err
        return real(path, size, throttle)

    detect.sample_hash = offline_for_old
    try:
        out = engine.run_job(conn, job)
    finally:
        detect.sample_hash = real
    assert out.state == "waiting" and "online" in out.status
    assert conn.execute("SELECT COUNT(*) FROM job_folders WHERE job_id = ? AND state = 'done'",
                        (job,)).fetchone()[0] == 0        # nothing wrongly marked finished


def test_schedule_gates(pools):
    at = lambda h: datetime(2026, 9, 27, h)
    assert engine.schedule_allows({"mode": "window", "start_hour": 22, "end_hour": 6}, at(23))[0]
    assert engine.schedule_allows({"mode": "window", "start_hour": 22, "end_hour": 6}, at(3))[0]
    assert not engine.schedule_allows({"mode": "window", "start_hour": 22, "end_hour": 6}, at(12))[0]
    assert not engine.schedule_allows({"mode": "idle", "idle_minutes": 5}, idle=lambda: 60)[0]
    assert engine.schedule_allows({"mode": "idle", "idle_minutes": 5}, idle=lambda: 600)[0]

    conn, _, _, (main_id, _) = pools
    job = engine.create_job(conn, "duplicates", "Idle only", [(main_id, None)],
                            {"schedule": {"mode": "idle", "idle_minutes": 5}})
    assert engine.run_job(conn, job, idle=lambda: 10).state == "waiting"
    assert engine.run_job(conn, job, idle=lambda: 3600).state == "done"


def test_full_hash_job(pools):
    conn, _, _, (main_id, _) = pools
    job = engine.create_job(conn, "full_hash", "Baseline", [(main_id, "2019/Chicago")])
    assert engine.run_job(conn, job).state == "done"
    assert conn.execute("SELECT COUNT(*) FROM files WHERE content_hash IS NOT NULL").fetchone()[0] == 2


# --- quarantine -----------------------------------------------------------------------

def _verified_a_group(conn, main_id):
    detect.process_folder(conn, main_id, "2019/Chicago")
    for gid, names in _groups(conn, "sampled").items():
        if names[0].endswith("a.jpg"):
            return detect.verify_group(conn, gid)[0]


def test_quarantine_moves_never_deletes_and_restores(pools, tmp_path):
    conn, main, old, (main_id, old_id) = pools
    gid = _verified_a_group(conn, main_id)
    old_copy = conn.execute("SELECT id FROM files WHERE root_id = ? AND rel_path = '2019/Chicago/a.jpg'",
                            (old_id,)).fetchone()[0]
    (dst,) = quarantine(conn, gid, [old_copy], backup_dir=tmp_path / "backups")
    assert not (old / "2019/Chicago/a.jpg").exists()
    assert os.path.exists(dst) and QUARANTINE_DIR in dst
    assert list((tmp_path / "backups").glob("*before-quarantine.zip"))     # snapshot first
    # A rescan doesn't catalog the quarantine folder as new photos.
    before = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    scan_root(conn, old_id)
    assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == before

    restore(conn, old_copy)
    assert (old / "2019/Chicago/a.jpg").read_bytes() == photo(1)


def test_quarantine_refuses_unsafe_requests(pools):
    conn, _, _, (main_id, _) = pools
    gid = _verified_a_group(conn, main_id)
    both = [fid for (fid,) in conn.execute("SELECT file_id FROM duplicate_group_files WHERE group_id = ?", (gid,))]
    with pytest.raises(QuarantineRefused, match="no copy"):
        quarantine(conn, gid, both)                                   # would leave nothing
    sampled = next(iter(_groups(conn, "sampled")))
    with pytest.raises(QuarantineRefused, match="verified"):
        quarantine(conn, sampled, both[:1])                           # sample alone isn't enough


def test_same_size_raws_with_different_capture_times_are_not_read(tmp_path):
    """Uncompressed ARWs are all one size; only same-size AND same-capture-time
    files are candidates (identical bytes imply identical EXIF)."""
    root = tmp_path / "Pool"
    (root / "A").mkdir(parents=True)
    (root / "B").mkdir()
    size = 400_000
    (root / "A/DSC1.ARW").write_bytes(photo(1, size))
    (root / "B/DSC1.ARW").write_bytes(photo(1, size))          # a real copy
    for i in range(2, 6):
        (root / f"B/DSC{i}.ARW").write_bytes(photo(i, size))   # other shots, same size
    conn = open_catalog(tmp_path / "c.db")
    rid = add_root(conn, root)
    scan_root(conn, rid)
    for fid, rel in conn.execute("SELECT id, rel_path FROM files").fetchall():
        shot = rel.rsplit("DSC", 1)[1].split(".")[0]
        conn.execute("INSERT INTO exif (file_id, captured_at) VALUES (?, ?)",
                     (fid, f"2026-06-19T10:00:0{shot}.100"))
    conn.commit()
    r = detect.process_folder(conn, rid, "A")
    assert r.hashed == 2                                         # the copy, not the other 4 shots
    assert list(_groups(conn, "sampled").values()) == [["A/DSC1.ARW", "B/DSC1.ARW"]]
    conn.close()


def test_duplicates_view_groups(pools):
    import os as _os
    _os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from lunelis.ui.dupes_view import load_groups
    conn, _, _, (main_id, old_id) = pools
    detect.process_folder(conn, main_id, "2019/Chicago")
    before = load_groups(conn, [])
    assert {g.verified for g in before} == {False} and len(before) == 2
    for g in before:
        detect.verify_group(conn, g.id)
    after = load_groups(conn, [old_id])
    # b vs b-edited dissolved on verification; a became one verified group
    # (its likely twin is hidden), with the keeper in the preferred root.
    assert len(after) == 1 and after[0].verified
    keep = conn.execute("SELECT root_id FROM files WHERE id = ?", (after[0].keeper,)).fetchone()[0]
    assert keep == old_id and after[0].extra_bytes == after[0].size
