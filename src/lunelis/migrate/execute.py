"""
Migration / consolidation, part 2: carrying out a plan, one source folder at
a time as a background job (kind "migrate"), so it can be paused, waits for a
sleeping NAS, and resumes after a restart.

For every file that moves:

  1. copy it to the target (via a temp name), hashing what's READ from the source
  2. re-read the copy and compare hashes - a mismatch deletes the copy and fails the file
  3. carry its sidecar (darktable/Lightroom XMP next to it), compared byte for byte
  4. repoint the SAME catalog entry at the new place (ratings, labels, picks,
     events, EXIF and thumbnail stay; the central sidecar moves with it;
     file_moves records it)                                   -> state 'copied'
  5. only then the original: renamed into its own drive's quarantine folder
     (`<source>\\_Lunelis Quarantine\\migration-<id>\\...`)     -> state 'done'
     or, with "keep originals until I review", left alone      -> state 'kept'

A crash between any two steps is safe: before 4 the source is untouched and
the copy is re-verified (or redone) next time; after 4 the item says 'copied'
and step 5 is all that's left. Skipped duplicates and damaged copies follow
their keeper at the end (finish()): their ratings/events are merged into the
copy that moved, and the originals go to quarantine with everything else.
Nothing is ever deleted.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from lunelis.dupes.detect import FolderResult
from lunelis.dupes.hashing import SourceOffline, Throttle, is_network_error
from lunelis.dupes.quarantine import QUARANTINE_DIR
from lunelis.importing.templates import sibling

CHUNK = 4 * 1024 * 1024
TMP_PREFIX = ".lunelis-migrating-"


class MigrationError(Exception):
    pass


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _abs(root: str, rel: str) -> str:
    return os.path.join(root, *rel.split("/"))


def _sha256(path: str, throttle: Throttle | None = None) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            h.update(chunk)
            if throttle:
                throttle.spend(len(chunk))
    return h.hexdigest()


def _copy_hashed(src: str, dst: str, throttle: Throttle, should_cancel) -> str | None:
    """Copy via a temp name, keeping the modified time; the sha256 of what was
    read. None if cancelled part-way (the temp file is removed)."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = os.path.join(os.path.dirname(dst), TMP_PREFIX + os.path.basename(dst))
    h = hashlib.sha256()
    try:
        with open(src, "rb") as fin, open(tmp, "wb") as fout:
            while chunk := fin.read(CHUNK):
                if should_cancel and should_cancel():
                    raise InterruptedError
                h.update(chunk)
                fout.write(chunk)
                throttle.spend(len(chunk))
            fout.flush()
            os.fsync(fout.fileno())
        shutil.copystat(src, tmp)
        os.replace(tmp, dst)
        return h.hexdigest()
    except InterruptedError:
        _unlink(tmp)
        return None
    except BaseException:
        _unlink(tmp)
        raise


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _same_bytes(a: str, b: str) -> bool:
    if os.path.getsize(a) != os.path.getsize(b):
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            x, y = fa.read(CHUNK), fb.read(CHUNK)
            if x != y:
                return False
            if not x:
                return True


def _quarantine_original(root: str, rel: str, sidecar: str | None, migration_id: int) -> str:
    """Rename a source file (and its sidecar) into the quarantine folder on its
    own drive - instant, nothing deleted."""
    src = _abs(root, rel)
    dst = os.path.join(root, QUARANTINE_DIR, f"migration-{migration_id}", *rel.split("/"))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst):
        base, ext = os.path.splitext(dst)
        dst = f"{base} ({datetime.now():%H%M%S}){ext}"
    os.rename(src, dst)
    if sidecar:
        s_src = os.path.join(os.path.dirname(src), sidecar)
        if os.path.exists(s_src):
            os.rename(s_src, os.path.join(os.path.dirname(dst), sidecar))
    return dst


