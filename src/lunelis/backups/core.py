"""
Backups (Phase 2, step 3): the library, mirrored to a USB drive, stick or
network folder - incremental, verified, and nothing ever deleted.

Layout of a backup folder (browsable in Explorer without Lunelis):

    <backup>\\<root key>\\<the source's own folders and files>   the mirror
    <backup>\\_Lunelis\\backup.json                              what this is
    <backup>\\_Lunelis\\catalog\\catalog-*.zip                    catalog snapshots
    <backup>\\_Lunelis\\sidecars\\...                             central sidecar store
    <backup>\\_Lunelis\\previous-versions\\<time>\\...             a file's earlier copy,
                                                                 kept when it changed

Rules:
- Incremental: a file is copied when the backup has no copy, or the library
  file's size/modified time changed since. A file moved or migrated inside
  the library is RENAMED inside the backup (rows are keyed by catalog entry),
  not copied again.
- Verified: hashed while it's read, the backup copy re-read and compared, the
  hash stored. "Verify" later re-reads every copy against that hash (bit rot).
  The first backup of a file also fills its integrity baseline
  (files.content_hash) for free.
- Never deleted: files removed from the library stay in the backup; a changed
  file's old copy moves to previous-versions.
- Removable drives are recognised by volume serial - a new drive letter is fine.
- Restore: missing or damaged library files back to where they were (a damaged
  file is quarantined first, never overwritten), or everything into a new folder.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from lunelis.dupes.detect import FolderResult, plan_folders
from lunelis.dupes.hashing import SourceOffline, Throttle, is_network_error
from lunelis.dupes.quarantine import QUARANTINE_DIR
from lunelis.importing.ingest import volume_info
from lunelis.migrate.execute import _copy_hashed, _sha256, _unlink
from lunelis.xmp.sync import root_key

META_DIR = "_Lunelis"
FREE_MARGIN = 512 * 1024 * 1024      # never fill the backup drive to the last byte
SNAPSHOTS_KEPT = 5
LIVE = "f.missing_since IS NULL AND f.excluded = 0 AND f.quarantined_at IS NULL"
_log = logging.getLogger(__name__)


class BackupError(ValueError):
    pass


class BackupFull(Exception):
    pass


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _abs(base: str, rel: str) -> str:
    return os.path.join(base, *rel.split("/"))


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(p)).rstrip("\\/")


def _inside(a: str, b: str) -> bool:
    a, b = _norm(a), _norm(b)
    return a == b or a.startswith(b + os.sep)


# --- sets -------------------------------------------------------------------------------

@dataclass
class BackupSet:
    id: int
    name: str
    dest_path: str
    volume_serial: str | None
    volume_label: str | None
    sources: list[int]
    options: dict
    status: str | None
    last_run_at: str | None
    last_verified_at: str | None


def get_set(conn: sqlite3.Connection, set_id: int) -> BackupSet:
    r = conn.execute("SELECT id, name, dest_path, volume_serial, volume_label, sources, options, status,"
                     " last_run_at, last_verified_at FROM backup_sets WHERE id = ?", (set_id,)).fetchone()
    if r is None:
        raise BackupError(f"no backup set {set_id}")
    return BackupSet(r[0], r[1], r[2], r[3], r[4], json.loads(r[5]), json.loads(r[6]), r[7], r[8], r[9])


def all_sets(conn: sqlite3.Connection) -> list[BackupSet]:
    return [get_set(conn, r[0]) for r in conn.execute("SELECT id FROM backup_sets ORDER BY id")]


def _is_local_drive(path: str) -> bool:
    return bool(os.path.splitdrive(os.path.abspath(path))[0]) and not path.startswith(("\\\\", "//"))


def create_set(conn: sqlite3.Connection, name: str, dest: str, sources: list[int],
               options: dict | None = None) -> int:
    """Record a new backup set. The folder must exist and sit outside every
    source (a backup inside the library would be cataloged as photos)."""
    dest = os.path.normpath(dest)
    if not sources:
        raise BackupError("Choose at least one source to back up.")
    if not os.path.isdir(dest):
        raise BackupError(f"{dest} can't be reached - is the drive plugged in?")
    for rid, path in conn.execute("SELECT id, path FROM roots"):
        if _inside(dest, path) or _inside(path, dest):
            raise BackupError(f"The backup folder overlaps the source {path} - choose a folder outside "
                              "your library (a USB drive or a separate share).")
    from lunelis import paths
    if _inside(dest, str(paths.DATA_DIR)):
        raise BackupError("The backup can't go inside Lunelis's own data folder.")
    serial, label = volume_info(dest) if _is_local_drive(dest) else (None, None)
    sid = conn.execute(
        "INSERT INTO backup_sets (name, dest_path, volume_serial, volume_label, sources, options)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (name.strip() or os.path.basename(dest) or dest, dest, serial, label, json.dumps(sorted(sources)),
         json.dumps({"auto_on_connect": True, **(options or {})}))).lastrowid
    conn.commit()
    _write_manifest(conn, sid, dest)
    return sid


def forget_set(conn: sqlite3.Connection, set_id: int) -> None:
    """Stop using a backup set. The backup's files on the drive are NOT touched."""
    conn.execute("DELETE FROM backup_sets WHERE id = ?", (set_id,))
    conn.commit()


