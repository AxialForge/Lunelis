"""0.51: fixes from the migration engine audit - a keeper that didn't arrive,
damaged files matched to a different photo, originals left 'copied', moves
cut short, sidecar clashes, a full target, pairs split by a missing date,
cancelling, two sources with one name. Temp folders only."""
import errno
import os

import pytest

from lunelis.importing.templates import DEFAULT_TEMPLATE
from lunelis.jobs import engine
from lunelis.migrate import execute, logs
from lunelis.migrate.plan import Options, plan
from test_migrate import _run, all_files, jpeg, lib  # noqa: F401 - the shared fixture


def _states(conn, mid):
    return dict(conn.execute("SELECT src_rel, state FROM migration_items WHERE migration_id = ?", (mid,)))


def test_a_duplicate_stays_put_when_its_keeper_failed_to_copy(lib, monkeypatch):
    conn, tmp, a, b, target, ra, rb, ids = lib
    real = execute._copy_hashed

    def copy(src, dst, *a_, **k):
        if src.endswith("DSC001.JPG") and "PoolA" in src:
            raise PermissionError(13, "denied", src)          # not a network error: the item fails
        return real(src, dst, *a_, **k)
    monkeypatch.setattr(execute, "_copy_hashed", copy)
    mid = plan(conn, str(target), DEFAULT_TEMPLATE, Options([ra, rb], keep_sources=False))
    _run(conn, mid, tmp)
    st = _states(conn, mid)
    assert st["2024/6-19-2024 Air Show/DSC001.JPG"] == "failed"
    # Its identical copy in the other pool is NOT set aside: it's one of the two copies left.
    assert (b / "2024 Album/6-19-2024 Air Show/DSC001.JPG").exists()
    assert st["2024 Album/6-19-2024 Air Show/DSC001.JPG"] != "done"


def test_a_damaged_file_isnt_skipped_for_a_different_photo_with_its_name_and_size(tmp_path):
    from lunelis.catalog.schema import open_catalog
    from lunelis.importers.metadata import extract_pending
    from lunelis.importers.scan import add_root, scan_root
    a, b, target = tmp_path / "A", tmp_path / "B", tmp_path / "T"
    jpeg(a / "IMG_0001.JPG", None, 7)                             # no capture time
    b.mkdir()
    data = bytearray((a / "IMG_0001.JPG").read_bytes())
    data[-10] ^= 0xFF                                             # same size, other bytes
    (b / "IMG_0001.JPG").write_bytes(bytes(data))
    os.utime(a / "IMG_0001.JPG", (1_500_000_000, 1_500_000_000))
    os.utime(b / "IMG_0001.JPG", (1_600_000_000, 1_600_000_000))  # years apart: another shot
    target.mkdir()
    conn = open_catalog(tmp_path / "c.db")
    ra, rb = add_root(conn, a), add_root(conn, b)
    for r in (ra, rb):
        scan_root(conn, r)
    extract_pending(conn)
    bad = conn.execute("SELECT id FROM files WHERE root_id = ?", (ra,)).fetchone()[0]
    conn.execute("INSERT INTO damaged (file_id, problem) VALUES (?, 'corrupt')", (bad,))
    conn.commit()
    mid = plan(conn, str(target), DEFAULT_TEMPLATE, Options([ra, rb]))
    act, dest = conn.execute("SELECT action, dest_rel FROM migration_items WHERE file_id = ?", (bad,)).fetchone()
    assert act == "move" and dest.startswith("Lunelis/Damaged/")
    # The same modified time (a real copy) still counts as the intact copy.
    os.utime(b / "IMG_0001.JPG", (1_500_000_000, 1_500_000_000))
    scan_root(conn, rb)
    conn.execute("DELETE FROM migration_items"); conn.execute("DELETE FROM migrations"); conn.commit()
    mid = plan(conn, str(target), DEFAULT_TEMPLATE, Options([ra, rb]))
    assert conn.execute("SELECT action FROM migration_items WHERE file_id = ?", (bad,)).fetchone()[0] == "skip_damaged"
    conn.close()


def test_an_original_that_couldnt_be_set_aside_is_tried_again_at_the_end(lib, monkeypatch):
    conn, tmp, a, b, target, ra, rb, ids = lib
    real, tried = execute._quarantine_original, set()

    def flaky(root, rel, *a_, **k):
        if rel not in tried:
            tried.add(rel)
            raise FileExistsError("the Trash share was busy")
        return real(root, rel, *a_, **k)
    monkeypatch.setattr(execute, "_quarantine_original", flaky)
    mid = plan(conn, str(target), DEFAULT_TEMPLATE, Options([ra, rb], keep_sources=False))
    _run(conn, mid, tmp)
    assert "copied" not in _states(conn, mid).values()
    assert conn.execute("SELECT COUNT(*) FROM migration_items WHERE migration_id = ? AND action = 'move'"
                        " AND (state != 'done' OR quarantine_path IS NULL)", (mid,)).fetchone()[0] == 0