def merge_user_data(conn: sqlite3.Connection, from_id: int, to_id: int) -> None:
    """Before a copy is set aside, anything the user did to it that the kept
    copy lacks moves over: stars, label, flag, event."""
    src = conn.execute("SELECT stars, flag, color_label FROM ratings WHERE file_id = ?", (from_id,)).fetchone()
    if src:
        dst = conn.execute("SELECT stars, flag, color_label FROM ratings WHERE file_id = ?", (to_id,)).fetchone()
        if dst is None:
            conn.execute("INSERT INTO ratings (file_id, stars, flag, color_label, xmp_pending)"
                         " VALUES (?, ?, ?, ?, 1)", (to_id, *src))
        else:
            stars = dst[0] or src[0]
            flag = dst[1] or src[1]
            label = dst[2] or src[2]
            if (stars, flag, label) != tuple(dst):
                conn.execute("UPDATE ratings SET stars = ?, flag = ?, color_label = ?, xmp_pending = 1,"
                             " updated_at = datetime('now') WHERE file_id = ?", (stars, flag, label, to_id))
    conn.execute("INSERT OR IGNORE INTO album_files (album_id, file_id, position)"
                 " SELECT album_id, ?, position FROM album_files WHERE file_id = ?", (to_id, from_id))
    ev = conn.execute("SELECT event_id FROM event_files WHERE file_id = ?", (from_id,)).fetchone()
    if ev and not conn.execute("SELECT 1 FROM event_files WHERE file_id = ?", (to_id,)).fetchone():
        from lunelis.events import model
        model.add_files(conn, ev[0], [to_id], commit=False)


# --- starting ---------------------------------------------------------------------------

def start(conn: sqlite3.Connection, migration_id: int, *, backup_dir: Path | None = None,
          schedule: dict | None = None, mb_per_s: float | None = None) -> int:
    """Turn a planned migration into a job. Snapshots the catalog first and
    adds the target as a source. Returns the job id."""
    from lunelis.importers.scan import add_root
    from lunelis.jobs import engine
    from lunelis.migrate.plan import check_target

    row = conn.execute("SELECT target, state FROM migrations WHERE id = ?", (migration_id,)).fetchone()
    if row is None or row[1] != "planned":
        raise MigrationError("only a planned (not yet started) migration can be started")
    if conn.execute("SELECT 1 FROM migrations WHERE state = 'running'").fetchone():
        raise MigrationError("another migration is running")
    target = row[0]
    check_target(conn, target)
    if backup_dir is not None:
        from lunelis.catalog.backup import snapshot
        snapshot(conn, backup_dir, "before-migration")
    target_root = add_root(conn, target)
    folders: dict[tuple[int, str], int] = {}
    for root_id, rel in conn.execute("SELECT src_root, src_rel FROM migration_items WHERE migration_id = ?",
                                     (migration_id,)):
        key = (root_id, rel.rsplit("/", 1)[0] if "/" in rel else "")
        folders[key] = folders.get(key, 0) + 1
    job_id = engine.create_job(
        conn, "migrate", f"Migrate to {target}", scope=[],
        options={"schedule": schedule or {"mode": "now"}, "mb_per_s": mb_per_s, "migration_id": migration_id},
        folders=[(rid, f, n) for (rid, f), n in sorted(folders.items())])
    conn.execute("UPDATE migrations SET state = 'running', job_id = ?, target_root_id = ? WHERE id = ?",
                 (job_id, target_root, migration_id))
    conn.commit()
    return job_id


def _running(conn: sqlite3.Connection):
    row = conn.execute("SELECT id, target, target_root_id, options FROM migrations WHERE state = 'running'"
                       " ORDER BY id LIMIT 1").fetchone()
    if row is None:
        raise MigrationError("no migration is running")
    return row[0], row[1], row[2], json.loads(row[3])


# --- the job kind ---------------------------------------------------------------------------