def _drive_letters() -> list[str]:
    if sys.platform != "win32":
        return []
    import ctypes
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    return [f"{chr(65 + i)}:\\" for i in range(26) if mask & (1 << i)
            and ctypes.windll.kernel32.GetDriveTypeW(f"{chr(65 + i)}:\\") in (2, 3)]   # removable, fixed


def resolve_dest(conn: sqlite3.Connection, set_id: int) -> str | None:
    """Where the backup is right now, or None if its drive isn't connected.
    A removable drive that came back under another letter is found by serial."""
    s = get_set(conn, set_id)
    if not s.volume_serial:
        return s.dest_path if os.path.isdir(s.dest_path) else None
    if os.path.isdir(s.dest_path) and volume_info(s.dest_path)[0] == s.volume_serial:
        return s.dest_path
    tail = os.path.splitdrive(s.dest_path)[1]
    for drive in _drive_letters():
        if volume_info(drive)[0] == s.volume_serial:
            moved = os.path.join(drive, tail.lstrip("\\/"))
            if os.path.isdir(moved):
                return moved
    return None


def sets_on_drive(conn: sqlite3.Connection, drive: str) -> list[int]:
    """Backup sets that live on this (just connected) drive."""
    serial = volume_info(drive)[0]
    if not serial:
        return []
    return [r[0] for r in conn.execute("SELECT id FROM backup_sets WHERE volume_serial = ?", (serial,))]


def connected_drives() -> dict[str, str]:
    """{volume serial: drive} for every local/removable drive right now."""
    out = {}
    for d in _drive_letters():
        serial = volume_info(d)[0]
        if serial:
            out[serial] = d
    return out


# --- what's pending ----------------------------------------------------------------------------

@dataclass
class Status:
    set_id: int
    connected: bool
    dest: str | None
    files: int = 0                # live library files in the set's sources
    bytes: int = 0
    backed_up: int = 0            # of those, with an up-to-date copy
    pending_files: int = 0
    pending_bytes: int = 0
    problems: int = 0             # copies a verify pass found bad
    free_bytes: int | None = None


def status(conn: sqlite3.Connection, set_id: int) -> Status:
    s = get_set(conn, set_id)
    dest = resolve_dest(conn, set_id)
    st = Status(set_id, dest is not None, dest)
    q = ",".join("?" * len(s.sources))
    for n, size, current in conn.execute(
            f"SELECT COUNT(*), COALESCE(SUM(f.size_bytes), 0),"
            f" (b.file_id IS NOT NULL AND b.size = f.size_bytes AND b.mtime = f.mtime) AS cur"
            f" FROM files f JOIN roots r ON r.id = f.root_id"
            f" LEFT JOIN backup_files b ON b.set_id = ? AND b.file_id = f.id"
            f" WHERE {LIVE} AND r.enabled = 1 AND f.root_id IN ({q}) GROUP BY cur",
            [set_id, *s.sources]):
        st.files += n
        st.bytes += size
        if current:
            st.backed_up += n
        else:
            st.pending_files += n
            st.pending_bytes += size
    st.problems = conn.execute("SELECT COUNT(*) FROM backup_files WHERE set_id = ? AND problem IS NOT NULL",
                               (set_id,)).fetchone()[0]
    if dest:
        try:
            st.free_bytes = shutil.disk_usage(dest).free
        except OSError:
            pass
    return st


# --- running -------------------------------------------------------------------------------------

