"""
The timelapse engine: interval shoots found in the library, for review.

A timelapse is a run of frames from one camera and lens (same focal length)
taken at a steady interval - the time between frames stays within
max(1 s, 5 %) of the run's own interval, measured from the capture times.

- A RAW+JPEG pair is one frame (the RAW stands for it).
- Exposure may change (sunsets, holy-grail ramping); a zoom or lens change
  ends the run.
- The interval is 2 s or more, and no two frames share a second; faster is
  a burst (stacks.py, up to 50 frames) or continuous shooting.
- A run needs `timelapse_min_frames` (100) frames. Shorter sets are made by
  hand: Photo > Collapse into a timelapse.
- A pause (a battery or card swap) of up to `timelapse_max_pause_minutes`
  keeps one timelapse unless `timelapse_split_gaps` is on (off by default):
  then each side is its own timelapse.

Found runs wait on the Timelapses page: confirm, dismiss, stack them into
one tile, or call a short one a burst. `timelapse_auto_stack` stacks every
run of `timelapse_auto_stack_frames` (500) or more as soon as it's found.
Nothing is ever built automatically: Info > Build timelapse... on any of its
frames hands them to Create > Timelapse.

detect() reads only the catalog (EXIF), ~1 s for 159k photos, so the whole
library is looked at again after each scan; what the user said about a run
(confirmed, dismissed, stacked) is kept by its frames.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

LIVE = "f.missing_since IS NULL AND f.excluded = 0 AND f.quarantined_at IS NULL"
MIN_INTERVAL_S = 2.0                 # faster is a burst or continuous shooting
STEADY_AFTER_PAUSE = 5               # gaps at the interval needed after a pause to bridge it
MAX_INTERVAL_S = 600.0
BURST_MAX = 50                       # a run this short can be called a burst instead


@dataclass
class Frame:
    file_id: int
    t: float
    camera: str
    lens: str
    focal: float | None
    folder: str
    stem: str
    is_raw: bool


@dataclass
class Run:
    frames: list[Frame] = field(default_factory=list)
    pauses: int = 0

    @property
    def ids(self) -> list[int]:
        return [f.file_id for f in self.frames]

    def interval(self) -> float:
        gaps = sorted(b.t - a.t for a, b in zip(self.frames, self.frames[1:]))
        return gaps[len(gaps) // 2] if gaps else 0.0


def _ts(iso: str) -> float | None:
    try:
        return datetime.fromisoformat(iso[:26]).timestamp()
    except ValueError:
        return None


def frames(conn: sqlite3.Connection) -> list[Frame]:
    """Every dated still, one per shot (a RAW+JPEG pair -> the RAW), by camera then time."""
    rows = conn.execute(
        f"SELECT f.id, e.captured_at, COALESCE(e.camera_make, '') || ' ' || COALESCE(e.camera_model, ''),"
        f" COALESCE(e.lens, ''), e.focal_length_mm, f.root_id, f.rel_path, f.is_raw"
        f" FROM files f JOIN exif e ON e.file_id = f.id"
        f" WHERE {LIVE} AND e.captured_at IS NOT NULL AND e.camera_model IS NOT NULL"
        f" AND COALESCE(f.format, '') NOT IN ('mp4', 'mov', 'mpeg-ts')").fetchall()
    shots: dict[tuple, Frame] = {}
    for fid, when, cam, lens, focal, root, rel, is_raw in rows:
        t = _ts(when)
        if t is None:
            continue
        folder, _, name = rel.rpartition("/")
        stem = name.rsplit(".", 1)[0].lower()
        f = Frame(fid, t, cam.strip(), lens, focal, f"{root}/{folder.lower()}", stem, bool(is_raw))
        key = (f.camera, f.folder, stem, int(t))
        have = shots.get(key)
        if have is None or (f.is_raw and not have.is_raw):
            shots[key] = f
    out = sorted(shots.values(), key=lambda f: (f.camera, f.t, f.file_id))
    return out


def _tol(interval: float) -> float:
    return max(1.0, 0.05 * interval)


def _same_optics(a: Frame, b: Frame) -> bool:
    if a.lens != b.lens:
        return False
    if a.focal is None or b.focal is None:
        return a.focal is None and b.focal is None
    return abs(a.focal - b.focal) <= 0.5


def detect(fs: list[Frame], min_frames: int = 100, split_gaps: bool = False,
           max_pause_s: float = 1800.0) -> list[Run]:
    """Steady-interval runs of at least min_frames frames."""
    out: list[Run] = []
    i, n = 0, len(fs)
    while i < n - 1:
        a, b = fs[i], fs[i + 1]
        gap = b.t - a.t
        if a.camera != b.camera or not _same_optics(a, b) or not MIN_INTERVAL_S <= gap <= MAX_INTERVAL_S:
            i += 1
            continue
        run = Run([a, b])
        interval = gap
        j = i + 2
        while j < n:
            prev, cur = run.frames[-1], fs[j]
            g = cur.t - prev.t
            if cur.camera != prev.camera or not _same_optics(prev, cur):
                break
            if g >= 1.0 and abs(g - interval) <= _tol(interval):
                run.frames.append(cur)
                interval = run.interval()
                j += 1
                continue
            # A pause: the run goes on if the next gap is the interval again.
            if not split_gaps and interval < g <= max_pause_s and _steady_from(fs, j, interval):
                run.frames.append(cur)
                run.pauses += 1
                j += 1
                continue
            break
        if len(run.frames) >= min_frames:
            out.extend(_split_if_choppy(run, min_frames))
            i = j
        else:
            i += 1
    return out


def _steady_from(fs: list[Frame], j: int, interval: float) -> bool:
    """After a pause at fs[j]: the next STEADY_AFTER_PAUSE gaps are the interval again."""
    seq = fs[j:j + STEADY_AFTER_PAUSE + 1]
    if len(seq) < STEADY_AFTER_PAUSE + 1:
        return False
    return all(b.camera == a.camera and _same_optics(a, b) and (b.t - a.t) >= 1.0
               and abs((b.t - a.t) - interval) <= _tol(interval) for a, b in zip(seq, seq[1:]))


def _split_if_choppy(run: Run, min_frames: int) -> list[Run]:
    """An interval shoot has a pause or two; walking around shooting has
    dozens. Too many: the stretches between pauses stand alone."""
    if run.pauses <= 2 + len(run.frames) // 200:
        return [run]
    interval = run.interval()
    parts, cur = [], Run([run.frames[0]])
    for a, b in zip(run.frames, run.frames[1:]):
        if abs((b.t - a.t) - interval) <= _tol(interval):
            cur.frames.append(b)
        else:
            parts.append(cur)
            cur = Run([b])
    parts.append(cur)
    return [p for p in parts if len(p.frames) >= min_frames]


def key_of(ids: list[int]) -> str:
    return hashlib.sha1(",".join(map(str, sorted(ids))).encode()).hexdigest()[:20]


def _overlap(a: set[int], b: set[int]) -> float:
    return len(a & b) / max(1, min(len(a), len(b)))


def refresh(conn: sqlite3.Connection) -> int:
    """Look at the whole library again; returns how many timelapses are new.
    Runs the user confirmed, dismissed or stacked keep that answer - matched
    by sharing most of their frames - and grow as more frames arrive."""
    from lunelis.settings import Settings
    s = Settings(conn)
    if not s.get("timelapse_detect"):
        return 0
    runs = detect(frames(conn), s.get("timelapse_min_frames"), s.get("timelapse_split_gaps"),
                  s.get("timelapse_max_pause_minutes") * 60.0)
    old = [(sid, set(json.loads(ids)), status, origin) for sid, ids, status, origin in conn.execute(
        "SELECT id, file_ids, status, origin FROM sequences")]
    seen: set[int] = set()
    added = 0
    for run in runs:
        ids = run.ids
        hit = next((o for o in old if o[3] == "auto" and _overlap(o[1], set(ids)) >= 0.5), None)
        detail = json.dumps({"interval": round(run.interval(), 1), "pauses": run.pauses})
        if hit:
            seen.add(hit[0])
            if hit[1] != set(ids) and hit[2] != "dismissed":
                conn.execute("UPDATE sequences SET file_ids = ?, frames = ?, detail = ?, key = ? WHERE id = ?",
                             (json.dumps(ids), len(ids), detail, key_of(ids), hit[0]))
                if stack_of(conn, hit[0]):
                    _restack(conn, hit[0], ids)
            continue
        if any(o[3] == "manual" and _overlap(o[1], set(ids)) >= 0.5 for o in old):
            continue                                   # the user already made this one by hand
        sid = conn.execute(
            "INSERT OR IGNORE INTO sequences (key, kind, origin, file_ids, frames, detail) VALUES (?, 'timelapse', 'auto', ?, ?, ?)",
            (key_of(ids), json.dumps(ids), len(ids), detail)).lastrowid
        if sid:
            added += 1
            if s.get("timelapse_auto_stack") and len(ids) >= s.get("timelapse_auto_stack_frames"):
                stack(conn, sid)
    # Found-but-unanswered runs that no longer show up (frames deleted) go.
    for sid, _ids, status, origin in old:
        if origin == "auto" and status == "found" and sid not in seen:
            unstack_sequence(conn, sid)
            conn.execute("DELETE FROM sequences WHERE id = ?", (sid,))
    conn.commit()
    return added


# --- what the user can do --------------------------------------------------------------------

@dataclass
class Sequence:
    id: int
    kind: str
    origin: str
    status: str
    file_ids: list[int]
    interval: float | None
    pauses: int
    stacked: bool


def all_sequences(conn: sqlite3.Connection, include_dismissed: bool = False) -> list[Sequence]:
    out = []
    for sid, kind, origin, status, ids, detail in conn.execute(
            "SELECT id, kind, origin, status, file_ids, detail FROM sequences"
            + ("" if include_dismissed else " WHERE status != 'dismissed'") + " ORDER BY id DESC"):
        d = json.loads(detail or "{}")
        out.append(Sequence(sid, kind, origin, status, json.loads(ids), d.get("interval"), d.get("pauses", 0),
                            stack_of(conn, sid) is not None))
    return out


def get(conn: sqlite3.Connection, sid: int) -> Sequence | None:
    return next((q for q in all_sequences(conn, True) if q.id == sid), None)


def sequence_of(conn: sqlite3.Connection, file_id: int) -> int | None:
    """The timelapse a photo (or its RAW+JPEG partner) belongs to, if any."""
    ids = {file_id}
    row = conn.execute("SELECT pair_of FROM files WHERE id = ?", (file_id,)).fetchone()
    if row and row[0]:
        ids.add(row[0])
    ids |= {r[0] for r in conn.execute("SELECT id FROM files WHERE pair_of = ?", (file_id,))}
    for sid, fids in conn.execute("SELECT id, file_ids FROM sequences WHERE status != 'dismissed'"
                                  " AND kind = 'timelapse'"):
        if ids & set(json.loads(fids)):
            return sid
    return None


def set_status(conn: sqlite3.Connection, sid: int, status: str) -> None:
    conn.execute("UPDATE sequences SET status = ?, answered_at = datetime('now') WHERE id = ?", (status, sid))
    if status == "dismissed":
        unstack_sequence(conn, sid)
    conn.commit()


def make_manual(conn: sqlite3.Connection, ids: list[int]) -> int:
    """Photo > Collapse into a timelapse: any number of frames, in shooting order."""
    if len(ids) < 2:
        raise ValueError("a timelapse needs at least two photos")
    # A RAW+JPEG pair is one frame: the JPEG half goes when its RAW is there too.
    # Asked in pieces: a timelapse of tens of thousands of frames went past
    # SQLite's limit on values in one statement (0.54).
    from lunelis import stacks
    order = stacks.shooting_order(conn, ids)
    in_order = set(order)
    # A photo is in one timelapse: take it out of any other.
    for sid, fids in conn.execute("SELECT id, file_ids FROM sequences WHERE status != 'dismissed'").fetchall():
        if in_order & set(json.loads(fids)):
            set_status(conn, sid, "dismissed")
    sid = conn.execute(
        "INSERT INTO sequences (key, kind, origin, status, file_ids, frames, detail) VALUES (?, 'timelapse', 'manual',"
        " 'confirmed', ?, ?, '{}')", (key_of(order) + f"-m{datetime.now():%Y%m%d%H%M%S%f}", json.dumps(order), len(order))).lastrowid
    conn.commit()
    return sid


# --- stacking: a timelapse as one tile in the library ---------------------------------------------

def stack_of(conn: sqlite3.Connection, sid: int) -> int | None:
    row = conn.execute("SELECT stack_id FROM sequences WHERE id = ?", (sid,)).fetchone()
    if not row or row[0] is None:
        return None
    exists = conn.execute("SELECT 1 FROM stacks WHERE id = ?", (row[0],)).fetchone()
    return row[0] if exists else None


def _restack(conn: sqlite3.Connection, sid: int, ids: list[int]) -> None:
    unstack_sequence(conn, sid)
    stack(conn, sid, ids)


def stack(conn: sqlite3.Connection, sid: int, ids: list[int] | None = None) -> int:
    """Show the timelapse as one tile (its first frame) with a frame count."""
    if ids is None:
        q = get(conn, sid)
        ids = q.file_ids if q else []
    if not ids or stack_of(conn, sid):
        return stack_of(conn, sid) or 0
    # Its frames leave any burst stack they were in (a photo is in one stack).
    from lunelis import stacks                   # in pieces: a long timelapse is past one statement's limit (0.54)
    for old in stacks.stacks_holding(conn, ids):
        conn.execute("DELETE FROM stack_files WHERE stack_id = ?", (old,))
        conn.execute("DELETE FROM stacks WHERE id = ?", (old,))
    # RAW+JPEG partners ride along, so the pair stays together behind the cover.
    in_ids = set(ids)
    members = list(ids) + [p for p in stacks.pair_partners(conn, ids) if p not in in_ids]
    st = conn.execute("INSERT INTO stacks (kind, cover_file_id, cover_chosen, size) VALUES ('timelapse', ?, 0, ?)",
                      (ids[0], len(ids))).lastrowid
    conn.executemany("INSERT OR IGNORE INTO stack_files (stack_id, file_id, position) VALUES (?, ?, ?)",
                     [(st, fid, i) for i, fid in enumerate(members)])
    conn.execute("UPDATE sequences SET stack_id = ? WHERE id = ?", (st, sid))
    conn.commit()
    return st


def unstack_sequence(conn: sqlite3.Connection, sid: int) -> None:
    st = stack_of(conn, sid)
    if st:
        conn.execute("DELETE FROM stack_files WHERE stack_id = ?", (st,))
        conn.execute("DELETE FROM stacks WHERE id = ?", (st,))
    conn.execute("UPDATE sequences SET stack_id = NULL WHERE id = ?", (sid,))
    conn.commit()


# --- timelapse <-> burst ------------------------------------------------------------------------

def to_burst(conn: sqlite3.Connection, sid: int) -> int:
    """A short run (50 frames or fewer) that's really a burst: one burst stack."""
    q = get(conn, sid)
    if q is None or len(q.file_ids) > BURST_MAX:
        raise ValueError(f"only a run of {BURST_MAX} frames or fewer can be a burst")
    unstack_sequence(conn, sid)
    conn.execute("UPDATE sequences SET kind = 'burst', status = 'confirmed' WHERE id = ?", (sid,))
    ids = q.file_ids
    qq = ",".join("?" * len(ids))
    for (old,) in conn.execute(f"SELECT DISTINCT stack_id FROM stack_files WHERE file_id IN ({qq})", ids).fetchall():
        conn.execute("DELETE FROM stack_files WHERE stack_id = ?", (old,))
        conn.execute("DELETE FROM stacks WHERE id = ?", (old,))
    st = conn.execute("INSERT INTO stacks (kind, cover_file_id, cover_chosen, size) VALUES ('chosen_burst', ?, 0, ?)",
                      (ids[0], len(ids))).lastrowid
    conn.executemany("INSERT INTO stack_files (stack_id, file_id, position) VALUES (?, ?, ?)",
                     [(st, fid, i) for i, fid in enumerate(ids)])
    conn.execute("UPDATE sequences SET stack_id = ? WHERE id = ?", (st, sid))
    conn.commit()
    return st


def burst_to_timelapse(conn: sqlite3.Connection, stack_id: int) -> int:
    """A burst stack that's really a short timelapse: unstacked, and kept as a
    timelapse (the burst finder won't stack those frames again)."""
    from lunelis import stacks
    ids = stacks.members(conn, stack_id)
    if len(ids) > BURST_MAX:
        raise ValueError(f"a burst has {BURST_MAX} frames at most")
    row = conn.execute("SELECT id FROM sequences WHERE stack_id = ?", (stack_id,)).fetchone()
    stacks.unstack(conn, stack_id)                 # remembered: never stacked as a burst again
    if row:
        conn.execute("UPDATE sequences SET kind = 'timelapse', stack_id = NULL WHERE id = ?", (row[0],))
        conn.commit()
        return row[0]
    return make_manual(conn, ids)
