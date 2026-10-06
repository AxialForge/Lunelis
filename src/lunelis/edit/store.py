"""
Edit stacks and your own filters, in the catalog.

`edits` holds one row per edited photo (no row = the original). Every save
bumps `rev` (the cached proxy/thumbnail follow it) and marks the photo's
sidecar for writing, so lunelis:EditStack reaches the XMP like a rating does.
"""
from __future__ import annotations

import sqlite3

from lunelis.edit import presets
from lunelis.edit.stack import BY_KEY, Stack, dumps, loads


def get(conn: sqlite3.Connection, file_id: int) -> Stack:
    row = conn.execute("SELECT stack FROM edits WHERE file_id = ?", (file_id,)).fetchone()
    return loads(row[0]) if row else Stack()


def rev(conn: sqlite3.Connection, file_id: int) -> int:
    row = conn.execute("SELECT rev FROM edits WHERE file_id = ?", (file_id,)).fetchone()
    return row[0] if row else 0


def save(conn: sqlite3.Connection, file_id: int, stack: Stack, *, commit: bool = True) -> bool:
    """Store a stack (an identity stack removes the edit). Returns whether
    anything changed."""
    old = conn.execute("SELECT stack FROM edits WHERE file_id = ?", (file_id,)).fetchone()
    if stack.is_identity():
        if not old:
            return False
        conn.execute("DELETE FROM edits WHERE file_id = ?", (file_id,))
    else:
        text = dumps(stack)
        if old and old[0] == text:
            return False
        conn.execute("INSERT INTO edits (file_id, stack) VALUES (?, ?)"
                     " ON CONFLICT(file_id) DO UPDATE SET stack = excluded.stack, rev = edits.rev + 1,"
                     " updated_at = datetime('now')", (file_id, text))
    # The sidecar carries the stack: queue it like a rating change.
    conn.execute("INSERT INTO ratings (file_id, xmp_pending) VALUES (?, 1)"
                 " ON CONFLICT(file_id) DO UPDATE SET xmp_pending = 1", (file_id,))
    if commit:
        conn.commit()
    return True


def edited_ids(conn: sqlite3.Connection, file_ids) -> set[int]:
    ids = list(file_ids)
    out: set[int] = set()
    for start in range(0, len(ids), 900):
        chunk = ids[start:start + 900]
        out.update(r[0] for r in conn.execute(
            f"SELECT file_id FROM edits WHERE file_id IN ({','.join('?' * len(chunk))})", chunk))
    return out


# --- filters -----------------------------------------------------------------------------

def filters(conn: sqlite3.Connection) -> list[tuple[str, dict, bool]]:
    """(name, adjustments, built-in) - built-ins first, then yours by name."""
    out = [(n, dict(p), True) for n, p in presets.BUILTIN.items()]
    for name, params in conn.execute("SELECT name, params FROM edit_filters ORDER BY name COLLATE NOCASE"):
        out.append((name, loads(params).adjust, False))
    return out


def filter_params(conn: sqlite3.Connection, name: str | None) -> dict | None:
    if not name:
        return None
    if name in presets.BUILTIN:
        return dict(presets.BUILTIN[name])
    row = conn.execute("SELECT params FROM edit_filters WHERE name = ?", (name,)).fetchone()
    return loads(row[0]).adjust if row else None


def save_filter(conn: sqlite3.Connection, name: str, adjust: dict) -> None:
    """Save adjustments as your own filter (geometry never goes in a filter)."""
    name = name.strip()
    if not name:
        raise ValueError("A filter needs a name.")
    if name in presets.BUILTIN:
        raise ValueError(f"'{name}' is a built-in filter - choose another name.")
    adjust = {k: v for k, v in adjust.items() if k in BY_KEY and v}
    if not adjust:
        raise ValueError("There are no adjustments to save.")
    conn.execute("INSERT INTO edit_filters (name, params) VALUES (?, ?)"
                 " ON CONFLICT(name) DO UPDATE SET params = excluded.params",
                 (name, dumps(Stack(adjust=adjust))))
    conn.commit()


def users_of(conn: sqlite3.Connection, name: str) -> list[int]:
    """Photos whose edit uses filter `name`."""
    return [fid for fid, text in conn.execute("SELECT file_id, stack FROM edits WHERE stack LIKE '%f=%'")
            if (loads(text).filter or "").lower() == name.lower()]


def delete_filter(conn: sqlite3.Connection, name: str) -> int:
    """Delete one of your filters. Photos using it keep their look: the
    filter's values are written into their own adjustments first. Returns
    how many photos that was."""
    users = users_of(conn, name)
    for fid in users:
        save(conn, fid, baked(conn, get(conn, fid)), commit=False)
    conn.execute("DELETE FROM edit_filters WHERE name = ?", (name,))
    conn.commit()
    return len(users)


