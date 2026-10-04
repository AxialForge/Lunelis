"""
RAW+JPEG pairs: a camera set to RAW+JPEG writes DSC01234.ARW and
DSC01234.JPG for one shot. Shown as one photo (the RAW, badged "ARW+JPG"),
and what you do to it - stars, labels, flags, archive, albums, tags, events -
happens to both.

files.pair_of links the two both ways (each points at the other). A pair is
two files in the same folder of the same source with the same name apart
from the extension, one RAW and one JPEG/HEIC. A third file with that stem
(a second JPEG, an edited copy) leaves the shot unpaired - Lunelis never
guesses. Rebuilt after each scan (and when the setting changes); nothing
about the files changes.
"""
from __future__ import annotations

import os
import sqlite3

COMPANION_FORMATS = ("jpeg", "heic")
LIVE = "missing_since IS NULL AND excluded = 0 AND quarantined_at IS NULL"


def rebuild(conn: sqlite3.Connection) -> int:
    """Link every RAW+JPEG pair; returns how many pairs."""
    groups: dict[tuple, list[tuple[int, int, str]]] = {}
    for fid, root, rel, is_raw, fmt in conn.execute(
            f"SELECT id, root_id, rel_path, is_raw, format FROM files WHERE {LIVE}"):
        folder, name = os.path.split(rel.replace("\\", "/"))
        stem = os.path.splitext(name)[0].lower()
        groups.setdefault((root, folder.lower(), stem), []).append((fid, is_raw, fmt or ""))
    links = []
    for members in groups.values():
        if len(members) != 2:
            continue
        a, b = members
        raw, other = (a, b) if a[1] else (b, a)
        if raw[1] and not other[1] and other[2] in COMPANION_FORMATS:
            links.append((other[0], raw[0]))
            links.append((raw[0], other[0]))
    conn.execute("UPDATE files SET pair_of = NULL WHERE pair_of IS NOT NULL")
    conn.executemany("UPDATE files SET pair_of = ? WHERE id = ?", [(b, a) for a, b in links])
    conn.commit()
    return len(links) // 2


def rebuild_from_settings(conn: sqlite3.Connection) -> int | None:
    from lunelis.settings import Settings
    if not Settings(conn).get("pair_raw_jpeg"):
        conn.execute("UPDATE files SET pair_of = NULL WHERE pair_of IS NOT NULL")
        conn.commit()
        return None
    return rebuild(conn)


def with_partners(conn: sqlite3.Connection, file_ids) -> list[int]:
    """The ids plus each one's partner, in order, without repeats."""
    ids = list(dict.fromkeys(file_ids))
    seen = set(ids)
    out = list(ids)
    for start in range(0, len(ids), 900):
        chunk = ids[start:start + 900]
        for partner, in conn.execute(
                f"SELECT pair_of FROM files WHERE pair_of IS NOT NULL AND id IN ({','.join('?' * len(chunk))})",
                chunk):
            if partner not in seen:
                seen.add(partner)
                out.append(partner)
    return out


def partner(conn: sqlite3.Connection, file_id: int) -> int | None:
    row = conn.execute("SELECT pair_of FROM files WHERE id = ?", (file_id,)).fetchone()
    return row[0] if row else None
