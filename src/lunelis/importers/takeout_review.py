"""
The Google Takeout review (0.42): what to bring into the new Library from a
Takeout export that Unpacker V2 already extracted.

Every photo and video in a Takeout source (takeout.takeout_roots) is listed
by year and album, ticked or unticked. What's already in the library - an
identical copy, or the same picture (a near-duplicate: Google re-encodes
uploads) outside the Takeout folders - is marked and unticked to begin with.
Everything else starts ticked; `-edited` copies (Google's edits) and the
`(1)` names Google gives to repeats are marked so they can be looked at.

The answers are stored (`takeout_choices`); a migration leaves unticked
Takeout items where they are, and its accounted-for report says so. The
JSON files never go to the Library - their dates, places and descriptions
are already in the catalog (takeout.py).
"""
from __future__ import annotations

import os
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass

YEAR_FOLDER = re.compile(r"^Photos from (\d{4})$", re.I)
NUMBERED = re.compile(r"\(\d+\)\.[^.]+$")


@dataclass
class Item:
    file_id: int
    root_id: int
    rel_path: str
    filename: str
    year: str                 # from the capture date (else the folder), "Undated" without one
    album: str                # the album folder, or "Photos from <year>" for Google's year folders
    in_library: bool          # an identical or near-identical copy outside the Takeout folders
    edited: bool              # Google's "-edited" copy
    numbered: bool            # Google's "(1)" repeat name
    included: bool            # ticked
    thumbnail: str | None


def _album_of(rel: str) -> str:
    """The album folder a Takeout item sits in: its own folder's name, below
    'Takeout/Google Photos' if that's in the path."""
    parts = rel.split("/")[:-1]
    low = [p.lower() for p in parts]
    if "google photos" in low:
        parts = parts[low.index("google photos") + 1:]
    return parts[-1] if parts else "Google Photos"


def _outside_copies(conn: sqlite3.Connection, takeout: set[int]) -> set[int]:
    """Takeout files with an identical or near-identical copy in a non-Takeout source."""
    rows = conn.execute(
        "SELECT m.group_id, f.id, f.root_id FROM duplicate_group_files m JOIN files f ON f.id = m.file_id"
        " JOIN duplicate_groups g ON g.id = m.group_id"
        " WHERE (g.method = 'similar' OR (g.method = 'exact' AND g.verified = 1))"
        " AND f.missing_since IS NULL AND f.quarantined_at IS NULL").fetchall() if takeout else []
    groups: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for gid, fid, rid in rows:
        groups[gid].append((fid, rid))
    out: set[int] = set()
    for members in groups.values():
        if any(rid not in takeout for _, rid in members):
            out |= {fid for fid, rid in members if rid in takeout}
    return out


def items(conn: sqlite3.Connection) -> list[Item]:
    from lunelis.importers.takeout import takeout_roots
    roots = takeout_roots(conn)
    if not roots:
        return []
    takeout = set(roots)
    already = _outside_copies(conn, takeout)
    chosen = dict(conn.execute("SELECT file_id, include FROM takeout_choices"))
    q = ",".join("?" * len(roots))
    out = []
    for fid, rid, rel, name, taken, thumb in conn.execute(
            f"SELECT f.id, f.root_id, f.rel_path, f.filename, e.captured_at, f.thumbnail_path FROM files f"
            f" LEFT JOIN exif e ON e.file_id = f.id WHERE f.root_id IN ({q}) AND f.missing_since IS NULL"
            f" AND f.excluded = 0 AND f.quarantined_at IS NULL ORDER BY e.captured_at, f.rel_path", roots):
        album = _album_of(rel)
        m = YEAR_FOLDER.match(album)
        year = taken[:4] if taken else (m.group(1) if m else "Undated")
        stem = name.rsplit(".", 1)[0].lower()
        inside = fid in already
        out.append(Item(fid, rid, rel, name, year, album, inside, stem.endswith("-edited"),
                        bool(NUMBERED.search(name)), bool(chosen.get(fid, not inside)), thumb))
    return out


def set_included(conn: sqlite3.Connection, file_ids: list[int], include: bool) -> None:
    conn.executemany("INSERT INTO takeout_choices (file_id, include) VALUES (?, ?)"
                     " ON CONFLICT(file_id) DO UPDATE SET include = excluded.include",
                     [(fid, int(include)) for fid in file_ids])
    conn.commit()


def unticked(conn: sqlite3.Connection, file_ids: list[int] | None = None) -> set[int]:
    """Takeout items not to migrate: unticked by the user, or never answered
    and already in the library."""
    out = {fid for fid, inc in conn.execute("SELECT file_id, include FROM takeout_choices") if not inc}
    answered = {r[0] for r in conn.execute("SELECT file_id FROM takeout_choices")}
    from lunelis.importers.takeout import takeout_roots
    roots = set(takeout_roots(conn))
    if roots:
        for fid in _outside_copies(conn, roots):
            if fid not in answered:
                out.add(fid)
    return out if file_ids is None else out & set(file_ids)


def counts(its: list[Item]) -> dict:
    """{'total', 'included', 'in_library', 'edited', 'numbered'} for the summary line."""
    return {"total": len(its), "included": sum(i.included for i in its),
            "in_library": sum(i.in_library for i in its), "edited": sum(i.edited for i in its),
            "numbered": sum(i.numbered for i in its)}


def tree(its: list[Item]) -> dict[str, dict[str, list[Item]]]:
    """{year: {album: [items]}}, years newest first."""
    out: dict[str, dict[str, list[Item]]] = defaultdict(lambda: defaultdict(list))
    for i in its:
        out[i.year][i.album].append(i)
    return {y: dict(sorted(out[y].items())) for y in sorted(out, reverse=True)}


def path_of(conn: sqlite3.Connection, item: Item) -> str:
    root = conn.execute("SELECT path FROM roots WHERE id = ?", (item.root_id,)).fetchone()[0]
    return os.path.join(root, *item.rel_path.split("/"))
