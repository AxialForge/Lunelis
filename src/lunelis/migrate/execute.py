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

DISK_FULL = {112, 39}            # ERROR_DISK_FULL, ERROR_HANDLE_DISK_FULL (Windows)


class TargetFull(SourceOffline):
    """The target ran out of space: the job waits instead of failing every
    file that's left (0.51)."""
    message = "Waiting - the target is full. Free some space; the migration carries on by itself."


def _disk_full(e: OSError) -> bool:
    import errno
    return getattr(e, "winerror", None) in DISK_FULL or e.errno == errno.ENOSPC
from lunelis.dupes.quarantine import QUARANTINE_DIR
from lunelis.importing.templates import sibling

CHUNK = 4 * 1024 * 1024
TMP_PREFIX = ".lunelis-migrating-"


class MigrationError(Exception):
    pass


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _ts(iso) -> float | None:
    """files.mtime (ISO 8601 text) as a timestamp."""
    if iso is None:
        return None
    if isinstance(iso, (int, float)):
        return float(iso)
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        pass
    try:
        return float(iso)                 # a plain number kept as text (0.52.2)
    except ValueError:
        return None


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


def set_aside_base(conn: sqlite3.Connection, migration_id: int, kind: str, root: str) -> str:
    """Where a set-aside file goes (0.41). With a Lunelis folder: its
    Duplicates (copies that weren't kept) or Trash (originals that were
    copied), under 'Migration N/<source folder name>' so the original path
    is kept. Without one: the quarantine folder on the file's own drive."""
    from lunelis import lunelis_folder
    from lunelis.settings import Settings
    sub = lunelis_folder.DUPLICATES if kind == "duplicate" else lunelis_folder.TRASH
    base = lunelis_folder.path(Settings(conn), sub)
    if base is None:
        return os.path.join(root, QUARANTINE_DIR, f"migration-{migration_id}")
    leaf = os.path.basename(os.path.normpath(root)) or root.replace(":", "").strip("\\/")
    # Two sources with the same last folder name (D:\\Photos, \\\\nas\\Photos) mustn't
    # share one folder there (0.51): the second gets its source number.
    leaves = [os.path.basename(os.path.normpath(p)).lower() for (p,) in conn.execute(
        "SELECT DISTINCT r.path FROM migration_items i JOIN roots r ON r.id = i.src_root"
        " WHERE i.migration_id = ?", (migration_id,))]
    if leaves.count(leaf.lower()) > 1:
        rid = conn.execute("SELECT id FROM roots WHERE path = ?", (root,)).fetchone()
        leaf = f"{leaf} (source {rid[0]})" if rid else leaf
    return os.path.join(str(base), f"Migration {migration_id}", leaf)


def _quarantine_original(root: str, rel: str, sidecar: str | None, migration_id: int,
                         base: str | None = None, record=None) -> str:
    """Move a source file (and its sidecar) aside - into `base` (the Lunelis
    folder's Duplicates / Trash), else the quarantine folder on its own drive.
    Nothing is deleted. `record(path)` is called with the place BEFORE the
    move, so a crash part-way still knows where the original went (0.51)."""
    src = _abs(root, rel)
    dst = os.path.join(base or os.path.join(root, QUARANTINE_DIR, f"migration-{migration_id}"), *rel.split("/"))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst) and os.path.exists(src) and _same_bytes(src, dst):
        # Our own copy from a move cut short after the copy: finish that move.
        if record:
            record(dst)
        _finish_cut_move(src, dst, sidecar)
        return dst
    s_name = sidecar
    if os.path.exists(dst) or (sidecar and os.path.exists(os.path.join(os.path.dirname(dst), sidecar))):
        # Decided before anything moves: the photo and its sidecar get the same new name.
        base, ext = os.path.splitext(dst)
        dst = f"{base} ({datetime.now():%H%M%S}){ext}"
        if sidecar:
            old = os.path.basename(src)
            s_name = (os.path.basename(dst) + sidecar[len(old):] if sidecar.lower().startswith(old.lower())
                      else f"({datetime.now():%H%M%S}) {sidecar}")
    from lunelis.dupes.quarantine import QuarantineRefused, move_pair
    s_src = os.path.join(os.path.dirname(src), sidecar) if sidecar else None
    if s_src and not os.path.exists(s_src):
        s_src = None
    if record:
        record(dst)
    try:
        move_pair(src, dst, s_src, os.path.join(os.path.dirname(dst), s_name) if s_src else None)   # both or neither
    except QuarantineRefused as e:
        raise FileExistsError(str(e)) from e      # an item error, like any other file problem
    return dst


