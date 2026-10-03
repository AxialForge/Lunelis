"""
Search: the library toolbar's box. Every word has to match (AND); the
photos found are shown in the library's order, with its other filters.

A word matches, as the start of a word, anything in a photo's search
text: its file name, its folders, camera (also by friendly name - "a7R V",
"a7rv"), lens, tags (every level), events and albums. Quoted words match
together ("anime expo"). Some words mean something special:

    2024, 2024-06, june, june 2024   - when it was taken
    raw  jpeg  heic  video  photo     - kind of file
    edited  unedited  picks  rejects  unrated  stacked
    4 stars / stars:4                 - at least 4 stars
    tag:  camera:  lens:  folder:  file:  event:  album:   - only that field
    -word                             - photos WITHOUT it

The index is an SQLite FTS5 table (search_fts, one row per file, rowid =
file id), built in ~1.5 s for 159k photos and queried in milliseconds.
Triggers (migration 24) mark a file in search_dirty whenever something it
is found by changes - a rename, new EXIF, a tag, an album, an event - and
`refresh()` re-indexes just those before a search.
"""
from __future__ import annotations

import re
import shlex
import sqlite3

COLUMNS = ("name", "folder", "camera", "lens", "tags", "events", "albums")
FIELD_ALIASES = {"tag": "tags", "tags": "tags", "camera": "camera", "lens": "lens", "folder": "folder",
                 "in": "folder", "file": "name", "name": "name", "event": "events", "album": "albums"}
MONTHS = {m: i for i, m in enumerate(
    ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"), 1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})
MONTHS["sept"] = 9
LIVE = "f.missing_since IS NULL AND f.excluded = 0 AND f.quarantined_at IS NULL"
_SUFFIX = re.compile(r"[a-z]|i{1,3}|iv|vi{0,3}|ix|x")    # "v", "ii", "iv"...

SCHEMA = """
    CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(
        name, folder, camera, lens, tags, events, albums,
        tokenize = 'unicode61 remove_diacritics 2');
    CREATE TABLE IF NOT EXISTS search_dirty (file_id INTEGER PRIMARY KEY);
    INSERT OR IGNORE INTO search_dirty (file_id) SELECT id FROM files;

    CREATE TRIGGER IF NOT EXISTS search_files_ins AFTER INSERT ON files
        BEGIN INSERT OR IGNORE INTO search_dirty VALUES (new.id); END;
    CREATE TRIGGER IF NOT EXISTS search_files_upd AFTER UPDATE OF rel_path, filename, root_id ON files
        BEGIN INSERT OR IGNORE INTO search_dirty VALUES (new.id); END;
    CREATE TRIGGER IF NOT EXISTS search_files_del AFTER DELETE ON files
        BEGIN DELETE FROM search_fts WHERE rowid = old.id; DELETE FROM search_dirty WHERE file_id = old.id; END;
    CREATE TRIGGER IF NOT EXISTS search_exif_ins AFTER INSERT ON exif
        BEGIN INSERT OR IGNORE INTO search_dirty VALUES (new.file_id); END;
    CREATE TRIGGER IF NOT EXISTS search_exif_upd AFTER UPDATE OF camera_make, camera_model, lens ON exif
        BEGIN INSERT OR IGNORE INTO search_dirty VALUES (new.file_id); END;
    CREATE TRIGGER IF NOT EXISTS search_tag_ins AFTER INSERT ON file_tags
        BEGIN INSERT OR IGNORE INTO search_dirty VALUES (new.file_id); END;
    CREATE TRIGGER IF NOT EXISTS search_tag_del AFTER DELETE ON file_tags
        BEGIN INSERT OR IGNORE INTO search_dirty VALUES (old.file_id); END;
    CREATE TRIGGER IF NOT EXISTS search_tag_name AFTER UPDATE OF name ON tags
        BEGIN INSERT OR IGNORE INTO search_dirty SELECT file_id FROM file_tags WHERE tag_id = new.id; END;
    CREATE TRIGGER IF NOT EXISTS search_album_ins AFTER INSERT ON album_files
        BEGIN INSERT OR IGNORE INTO search_dirty VALUES (new.file_id); END;
    CREATE TRIGGER IF NOT EXISTS search_album_del AFTER DELETE ON album_files
        BEGIN INSERT OR IGNORE INTO search_dirty VALUES (old.file_id); END;
    CREATE TRIGGER IF NOT EXISTS search_album_name AFTER UPDATE OF name ON albums
        BEGIN INSERT OR IGNORE INTO search_dirty SELECT file_id FROM album_files WHERE album_id = new.id; END;
    CREATE TRIGGER IF NOT EXISTS search_event_ins AFTER INSERT ON event_files
        BEGIN INSERT OR IGNORE INTO search_dirty VALUES (new.file_id); END;
    CREATE TRIGGER IF NOT EXISTS search_event_del AFTER DELETE ON event_files
        BEGIN INSERT OR IGNORE INTO search_dirty VALUES (old.file_id); END;
    CREATE TRIGGER IF NOT EXISTS search_event_name AFTER UPDATE OF name ON events
        BEGIN INSERT OR IGNORE INTO search_dirty SELECT file_id FROM event_files WHERE event_id = new.id; END;
"""


# --- the index ------------------------------------------------------------------------------

def _camera_words(make: str | None, model: str | None) -> str:
    from lunelis.ui.photoinfo import CAMERA_NAMES
    words = [make or "", model or ""]
    friendly = CAMERA_NAMES.get((model or "").upper())
    if friendly:
        ascii_name = friendly.replace("α", "a")
        words += [friendly, ascii_name, ascii_name.replace(" ", "")]      # "a7R V", "a7RV"
    return " ".join(w for w in words if w)


def refresh(conn: sqlite3.Connection, *, limit: int | None = None, batch: int = 5000) -> int:
    """Re-index the files marked dirty. Returns how many were done."""
    done = 0
    while True:
        n = batch if limit is None else min(batch, limit - done)
        if n <= 0:
            break
        ids = [r[0] for r in conn.execute("SELECT file_id FROM search_dirty LIMIT ?", (n,))]
        if not ids:
            break
        marks = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT f.id, f.filename, f.rel_path, e.camera_make, e.camera_model, e.lens,"
            f" (SELECT group_concat(replace(t.name, '|', ' '), ' ') FROM file_tags ft JOIN tags t ON t.id = ft.tag_id"
            f"   WHERE ft.file_id = f.id),"
            f" (SELECT group_concat(ev.name, ' ') FROM event_files ef JOIN events ev ON ev.id = ef.event_id"
            f"   WHERE ef.file_id = f.id),"
            f" (SELECT group_concat(a.name, ' ') FROM album_files af JOIN albums a ON a.id = af.album_id"
            f"   WHERE af.file_id = f.id)"
            f" FROM files f LEFT JOIN exif e ON e.file_id = f.id WHERE f.id IN ({marks})", ids).fetchall()
        conn.execute(f"DELETE FROM search_fts WHERE rowid IN ({marks})", ids)
        conn.executemany(
            "INSERT INTO search_fts (rowid, name, folder, camera, lens, tags, events, albums)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(fid, name, rel.rsplit("/", 1)[0] if "/" in rel else "", _camera_words(make, model),
              lens or "", tags or "", events or "", albums or "")
             for fid, name, rel, make, model, lens, tags, events, albums in rows])
        conn.execute(f"DELETE FROM search_dirty WHERE file_id IN ({marks})", ids)
        conn.commit()
        done += len(ids)
    return done


