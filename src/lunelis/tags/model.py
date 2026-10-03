"""
Tags (keywords): words you attach to photos - "Beach", "Grandma" - and
find them by later.

Tags can nest: "Places|Ohio|Cleveland" is Cleveland inside Ohio inside
Places (typed as "Places > Ohio > Cleveland" too). A tag's `name` is its
full path; the parents exist as tags of their own, and filtering by
"Places|Ohio" includes everything inside it. Names are unique ignoring
case ("beach" and "Beach" are one tag).

Tags are written to the photo's XMP sidecar as dc:subject (every level,
flat) and lr:hierarchicalSubject (the paths) - what darktable and
Lightroom read (xmp/sidecar.py). Any change queues the sidecar like a
rating does.
"""
from __future__ import annotations

import re
import sqlite3

SEP = "|"


def normalize(text: str) -> str | None:
    """'Places > Ohio / Cleveland ' -> 'Places|Ohio|Cleveland' (None if empty)."""
    parts = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\s*(?:\||>)\s*", text or "")]
    parts = [p for p in parts if p]
    return SEP.join(parts) if parts else None


def split_input(text: str) -> list[str]:
    """What someone typed in a tag box: commas or semicolons separate tags."""
    out = []
    for piece in re.split(r"[,;\n]", text or ""):
        n = normalize(piece)
        if n and n.lower() not in {o.lower() for o in out}:
            out.append(n)
    return out


def leaf(name: str) -> str:
    return name.rsplit(SEP, 1)[-1]


def ancestors(name: str) -> list[str]:
    parts = name.split(SEP)
    return [SEP.join(parts[:i]) for i in range(1, len(parts))]


def _queue_sidecars(conn: sqlite3.Connection, file_ids) -> None:
    conn.executemany("INSERT INTO ratings (file_id, xmp_pending) VALUES (?, 1)"
                     " ON CONFLICT(file_id) DO UPDATE SET xmp_pending = 1", [(f,) for f in file_ids])


def tag_id(conn: sqlite3.Connection, name: str, create: bool = True) -> int | None:
    """The tag's id (making it, and its parents, if needed)."""
    n = normalize(name)
    if n is None:
        return None
    row = conn.execute("SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (n,)).fetchone()
    if row:
        return row[0]
    if not create:
        return None
    for parent in ancestors(n):
        if not conn.execute("SELECT 1 FROM tags WHERE name = ? COLLATE NOCASE", (parent,)).fetchone():
            conn.execute("INSERT INTO tags (name) VALUES (?)", (parent,))
    return conn.execute("INSERT INTO tags (name) VALUES (?)", (n,)).lastrowid


def add(conn: sqlite3.Connection, file_ids, names, *, commit: bool = True) -> int:
    """Tag photos. Returns how many new (photo, tag) pairs were made."""
    ids = list(file_ids)
    made = 0
    touched: set[int] = set()
    for name in names:
        tid = tag_id(conn, name)
        if tid is None:
            continue
        before = conn.total_changes
        conn.executemany("INSERT OR IGNORE INTO file_tags (file_id, tag_id) VALUES (?, ?)", [(f, tid) for f in ids])
        n = conn.total_changes - before
        if n:
            made += n
            touched.update(ids)
    if touched:
        _queue_sidecars(conn, touched)
    if commit:
        conn.commit()
    return made


def remove(conn: sqlite3.Connection, file_ids, name: str, *, commit: bool = True) -> int:
    """Untag photos (the tag itself stays, for next time)."""
    tid = tag_id(conn, name, create=False)
    if tid is None:
        return 0
    ids = list(file_ids)
    before = conn.total_changes
    conn.executemany("DELETE FROM file_tags WHERE file_id = ? AND tag_id = ?", [(f, tid) for f in ids])
    n = conn.total_changes - before
    if n:
        _queue_sidecars(conn, ids)
    if commit:
        conn.commit()
    return n


def tags_of(conn: sqlite3.Connection, file_id: int) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT t.name FROM file_tags ft JOIN tags t ON t.id = ft.tag_id WHERE ft.file_id = ?"
        " ORDER BY t.name COLLATE NOCASE", (file_id,))]


def counts_in(conn: sqlite3.Connection, file_ids) -> dict[str, int]:
    """Tag -> how many of these photos have it (for a selection's tag list)."""
    ids = list(file_ids)
    out: dict[str, int] = {}
    for start in range(0, len(ids), 900):
        chunk = ids[start:start + 900]
        for name, n in conn.execute(
                f"SELECT t.name, COUNT(*) FROM file_tags ft JOIN tags t ON t.id = ft.tag_id"
                f" WHERE ft.file_id IN ({','.join('?' * len(chunk))}) GROUP BY t.id", chunk):
            out[name] = out.get(name, 0) + n
    return dict(sorted(out.items(), key=lambda kv: kv[0].lower()))