def _finish_cut_move(src: str, dst: str, sidecar: str | None) -> None:
    """The original's verified copy is already at `dst`: its sidecar follows,
    then the original is removed - what move_pair would have finished."""
    from lunelis.dupes.quarantine import move_one
    if sidecar:
        s_src = os.path.join(os.path.dirname(src), sidecar)
        s_dst = os.path.join(os.path.dirname(dst), sidecar)
        if os.path.exists(s_src) and not os.path.exists(s_dst):
            move_one(s_src, s_dst)
    os.remove(src)


def _sidecar_after_cut_move(root: str, rel: str, sidecar: str | None, q: str | None) -> None:
    """The original reached the Trash but its sidecar didn't (the share went
    between the two): the sidecar follows now."""
    if not (sidecar and q and os.path.exists(q)):
        return
    s_src = os.path.join(os.path.dirname(_abs(root, rel)), sidecar)
    s_dst = os.path.join(os.path.dirname(q), sidecar)
    if os.path.exists(s_src) and not os.path.exists(s_dst):
        from lunelis.dupes.quarantine import move_one
        move_one(s_src, s_dst)


def merge_user_data(conn: sqlite3.Connection, from_id: int, to_id: int) -> None:
    """Before a copy is set aside, anything the user did to it that the kept
    copy lacks moves over: stars, label, flag, albums, event, tags."""
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
    conn.execute("INSERT OR IGNORE INTO file_tags (file_id, tag_id)"
                 " SELECT ?, tag_id FROM file_tags WHERE file_id = ? AND confidence IS NULL", (to_id, from_id))
    ev = conn.execute("SELECT event_id FROM event_files WHERE file_id = ?", (from_id,)).fetchone()
    if ev and not conn.execute("SELECT 1 FROM event_files WHERE file_id = ?", (to_id,)).fetchone():
        from lunelis.events import model
        model.add_files(conn, ev[0], [to_id], commit=False)


def carry_sidecar(conn: sqlite3.Connection, from_id: int, to_id: int) -> str | None:
    """Before an exact duplicate is set aside: if it has an XMP sidecar (darktable
    history, a culling tool's ratings) and the copy that stays has none, a copy
    of the sidecar goes beside the one that stays, named for it - so emptying
    the quarantine later can't take the only sidecar. Returns its new path."""
    rows = {fid: (os.path.join(root, *rel.split("/")), sidecar) for fid, root, rel, sidecar in conn.execute(
        "SELECT f.id, r.path, f.rel_path, f.sidecar FROM files f JOIN roots r ON r.id = f.root_id"
        " WHERE f.id IN (?, ?)", (from_id, to_id))}
    if from_id not in rows or to_id not in rows:
        return None
    (src_path, src_side), (dst_path, dst_side) = rows[from_id], rows[to_id]
    if not src_side or dst_side:
        return None
    side = os.path.join(os.path.dirname(src_path), src_side)
    if not os.path.isfile(side):
        return None
    src_name, dst_name = os.path.basename(src_path), os.path.basename(dst_path)
    if src_side.lower().startswith(src_name.lower()):        # IMG_1.JPG.xmp -> <name>.JPG.xmp
        new_name = dst_name + src_side[len(src_name):]
    else:                                                     # IMG_1.xmp -> <stem>.xmp
        new_name = os.path.splitext(dst_name)[0] + src_side[len(os.path.splitext(src_name)[0]):]
    target = os.path.join(os.path.dirname(dst_path), new_name)
    if os.path.exists(target):
        return None                                           # never overwrite a file that's there
    try:
        shutil.copy2(side, target)
    except OSError:
        return None
    conn.execute("UPDATE files SET sidecar = ? WHERE id = ?", (new_name, to_id))
    return target


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
    from lunelis.migrate.plan import summary
    s = summary(conn, migration_id)
    if not s.enough_space:
        raise MigrationError("There isn't enough free space on the target any more - free some space, "
                             "or choose another target and preview again.")
    if backup_dir is not None:
        from lunelis.catalog.backup import snapshot
        snapshot(conn, backup_dir, "before-migration")
    from lunelis.migrate import logs
    logs.write_before(conn, migration_id)          # every file in the sources, before anything moves
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
            if _disk_full(e):
                conn.rollback()
                raise TargetFull(str(e)) from e
            if is_network_error(e) or not os.path.isdir(root) or not os.path.isdir(target):
                conn.rollback()
                raise SourceOffline(str(e)) from e
            _item_failed(conn, item[0], e)
            result.errors.append(str(e))
        except Exception as e:                     # noqa: BLE001 - one odd file mustn't stop the job (0.51)
            conn.rollback()
            _item_failed(conn, item[0], e)
            result.errors.append(str(e))
    return result


