"""
The jobs engine (Phase 1, Step 8).

A job is a kind + options + a list of folders (`job_folders`). The runner
works through pending folders one at a time and marks each done in the
catalog as it finishes, so:

- pause / quit / power-off lose at most the folder in progress (and every
  hash computed inside it was already saved), and the job resumes at the
  first unfinished folder;
- a job whose share went to sleep switches to `waiting` and retries instead
  of failing thousands of files;
- `options.schedule` gates when it may run: "now", "idle" (no keyboard/mouse
  for N minutes) or a nightly "window" of hours.

Kinds are plain functions (root_id, folder) -> FolderResult, registered in
KINDS; the engine knows nothing about what a kind does to a folder.
"""
from __future__ import annotations

import ctypes
import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from lunelis.dupes import detect
from lunelis.dupes.hashing import SourceOffline, Throttle, full_hash

OFFLINE_RETRY_S = 60

StatusFn = Callable[[int, str], None]      # (job id, status line)


# --- kinds ---------------------------------------------------------------------------

def _full_hash_folder(conn, root_id, folder, *, throttle, should_cancel, workers):
    """Integrity baseline: full sha256 of every file in the folder."""
    from concurrent.futures import ThreadPoolExecutor

    result = detect.FolderResult()
    rows = [r for r in conn.execute(
        "SELECT f.id, r.path, f.rel_path FROM files f JOIN roots r ON r.id = f.root_id"
        f" WHERE f.root_id = ? AND {detect.LIVE} AND f.content_hash IS NULL", (root_id,))
        if detect._dir_of(r[2]) == folder]

    def work(row):
        fid, root, rel = row
        if should_cancel and should_cancel():
            return fid, None, 0
        digest, n = full_hash(detect._abs(root, rel), throttle, should_cancel)
        return fid, digest, n

    with ThreadPoolExecutor(max_workers=max(1, min(workers, 4))) as pool:
        done = []
        for fid, digest, n in pool.map(work, rows):
            result.bytes_read += n
            if digest:
                done.append((digest, fid))
                result.hashed += 1
    conn.executemany("UPDATE files SET content_hash = ? WHERE id = ?", done)
    conn.commit()
    result.cancelled = bool(should_cancel and should_cancel())
    return result


def _verify_folder(conn, root_id, folder, *, throttle, should_cancel, workers):
    """Full-hash every 'likely' group that has a member in this folder, turning
    them into verified 'exact' groups (or dissolving a sample collision)."""
    result = detect.FolderResult()
    groups = [gid for gid, rel in conn.execute(
        "SELECT DISTINCT g.id, f.rel_path FROM duplicate_groups g"
        " JOIN duplicate_group_files m ON m.group_id = g.id JOIN files f ON f.id = m.file_id"
        " WHERE g.method = 'sampled' AND f.root_id = ?", (root_id,)) if detect._dir_of(rel) == folder]
    for gid in dict.fromkeys(groups):
        if should_cancel and should_cancel():
            result.cancelled = True
            return result
        before = conn.execute("SELECT COALESCE(SUM(f.size_bytes), 0) FROM duplicate_group_files m"
                              " JOIN files f ON f.id = m.file_id WHERE m.group_id = ?"
                              " AND f.content_hash IS NULL", (gid,)).fetchone()[0]
        result.groups.update(detect.verify_group(conn, gid, throttle=throttle,
                                                 should_cancel=should_cancel, workers=min(workers, 4)))
        result.bytes_read += before
        result.hashed += 1
    return result


def _integrity_folder(conn, root_id, folder, *, throttle, should_cancel, workers, options=None):
    """Re-hash files that have a baseline hash; a mismatch while size and
    date are unchanged is silent corruption (the scanner clears the baseline
    whenever a file is really edited, so a remaining hash still describes
    the current size/date).

    The rolling check (jobs/rolling.py) passes `only_ids`: just those files,
    and a file with no baseline yet gets one. Every file read gets checked_at."""
    from concurrent.futures import ThreadPoolExecutor

    only = set((options or {}).get("only_ids") or ())
    result = detect.FolderResult()
    rows = [r for r in conn.execute(
        "SELECT f.id, r.path, f.rel_path, f.content_hash FROM files f JOIN roots r ON r.id = f.root_id"
        f" WHERE f.root_id = ? AND {detect.LIVE}" + ("" if only else " AND f.content_hash IS NOT NULL"),
        (root_id,))
        if detect._dir_of(r[2]) == folder and (not only or r[0] in only)]

    def work(row):
        fid, root, rel, baseline = row
        digest, n = full_hash(detect._abs(root, rel), throttle, should_cancel)
        return fid, baseline, digest, n

    changed, baselines, checked = [], [], []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 4))) as pool:
        for fid, baseline, digest, n in pool.map(work, rows):
            result.bytes_read += n
            result.hashed += 1
            if not digest:
                continue
            checked.append((fid,))
            if baseline is None:
                baselines.append((digest, fid))
            elif digest != baseline:
                changed.append((fid, f"baseline {baseline[:12]}..., now {digest[:12]}..."))
    conn.executemany(
        "INSERT INTO damaged (file_id, problem, detail) VALUES (?, 'changed_on_disk', ?)"
        " ON CONFLICT(file_id) DO UPDATE SET problem = 'changed_on_disk', detail = excluded.detail",
        changed)
    conn.executemany("UPDATE files SET content_hash = ? WHERE id = ? AND content_hash IS NULL", baselines)
    conn.executemany("UPDATE files SET checked_at = datetime('now') WHERE id = ?", checked)
    conn.commit()
    result.cancelled = bool(should_cancel and should_cancel())
    return result


