"""
Keeping catalog ratings and XMP sidecars in step.

import_sidecars()  sidecar -> catalog, for sidecars that changed since we last
                   read or wrote them (the scanner records name + mtime).
export_pending()   catalog -> sidecar, for ratings changed in Lunelis.

Conflict rule: a change made in Lunelis and not yet written out wins - its
sidecar isn't imported until it has been exported. After that, whichever
side changed last wins, because an export records the sidecar's new mtime as
synced and any later outside edit changes that mtime again.
"""
from __future__ import annotations

import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable

from lunelis.xmp.sidecar import XmpFields, default_sidecar, read_sidecar, write_sidecar

WORKERS = 8                      # sidecar I/O is NAS round trips, like metadata
BATCH_SIZE = 200
ProgressFn = Callable[[int, int, str], None]
CancelFn = Callable[[], bool]


@dataclass
class SyncResult:
    done: int = 0
    failed: int = 0
    cancelled: bool = False
    seconds: float = 0.0


def _iso_utc_mtime(path: str) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(os.stat(path).st_mtime, tz=timezone.utc).isoformat()


def _run(todo, work, apply, *, on_progress, should_cancel, workers) -> SyncResult:
    """Shared shape: parallel I/O, results applied on the calling thread in batches."""
    started = time.perf_counter()
    result = SyncResult()
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for start in range(0, len(todo), BATCH_SIZE):
            if should_cancel and should_cancel():
                result.cancelled = True
                break
            chunk = todo[start:start + BATCH_SIZE]
            futures = [pool.submit(work, row) for row in chunk]
            for row, fut in zip(chunk, futures):
                ok = apply(row, *fut.result())
                result.done += ok
                result.failed += not ok
                done += 1
                if on_progress and (done % 25 == 0 or done == len(todo)):
                    on_progress(done, len(todo), row["rel_path"])
            apply(None)                        # commit the batch
    result.seconds = time.perf_counter() - started
    return result


# --- sidecar -> catalog ----------------------------------------------------

IMPORT_SQL = """
    SELECT f.id, r.path AS root, f.rel_path, f.sidecar, f.sidecar_mtime,
           rt.file_id IS NOT NULL AS has_row, rt.flag
    FROM files f
    JOIN roots r ON r.id = f.root_id
    LEFT JOIN ratings rt ON rt.file_id = f.id
    WHERE f.sidecar IS NOT NULL
      AND f.sidecar_mtime IS NOT f.sidecar_synced_mtime
      AND f.missing_since IS NULL AND f.excluded = 0 AND r.enabled = 1
      AND COALESCE(rt.xmp_pending, 0) = 0
"""


def import_pending_count(conn: sqlite3.Connection) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM ({IMPORT_SQL})").fetchone()[0]


def import_sidecars(conn: sqlite3.Connection, *, on_progress: ProgressFn | None = None,
                    should_cancel: CancelFn | None = None, workers: int = WORKERS) -> SyncResult:
    prev_factory, conn.row_factory = conn.row_factory, sqlite3.Row
    todo = conn.execute(IMPORT_SQL).fetchall()

    def work(row):
        folder = os.path.dirname(os.path.join(row["root"], *row["rel_path"].split("/")))
        try:
            return read_sidecar(os.path.join(folder, row["sidecar"])), None
        except Exception as e:
            return None, f"{type(e).__name__}: {e}"[:300]

    upserts: list[tuple] = []
    synced: list[tuple] = []
    keyword_adds: list[tuple] = []

    def apply(row, fields=None, err=None):
        if row is None:
            conn.executemany(
                "INSERT INTO ratings (file_id, stars, flag, color_label) VALUES (?, ?, ?, ?)"
                " ON CONFLICT(file_id) DO UPDATE SET stars = excluded.stars, flag = excluded.flag,"
                " color_label = excluded.color_label, updated_at = datetime('now')"
                " WHERE ratings.xmp_pending = 0",
                upserts)
            conn.executemany("UPDATE files SET sidecar_synced_mtime = ? WHERE id = ?", synced)
            if keyword_adds:
                from lunelis.tags import model as tags
                for fid, kws in keyword_adds:
                    for kw in kws:
                        tid = tags.tag_id(conn, kw)
                        if tid is not None:
                            conn.execute("INSERT OR IGNORE INTO file_tags (file_id, tag_id) VALUES (?, ?)", (fid, tid))
            conn.commit()
            upserts.clear()
            synced.clear()
            keyword_adds.clear()
            return True
        if fields is None:
            return False                       # unreadable: retried next scan
        synced.append((row["sidecar_mtime"], row["id"]))
        if fields.rejected:
            flag = "reject"
        else:
            flag = row["flag"] if row["flag"] == "pick" else None   # picks live only in the catalog
        if row["has_row"] or (fields.stars, fields.rejected, fields.label) != (0, False, None):
            # Don't create a row for every untouched sidecar (darktable writes
            # one per imported photo, rating 0, no label).
            upserts.append((row["id"], fields.stars, flag, fields.label))
        if fields.keywords:
            # Tags from a sidecar are only ever ADDED: a tool that rewrites a
            # sidecar without keywords must not wipe the photo's tags.
            keyword_adds.append((row["id"], fields.keywords))
        return True

    try:
        return _run(todo, work, apply, on_progress=on_progress,
                    should_cancel=should_cancel, workers=workers)
    finally:
        conn.row_factory = prev_factory


# --- catalog -> sidecar ----------------------------------------------------

