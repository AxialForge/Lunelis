"""
Events (Phase 2, step 1): trips, shoots and days out as catalog objects.

An event is a name plus the photos in it; its date range is always the
capture-time range of those photos (kept in events.start_at / end_at). A photo
is in at most one event - adding it to another moves it. Removing an event
never touches a photo, on disk or in the catalog.

Events are made from a selection, from a named card import, or by accepting a
suggestion (lunelis.events.suggest: existing folder names, gaps in capture
time). Storage templates file an event's photos by its START date, so a night
shoot or a week-long trip lands in one folder.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

SOURCES = ("manual", "folder", "suggested", "import")


@dataclass
class Event:
    id: int
    name: str
    start_at: str | None
    end_at: str | None
    source: str
    photos: int

    def dates(self) -> str:
        return date_range_text(self.start_at, self.end_at)


def _dt(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso[:19])
    except ValueError:
        return None


def date_range_text(start: str | None, end: str | None) -> str:
    """'Jun 19, 2026' · 'Jun 19 - 21, 2026' · 'Jun 30 - Jul 2, 2026' · 'Dec 30, 2025 - Jan 2, 2026'."""
    a, b = _dt(start), _dt(end)
    if a is None:
        return "No date"
    if b is None or b.date() == a.date():
        return f"{a:%b} {a.day}, {a.year}"
    if a.year != b.year:
        return f"{a:%b} {a.day}, {a.year} - {b:%b} {b.day}, {b.year}"
    if a.month != b.month:
        return f"{a:%b} {a.day} - {b:%b} {b.day}, {a.year}"
    return f"{a:%b} {a.day} - {b.day}, {a.year}"


def _clean(name: str) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise ValueError("an event needs a name")
    return name


def refresh_range(conn: sqlite3.Connection, event_id: int) -> None:
    conn.execute(
        "UPDATE events SET (start_at, end_at) = ("
        "  SELECT MIN(e.captured_at), MAX(e.captured_at) FROM event_files ef"
        "  JOIN exif e ON e.file_id = ef.file_id WHERE ef.event_id = ?)"
        " WHERE id = ?", (event_id, event_id))


def create(conn: sqlite3.Connection, name: str, file_ids: Iterable[int] = (), source: str = "manual") -> int:
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}")
    cur = conn.execute("INSERT INTO events (name, source) VALUES (?, ?)", (_clean(name), source))
    event_id = cur.lastrowid
    add_files(conn, event_id, file_ids, commit=False)
    conn.commit()
    return event_id


def add_files(conn: sqlite3.Connection, event_id: int, file_ids: Iterable[int], *, commit: bool = True) -> int:
    """Put photos in an event (moving them out of any other). Returns how many."""
    ids = list(dict.fromkeys(file_ids))
    if not ids:
        refresh_range(conn, event_id)
        if commit:
            conn.commit()
        return 0
    touched = {event_id}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        q = ",".join("?" * len(chunk))
        touched.update(r[0] for r in conn.execute(
            f"SELECT DISTINCT event_id FROM event_files WHERE file_id IN ({q})", chunk))
    conn.executemany(
        "INSERT INTO event_files (file_id, event_id) VALUES (?, ?)"
        " ON CONFLICT(file_id) DO UPDATE SET event_id = excluded.event_id",
        [(fid, event_id) for fid in ids])
    for eid in touched:
        refresh_range(conn, eid)
    _drop_empty(conn, touched - {event_id})
    if commit:
        conn.commit()
    return len(ids)


def remove_files(conn: sqlite3.Connection, file_ids: Iterable[int]) -> int:
    """Take photos out of whatever event they're in. An event left with no
    photos is removed (it would be a name with no date)."""
    ids = list(dict.fromkeys(file_ids))
    touched: set[int] = set()
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        q = ",".join("?" * len(chunk))
        touched.update(r[0] for r in conn.execute(
            f"SELECT DISTINCT event_id FROM event_files WHERE file_id IN ({q})", chunk))
        conn.execute(f"DELETE FROM event_files WHERE file_id IN ({q})", chunk)
    for eid in touched:
        refresh_range(conn, eid)
    _drop_empty(conn, touched)
    conn.commit()
    return len(ids)


def _drop_empty(conn: sqlite3.Connection, event_ids: Iterable[int]) -> None:
    for eid in event_ids:
        if not conn.execute("SELECT 1 FROM event_files WHERE event_id = ? LIMIT 1", (eid,)).fetchone():
            conn.execute("UPDATE imports SET event_id = NULL WHERE event_id = ?", (eid,))
            conn.execute("DELETE FROM events WHERE id = ?", (eid,))


def rename(conn: sqlite3.Connection, event_id: int, name: str) -> None:
    """Renames the event in the catalog. Folders on disk are not renamed
    (that will be an optional move job, with migration)."""
    conn.execute("UPDATE events SET name = ? WHERE id = ?", (_clean(name), event_id))
    conn.commit()


def delete(conn: sqlite3.Connection, event_id: int) -> None:
    """Remove the event. Its photos stay exactly where and as they are."""
    conn.execute("UPDATE imports SET event_id = NULL WHERE event_id = ?", (event_id,))
    conn.execute("DELETE FROM events WHERE id = ?", (event_id,))   # cascades event_files
    conn.commit()


def all_events(conn: sqlite3.Connection) -> list[Event]:
    """Newest first. Photo counts are of live files (not missing, skipped or quarantined)."""
    rows = conn.execute(
        "SELECT ev.id, ev.name, ev.start_at, ev.end_at, ev.source,"
        "  (SELECT COUNT(*) FROM event_files ef JOIN files f ON f.id = ef.file_id"
        "   WHERE ef.event_id = ev.id AND f.missing_since IS NULL AND f.excluded = 0"
        "   AND f.quarantined_at IS NULL)"
        " FROM events ev ORDER BY ev.start_at DESC NULLS LAST, ev.id DESC").fetchall()
    return [Event(*r) for r in rows]


def get(conn: sqlite3.Connection, event_id: int) -> Event | None:
    return next((e for e in all_events(conn) if e.id == event_id), None)


def events_of(conn: sqlite3.Connection, file_ids: Iterable[int]) -> dict[int, int]:
    """{file id: event id} for those of the files that are in an event."""
    ids = list(file_ids)
    out: dict[int, int] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        q = ",".join("?" * len(chunk))
        out.update(conn.execute(f"SELECT file_id, event_id FROM event_files WHERE file_id IN ({q})", chunk))
    return out


# --- named imports ------------------------------------------------------------------

def import_file_ids(conn: sqlite3.Connection, import_id: int, roots=None) -> list[int]:
    """Catalog ids of an import's placed photos (empty until the scan after
    the import has cataloged them)."""
    if roots is None:
        roots = [(rid, os.path.normcase(os.path.normpath(p)))
                 for rid, p in conn.execute("SELECT id, path FROM roots")]
    file_ids = []
    for (dest,) in conn.execute("SELECT dest_path FROM import_items WHERE import_id = ?"
                                " AND state IN ('placed', 'already_in_library') AND dest_path IS NOT NULL",
                                (import_id,)):
        norm = os.path.normcase(os.path.normpath(dest))
        for rid, base in roots:
            if norm.startswith(base.rstrip("\\") + "\\"):
                rel = os.path.relpath(os.path.normpath(dest), base).replace("\\", "/")
                row = conn.execute("SELECT id FROM files WHERE root_id = ? AND rel_path = ?",
                                   (rid, rel)).fetchone() or conn.execute(
                    "SELECT id FROM files WHERE root_id = ? AND rel_path = ? COLLATE NOCASE",
                    (rid, rel)).fetchone()
                if row:
                    file_ids.append(row[0])
                break
    return file_ids


def link_imports(conn: sqlite3.Connection) -> int:
    """Join the photos of finished, named imports to their event, once they've
    been cataloged by the scan that follows an import. Returns photos linked."""
    roots = [(rid, os.path.normcase(os.path.normpath(p)))
             for rid, p in conn.execute("SELECT id, path FROM roots")]
    linked = 0
    for imp, name, event_id in conn.execute(
            "SELECT id, name, event_id FROM imports WHERE state = 'done' AND TRIM(COALESCE(name, '')) != ''"
            " AND event_id IS NULL").fetchall():
        file_ids = import_file_ids(conn, imp, roots)
        if not file_ids:
            continue                   # not cataloged yet (destination outside every source?)
        eid = create(conn, name, file_ids, source="import")
        conn.execute("UPDATE imports SET event_id = ? WHERE id = ?", (eid, imp))
        conn.commit()
        linked += len(file_ids)
    return linked