LIVE = "f.missing_since IS NULL AND f.excluded = 0 AND f.quarantined_at IS NULL"


def all_tags(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    """Every tag with how many live photos carry it directly."""
    return [(n, c) for n, c in conn.execute(
        f"SELECT t.name, COUNT(f.id) FROM tags t LEFT JOIN file_tags ft ON ft.tag_id = t.id"
        f" LEFT JOIN files f ON f.id = ft.file_id AND {LIVE}"
        f" GROUP BY t.id ORDER BY t.name COLLATE NOCASE")]


def names(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT name FROM tags ORDER BY name COLLATE NOCASE")]


def _like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def filter_sql(name: str) -> tuple[str, list]:
    """A library WHERE clause: photos with this tag or any tag inside it."""
    return ("f.id IN (SELECT ft.file_id FROM file_tags ft JOIN tags t ON t.id = ft.tag_id"
            " WHERE t.name = ? COLLATE NOCASE OR t.name LIKE ? ESCAPE '\\')",
            [name, _like(name) + SEP + "%"])


def _subtree(conn: sqlite3.Connection, name: str) -> list[tuple[int, str]]:
    return [tuple(r) for r in conn.execute(
        "SELECT id, name FROM tags WHERE name = ? COLLATE NOCASE OR name LIKE ? ESCAPE '\\'",
        (name, _like(name) + SEP + "%"))]


def _photos_of(conn: sqlite3.Connection, tag_ids) -> list[int]:
    ids = list(tag_ids)
    return [r[0] for r in conn.execute(
        f"SELECT DISTINCT file_id FROM file_tags WHERE tag_id IN ({','.join('?' * len(ids))})", ids)] if ids else []


def rename(conn: sqlite3.Connection, old: str, new: str) -> None:
    """Rename a tag (and everything inside it). Renaming onto an existing
    tag merges them."""
    new_n = normalize(new)
    if not new_n:
        raise ValueError("A tag needs a name.")
    old_n = normalize(old)
    if new_n.lower() == old_n.lower() and new_n == old_n:
        return
    if new_n.lower().startswith(old_n.lower() + SEP):
        raise ValueError("A tag can't go inside itself.")
    subtree = _subtree(conn, old_n)
    photos = _photos_of(conn, [i for i, _ in subtree])
    for tid, name in sorted(subtree, key=lambda r: -len(r[1])):          # children first
        target = new_n + name[len(old_n):]
        existing = conn.execute("SELECT id FROM tags WHERE name = ? COLLATE NOCASE AND id != ?",
                                (target, tid)).fetchone()
        if existing:
            conn.execute("INSERT OR IGNORE INTO file_tags (file_id, tag_id, confidence)"
                         " SELECT file_id, ?, confidence FROM file_tags WHERE tag_id = ?", (existing[0], tid))
            conn.execute("DELETE FROM tags WHERE id = ?", (tid,))
        else:
            for parent in ancestors(target):
                tag_id(conn, parent)
            conn.execute("UPDATE tags SET name = ? WHERE id = ?", (target, tid))
    if photos:
        _queue_sidecars(conn, photos)
    conn.commit()


def merge(conn: sqlite3.Connection, source: str, into: str) -> None:
    rename(conn, source, into)


def delete(conn: sqlite3.Connection, name: str) -> int:
    """Delete a tag and everything inside it (from the photos too - the
    photos themselves stay). Returns how many photos lost a tag."""
    subtree = _subtree(conn, normalize(name))
    photos = _photos_of(conn, [i for i, _ in subtree])
    conn.executemany("DELETE FROM tags WHERE id = ?", [(i,) for i, _ in subtree])
    if photos:
        _queue_sidecars(conn, photos)
    conn.commit()
    return len(photos)


def remember_recent(conn: sqlite3.Connection, used: list[str]) -> None:
    from lunelis.settings import Settings
    s = Settings(conn)
    recent = [t for t in (s.get("tags_recent") or []) if t.lower() not in {u.lower() for u in used}]
    s.set("tags_recent", (used + recent)[:12])


def recent(conn: sqlite3.Connection) -> list[str]:
    from lunelis.settings import Settings
    known = {n.lower() for n in names(conn)}
    return [t for t in (Settings(conn).get("tags_recent") or []) if t.lower() in known]