EXPORT_SQL = """
    SELECT f.id, r.id AS root_id, r.path AS root, f.rel_path, f.filename, f.sidecar,
           rt.stars, rt.flag, rt.color_label, ed.stack AS edit_stack,
           (SELECT group_concat(t.name, char(31)) FROM file_tags ft JOIN tags t ON t.id = ft.tag_id
             WHERE ft.file_id = f.id AND ft.confidence IS NULL) AS tag_names
    FROM ratings rt
    JOIN files f ON f.id = rt.file_id
    LEFT JOIN edits ed ON ed.file_id = f.id
    JOIN roots r ON r.id = f.root_id
    WHERE rt.xmp_pending = 1
"""


def root_key(root_id: int, root_path: str) -> str:
    """The central store's folder for a root: '<id>-<last path part>', so two
    roots with the same folder name can't collide and the tree stays legible."""
    tail = os.path.basename(os.path.normpath(root_path)) or root_path[:1]   # 'D:\' -> 'D'
    tail = "".join(c if c.isalnum() or c in " ._-" else "_" for c in tail).strip() or "root"
    return f"{root_id}-{tail}"


def central_path(store: str | os.PathLike, root_id: int, root_path: str,
                 rel_path: str, filename: str) -> str:
    """Where the central store keeps a file's sidecar: a mirror of its folder,
    darktable-style name - e.g. <store>/2-Photos/2026/6-19-2026/_A757643.ARW.xmp."""
    rel_dir = rel_path.rsplit("/", 1)[0] if "/" in rel_path else ""
    return os.path.join(store, root_key(root_id, root_path), *rel_dir.split("/"),
                        default_sidecar(filename))


def export_pending(conn: sqlite3.Connection, *, on_progress: ProgressFn | None = None,
                   should_cancel: CancelFn | None = None, workers: int = WORKERS,
                   mode: str | None = None, update_existing: bool | None = None,
                   store_dir: str | os.PathLike | None = None) -> SyncResult:
    """Write pending ratings out, per the sidecar settings:

    mode 'central'  -> the central store (photo folders untouched)
    mode 'beside'   -> next to the photo (existing sidecar, else a new name.EXT.xmp)
    mode 'catalog'  -> nowhere new
    update_existing -> additionally keep a sidecar that ALREADY exists next to
                       the photo in step, without ever creating one there.
    """
    from lunelis.settings import Settings

    s = Settings(conn)
    mode = mode or s.get("sidecar_mode")
    if update_existing is None:
        update_existing = s.get("update_existing_sidecars")
    if store_dir is None:
        from lunelis.paths import SIDECAR_STORE
        store_dir = s.get("sidecar_store_dir") or SIDECAR_STORE

    prev_factory, conn.row_factory = conn.row_factory, sqlite3.Row
    todo = conn.execute(EXPORT_SQL).fetchall()

    def work(row):
        fields = XmpFields(stars=row["stars"] or 0, rejected=row["flag"] == "reject",
                           label=row["color_label"], edit=row["edit_stack"],
                           keywords=tuple(sorted((row["tag_names"] or "").split(chr(31)), key=str.lower))
                           if row["tag_names"] else ())
        empty = fields == XmpFields()          # e.g. a pick-only change: nothing XMP can hold
        folder = os.path.dirname(os.path.join(row["root"], *row["rel_path"].split("/")))
        # The central store first, on its own: an existing sidecar beside the
        # photo that can't be updated (unreadable XML, a read-only share) is
        # an extra and must not keep the store from getting the change.
        if mode == "central":
            try:
                path = central_path(store_dir, row["root_id"], row["root"],
                                    row["rel_path"], row["filename"])
                if not (empty and not os.path.exists(path)):
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    write_sidecar(path, fields)
            except Exception as e:
                return False, None, f"{type(e).__name__}: {e}"[:300]
        beside_name = None
        try:
            # Next to the photo.
            if row["sidecar"] and (mode == "beside" or update_existing):
                beside_name = row["sidecar"]
            elif mode == "beside" and not empty:
                beside_name = default_sidecar(row["filename"])
            beside_mtime = None
            if beside_name:
                path = os.path.join(folder, beside_name)
                write_sidecar(path, fields)
                beside_mtime = _iso_utc_mtime(path)
            return beside_name, beside_mtime, None
        except Exception as e:
            err = f"{type(e).__name__}: {e}"[:300]
            if mode == "beside":
                return False, None, err            # the one place it goes: stays pending
            return None, None, f"The sidecar beside the photo wasn't updated: {err}"[:300]

    cleared: list[tuple] = []
    failed: list[tuple] = []
    sidecars: list[tuple] = []

    def apply(row, name=None, mtime=None, err=None):
        if row is None:
            # Clear pending only if the rating is still what we wrote: a change
            # made while the write was in flight stays pending for next time.
            conn.executemany(
                "UPDATE ratings SET xmp_pending = 0, xmp_error = NULL WHERE file_id = ?"
                " AND stars IS ? AND flag IS ? AND color_label IS ?", cleared)
            conn.executemany("UPDATE ratings SET xmp_error = ? WHERE file_id = ?", failed)
            # A sidecar we wrote next to the photo is recorded as synced, so the
            # next scan doesn't re-import our own write as an outside edit.
            conn.executemany(
                "UPDATE files SET sidecar = ?, sidecar_mtime = ?, sidecar_synced_mtime = ? WHERE id = ?",
                sidecars)
            conn.commit()
            cleared.clear()
            failed.clear()
            sidecars.clear()
            return True
        if name is False:
            failed.append((err, row["id"]))    # NAS offline, read-only share...: stays pending
            return False
        cleared.append((row["id"], row["stars"], row["flag"], row["color_label"]))
        if err:
            failed.append((err, row["id"]))    # saved where it belongs; the extra copy said why not
        if name:
            sidecars.append((name, mtime, mtime, row["id"]))
        return True

    try:
        return _run(todo, work, apply, on_progress=on_progress,
                    should_cancel=should_cancel, workers=workers)
    finally:
        conn.row_factory = prev_factory
