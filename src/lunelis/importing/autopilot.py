"""
Autopilot Import: card in, finished shoot out - then you review it.

After an import (verified as always) and the scan that catalogues it, the
autopilot works through these stages, each recorded as it finishes so an
interrupted run carries on where it stopped:

| stage   | what it does                                                     | in the review            |
|---------|------------------------------------------------------------------|--------------------------|
| bursts  | each burst's sharpest frame becomes its cover                    | Keep / Undo              |
| scenes  | scene-tag suggestions (when the scene model is on)               | Keep / Undo              |
| event   | an event with a suggested name ("Beach · Oct 4, 2026")           | Keep (rename later) / Undo |
| edits   | an edit in your style per photo (Learn My Look) - NOT applied    | Apply / Skip             |
| album   | a draft album of the best frames                                 | Keep / Undo              |
| reel    | a highlight reel of the best frames - NOT made yet               | Make it / Skip           |

Nothing is set aside, deleted, renamed or edited before you've reviewed it:
the first stages only add things that Undo takes away again, and edits and
the reel wait for your click. Any stage can be turned off
(`autopilot_skip`); a stage that can't run (no scene model, no learned look)
says why and is skipped.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np

STAGES = ("bursts", "scenes", "event", "edits", "album", "reel")
TITLES = {"bursts": "Best frame of each burst", "scenes": "Scene tags", "event": "An event",
          "edits": "Edits in your style", "album": "A draft album", "reel": "A highlight reel"}
REEL_MAX = 30
VIDEO = ("mp4", "mov", "mpeg-ts")


# --- runs ------------------------------------------------------------------------------------------

def create_run(conn: sqlite3.Connection, import_id: int) -> int:
    from lunelis.settings import Settings
    skip = set(Settings(conn).get("autopilot_skip"))
    stages = {s: {"status": "off" if s in skip else "pending", "summary": "", "data": {}} for s in STAGES}
    rid = conn.execute("INSERT INTO autopilot_runs (import_id, stages) VALUES (?, ?)",
                       (import_id, json.dumps(stages))).lastrowid
    conn.commit()
    return rid


def get(conn: sqlite3.Connection, run_id: int) -> dict | None:
    row = conn.execute("SELECT id, import_id, state, stages, file_ids, created_at FROM autopilot_runs WHERE id = ?",
                       (run_id,)).fetchone()
    if row is None:
        return None
    return {"id": row[0], "import_id": row[1], "state": row[2], "stages": json.loads(row[3]),
            "file_ids": json.loads(row[4] or "[]"), "created_at": row[5]}


def _save(conn, run: dict) -> None:
    conn.execute("UPDATE autopilot_runs SET state = ?, stages = ?, file_ids = ? WHERE id = ?",
                 (run["state"], json.dumps(run["stages"]), json.dumps(run["file_ids"]), run["id"]))
    conn.commit()


def unfinished(conn: sqlite3.Connection) -> list[int]:
    return [r[0] for r in conn.execute("SELECT id FROM autopilot_runs WHERE state = 'running' ORDER BY id")]


def to_review(conn: sqlite3.Connection) -> list[int]:
    return [r[0] for r in conn.execute("SELECT id FROM autopilot_runs WHERE state = 'review' ORDER BY id DESC")]


# --- helpers ---------------------------------------------------------------------------------------

def _chunks(ids, n: int = 900):
    """A very large import has more photos than SQLite takes values in one
    statement (32,766): every "IN (...)" over the import's photos is asked in
    pieces. One stage used to fail with "too many SQL variables" (0.54)."""
    ids = list(ids)
    for i in range(0, len(ids), n):
        yield ids[i:i + n]


def _marks(chunk) -> str:
    return ",".join("?" * len(chunk))


def _by_time(rows) -> list:
    """Rows of (id, captured_at, ...) as ORDER BY e.captured_at, f.id gave them: no time first."""
    return sorted(rows, key=lambda r: (r[1] is not None, r[1] or "", r[0]))


def _photos(conn, ids: list[int]) -> list[int]:
    if not ids:
        return []
    rows = []
    for chunk in _chunks(ids):
        rows += conn.execute(
            f"SELECT f.id, e.captured_at FROM files f LEFT JOIN exif e ON e.file_id = f.id"
            f" WHERE f.id IN ({_marks(chunk)}) AND COALESCE(f.format, '') NOT IN ({_marks(VIDEO)})",
            (*chunk, *VIDEO)).fetchall()
    return [r[0] for r in _by_time(rows)]


def _thumb_gray(conn, fid: int, thumbs: Path) -> np.ndarray | None:
    from PIL import Image
    row = conn.execute("SELECT thumbnail_path FROM files WHERE id = ?", (fid,)).fetchone()
    if not row or not row[0]:
        return None
    try:
        return np.asarray(Image.open(thumbs / row[0]).convert("L"), np.float32)
    except OSError:
        return None


def sharpness(img: np.ndarray) -> float:
    import cv2
    return float(cv2.Laplacian(img, cv2.CV_32F).var())


def best_frames(conn, ids: list[int]) -> list[int]:
    """The photos worth showing: not rejected, and of a burst only its cover."""
    if not ids:
        return []
    rows = []
    for chunk in _chunks(ids):
        rows += conn.execute(
            f"SELECT f.id, e.captured_at, rt.flag, sf.stack_id, s.cover_file_id FROM files f"
            f" LEFT JOIN ratings rt ON rt.file_id = f.id"
            f" LEFT JOIN stack_files sf ON sf.file_id = f.id LEFT JOIN stacks s ON s.id = sf.stack_id"
            f" LEFT JOIN exif e ON e.file_id = f.id WHERE f.id IN ({_marks(chunk)})", chunk).fetchall()
    out = []
    for fid, _at, flag, stack, cover in _by_time(rows):
        if flag == "reject" or (stack is not None and cover != fid):
            continue
        out.append(fid)
    photos = set(_photos(conn, out))
    return [f for f in out if f in photos]


# --- stages ----------------------------------------------------------------------------------------

def _bursts(conn, ids, thumbs, data_dir, stop) -> tuple[str, str, dict]:
    from lunelis import stacks
    stacks.rebuild_from_settings(conn)
    sids = stacks.stacks_holding(conn, ids) if ids else []
    before, changed = {}, 0
    for sid in sids:
        if stop():
            break
        members = stacks.members(conn, sid)
        scored = [(sharpness(g), f) for f in members if (g := _thumb_gray(conn, f, thumbs)) is not None]
        if not scored:
            continue
        best = max(scored)[1]
        cover, chosen = conn.execute("SELECT cover_file_id, cover_chosen FROM stacks WHERE id = ?", (sid,)).fetchone()
        before[str(sid)] = [cover, chosen]
        if best != cover:
            stacks.set_cover(conn, best)
            changed += 1
    if not sids:
        return "done", "No bursts in this shoot.", {"before": {}}
    return "done", f"{len(sids)} burst{'s' if len(sids) != 1 else ''}: the sharpest frame of each is now its cover" + \
        (f" ({changed} changed)." if changed != len(sids) else "."), {"before": before}


def _scenes(conn, ids, thumbs, data_dir, stop):
    from lunelis.recognize import scenes
    rec = scenes.backend()
    if rec is None:
        return "skipped", "Scene tags are off - turn them on in Settings to have shoots tagged.", {}
    photos = _photos(conn, ids)

    def pairs():
        found = set()
        for chunk in _chunks(photos):
            found.update(tuple(r) for r in conn.execute(
                f"SELECT file_id, tag_id FROM file_tags WHERE confidence IS NOT NULL"
                f" AND file_id IN ({_marks(chunk)})", chunk))
        return found
    before = pairs() if photos else set()
    _emb, n = scenes.tag_files(conn, photos, rec, data_dir, stop)
    added = sorted(pairs() - before) if photos else []
    return "done", f"{n:,} scene suggestion{'s' if n != 1 else ''} to look over on the Tags page.", \
        {"ids": photos, "added": [list(p) for p in added]}


def _top_scene(conn, ids) -> str | None:
    if not ids:
        return None
    counts: dict[str, int] = {}
    for chunk in _chunks(ids):
        for name, c in conn.execute(
                f"SELECT t.name, COUNT(*) c FROM file_tags ft JOIN tags t ON t.id = ft.tag_id"
                f" WHERE ft.file_id IN ({_marks(chunk)}) AND t.name LIKE 'Scene|%' GROUP BY t.id", chunk):
            counts[name] = counts.get(name, 0) + c
    if not counts:
        return None
    name = max(counts, key=counts.get)
    return name.split("|")[-1] if counts[name] >= max(2, len(ids) // 5) else None


def _event_name(conn, ids) -> str:
    from lunelis.events.model import date_range_text
    spans = [conn.execute(f"SELECT MIN(captured_at), MAX(captured_at) FROM exif WHERE file_id IN ({_marks(chunk)})",
                          chunk).fetchone() for chunk in _chunks(ids)]
    spans = [s for s in spans if s and s[0]]
    row = (min(s[0] for s in spans), max(s[1] for s in spans)) if spans else None
    when = date_range_text(row[0], row[1]) if row and row[0] else datetime.now().strftime("%b %d, %Y").replace(" 0", " ")
    return f"{_top_scene(conn, ids) or 'Shoot'} · {when}"


def _event(conn, ids, thumbs, data_dir, stop):
    from lunelis.events import model as ev
    already = ev.events_of(conn, ids)
    if already and len(already) == len(ids):
        name = ev.get(conn, next(iter(already.values()))).name
        return "skipped", f"Already in the event \"{name}\" (from the import's name).", {}
    name = _event_name(conn, ids)
    eid = ev.create(conn, name, [f for f in ids if f not in already], source="import")
    return "done", f"Made the event \"{name}\" - rename it any time on the Albums page.", {"event_id": eid}


def _edits(conn, ids, thumbs, data_dir, stop):
    from lunelis.edit import look
    model = look.Model.load(Path(data_dir))
    if model is None:
        return "skipped", f"Learn My Look needs {look.MIN_EDITS} edited photos first.", {}
    edited = {r[0] for r in conn.execute("SELECT file_id FROM edits")}
    out = {}
    for fid in _photos(conn, ids):
        if stop():
            break
        if fid in edited:
            continue
        row = conn.execute("SELECT thumbnail_path FROM files WHERE id = ?", (fid,)).fetchone()
        if not row or not row[0]:
            continue
        from PIL import Image
        try:
            adj = model.predict(look.features(np.asarray(Image.open(Path(thumbs) / row[0]).convert("RGB"))))
        except OSError:
            continue
        if adj:
            out[str(fid)] = adj
    if not out:
        return "skipped", "Your look wouldn't change these photos.", {}
    return "waiting", f"An edit in your style for {len(out):,} photo{'s' if len(out) != 1 else ''} " \
                      f"(learned from {model.n:,} edits) - not applied yet.", {"edits": out}


def _album(conn, ids, thumbs, data_dir, stop, run=None):
    from lunelis.albums import model as albums
    best = best_frames(conn, ids)
    if not best:
        return "skipped", "No photos to put in an album.", {}
    name = _event_name(conn, ids) + " - best"
    aid = albums.create(conn, name, best)
    return "done", f"Made the album \"{name}\" with {len(best):,} photo{'s' if len(best) != 1 else ''}.", {"album_id": aid}


def _reel(conn, ids, thumbs, data_dir, stop):
    best = best_frames(conn, ids)[:REEL_MAX]
    if len(best) < 3:
        return "skipped", "Too few photos for a highlight reel.", {}
    return "waiting", f"A highlight reel of {len(best)} photos - not made yet.", {"ids": best}


RUNNERS = {"bursts": _bursts, "scenes": _scenes, "event": _event, "edits": _edits, "album": _album, "reel": _reel}


def run(conn: sqlite3.Connection, run_id: int, thumbs: Path, data_dir: Path,
        progress: Callable[[str], None] | None = None, stop: Callable[[], bool] = lambda: False) -> dict:
    """Work through the run's pending stages; each is saved as it finishes."""
    from lunelis.events.model import import_file_ids
    r = get(conn, run_id)
    if not r["file_ids"]:
        r["file_ids"] = import_file_ids(conn, r["import_id"])
        _save(conn, r)
    ids = r["file_ids"]
    for stage in STAGES:
        st = r["stages"][stage]
        if st["status"] != "pending":
            continue
        if stop():
            return r
        if progress:
            progress(TITLES[stage])
        if not ids:
            st.update(status="skipped", summary="No photos from this import are in the library yet.")
        else:
            try:
                status, summary, data = RUNNERS[stage](conn, ids, thumbs, data_dir, stop)
            except Exception as e:                 # one stage failing never stops the others
                status, summary, data = "failed", f"Couldn't do this: {e}", {}
            if stop() and status not in ("done", "waiting"):
                return r
            st.update(status=status, summary=summary, data=data)
        _save(conn, r)
    r["state"] = "review"
    _save(conn, r)
    return r