def apply_import_filter(conn: sqlite3.Connection) -> list[int]:
    """Give newly imported photos the Settings > Import filter, once per
    import (a photo you've already edited is left alone). Returns the photos
    that got it - their thumbnails need rendering."""
    from lunelis.events.model import import_file_ids
    from lunelis.settings import Settings
    name = Settings(conn).get("import_filter")
    done: list[int] = []
    for (imp,) in conn.execute("SELECT id FROM imports WHERE state = 'done' AND edits_applied = 0").fetchall():
        fids = import_file_ids(conn, imp)
        if not fids:
            continue                        # not cataloged yet: next time
        if name and filter_params(conn, name) is not None:
            already = edited_ids(conn, fids)
            for fid in fids:
                if fid not in already:
                    save(conn, fid, Stack(filter=name), commit=False)
                    done.append(fid)
        conn.execute("UPDATE imports SET edits_applied = 1 WHERE id = ?", (imp,))
        conn.commit()
    return done


def flatten(conn: sqlite3.Connection, stack: Stack) -> dict:
    """A stack's filter + manual adjustments as plain slider values (for
    saving it as a new filter). Curves, masks, lens and retouch aren't sliders
    and stay with the photo."""
    from lunelis.edit.stack import effective
    return {k: v for k, v in effective(stack, filter_params(conn, stack.filter)).items() if not k.startswith("_")}


def baked(conn: sqlite3.Connection, stack: Stack) -> Stack:
    """The same look with its filter written into the photo's own sliders -
    everything else in the edit (crop, curves, masks, lens, retouch) kept."""
    from dataclasses import replace
    return replace(stack, filter=None, amount=100, adjust=flatten(conn, stack))


# --- virtual copies -------------------------------------------------------------------------------
# More than one edit of the same photo without copying the file: each copy is
# its own stack in `copies`. The photo's own edit (above) stays "the original".

def copies_of(conn: sqlite3.Connection, file_id: int) -> list[tuple[int, str]]:
    return [tuple(r) for r in conn.execute(
        "SELECT id, name FROM copies WHERE file_id = ? ORDER BY id", (file_id,))]


def add_copy(conn: sqlite3.Connection, file_id: int, stack: Stack, name: str | None = None) -> int:
    n = conn.execute("SELECT COUNT(*) FROM copies WHERE file_id = ?", (file_id,)).fetchone()[0]
    cid = conn.execute("INSERT INTO copies (file_id, name, stack) VALUES (?, ?, ?)",
                       (file_id, (name or f"Copy {n + 1}").strip(), dumps(stack))).lastrowid
    conn.commit()
    return cid


def get_copy(conn: sqlite3.Connection, copy_id: int) -> Stack:
    row = conn.execute("SELECT stack FROM copies WHERE id = ?", (copy_id,)).fetchone()
    return loads(row[0]) if row else Stack()


def save_copy(conn: sqlite3.Connection, copy_id: int, stack: Stack) -> bool:
    text = dumps(stack)
    cur = conn.execute("UPDATE copies SET stack = ?, updated_at = datetime('now') WHERE id = ? AND stack != ?",
                       (text, copy_id, text))
    conn.commit()
    return cur.rowcount > 0


def rename_copy(conn: sqlite3.Connection, copy_id: int, name: str) -> None:
    conn.execute("UPDATE copies SET name = ? WHERE id = ?", (name.strip() or "Copy", copy_id))
    conn.commit()


def delete_copy(conn: sqlite3.Connection, copy_id: int) -> None:
    conn.execute("DELETE FROM copies WHERE id = ?", (copy_id,))
    conn.commit()


# --- presets as files -----------------------------------------------------------------------------

PRESET_FORMAT = "lunelis-presets/1"


def export_filters(conn: sqlite3.Connection, names: list[str] | None = None) -> dict:
    """Your own filters as a shareable document (adjustments by slider key)."""
    import json
    out = []
    for name, params, built_in in filters(conn):
        if built_in or (names is not None and name not in names):
            continue
        out.append({"name": name, "adjust": params if isinstance(params, dict) else json.loads(params)})
    return {"format": PRESET_FORMAT, "presets": out}


def import_filters(conn: sqlite3.Connection, doc: dict, replace_existing: bool = False) -> tuple[int, list[str]]:
    """Add the presets from an exported document; (added, skipped names - already there)."""
    if not isinstance(doc, dict) or doc.get("format") != PRESET_FORMAT:
        raise ValueError("That isn't a Lunelis presets file.")
    have = {n.lower() for n, _p, _b in filters(conn)}
    added, skipped = 0, []
    for p in doc.get("presets", []):
        name = str(p.get("name", "")).strip()
        adjust = {k: float(v) for k, v in (p.get("adjust") or {}).items() if k in BY_KEY}
        if not name or not adjust:
            continue
        if name.lower() in have and not replace_existing:
            skipped.append(name)
            continue
        try:
            save_filter(conn, name, adjust)
        except ValueError:                       # a built-in's name
            skipped.append(name)
            continue
        added += 1
    return added, skipped
