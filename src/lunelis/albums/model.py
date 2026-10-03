"""
Albums: your own collections, plus automatic ones.

Your albums are hand-made: a photo can be in any number of them (unlike an
event, which a photo belongs to at most one of). Removing an album, or a
photo from an album, never touches a photo.

Automatic albums are saved searches Lunelis keeps up to date by itself -
Favorites, Picks, Videos, RAW files, Recently imported, Screenshots and one
per camera. Each is a SQL condition over the library query's tables
(f = files, e = exif, rt = ratings), so opening one is just a library filter.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterable

LIVE = "f.missing_since IS NULL AND f.excluded = 0 AND f.quarantined_at IS NULL"

# key -> (title, condition, blurb). Conditions must work inside the library's grid query.
AUTO: dict[str, tuple[str, str, str]] = {
    "favorites": ("Favorites", "rt.stars >= 4", "4 and 5 stars"),
    "picks": ("Picks", "rt.flag = 'pick'", "Everything you picked"),
    "videos": ("Videos", "f.format IN ('mp4', 'mov', 'mpeg-ts')", "Every video"),
    "raw": ("RAW files", "f.is_raw = 1", "Straight from the camera"),
    "recent": ("Recently imported", "f.imported_at >= datetime('now', '-30 days')", "The last 30 days"),
    "screenshots": ("Screenshots", "(f.filename LIKE '%screenshot%' OR f.rel_path LIKE '%screenshot%')",
                    "Screen grabs"),
    "archive": ("Archive", "f.archived_at IS NOT NULL", "Out of the library, still kept"),
}
CAMERA_PREFIX = "camera:"
TOP_CAMERAS = 8


def auto_condition(key: str) -> tuple[str, list]:
    """SQL condition + params for an automatic album key (incl. 'camera:<model>')."""
    if key.startswith(CAMERA_PREFIX):
        return "e.camera_model = ?", [key[len(CAMERA_PREFIX):]]
    return AUTO[key][1], []


@dataclass
class Album:
    kind: str                 # 'album' | 'event' | 'auto'
    key: str                  # album/event id as text, or the auto key
    name: str
    count: int
    cover_id: int | None      # a file id to show as the cover
    start_at: str | None = None
    end_at: str | None = None
    blurb: str = ""


def _clean(name: str) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise ValueError("an album needs a name")
    return name


def create(conn: sqlite3.Connection, name: str, file_ids: Iterable[int] = ()) -> int:
    aid = conn.execute("INSERT INTO albums (name, updated_at) VALUES (?, datetime('now'))",
                       (_clean(name),)).lastrowid
    add_files(conn, aid, file_ids)
    return aid


def add_files(conn: sqlite3.Connection, album_id: int, file_ids: Iterable[int]) -> int:
    ids = list(dict.fromkeys(file_ids))
    start = conn.execute("SELECT COALESCE(MAX(position), 0) FROM album_files WHERE album_id = ?",
                         (album_id,)).fetchone()[0]
    before = conn.total_changes
    conn.executemany("INSERT OR IGNORE INTO album_files (album_id, file_id, position) VALUES (?, ?, ?)",
                     [(album_id, fid, start + n + 1) for n, fid in enumerate(ids)])
    added = conn.total_changes - before
    conn.execute("UPDATE albums SET updated_at = datetime('now') WHERE id = ?", (album_id,))
    conn.commit()
    return added


def remove_files(conn: sqlite3.Connection, album_id: int, file_ids: Iterable[int]) -> int:
    ids = list(file_ids)
    n = 0
    for start in range(0, len(ids), 900):
        chunk = ids[start:start + 900]
        n += conn.execute(f"DELETE FROM album_files WHERE album_id = ? AND file_id IN "
                          f"({','.join('?' * len(chunk))})", (album_id, *chunk)).rowcount
    conn.execute("UPDATE albums SET cover_file_id = NULL WHERE id = ? AND cover_file_id NOT IN "
                 "(SELECT file_id FROM album_files WHERE album_id = ?)", (album_id, album_id))
    conn.commit()
    return n


def rename(conn: sqlite3.Connection, album_id: int, name: str) -> None:
    conn.execute("UPDATE albums SET name = ?, updated_at = datetime('now') WHERE id = ?", (_clean(name), album_id))
    conn.commit()


def set_cover(conn: sqlite3.Connection, album_id: int, file_id: int) -> None:
    conn.execute("UPDATE albums SET cover_file_id = ? WHERE id = ?", (file_id, album_id))
    conn.commit()


def delete(conn: sqlite3.Connection, album_id: int) -> None:
    """Remove the album. Its photos stay exactly as they are."""
    conn.execute("DELETE FROM albums WHERE id = ?", (album_id,))   # cascades album_files
    conn.commit()


def albums_of(conn: sqlite3.Connection, file_id: int) -> list[tuple[int, str]]:
    return [tuple(r) for r in conn.execute(
        "SELECT a.id, a.name FROM album_files af JOIN albums a ON a.id = af.album_id"
        " WHERE af.file_id = ? AND a.is_smart = 0 ORDER BY a.name COLLATE NOCASE", (file_id,))]


# --- listing (for the Albums page) ------------------------------------------------------

def your_albums(conn: sqlite3.Connection) -> list[Album]:
    rows = conn.execute(
        f"SELECT a.id, a.name, a.cover_file_id,"
        f"  (SELECT COUNT(*) FROM album_files af JOIN files f ON f.id = af.file_id"
        f"   WHERE af.album_id = a.id AND {LIVE}),"
        f"  (SELECT af.file_id FROM album_files af JOIN files f ON f.id = af.file_id"
        f"   WHERE af.album_id = a.id AND {LIVE} ORDER BY af.position DESC LIMIT 1),"
        f"  (SELECT MIN(e.captured_at) FROM album_files af JOIN exif e ON e.file_id = af.file_id"
        f"   WHERE af.album_id = a.id),"
        f"  (SELECT MAX(e.captured_at) FROM album_files af JOIN exif e ON e.file_id = af.file_id"
        f"   WHERE af.album_id = a.id)"
        f" FROM albums a WHERE a.is_smart = 0 ORDER BY COALESCE(a.updated_at, a.created_at) DESC").fetchall()
    return [Album("album", str(r[0]), r[1], r[3], r[2] or r[4], r[5], r[6]) for r in rows]


def event_albums(conn: sqlite3.Connection) -> list[Album]:
    from lunelis.events import model as events
    out = []
    for e in events.all_events(conn):
        cover = conn.execute(
            f"SELECT ef.file_id FROM event_files ef JOIN files f ON f.id = ef.file_id"
            f" LEFT JOIN ratings rt ON rt.file_id = f.id WHERE ef.event_id = ? AND {LIVE}"
            f" ORDER BY COALESCE(rt.stars, 0) DESC, f.id LIMIT 1", (e.id,)).fetchone()
        out.append(Album("event", str(e.id), e.name, e.photos, cover[0] if cover else None, e.start_at, e.end_at))
    return out


def auto_albums(conn: sqlite3.Connection) -> list[Album]:
    """The automatic albums that have something in them, then the top cameras.
    ~1 s on a 159k library - the Albums page runs it off the GUI thread."""
    everything = (f"FROM files f JOIN roots r ON r.id = f.root_id LEFT JOIN exif e ON e.file_id = f.id"
                  f" LEFT JOIN ratings rt ON rt.file_id = f.id WHERE {LIVE} AND r.enabled = 1")
    base = everything + " AND f.archived_at IS NULL"          # the library: archived photos left out
    out = []
    total = conn.execute(f"SELECT COUNT(*) {base}").fetchone()[0]
    # Covers: the newest photo with a thumbnail, not from a Google Takeout
    # export (re-encoded copies there can have lost their rotation).
    from lunelis.importers.takeout import takeout_roots
    skip = ",".join(str(r) for r in takeout_roots(conn)) or "NULL"
    pick = f"MAX(CASE WHEN f.thumbnail_path IS NOT NULL AND f.root_id NOT IN ({skip}) THEN f.id END)"
    for key, (title, cond, blurb) in AUTO.items():
        n, cover = conn.execute(
            f"SELECT COUNT(*), {pick} {everything if key == 'archive' else base} AND {cond}"
        ).fetchone()
        # An album that is the whole library says nothing (e.g. "Recently
        # imported" in the first month, when everything was just cataloged).
        if n and n < total:
            out.append(Album("auto", key, title, n, cover, blurb=blurb))
    for model, n, cover in conn.execute(
            f"SELECT e.camera_model, COUNT(*), {pick} {base}"
            f" AND e.camera_model IS NOT NULL AND TRIM(e.camera_model) != ''"
            f" GROUP BY e.camera_model ORDER BY COUNT(*) DESC LIMIT ?", (TOP_CAMERAS,)):
        from lunelis.ui.photoinfo import CAMERA_NAMES
        out.append(Album("auto", CAMERA_PREFIX + model, CAMERA_NAMES.get(model.upper(), model), n, cover,
                         blurb="Camera"))
    return out