def start(conn: sqlite3.Connection, set_id: int, *, verify: bool = False) -> int:
    """Queue a backup (or a verify pass) of the set as a background job."""
    from lunelis.jobs import engine
    s = get_set(conn, set_id)
    busy = conn.execute("SELECT id FROM jobs WHERE kind IN ('backup', 'backup_verify')"
                        " AND state IN ('queued', 'running', 'waiting', 'paused')"
                        " AND json_extract(options, '$.set_id') = ?", (set_id,)).fetchone()
    if busy:
        return busy[0]
    kind = "backup_verify" if verify else "backup"
    folders = plan_folders(conn, [(rid, None) for rid in s.sources])
    opts = {"set_id": set_id, "schedule": s.options.get("schedule", {"mode": "now"}),
            "mb_per_s": s.options.get("mb_per_s")}
    title = f"{'Verify backup' if verify else 'Back up'}: {s.name}"
    return engine.create_job(conn, kind, title, scope=[], options=opts, folders=folders)


def _set_status(conn, set_id: int, text: str) -> None:
    conn.execute("UPDATE backup_sets SET status = ? WHERE id = ?", (text, set_id))
    conn.commit()


def _dest_or_wait(conn, set_id: int) -> str:
    dest = resolve_dest(conn, set_id)
    if dest is None:
        _set_status(conn, set_id, "Waiting for the backup drive to be connected")
        raise SourceOffline("backup drive not connected")
    return dest


def backup_folder(conn: sqlite3.Connection, root_id: int, folder: str, *, throttle: Throttle,
                  should_cancel, workers: int, options: dict) -> FolderResult:
    """Job kind "backup": bring one library folder's backup up to date."""
    set_id = options["set_id"]
    result = FolderResult()
    dest = _dest_or_wait(conn, set_id)
    root = conn.execute("SELECT path FROM roots WHERE id = ?", (root_id,)).fetchone()[0]
    if not os.path.isdir(root):
        raise SourceOffline(root)
    rkey = root_key(root_id, root)
    rows = [r for r in conn.execute(
        f"SELECT f.id, f.rel_path, f.size_bytes, f.mtime, f.sidecar, f.content_hash FROM files f"
        f" WHERE f.root_id = ? AND {LIVE}", (root_id,))
        if (r[1].rsplit("/", 1)[0] if "/" in r[1] else "") == folder]
    have = {r[0]: r[1:] for r in conn.execute(
        f"SELECT file_id, rel, size, mtime, problem FROM backup_files WHERE set_id = ? AND file_id IN "
        f"({','.join('?' * len(rows)) or 'NULL'})", [set_id, *[r[0] for r in rows]])}
    for fid, rel, size, mtime, sidecar, content_hash in rows:
        if should_cancel and should_cancel():
            result.cancelled = True
            return result
        target_rel = f"{rkey}/{rel}"
        prev = have.get(fid)
        try:
            if prev and prev[1] == size and prev[2] == mtime and not prev[3]:   # a flagged copy is redone
                if prev[0] == target_rel:
                    # The photo is as it was, but its sidecar may hold newer
                    # ratings or edits: that goes to the backup too (0.53).
                    _sync_sidecar(_abs(root, rel), _abs(dest, target_rel), sidecar)
                    continue
                if _rename_in_backup(dest, prev[0], target_rel, sidecar):   # moved in the library: here too
                    conn.execute("UPDATE backup_files SET rel = ? WHERE set_id = ? AND file_id = ?",
                                 (target_rel, set_id, fid))
                    conn.commit()
                    continue
                prev = None                                       # its old copy is gone: copy it afresh
            if shutil.disk_usage(dest).free < size + FREE_MARGIN:
                _set_status(conn, set_id, "The backup drive is full - free some space or choose fewer sources")
                result.cancelled = True
                return result
            if prev:                                              # changed: keep the old copy
                _keep_previous(dest, prev[0])
            src, dst = _abs(root, rel), _abs(dest, target_rel)
            if os.path.exists(dst) and not (prev and prev[0] == target_rel):
                _keep_previous(dest, target_rel)                  # a file this set doesn't know: keep it
            digest = _copy_hashed(src, dst, throttle, should_cancel)
            if digest is None:
                result.cancelled = True
                return result
            if _sha256(dst, throttle) != digest:
                _unlink(dst)
                result.errors.append(f"{src}: the backup copy didn't verify")
                _log.warning("Backup: %s - the copy didn't verify", src)
                continue
            _sync_sidecar(src, dst, sidecar)
            conn.execute(
                "INSERT INTO backup_files (set_id, file_id, rel, size, mtime, sha256, verified_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(set_id, file_id) DO UPDATE SET rel = excluded.rel,"
                " size = excluded.size, mtime = excluded.mtime, sha256 = excluded.sha256,"
                " backed_up_at = datetime('now'), verified_at = excluded.verified_at, problem = NULL",
                (set_id, fid, target_rel, size, mtime, digest, _now()))
            if not content_hash:                                  # a free integrity baseline
                conn.execute("UPDATE files SET content_hash = ? WHERE id = ?", (digest, fid))
            conn.commit()
            result.hashed += 1
            result.bytes_read += size * 2
        except OSError as e:
            if is_network_error(e) or not os.path.isdir(root) or resolve_dest(conn, set_id) is None:
                raise SourceOffline(str(e)) from e
            result.errors.append(f"{rel}: {e}")
            _log.warning("Backup: %s wasn't copied - %s", rel, e)
    return result


