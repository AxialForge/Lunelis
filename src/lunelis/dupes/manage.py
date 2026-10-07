"""
Everything in quarantine, in one list - what the Quarantine page shows.

Two kinds of entry:
- a cataloged photo set aside as a copy: an exact duplicate, a
  near-duplicate, or a copy skipped by a migration (files.quarantined_at);
- a migration's original: the catalog entry moved on to the new copy, and
  the old file was renamed into quarantine (migration_items only).

For each, the KEPT copy - the one that made setting this aside safe - is
worked out and checked on disk. From here a user can restore (put it
back), or empty (remove it for good). Emptying is the only irreversible
step anywhere in Lunelis, so it:
- refuses any entry whose kept copy is missing or (for byte copies) a
  different size - that entry stays in quarantine;
- sends files on local drives to the Recycle Bin (undoable) - network
  shares have no Recycle Bin, so there the file is deleted, and the page
  says so first;
- snapshots the catalog first, and logs every file in `purged`.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from lunelis.dupes.quarantine import QUARANTINE_DIR, QuarantineRefused, _now, restore

REASONS = {"duplicate": "Exact copy", "similar": "Near-duplicate", "migration": "Migration copy",
           "original": "Migrated original"}


@dataclass
class Entry:
    key: str                     # "f<file id>" or "m<migration item id>"
    reason: str                  # duplicate | similar | migration | original
    original: str                # where it was
    now: str                     # where it is (in quarantine)
    size: int
    when: str | None
    kept: str | None             # the copy that stays
    kept_size: int | None
    file_id: int | None          # the catalog row (its thumbnail, for a picture)
    thumbnail: str | None
    exact: bool                  # the kept copy is byte-identical (so sizes must match)

    # Disk checks are made once and remembered (probe() on a worker thread):
    # on a NAS each one is a network round trip, and the page asks often.
    _present: bool | None = field(default=None, compare=False, repr=False)
    _kept_ok: bool | None = field(default=None, compare=False, repr=False)

    @property
    def present(self) -> bool:
        if self._present is None:
            self._present = os.path.exists(self.now)
        return self._present

    def kept_ok(self) -> bool:
        if self._kept_ok is None:
            self._kept_ok = self._check_kept()
        return self._kept_ok

    def _check_kept(self) -> bool:
        if not self.kept or not os.path.exists(self.kept):
            return False
        if self.exact and self.kept_size is not None:
            try:
                return os.path.getsize(self.kept) == self.size
            except OSError:
                return False
        return True

    def probe(self) -> "Entry":
        self.present
        self.kept_ok()
        return self


def _path(root: str, rel: str) -> str:
    return os.path.join(root, *rel.split("/"))


def entries(conn: sqlite3.Connection) -> list[Entry]:
    roots = dict(conn.execute("SELECT id, path FROM roots"))
    out: list[Entry] = []
    # 1. Cataloged photos set aside.
    for fid, root_id, rel, qpath, when, size, thumb in conn.execute(
            "SELECT id, root_id, rel_path, quarantine_path, quarantined_at, size_bytes, thumbnail_path"
            " FROM files WHERE quarantined_at IS NOT NULL AND quarantine_path IS NOT NULL").fetchall():
        reason, kept_id, exact = _why(conn, fid)
        kept = kept_size = None
        if kept_id is not None:
            k = conn.execute("SELECT root_id, rel_path, size_bytes FROM files WHERE id = ?", (kept_id,)).fetchone()
            if k:
                kept, kept_size = _path(roots[k[0]], k[1]), k[2]
        out.append(Entry(f"f{fid}", reason, _path(roots.get(root_id, ""), rel), qpath, size or 0, when,
                         kept, kept_size, fid, thumb, exact))
    # 2. Migrated originals (their catalog row now points at the new copy).
    for item_id, fid, src_root, src_rel, qpath, size, state in conn.execute(
            "SELECT i.id, i.file_id, i.src_root, i.src_rel, i.quarantine_path, i.size, i.state"
            " FROM migration_items i WHERE i.action = 'move' AND i.state IN ('done', 'released')"
            " AND i.quarantine_path IS NOT NULL").fetchall():
        k = conn.execute("SELECT root_id, rel_path, size_bytes, thumbnail_path FROM files WHERE id = ?",
                         (fid,)).fetchone()
        kept, kept_size, thumb = (_path(roots[k[0]], k[1]), k[2], k[3]) if k else (None, None, None)
        when = conn.execute("SELECT finished_at FROM migrations m JOIN migration_items i ON i.migration_id = m.id"
                            " WHERE i.id = ?", (item_id,)).fetchone()
        out.append(Entry(f"m{item_id}", "original", _path(roots.get(src_root, ""), src_rel), qpath, size or 0,
                         when[0] if when else None, kept, kept_size, fid, thumb, True))
    out.sort(key=lambda e: (e.when or "", e.original), reverse=True)
    return out


def _why(conn: sqlite3.Connection, fid: int) -> tuple[str, int | None, bool]:
    """(reason, the kept copy's file id, is the kept copy byte-identical)."""
    mi = conn.execute("SELECT keeper_id FROM migration_items WHERE file_id = ? AND quarantine_path IS NOT NULL"
                      " ORDER BY id DESC LIMIT 1", (fid,)).fetchone()
    if mi:
        return "migration", mi[0], True
    for method, gid in conn.execute(
            "SELECT g.method, g.id FROM duplicate_group_files m JOIN duplicate_groups g ON g.id = m.group_id"
            " WHERE m.file_id = ? ORDER BY g.method = 'exact' DESC", (fid,)).fetchall():
        keeper = conn.execute(
            "SELECT f.id FROM duplicate_group_files m JOIN files f ON f.id = m.file_id WHERE m.group_id = ?"
            " AND f.id != ? AND f.quarantined_at IS NULL AND f.missing_since IS NULL ORDER BY f.id LIMIT 1",
            (gid, fid)).fetchone()
        if method in ("exact", "sampled"):
            return "duplicate", keeper[0] if keeper else None, True
        return "similar", keeper[0] if keeper else None, False
    return "duplicate", None, True


def due(items: list[Entry], keep_days: int, now=None) -> list[Entry]:
    """The set-aside files kept longer than `keep_days` (0 = kept forever:
    none). They're only offered: nothing is removed until the user says so."""
    if not keep_days:
        return []
    from datetime import datetime, timedelta, timezone
    now = now or datetime.now(timezone.utc)
    out = []
    for e in items:
        try:
            when = datetime.fromisoformat((e.when or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if now - when >= timedelta(days=keep_days):
            out.append(e)
    return out


def summary(items: list[Entry]) -> dict[str, tuple[int, int]]:
    """Drive -> (files, bytes)."""
    out: dict[str, tuple[int, int]] = {}
    for e in items:
        drive = _drive(e.now)
        n, b = out.get(drive, (0, 0))
        out[drive] = (n + 1, b + e.size)
    return out


def _drive(path: str) -> str:
    p = os.path.abspath(path)
    if p.startswith("\\\\"):
        parts = p.split("\\")
        return "\\\\" + "\\".join(parts[2:4])
    return os.path.splitdrive(p)[0] or p


# --- restoring ----------------------------------------------------------------------------------

def restore_entry(conn: sqlite3.Connection, e: Entry) -> str:
    if e.key.startswith("f"):
        return restore(conn, int(e.key[1:]))
    item_id = int(e.key[1:])
    if os.path.exists(e.original):
        raise QuarantineRefused(f"something already exists at {e.original}")
    os.makedirs(os.path.dirname(e.original), exist_ok=True)
    os.rename(e.now, e.original)
    conn.execute("UPDATE migration_items SET state = 'restored', quarantine_path = NULL,"
                 " note = COALESCE(note || ' - ', '') || 'original restored from quarantine' WHERE id = ?", (item_id,))
    conn.commit()
    return e.original


# --- emptying ------------------------------------------------------------------------------------

def _is_network(path: str) -> bool:
    """No Recycle Bin here: a share, a removable drive or a FAT/exFAT volume.
    Files here are deleted for good, so they get the stricter checks."""
    from lunelis.paths import has_recycle_bin
    return not has_recycle_bin(path)


def _recycle(path: str) -> None:
    """Send a local file to the Recycle Bin (SHFileOperation with undo)."""
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]
    FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 4, 16, 64, 1024
    op = SHFILEOPSTRUCTW(None, FO_DELETE, os.path.abspath(path) + "\0", None,
                         FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI, False, None, None)
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc != 0 or op.fAnyOperationsAborted:
        raise OSError(f"the Recycle Bin refused it (code {rc})")


@dataclass
class EmptyResult:
    recycled: int = 0
    deleted: int = 0
    bytes: int = 0
    skipped: list = None

    def __post_init__(self):
        self.skipped = self.skipped or []


def empty(conn: sqlite3.Connection, items: list[Entry], *, backup_dir: Path | None = None,
          on_progress=None, should_cancel=None) -> EmptyResult:
    """Remove quarantined files for good (local: Recycle Bin). Never one
    whose kept copy isn't there."""
    res = EmptyResult()
    if backup_dir is not None and items:
        from lunelis.catalog.backup import snapshot
        snapshot(conn, backup_dir, "before-empty-quarantine")
    for i, e in enumerate(items, 1):
        if should_cancel and should_cancel():
            break
        if on_progress:
            on_progress(i, len(items))
        if not e.present:
            res.skipped.append((e, "it's no longer in the quarantine folder"))
            continue
        if not e.kept_ok():
            res.skipped.append((e, "its kept copy is missing or different - it stays in quarantine"))
            continue
        side = _sidecars_near(e)
        network = _is_network(e.now)
        if e.exact and not _same_bytes(e.kept, e.now):
            # The kept copy must really be the same bytes, not just the same
            # size, before this one goes (a Recycle Bin gets emptied too).
            res.skipped.append((e, "its kept copy isn't byte-for-byte the same any more - it stays in quarantine"))
            continue
        try:
            if network:
                os.remove(e.now)
                how = "deleted"
                res.deleted += 1
            else:
                _recycle(e.now)
                how = "recycled"
                res.recycled += 1
        except OSError as err:
            res.skipped.append((e, str(err)))
            continue
        res.bytes += e.size
        sha = None
        if e.file_id is not None:
            r = conn.execute("SELECT content_hash FROM files WHERE id = ?", (e.file_id,)).fetchone()
            sha = r[0] if r else None
        conn.execute("INSERT INTO purged (path, original, size, content_hash, reason, kept, how)"
                     " VALUES (?, ?, ?, ?, ?, ?, ?)", (e.now, e.original, e.size, sha, e.reason, e.kept, how))
        if e.key.startswith("f"):
            # The copy is gone: so is its catalog entry (its ratings, labels and
            # albums were merged into the kept copy when it was set aside).
            conn.execute("DELETE FROM files WHERE id = ?", (int(e.key[1:]),))
        else:
            conn.execute("UPDATE migration_items SET state = 'purged', quarantine_path = NULL WHERE id = ?",
                         (int(e.key[1:]),))
        conn.commit()
        # The photo is gone and recorded: its sidecars follow, best effort (one
        # that won't go stays in the quarantine folder; nothing is left unrecorded).
        for sc in side:
            try:
                os.remove(sc) if network else _recycle(sc)
            except FileNotFoundError:
                pass
            except OSError:
                res.sidecars_left = getattr(res, "sidecars_left", 0) + 1
        _prune_empty_dirs(e.now)
    return res


def _same_bytes(a: str | None, b: str) -> bool:
    import hashlib

    def digest(path: str) -> str | None:
        h = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
        except OSError:
            return None
        return h.hexdigest()
    if not a:
        return False
    da = digest(a)
    return da is not None and da == digest(b)


def _sidecars_near(e: Entry) -> list[str]:
    folder, name = os.path.split(e.now)
    stem = os.path.splitext(name)[0]
    out, seen = [], set()
    for cand in (name + ".xmp", stem + ".xmp"):
        p = os.path.join(folder, cand)
        key = os.path.normcase(p)                     # x.xmp and x.XMP are one file on Windows and SMB
        if key not in seen and os.path.exists(p):
            seen.add(key)
            out.append(p)
    return out


def _prune_empty_dirs(path: str) -> None:
    """Remove folders left empty inside the quarantine folder (never above it)."""
    d = os.path.dirname(path)
    while QUARANTINE_DIR in d.split(os.sep):
        try:
            os.rmdir(d)                     # only succeeds when empty
        except OSError:
            return
        if os.path.basename(d) == QUARANTINE_DIR:
            return
        d = os.path.dirname(d)
