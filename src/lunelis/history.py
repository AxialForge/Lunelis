"""
Undo and redo for what you do to photos: stars / labels / flags, tags,
albums, events, the archive, stacks, and edits pasted or reset on many.

Every change is recorded as (what, the photos, their state BEFORE). Undo
captures the state now, puts the before-state back, and keeps the now-state
for redo - so undo and redo are the same operation, and work for any kind
that can `capture` and `restore` its state. Nothing here touches a file on
disk; sidecars follow because the restores go through the normal writers.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Callable

STEPS = 30


# --- capture / restore, per kind ----------------------------------------------------------

def _chunks(ids, n=900):
    ids = list(ids)
    for i in range(0, len(ids), n):
        yield ids[i:i + n]


def _cap_ratings(conn, ids, _arg):
    from lunelis.catalog.ratings import snapshot
    return snapshot(conn, ids)


def _res_ratings(conn, ids, _arg, snap):
    from lunelis.catalog.ratings import restore
    restore(conn, snap)


def _cap_archive(conn, ids, _arg):
    out = {}
    for c in _chunks(ids):
        out.update(conn.execute(f"SELECT id, archived_at FROM files WHERE id IN ({','.join('?' * len(c))})", c))
    return out


def _res_archive(conn, ids, _arg, snap):
    conn.executemany("UPDATE files SET archived_at = ? WHERE id = ?", [(v, k) for k, v in snap.items()])
    conn.commit()


def _cap_tags(conn, ids, _arg):
    from lunelis.tags import model as tags
    return {fid: set(tags.tags_of(conn, fid)) for fid in ids}


def _res_tags(conn, ids, _arg, snap):
    from lunelis.tags import model as tags
    now = _cap_tags(conn, ids, None)
    for fid, want in snap.items():
        have = now.get(fid, set())
        for name in have - want:
            tags.remove(conn, [fid], name, commit=False)
        if want - have:
            tags.add(conn, [fid], sorted(want - have), commit=False)
    conn.commit()


def _cap_events(conn, ids, _arg):
    out = {fid: None for fid in ids}
    for c in _chunks(ids):
        out.update(conn.execute(f"SELECT file_id, event_id FROM event_files WHERE file_id IN ({','.join('?' * len(c))})", c))
    return out


def _res_events(conn, ids, _arg, snap):
    from lunelis.events import model as events
    by_event: dict[int | None, list[int]] = {}
    for fid, eid in snap.items():
        by_event.setdefault(eid, []).append(fid)
    existing = {r[0] for r in conn.execute("SELECT id FROM events")}
    for eid, fids in by_event.items():
        if eid is None:
            events.remove_files(conn, fids)
        elif eid in existing:
            events.add_files(conn, eid, fids, commit=False)
    conn.commit()


def _cap_album(conn, ids, album_id):
    """Which of these photos are in the album (and whether it exists, with its name)."""
    row = conn.execute("SELECT name FROM albums WHERE id = ?", (album_id,)).fetchone()
    inside = set()
    for c in _chunks(ids):
        inside.update(r[0] for r in conn.execute(
            f"SELECT file_id FROM album_files WHERE album_id = ? AND file_id IN ({','.join('?' * len(c))})",
            (album_id, *c)))
    return {"exists": row is not None, "name": row[0] if row else None, "inside": inside}


def _res_album(conn, ids, album_id, snap):
    from lunelis.albums import model as albums
    exists = conn.execute("SELECT 1 FROM albums WHERE id = ?", (album_id,)).fetchone() is not None
    if not snap["exists"]:
        if exists:
            albums.delete(conn, album_id)
        return
    if not exists:
        conn.execute("INSERT INTO albums (id, name) VALUES (?, ?)", (album_id, snap["name"]))
        conn.commit()
    now = _cap_album(conn, ids, album_id)["inside"]
    if now - snap["inside"]:
        albums.remove_files(conn, album_id, now - snap["inside"])
    if snap["inside"] - now:
        albums.add_files(conn, album_id, snap["inside"] - now)


def _cap_stack(conn, ids, stack_id):
    row = conn.execute("SELECT * FROM stacks WHERE id = ?", (stack_id,)).fetchone()
    if row is None:
        return {"stack": None, "dismissed": _dismissed(conn, ids)}
    cols = [d[0] for d in conn.execute("SELECT * FROM stacks LIMIT 0").description]
    members = conn.execute("SELECT file_id, position FROM stack_files WHERE stack_id = ? ORDER BY position",
                           (stack_id,)).fetchall()
    return {"stack": dict(zip(cols, tuple(row))), "members": [tuple(m) for m in members],
            "dismissed": _dismissed(conn, ids)}


def _dismissed(conn, ids):
    out = set()
    for c in _chunks(ids):
        out.update(r[0] for r in conn.execute(
            f"SELECT file_id FROM stack_dismissed WHERE file_id IN ({','.join('?' * len(c))})", c))
    return out


def _res_stack(conn, ids, stack_id, snap):
    conn.execute("DELETE FROM stack_files WHERE stack_id = ?", (stack_id,))
    conn.execute("DELETE FROM stacks WHERE id = ?", (stack_id,))
    if snap["stack"] is not None:
        cols = list(snap["stack"])
        conn.execute(f"INSERT INTO stacks ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                     [snap["stack"][c] for c in cols])
        conn.executemany("INSERT INTO stack_files (stack_id, file_id, position) VALUES (?, ?, ?)",
                         [(stack_id, f, p) for f, p in snap["members"]])
    for c in _chunks(ids):
        conn.execute(f"DELETE FROM stack_dismissed WHERE file_id IN ({','.join('?' * len(c))})", c)
    conn.executemany("INSERT OR IGNORE INTO stack_dismissed (file_id) VALUES (?)", [(f,) for f in snap["dismissed"]])
    conn.commit()


def _cap_edits(conn, ids, _arg):
    from lunelis.edit import store
    return {fid: store.get(conn, fid) for fid in ids}


def _res_edits(conn, ids, _arg, snap):
    from lunelis.edit import store
    for fid, stack in snap.items():
        store.save(conn, fid, stack, commit=False)
    conn.commit()


KINDS: dict[str, tuple[Callable, Callable]] = {
    "ratings": (_cap_ratings, _res_ratings),
    "archive": (_cap_archive, _res_archive),
    "tags": (_cap_tags, _res_tags),
    "events": (_cap_events, _res_events),
    "album": (_cap_album, _res_album),
    "stack": (_cap_stack, _res_stack),
    "edits": (_cap_edits, _res_edits),
}


@dataclass
class Step:
    label: str                      # "4 stars on 12 photos"
    kind: str
    ids: list[int]
    state: Any                      # the state to put back
    arg: Any = None                 # album id, stack id...


@dataclass
class History:
    undos: list[Step] = field(default_factory=list)
    redos: list[Step] = field(default_factory=list)

    def before(self, conn: sqlite3.Connection, label: str, kind: str, ids, arg=None) -> None:
        """Call just BEFORE changing these photos: remembers how they were."""
        ids = list(ids)
        cap, _ = KINDS[kind]
        self.undos.append(Step(label, kind, ids, cap(conn, ids, arg), arg))
        del self.undos[:-STEPS]
        self.redos.clear()

    def record(self, label: str, kind: str, ids, state, arg=None) -> None:
        """A change already made, with the state before it (e.g. a new album:
        before it, it didn't exist)."""
        self.undos.append(Step(label, kind, list(ids), state, arg))
        del self.undos[:-STEPS]
        self.redos.clear()

    def forget_last(self) -> None:
        """The change recorded with before() didn't happen after all."""
        if self.undos:
            self.undos.pop()

    def _swap(self, conn, frm: list[Step], to: list[Step]) -> Step | None:
        if not frm:
            return None
        step = frm.pop()
        cap, res = KINDS[step.kind]
        now = cap(conn, step.ids, step.arg)
        res(conn, step.ids, step.arg, step.state)
        to.append(Step(step.label, step.kind, step.ids, now, step.arg))
        return step

    def undo(self, conn: sqlite3.Connection) -> Step | None:
        return self._swap(conn, self.undos, self.redos)

    def redo(self, conn: sqlite3.Connection) -> Step | None:
        return self._swap(conn, self.redos, self.undos)

    def next_undo(self) -> str | None:
        return self.undos[-1].label if self.undos else None

    def next_redo(self) -> str | None:
        return self.redos[-1].label if self.redos else None