def _item_failed(conn, item_id: int, e: BaseException) -> None:
    # A file already copied and repointed stays 'copied': only setting its
    # original aside is left, and finish() / the next run tries that again.
    conn.execute("UPDATE migration_items SET state = CASE state WHEN 'copied' THEN 'copied' ELSE 'failed'"
                 " END, error = ? WHERE id = ?", (f"{type(e).__name__}: {e}"[:300], item_id))
    conn.commit()


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
    planned_mtime = _ts(conn.execute("SELECT mtime FROM files WHERE id = ?", (file_id,)).fetchone()[0])
    if planned_mtime is not None and abs(os.path.getmtime(src) - planned_mtime) > 2:
        return _fail(conn, item_id, "The original was edited since it was cataloged - rescan, then plan again")

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
                other = conn.execute("SELECT id FROM files WHERE root_id = ? AND rel_path = ? COLLATE NOCASE"
                                     " AND id != ?", (target_root, cand_rel, file_id)).fetchone()
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
                if _copy_hashed(s_src, s_dst, throttle, None) is None:     # via a temp name (0.51)
                    raise InterruptedError
            if not _same_bytes(s_src, s_dst):
                # Our verified copy goes again (the original is untouched), so a
                # new plan doesn't find the name taken and file a second copy.
                _unlink(dst)
                return _fail(conn, item_id, f"A different {sidecar} is already at the destination")
        elif not os.path.exists(s_dst):
            sidecar = None
        # (A sidecar two files share - IMG_1.xmp for the CR2 and the JPG - may
        # have gone aside with the first: it's already beside this one.)
        if sidecar:
            side_mtime = datetime.fromtimestamp(os.stat(s_dst).st_mtime, tz=timezone.utc).isoformat(
                timespec="seconds")

    # Repoint the same catalog entry, and move its central sidecar with it.
    conn.execute("UPDATE files SET root_id = ?, rel_path = ?, sidecar = ?, sidecar_mtime = ?,"
                 " missing_since = NULL WHERE id = ?", (target_root, dest_rel, sidecar, side_mtime, file_id))
    conn.execute("INSERT INTO file_moves (file_id, from_root, from_path, to_root, to_path, method)"
                 " VALUES (?, ?, ?, ?, ?, 'migrate')", (file_id, root_id, src_rel, target_root, dest_rel))
    conn.execute("UPDATE migration_items SET state = 'copied', sha256 = ? WHERE id = ?", (digest, item_id))
    conn.commit()
    try:
        _move_central_sidecar(conn, file_id, root_id, root, src_rel, target_root, target, dest_rel)
    except OSError as e:                           # the catalog holds the edits; the file is a mirror
        import logging
        logging.getLogger(__name__).warning("Central sidecar not moved for %s: %s", dest_rel, e)
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
        # Gone already: moved aside by a run that was cut off - its place was
        # recorded first, and a sidecar left behind follows it now.
        q = conn.execute("SELECT quarantine_path FROM migration_items WHERE id = ?", (item_id,)).fetchone()[0]
        _sidecar_after_cut_move(root, src_rel, sidecar, q)
        conn.execute("UPDATE migration_items SET state = 'done' WHERE id = ?", (item_id,))
    elif os.path.getsize(src) != size:
        conn.execute("UPDATE migration_items SET state = 'kept', note = 'Original changed after copying -"
                     " left in place' WHERE id = ?", (item_id,))
    else:
        q = _quarantine_original(root, src_rel, sidecar, mid, set_aside_base(conn, mid, "original", root),
                                 record=_recorder(conn, item_id))
        conn.execute("UPDATE migration_items SET state = 'done', quarantine_path = ? WHERE id = ?", (q, item_id))
    conn.commit()


