"""
Duplicate detection v1: byte-identical files (Phase 1, Step 8).

Work is done one FOLDER at a time, and a folder is only "done" once its
duplicates are known across the whole catalog: hashing a folder also hashes
every file anywhere that has the same size as one of its files (a byte-
identical copy must have the same size). So results appear folder by folder
without ever needing a whole-library pass first.

1. Sampled pass (process_folder): sample_hash for the folder's files that
   share a size with any other file, plus those other files. Files with the
   same sample hash form a 'sampled' group - "likely identical".
2. Verification (verify_group): full sha256 of every member. Members that
   really are identical form a 'exact' group (verified = 1). Only verified
   groups can be acted on (quarantine).
"""
from __future__ import annotations

import logging
import os
import sqlite3
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Iterable

from lunelis.dupes.hashing import SourceOffline, Throttle, full_hash, is_network_error, sample_hash

WORKERS = 8
CancelFn = Callable[[], bool]

LIVE = "f.missing_since IS NULL AND f.excluded = 0 AND f.quarantined_at IS NULL AND r.enabled = 1"
_log = logging.getLogger(__name__)


@dataclass
class FolderResult:
    hashed: int = 0
    bytes_read: int = 0
    errors: list[str] = field(default_factory=list)
    groups: set[int] = field(default_factory=set)        # group ids touched
    cancelled: bool = False


def _abs(root: str, rel_path: str) -> str:
    return os.path.join(root, *rel_path.split("/"))


def _dir_of(rel_path: str) -> str:
    return rel_path.rsplit("/", 1)[0] if "/" in rel_path else ""


def folder_sql(folder: str, col: str = "f.rel_path") -> tuple[str, list]:
    """(SQL, parameters) for "this file sits directly in `folder`".

    Every folder-by-folder job used to list the whole source for each folder
    and pick the folder's files out in Python: minutes of overhead per job on
    160k files. A folder's files are one run of rel_path ('2019/Trip/' up to
    '2019/Trip0' - '0' is the character after '/'), which the catalog's own
    (root_id, rel_path) index finds directly; the instr() drops the files of
    sub-folders. Put `f.root_id = ?` beside it so that index is used (0.54)."""
    if not folder:
        return f"instr({col}, '/') = 0", []
    return (f"{col} >= ? AND {col} < ? AND instr(substr({col}, ?), '/') = 0",
            [folder + "/", folder + "0", len(folder) + 2])


# --- planning ----------------------------------------------------------------

