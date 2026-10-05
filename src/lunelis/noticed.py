"""
"Lunelis noticed": shot sequences that could become something more.

Finds, from what the camera recorded and then from the pictures themselves:

| kind       | from the EXIF                                              | then checked on the thumbnails |
|------------|------------------------------------------------------------|--------------------------------|
| hdr        | 3-9 frames under 2 s apart, same lens / aperture / focal   | they line up (within 5 %)      |
|            | length, exposures spread >= 1.5 EV                         |                                |
| startrails | 10+ night exposures of 4 s or longer at a steady interval  | they line up                   |
| timelapse  | 20+ frames at a steady interval (1 s or more), same settings | they line up                 |
| focus      | 3+ frames under 3 s apart, same exposure                   | they line up, and the sharpest |
|            |                                                            | part of the frame moves        |
| panorama   | 3+ frames under 10 s apart, same focal length and exposure | each overlaps the next, shifted|
|            |                                                            | 10-85 % of the frame           |

Frames claimed by one kind aren't offered as another. Nothing is ever built
automatically: a suggestion waits on the Library status page ("14 frames look
like a panorama. Build it?"); a dismissed one is remembered by its frames and
never offered again.

`find(conn)` is incremental: only sequences holding a photo added since the
last look are checked (setting `noticed_upto`, the highest file id seen).
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np

KINDS = {
    "hdr": "an HDR bracket",
    "panorama": "a panorama",
    "focus": "a focus stack",
    "timelapse": "a timelapse",
    "startrails": "star trails",
}
RUN_GAP_S = 90                 # a longer pause than this ends a sequence


@dataclass
class Frame:
    file_id: int
    t: float                   # capture time, seconds
    camera: str
    lens: str
    focal: float | None
    aperture: float | None
    shutter: float | None      # seconds
    iso: int | None
    ev: float | None           # log2(shutter * ISO / aperture^2): the exposure the settings give
    thumb: str | None
    path: str


@dataclass
class Suggestion:
    kind: str
    file_ids: list[int]
    detail: dict

    @property
    def key(self) -> str:
        return self.kind + ":" + hashlib.sha1(",".join(map(str, sorted(self.file_ids))).encode()).hexdigest()[:16]

    def text(self) -> str:
        return f"{len(self.file_ids)} frames look like {KINDS[self.kind]}."


# --- reading frames ----------------------------------------------------------------------------

def shutter_seconds(text: str | None) -> float | None:
    if not text:
        return None
    try:
        if "/" in text:
            a, b = text.split("/", 1)
            return float(a) / float(b)
        return float(text)
    except (ValueError, ZeroDivisionError):
        return None


def _ev(shutter: float | None, iso: int | None, aperture: float | None) -> float | None:
    if not shutter or not aperture:
        return None
    return math.log2(shutter * (iso or 100) / 100.0 / (aperture * aperture))


def _time(stamp: str | None) -> float | None:
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).replace(tzinfo=None).timestamp()
    except ValueError:
        return None


FRAMES_SQL = """
    SELECT f.id, e.captured_at, COALESCE(e.camera_make, '') || ' ' || COALESCE(e.camera_model, ''),
           COALESCE(e.lens, ''), e.focal_length_mm, e.aperture, e.shutter_speed, e.iso,
           f.thumbnail_path, r.path, f.rel_path
    FROM files f JOIN exif e ON e.file_id = f.id JOIN roots r ON r.id = f.root_id
    WHERE f.missing_since IS NULL AND f.excluded = 0 AND f.quarantined_at IS NULL
      AND e.captured_at IS NOT NULL
      AND COALESCE(f.format, '') NOT IN ('mp4', 'mov', 'mpeg-ts')
      AND NOT (f.pair_of IS NOT NULL AND f.is_raw = 0)