def _recorder(conn, item_id: int):
    def record(path: str) -> None:
        conn.execute("UPDATE migration_items SET quarantine_path = ? WHERE id = ?", (path, item_id))
        conn.commit()
    return record


# --- the end of the job, and releasing kept originals ---------------------------------------------

def _set_aside(conn, mid, keep: bool, item_id, file_id, keeper_id, root, src_rel) -> None:
    """A skipped duplicate/damaged copy: merge its user data into the copy that
    moved, then quarantine it (or keep it, in review mode)."""
    in_migration = conn.execute("SELECT 1 FROM migration_items WHERE migration_id = ? AND file_id = ?"
                                " AND action = 'move'", (mid, keeper_id)).fetchone()
    if in_migration:
        # Its keeper had to arrive: one that failed (disk full, a long path)
        # is still at its source, but that doesn't make this copy spare (0.51).
        ok = conn.execute("SELECT 1 FROM migration_items WHERE migration_id = ? AND file_id = ?"
                          " AND action = 'move' AND state IN ('copied', 'done', 'kept', 'released')",
                          (mid, keeper_id)).fetchone()
    else:
        ok = conn.execute("SELECT 1 FROM files WHERE id = ? AND missing_since IS NULL"
                          " AND quarantined_at IS NULL", (keeper_id,)).fetchone()
    if not ok:
        return                                     # its keeper didn't make it: leave this copy alone
    merge_user_data(conn, file_id, keeper_id)
    if conn.execute("SELECT action FROM migration_items WHERE id = ?", (item_id,)).fetchone()[0] == "skip_duplicate":
        carry_sidecar(conn, file_id, keeper_id)    # byte-identical: its sidecar describes the same photo
    if keep:
        conn.execute("UPDATE migration_items SET state = 'kept' WHERE id = ?", (item_id,))
    else:
        sidecar = conn.execute("SELECT sidecar FROM files WHERE id = ?", (file_id,)).fetchone()[0]
        if os.path.exists(_abs(root, src_rel)):
            q = _quarantine_original(root, src_rel, sidecar, mid, set_aside_base(conn, mid, "duplicate", root),
                                     record=_recorder(conn, item_id))
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
    # Copied, but setting the original aside failed (a busy share, a name
    # clash): one more try now; what's still left shows as 'copied' (0.51).
    for item_id, file_id, src_root, src_rel, size in conn.execute(
            "SELECT id, file_id, src_root, src_rel, size FROM migration_items WHERE migration_id = ?"
            " AND action = 'move' AND state = 'copied'", (mid,)).fetchall():
        sidecar = conn.execute("SELECT sidecar FROM files WHERE id = ?", (file_id,)).fetchone()[0]
        try:
            _finish_original(conn, mid, opts, item_id, roots[src_root], src_rel, size, sidecar)
        except OSError as e:
            _item_failed(conn, item_id, e)
    _remove_temp_files(conn, mid)
    for item_id, file_id, keeper_id, src_root, src_rel in conn.execute(
            "SELECT id, file_id, keeper_id, src_root, src_rel FROM migration_items WHERE migration_id = ?"
            " AND action IN ('skip_duplicate', 'skip_damaged') AND state IN ('planned', 'skipped')",
            (mid,)).fetchall():
        if keeper_id is not None:
            try:
                _set_aside(conn, mid, keep, item_id, file_id, keeper_id, roots[src_root], src_rel)
            except OSError as e:                   # one copy that won't move mustn't stop the rest (0.51)
                conn.rollback()
                conn.execute("UPDATE migration_items SET error = ? WHERE id = ?",
                             (f"Not set aside: {type(e).__name__}: {e}"[:300], item_id))
                conn.commit()
    conn.execute("UPDATE migrations SET state = 'done', finished_at = datetime('now') WHERE id = ?", (mid,))
    conn.commit()
    from lunelis.migrate import logs
    try:
        logs.write_manifest(conn, mid)
        logs.write_after(conn, mid)
        logs.accounted(conn, mid)
    except OSError as e:                           # the logs folder unreachable: the report is made at release
        import logging
        logging.getLogger(__name__).warning("Couldn't write the migration logs: %s", e)