def pending(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM search_dirty").fetchone()[0]


# --- the query --------------------------------------------------------------------------------

def _phrase(text: str) -> str | None:
    """An FTS5 prefix phrase for a user word: '24-105' -> '"24 105"*'."""
    toks = re.findall(r"\w+", text.lower())
    if not toks:
        return None
    return '"' + " ".join(toks) + '"*'


def parse(query: str) -> tuple[str | None, list[str], list, list[str]]:
    """(FTS5 match expression or None, extra WHERE clauses, their params,
    FTS5 expressions the photos must NOT match)."""
    try:
        words = shlex.split(query or "", posix=True)
    except ValueError:                                   # an unclosed quote
        words = (query or "").replace('"', " ").split()
    fts: list[str] = []
    negative: list[str] = []
    where: list[str] = []
    params: list = []
    i = 0
    while i < len(words):
        w = words[i]
        low = w.lower()
        nxt = words[i + 1].lower() if i + 1 < len(words) else ""
        neg = low.startswith("-") and len(low) > 1
        if neg:
            w, low = w[1:], low[1:]
        # Dates.
        if re.fullmatch(r"(19|20)\d\d", low):
            where.append(("NOT " if neg else "") + "substr(e.captured_at, 1, 4) = ?")
            params.append(low)
        elif re.fullmatch(r"(19|20)\d\d-\d\d", low):
            where.append(("NOT " if neg else "") + "substr(e.captured_at, 1, 7) = ?")
            params.append(low)
        elif low in MONTHS:
            month = f"{MONTHS[low]:02d}"
            if re.fullmatch(r"(19|20)\d\d", nxt):
                where.append(("NOT " if neg else "") + "substr(e.captured_at, 1, 7) = ?")
                params.append(f"{nxt}-{month}")
                i += 1
            else:
                where.append(("NOT " if neg else "") + "substr(e.captured_at, 6, 2) = ?")
                params.append(month)
        # Stars.
        elif re.fullmatch(r"[1-5]", low) and nxt in ("star", "stars"):
            where.append(("NOT " if neg else "") + "COALESCE(rt.stars, 0) >= ?")
            params.append(int(low))
            i += 1
        elif re.fullmatch(r"stars?:[1-5]", low):
            where.append(("NOT " if neg else "") + "COALESCE(rt.stars, 0) >= ?")
            params.append(int(low[-1]))
        # Kinds and states.
        elif low in KEYWORDS:
            where.append(("NOT " if neg else "") + f"({KEYWORDS[low]})")
        # A field.
        elif ":" in low and low.split(":", 1)[0] in FIELD_ALIASES:
            field, rest = w.split(":", 1)
            ph = _phrase(rest)
            if ph:
                (negative if neg else fts).append(f"{FIELD_ALIASES[field.lower()]} : {ph}")
        else:
            # "a7r v", "canon r5 ii": a lone letter or roman numeral belongs to the
            # word before it (else "v" would match every word starting with v).
            while not neg and i + 1 < len(words) and _SUFFIX.fullmatch(words[i + 1].lower()):
                w = f"{w} {words[i + 1]}"
                i += 1
            ph = _phrase(w)
            if ph:
                (negative if neg else fts).append(ph)
        i += 1
    return (" AND ".join(fts) if fts else None), where, params, negative


KEYWORDS = {
    "raw": "f.is_raw = 1",
    "raws": "f.is_raw = 1",
    "jpeg": "LOWER(f.ext) IN ('jpg', 'jpeg')",
    "jpg": "LOWER(f.ext) IN ('jpg', 'jpeg')",
    "heic": "LOWER(f.ext) IN ('heic', 'heif', 'hif')",
    "video": "COALESCE(f.format, '') IN ('mp4', 'mov', 'mpeg-ts')",
    "videos": "COALESCE(f.format, '') IN ('mp4', 'mov', 'mpeg-ts')",
    "photo": "COALESCE(f.format, '') NOT IN ('mp4', 'mov', 'mpeg-ts')",
    "photos": "COALESCE(f.format, '') NOT IN ('mp4', 'mov', 'mpeg-ts')",
    "edited": "f.id IN (SELECT file_id FROM edits)",
    "unedited": "f.id NOT IN (SELECT file_id FROM edits)",
    "pick": "rt.flag = 'pick'",
    "picks": "rt.flag = 'pick'",
    "reject": "rt.flag = 'reject'",
    "rejects": "rt.flag = 'reject'",
    "unrated": "COALESCE(rt.stars, 0) = 0",
    "stacked": "f.id IN (SELECT file_id FROM stack_files)",
    "untagged": "f.id NOT IN (SELECT file_id FROM file_tags)",
}


def filter_sql(query: str) -> tuple[str, list]:
    """The library WHERE clause for a search (aliases f / e / rt)."""
    fts, where, params, negative = parse(query)
    out = []
    out_params: list = []
    if fts:
        out.append("f.id IN (SELECT rowid FROM search_fts WHERE search_fts MATCH ?)")
        out_params.append(fts)
    for n in negative:
        out.append("f.id NOT IN (SELECT rowid FROM search_fts WHERE search_fts MATCH ?)")
        out_params.append(n)
    out.extend(where)
    out_params.extend(params)
    return (" AND ".join(out) if out else "1"), out_params


def suggestions(conn: sqlite3.Connection) -> list[str]:
    """Things worth completing to: tags, events, albums, cameras, lenses."""
    out: list[str] = []
    out += [r[0].replace("|", " ") for r in conn.execute("SELECT name FROM tags")]
    out += [r[0] for r in conn.execute("SELECT name FROM events")]
    out += [r[0] for r in conn.execute("SELECT name FROM albums WHERE is_smart = 0")]
    for make, model in conn.execute("SELECT DISTINCT camera_make, camera_model FROM exif WHERE camera_model IS NOT NULL"):
        from lunelis.ui.photoinfo import CAMERA_NAMES
        out.append(CAMERA_NAMES.get(model.upper(), model).replace("α", "a"))
    out += [r[0] for r in conn.execute("SELECT DISTINCT lens FROM exif WHERE lens IS NOT NULL AND lens != ''")]
    seen, uniq = set(), []
    for s in out:
        if s and s.lower() not in seen:
            seen.add(s.lower())
            uniq.append(s)
    return sorted(uniq, key=str.lower)