"""


def frames(conn: sqlite3.Connection) -> list[Frame]:
    out = []
    for fid, when, cam, lens, focal, ap, sh, iso, thumb, root, rel in conn.execute(FRAMES_SQL):
        t = _time(when)
        if t is None:
            continue
        s = shutter_seconds(sh)
        out.append(Frame(fid, t, cam.strip(), lens, focal, ap, s, iso, _ev(s, iso, ap), thumb,
                         str(Path(root, *rel.split("/")))))
    out.sort(key=lambda f: (f.camera, f.t, f.file_id))
    return out


def runs(fs: list[Frame]) -> list[list[Frame]]:
    """Sequences from one camera with no pause longer than RUN_GAP_S."""
    out: list[list[Frame]] = []
    for f in fs:
        if out and out[-1][-1].camera == f.camera and f.t - out[-1][-1].t <= RUN_GAP_S:
            out[-1].append(f)
        else:
            out.append([f])
    return [r for r in out if len(r) >= 3]


# --- EXIF rules ----------------------------------------------------------------------------------

def _same_optics(a: Frame, b: Frame) -> bool:
    return a.lens == b.lens and _close(a.focal, b.focal, 0.5) and _close(a.aperture, b.aperture, 0.05)


def _close(a, b, tol) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= tol


def _same_exposure(a: Frame, b: Frame) -> bool:
    return _same_optics(a, b) and a.ev is not None and b.ev is not None and abs(a.ev - b.ev) < 0.34


def _steady(seq: list[Frame]) -> bool:
    gaps = [b.t - a.t for a, b in zip(seq, seq[1:])]
    med = float(np.median(gaps))
    return med >= 1.0 and all(abs(g - med) <= max(0.25 * med, 0.6) for g in gaps)


def _intervals(run: list[Frame], used: set[int], min_len: int, night: bool) -> list[list[Frame]]:
    """Longest stretches at a steady interval with the same settings."""
    out, i = [], 0
    while i < len(run):
        if run[i].file_id in used or (night and not (run[i].shutter or 0) >= 4):
            i += 1
            continue
        j = i + 1
        while j < len(run) and run[j].file_id not in used and _same_exposure(run[i], run[j]) \
                and (not night or (run[j].shutter or 0) >= 4) and _steady(run[i:j + 1]):
            j += 1
        if j - i >= min_len:
            out.append(run[i:j])
            i = j
        else:
            i += 1
    return out


def _brackets(run: list[Frame], used: set[int]) -> list[list[Frame]]:
    out, i = [], 0
    while i < len(run) - 2:
        best = None
        for n in (9, 7, 5, 3):
            seq = run[i:i + n]
            if len(seq) < n or any(f.file_id in used or f.ev is None for f in seq):
                continue
            if any(b.t - a.t > 2.0 for a, b in zip(seq, seq[1:])):
                continue
            if not all(_same_optics(seq[0], f) for f in seq):
                continue
            evs = sorted(f.ev for f in seq)
            if evs[-1] - evs[0] >= 1.5 and all(b - a >= 0.3 for a, b in zip(evs, evs[1:])):
                best = seq
                break
        if best:
            out.append(best)
            i += len(best)
        else:
            i += 1
    return out


def _close_together(run: list[Frame], used: set[int], max_gap: float) -> list[list[Frame]]:
    """Stretches of 3+ frames with the same exposure, each within max_gap of the last."""
    out, cur = [], []
    for f in run:
        if f.file_id in used:
            if len(cur) >= 3:
                out.append(cur)
            cur = []
            continue
        if cur and (f.t - cur[-1].t > max_gap or not _same_exposure(cur[0], f)):
            if len(cur) >= 3:
                out.append(cur)
            cur = []
        cur.append(f)
    if len(cur) >= 3:
        out.append(cur)
    return out


# --- picture checks ----------------------------------------------------------------------------

def _gray(f: Frame, thumbs: Path | None, edge: int = 512) -> np.ndarray | None:
    from PIL import Image
    try:
        if f.thumb and thumbs is not None and (thumbs / f.thumb).exists():
            img = Image.open(thumbs / f.thumb)
        else:
            from lunelis.raw.thumbnails import render
            img = render(f.path, edge=edge)
        img = img.convert("L")
        img.thumbnail((edge, edge))
        return np.asarray(img)
    except Exception:
        return None


def shift(a: np.ndarray, b: np.ndarray) -> tuple[float, float, int] | None:
    """How far b's content sits from a's, as fractions of a's size, and how many
    features agree (RANSAC inliers). None when they don't match at all."""
    import cv2
    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]))
    a2, b2 = cv2.equalizeHist(a), cv2.equalizeHist(b)        # brackets differ in brightness
    orb = cv2.ORB_create(1500)
    ka, da = orb.detectAndCompute(a2, None)
    kb, db = orb.detectAndCompute(b2, None)
    if da is None or db is None or len(ka) < 10 or len(kb) < 10:
        return None
    matches = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(da, db)
    if len(matches) < 10:
        return None
    pa = np.float32([ka[m.queryIdx].pt for m in matches])
    pb = np.float32([kb[m.trainIdx].pt for m in matches])
    m, inl = cv2.estimateAffinePartial2D(pb, pa, method=cv2.RANSAC, ransacReprojThreshold=4.0)
    if m is None or inl is None:
        return None
    n = int(inl.sum())
    return float(m[0, 2]) / a.shape[1], float(m[1, 2]) / a.shape[0], n


MIN_INLIERS = 12


def aligned(images: list[np.ndarray], tol: float = 0.05) -> bool:
    for a, b in zip(images, images[1:]):
        s = shift(a, b)
        if s is None or s[2] < MIN_INLIERS or abs(s[0]) > tol or abs(s[1]) > tol:
            return False
    return True