def migrate_folder(conn: sqlite3.Connection, root_id: int, folder: str, *, throttle: Throttle,
                   should_cancel, workers: int) -> FolderResult:
    """The jobs-engine kind: every planned item from one source folder."""
    result = FolderResult()
    mid, target, target_root, opts = _running(conn)
    root = conn.execute("SELECT path FROM roots WHERE id = ?", (root_id,)).fetchone()[0]
    if not os.path.isdir(root):
        raise SourceOffline(root)
    if not os.path.isdir(target):
        raise SourceOffline(target)
    items = [r for r in conn.execute(
        "SELECT id, file_id, src_rel, dest_rel, size, action, state FROM migration_items"
        " WHERE migration_id = ? AND src_root = ? AND state IN ('planned', 'copied') ORDER BY src_rel",
        (mid, root_id)) if (r[2].rsplit("/", 1)[0] if "/" in r[2] else "") == folder]
    for item in items:
        if should_cancel and should_cancel():
            result.cancelled = True
            return result
        try:
            result.bytes_read += _migrate_item(conn, mid, target, target_root, opts, root_id, root, item,
                                               throttle, should_cancel)
            result.hashed += 1
        except InterruptedError:
            result.cancelled = True
            return result
        except OSError as e:
            if is_network_error(e) or not os.path.isdir(root) or not os.path.isdir(target):
                raise SourceOffline(str(e)) from e
            conn.execute("UPDATE migration_items SET state = 'failed', error = ? WHERE id = ?",
                         (f"{type(e).__name__}: {e}"[:300], item[0]))
            conn.commit()
            result.errors.append(str(e))
    return result


def _fail(conn, item_id: int, why: str) -> int:
    conn.execute("UPDATE migration_items SET state = 'failed', error = ? WHERE id = ?", (why, item_id))
    conn.commit()
    return 0


def _migrate_item(conn, mid, target, target_root, opts, root_id, root, item, throttle, should_cancel) -> int:
    item_id, file_id, src_rel, dest_rel, size, act, state = item
    if act != "move":
        conn.execute("UPDATE migration_items SET state = 'skipped' WHERE id = ?", (item_id,))
        conn.commit()
        return 0
    sidecar = conn.execute("SELECT sidecar FROM files WHERE id = ?", (file_id,)).fetchone()[0]
    if state == "copied":                         # repointed already: only the original is left
        _finish_original(conn, mid, opts, item_id, root, src_rel, size, sidecar)
        return 0

    cur = conn.execute("SELECT root_id, rel_path FROM files WHERE id = ?", (file_id,)).fetchone()
    if tuple(cur) != (root_id, src_rel):
        return _fail(conn, item_id, "The file moved since the plan was made - plan again")
    src = _abs(root, src_rel)
    if not os.path.exists(src):
        return _fail(conn, item_id, "The original is missing")
    if os.path.getsize(src) != size:
        return _fail(conn, item_id, "The original changed since the plan was made - plan again")

    # The destination: never overwrite, never rename. An identical file already
    # there is either our own earlier copy (resuming) or an unverified duplicate.
    folder, name = dest_rel.rsplit("/", 1) if "/" in dest_rel else ("", dest_rel)
    base = folder.replace("/", "\\")
    digest = None
    k = 1
    while True:
        cand_rel = dest_rel if k == 1 else "/".join([*sibling(base, k).split("\\"), name])
        dst = _abs(target, cand_rel)
        if not os.path.exists(dst):
            break
        if os.path.getsize(dst) == size:
            src_hash = _sha256(src, throttle)
            if _sha256(dst, throttle) == src_hash:
                other = conn.execute("SELECT id FROM files WHERE root_id = ? AND rel_path = ? AND id != ?",
                                     (target_root, cand_rel, file_id)).fetchone()
                if other:                          # an identical file already moved there
                    merge_user_data(conn, file_id, other[0])
                    conn.execute("UPDATE migration_items SET action = 'skip_duplicate', keeper_id = ?,"
                                 " state = 'skipped', note = 'Identical to a file already moved' WHERE id = ?",
                                 (other[0], item_id))
                    conn.commit()
                    return size
                digest = src_hash                  # our own copy from before an interruption
                break
        k += 1
    if cand_rel != dest_rel:
        conn.execute("UPDATE migration_items SET dest_rel = ?, note = ? WHERE id = ?",
                     (cand_rel, f"Name taken - goes to {cand_rel.rsplit('/', 1)[0]}", item_id))
        dest_rel = cand_rel

    if digest is None:
        digest = _copy_hashed(src, dst, throttle, should_cancel)
        if digest is None:
            raise InterruptedError
        if _sha256(dst, throttle) != digest:
            _unlink(dst)
            return _fail(conn, item_id, "The copy didn't match the original - removed; the original is untouched")

    # Its sidecar travels with it.
    side_mtime = None
    if sidecar:
        s_src = os.path.join(os.path.dirname(src), sidecar)
        s_dst = os.path.join(os.path.dirname(dst), sidecar)
        if os.path.exists(s_src):
            if not os.path.exists(s_dst):
                shutil.copy2(s_src, s_dst)
            if not _same_bytes(s_src, s_dst):
                return _fail(conn, item_id, f"A different {sidecar} is already at the destination")
            side_mtime = datetime.fromtimestamp(os.stat(s_dst).st_mtime, tz=timezone.utc).isoformat(
                timespec="seconds")
        else:
            sidecar = None

    # Repoint the same catalog entry, and move its central sidecar with it.
    _move_central_sidecar(conn, file_id, root_id, root, src_rel, target_root, target, dest_rel)
    conn.execute("UPDATE files SET root_id = ?, rel_path = ?, sidecar = ?, sidecar_mtime = ?,"
                 " missing_since = NULL WHERE id = ?", (target_root, dest_rel, sidecar, side_mtime, file_id))
    conn.execute("INSERT INTO file_moves (file_id, from_root, from_path, to_root, to_path, method)"
                 " VALUES (?, ?, ?, ?, ?, 'migrate')", (file_id, root_id, src_rel, target_root, dest_rel))
    conn.execute("UPDATE migration_items SET state = 'copied', sha256 = ? WHERE id = ?", (digest, item_id))
    conn.commit()
    _finish_original(conn, mid, opts, item_id, root, src_rel, size, sidecar)
    return size * 2


