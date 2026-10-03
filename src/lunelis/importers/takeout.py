"""
Google Takeout JSON sidecars -> dates and GPS (Phase 1, Step 10).

Google Photos exports each item's real "photo taken" time, GPS, description
and people as a JSON sidecar. For files with no EXIF of their own (Snapchat
saves, screenshots, re-encoded uploads) it's the only reliable date, and
without it date-sorting and date-based storage templates put them on the
day the export was unzipped.

Matching a JSON to its photo (all learned from the real export):
- the JSON's `title` is the original filename - use it, not the JSON's own
  name, which Google truncates (and organiser tools move into `_json/<album>/`);
- `name.jpg.supplemental-metadata(2).json` belongs to `name(2).jpg`;
- the same photo's JSON can appear once per album; any copy will do,
  preferring one with GPS or a description;
- a name present in several folders is resolved by the folder whose
  year/month matches the JSON's date; `name-edited.jpg` uses name.jpg's JSON.

The JSON never overrides the file's own metadata: it only fills captured_at
/ GPS where the file has none, and marks date_source = 'takeout'.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

_NUMBERED = re.compile(r"\((\d+)\)\.json$", re.I)
_YEAR_MONTH = re.compile(r"(?:^|/)((?:19|20)\d\d)/(\d\d)(?:/|$)")


@dataclass
class TakeoutResult:
    json_files: int = 0
    matched: int = 0
    dated: int = 0
    located: int = 0
    unmatched: list[str] = field(default_factory=list)


def is_takeout_json(name: str) -> bool:
    low = name.lower()
    return low.endswith(".json") and (".supplemental" in low or re.search(r"\.(jpe?g|png|heic|mp4|mov|gif|webp|dng|arw|hif)(\(\d+\))?\.json$", low) is not None)


def find_jsons(root: str) -> list[str]:
    out = []
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                    elif is_takeout_json(e.name):
                        out.append(e.path)
        except OSError:
            continue
    return out


def target_name(json_path: str, title: str) -> str:
    """The media filename a JSON describes: its title, with Google's (n)
    duplicate number moved to where the media file has it."""
    m = _NUMBERED.search(os.path.basename(json_path))
    if not m:
        return title
    stem, dot, ext = title.rpartition(".")
    return f"{stem}({m.group(1)}).{ext}" if dot else f"{title}({m.group(1)})"


def _parse(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or "title" not in d:
        return None
    taken = (d.get("photoTakenTime") or {}).get("timestamp")
    geo = d.get("geoData") or {}
    lat, lon = geo.get("latitude"), geo.get("longitude")
    if not lat and not lon:
        geo = d.get("geoDataExif") or {}
        lat, lon = geo.get("latitude"), geo.get("longitude")
    if lat == 0 and lon == 0:
        lat = lon = None
    return {
        "title": d["title"],
        "taken": int(taken) if taken and str(taken).isdigit() and int(taken) > 0 else None,
        "lat": lat, "lon": lon,
        "description": (d.get("description") or "").strip() or None,
        "people": [p.get("name") for p in d.get("people", []) if p.get("name")],
    }


def _local(ts: int) -> tuple[str, str]:
    local = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
    mins = int(local.utcoffset().total_seconds() // 60)
    return (local.replace(tzinfo=None).isoformat(timespec="seconds"),
            f"{'+' if mins >= 0 else '-'}{abs(mins) // 60:02d}:{abs(mins) % 60:02d}")


def import_root(conn: sqlite3.Connection, root_id: int) -> TakeoutResult:
    root = conn.execute("SELECT path FROM roots WHERE id = ?", (root_id,)).fetchone()[0]
    result = TakeoutResult()
    jsons = find_jsons(root)
    result.json_files = len(jsons)
    if not jsons:
        return result

    files_by_name: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for fid, rel, name in conn.execute(
            "SELECT id, rel_path, filename FROM files WHERE root_id = ? AND missing_since IS NULL AND excluded = 0", (root_id,)):
        files_by_name[name.lower()].append((fid, rel))

    best: dict[int, tuple[str, dict]] = {}              # file id -> (json path, data)

    def consider(fid: int, path: str, data: dict) -> None:
        old = best.get(fid)
        score = (data["lat"] is not None, data["description"] is not None, data["taken"] is not None)
        if old is None or score > (old[1]["lat"] is not None, old[1]["description"] is not None,
                                   old[1]["taken"] is not None):
            best[fid] = (path, data)

    def pick(cands: list[tuple[int, str]], data: dict) -> list[tuple[int, str]]:
        if len(cands) <= 1 or not data["taken"]:
            return cands
        local = datetime.fromtimestamp(data["taken"], tz=timezone.utc).astimezone()
        near = {(local.year, local.month)}
        for delta in (-1, 1):                          # time-zone edges of a month
            d = datetime.fromtimestamp(data["taken"] + delta * 86400, tz=timezone.utc).astimezone()
            near.add((d.year, d.month))
        hit = [c for c in cands if (m := _YEAR_MONTH.search(c[1])) and (int(m[1]), int(m[2])) in near]
        return hit or cands

    for path in jsons:
        data = _parse(path)
        if not data:
            continue
        name = target_name(path, data["title"]).lower()
        cands = pick(files_by_name.get(name, []), data)
        if len(cands) == 1:
            consider(cands[0][0], path, data)
        elif not cands:
            result.unmatched.append(os.path.relpath(path, root))
        # several equally good candidates: leave them - a wrong date is worse than none

    # "-edited" copies inherit their original's JSON.
    for name, entries in files_by_name.items():
        stem, dot, ext = name.rpartition(".")
        if dot and stem.endswith("-edited"):
            orig = files_by_name.get(f"{stem[:-7]}.{ext}", [])
            for fid, rel in entries:
                if fid in best:
                    continue
                same_folder = [o for o in orig if os.path.dirname(o[1]) == os.path.dirname(rel)]
                src = (same_folder or orig)
                if len(src) == 1 and src[0][0] in best:
                    best[fid] = best[src[0][0]]

    rows = []
    for fid, (path, d) in best.items():
        rows.append((fid, os.path.relpath(path, root).replace("\\", "/"), d["title"], d["taken"],
                     d["lat"], d["lon"], d["description"], json.dumps(d["people"]) if d["people"] else None))
    conn.executemany(
        "INSERT INTO takeout_meta (file_id, json_path, title, taken_utc, lat, lon, description, people)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(file_id) DO UPDATE SET json_path = excluded.json_path,"
        " title = excluded.title, taken_utc = excluded.taken_utc, lat = excluded.lat, lon = excluded.lon,"
        " description = excluded.description, people = excluded.people", rows)
    result.matched = len(rows)

    # Fill only what the file itself doesn't have.
    dated = located = 0
    for fid, taken, lat, lon in conn.execute(
            "SELECT t.file_id, t.taken_utc, t.lat, t.lon FROM takeout_meta t JOIN files f ON f.id = t.file_id"
            " WHERE f.root_id = ?", (root_id,)).fetchall():
        row = conn.execute("SELECT captured_at, gps_lat FROM exif WHERE file_id = ?", (fid,)).fetchone()
        if row is None:
            continue                                   # metadata pass hasn't run yet; next time
        if taken and not row[0]:
            at, off = _local(taken)
            conn.execute("UPDATE exif SET captured_at = ?, captured_offset = ?, date_source = 'takeout'"
                         " WHERE file_id = ?", (at, off, fid))
            dated += 1
        if lat is not None and row[1] is None:
            conn.execute("UPDATE exif SET gps_lat = ?, gps_lon = ? WHERE file_id = ?", (lat, lon, fid))
            located += 1
    conn.commit()
    result.dated, result.located = dated, located
    return result


def takeout_roots(conn: sqlite3.Connection) -> list[int]:
    """Roots that look like a Google Takeout export (by name or by a
    'Google Photos' folder near the top)."""
    out = []
    for rid, path in conn.execute("SELECT id, path FROM roots WHERE enabled = 1"):
        # The root's OWN name only - "takeout" anywhere in the full path also
        # matched unrelated folders that merely sit under one so named.
        if "takeout" in os.path.basename(os.path.normpath(path)).lower():
            out.append(rid)
            continue
        for sub in ("Google Photos", os.path.join("Takeout", "Google Photos")):
            if os.path.isdir(os.path.join(path, sub)):
                out.append(rid)
                break
    return out
