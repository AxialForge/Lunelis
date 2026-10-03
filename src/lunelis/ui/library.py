"""
The ordered list of photos the grid shows.

Loaded once per sort/refresh in one query and kept as the raw row tuples -
the grid asks for rows by position only, so there's nothing to build.

Burst stacks collapse here, in Python, after the query: each stack shows
its cover - or, when a filter left the cover out, its first frame that's
still in - and the rest of the frames are skipped until the stack is
expanded. Doing it in SQL would hide a 5-star frame whenever the filter
dropped the cover.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field

from lunelis.xmp.sidecar import LABELS

# (label, ORDER BY). The date key falls back to file mtime so photos without
# EXIF (timelapse frames, screenshots) still land roughly where they belong.
SORTS: dict[str, tuple[str, str]] = {
    # Undated files (sort_date NULL) go last either way.
    "date_desc": ("Date (newest)", "sort_date DESC NULLS LAST, f.id DESC"),
    "date_asc": ("Date (oldest)", "sort_date ASC NULLS LAST, f.id ASC"),
    "name": ("File name", "f.filename COLLATE NOCASE, f.id"),
    "size": ("File size", "f.size_bytes DESC, f.id"),
    "imported": ("Recently imported", "f.imported_at DESC, f.id DESC"),
}

_QUERY = """
    SELECT f.id, f.thumbnail_path, f.thumb_error IS NOT NULL, f.ext, f.format, f.is_raw,
           COALESCE(rt.stars, 0),
           COALESCE(e.captured_at, CASE WHEN f.root_id IN ({untrusted}) THEN NULL ELSE f.mtime END)
               AS sort_date,
           rt.flag, rt.color_label, e.duration_s,
           sf.stack_id, s.size, s.cover_file_id, ed.rev
    FROM files f
    JOIN roots r ON r.id = f.root_id
    -- Forced: the planner prefers the exif primary key, which reads the
    -- ~2 KB rows; the covering index halves the load (497 -> 252 ms, 159k rows).
    LEFT JOIN exif e INDEXED BY idx_exif_file_captured ON e.file_id = f.id
    LEFT JOIN ratings rt ON rt.file_id = f.id
    LEFT JOIN stack_files sf ON sf.file_id = f.id
    LEFT JOIN stacks s ON s.id = sf.stack_id
    LEFT JOIN edits ed ON ed.file_id = f.id
    WHERE f.missing_since IS NULL AND f.excluded = 0 AND f.quarantined_at IS NULL AND r.enabled = 1 {where}
    ORDER BY {order}