def _move_central_sidecar(conn, file_id, from_root, from_root_path, from_rel, to_root, to_root_path, to_rel):
    from lunelis import paths
    from lunelis.settings import Settings
    from lunelis.xmp.sync import central_path
    store = Settings(conn).get("sidecar_store_dir") or paths.SIDECAR_STORE
    name = to_rel.rsplit("/", 1)[-1]
    old = central_path(store, from_root, from_root_path, from_rel, from_rel.rsplit("/", 1)[-1])
    if os.path.exists(old):
        new = central_path(store, to_root, to_root_path, to_rel, name)
        os.makedirs(os.path.dirname(new), exist_ok=True)
        os.replace(old, new)


def _finish_original(conn, mid, opts, item_id, root, src_rel, size, sidecar) -> None:
    """Step 5: the original goes to quarantine - or stays, if the user asked
    to review first, or if it changed in the meantime."""
    src = _abs(root, src_rel)
    if opts.get("keep_sources", True):
        conn.execute("UPDATE migration_items SET state = 'kept' WHERE id = ?", (item_id,))
    elif not os.path.exists(src):
        conn.execute("UPDATE migration_items SET state = 'done' WHERE id = ?", (item_id,))
    elif os.path.getsize(src) != size:
        conn.execute("UPDATE migration_items SET state = 'kept', note = 'Original changed after copying -"
                     " left in place' WHERE id = ?", (item_id,))
    else:
        q = _quarantine_original(root, src_rel, sidecar, mid)
        conn.execute("UPDATE migration_items SET state = 'done', quarantine_path = ? WHERE id = ?", (q, item_id))
    conn.commit()


# --- the end of the job, and releasing kept originals ---------------------------------------------

