"""
Folder root + scan (Phase 1, Step 2).

Walks a root and makes the `files` table match what's on disk: new files are
inserted, changed ones (size or mtime differ) are updated, files that are gone
get `missing_since` set. Nothing is hashed, decoded or read here - only
directory metadata - so a rescan of the whole library stays cheap. Content
hashing is Step 7, EXIF is Step 3, proxies are Step 4.

The scanner never deletes a row. A missing file keeps its ratings, albums and
faces until the user decides otherwise, and a root that is unreachable (NAS
offline, drive unplugged) is refused outright rather than scanned as empty.

    python -m lunelis.importers.scan D:\\Photos      # dev trigger + timing
"""
from __future__ import annotations

import ctypes
import os
import sqlite3
import stat
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from lunelis.importers.formats import ext_of, is_cataloged, is_raw
from lunelis.xmp.sidecar import choose_sidecar

# Directories that are never photo folders and are often unreadable anyway.
SKIP_DIR_NAMES = frozenset({
    "$recycle.bin", "system volume information", "@eadir", "#recycle", "#snapshot",
    "_lunelis quarantine",       # our own quarantine folder (dupes/quarantine.py)
})

BATCH_SIZE = 2000          # rows per commit: an interrupted scan keeps its progress
PROGRESS_EVERY = 500       # files between progress callbacks

ProgressFn = Callable[[int, str], None]   # (files_seen, current_dir)
CancelFn = Callable[[], bool]


class RootUnavailable(Exception):
    """The root folder can't be reached right now (offline share, unplugged drive)."""


class RootOverlap(ValueError):
    """The new root is inside, or contains, a root that's already cataloged."""


@dataclass
class ScanResult:
    root_id: int
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    missing: int = 0            # newly flagged this scan
    restored: int = 0           # flagged before, found again this scan
    skipped: int = 0            # files with an extension we don't catalog
    sidecars_changed: int = 0   # XMP sidecar appeared / changed / vanished
    cancelled: bool = False
    errors: list[str] = field(default_factory=list)   # unreadable directories
    seconds: float = 0.0

    @property
    def seen(self) -> int:
        return self.added + self.updated + self.unchanged + self.restored


# --- roots -----------------------------------------------------------------

def _norm(path: str | Path) -> str:
    # abspath, not Path.resolve(): resolve() rewrites a mapped drive letter to
    # its UNC target, and the user should see the path they picked.
    norm = os.path.normpath(os.path.abspath(str(path)))
    if sys.platform == "win32":
        # Expand 8.3 short names (C:\Users\EXAMPL~1) so one folder can't be
        # added twice under two spellings and slip past the overlap check.
        buf = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.kernel32.GetLongPathNameW(norm, buf, len(buf)):
            norm = buf.value
    return norm


def root_kind(path: str) -> str:
    """'nas' for UNC paths and mapped network drives, else 'local'."""
    if path.startswith("\\\\"):
        return "nas"
    if sys.platform == "win32" and len(path) >= 2 and path[1] == ":":
        DRIVE_REMOTE = 4
        if ctypes.windll.kernel32.GetDriveTypeW(path[:2] + "\\") == DRIVE_REMOTE:
            return "nas"
    return "local"


def add_root(conn: sqlite3.Connection, path: str | Path) -> int:
    """Register a folder as a library root and return its id.

    Re-adding an existing root returns the existing id. A root nested inside
    another (or containing one) is refused, since every file under it would
    otherwise be cataloged twice under two different rel_paths.
    """
    norm = _norm(path)
    if not os.path.isdir(norm):
        raise RootUnavailable(f"Not a reachable folder: {norm}")

    key = os.path.normcase(norm)
    for row in conn.execute("SELECT id, path FROM roots"):
        other = os.path.normcase(row["path"])
        if other == key:
            return row["id"]
        try:
            common = os.path.commonpath([key, other])
        except ValueError:          # different drives
            continue
        if common in (key, other):
            raise RootOverlap(f"{norm} overlaps existing root {row['path']}")

    cur = conn.execute(
        "INSERT INTO roots (path, kind) VALUES (?, ?)", (norm, root_kind(norm))
    )
    conn.commit()
    return cur.lastrowid


