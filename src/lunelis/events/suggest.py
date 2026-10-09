"""
Event suggestions - nothing here changes the catalog; the user accepts,
renames or dismisses each one.

1. From folder names. Libraries are often already organised into events:
   `6-19-2026 Air Show`, `9-6-2021_Air_Show`, `2019-07-04 Fireworks`,
   `Memories\\Aquarium`. The OUTERMOST named folder under a source is the
   event, so a trip's day folders (`Las Vegas Vaca\\Aug 17 grand canyon tour`)
   and working folders (`Clips`, `JPEG Files`) belong to it. Folders named
   like workflow rather than outings (`Timelaps`, `TL1`, `2024 Edits`,
   `Folder 1`) are ignored, and dates are taken out of names. The same name
   on consecutive days (`6-17-2024 Myrtle Beach` ... `6-20-2024 Myrtle Beach`)
   or in two places (old and new NAS pools) is one suggestion.
2. From gaps in capture time. Photos not in an event and not claimed by a
   folder suggestion are split wherever there's a long gap between shots; a
   run with enough photos over no more than a few weeks is suggested, named
   by its dates.

Only photos that aren't in an event yet are considered, and a dismissed
suggestion (keyed by what it is, not by id) doesn't come back.
"""
from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from lunelis.events.model import date_range_text

MAX_NAMED_SPAN = timedelta(days=21)   # a words-only folder spanning longer is an album, not an event
DATED_RUN_GAP = timedelta(days=3)     # a dated folder keeps the run of photos around its date
MAX_GAP_SPAN = timedelta(days=21)     # a gap run spanning longer is everyday life, not an event
MERGE_DAYS = timedelta(days=2)        # same name, this close together: one event
MIN_NAMED_PHOTOS = 3

_D = r"(?:\d{1,2}[-_.]\d{1,2}[-_.]\d{4}|\d{4}[-_.]\d{1,2}[-_.]\d{1,2}|(?:19|20)\d{6})"
_MDY = re.compile(r"^(\d{1,2})[-_.](\d{1,2})[-_.](\d{4})(?:[\s_\-]+(.*))?$")
_YMD = re.compile(r"^(\d{4})[-_.](\d{1,2})[-_.](\d{1,2})(?:[\s_\-]+(.*))?$")
_COMPACT = re.compile(r"^((?:19|20)\d{2})(\d{2})(\d{2})(?:[\s_\-]+(.*))?$")
_DATES_IN_NAME = re.compile(rf"(?<!\d){_D}(?!\d)|(?<!\d)\d{{1,2}}-\d{{1,2}}-\d{{2}}(?!\d)")
_SIBLING = re.compile(r"\s*\(\d+\)$")                    # "6-19-2026 Air Show (2)" from a name clash
_MONTH_NAMES = [date(2000, m, 1).strftime("%B").lower() for m in range(1, 13)] + \
               [date(2000, m, 1).strftime("%b").lower() for m in range(1, 13)]
_MONTHS = "|".join(_MONTH_NAMES)
_WORD_DATE = re.compile(rf"\b({_MONTHS})\.?\s+\d{{1,2}}(st|nd|rd|th)?(,?\s+(19|20)\d{{2}})?\b", re.I)
_GENERIC_WORDS = {
    "dcim", "private", "m4root", "clip", "clips", "avchd", "raw", "raws", "jpg", "jpgs", "jpeg", "heic",
    "dng", "final", "finals", "selects", "picks", "video", "videos", "photo", "photos", "pictures",
    "pics", "images", "album", "albums", "camera", "camera roll", "misc", "other", "others",
    "untitled", "backup", "backups", "old", "new", "temp", "tmp", "screenshots", "screenshot",
    "downloads", "download", "whatsapp", "whatsapp images", "instagram", "facebook", "snapchat",
    "wallpapers", "library", "originals", "proxy", "proxies", "thumbnails", "lightroom", "capture one",
    "_json", "json", "takeout", "google photos", "unsorted", "undated", "imports", "import", "archive",
    "jpeg files", "raw files", "jpg files", "memories", "vacation", "vaca", "trip",
    "original", "originals", "orig", "arw", "cr2", "cr3", "nef", "raf", "orf", "rw2", "from phone",
    "phone", "iphone", "android", "photos from", "no wm", "watermark", "watermarked", "wm", "hdr", "panorama", "pano",
}
_GENERIC_PATTERNS = [re.compile(p) for p in (
    r"\d{3}[a-z_]{5}",                                   # camera folders: 100MSDCF
    r"photos from \d{4}",                                # Google Takeout year albums
    r"(new )?folder( \d+)?",
    r".*\b(edit|edits|edited|export|exports|exported|to export)\b.*",
    r".*\b(t(ime)?[\s=\-]?la(p|ps|pse|pses)|timelapse|timelapses|hyperlapse)\b.*",
    r"tl ?\d*",
    r"d\d+|day \d+",
    r"(image |photo )?seq(u)?[ea]nce[s]?( \d+)*",
    r"(set|stack|batch|part|roll|card|group|shoot) ?\d*( \d+)*",
    r"albums?|album \w+",
    r"(hdr |pano(rama)? )?merge[sd]?( \d+)?( r)?|hdr|pano(rama)?",
    rf"({_MONTHS})( \d{{1,2}})?",                        # "Aug 21" - a day inside a trip
)]


