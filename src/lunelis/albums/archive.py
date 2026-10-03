"""
The Archive: photos you want to keep but not see.

Archiving only sets `files.archived_at` (migration 26). An archived photo
leaves the Library grid, search, filters, events and the automatic albums,
but stays in any album you put it in and in the Archive album, and its file
doesn't move. Bringing it back clears the mark.

Moving archived photos to an archive drive is a separate, optional step
(the "archive_move" job); this module never touches files.
"""
from __future__ import annotations

import sqlite3

CONDITION = "f.archived_at IS NOT NULL"
NOT_ARCHIVED = "f.archived_at IS NULL"


def _chunks(ids: list[int], size: int = 900):
    for i in range(0, len(ids), size):
        yield ids[i:i + size]


def archive(conn: sqlite3.Connection, file_ids: list[int]) -> int:
    """Archive these photos. Returns how many weren't archived yet."""
    n = 0
    for chunk in _chunks(list(file_ids)):
        n += conn.execute(
            f"UPDATE files SET archived_at = datetime('now') WHERE archived_at IS NULL"
            f" AND id IN ({','.join('?' * len(chunk))})", chunk).rowcount
    conn.commit()
    return n


def unarchive(conn: sqlite3.Connection, file_ids: list[int]) -> int:
    """Bring these photos back into the library. Returns how many were archived."""
    n = 0
    for chunk in _chunks(list(file_ids)):
        n += conn.execute(
            f"UPDATE files SET archived_at = NULL WHERE archived_at IS NOT NULL"
            f" AND id IN ({','.join('?' * len(chunk))})", chunk).rowcount
    conn.commit()
    return n


def archived_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM files WHERE archived_at IS NOT NULL"
                        " AND missing_since IS NULL AND quarantined_at IS NULL").fetchone()[0]


def split(conn: sqlite3.Connection, file_ids: list[int]) -> tuple[list[int], list[int]]:
    """(archived, not archived) among these ids - for menus that toggle."""
    archived: set[int] = set()
    for chunk in _chunks(list(file_ids)):
        archived |= {r[0] for r in conn.execute(
            f"SELECT id FROM files WHERE archived_at IS NOT NULL AND id IN ({','.join('?' * len(chunk))})", chunk)}
    return [i for i in file_ids if i in archived], [i for i in file_ids if i not in archived]
