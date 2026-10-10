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
    json_read: int = 0              # how many of them had to be opened this time (new or changed)


def is_takeout_json(name: str) -> bool:
    low = name.lower()
    return low.endswith(".json") and (".supplemental" in low or re.search(r"\.(jpe?g|png|heic|mp4|mov|gif|webp|dng|arw|hif)(\(\d+\))?\.json$", low) is not None)


def find_jsons(root: str) -> list[str]:
    return [path for path, _, _ in _find_jsons(root)]


def _find_jsons(root: str) -> list[tuple[str, int, int]]:
    """[(path, size, mtime in ns)] - the size and time come with the folder
    listing on Windows, so knowing a JSON is unchanged costs no extra read."""
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
                        try:
                            st = e.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        out.append((e.path, st.st_size, st.st_mtime_ns))
        except OSError:
            continue
    return out


def _read_jsons(conn: sqlite3.Connection, root_id: int, root: str,
                found: list[tuple[str, int, int]]) -> tuple[list[tuple[str, dict | None]], int]:
    """What every JSON says, opening only those that are new or changed since
    they were last read (same size and time = same answer, remembered in
    takeout_jsons). Every scan used to open all of them again - 24,000 small
    reads over the network. Matching still sees every JSON, so photos added
    later are matched exactly as before. Returns ([(path, data)], files opened) (0.54)."""
    known = {rel: (size, mtime, data) for rel, size, mtime, data in conn.execute(
        "SELECT rel_path, size, mtime_ns, data FROM takeout_jsons WHERE root_id = ?", (root_id,))}
    out: list[tuple[str, dict | None]] = []
    fresh: list[tuple] = []
    seen: set[str] = set()
    for path, size, mtime in found:
        rel = os.path.relpath(path, root).replace("\\", "/")
        seen.add(rel)
        old = known.get(rel)
        if old is not None and (old[0], old[1]) == (size, mtime):
            data = None
            if old[2] is not None:
                try:
                    data = json.loads(old[2])
                except ValueError:
                    old = None                         # a damaged row: read the file again
            if old is not None:
                out.append((path, data))
                continue
        data = _parse(path)
        fresh.append((root_id, rel, size, mtime, json.dumps(data) if data else None))
        out.append((path, data))
    conn.executemany("INSERT OR REPLACE INTO takeout_jsons (root_id, rel_path, size, mtime_ns, data)"
                     " VALUES (?, ?, ?, ?, ?)", fresh)
    conn.executemany("DELETE FROM takeout_jsons WHERE root_id = ? AND rel_path = ?",
                     [(root_id, rel) for rel in known if rel not in seen])
    return out, len(fresh)


def target_name(json_path: str, title: str) -> str:
    """The media filename a JSON describes: its title, with Google's (n)
    duplicate number moved to where the media file has it."""
    m = _NUMBERED.search(os.path.basename(json_path))
    if not m:
        return title
    stem, dot, ext = title.rpartition(".")
    return f"{stem}({m.group(1)}).{ext}" if dot else f"{title}({m.group(1)})"


MAX_JSON_BYTES = 4 << 20                  # Google's are a few KB; anything huge isn't one of them


def _parse(path: str) -> dict | None:
    """The useful fields of one Takeout JSON, or None. Never raises: one odd
    file (deeply nested, huge, wrong types) must not stop the pass."""
    try:
        if os.path.getsize(path) > MAX_JSON_BYTES:
            return None
        return _parse_json(path)
    except Exception:                    # RecursionError, MemoryError, odd types...
        return None


def _parse_json(path: str) -> dict | None:
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    if not isinstance(d, dict) or not isinstance(d.get("title"), str):
        return None

    def obj(v) -> dict:
        return v if isinstance(v, dict) else {}

    def num(v) -> float | None:
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) <= 180 else None

    taken = obj(d.get("photoTakenTime")).get("timestamp")
    geo = obj(d.get("geoData"))
    lat, lon = num(geo.get("latitude")), num(geo.get("longitude"))
    if not lat and not lon:
        geo = obj(d.get("geoDataExif"))
        lat, lon = num(geo.get("latitude")), num(geo.get("longitude"))
    if (lat == 0 and lon == 0) or (lat is not None and abs(lat) > 90):
        lat = lon = None
    ts = int(taken) if isinstance(taken, (str, int)) and str(taken).isdigit() else 0
    desc = d.get("description")
    people = d.get("people") if isinstance(d.get("people"), list) else []
    return {
        "title": d["title"],
        "taken": ts if 0 < ts < 32503680000 else None,          # before year 3000
        "lat": lat, "lon": lon,
        "description": desc.strip() or None if isinstance(desc, str) else None,
        "people": [p["name"] for p in people if isinstance(p, dict) and isinstance(p.get("name"), str) and p["name"]],
    }


def _local(ts: int) -> tuple[str, str]:
    local = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
    mins = int(local.utcoffset().total_seconds() // 60)
    return (local.replace(tzinfo=None).isoformat(timespec="seconds"),
            f"{'+' if mins >= 0 else '-'}{abs(mins) // 60:02d}:{abs(mins) % 60:02d}")


def import_root(conn: sqlite3.Connection, root_id: int) -> TakeoutResult:
    root = conn.execute("SELECT path FROM roots WHERE id = ?", (root_id,)).fetchone()[0]
    result = TakeoutResult()
    found = _find_jsons(root)
    result.json_files = len(found)
    if not found:
        return result
    jsons, result.json_read = _read_jsons(conn, root_id, root, found)

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

    for path, data in jsons:
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
