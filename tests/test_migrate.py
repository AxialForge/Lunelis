"""Phase 2, step 2: migration / consolidation - plan (dry run), copy -> verify
-> repoint -> quarantine the original, duplicates, damaged copies, review
mode, resuming. Everything happens in temp folders."""
import os
import shutil

import piexif
import pytest
from PIL import Image

from lunelis.catalog.ratings import set_ratings
from lunelis.catalog.schema import open_catalog
from lunelis.dupes.quarantine import QUARANTINE_DIR
from lunelis.events import model as events
from lunelis.importers.metadata import extract_pending
from lunelis.importers.scan import add_root, scan_root
from lunelis.importing.templates import DEFAULT_TEMPLATE
from lunelis.jobs import engine
from lunelis.migrate import execute
from lunelis.migrate.plan import Options, PlanError, plan, summary


def jpeg(path, when=None, seed=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.effect_noise((48, 32), 30 + seed).convert("RGB")
    if when:
        exif = piexif.dump({"0th": {piexif.ImageIFD.Model: b"ILCE-7RM5"},
                            "Exif": {piexif.ExifIFD.DateTimeOriginal: when.encode()}})
        img.save(path, "JPEG", exif=exif, quality=90)
    else:
        img.save(path, "JPEG", quality=90)


def all_files(folder):
    return sorted(str(p.relative_to(folder)).replace("\\", "/") for p in folder.rglob("*") if p.is_file())


@pytest.fixture
def lib(tmp_path):
    a, b, target = tmp_path / "PoolA", tmp_path / "PoolB", tmp_path / "NewDrive"
    show = "2024/6-19-2024 Air Show"
    jpeg(a / show / "DSC001.JPG", "2024:06:19 10:00:00", 1)
    (a / show / "DSC001.JPG.xmp").write_text("<x:xmpmeta darktable='yes'/>", encoding="utf-8")
    jpeg(a / show / "DSC002.JPG", "2024:06:19 11:00:00", 2)
    shutil.copy2(a / show / "DSC002.JPG", a / show / "DSC002.ARW")        # a RAW+JPEG pair (same stem)
    jpeg(a / "misc/scan.jpg", None, 3)                                       # no date
    (b / "2024 Album/6-19-2024 Air Show").mkdir(parents=True)
    shutil.copy2(a / show / "DSC001.JPG", b / "2024 Album/6-19-2024 Air Show/DSC001.JPG")   # identical copy
    jpeg(b / "phone/DSC002.JPG", "2024:06:19 12:00:00", 4)                  # a DIFFERENT photo, same name
    target.mkdir()
    conn = open_catalog(tmp_path / "cat.db")
    ra, rb = add_root(conn, a), add_root(conn, b)
    for r in (ra, rb):
        scan_root(conn, r)
    extract_pending(conn)
    # Find and verify duplicates the normal way (jobs), as the user would first.
    for kind in ("duplicates", "verify"):
        engine.run_job(conn, engine.create_job(conn, kind, kind, [(ra, None), (rb, None)]))
    ids = {rel: i for i, rel in conn.execute("SELECT id, rel_path FROM files")}
    yield conn, tmp_path, a, b, target, ra, rb, ids
    conn.close()


def _run(conn, mid, tmp_path, **kw):
    job = execute.start(conn, mid, backup_dir=tmp_path / "backups")
    out = engine.run_job(conn, job, **kw)
    return job, out


def test_plan_is_a_dry_run_that_explains_everything(lib):
    conn, tmp, a, b, target, ra, rb, ids = lib
    before = all_files(tmp / "PoolA") + all_files(tmp / "PoolB")
    mid = plan(conn, str(target), DEFAULT_TEMPLATE, Options([ra, rb]))
    s = summary(conn, mid)
    assert (s.move_files, s.dup_files, s.undated, s.sibling_folders) == (5, 1, 1, 1)
    assert s.enough_space and s.state == "planned"
    assert {f for f, _, _ in s.folders} == {"2024", "Undated"}
    assert all_files(tmp / "PoolA") + all_files(tmp / "PoolB") == before     # nothing touched
    assert all_files(target) == []
    items = dict(conn.execute("SELECT src_rel, dest_rel FROM migration_items WHERE migration_id = ?", (mid,)))
    # The pair stays together; the other DSC002 goes to a sibling folder; names never change.
    assert items["2024/6-19-2024 Air Show/DSC002.ARW"] == "2024/6-19-2024/DSC002.ARW"
    assert items["2024/6-19-2024 Air Show/DSC002.JPG"] == "2024/6-19-2024/DSC002.JPG"
    assert items["phone/DSC002.JPG"] == "2024/6-19-2024 (2)/DSC002.JPG"
    assert items["misc/scan.jpg"] == "Undated/scan.jpg"


def test_targets_that_would_overlap_are_refused(lib):
    conn, tmp, a, b, target, ra, rb, ids = lib
    for bad in (str(a / "2024"), str(tmp), str(tmp / "missing")):
        with pytest.raises(PlanError):
            plan(conn, bad, DEFAULT_TEMPLATE, Options([ra]))


def test_migration_moves_verified_copies_keeps_catalog_entries_and_quarantines(lib):
    conn, tmp, a, b, target, ra, rb, ids = lib
    keeper = ids["2024/6-19-2024 Air Show/DSC001.JPG"]
    dup = next(i for rel, i in ids.items() if rel.startswith("2024 Album/"))
    set_ratings(conn, [dup], stars=5)                       # rated on the copy that WON'T move
    ev = events.create(conn, "Air Show", [ids["2024/6-19-2024 Air Show/DSC002.JPG"]])
    original = (a / "2024/6-19-2024 Air Show/DSC001.JPG").read_bytes()

    mid = plan(conn, str(target), DEFAULT_TEMPLATE, Options([ra, rb], keep_sources=False))
    job, out = _run(conn, mid, tmp)
    assert out.state == "done"
    # The event's pair goes to a folder named after it (both halves); the other
    # pool's different DSC002 then has its own name free in the plain day folder.
    assert all_files(target) == sorted([
        "2024/6-19-2024 Air Show/DSC002.ARW", "2024/6-19-2024 Air Show/DSC002.JPG",
        "2024/6-19-2024/DSC001.JPG", "2024/6-19-2024/DSC001.JPG.xmp", "2024/6-19-2024/DSC002.JPG",
        "Undated/scan.jpg"])
    assert (target / "2024/6-19-2024/DSC001.JPG").read_bytes() == original
    # Same catalog entry, now at the target; the other copy's rating came along.
    troot = conn.execute("SELECT target_root_id FROM migrations WHERE id = ?", (mid,)).fetchone()[0]
    assert tuple(conn.execute("SELECT root_id, rel_path, sidecar FROM files WHERE id = ?", (keeper,)).fetchone()) == \
        (troot, "2024/6-19-2024/DSC001.JPG", "DSC001.JPG.xmp")
    assert conn.execute("SELECT stars FROM ratings WHERE file_id = ?", (keeper,)).fetchone()[0] == 5
    assert events.get(conn, ev).photos == 1
    # Originals: in quarantine on their own drive, nothing deleted.
    assert all_files(a) == sorted(f"{QUARANTINE_DIR}/migration-{mid}/{r}" for r in [
        "2024/6-19-2024 Air Show/DSC001.JPG", "2024/6-19-2024 Air Show/DSC001.JPG.xmp",
        "2024/6-19-2024 Air Show/DSC002.ARW", "2024/6-19-2024 Air Show/DSC002.JPG", "misc/scan.jpg"])
    assert all_files(b) == sorted(f"{QUARANTINE_DIR}/migration-{mid}/{r}" for r in [
        "2024 Album/6-19-2024 Air Show/DSC001.JPG", "phone/DSC002.JPG"])
    assert conn.execute("SELECT quarantined_at IS NOT NULL FROM files WHERE id = ?", (dup,)).fetchone()[0]
    assert list((tmp / "backups").glob("catalog-*-before-migration.zip"))
    # Rescans find nothing new and nothing missing anywhere.
    for r in (ra, rb, troot):
        res = scan_root(conn, r)
        assert (res.added, res.missing) == (0, 0)


def test_review_mode_keeps_originals_until_released(lib):
    conn, tmp, a, b, target, ra, rb, ids = lib
    before_a = all_files(a)
    mid = plan(conn, str(target), DEFAULT_TEMPLATE, Options([ra, rb], keep_sources=True))
    _run(conn, mid, tmp)
    assert all_files(a) == before_a                           # originals untouched
    assert len(all_files(target)) == 6
    # Their catalog entries live in the target now: a rescan mustn't catalog the originals again.
    assert scan_root(conn, ra).added == 0
    assert conn.execute("SELECT COUNT(*) FROM files WHERE root_id = ?", (ra,)).fetchone()[0] == 0
    n = execute.release(conn, mid, backup_dir=tmp / "backups")
    assert n == 6                                             # 5 moved originals + the skipped duplicate
    assert all(p.startswith(QUARANTINE_DIR) for p in all_files(a))


def test_an_interrupted_migration_resumes_without_duplicating(lib):
    conn, tmp, a, b, target, ra, rb, ids = lib
    mid = plan(conn, str(target), DEFAULT_TEMPLATE, Options([ra, rb], keep_sources=False))
    calls = {"n": 0}

    def stop_after_two():
        calls["n"] += 1
        return calls["n"] > 4

    job, out = _run(conn, mid, tmp, should_stop=stop_after_two)
    assert out.state == "paused"
    engine.resume(conn, job)
    assert engine.run_job(conn, job).state == "done"
    assert len(all_files(target)) == 6
    assert not [p for p in all_files(target) if ".lunelis-migrating-" in p]
    states = dict(conn.execute("SELECT state, COUNT(*) FROM migration_items WHERE migration_id = ?"
                               " GROUP BY state", (mid,)))
    assert states == {"done": 6}


def test_a_copy_that_doesnt_verify_is_removed_and_the_original_kept(lib, monkeypatch):
    conn, tmp, a, b, target, ra, rb, ids = lib
    mid = plan(conn, str(target), DEFAULT_TEMPLATE, Options([ra], keep_sources=False))
    real = execute._sha256
    monkeypatch.setattr(execute, "_sha256", lambda p, t=None: "bad" if str(target) in str(p) else real(p, t))
    _run(conn, mid, tmp)
    assert all_files(target) == [] or all(p.endswith(".xmp") for p in all_files(target))
    assert all(not p.startswith(QUARANTINE_DIR) for p in all_files(a))   # originals untouched
    assert {s for (s,) in conn.execute("SELECT DISTINCT state FROM migration_items WHERE migration_id = ?"
                                       " AND action = 'move'", (mid,))} == {"failed"}
    assert conn.execute("SELECT COUNT(*) FROM files WHERE root_id = ?", (ra,)).fetchone()[0] == 4


def test_events_name_their_folders_and_damaged_copies_stay_behind(lib):
    conn, tmp, a, b, target, ra, rb, ids = lib
    events.create(conn, "Air Show", [ids["2024/6-19-2024 Air Show/DSC002.JPG"], ids["2024/6-19-2024 Air Show/DSC002.ARW"]])
    # The old pool's copy of DSC001 is damaged; the good one elsewhere moves instead.
    dup = next(i for rel, i in ids.items() if rel.startswith("2024 Album/"))
    conn.execute("INSERT INTO damaged (file_id, problem) VALUES (?, 'corrupt')", (dup,))
    conn.execute("DELETE FROM duplicate_groups")                  # not a verified duplicate any more
    conn.commit()
    mid = plan(conn, str(target), DEFAULT_TEMPLATE, Options([ra, rb]))
    items = {rel: (dest, act) for rel, dest, act in conn.execute(
        "SELECT src_rel, dest_rel, action FROM migration_items WHERE migration_id = ?", (mid,))}
    assert items["2024/6-19-2024 Air Show/DSC002.JPG"][0] == "2024/6-19-2024 Air Show/DSC002.JPG"
    assert items["2024 Album/6-19-2024 Air Show/DSC001.JPG"] == (None, "skip_damaged")


def test_migrate_page_shows_the_plan_and_guards_start(lib):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from lunelis.ui.migrate_view import MigrateView
    conn, tmp, a, b, target, ra, rb, ids = lib
    mid = plan(conn, str(target), DEFAULT_TEMPLATE, Options([ra, rb]))
    view = MigrateView(conn)
    view.bg.wait()                                     # read on a worker
    assert view.migration_id == mid
    assert "Preview - nothing has moved yet" in view.message.text()
    assert view.start_b.isEnabled() and not view.discard_b.isHidden()
    conn.execute("UPDATE migration_items SET size = 10 * 1000 * 1000 * 1000 * 1000 WHERE migration_id = ?", (mid,))
    conn.commit()                                      # the page reads on its own connection
    view._show()
    assert not view.start_b.isEnabled()                            # not while newer figures load
    view.bg.wait()
    assert not view.start_b.isEnabled()                            # not enough space: can't start
    assert "Not enough space" in view.message.text()


def test_unverified_copies_are_compared_at_copy_time_not_copied_twice(lib):
    conn, tmp, a, b, target, ra, rb, ids = lib
    conn.execute("DELETE FROM duplicate_groups")                  # the user never verified duplicates
    conn.commit()
    dup = next(i for rel, i in ids.items() if rel.startswith("2024 Album/"))
    set_ratings(conn, [dup], label="Red")
    mid = plan(conn, str(target), DEFAULT_TEMPLATE, Options([ra, rb], keep_sources=False))
    s = summary(conn, mid)
    assert (s.dup_files, s.probable_copies, s.sibling_folders) == (0, 1, 1)
    _run(conn, mid, tmp)
    assert sum(1 for p in all_files(target) if p.endswith("DSC001.JPG")) == 1      # copied once
    keeper = ids["2024/6-19-2024 Air Show/DSC001.JPG"]
    assert conn.execute("SELECT color_label FROM ratings WHERE file_id = ?", (keeper,)).fetchone()[0] == "Red"
    assert conn.execute("SELECT quarantined_at IS NOT NULL FROM files WHERE id = ?", (dup,)).fetchone()[0]
    assert all(p.startswith(QUARANTINE_DIR) for p in all_files(b))


def test_moving_only_the_archive_to_another_drive(lib):
    """Library > Move the Archive to a drive: just the archived photos move
    (verified, original to quarantine) and they stay archived."""
    from lunelis.albums import archive
    conn, tmp, a, b, target, ra, rb, ids = lib
    scan = ids["misc/scan.jpg"]
    with pytest.raises(PlanError):                           # nothing archived yet
        plan(conn, str(target), DEFAULT_TEMPLATE, Options([ra, rb], keep_sources=False, only_archived=True))
    archive.archive(conn, [scan])
    mid = plan(conn, str(target), DEFAULT_TEMPLATE, Options([ra, rb], keep_sources=False, only_archived=True))
    job, out = _run(conn, mid, tmp)
    assert out.state == "done"
    assert all_files(target) == ["Undated/scan.jpg"]
    assert (a / "2024/6-19-2024 Air Show/DSC001.JPG").exists()           # everything else stayed put
    troot = conn.execute("SELECT target_root_id FROM migrations WHERE id = ?", (mid,)).fetchone()[0]
    row = conn.execute("SELECT root_id, rel_path, archived_at IS NOT NULL FROM files WHERE id = ?", (scan,)).fetchone()
    assert tuple(row) == (troot, "Undated/scan.jpg", 1)                  # same entry, still archived
    assert all_files(a) == sorted([
        "2024/6-19-2024 Air Show/DSC001.JPG", "2024/6-19-2024 Air Show/DSC001.JPG.xmp",
        "2024/6-19-2024 Air Show/DSC002.ARW", "2024/6-19-2024 Air Show/DSC002.JPG",
        f"{QUARANTINE_DIR}/migration-{mid}/misc/scan.jpg"])


def test_a_damaged_only_copy_goes_to_the_lunelis_folders_damaged(lib):
    # 0.51: kept (it's the only copy) but out of the Library.
    from lunelis.settings import Settings
    conn, tmp, a, b, target, ra, rb, ids = lib
    conn.execute("INSERT INTO damaged (file_id, problem) VALUES (?, 'corrupt')", (ids["misc/scan.jpg"],))
    conn.commit()
    mid = plan(conn, str(target), DEFAULT_TEMPLATE, Options([ra, rb]))
    dest, act = conn.execute("SELECT dest_rel, action FROM migration_items WHERE migration_id = ? AND src_rel = ?",
                             (mid, "misc/scan.jpg")).fetchone()
    assert (dest, act) == ("Lunelis/Damaged/misc/scan.jpg", "move")
    Settings(conn).set("lunelis_folder", str(target / "Lunelis stuff"))      # a Lunelis folder inside the target
    conn.execute("DELETE FROM migration_items"); conn.execute("DELETE FROM migrations"); conn.commit()
    mid = plan(conn, str(target), DEFAULT_TEMPLATE, Options([ra, rb]))
    assert conn.execute("SELECT dest_rel FROM migration_items WHERE migration_id = ? AND src_rel = ?",
                        (mid, "misc/scan.jpg")).fetchone()[0] == "Lunelis stuff/Damaged/misc/scan.jpg"