def test_a_move_to_another_drive_cut_short_leaves_no_half_file(tmp_path, monkeypatch):
    import shutil
    from lunelis.dupes import quarantine
    src, dst = tmp_path / "src.jpg", tmp_path / "Trash" / "src.jpg"
    src.write_bytes(os.urandom(5000))
    dst.parent.mkdir()
    monkeypatch.setattr(os, "rename", lambda *a: (_ for _ in ()).throw(OSError(errno.EXDEV, "other drive")))

    def half(s, d):
        with open(d, "wb") as fh:
            fh.write(open(s, "rb").read()[:100])
        raise OSError(errno.EIO, "the share went away")
    monkeypatch.setattr(shutil, "copy2", half)
    with pytest.raises(OSError):
        quarantine.move_one(str(src), str(dst))
    assert src.exists() and os.listdir(dst.parent) == []


def test_the_set_aside_place_is_recorded_before_the_move(lib, monkeypatch):
    conn, tmp, a, b, target, ra, rb, ids = lib
    from lunelis.dupes import quarantine
    real = quarantine.move_pair

    def crash(src, dst, *rest):
        real(src, dst, *rest)
        raise KeyboardInterrupt                                      # "power cut" after the move
    monkeypatch.setattr(quarantine, "move_pair", crash)
    mid = plan(conn, str(target), DEFAULT_TEMPLATE, Options([ra], keep_sources=False))
    with pytest.raises(KeyboardInterrupt):
        _run(conn, mid, tmp)
    q = conn.execute("SELECT quarantine_path FROM migration_items WHERE migration_id = ? AND state = 'copied'",
                     (mid,)).fetchone()[0]
    assert q and os.path.exists(q)                                  # we know where it went
    monkeypatch.setattr(quarantine, "move_pair", real)
    conn.execute("UPDATE jobs SET state = 'queued'"); conn.commit()
    engine.run_job(conn, conn.execute("SELECT job_id FROM migrations WHERE id = ?", (mid,)).fetchone()[0])
    assert conn.execute("SELECT COUNT(*) FROM migration_items WHERE migration_id = ? AND action = 'move'"
                        " AND quarantine_path IS NULL", (mid,)).fetchone()[0] == 0


def test_a_sidecar_clash_takes_back_the_photo_copy(lib):
    conn, tmp, a, b, target, ra, rb, ids = lib
    mid = plan(conn, str(target), DEFAULT_TEMPLATE, Options([ra, rb]))
    dest = conn.execute("SELECT dest_rel FROM migration_items WHERE migration_id = ? AND src_rel = ?",
                        (mid, "2024/6-19-2024 Air Show/DSC001.JPG")).fetchone()[0]
    clash = target.joinpath(*dest.split("/")).with_name("DSC001.JPG.xmp")
    clash.parent.mkdir(parents=True, exist_ok=True)
    clash.write_text("<x:xmpmeta something-else='yes'/>", encoding="utf-8")
    _run(conn, mid, tmp)
    assert _states(conn, mid)["2024/6-19-2024 Air Show/DSC001.JPG"] == "failed"
    assert not target.joinpath(*dest.split("/")).exists()          # no orphan copy left behind
    assert (a / "2024/6-19-2024 Air Show/DSC001.JPG").exists()


def test_a_full_target_waits_instead_of_failing_every_file(lib, monkeypatch):
    conn, tmp, a, b, target, ra, rb, ids = lib
    monkeypatch.setattr(execute, "_copy_hashed",
                        lambda *a_, **k: (_ for _ in ()).throw(OSError(errno.ENOSPC, "No space left")))
    mid = plan(conn, str(target), DEFAULT_TEMPLATE, Options([ra, rb]))
    job, out = _run(conn, mid, tmp)
    assert out.state == "waiting" and "full" in out.status
    assert "failed" not in _states(conn, mid).values()


def test_raw_and_jpeg_stay_together_when_the_raw_has_no_date(tmp_path):
    import shutil
    from lunelis import pairs
    from lunelis.catalog.schema import open_catalog
    from lunelis.importers.metadata import extract_pending
    from lunelis.importers.scan import add_root, scan_root
    src, target = tmp_path / "Card", tmp_path / "T"
    jpeg(src / "IMG_5.JPG", "2024:06:19 10:00:00", 3)
    (src / "IMG_5.CR2").write_bytes(b"II*\0" + os.urandom(3000))  # a RAW whose date can't be read
    target.mkdir()
    conn = open_catalog(tmp_path / "c.db")
    r = add_root(conn, src)
    scan_root(conn, r)
    extract_pending(conn)
    conn.execute("UPDATE files SET is_raw = 1 WHERE filename = 'IMG_5.CR2'")
    pairs.rebuild(conn)
    mid = plan(conn, str(target), DEFAULT_TEMPLATE, Options([r], library_layout=True))
    folders = {d.rsplit("/", 1)[0] for (d,) in conn.execute(
        "SELECT dest_rel FROM migration_items WHERE migration_id = ?", (mid,))}
    assert len(folders) == 1 and "Undated" not in folders.pop()
    conn.close()
    shutil.rmtree(tmp_path / "T")


