"""
Regular file checks: a little of the library re-read every week (or month),
the files checked longest ago first, so over time every file is read again
and silent damage (a bad sector, a NAS bit flip) is caught while a backup
still holds a good copy.

Each run is an ordinary "integrity" job (jobs/engine.py) limited to the
chosen files (`only_ids`) and to idle time, so it never slows you down; a
file with no baseline hash yet gets one, so the next pass can compare.

Settings: `integrity_every` ("off" | "week" | "month"), `integrity_gb` (how
much is read per run), `integrity_last` (when the last run was planned).
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta

from lunelis.dupes.detect import LIVE, _dir_of

EVERY_DAYS = {"week": 7, "month": 30}
TITLE = "Regular file check"


def running(conn: sqlite3.Connection) -> bool:
    return conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE kind = 'integrity' AND title = ?"
        " AND state IN ('queued', 'running', 'waiting', 'paused')", (TITLE,)).fetchone()[0] > 0


def due(conn: sqlite3.Connection, now: datetime | None = None) -> bool:
    from lunelis.settings import Settings
    s = Settings(conn)
    days = EVERY_DAYS.get(s.get("integrity_every"))
    if not days or running(conn):
        return False
    last = s.get("integrity_last")
    if not last:
        return True
    now = now or datetime.now()
    return now - datetime.fromisoformat(last) >= timedelta(days=days)


def plan(conn: sqlite3.Connection, limit_bytes: int) -> list[tuple[int, int, str, int]]:
    """[(file id, root id, rel path, size)]: never-checked first, then the oldest checks,
    up to limit_bytes (at least one file)."""
    out, total = [], 0
    for fid, rid, rel, size in conn.execute(
            "SELECT f.id, f.root_id, f.rel_path, COALESCE(f.size_bytes, 0) FROM files f"
            f" JOIN roots r ON r.id = f.root_id WHERE {LIVE}"
            " ORDER BY f.checked_at IS NOT NULL, f.checked_at, f.id"):
        if out and total + size > limit_bytes:
            break
        out.append((fid, rid, rel, size))
        total += size
    return out


def start(conn: sqlite3.Connection, now: datetime | None = None) -> int | None:
    """Plan the next run as a job; returns its id (None when there's nothing to check)."""
    from lunelis.jobs import engine
    from lunelis.settings import Settings
    s = Settings(conn)
    chosen = plan(conn, int(s.get("integrity_gb") * 1e9))
    s.set("integrity_last", (now or datetime.now()).isoformat(timespec="seconds"))
    if not chosen:
        return None
    counts: dict[tuple[int, str], int] = defaultdict(int)
    for _fid, rid, rel, _size in chosen:
        counts[(rid, _dir_of(rel))] += 1
    folders = [(rid, folder, n) for (rid, folder), n in sorted(counts.items())]
    roots = sorted({rid for rid, _f, _n in folders})
    return engine.create_job(
        conn, "integrity", TITLE, [(rid, None) for rid in roots],
        {"only_ids": [c[0] for c in chosen], "rolling": True,
         "schedule": {"mode": "idle", "idle_minutes": 10}, "workers": 2},
        folders=folders)


def summary(conn: sqlite3.Connection) -> tuple[int, int, str | None]:
    """(files checked at least once, live files, oldest check)."""
    done, total, oldest = conn.execute(
        "SELECT SUM(f.checked_at IS NOT NULL), COUNT(*), MIN(f.checked_at) FROM files f"
        f" JOIN roots r ON r.id = f.root_id WHERE {LIVE}").fetchone()
    return done or 0, total or 0, oldest