def _remove_temp_files(conn: sqlite3.Connection, mid: int) -> None:
    """Half-written copies a power cut left behind (only ever our own temp names)."""
    target = conn.execute("SELECT target FROM migrations WHERE id = ?", (mid,)).fetchone()[0]
    folders = {d.rsplit("/", 1)[0] if "/" in d else "" for (d,) in conn.execute(
        "SELECT dest_rel FROM migration_items WHERE migration_id = ? AND dest_rel IS NOT NULL", (mid,))}
    for f in folders:
        try:
            with os.scandir(_abs(target, f) if f else target) as it:
                for e in it:
                    if e.name.startswith(TMP_PREFIX) and e.is_file():
                        _unlink(e.path)
        except OSError:
            continue


def cancelled(conn: sqlite3.Connection, job_id: int) -> None:
    """The job was cancelled from the Jobs page: the migration stops there -
    no longer 'running' (which blocked every new plan), and its manifest
    records what did move (0.51)."""
    row = conn.execute("SELECT id FROM migrations WHERE job_id = ? AND state = 'running'", (job_id,)).fetchone()
    if row is None:
        return
    conn.execute("UPDATE migrations SET state = 'cancelled', finished_at = datetime('now') WHERE id = ?", (row[0],))
    conn.commit()
    from lunelis.migrate import logs
    try:
        logs.write_manifest(conn, row[0])
    except OSError:
        pass


def release(conn: sqlite3.Connection, migration_id: int, *, backup_dir: Path | None = None) -> int:
    """'Keep originals until I review' -> the user reviewed: move the kept
    originals aside now. Returns how many went. Refused while the
    accounted-for report finds any source file unaccounted for (logs.py)."""
    from lunelis.migrate import logs
    rep = logs.accounted(conn, migration_id)
    if not rep.clean:
        raise MigrationError(f"{len(rep.unaccounted):,} source file(s) aren't accounted for yet - see the "
                             f"accounted-for report in {logs.logs_dir(conn, migration_id)}. Nothing was released.")
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
        if act == "move" and not _library_copy_ok(conn, migration_id, item_id):
            continue                               # the copy doesn't check out: the original stays
        # (A moved file's catalog sidecar name is the same at both ends.)
        sidecar = conn.execute("SELECT sidecar FROM files WHERE id = ?", (file_id,)).fetchone()[0]
        q = _quarantine_original(root, src_rel, sidecar, migration_id, set_aside_base(
            conn, migration_id, "original" if act == "move" else "duplicate", root))
        if act != "move":
            conn.execute("UPDATE files SET quarantined_at = ?, quarantine_path = ? WHERE id = ?", (_now(), q, file_id))
        conn.execute("UPDATE migration_items SET state = 'released', quarantine_path = ? WHERE id = ?", (q, item_id))
        conn.commit()
        n += 1
    conn.execute("UPDATE migrations SET state = 'released' WHERE id = ?", (migration_id,))
    conn.commit()
    logs.write_manifest(conn, migration_id)
    return n


def _library_copy_ok(conn: sqlite3.Connection, migration_id: int, item_id: int) -> bool:
    """Re-read the Library copy and compare it with the hash taken while
    copying. Hours or days after the copy, so it comes from the NAS's disks,
    not the PC's cache of what it just wrote (0.51)."""
    target, dest, sha = conn.execute(
        "SELECT m.target, i.dest_rel, i.sha256 FROM migration_items i JOIN migrations m ON m.id = i.migration_id"
        " WHERE i.id = ?", (item_id,)).fetchone()
    if not sha:
        return True                                # from before hashes were kept: compared at copy time
    try:
        ok = _sha256(_abs(target, dest)) == sha
    except OSError:
        ok = False
    if not ok:
        conn.execute("UPDATE migration_items SET note = 'Library copy failed the re-check - original kept'"
                     " WHERE id = ?", (item_id,))
        conn.commit()
    return ok


def kept_sources(conn: sqlite3.Connection, root_id: int) -> set[str]:
    """Originals of files that now live in a migration target but were left in
    place for review - a rescan of their old source must not catalog them as
    new files."""
    return {r[0] for r in conn.execute(
        "SELECT src_rel FROM migration_items WHERE src_root = ? AND action = 'move'"
        " AND state IN ('copied', 'kept')", (root_id,))}