def _migrate_folder(conn, root_id, folder, **kw):
    from lunelis.migrate.execute import migrate_folder
    return migrate_folder(conn, root_id, folder, **kw)


def _backup_folder(conn, root_id, folder, **kw):
    from lunelis.backups.core import backup_folder
    return backup_folder(conn, root_id, folder, **kw)


def _backup_verify_folder(conn, root_id, folder, **kw):
    from lunelis.backups.core import verify_folder
    return verify_folder(conn, root_id, folder, **kw)


def _scene_tags_folder(conn, root_id, folder, **kw):
    from lunelis.recognize.scenes import job_folder
    return job_folder(conn, root_id, folder, **kw)


def _backup_finish(conn, job_id):
    from lunelis.backups.core import finish
    finish(conn, job_id)


def _migrate_finish(conn, job_id):
    from lunelis.migrate.execute import finish
    finish(conn, job_id)


KINDS = {
    "duplicates": detect.process_folder,
    "verify": _verify_folder,
    "full_hash": _full_hash_folder,
    "integrity": _integrity_folder,
    "migrate": _migrate_folder,
    "backup": _backup_folder,
    "backup_verify": _backup_verify_folder,
    "scene_tags": _scene_tags_folder,
}

# Kinds that need the job's options (e.g. which backup set) passed in.
OPTION_KINDS = {"backup", "backup_verify", "integrity"}

# Run once when a job's last folder is done (a kind that has an "afterwards").
FINISHERS = {
    "migrate": _migrate_finish,
    "backup": _backup_finish,
    "backup_verify": _backup_finish,
}


# --- creating / listing -----------------------------------------------------------------

def create_job(conn: sqlite3.Connection, kind: str, title: str,
               scope: list[tuple[int, str | None]], options: dict | None = None,
               folders: list[tuple[int, str, int]] | None = None) -> int:
    """`folders` = [(root id, folder, files)] to work through, when the kind
    has already planned them (a migration); otherwise they come from `scope`."""
    if kind not in KINDS:
        raise ValueError(f"unknown job kind {kind!r}")
    options = {"schedule": {"mode": "now"}, "workers": 8, "mb_per_s": None,
               **(options or {}), "scope": scope}
    if folders is None:
        folders = detect.plan_folders(conn, scope)
    job_id = conn.execute(
        "INSERT INTO jobs (kind, title, options, files_total, updated_at) VALUES (?, ?, ?, ?, datetime('now'))",
        (kind, title, json.dumps(options), sum(n for _, _, n in folders))).lastrowid
    conn.executemany("INSERT INTO job_folders (job_id, root_id, folder, files) VALUES (?, ?, ?, ?)",
                     [(job_id, rid, f, n) for rid, f, n in folders])
    conn.commit()
    return job_id


def set_state(conn: sqlite3.Connection, job_id: int, state: str, status: str | None = None) -> None:
    import logging
    logging.getLogger("lunelis.jobs").info("job %s -> %s%s", job_id, state, f" ({status})" if status else "")
    fin = ", finished_at = datetime('now')" if state in ("done", "cancelled", "failed") else ""
    conn.execute(f"UPDATE jobs SET state = ?, status = COALESCE(?, status), updated_at = datetime('now'){fin}"
                 " WHERE id = ?", (state, status, job_id))
    conn.commit()


def recover_interrupted(conn: sqlite3.Connection) -> int:
    """At start-up: a job still marked running was cut off (quit, crash, power
    loss). Queue it again - its finished folders are already recorded."""
    n = conn.execute("UPDATE jobs SET state = 'queued', status = 'Resuming after an interruption'"
                     " WHERE state IN ('running', 'waiting')").rowcount
    conn.commit()
    return n