def _sync_sidecar(src: str, dst: str, sidecar: str | None) -> None:
    """Copy a photo's sidecar beside its backup copy when it's new or changed."""
    if not sidecar:
        return
    s, d = os.path.join(os.path.dirname(src), sidecar), os.path.join(os.path.dirname(dst), sidecar)
    try:
        st = os.stat(s)
    except OSError:
        return
    try:
        have = os.stat(d)
        if have.st_size == st.st_size and abs(have.st_mtime - st.st_mtime) < 2:
            return
    except OSError:
        pass
    os.makedirs(os.path.dirname(d), exist_ok=True)
    tmp = d + ".lunelis-tmp"
    shutil.copy2(s, tmp)
    os.replace(tmp, d)


def not_backed_up(conn: sqlite3.Connection, set_id: int, job_id: int) -> int:
    """How many photos of this run's sources have no good, current copy in the
    set - what "Up to date" has to be honest about (0.53)."""
    return conn.execute(
        f"SELECT COUNT(*) FROM files f WHERE {LIVE}"
        " AND f.root_id IN (SELECT DISTINCT root_id FROM job_folders WHERE job_id = ?)"
        " AND NOT EXISTS (SELECT 1 FROM backup_files b WHERE b.set_id = ? AND b.file_id = f.id"
        "   AND b.size = f.size_bytes AND b.mtime = f.mtime AND b.problem IS NULL)",
        (job_id, set_id)).fetchone()[0]


def _rename_in_backup(dest: str, old_rel: str, new_rel: str, sidecar: str | None) -> bool:
    """Move a backed-up copy to its file's new place. False when there's no
    copy to move (the caller then copies afresh); a file already at the new
    place that the set doesn't know about is kept in previous-versions."""
    old, new = _abs(dest, old_rel), _abs(dest, new_rel)
    if not os.path.exists(old):
        return False
    if os.path.exists(new):
        _keep_previous(dest, new_rel)
    os.makedirs(os.path.dirname(new), exist_ok=True)
    os.replace(old, new)
    if sidecar and os.path.exists(os.path.join(os.path.dirname(old), sidecar)):
        os.replace(os.path.join(os.path.dirname(old), sidecar), os.path.join(os.path.dirname(new), sidecar))
    return True


def _keep_previous(dest: str, rel: str) -> None:
    old = _abs(dest, rel)
    if os.path.exists(old):
        keep = _abs(dest, f"{META_DIR}/previous-versions/{datetime.now():%Y%m%d-%H%M%S}/{rel}")
        os.makedirs(os.path.dirname(keep), exist_ok=True)
        os.replace(old, keep)