def overlapping(images: list[np.ndarray]) -> bool:
    """Each frame overlaps the next with a real move (a panorama's sweep)."""
    for a, b in zip(images, images[1:]):
        s = shift(a, b)
        if s is None or s[2] < MIN_INLIERS:
            return False
        moved = math.hypot(s[0], s[1])
        if not 0.10 <= moved <= 0.85:
            return False
    return True


def sharpest_cell(img: np.ndarray, grid: int = 3) -> int:
    import cv2
    lap = cv2.Laplacian(img.astype(np.float32), cv2.CV_32F)
    h, w = img.shape
    best, cell = -1.0, 0
    for r in range(grid):
        for c in range(grid):
            v = float(lap[r * h // grid:(r + 1) * h // grid, c * w // grid:(c + 1) * w // grid].var())
            if v > best:
                best, cell = v, r * grid + c
    return cell


def focus_moves(images: list[np.ndarray]) -> bool:
    return len({sharpest_cell(i) for i in images}) >= 2


# --- finding -------------------------------------------------------------------------------------

def detect(fs: list[Frame], thumbs: Path | None, stop: Callable[[], bool] | None = None) -> list[Suggestion]:
    out: list[Suggestion] = []
    for run in runs(fs):
        if stop and stop():
            break
        used: set[int] = set()

        def take(kind, seq, check, **detail):
            imgs = [_gray(f, thumbs) for f in seq]
            if any(i is None for i in imgs) or not check(imgs):
                return
            used.update(f.file_id for f in seq)
            out.append(Suggestion(kind, [f.file_id for f in seq], detail))

        for seq in _intervals(run, used, 10, night=True):
            take("startrails", seq, lambda im: aligned(im[:: max(1, len(im) // 6)]),
                 interval=round(seq[1].t - seq[0].t, 1))
        for seq in _intervals(run, used, 20, night=False):
            take("timelapse", seq, lambda im: aligned(im[:: max(1, len(im) // 6)], tol=0.15),
                 interval=round(seq[1].t - seq[0].t, 1))
        for seq in _brackets(run, used):
            take("hdr", seq, aligned, ev_span=round(max(f.ev for f in seq) - min(f.ev for f in seq), 1))
        for seq in _close_together(run, used, 3.0):
            take("focus", seq, lambda im: aligned(im) and focus_moves(im))
        for seq in _close_together(run, used, 10.0):
            take("panorama", seq, overlapping)
    return out


def find(conn: sqlite3.Connection, thumbs: Path | None, stop: Callable[[], bool] | None = None,
         everything: bool = False) -> int:
    """Look for new suggestions; returns how many were added. Sequences whose
    photos were all seen last time are skipped unless `everything`."""
    from lunelis.settings import Settings
    s = Settings(conn)
    upto = 0 if everything else s.get("noticed_upto")
    fs = frames(conn)
    if not fs:
        return 0
    newest = max(f.file_id for f in fs)
    keep = {f.file_id for r in runs(fs) if any(x.file_id > upto for x in r) for f in r}
    found = detect([f for f in fs if f.file_id in keep], thumbs, stop)
    if stop and stop():
        return 0
    added = 0
    for sug in found:
        cur = conn.execute(
            "INSERT OR IGNORE INTO suggestions (key, kind, file_ids, detail) VALUES (?, ?, ?, ?)",
            (sug.key, sug.kind, json.dumps(sug.file_ids), json.dumps(sug.detail)))
        added += cur.rowcount
    conn.commit()
    s.set("noticed_upto", newest)
    return added


# --- the suggestions ---------------------------------------------------------------------------

def open_suggestions(conn: sqlite3.Connection) -> list[tuple[int, Suggestion]]:
    """(id, suggestion) still waiting for an answer, newest shoot first; a
    suggestion whose photos have gone (deleted, quarantined) is left out."""
    live = {r[0] for r in conn.execute(
        "SELECT id FROM files WHERE missing_since IS NULL AND quarantined_at IS NULL")}
    out = []
    for sid, kind, ids, detail in conn.execute(
            "SELECT id, kind, file_ids, detail FROM suggestions WHERE status = 'open' ORDER BY id DESC"):
        fids = json.loads(ids)
        if all(f in live for f in fids):
            out.append((sid, Suggestion(kind, fids, json.loads(detail or "{}"))))
    return out


def dismiss(conn: sqlite3.Connection, sid: int) -> None:
    conn.execute("UPDATE suggestions SET status = 'dismissed', answered_at = datetime('now') WHERE id = ?", (sid,))
    conn.commit()


def mark_built(conn: sqlite3.Connection, sid: int) -> None:
    conn.execute("UPDATE suggestions SET status = 'built', answered_at = datetime('now') WHERE id = ?", (sid,))
    conn.commit()
