"""
Burst stacks: frames a camera fired in quick succession, shown in the grid
as one tile (the cover) with a frame count, until the user opens the stack.

A burst is a run of photos from one camera in one folder where each frame
follows the previous within `burst_gap_seconds` - measured to the
sub-second when both frames have sub-seconds (49k photos in the real
library do), else they must share the same second (an interval timer
firing every 1 s is not a burst). A run needs `burst_min_frames` distinct
shots (a RAW+JPEG pair is one shot). Videos never stack.

Stacks are rebuilt after every metadata pass. What the user said survives
a rebuild: a cover they chose stays the cover while it's still in a stack,
and a photo they unstacked (stack_dismissed) never stacks again.
Stacking only changes what the grid shows - nothing is moved or hidden
anywhere else (albums, events, duplicates and backups see every frame).
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime

LIVE = "f.missing_since IS NULL AND f.excluded = 0 AND f.quarantined_at IS NULL"


def _ts(iso: str) -> float | None:
    try:
        return datetime.fromisoformat(iso[:26]).timestamp()
    except ValueError:
        return None


def detect(conn: sqlite3.Connection, gap_s: float = 1.0, min_frames: int = 3,
           max_frames: int = 50) -> list[list[int]]:
    """Burst runs as lists of file ids in shooting order (~0.6 s on 159k)."""
    rows = conn.execute(
        f"SELECT f.id, f.root_id, f.rel_path, e.captured_at, e.camera_model"
        f" FROM files f JOIN roots r ON r.id = f.root_id JOIN exif e ON e.file_id = f.id"
        f" WHERE {LIVE} AND r.enabled = 1 AND e.captured_at IS NOT NULL AND e.camera_model IS NOT NULL"
        f" AND COALESCE(f.format, '') NOT IN ('mp4', 'mov', 'mpeg-ts')"
        f" AND f.id NOT IN (SELECT file_id FROM stack_dismissed)"
        # Frames in a timelapse (or a burst the user chose) stack there, not here.
        f" AND f.id NOT IN (SELECT sf.file_id FROM stack_files sf JOIN stacks s ON s.id = sf.stack_id"
        f"                  WHERE s.kind != 'burst')").fetchall()
    by: dict[tuple, list] = defaultdict(list)
    for fid, root, rel, taken, camera in rows:
        ts = _ts(taken)
        if ts is None:
            continue
        folder, _, name = rel.rpartition("/")
        by[(root, folder.lower(), camera)].append((ts, fid, taken, name.rsplit(".", 1)[0].lower()))
    out = []
    for items in by.values():
        if len(items) < min_frames:
            continue
        items.sort()
        run = [items[0]]
        for it in items[1:]:
            if _follows(run[-1], it, gap_s):
                run.append(it)
            else:
                _keep(run, min_frames, max_frames, out)
                run = [it]
        _keep(run, min_frames, max_frames, out)
    return out


def _follows(a: tuple, b: tuple, gap_s: float) -> bool:
    if "." in a[2][19:] and "." in b[2][19:]:
        return b[0] - a[0] <= gap_s
    return a[2][:19] == b[2][:19]


def _keep(run: list, min_frames: int, max_frames: int, out: list) -> None:
    if min_frames <= len({it[3] for it in run}) <= max_frames:    # distinct shots, not RAW+JPEG halves
        out.append([it[1] for it in run])


def default_cover(conn: sqlite3.Connection, members: list[int]) -> int:
    """Best-rated frame, then a pick, then the first frame; a JPEG before its RAW."""
    info = {r[0]: r[1:] for r in conn.execute(
        f"SELECT f.id, COALESCE(rt.stars, 0), rt.flag, f.is_raw FROM files f"
        f" LEFT JOIN ratings rt ON rt.file_id = f.id WHERE f.id IN ({','.join(map(str, members))})")}
    order = {fid: i for i, fid in enumerate(members)}
    return min(members, key=lambda f: (-(info[f][0] or 0), info[f][1] != "pick", info[f][2] or 0, order[f]))


def rebuild(conn: sqlite3.Connection, gap_s: float = 1.0, min_frames: int = 3, max_frames: int = 50) -> int:
    """Make the burst stacks match what detect() finds now; unchanged stacks
    keep their id and cover. Returns the number of stacks."""
    runs = detect(conn, gap_s, min_frames, max_frames)
    old: dict[frozenset, tuple[int, int | None]] = {}
    chosen: set[int] = set()
    members_of: dict[int, list[int]] = defaultdict(list)
    for sid, fid in conn.execute("SELECT s.id, sf.file_id FROM stacks s JOIN stack_files sf ON sf.stack_id = s.id"
                                 " WHERE s.kind = 'burst'"):
        members_of[sid].append(fid)
    covers = {sid: (cover, bool(c)) for sid, cover, c in conn.execute(
        "SELECT id, cover_file_id, cover_chosen FROM stacks WHERE kind = 'burst'")}
    for sid, fids in members_of.items():
        cover, was_chosen = covers.get(sid, (None, False))
        old[frozenset(fids)] = (sid, cover)
        if was_chosen and cover:
            chosen.add(cover)
    keep: set[int] = set()
    new_runs = []
    for run in runs:
        hit = old.get(frozenset(run))
        if hit:
            keep.add(hit[0])
        else:
            new_runs.append(run)
    gone = [sid for sid in covers if sid not in keep]
    for start in range(0, len(gone), 900):
        chunk = gone[start:start + 900]
        conn.execute(f"DELETE FROM stack_files WHERE stack_id IN ({','.join('?' * len(chunk))})", chunk)
        conn.execute(f"DELETE FROM stacks WHERE id IN ({','.join('?' * len(chunk))})", chunk)
    for run in new_runs:
        user = next((f for f in run if f in chosen), None)
        cover = user or default_cover(conn, run)
        sid = conn.execute("INSERT INTO stacks (kind, cover_file_id, cover_chosen, size) VALUES ('burst', ?, ?, ?)",
                           (cover, int(user is not None), len(run))).lastrowid
        conn.executemany("INSERT INTO stack_files (stack_id, file_id, position) VALUES (?, ?, ?)",
                         [(sid, fid, i) for i, fid in enumerate(run)])
    conn.commit()
    return len(runs)


def rebuild_from_settings(conn: sqlite3.Connection) -> int | None:
    from lunelis.settings import Settings
    s = Settings(conn)
    if not s.get("stack_bursts"):
        return None
    return rebuild(conn, s.get("burst_gap_seconds"), s.get("burst_min_frames"), s.get("burst_max_frames"))


# --- what the user can do to a stack -------------------------------------------------

def stack_of(conn: sqlite3.Connection, file_id: int) -> int | None:
    row = conn.execute("SELECT stack_id FROM stack_files WHERE file_id = ?", (file_id,)).fetchone()
    return row[0] if row else None


def members(conn: sqlite3.Connection, stack_id: int) -> list[int]:
    return [r[0] for r in conn.execute(
        "SELECT file_id FROM stack_files WHERE stack_id = ? ORDER BY position", (stack_id,))]


def set_cover(conn: sqlite3.Connection, file_id: int) -> bool:
    sid = stack_of(conn, file_id)
    if sid is None:
        return False
    conn.execute("UPDATE stacks SET cover_file_id = ?, cover_chosen = 1 WHERE id = ?", (file_id, sid))
    conn.commit()
    return True


def unstack(conn: sqlite3.Connection, stack_id: int) -> int:
    """Break a stack up for good: its frames show one by one and are never
    stacked again by a rebuild."""
    fids = members(conn, stack_id)
    conn.executemany("INSERT OR IGNORE INTO stack_dismissed (file_id) VALUES (?)", [(f,) for f in fids])
    conn.execute("DELETE FROM stack_files WHERE stack_id = ?", (stack_id,))
    conn.execute("DELETE FROM stacks WHERE id = ?", (stack_id,))
    conn.commit()
    return len(fids)


def make_burst(conn: sqlite3.Connection, ids: list[int]) -> int:
    """Photo > Collapse into a burst (0.53): these photos, in shooting order,
    as one burst tile. A 'chosen_burst' stack - the automatic rebuild leaves it
    alone. Its photos leave any other stack or timelapse. Returns the stack id."""
    if len(ids) < 2:
        raise ValueError("a burst needs at least two photos")
    q = ",".join("?" * len(ids))
    # A RAW+JPEG pair is one shot: the JPEG half rides along behind its RAW.
    order = [r[0] for r in conn.execute(
        f"SELECT f.id FROM files f LEFT JOIN exif e ON e.file_id = f.id WHERE f.id IN ({q})"
        f" AND NOT (f.pair_of IS NOT NULL AND f.pair_of IN ({q}))"
        f" ORDER BY e.captured_at, f.rel_path", ids + ids)]
    partners = [r[0] for r in conn.execute(f"SELECT id FROM files WHERE pair_of IN ({q})", ids)]
    members_ = order + [p for p in partners if p not in set(order)]
    m = ",".join("?" * len(members_))
    for (old,) in conn.execute(f"SELECT DISTINCT stack_id FROM stack_files WHERE file_id IN ({m})",
                               members_).fetchall():
        conn.execute("DELETE FROM stack_files WHERE stack_id = ?", (old,))
        conn.execute("DELETE FROM stacks WHERE id = ?", (old,))
        conn.execute("UPDATE sequences SET stack_id = NULL WHERE stack_id = ?", (old,))
    import json
    for sid, fids in conn.execute("SELECT id, file_ids FROM sequences WHERE status != 'dismissed'").fetchall():
        if set(order) & set(json.loads(fids)):
            conn.execute("UPDATE sequences SET status = 'dismissed' WHERE id = ?", (sid,))
    conn.execute(f"DELETE FROM stack_dismissed WHERE file_id IN ({m})", members_)
    st = conn.execute("INSERT INTO stacks (kind, cover_file_id, cover_chosen, size) VALUES ('chosen_burst', ?, 0, ?)",
                      (default_cover(conn, order), len(order))).lastrowid
    conn.executemany("INSERT INTO stack_files (stack_id, file_id, position) VALUES (?, ?, ?)",
                     [(st, fid, i) for i, fid in enumerate(members_)])
    conn.commit()
    return st


def stats(conn: sqlite3.Connection) -> tuple[int, int]:
    """(stacks, frames hidden behind covers)."""
    n, size = conn.execute("SELECT COUNT(*), COALESCE(SUM(size), 0) FROM stacks WHERE kind = 'burst'").fetchone()
    return n, size - n