# --- the review ------------------------------------------------------------------------------------

def undo(conn: sqlite3.Connection, run_id: int, stage: str) -> None:
    r = get(conn, run_id)
    st = r["stages"][stage]
    d = st["data"]
    if stage == "bursts":
        for sid, (cover, chosen) in d.get("before", {}).items():
            conn.execute("UPDATE stacks SET cover_file_id = ?, cover_chosen = ? WHERE id = ?", (cover, chosen, int(sid)))
    elif stage == "scenes" and d.get("added"):
        # Only what this run suggested (still a suggestion): earlier ones and accepted tags stay.
        conn.executemany("DELETE FROM file_tags WHERE file_id = ? AND tag_id = ? AND confidence IS NOT NULL",
                         [tuple(p) for p in d["added"]])
    elif stage == "event" and d.get("event_id"):
        from lunelis.events import model as ev
        ev.delete(conn, d["event_id"])
    elif stage == "album" and d.get("album_id"):
        from lunelis.albums import model as albums
        albums.delete(conn, d["album_id"])
    st["status"] = "undone"
    conn.commit()
    _save(conn, r)


def apply_edits(conn: sqlite3.Connection, run_id: int) -> int:
    from lunelis.edit import store
    from lunelis.edit.stack import Stack
    r = get(conn, run_id)
    st = r["stages"]["edits"]
    n = 0
    for fid, adj in st["data"].get("edits", {}).items():
        if store.rev(conn, int(fid)):
            continue                                    # edited by hand since: yours wins
        store.save(conn, int(fid), Stack(adjust=adj), commit=False)
        conn.execute("UPDATE files SET thumbnail_path = NULL WHERE id = ?", (int(fid),))
        n += 1
    conn.commit()
    st.update(status="applied", summary=f"Applied to {n:,} photo{'s' if n != 1 else ''}.")
    _save(conn, r)
    return n