def _clean_name(text: str | None) -> str | None:
    if not text:
        return None
    # Folders made on a Mac carry private-use characters (U+F0xx) for : ? * etc.
    name = " ".join(re.sub("[\uf000-\uf8ff]", " ", text).replace("_", " ").split())
    name = _DATES_IN_NAME.sub(" ", name)
    # A month-day with no year at the start ("03-02 Ski trip") - 0.49, it stayed in the name.
    name = re.sub(r"^\s*\d{1,2}[-.]\d{1,2}(?=\s+[^\d\s])", " ", name)
    name = _WORD_DATE.sub(" ", name)
    name = re.sub(r"\s*\([^)]*\)", " ", name)               # "JPEG (.JPG)", "Air Show (2)"
    name = re.sub(r"(?i)\s+(day|d|pt|part)\s*\d+$", "", " ".join(name.split()))   # "NYC day 3"
    name = " ".join(name.split()).strip(" -.,")
    # Judge the name without its years: "2024 Album" is an album, "Epcot 2017" an outing.
    core = " ".join(re.sub(r"(?<!\d)(19|20)\d{2}(?!\d)", " ", name).replace("-", " ").split()).lower()
    if (len(re.sub(r"[^a-z]", "", core)) < 3 or core in _GENERIC_WORDS
            or any(p.fullmatch(core) for p in _GENERIC_PATTERNS)):
        return None
    return name