def _set_aside(conn, mid, keep: bool, item_id, file_id, keeper_id, root, src_rel) -> None:
    """A skipped duplicate/damaged copy: merge its user data into the copy that
    moved, then quarantine it (or keep it, in review mode)."""
    moved = conn.execute("SELECT 1 FROM migration_items WHERE migration_id = ? AND file_id = ?"
                         " AND state IN ('copied', 'done', 'kept', 'released')", (mid, keeper_id)).fetchone()
    keeper_live = conn.execute("SELECT 1 FROM files WHERE id = ? AND missing_since IS NULL"
                               " AND quarantined_at IS NULL", (keeper_id,)).fetchone()
    if not (moved or keeper_live):
        return                                     # its keeper didn't make it: leave this copy alone
    merge_user_data(conn, file_id, keeper_id)
    if keep:
        conn.execute("UPDATE migration_items SET state = 'kept' WHERE id = ?", (item_id,))
    else:
        sidecar = conn.execute("SELECT sidecar FROM files WHERE id = ?", (file_id,)).fetchone()[0]
        if os.path.exists(_abs(root, src_rel)):
            q = _quarantine_original(root, src_rel, sidecar, mid)
            conn.execute("UPDATE files SET quarantined_at = ?, quarantine_path = ? WHERE id = ?", (_now(), q, file_id))
            conn.execute("UPDATE migration_items SET state = 'done', quarantine_path = ? WHERE id = ?", (q, item_id))
    conn.commit()


def finish(conn: sqlite3.Connection, job_id: int) -> None:
    """Called by the engine when the job's last folder is done."""
    row = conn.execute("SELECT id, options FROM migrations WHERE job_id = ?", (job_id,)).fetchone()
    if row is None:
        return
    mid, opts = row[0], json.loads(row[1])
    keep = opts.get("keep_sources", True)
    roots = dict(conn.execute("SELECT id, path FROM roots"))
    for item_id, file_id, keeper_id, src_root, src_rel in conn.execute(
            "SELECT id, file_id, keeper_id, src_root, src_rel FROM migration_items WHERE migration_id = ?"
            " AND action IN ('skip_duplicate', 'skip_damaged') AND state IN ('planned', 'skipped')",
            (mid,)).fetchall():
        if keeper_id is not None:
            _set_aside(conn, mid, keep, item_id, file_id, keeper_id, roots[src_root], src_rel)
    conn.execute("UPDATE migrations SET state = 'done', finished_at = datetime('now') WHERE id = ?", (mid,))
    conn.commit()


def release(conn: sqlite3.Connection, migration_id: int, *, backup_dir: Path | None = None) -> int:
    """'Keep originals until I review' -> the user reviewed: move the kept
    originals to quarantine now. Returns how many went."""
    if backup_dir is not None:
        from lunelis.catalog.backup import snapshot
        snapshot(conn, backup_dir, "before-release")
    roots = dict(conn.execute("SELECT id, path FROM roots"))
    n = 0
    for item_id, file_id, act, src_root, src_rel, size in conn.execute(
            "SELECT id, file_id, action, src_root, src_rel, size FROM migration_items"
            " WHERE migration_id = ? AND state = 'kept'", (migration_id,)).fetchall():
        root = roots[src_root]
        src = _abs(root, src_rel)
        if not os.path.exists(src) or os.path.getsize(src) != size:
            continue                               # gone or changed: not ours to move any more
        # (A moved file's catalog sidecar name is the same at both ends.)
        sidecar = conn.execute("SELECT sidecar FROM files WHERE id = ?", (file_id,)).fetchone()[0]
        q = _quarantine_original(root, src_rel, sidecar, migration_id)
        if act != "move":
            conn.execute("UPDATE files SET quarantined_at = ?, quarantine_path = ? WHERE id = ?", (_now(), q, file_id))
        conn.execute("UPDATE migration_items SET state = 'released', quarantine_path = ? WHERE id = ?", (q, item_id))
        conn.commit()
        n += 1
    conn.execute("UPDATE migrations SET state = 'released' WHERE id = ?", (migration_id,))
    conn.commit()
    return n


def kept_sources(conn: sqlite3.Connection, root_id: int) -> set[str]:
    """Originals of files that now live in a migration target but were left in
    place for review - a rescan of their old source must not catalog them as
    new files."""
    return {r[0] for r in conn.execute(
        "SELECT src_rel FROM migration_items WHERE src_root = ? AND action = 'move'"
        " AND state IN ('copied', 'kept')", (root_id,))}