def verify_folder(conn: sqlite3.Connection, root_id: int, folder: str, *, throttle: Throttle,
                  should_cancel, workers: int, options: dict) -> FolderResult:
    """Job kind "backup_verify": re-read this folder's backup copies against
    their stored hashes."""
    set_id = options["set_id"]
    result = FolderResult()
    dest = _dest_or_wait(conn, set_id)
    rows = [r for r in conn.execute(
        "SELECT b.file_id, b.rel, b.sha256, f.rel_path FROM backup_files b JOIN files f ON f.id = b.file_id"
        " WHERE b.set_id = ? AND f.root_id = ?", (set_id, root_id))
        if (r[3].rsplit("/", 1)[0] if "/" in r[3] else "") == folder]
    for fid, rel, sha, _ in rows:
        if should_cancel and should_cancel():
            result.cancelled = True
            return result
        path = _abs(dest, rel)
        try:
            problem = None if os.path.exists(path) and _sha256(path, throttle) == sha else (
                "missing from the backup" if not os.path.exists(path) else "the backup copy has changed (bit rot?)")
        except OSError as e:
            if resolve_dest(conn, set_id) is None:
                raise SourceOffline(str(e)) from e
            problem = f"unreadable: {e}"
        conn.execute("UPDATE backup_files SET verified_at = ?, problem = ? WHERE set_id = ? AND file_id = ?",
                     (_now(), problem, set_id, fid))
        conn.commit()
        result.hashed += 1
    return result


def _write_manifest(conn, set_id: int, dest: str) -> None:
    meta = os.path.join(dest, META_DIR)
    os.makedirs(meta, exist_ok=True)
    s = get_set(conn, set_id)
    roots = dict(conn.execute("SELECT id, path FROM roots"))
    from lunelis.paths import write_json_atomic
    write_json_atomic(Path(meta) / "backup.json",
                      {"app": "Lunelis", "set_id": set_id, "name": s.name, "updated": _now(),
                       "sources": {root_key(r, roots[r]): roots[r] for r in s.sources if r in roots}})


def _mirror_tree(src: Path, dst: Path) -> None:
    """Copy new/changed files of a small tree (the sidecar store) - never deletes."""
    if not src.is_dir():
        return
    for p in src.rglob("*"):
        if p.is_file():
            t = dst / p.relative_to(src)
            if not t.exists() or t.stat().st_size != p.stat().st_size or t.stat().st_mtime < p.stat().st_mtime:
                t.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, t)


def finish(conn: sqlite3.Connection, job_id: int) -> None:
    """After the last folder: the catalog snapshot, central sidecars and the
    manifest go to the backup too, so a new PC can be restored from it alone."""
    kind, options = conn.execute("SELECT kind, options FROM jobs WHERE id = ?", (job_id,)).fetchone()
    set_id = json.loads(options)["set_id"]
    dest = resolve_dest(conn, set_id)
    if dest is None:
        return
    if kind == "backup_verify":
        n = conn.execute("SELECT COUNT(*) FROM backup_files WHERE set_id = ? AND problem IS NOT NULL",
                         (set_id,)).fetchone()[0]
        conn.execute("UPDATE backup_sets SET last_verified_at = ?, status = ? WHERE id = ?",
                     (_now(), f"Verified - {n:,} copies need attention" if n else "Verified - every copy is good",
                      set_id))
        conn.commit()
        return
    from lunelis import paths
    from lunelis.catalog.backup import snapshot
    from lunelis.settings import Settings
    snapshot(conn, Path(dest) / META_DIR / "catalog", "backup", keep=SNAPSHOTS_KEPT)
    store = Settings(conn).get("sidecar_store_dir") or paths.SIDECAR_STORE
    _mirror_tree(Path(store), Path(dest) / META_DIR / "sidecars")
    _write_manifest(conn, set_id, dest)
    missing = not_backed_up(conn, set_id, job_id)
    status = "Up to date" if not missing else (
        f"Finished, but {missing:,} photo{'s' if missing != 1 else ''} couldn't be copied - "
        "Help > Open the log folder says which; run the backup again")
    conn.execute("UPDATE backup_sets SET last_run_at = ?, status = ? WHERE id = ?", (_now(), status, set_id))
    conn.commit()


# --- restoring --------------------------------------------------------------------------------------

@dataclass
class RestoreResult:
    restored: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    set_aside: list[str] = field(default_factory=list)    # damaged files moved out of the way, kept
    set_aside_dir: str | None = None


def restorable(conn: sqlite3.Connection, set_id: int) -> list[int]:
    """Library files this backup can bring back: missing, or damaged, with a
    copy in the backup that the last verify didn't flag."""
    return [r[0] for r in conn.execute(
        "SELECT f.id FROM backup_files b JOIN files f ON f.id = b.file_id"
        " WHERE b.set_id = ? AND b.problem IS NULL AND f.quarantined_at IS NULL"
        " AND (f.missing_since IS NOT NULL"
        " OR EXISTS (SELECT 1 FROM damaged d WHERE d.file_id = f.id AND d.dismissed = 0))",
        (set_id,))]


