"""
Catalog housekeeping (0.50): old bookkeeping rows are removed, so the catalog
doesn't grow for ever with records nobody looks at again. Run once a day.

What goes                                                     What stays
- finished / cancelled / failed jobs older than 90 days       a job a migration still points to
  (and their folder lists)
- the per-file rows of imports finished over 180 days ago     the import itself (event names use it)
- migration plans never run (planned / cancelled) after 30    every migration that ran: the Trash and
  days                                                        Quarantine restore from its records, and
                                                              its CSV logs are on disk anyway

Nothing here touches a photo, a rating, an edit or a file on disk.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

JOB_DAYS = 90
IMPORT_DAYS = 180
PLAN_DAYS = 30


@dataclass
class Pruned:
    jobs: int = 0
    import_items: int = 0
    plans: int = 0

    @property
    def total(self) -> int:
        return self.jobs + self.import_items + self.plans


def prune(conn: sqlite3.Connection, now: str = "now") -> Pruned:
    out = Pruned()
    out.plans = conn.execute(
        "DELETE FROM migrations WHERE state IN ('planned', 'cancelled')"
        " AND created_at < datetime(?, ?)", (now, f"-{PLAN_DAYS} days")).rowcount
    out.jobs = conn.execute(
        "DELETE FROM jobs WHERE state IN ('done', 'cancelled', 'failed')"
        " AND COALESCE(finished_at, updated_at, created_at) < datetime(?, ?)"
        " AND id NOT IN (SELECT job_id FROM migrations WHERE job_id IS NOT NULL)",
        (now, f"-{JOB_DAYS} days")).rowcount
    out.import_items = conn.execute(
        "DELETE FROM import_items WHERE import_id IN (SELECT id FROM imports WHERE state = 'done'"
        " AND COALESCE(finished_at, created_at) < datetime(?, ?))", (now, f"-{IMPORT_DAYS} days")).rowcount
    conn.commit()
    return out


def prune_daily(conn: sqlite3.Connection) -> Pruned | None:
    """prune() at most once a day (setting housekeeping_last)."""
    from datetime import date
    from lunelis.settings import Settings
    s = Settings(conn)
    today = date.today().isoformat()
    if s.get("housekeeping_last") == today:
        return None
    res = prune(conn)
    s.set("housekeeping_last", today)
    return res