class RootInUse(Exception):
    """The source can't be removed right now (a migration or job uses it)."""


def remove_root(conn: sqlite3.Connection, root_id: int) -> int:
    """Take a source out of the library: its catalog entries go (and with them
    ratings, tags, albums, faces and edits kept only in the catalog). Nothing
    on disk is touched - the folder, the photos and their sidecars stay as they
    are, and adding the folder again catalogs it afresh. Returns how many
    files were removed from the catalog."""
    if conn.execute("SELECT 1 FROM migrations WHERE state IN ('running', 'done') AND ("
                    " target_root_id = ? OR id IN (SELECT migration_id FROM migration_items"
                    " WHERE src_root = ? AND state IN ('copied', 'kept')))", (root_id, root_id)).fetchone():
        raise RootInUse("A migration still depends on this source - finish it (release the originals) first.")
    if conn.execute("SELECT 1 FROM job_folders jf JOIN jobs j ON j.id = jf.job_id WHERE jf.root_id = ?"
                    " AND j.state IN ('running', 'queued', 'waiting', 'paused')", (root_id,)).fetchone():
        raise RootInUse("A background job is working in this source - let it finish or cancel it in Jobs first.")
    n = conn.execute("SELECT COUNT(*) FROM files WHERE root_id = ?", (root_id,)).fetchone()[0]
    conn.commit()
    try:                                             # one transaction: all of it or none of it
        conn.execute("DELETE FROM files WHERE root_id = ?", (root_id,))     # cascades to its rows
        conn.execute("DELETE FROM excluded_folders WHERE root_id = ?", (root_id,))
        conn.execute("DELETE FROM job_folders WHERE root_id = ?", (root_id,))
        conn.execute("UPDATE migrations SET target_root_id = NULL WHERE target_root_id = ?", (root_id,))
        conn.execute("DELETE FROM roots WHERE id = ?", (root_id,))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return n


# --- walking ---------------------------------------------------------------

def _iso_utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _is_hidden(entry: os.DirEntry) -> bool:
    if entry.name.startswith("."):
        return True
    attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    return bool(attrs & (stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM)) if attrs else False


def _walk(root: str, result: ScanResult, errored_dirs: list[str],
          on_progress: ProgressFn | None, should_cancel: CancelFn | None,
          skip_dirs: frozenset[str] = frozenset()):
    """Yield (rel_path, filename, size, mtime_iso, sidecar, sidecar_mtime) for
    every cataloged file. `sidecar` is the name of the XMP file in the same
    folder that belongs to it (xmp.sidecar.choose_sidecar), found from the
    same directory listing - no extra round trips.

    Uses scandir directly: on Windows the DirEntry stat comes free from the
    directory listing, so a walk costs one round-trip per folder, not per
    file - the difference between seconds and minutes on an SMB share.

    `skip_dirs` are folders the user excluded (lower-cased rel paths).
    """
    stack = [""]
    seen = 0
    while stack:
        if should_cancel and should_cancel():
            result.cancelled = True
            return
        rel_dir = stack.pop()
        abs_dir = os.path.join(root, rel_dir) if rel_dir else root
        try:
            with os.scandir(abs_dir) as it:
                entries = list(it)
        except OSError as e:
            errored_dirs.append(rel_dir)
            result.errors.append(f"{abs_dir}: {e.strerror or e}")
            continue

        xmps: dict[str, os.DirEntry] = {}
        raw_stems: set[str] = set()
        for entry in entries:
            low = entry.name.lower()
            if low.endswith(".xmp"):
                xmps[low] = entry
            elif is_raw(entry.name):
                raw_stems.add(low.rsplit(".", 1)[0])
        xmp_names = set(xmps)

        for entry in entries:
            rel = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
            try:
                if entry.is_symlink() or _is_hidden(entry):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if entry.name.lower() not in SKIP_DIR_NAMES and rel.lower() not in skip_dirs:
                        stack.append(rel)
                    continue
                if not is_cataloged(entry.name):
                    result.skipped += 1
                    continue
                st = entry.stat(follow_symlinks=False)
            except OSError as e:
                result.errors.append(f"{entry.path}: {e.strerror or e}")
                continue
            seen += 1
            if on_progress and seen % PROGRESS_EVERY == 0:
                on_progress(seen, abs_dir)
            sidecar = choose_sidecar(entry.name, xmp_names, raw_stems) if xmps else None
            side_mtime = None
            if sidecar:
                try:
                    side_mtime = _iso_utc(xmps[sidecar.lower()].stat(follow_symlinks=False).st_mtime)
                except OSError:
                    sidecar = None
            yield rel, entry.name, st.st_size, _iso_utc(st.st_mtime), sidecar, side_mtime
    if on_progress:
        on_progress(seen, root)