"""

VIDEO_FORMATS = {"mp4", "mov", "mpeg-ts"}


# Row layout, as the query returns it.
ID, THUMB, UNAVAILABLE, EXT, FORMAT, IS_RAW, STARS, SORT_DATE, FLAG, LABEL, DURATION, \
    STACK, STACK_SIZE, STACK_COVER, EDIT_REV = range(15)

UNRATED = -1          # Filter.min_stars value meaning "no stars"


def untrusted_mtime_roots(conn: sqlite3.Connection) -> list[int]:
    """Roots where a file's modified date means nothing: a Google Takeout
    export's files all carry the day it was unzipped, so an undated file
    there would otherwise sort as the newest photo in the library."""
    from lunelis.importers.takeout import takeout_roots
    return takeout_roots(conn)


@dataclass(frozen=True)
class Filter:
    min_stars: int = 0            # 0 = any, 1-5 = at least, UNRATED = exactly none
    flag: str | None = None       # 'pick' | 'reject'
    label: str | None = None      # one of LABELS
    event_id: int | None = None   # only this event's photos
    event_name: str | None = field(default=None, compare=False)   # for the filter chip
    album_id: int | None = None   # only this album's photos
    auto: str | None = None       # an automatic album (albums.model.AUTO key or 'camera:<model>')
    scope_name: str | None = field(default=None, compare=False)   # album name, for the chip
    tag: str | None = None        # only photos with this tag (or one inside it)
    query: str | None = None      # the search box (search.py)
    ids: tuple | None = None      # only these files (the Edit page's "Selected in the library")
    hide_videos: bool = field(default=False, compare=False)      # Settings > Appearance, not a filter chip

    def active(self) -> bool:
        return self != Filter()

    def sql(self) -> tuple[str, list]:
        where, params = [], []
        if self.min_stars == UNRATED:
            where.append("COALESCE(rt.stars, 0) = 0")
        elif self.min_stars > 0:
            where.append("rt.stars >= ?")
            params.append(self.min_stars)
        if self.flag:
            where.append("rt.flag = ?")
            params.append(self.flag)
        if self.label:
            where.append("rt.color_label = ?")
            params.append(self.label)
        if self.hide_videos:
            where.append("COALESCE(f.format, '') NOT IN ('mp4', 'mov', 'mpeg-ts')")
        if self.ids is not None:
            import json
            where.append("f.id IN (SELECT value FROM json_each(?))")
            params.append(json.dumps(list(self.ids)))
        if self.event_id is not None:
            where.append("f.id IN (SELECT file_id FROM event_files WHERE event_id = ?)")
            params.append(self.event_id)
        if self.album_id is not None:
            where.append("f.id IN (SELECT file_id FROM album_files WHERE album_id = ?)")
            params.append(self.album_id)
        if self.query and self.query.strip():
            from lunelis.search import filter_sql as search_sql
            cond, extra = search_sql(self.query)
            where.append(f"({cond})")
            params.extend(extra)
        if self.tag:
            from lunelis.tags.model import filter_sql
            cond, extra = filter_sql(self.tag)
            where.append(cond)
            params.extend(extra)
        if self.auto:
            from lunelis.albums.model import auto_condition
            cond, extra = auto_condition(self.auto)
            where.append(f"({cond})")
            params.extend(extra)
        # Archived photos are out of the library - except in an album you put
        # them in, and in the Archive album itself.
        if self.album_id is None and self.auto != "archive" and self.ids is None:
            where.append("f.archived_at IS NULL")
        return "".join(f" AND {w}" for w in where), params


@dataclass
class Tile:
    file_id: int
    thumbnail_path: str | None
    unavailable: bool
    badge: str
    stars: int
    is_video: bool
    flag: str | None = None
    label: str | None = None
    duration: float | None = None
    stack_size: int = 0           # a collapsed stack's cover: how many frames it holds
    stack_open: bool = False      # a frame of an expanded stack
    edited: bool = False


class LibraryIndex:
    """Rows are kept exactly as sqlite returns them: converting 160k rows into
    parallel arrays cost ~400ms of pure Python on every load/sort."""

    def __init__(self) -> None:
        self.rows: list[tuple] = []          # what the grid shows
        self.all_rows: list[tuple] = []      # every row the query returned (stacks not collapsed)
        self.collapse = True                 # Settings > stack_bursts
        self.expanded: set[int] = set()      # stack ids shown frame by frame
        self.sort_key = "date_desc"
        self.filter = Filter()
        self.load_seconds = 0.0
        self._pos: dict[int, int] | None = None     # file id -> position, built on demand

    def __len__(self) -> int:
        return len(self.rows)

    def load(self, conn: sqlite3.Connection, sort_key: str | None = None,
             filt: Filter | None = None) -> None:
        started = time.perf_counter()
        if sort_key:
            self.sort_key = sort_key
        if filt is not None:
            self.filter = filt
        where, params = self.filter.sql()
        self.all_rows = conn.execute(
            _QUERY.format(order=SORTS[self.sort_key][1], where=where,
                          untrusted=",".join(str(i) for i in untrusted_mtime_roots(conn)) or "NULL"),
            params).fetchall()
        self._collapse()
        self.load_seconds = time.perf_counter() - started

    def _collapse(self) -> None:
        self._pos = None
        if not self.collapse:
            self.rows = self.all_rows
            return
        present = {r[ID] for r in self.all_rows if r[STACK] is not None and r[ID] == r[STACK_COVER]}
        shown: set[int] = set()
        rows = []
        for r in self.all_rows:
            sid = r[STACK]
            if sid is None or sid in self.expanded:
                rows.append(r)
            elif r[ID] == r[STACK_COVER] or (r[STACK_COVER] not in present and sid not in shown):
                shown.add(sid)
                rows.append(r)
        self.rows = rows

    def set_collapse(self, on: bool) -> None:
        if on != self.collapse:
            self.collapse = on
            self._collapse()

    def toggle_stack(self, stack_id: int) -> bool:
        """Expand or collapse one stack; returns True when it's now expanded."""
        if stack_id in self.expanded:
            self.expanded.discard(stack_id)
        else:
            self.expanded.add(stack_id)
        self._collapse()
        return stack_id in self.expanded

    def stack_id(self, i: int) -> int | None:
        return self.rows[i][STACK]

    def refresh_ratings(self, conn: sqlite3.Connection, file_ids) -> None:
        """Patch rating fields of already-loaded rows in place - re-running the
        whole query (~250 ms) on every keypress would make rating feel laggy.
        Rows that stop matching the filter stay until the next load, as in
        Lightroom, so what you just rated doesn't vanish under the cursor."""
        if self._pos is None:
            self._pos = {r[ID]: i for i, r in enumerate(self.rows)}
        ids = [fid for fid in file_ids if fid in self._pos]
        idset = set(ids)                                  # once, not once per row (159k rows)
        all_pos = None if self.rows is self.all_rows else \
            {r[ID]: i for i, r in enumerate(self.all_rows) if r[ID] in idset}
        for start in range(0, len(ids), 900):
            chunk = ids[start:start + 900]
            got = {r[0]: r[1:] for r in conn.execute(
                f"SELECT file_id, stars, flag, color_label FROM ratings"
                f" WHERE file_id IN ({','.join('?' * len(chunk))})", chunk)}
            for fid in chunk:
                stars, flag, label = got.get(fid, (0, None, None))
                i = self._pos[fid]
                r = list(self.rows[i])
                r[STARS], r[FLAG], r[LABEL] = stars or 0, flag, label
                self.rows[i] = tuple(r)
                if all_pos is not None and fid in all_pos:
                    self.all_rows[all_pos[fid]] = self.rows[i]

    def refresh_edits(self, conn: sqlite3.Connection, file_ids) -> None:
        """Patch the edited mark of loaded rows (after an edit was saved)."""
        ids = set(file_ids)
        revs = {fid: rev for fid, rev in conn.execute(
            f"SELECT file_id, rev FROM edits WHERE file_id IN ({','.join('?' * len(ids))})", list(ids))} if ids else {}
        for rows in ((self.rows,) if self.rows is self.all_rows else (self.rows, self.all_rows)):
            for i, r in enumerate(rows):
                if r[ID] in ids:
                    rr = list(r)
                    rr[EDIT_REV] = revs.get(r[ID])
                    rows[i] = tuple(rr)

    def file_id(self, i: int) -> int:
        return self.rows[i][ID]

    def position(self, file_id: int) -> int:
        # Where a photo is in the current order (-1 if filtered out).
        if self._pos is None:
            self._pos = {r[ID]: i for i, r in enumerate(self.rows)}
        return self._pos.get(file_id, -1)

    def tile(self, i: int) -> Tile:
        r = self.rows[i]
        sid = r[STACK]
        closed = sid is not None and self.collapse and sid not in self.expanded
        return Tile(r[ID], r[THUMB], bool(r[UNAVAILABLE]), (r[EXT] or "").upper(),
                    r[STARS], r[FORMAT] in VIDEO_FORMATS, r[FLAG], r[LABEL], r[DURATION],
                    (r[STACK_SIZE] or 0) if closed else 0, sid is not None and sid in self.expanded,
                    r[EDIT_REV] is not None)