def restore_files(conn: sqlite3.Connection, set_id: int, file_ids: list[int] | None = None, *,
                  to_folder: str | None = None, should_cancel=None, on_progress=None) -> RestoreResult:
    """Copy files back from the backup, each verified against the hash taken
    when it was backed up.

    to_folder None: to their places in the library (missing/damaged files only;
    a damaged file is quarantined first, a healthy one is never overwritten).
    to_folder set: everything asked for, mirrored into that folder."""
    res = RestoreResult()
    dest = resolve_dest(conn, set_id)
    if dest is None:
        raise BackupError("The backup drive isn't connected.")
    if file_ids is None:
        file_ids = restorable(conn, set_id) if to_folder is None else \
            [r[0] for r in conn.execute("SELECT file_id FROM backup_files WHERE set_id = ?", (set_id,))]
    throttle = Throttle(None)
    stamp = f"restore-{datetime.now():%Y%m%d-%H%M%S}"
    for n, fid in enumerate(file_ids, 1):
        if should_cancel and should_cancel():
            break
        row = conn.execute(
            "SELECT b.rel, b.sha256, b.size, f.root_id, r.path, f.rel_path,"
            " EXISTS (SELECT 1 FROM damaged d WHERE d.file_id = f.id AND d.dismissed = 0)"
            " FROM backup_files b JOIN files f ON f.id = b.file_id JOIN roots r ON r.id = f.root_id"
            " WHERE b.set_id = ? AND b.file_id = ?", (set_id, fid)).fetchone()
        if row is None:
            res.skipped += 1
            continue
        brel, sha, size, root_id, root, rel, damaged = row
        src = _abs(dest, brel)
        target = _abs(to_folder, brel) if to_folder else _abs(root, rel)
        try:
            if not os.path.exists(src):
                raise OSError("not in the backup")
            if to_folder is None:
                if not os.path.isdir(root):
                    raise OSError(f"{root} isn't reachable")
                if os.path.exists(target):
                    if not damaged:
                        res.skipped += 1                    # healthy: never overwrite
                        continue
                    q = os.path.join(root, QUARANTINE_DIR, stamp, *rel.split("/"))
                    os.makedirs(os.path.dirname(q), exist_ok=True)
                    os.rename(target, q)                    # the damaged one is kept, not replaced in place
                    res.set_aside.append(q)
                    res.set_aside_dir = os.path.join(root, QUARANTINE_DIR, stamp)
                    from lunelis.log import LOG
                    LOG.info("Restore set the damaged %s aside as %s", target, q)
            elif os.path.exists(target):
                res.skipped += 1
                continue
            digest = _copy_hashed(src, target, throttle, should_cancel)
            if digest != sha or _sha256(target) != sha:
                _unlink(target)
                raise OSError("the backup copy doesn't match its recorded hash")
            if to_folder is None:
                st = os.stat(target)
                conn.execute("UPDATE files SET missing_since = NULL, size_bytes = ?, content_hash = ? WHERE id = ?",
                             (st.st_size, sha, fid))
                conn.execute("DELETE FROM damaged WHERE file_id = ?", (fid,))
                conn.commit()
            res.restored += 1
        except OSError as e:
            res.failed += 1
            res.errors.append(f"{rel}: {e}")
        if on_progress:
            on_progress(n, len(file_ids))
    return res


def restore_catalog(conn: sqlite3.Connection, set_id: int, data_dir: Path) -> Path:
    """Ask for the backup's newest catalog snapshot to replace the catalog at
    the next start (see catalog.backup.request_restore)."""
    from lunelis.catalog.backup import list_snapshots, request_restore
    dest = resolve_dest(conn, set_id)
    if dest is None:
        raise BackupError("The backup drive isn't connected.")
    snaps = list_snapshots(Path(dest) / META_DIR / "catalog")
    if not snaps:
        raise BackupError("This backup has no catalog snapshot yet - run it once first.")
    # Copied off the drive now: it may be unplugged before the restart.
    local = data_dir / f"restore-from-backup-{snaps[0].name}"
    shutil.copy2(snaps[0], local)
    request_restore(data_dir, local)
    return snaps[0]