def _under_any(rel_path: str, dirs) -> bool:
    for d in dirs:
        if d == "" or rel_path.startswith(d + "/"):
            return True
    return False


# --- skipped (excluded) folders ---------------------------------------------------

def excluded_folders(conn: sqlite3.Connection, root_id: int | None = None) -> list[tuple[int, str]]:
    """[(root id, rel path)] of the folders the user told Lunelis to skip."""
    if root_id is None:
        return [tuple(r) for r in conn.execute(
            "SELECT root_id, rel_path FROM excluded_folders ORDER BY root_id, rel_path")]
    return [tuple(r) for r in conn.execute(
        "SELECT root_id, rel_path FROM excluded_folders WHERE root_id = ? ORDER BY rel_path", (root_id,))]


def apply_exclusions(conn: sqlite3.Connection, root_id: int) -> int:
    """Set aside (excluded = 1) the files under a root's skipped folders and
    bring back the rest. No disk access - takes effect at once. Returns how
    many files are now set aside."""
    dirs = [rel.lower() for _, rel in excluded_folders(conn, root_id)]
    rows = conn.execute("SELECT id, rel_path, excluded FROM files WHERE root_id = ?", (root_id,)).fetchall()
    changes, hidden = [], 0
    for fid, rel, was in rows:
        now = int(_under_any(rel.lower(), dirs))
        hidden += now
        if now != was:
            changes.append((now, fid))
    conn.executemany("UPDATE files SET excluded = ? WHERE id = ?", changes)
    conn.commit()
    return hidden


def exclude_folder(conn: sqlite3.Connection, root_id: int, rel_path: str) -> int:
    """Skip a folder (and everything under it) from now on. Nothing on disk
    is touched; cataloged files there are set aside with their ratings kept.
    Returns the number of files set aside for this root."""
    rel = rel_path.replace("\\", "/").strip("/")
    if not rel:
        raise ValueError("to skip a whole source, turn the source off instead")
    conn.execute("INSERT OR IGNORE INTO excluded_folders (root_id, rel_path) VALUES (?, ?)", (root_id, rel))
    conn.commit()
    return apply_exclusions(conn, root_id)


def include_folder(conn: sqlite3.Connection, root_id: int, rel_path: str) -> int:
    """Stop skipping a folder. Files cataloged before come back at once; new
    ones there appear with the next scan."""
    conn.execute("DELETE FROM excluded_folders WHERE root_id = ? AND rel_path = ?", (root_id, rel_path))
    conn.commit()
    return apply_exclusions(conn, root_id)


# --- scan ------------------------------------------------------------------

