"""
Ratings, flags and colour labels in the catalog (Phase 1, Step 6).

The catalog is the fast copy; the XMP sidecar is the portable one. Every
change here sets `xmp_pending`, and xmp.sync.export_pending() writes it out
in the background, so rating a hundred NAS photos never waits on the network.
"""
from __future__ import annotations

import sqlite3
from typing import Iterable

from lunelis.xmp.sidecar import LABELS

FLAGS = ("pick", "reject")
_UNSET = object()


def snapshot(conn: sqlite3.Connection, file_ids: Iterable[int]) -> dict[int, tuple]:
    """{file id: (stars, flag, label)} as they are now - what undo puts back."""
    ids = list(file_ids)
    out = {fid: (0, None, None) for fid in ids}
    for start in range(0, len(ids), 900):
        chunk = ids[start:start + 900]
        for fid, stars, flag, label in conn.execute(
                f"SELECT file_id, stars, flag, color_label FROM ratings"
                f" WHERE file_id IN ({','.join('?' * len(chunk))})", chunk):
            out[fid] = (stars or 0, flag, label)
    return out


def restore(conn: sqlite3.Connection, snap: dict[int, tuple]) -> int:
    """Put a snapshot back (sidecars follow, as for any change)."""
    groups: dict[tuple, list[int]] = {}
    for fid, values in snap.items():
        groups.setdefault(values, []).append(fid)
    for (stars, flag, label), ids in groups.items():
        set_ratings(conn, ids, stars=stars, flag=flag, label=label)
    return len(snap)


def set_ratings(conn: sqlite3.Connection, file_ids: Iterable[int], *,
                stars: int | None = None, flag=_UNSET, label=_UNSET) -> int:
    """Update the given files; only the arguments passed change. Returns count.

    stars: 0-5 · flag: 'pick' | 'reject' | None · label: one of LABELS | None
    """
    if stars is not None and not 0 <= stars <= 5:
        raise ValueError("stars must be 0-5")
    if flag is not _UNSET and flag not in (None, *FLAGS):
        raise ValueError(f"flag must be one of {FLAGS} or None")
    if label is not _UNSET and label not in (None, *LABELS):
        raise ValueError(f"label must be one of {LABELS} or None")
    ids = [(fid,) for fid in file_ids]
    if not ids:
        return 0
    conn.executemany("INSERT OR IGNORE INTO ratings (file_id) VALUES (?)", ids)
    sets, params = ["xmp_pending = 1", "updated_at = datetime('now')"], []
    if stars is not None:
        sets.append("stars = ?")
        params.append(stars)
    if flag is not _UNSET:
        sets.append("flag = ?")
        params.append(flag)
    if label is not _UNSET:
        sets.append("color_label = ?")
        params.append(label)
    conn.executemany(f"UPDATE ratings SET {', '.join(sets)} WHERE file_id = ?",
                     [(*params, fid) for (fid,) in ids])
    conn.commit()
    return len(ids)


def pending_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM ratings WHERE xmp_pending = 1").fetchone()[0]
