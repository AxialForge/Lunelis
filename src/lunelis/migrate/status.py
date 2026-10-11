"""
Where a migration stands, in plain numbers (0.55) - what the wizard's Run and
Release steps show. Read-only: a handful of counts from the catalog, cheap
enough to ask for every couple of seconds while the copy runs.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

COPIED = ("copied", "done", "kept", "released")


@dataclass
class Progress:
    migration_id: int
    state: str = "planned"          # the migration: planned | running | done | released | cancelled
    target: str = ""
    job_state: str = ""             # its job: queued | running | waiting | paused | done | cancelled | failed
    job_status: str = ""            # the job's own line ("Waiting for ... to come back")
    files_total: int = 0            # files that are to be copied
    files_done: int = 0
    bytes_total: int = 0
    bytes_done: int = 0
    failed: int = 0
    folders_total: int = 0
    folders_done: int = 0
    folder: str = ""                # the folder being copied now
    set_aside: int = 0              # identical copies that don't travel
    left: int = 0                   # left in place on purpose (unticked, damaged with an intact copy)
    kept: int = 0                   # originals waiting for "Release"
    released: int = 0

    @property
    def percent(self) -> int:
        """By size when there is one - a few big videos are most of the time."""
        if self.bytes_total:
            return min(100, int(self.bytes_done * 100 / self.bytes_total))
        return min(100, int(self.files_done * 100 / self.files_total)) if self.files_total else 0

    @property
    def copying(self) -> bool:
        return self.state == "running"

    @property
    def remaining_bytes(self) -> int:
        return max(0, self.bytes_total - self.bytes_done)

    def headline(self) -> str:
        if self.state == "planned":
            return "Not started yet"
        if self.state == "cancelled":
            return "This migration was cancelled"
        if self.state == "released":
            return "Finished - the originals have been released"
        if self.state == "done":
            return ("The copy is finished" if not self.failed
                    else f"The copy is finished, but {self.failed:,} file{'s' if self.failed != 1 else ''} failed")
        return {"paused": "Paused", "waiting": "Waiting", "queued": "Waiting for its turn",
                "failed": "Stopped by a problem", "cancelled": "Stopped"}.get(self.job_state, "Copying")


def read(conn: sqlite3.Connection, migration_id: int) -> Progress:
    p = Progress(migration_id)
    row = conn.execute("SELECT state, target, job_id FROM migrations WHERE id = ?", (migration_id,)).fetchone()
    if row is None:
        return p
    p.state, p.target, job = row[0], row[1] or "", row[2]
    for action, state, n, size in conn.execute(
            "SELECT action, state, COUNT(*), COALESCE(SUM(size), 0) FROM migration_items WHERE migration_id = ?"
            " GROUP BY action, state", (migration_id,)):
        if action == "move":
            p.files_total += n
            p.bytes_total += size
            if state in COPIED:
                p.files_done += n
                p.bytes_done += size
            p.failed += n if state == "failed" else 0
            p.kept += n if state == "kept" else 0
            p.released += n if state == "released" else 0
        elif action == "skip_duplicate":
            p.set_aside += n
        else:
            p.left += n
    if job is not None:
        j = conn.execute("SELECT state, status FROM jobs WHERE id = ?", (job,)).fetchone()
        if j:
            p.job_state, p.job_status = j[0] or "", j[1] or ""
        for state, n in conn.execute("SELECT state, COUNT(*) FROM job_folders WHERE job_id = ? GROUP BY state",
                                     (job,)):
            p.folders_total += n
            p.folders_done += n if state == "done" else 0
        cur = conn.execute("SELECT r.path, jf.folder FROM job_folders jf LEFT JOIN roots r ON r.id = jf.root_id"
                           " WHERE jf.job_id = ? AND jf.state = 'pending' ORDER BY jf.rowid LIMIT 1",
                           (job,)).fetchone()
        if cur and p.state == "running":
            p.folder = cur[1] or "(top folder)"
    return p


def time_left(seconds: float | None) -> str:
    if seconds is None:
        return ""
    if seconds < 90:
        return "about a minute left"
    if seconds < 3600 * 1.5:
        return f"about {round(seconds / 60):,} minutes left"
    return f"about {seconds / 3600:,.1f} hours left"