def plan_folders(conn: sqlite3.Connection, scope: Iterable[tuple[int, str | None]]
                 ) -> list[tuple[int, str, int]]:
    """[(root_id, folder, file count)] for a scope of (root_id, folder or None
    for the whole root). Folders come back in path order."""
    out: list[tuple[int, str, int]] = []
    for root_id, prefix in scope:
        counts: dict[str, int] = defaultdict(int)
        sql = ("SELECT f.rel_path FROM files f JOIN roots r ON r.id = f.root_id"
               f" WHERE f.root_id = ? AND {LIVE}")
        params: list = [root_id]
        if prefix:
            sql += " AND (f.rel_path LIKE ? ESCAPE '\\')"
            params.append(prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%")
        for (rel,) in conn.execute(sql, params):
            counts[_dir_of(rel)] += 1
        out += [(root_id, d, n) for d, n in sorted(counts.items())]
    return out


# --- the sampled pass ----------------------------------------------------------

def _hash_rows(rows, throttle, should_cancel, workers, result: FolderResult):
    """Sample-hash (id, root, rel_path, size) rows in parallel. Returns
    [(hash, id)] for the ones that hashed. Raises SourceOffline if the share
    went away, so the job can wait instead of failing every file."""
    def work(row):
        fid, root, rel, size = row
        if should_cancel and should_cancel():
            return fid, None, 0, None
        try:
            digest, n = sample_hash(_abs(root, rel), size, throttle)
            return fid, digest, n, None
        except SourceOffline:
            raise
        except OSError as e:
            if is_network_error(e):
                raise SourceOffline(e.errno, e.strerror) from e
            return fid, None, 0, f"{rel}: {e.strerror or e}"

    out = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for fid, digest, n, err in pool.map(work, rows):
            if err:
                result.errors.append(err)
                # Nothing reads result.errors: without this a file that couldn't
                # be compared was dropped without a word (0.54).
                _log.warning("Duplicates: %s - not compared", err)
            if digest:
                out.append((digest, fid))
                result.hashed += 1
                result.bytes_read += n
    if should_cancel and should_cancel():
        result.cancelled = True
    return out


def _candidates(conn: sqlite3.Connection, root_id: int, folder: str, sizes: list[int]) -> list[tuple]:
    """Unhashed files that could be byte-identical to a file in this folder.

    Size alone isn't selective enough: uncompressed Sony ARWs are all exactly
    the same size, so on the real library one folder's RAWs matched ~18k
    others and a 15.8k-file job hashed 91k files. Byte-identical files have
    identical EXIF too (same bytes), so a candidate must also share the
    capture time (to the millisecond). A file with no capture time - no EXIF,
    or unreadable - matches on size alone, so nothing identical is missed.
    """
    rows = []
    for start in range(0, len(sizes), 500):
        chunk = sizes[start:start + 500]
        rows += conn.execute(
            f"SELECT f.id, r.path, f.rel_path, f.size_bytes, f.root_id, e.captured_at, f.sample_hash"
            f" FROM files f JOIN roots r ON r.id = f.root_id LEFT JOIN exif e ON e.file_id = f.id"
            f" WHERE {LIVE} AND f.size_bytes IN ({','.join('?' * len(chunk))})", chunk).fetchall()

    by_size: dict[int, list[tuple]] = defaultdict(list)
    for row in rows:
        by_size[row[3]].append(row)
    need: dict[int, tuple] = {}
    for bucket in by_size.values():
        mine = [r for r in bucket if r[4] == root_id and _dir_of(r[2]) == folder]
        others = [r for r in bucket if not (r[4] == root_id and _dir_of(r[2]) == folder)]
        by_time: dict[str | None, list[tuple]] = defaultdict(list)
        for o in others:
            by_time[o[5]].append(o)
        undated = by_time.get(None, [])
        for m in mine:
            matches = others if m[5] is None else by_time.get(m[5], []) + undated
            matches = [o for o in matches if o[0] != m[0]]
            if matches:
                for r in [m, *matches]:
                    if r[6] is None:
                        need[r[0]] = r[:4]
    return list(need.values())


def process_folder(conn: sqlite3.Connection, root_id: int, folder: str, *,
                   throttle: Throttle | None = None, should_cancel: CancelFn | None = None,
                   workers: int = WORKERS) -> FolderResult:
    """Find every byte-identical candidate of this folder's files, anywhere."""
    result = FolderResult()
    # Just this folder's rows, found through the index (0.54) - see folder_sql.
    where, args = folder_sql(folder)
    in_folder = "f.root_id = ? AND " + where
    params = [root_id, *args]

    # Sizes in this folder that also occur on some other live file.
    sizes = [s for (s,) in conn.execute(
        f"SELECT DISTINCT f.size_bytes FROM files f JOIN roots r ON r.id = f.root_id"
        f" WHERE {in_folder} AND {LIVE} AND f.size_bytes > 0"
        f" AND EXISTS (SELECT 1 FROM files g WHERE g.size_bytes = f.size_bytes AND g.id != f.id"
        f"             AND g.missing_since IS NULL AND g.excluded = 0 AND g.quarantined_at IS NULL)", params)]
    if not sizes:
        return result
    todo = _candidates(conn, root_id, folder, sizes)
    hashed = _hash_rows(todo, throttle, should_cancel, workers, result)
    conn.executemany("UPDATE files SET sample_hash = ? WHERE id = ?", hashed)
    conn.commit()
    if result.cancelled:
        return result

    keys = {k for (k,) in conn.execute(
        f"SELECT DISTINCT f.sample_hash FROM files f JOIN roots r ON r.id = f.root_id"
        f" WHERE {in_folder} AND {LIVE} AND f.sample_hash IS NOT NULL", params)}
    result.groups = rebuild_groups(conn, "sampled", "sample_hash", keys)
    return result


def rebuild_groups(conn: sqlite3.Connection, method: str, column: str, keys: Iterable[str]) -> set[int]:
    """Make duplicate_groups(method, key) match the live files sharing each key."""
    touched: set[int] = set()
    for key in keys:
        members = [fid for (fid,) in conn.execute(
            f"SELECT f.id FROM files f JOIN roots r ON r.id = f.root_id"
            f" WHERE f.{column} = ? AND {LIVE} ORDER BY f.id", (key,))]
        row = conn.execute("SELECT id FROM duplicate_groups WHERE method = ? AND hash_key = ?",
                           (method, key)).fetchone()
        if len(members) < 2:
            if row:
                conn.execute("DELETE FROM duplicate_groups WHERE id = ?", (row[0],))
            continue
        if row:
            gid = row[0]
            conn.execute("DELETE FROM duplicate_group_files WHERE group_id = ?", (gid,))
        else:
            gid = conn.execute(
                "INSERT INTO duplicate_groups (method, hash_key, verified) VALUES (?, ?, ?)",
                (method, key, int(method == "exact"))).lastrowid
        conn.executemany("INSERT INTO duplicate_group_files (group_id, file_id) VALUES (?, ?)",
                         [(gid, fid) for fid in members])
        touched.add(gid)
    conn.commit()
    return touched


# --- verification -----------------------------------------------------------------

def verify_group(conn: sqlite3.Connection, group_id: int, *, throttle: Throttle | None = None,
                 should_cancel: CancelFn | None = None, workers: int = 4) -> list[int]:
    """Full-hash every member of a group; returns the verified 'exact' group
    ids that came out of it (usually one; none if the sample was a fluke)."""
    rows = conn.execute(
        "SELECT f.id, r.path, f.rel_path, f.content_hash FROM duplicate_group_files m"
        " JOIN files f ON f.id = m.file_id JOIN roots r ON r.id = f.root_id"
        " WHERE m.group_id = ?", (group_id,)).fetchall()

    def work(row):
        fid, root, rel, known = row
        if known:
            return fid, known
        try:
            digest, _ = full_hash(_abs(root, rel), throttle, should_cancel)
        except (FileNotFoundError, PermissionError) as e:
            # Gone or locked since the scan: this one stays unverified and the
            # job goes on - it used to end "Stopped by an error" at the same
            # folder on every retry (0.54).
            _log.warning("Duplicates: %s - not verified (%s)", rel, e.strerror or e)
            return fid, None
        return fid, digest

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(work, rows))
    conn.executemany("UPDATE files SET content_hash = ? WHERE id = ? AND content_hash IS NULL",
                     [(h, fid) for fid, h in results if h])
    conn.commit()
    keys = {h for _, h in results if h}
    return sorted(rebuild_groups(conn, "exact", "content_hash", keys))


# --- keeper suggestion ---------------------------------------------------------------

def keeper_rank(preferred_roots: list[int] | None = None):
    """Sort key for (file id, root id, root path, rel path) rows: best copy to
    keep first. Byte-identical copies have the same content, so the rule is
    about WHERE: a preferred root, then not a Takeout export, then the
    shallowest path, then the shortest, then the one cataloged first."""
    order = {rid: i for i, rid in enumerate(preferred_roots or [])}

    def rank(r):
        fid, rid, root, rel = r[:4]
        takeout = "takeout" in (root + "/" + rel).lower()
        return (order.get(rid, len(order)), takeout, rel.count("/"), len(rel), fid)

    return rank


def suggest_keeper(conn: sqlite3.Connection, group_id: int,
                   preferred_roots: list[int] | None = None) -> int:
    rows = conn.execute(
        "SELECT f.id, f.root_id, r.path, f.rel_path FROM duplicate_group_files m"
        " JOIN files f ON f.id = m.file_id JOIN roots r ON r.id = f.root_id"
        " WHERE m.group_id = ? AND f.quarantined_at IS NULL AND f.missing_since IS NULL AND f.excluded = 0",
        (group_id,)).fetchall()
    return min(rows, key=keeper_rank(preferred_roots))[0]