def scan_root(conn: sqlite3.Connection, root_id: int, *,
              on_progress: ProgressFn | None = None,
              should_cancel: CancelFn | None = None) -> ScanResult:
    """Bring `files` for one root in line with the disk. See module docstring."""
    started = time.perf_counter()
    row = conn.execute("SELECT path FROM roots WHERE id = ?", (root_id,)).fetchone()
    if row is None:
        raise KeyError(f"No root with id {root_id}")
    root = row[0]
    if not os.path.isdir(root):
        # Never treat an unreachable root as empty - that would flag the whole
        # library as missing the first time the NAS is asleep.
        raise RootUnavailable(f"Root is offline or gone: {root}")

    result = ScanResult(root_id=root_id)
    known = {
        r[0]: (r[1], r[2], r[3], r[4], r[5])
        for r in conn.execute(
            "SELECT rel_path, size_bytes, mtime, missing_since, sidecar, sidecar_mtime"
            " FROM files WHERE root_id = ?",
            (root_id,),
        )
    }
    # Quarantined files were moved out on purpose - never "missing".
    quarantined = {r[0] for r in conn.execute(
        "SELECT rel_path FROM files WHERE root_id = ? AND quarantined_at IS NOT NULL", (root_id,))}
    found: set[str] = set()
    errored_dirs: list[str] = []
    skip = frozenset(rel.lower() for _, rel in excluded_folders(conn, root_id))
    # Originals left in place by a migration ("keep until I review") now have
    # their catalog entry in the target - don't catalog them again here.
    from lunelis.migrate.execute import kept_sources
    migrated = kept_sources(conn, root_id)
    inserts: list[tuple] = []
    updates: list[tuple] = []
    restores: list[tuple] = []
    sidecars: list[tuple] = []

    def flush() -> None:
        if inserts:
            conn.executemany(
                "INSERT INTO files (root_id, rel_path, filename, ext, size_bytes, mtime, is_raw,"
                " sidecar, sidecar_mtime) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                inserts,
            )
        if sidecars:
            # A sidecar appearing, changing or vanishing says nothing about the
            # photo's own bytes - nothing derived from them is invalidated.
            conn.executemany(
                "UPDATE files SET sidecar = ?, sidecar_mtime = ? WHERE root_id = ? AND rel_path = ?",
                sidecars,
            )
        if updates:
            # Content changed: anything derived from the old bytes is stale.
            conn.executemany(
                "UPDATE files SET size_bytes = ?, mtime = ?, missing_since = NULL,"
                " content_hash = NULL, sample_hash = NULL, perceptual_hash = NULL, thumbnail_path = NULL,"
                " thumb_error = NULL"
                " WHERE root_id = ? AND rel_path = ?",
                updates,
            )
        if restores:
            conn.executemany(
                "UPDATE files SET missing_since = NULL WHERE root_id = ? AND rel_path = ?",
                restores,
            )
        conn.commit()
        inserts.clear()
        updates.clear()
        restores.clear()
        sidecars.clear()

    for rel, name, size, mtime, sidecar, side_mtime in _walk(
            root, result, errored_dirs, on_progress, should_cancel, skip):
        found.add(rel)
        prev = known.get(rel)
        if prev is None and rel in migrated:
            continue
        if prev is not None and (prev[3], prev[4]) != (sidecar, side_mtime):
            sidecars.append((sidecar, side_mtime, root_id, rel))
            result.sidecars_changed += 1
        if prev is None:
            inserts.append((root_id, rel, name, ext_of(name), size, mtime, int(is_raw(name)),
                            sidecar, side_mtime))
            result.added += 1
        elif (prev[0], prev[1]) != (size, mtime):
            updates.append((size, mtime, root_id, rel))
            result.updated += 1
        elif prev[2] is not None:
            restores.append((root_id, rel))
            result.restored += 1
        else:
            result.unchanged += 1
        if len(inserts) + len(updates) + len(restores) + len(sidecars) >= BATCH_SIZE:
            flush()
    flush()

    # Only a complete walk can say a file is gone. A cancelled scan, or a
    # folder we couldn't read, says nothing about the files under it.
    if not result.cancelled:
        now = _now()
        gone = [
            (now, root_id, rel)
            for rel, (_, _, missing_since, _, _) in known.items()
            if rel not in found and missing_since is None and rel not in quarantined
            and not _under_any(rel, errored_dirs)
            and not _under_any(rel.lower(), skip)     # skipped, not gone
        ]
        conn.executemany(
            "UPDATE files SET missing_since = ? WHERE root_id = ? AND rel_path = ?", gone
        )
        result.missing = len(gone)
        conn.execute("UPDATE roots SET last_scanned_at = ? WHERE id = ?", (now, root_id))
        conn.commit()
    if skip:
        apply_exclusions(conn, root_id)

    result.seconds = time.perf_counter() - started
    return result