def test_cancelling_a_migration_job_stops_the_migration(lib):
    conn, tmp, a, b, target, ra, rb, ids = lib
    mid = plan(conn, str(target), DEFAULT_TEMPLATE, Options([ra, rb]))
    job = execute.start(conn, mid, backup_dir=tmp / "backups")
    engine.cancel(conn, job)
    assert conn.execute("SELECT state FROM migrations WHERE id = ?", (mid,)).fetchone()[0] == "cancelled"
    (tmp / "Other").mkdir()
    plan(conn, str(tmp / "Other"), DEFAULT_TEMPLATE, Options([ra, rb]))   # a new plan isn't blocked


def test_two_sources_with_one_name_get_their_own_trash_folders(lib, tmp_path):
    from lunelis.importers.scan import add_root
    from lunelis.settings import Settings
    conn, tmp, a, b, target, ra, rb, ids = lib
    x, y = tmp / "D" / "Photos", tmp / "NAS" / "Photos"
    jpeg(x / "1.jpg", "2024:01:01 10:00:00", 11)
    jpeg(y / "1.jpg", "2024:01:02 10:00:00", 12)
    rx, ry = add_root(conn, x), add_root(conn, y)
    from lunelis.importers.scan import scan_root
    scan_root(conn, rx); scan_root(conn, ry)
    Settings(conn).set("lunelis_folder", str(tmp / "Lunelis"))
    mid = plan(conn, str(target), DEFAULT_TEMPLATE, Options([rx, ry]))
    bx = execute.set_aside_base(conn, mid, "original", str(x))
    by = execute.set_aside_base(conn, mid, "original", str(y))
    assert bx != by


def test_an_unreadable_folder_is_reported_not_skipped(tmp_path):
    f = tmp_path / "not-a-folder.txt"
    f.write_text("x")
    bad: list = []
    assert logs.inventory(str(f), unreadable=bad) == [] and bad


def test_a_name_differing_only_in_case_is_found_at_the_target():
    import inspect
    assert "COLLATE NOCASE" in inspect.getsource(execute._migrate_item)


def test_release_keeps_an_original_whose_library_copy_no_longer_checks_out(lib):
    conn, tmp, a, b, target, ra, rb, ids = lib
    mid = plan(conn, str(target), DEFAULT_TEMPLATE, Options([ra], keep_sources=True))
    _run(conn, mid, tmp)
    dest = conn.execute("SELECT dest_rel FROM migration_items WHERE migration_id = ? AND src_rel = ?",
                        (mid, "2024/6-19-2024 Air Show/DSC001.JPG")).fetchone()[0]
    copy = target.joinpath(*dest.split("/"))
    data = bytearray(copy.read_bytes())
    data[-5] ^= 0xFF                                              # the NAS copy went bad since
    copy.write_bytes(bytes(data))
    execute.release(conn, mid)
    assert (a / "2024/6-19-2024 Air Show/DSC001.JPG").exists()      # its original stays
    assert _states(conn, mid)["2024/6-19-2024 Air Show/DSC001.JPG"] == "kept"


def test_a_shared_sidecar_stays_recorded_for_the_second_half_of_a_pair(lib):
    conn, tmp, a, b, target, ra, rb, ids = lib
    folder = a / "2024/6-19-2024 Air Show"
    (folder / "DSC002.xmp").write_text("<x:xmpmeta shared='yes'/>", encoding="utf-8")
    conn.execute("UPDATE files SET sidecar = 'DSC002.xmp' WHERE rel_path LIKE '%DSC002.%' AND root_id = ?", (ra,))
    conn.commit()
    mid = plan(conn, str(target), DEFAULT_TEMPLATE, Options([ra], keep_sources=False))
    _run(conn, mid, tmp)
    sides = [r[0] for r in conn.execute(
        "SELECT sidecar FROM files WHERE root_id NOT IN (?, ?) AND rel_path LIKE '%DSC002.%'", (ra, rb))]
    assert sides == ["DSC002.xmp", "DSC002.xmp"]


def test_a_lightroom_style_sidecar_counts_as_travelled_in_the_report(lib):
    conn, tmp, a, b, target, ra, rb, ids = lib
    (a / "2024/6-19-2024 Air Show/DSC002.xmp").write_text("<x/>", encoding="utf-8")
    mid = plan(conn, str(target), DEFAULT_TEMPLATE, Options([ra], keep_sources=True))
    _run(conn, mid, tmp)
    rep = logs.accounted(conn, mid, write=False)
    row = next(r for r in rep.rows if r[0].endswith("DSC002.xmp"))
    assert row[2] == "Sidecar - travelled with its photo"