def make_reel(conn: sqlite3.Connection, run_id: int, folder: Path,
              progress: Callable[[int, int], None] | None = None) -> str:
    from lunelis.create import slideshow
    r = get(conn, run_id)
    st = r["stages"]["reel"]
    name = _event_name(conn, r["file_ids"]) + " - highlights"
    path = slideshow.make(conn, st["data"]["ids"], slideshow.SlideshowOptions(seconds=2.5, fade=0.6),
                          folder, name, progress)
    st.update(status="made", summary=f"Saved {Path(path).name} in the Create folder.", data={**st["data"], "path": path})
    _save(conn, r)
    return path


def skip(conn: sqlite3.Connection, run_id: int, stage: str) -> None:
    r = get(conn, run_id)
    r["stages"][stage].update(status="skipped", summary="Skipped.")
    _save(conn, r)


def finish(conn: sqlite3.Connection, run_id: int) -> None:
    """Done reviewing: what's left is kept; waiting stages are skipped."""
    r = get(conn, run_id)
    for st in r["stages"].values():
        if st["status"] == "waiting":
            st.update(status="skipped", summary="Skipped.")
    r["state"] = "done"
    conn.execute("UPDATE autopilot_runs SET finished_at = datetime('now') WHERE id = ?", (run_id,))
    _save(conn, r)