def catalog_file(conn: sqlite3.Connection, path: str | Path) -> int | None:
    """Catalog one new file (a merge Lunelis just saved) without rescanning
    its whole root. Returns its id, or None when it isn't inside an enabled
    source (then it shows up only if that folder is added)."""
    full = os.path.normcase(os.path.abspath(str(path)))
    for rid, root in conn.execute("SELECT id, path FROM roots WHERE enabled = 1"):
        base = os.path.normcase(os.path.abspath(root)).rstrip("\\/") + os.sep
        if not full.startswith(base):
            continue
        rel = os.path.relpath(os.path.abspath(str(path)), os.path.abspath(root)).replace("\\", "/")
        if _under_any(rel.lower(), [r.lower() for _, r in excluded_folders(conn, rid)]):
            return None
        row = conn.execute("SELECT id FROM files WHERE root_id = ? AND rel_path = ?", (rid, rel)).fetchone()
        if row:
            return row[0]
        st = os.stat(path)
        name = os.path.basename(str(path))
        cur = conn.execute(
            "INSERT INTO files (root_id, rel_path, filename, ext, size_bytes, mtime, is_raw) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (rid, rel, name, ext_of(name), st.st_size, _iso_utc(st.st_mtime), int(is_raw(name))))
        conn.commit()
        return cur.lastrowid
    return None


def catalog_stats(conn: sqlite3.Connection) -> dict[str, int]:
    """Counts for the shell's status display."""
    r = conn.execute(
        "SELECT COUNT(*),"
        " COALESCE(SUM(is_raw), 0),"
        " COALESCE(SUM(missing_since IS NOT NULL), 0),"
        " COALESCE(SUM(excluded = 1 AND missing_since IS NULL), 0),"
        " COALESCE(SUM(size_bytes), 0)"
        " FROM files"
    ).fetchone()
    roots = conn.execute("SELECT COUNT(*) FROM roots").fetchone()[0]
    return {"roots": roots, "files": r[0], "raw": r[1], "missing": r[2], "excluded": r[3], "bytes": r[4]}


def _main(argv: list[str]) -> int:
    import argparse

    from lunelis.catalog.schema import open_catalog
    from lunelis.paths import DEFAULT_CATALOG_PATH

    ap = argparse.ArgumentParser(prog="python -m lunelis.importers.scan",
                                 description="Add a folder root (if new) and scan it.")
    ap.add_argument("folder")
    ap.add_argument("--db", default=str(DEFAULT_CATALOG_PATH), help="catalog path")
    args = ap.parse_args(argv)

    conn = open_catalog(args.db)
    try:
        root_id = add_root(conn, args.folder)

        def progress(n: int, where: str) -> None:
            print(f"\r  {n:>8,} files  {where[-60:]:<60}", end="", flush=True)

        r = scan_root(conn, root_id, on_progress=progress)
        print()
        print(f"root {root_id}: +{r.added:,} new, {r.updated:,} changed, "
              f"{r.unchanged:,} unchanged, {r.restored:,} restored, {r.missing:,} now missing, "
              f"{r.skipped:,} non-photo files skipped, in {r.seconds:.2f}s")
        for e in r.errors[:20]:
            print("  unreadable:", e)
        if len(r.errors) > 20:
            print(f"  ...and {len(r.errors) - 20} more")
        return 0
    except (RootUnavailable, RootOverlap) as e:
        print(e, file=sys.stderr)
        return 2
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
