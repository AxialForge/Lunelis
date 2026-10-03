"""
The damaged-file check (Phase 1, Step 9).

Finds files that can't be what they claim, and where an intact copy survives.

Almost everything is already known from earlier passes, so the check reads
very little:
  zero_bytes       size 0 (catalog only)
  zero_filled      header unrecognisable AND the sampled slices are all zeros
                   - a zero-filled file can never pass sniff(), so only files
                   with an unrecognised header are ever read (~200 KB each)
  unrecognised     header unrecognisable, but not zeros
  truncated        thumbnail decode ran out of data
  corrupt          thumbnail decode hit a broken data stream
  changed_on_disk  set by the 'integrity' job: a full re-hash no longer
                   matches the baseline though size and date are unchanged
                   (silent corruption / bit rot)

On the real library (2026-09-27): 41 photos zero-filled identically in both
NAS pools, 2 zero-byte files, 44 corrupt JPEGs, 1 truncated.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass

from lunelis.dupes.hashing import SLICE

LIVE = "f.missing_since IS NULL AND f.excluded = 0 AND f.quarantined_at IS NULL"

PROBLEM_TEXT = {
    "zero_bytes": "Empty file (0 bytes)",
    "zero_filled": "Filled with zeros - the image data is gone",
    "unrecognised": "Not a recognisable photo or video",
    "truncated": "Cut short - the end of the file is missing",
    "corrupt": "Corrupt image data",
    "changed_on_disk": "Contents changed on disk without the file being edited",
}


@dataclass
class CheckResult:
    found: dict
    read_files: int


def _all_zero(path: str, size: int) -> bool:
    with open(path, "rb") as fh:
        offsets = [0] if size <= 3 * SLICE else [0, size // 2 - SLICE // 2, size - SLICE]
        for off in offsets:
            fh.seek(off)
            if fh.read(SLICE).strip(b"\0"):
                return False
    return True


def check(conn: sqlite3.Connection) -> CheckResult:
    """Rebuild the `damaged` table from what the catalog knows, reading only
    the handful of files whose header wasn't recognisable. Dismissals of
    problems that still exist are kept; fixed files drop off."""
    found: dict[int, tuple[str, str | None]] = {}

    for (fid,) in conn.execute(f"SELECT f.id FROM files f WHERE {LIVE} AND f.size_bytes = 0"):
        found[fid] = ("zero_bytes", None)

    reads = 0
    for fid, root, rel, size in conn.execute(
            f"SELECT f.id, r.path, f.rel_path, f.size_bytes FROM files f JOIN roots r ON r.id = f.root_id"
            f" JOIN exif e ON e.file_id = f.id"
            f" WHERE {LIVE} AND f.size_bytes > 0 AND e.read_error LIKE '%unrecognised file contents%'"
    ).fetchall():
        try:
            reads += 1
            zero = _all_zero(os.path.join(root, *rel.split("/")), size)
        except OSError:
            continue                         # unreachable right now: say nothing either way
        found[fid] = ("zero_filled", None) if zero else ("unrecognised", None)

    for fid, err in conn.execute(
            f"SELECT f.id, f.thumb_error FROM files f WHERE {LIVE} AND f.thumb_error IS NOT NULL"):
        if fid in found:
            continue
        if "truncated" in err:
            found[fid] = ("truncated", err[:200])
        elif "broken data stream" in err:
            found[fid] = ("corrupt", err[:200])

    # changed_on_disk rows come from the integrity job; keep them while the
    # file is still live and unchanged (the scanner clears content_hash on a
    # real edit, which is when they stop applying).
    for fid, detail in conn.execute(
            f"SELECT d.file_id, d.detail FROM damaged d JOIN files f ON f.id = d.file_id"
            f" WHERE d.problem = 'changed_on_disk' AND {LIVE}"):
        found.setdefault(fid, ("changed_on_disk", detail))

    existing = {fid: (p, dis) for fid, p, dis in conn.execute("SELECT file_id, problem, dismissed FROM damaged")}
    conn.executemany("DELETE FROM damaged WHERE file_id = ?",
                     [(fid,) for fid in existing if fid not in found])
    conn.executemany(
        "INSERT INTO damaged (file_id, problem, detail) VALUES (?, ?, ?)"
        " ON CONFLICT(file_id) DO UPDATE SET problem = excluded.problem, detail = excluded.detail,"
        " dismissed = CASE WHEN damaged.problem = excluded.problem THEN damaged.dismissed ELSE 0 END",
        [(fid, p, d) for fid, (p, d) in found.items()])
    conn.commit()
    counts: dict[str, int] = {}
    for p, _ in found.values():
        counts[p] = counts.get(p, 0) + 1
    return CheckResult(counts, reads)


# --- where an intact copy survives -------------------------------------------

def _like_prefix(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


def survivors(conn: sqlite3.Connection, file_id: int) -> list[tuple[int, str, str]]:
    """[(file id, full path, why it's a good copy)] - best first.

    - the same file elsewhere: same name and size, not itself damaged
    - the RAW original: same stem in the same folder (for a damaged JPEG)
    - a copy under another name/size with the same name, e.g. a Google
      Takeout re-encode (lower quality, but the picture survives)
    """
    row = conn.execute(
        "SELECT f.filename, f.size_bytes, f.root_id, f.rel_path FROM files f WHERE f.id = ?",
        (file_id,)).fetchone()
    if not row:
        return []
    name, size, root_id, rel = row
    stem = name.rsplit(".", 1)[0].lower()
    folder = rel.rsplit("/", 1)[0] if "/" in rel else ""
    out: list[tuple[int, str, str, int]] = []
    for fid, rpath, frel, fname, fsize, is_raw, fmt in conn.execute(
            f"SELECT f.id, r.path, f.rel_path, f.filename, f.size_bytes, f.is_raw, f.format"
            f" FROM files f JOIN roots r ON r.id = f.root_id"
            f" WHERE {LIVE} AND f.id != ? AND f.format IS NOT NULL"
            f" AND NOT EXISTS (SELECT 1 FROM damaged d WHERE d.file_id = f.id)"
            # Both forms can use idx_files_filename_nocase (LIKE is
            # case-insensitive, and the pattern is a plain prefix).
            f" AND (f.filename = ? COLLATE NOCASE OR f.filename LIKE ? ESCAPE '\\')",
            (file_id, name, _like_prefix(stem) + ".%")):
        full = os.path.join(rpath, *frel.split("/"))
        same_name = fname.lower() == name.lower()
        ffolder = frel.rsplit("/", 1)[0] if "/" in frel else ""
        if same_name and fsize == size:
            out.append((fid, full, "Same file, another location", 0))
        elif is_raw and not same_name and ffolder == folder:
            out.append((fid, full, "The RAW original", 1))
        elif same_name:
            where = "Google Takeout copy (re-encoded)" if "takeout" in full.lower() else "Copy with a different size"
            out.append((fid, full, where, 2))
        elif is_raw:
            out.append((fid, full, "A RAW with the same name elsewhere", 3))
    out.sort(key=lambda t: (t[3], t[1]))
    return [(fid, full, why) for fid, full, why, _ in out]