def next_runnable(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT id FROM jobs WHERE state = 'queued' ORDER BY id LIMIT 1").fetchone()
    return row[0] if row else None


# --- schedule -------------------------------------------------------------------------

def idle_seconds() -> float:
    """Seconds since the last keyboard/mouse input (Windows); 0 elsewhere."""
    if sys.platform != "win32":
        return 0.0

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    return (ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 1000.0


def schedule_allows(schedule: dict, now: datetime | None = None,
                    idle: Callable[[], float] = idle_seconds) -> tuple[bool, str]:
    mode = schedule.get("mode", "now")
    if mode == "idle":
        need = schedule.get("idle_minutes", 5) * 60
        return (idle() >= need, f"Waiting until the PC has been idle {need // 60} min")
    if mode == "window":
        start, end = schedule.get("start_hour", 22), schedule.get("end_hour", 6)
        h = (now or datetime.now()).hour
        inside = start <= h < end if start < end else (h >= start or h < end)
        return inside, f"Scheduled for {start:02d}:00-{end:02d}:00"
    return True, ""


# --- running --------------------------------------------------------------------------

@dataclass
class RunOutcome:
    state: str
    status: str


def run_job(conn: sqlite3.Connection, job_id: int, *, should_stop: Callable[[], bool] = lambda: False,
            on_status: StatusFn | None = None, idle: Callable[[], float] = idle_seconds) -> RunOutcome:
    """Work through a job's pending folders until it's done, stopped, or has to
    wait (schedule / offline). `should_stop` = the user pressed pause or quit."""
    kind, options = conn.execute("SELECT kind, options FROM jobs WHERE id = ?", (job_id,)).fetchone()
    options = json.loads(options)
    fn = KINDS[kind]
    throttle = Throttle(options.get("mb_per_s"))
    workers = options.get("workers", 8)

    def status(text: str) -> None:
        conn.execute("UPDATE jobs SET status = ?, updated_at = datetime('now') WHERE id = ?", (text, job_id))
        conn.commit()
        if on_status:
            on_status(job_id, text)

    set_state(conn, job_id, "running")
    while True:
        if should_stop():
            set_state(conn, job_id, "paused", "Paused")
            return RunOutcome("paused", "Paused")
        ok, why = schedule_allows(options.get("schedule", {}), idle=idle)
        if not ok:
            set_state(conn, job_id, "waiting", why)
            return RunOutcome("waiting", why)

        row = conn.execute(
            "SELECT jf.root_id, jf.folder, r.path FROM job_folders jf JOIN roots r ON r.id = jf.root_id"
            " WHERE jf.job_id = ? AND jf.state = 'pending' ORDER BY jf.rowid LIMIT 1", (job_id,)).fetchone()
        if row is None:
            if kind in FINISHERS:
                FINISHERS[kind](conn, job_id)
            n = conn.execute("SELECT COUNT(*) FROM job_folders WHERE job_id = ?", (job_id,)).fetchone()[0]
            set_state(conn, job_id, "done", f"Finished {n:,} folder{'s' if n != 1 else ''}")
            return RunOutcome("done", "Finished")
        root_id, folder, root_path = row
        status(f"{root_path}\\{folder}" if folder else root_path)
        try:
            extra = {"options": options} if kind in OPTION_KINDS else {}
            res = fn(conn, root_id, folder, throttle=throttle, should_cancel=should_stop, workers=workers, **extra)
        except SourceOffline:
            msg = f"Waiting for {root_path} to come back online"
            set_state(conn, job_id, "waiting", msg)
            return RunOutcome("waiting", msg)
        if res.cancelled:
            set_state(conn, job_id, "paused", "Paused")
            return RunOutcome("paused", "Paused")
        conn.execute("UPDATE job_folders SET state = 'done', done_at = datetime('now')"
                     " WHERE job_id = ? AND root_id = ? AND folder = ?", (job_id, root_id, folder))
        conn.execute("UPDATE jobs SET files_done = files_done + (SELECT files FROM job_folders"
                     " WHERE job_id = ? AND root_id = ? AND folder = ?), bytes_read = bytes_read + ?"
                     " WHERE id = ?", (job_id, root_id, folder, res.bytes_read, job_id))
        conn.commit()


def pause(conn: sqlite3.Connection, job_id: int) -> None:
    set_state(conn, job_id, "paused", "Paused")


def resume(conn: sqlite3.Connection, job_id: int) -> None:
    set_state(conn, job_id, "queued", "Queued")


def cancel(conn: sqlite3.Connection, job_id: int) -> None:
    set_state(conn, job_id, "cancelled", "Cancelled - hashes already computed are kept")


def wake_waiting(conn: sqlite3.Connection) -> int:
    """Re-queue waiting jobs (called every OFFLINE_RETRY_S by the app)."""
    n = conn.execute("UPDATE jobs SET state = 'queued' WHERE state = 'waiting'").rowcount
    conn.commit()
    return n