def name_key(name: str) -> str:
    """'Air Show', 'airshow', 'Air_Show' -> 'airshow' (for merging suggestions)."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def parse_folder_name(folder: str) -> tuple[date | None, str | None]:
    """'6-19-2026 Air Show' -> (2026-06-19, 'Air Show'); '9-6-2021_Air_Show' ->
    (2021-09-06, 'Air Show'); 'Epcot 2017' -> (None, 'Epcot 2017');
    '3-14-2026' -> (2026-03-14, None); '100MSDCF' -> (None, None)."""
    name = folder.strip()
    for pattern, order in ((_MDY, "mdy"), (_YMD, "ymd"), (_COMPACT, "ymd")):
        m = pattern.match(name)
        if not m:
            continue
        a, b, c, rest = m.groups()
        y, mo, d = (int(c), int(a), int(b)) if order == "mdy" else (int(a), int(b), int(c))
        try:
            when = date(y, mo, d)
        except ValueError:
            continue
        return when, _clean_name(re.sub(r"^[\s\-_]*", "", rest or ""))
    return None, _clean_name(name)


@dataclass
class Suggestion:
    key: str                          # what it is - survives recomputing; dismissals use it
    kind: str                         # 'folder' | 'gap'
    name: str                         # proposed name (the user can edit it)
    start_at: str | None
    end_at: str | None
    file_ids: list[int] = field(default_factory=list)
    folders: list[str] = field(default_factory=list)   # where it was found (folder kind)

    def dates(self) -> str:
        return date_range_text(self.start_at, self.end_at)

    def why(self) -> str:
        if self.kind == "folder":
            more = f" (+{len(self.folders) - 1} more)" if len(self.folders) > 1 else ""
            return f"{self.folders[0]}{more}"
        return "Taken close together, apart from other photos"


@dataclass
class SuggestResult:
    suggestions: list[Suggestion]
    seconds: float = 0.0


def _t(iso: str) -> datetime:
    return datetime.fromisoformat(iso[:19])


def _runs(items: list[tuple[int, str]], gap: timedelta) -> list[list[tuple[int, str]]]:
    """Sorted (id, time) items split wherever consecutive times are more than `gap` apart."""
    runs: list[list[tuple[int, str]]] = []
    prev = None
    for fid, taken in items:
        try:
            t = _t(taken)
        except ValueError:
            continue
        if prev is None or t - prev > gap:
            runs.append([])
        runs[-1].append((fid, taken))
        prev = t
    return runs


def root_path_join(root: str, rel: str) -> str:
    return root.rstrip("\\/") + "\\" + rel.replace("/", "\\") if rel else root


def suggest(conn: sqlite3.Connection, *, gap_hours: float = 18, min_photos: int = 30) -> SuggestResult:
    started = time.perf_counter()
    dismissed = {r[0] for r in conn.execute("SELECT key FROM event_dismissed")}
    roots = dict(conn.execute("SELECT id, path FROM roots"))
    rows = conn.execute(
        "SELECT f.id, f.root_id, f.rel_path, e.captured_at"
        " FROM files f JOIN roots r ON r.id = f.root_id LEFT JOIN exif e ON e.file_id = f.id"
        " WHERE f.missing_since IS NULL AND f.excluded = 0 AND f.quarantined_at IS NULL AND r.enabled = 1"
        " AND NOT EXISTS (SELECT 1 FROM event_files ef WHERE ef.file_id = f.id)").fetchall()

    # --- 1. named folders: the outermost named folder under the source ---------------------
    cache: dict[tuple[int, str], tuple[str, date | None, str] | None] = {}

    def named_folder(root_id: int, d: str):
        key = (root_id, d)
        if key not in cache:
            hit = None
            parts = d.split("/") if d else []
            for i in range(1, len(parts) + 1):
                when, name = parse_folder_name(parts[i - 1])
                if name:
                    hit = ("/".join(parts[:i]), when, name)
                    break
            cache[key] = hit
        return cache[key]

    groups: dict[tuple[int, str], dict] = {}
    loose: list[tuple[int, str]] = []            # dated photos left for gap suggestions
    for fid, root_id, rel, taken in rows:
        hit = named_folder(root_id, rel.rsplit("/", 1)[0] if "/" in rel else "")
        if hit is None:
            if taken:
                loose.append((fid, taken))
            continue
        folder, when, name = hit
        g = groups.setdefault((root_id, folder), {"when": when, "name": name, "dated": [], "undated": []})
        (g["dated"] if taken else g["undated"]).append((fid, taken) if taken else fid)

    found: list[Suggestion] = []
    for (root_id, folder), g in groups.items():
        dated = sorted(g["dated"], key=lambda r: r[1])
        undated = g["undated"]
        if g["when"] is None:
            # Words only: it's an event if its photos look like one outing.
            if len(dated) + len(undated) < MIN_NAMED_PHOTOS or not dated \
                    or _t(dated[-1][1]) - _t(dated[0][1]) > MAX_NAMED_SPAN:
                loose.extend(dated)              # an album: its photos can still form gap runs
                continue
            keep = dated
        else:
            # Dated: keep the run of photos around the folder's date; strays
            # (a subfolder from another trip) are left for other suggestions.
            keep = []
            if dated:
                runs = _runs(dated, DATED_RUN_GAP)
                target = datetime.combine(g["when"], datetime.min.time())

                def distance(run):
                    a, b = _t(run[0][1]), _t(run[-1][1])
                    return timedelta(0) if a - timedelta(days=1) <= target <= b else min(abs(a - target), abs(b - target))
                keep = min(runs, key=distance)
                if distance(keep) > timedelta(days=7):
                    keep = []
                for run in runs:
                    if run is not keep:
                        loose.extend(run)
            if not keep and not undated:
                continue
        ids = [f for f, _ in keep] + undated
        start = keep[0][1] if keep else g["when"].isoformat() + "T00:00:00"
        end = keep[-1][1] if keep else start
        found.append(Suggestion("", "folder", g["name"], start, end, ids,
                                [root_path_join(roots.get(root_id, "?"), folder)]))

    # Same name on consecutive days, or the same folder in two places: one event.
    found.sort(key=lambda s: (name_key(s.name), s.start_at))
    merged: list[Suggestion] = []
    for s in found:
        last = merged[-1] if merged else None
        if last and name_key(last.name) == name_key(s.name) and _t(s.start_at) <= _t(last.end_at) + MERGE_DAYS:
            last.file_ids.extend(s.file_ids)
            last.folders.extend(f for f in s.folders if f not in last.folders)
            last.end_at = max(last.end_at, s.end_at)
        else:
            merged.append(s)
    # A folder whose event already exists: what's left are strays the event
    # deliberately left out (another trip's photos in a subfolder) - not a new event.
    existing = [(name_key(n), _t(a)) for n, a in conn.execute(
        "SELECT name, start_at FROM events WHERE start_at IS NOT NULL")]
    out = []
    for s in merged:
        s.key = f"folder:{name_key(s.name)}:{s.start_at[:10]}"
        s.file_ids = list(dict.fromkeys(s.file_ids))
        if s.key in dismissed or any(k == name_key(s.name) and abs(a - _t(s.start_at)) <= timedelta(days=14)
                                     for k, a in existing):
            continue
        out.append(s)
    out.sort(key=lambda s: s.start_at or "", reverse=True)

    # --- 2. gaps in capture time -----------------------------------------------------------
    loose.sort(key=lambda r: r[1])
    gaps = []
    for run in _runs(loose, timedelta(hours=gap_hours)):
        if len(run) < min_photos or _t(run[-1][1]) - _t(run[0][1]) > MAX_GAP_SPAN:
            continue
        start, end = run[0][1], run[-1][1]
        key = f"gap:{start[:16]}"
        if key not in dismissed:
            gaps.append(Suggestion(key, "gap", date_range_text(start, end), start, end, [f for f, _ in run]))
    gaps.sort(key=lambda s: s.start_at or "", reverse=True)
    return SuggestResult(out + gaps, time.perf_counter() - started)


def dismiss(conn: sqlite3.Connection, keys) -> None:
    conn.executemany("INSERT OR IGNORE INTO event_dismissed (key) VALUES (?)", [(k,) for k in keys])
    conn.commit()


def accept(conn: sqlite3.Connection, s: Suggestion, name: str | None = None) -> int:
    from lunelis.events import model
    return model.create(conn, name or s.name, s.file_ids, source="folder" if s.kind == "folder" else "suggested")
