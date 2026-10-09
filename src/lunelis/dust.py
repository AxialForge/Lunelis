"""
Sensor dust map: the same soft dark spot in many photos from one camera.

Dust on a sensor shows as a small, soft, dark blot in the same place on
every frame - clearest at f/8 and narrower, on plain bright areas (sky,
walls, snow). Per camera:

1. **Look:** every photo at f/8 or narrower is read from its thumbnail, turned
   back to the sensor's own orientation, and checked for small dark blots in
   smooth, bright areas. A grid over the sensor counts, per cell, how often
   it was *checkable* (smooth and bright there) and how often a blot was there.
2. **Map:** cells with a blot in at least half of the frames that could show
   one (and at least MIN_SEEN of them) are dust; neighbouring cells join into
   one spot, with a confidence (that share) and the first and last date it
   was seen.
3. **Cleanings:** a spot that's seen up to a date and never after, while
   later frames could have shown it, reads as "cleaned"; one first seen after
   others were already there is "new dust".
4. **Heal:** after you've looked at the map, each spot becomes a Heal spot
   (edit/retouch.py) in the photos taken while it was there - edits, so the
   originals never change, and Undo takes them all off again.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np

GRID = (48, 32)                  # cells across the sensor (long side, short side)
MIN_APERTURE = 8.0
MIN_SEEN = 5                     # frames that could show a spot there
MIN_SHARE = 0.5                  # of those, at least this share show it
BLOT_SIGMA = 4.0                 # px at thumbnail size: the blur a blot is compared against


@dataclass
class Spot:
    x: float                     # sensor fractions (landscape, as recorded)
    y: float
    r: float                     # radius, fraction of the long side
    confidence: float
    seen: int
    checkable: int
    first: str | None = None
    last: str | None = None
    state: str = "present"       # present | cleaned | new


@dataclass
class DustMap:
    camera: str
    frames: int
    spots: list[Spot] = field(default_factory=list)
    cleanings: list[str] = field(default_factory=list)      # dates after which spots vanished

    def to_json(self) -> str:
        return json.dumps({"camera": self.camera, "frames": self.frames, "cleanings": self.cleanings,
                           "spots": [s.__dict__ for s in self.spots]})

    @classmethod
    def from_json(cls, text: str) -> "DustMap":
        d = json.loads(text)
        return cls(d["camera"], d["frames"], [Spot(**s) for s in d["spots"]], d.get("cleanings", []))


# --- one frame ---------------------------------------------------------------------------------

def to_sensor(a: np.ndarray, orientation: int | None) -> np.ndarray:
    """An upright image back to how the sensor recorded it (EXIF 6 / 8 / 3)."""
    if orientation == 6:
        return np.rot90(a, 1)
    if orientation == 8:
        return np.rot90(a, -1)
    if orientation == 3:
        return np.rot90(a, 2)
    return a


def blots(gray: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(checkable, blot) boolean maps for one frame (0-1 grey, sensor orientation)."""
    import cv2
    g = gray.astype(np.float32)
    soft = cv2.GaussianBlur(g, (0, 0), BLOT_SIGMA)
    wide = cv2.GaussianBlur(g, (0, 0), BLOT_SIGMA * 4)
    local_sd = np.sqrt(np.maximum(cv2.GaussianBlur(g * g, (0, 0), BLOT_SIGMA * 4) - wide * wide, 0))
    checkable = (wide > 0.35) & (local_sd < 0.06)
    dip = wide - soft                                      # a soft dark blot sits below its surroundings
    blot = checkable & (dip > 0.015) & (dip > 3 * np.median(np.abs(soft - wide)[checkable]) if checkable.any() else False)
    return checkable, blot


def _cells(m: np.ndarray) -> np.ndarray:
    """A boolean map pooled onto the GRID (any pixel in a cell)."""
    h, w = m.shape
    gw, gh = GRID if w >= h else GRID[::-1]
    ys = (np.arange(h) * gh // h)
    xs = (np.arange(w) * gw // w)
    out = np.zeros((gh, gw), bool)
    yy, xx = np.nonzero(m)
    out[ys[yy], xs[xx]] = True
    return out if w >= h else out.T


# --- the map -----------------------------------------------------------------------------------

def cameras(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    """Cameras with frames at f/8 or narrower, and how many."""
    return [tuple(r) for r in conn.execute(
        "SELECT e.camera_model, COUNT(*) FROM files f JOIN exif e ON e.file_id = f.id"
        " WHERE e.camera_model IS NOT NULL AND e.aperture >= ? AND f.thumbnail_path IS NOT NULL"
        " AND f.missing_since IS NULL AND COALESCE(f.format, '') NOT IN ('mp4', 'mov', 'mpeg-ts')"
        " GROUP BY e.camera_model ORDER BY COUNT(*) DESC", (MIN_APERTURE,))]


def _frames(conn, camera: str):
    return conn.execute(
        "SELECT f.id, f.thumbnail_path, e.orientation, e.captured_at FROM files f JOIN exif e ON e.file_id = f.id"
        " WHERE e.camera_model = ? AND e.aperture >= ? AND f.thumbnail_path IS NOT NULL AND f.missing_since IS NULL"
        " AND COALESCE(f.format, '') NOT IN ('mp4', 'mov', 'mpeg-ts') AND NOT (f.pair_of IS NOT NULL AND f.is_raw = 0)"
        " ORDER BY e.captured_at", (camera, MIN_APERTURE)).fetchall()


def build(conn: sqlite3.Connection, camera: str, thumbs: Path,
          progress: Callable[[int, int], None] | None = None, stop: Callable[[], bool] | None = None) -> DustMap:
    from PIL import Image
    rows = _frames(conn, camera)
    checkable = np.zeros(GRID[::-1], np.int32)
    hits = np.zeros(GRID[::-1], np.int32)
    per_frame: list[tuple[str | None, np.ndarray, np.ndarray]] = []
    for i, (fid, thumb, orient, when) in enumerate(rows):
        if stop and stop():
            break
        if progress and i % 20 == 0:
            progress(i, len(rows))
        try:
            g = np.asarray(Image.open(thumbs / thumb).convert("L"), np.float32) / 255.0
        except OSError:
            continue
        c, b = blots(to_sensor(g, orient))
        cc, bc = _cells(c), _cells(b)
        if cc.shape != checkable.shape:
            continue
        checkable += cc
        hits += bc
        per_frame.append((when, cc, bc))
    m = DustMap(camera, len(per_frame))
    share = np.where(checkable > 0, hits / np.maximum(checkable, 1), 0)
    dust = (checkable >= MIN_SEEN) & (share >= MIN_SHARE)
    m.spots = _join(dust, share, checkable, hits, per_frame)
    m.cleanings = sorted({s.last[:10] for s in m.spots if s.state == "cleaned" and s.last})
    return m


def _join(dust, share, checkable, hits, per_frame) -> list[Spot]:
    import cv2
    n, labels = cv2.connectedComponents(dust.astype(np.uint8), connectivity=8)
    gh, gw = dust.shape
    out = []
    for k in range(1, n):
        ys, xs = np.nonzero(labels == k)
        cx, cy = (xs.mean() + 0.5) / gw, (ys.mean() + 0.5) / gh
        r = max(len(set(xs)), len(set(ys))) / max(gw, gh) / 2 + 0.5 / max(gw, gh)
        cells = labels == k
        dates_seen = [w for w, cc, bc in per_frame if (bc & cells).any()]
        dates_could = [w for w, cc, bc in per_frame if (cc & cells).any()]
        spot = Spot(round(float(cx), 4), round(float(cy), 4), round(float(r), 4),
                    round(float(share[cells].mean()), 2), int(hits[cells].max()), int(checkable[cells].max()),
                    min(dates_seen) if dates_seen else None, max(dates_seen) if dates_seen else None)
        later = [w for w in dates_could if w and spot.last and w > spot.last]
        earlier = [w for w in dates_could if w and spot.first and w < spot.first]
        if len(later) >= MIN_SEEN:
            spot.state = "cleaned"                   # could have shown it many times since, never did
        elif len(earlier) >= MIN_SEEN:
            spot.state = "new"
        out.append(spot)
    return sorted(out, key=lambda s: -s.confidence)


def save(conn: sqlite3.Connection, m: DustMap) -> None:
    conn.execute("INSERT INTO dust_maps (camera, map, made_at) VALUES (?, ?, datetime('now'))"
                 " ON CONFLICT(camera) DO UPDATE SET map = excluded.map, made_at = excluded.made_at",
                 (m.camera, m.to_json()))
    conn.commit()


def load(conn: sqlite3.Connection, camera: str) -> DustMap | None:
    row = conn.execute("SELECT map FROM dust_maps WHERE camera = ?", (camera,)).fetchone()
    return DustMap.from_json(row[0]) if row else None


# --- healing -----------------------------------------------------------------------------------

def _to_frame(x: float, y: float, orientation: int | None) -> tuple[float, float]:
    """Sensor fractions -> the upright photo's fractions."""
    if orientation == 6:
        return 1 - y, x
    if orientation == 8:
        return y, 1 - x
    if orientation == 3:
        return 1 - x, 1 - y
    return x, y


def affected(conn: sqlite3.Connection, m: DustMap, spots: list[Spot] | None = None) -> dict[int, list]:
    """file id -> the heal spots it would get: each spot on the photos from
    its camera taken between its first and last sighting (any aperture -
    the dust is there even where it's hard to see)."""
    from lunelis.edit.retouch import Spot as Heal
    spots = m.spots if spots is None else spots
    out: dict[int, list] = {}
    for fid, orient, when in conn.execute(
            "SELECT f.id, e.orientation, e.captured_at FROM files f JOIN exif e ON e.file_id = f.id"
            " WHERE e.camera_model = ? AND f.missing_since IS NULL"
            " AND COALESCE(f.format, '') NOT IN ('mp4', 'mov', 'mpeg-ts')", (m.camera,)):
        for s in spots:
            if s.first and when and not (s.first <= when <= (s.last or when)):
                continue
            fx, fy = _to_frame(s.x, s.y, orient)
            out.setdefault(fid, []).append(Heal("heal", round(fx, 4), round(fy, 4), round(max(s.r, 0.004), 4)))
    return out


def heal(conn: sqlite3.Connection, m: DustMap, spots: list[Spot] | None = None) -> tuple[int, int]:
    """Add the heal spots to each affected photo's edit; (photos healed,
    photos skipped - rotated or flipped in Lunelis, where the sensor's corners
    moved). Recorded in dust_heals so undo() can take them off again."""
    from dataclasses import replace
    from lunelis.edit import store
    done, skipped, record = 0, 0, {}
    for fid, new in affected(conn, m, spots).items():
        st = store.get(conn, fid)
        g = st.geometry
        if g.rotate or g.flip_h or g.flip_v:
            skipped += 1
            continue
        add = tuple(s for s in new if s not in st.retouch)
        if not add:
            continue
        store.save(conn, fid, replace(st, retouch=st.retouch + add), commit=False)
        conn.execute("UPDATE files SET thumbnail_path = NULL WHERE id = ?", (fid,))
        record[str(fid)] = [list((s.x, s.y, s.r)) for s in add]
        done += 1
    if record:
        # Only a heal that added spots is recorded: a second press added nothing,
        # and Undo then took off that empty one, so it seemed to do nothing (0.49).
        conn.execute("INSERT INTO dust_heals (camera, spots, made_at) VALUES (?, ?, datetime('now'))",
                     (m.camera, json.dumps(record)))
    conn.commit()
    return done, skipped


def undo(conn: sqlite3.Connection, camera: str) -> int:
    """Take off the spots the last heal for this camera added."""
    from dataclasses import replace
    from lunelis.edit import store
    row = conn.execute("SELECT id, spots FROM dust_heals WHERE camera = ? ORDER BY id DESC LIMIT 1",
                       (camera,)).fetchone()
    if row is None:
        return 0
    n = 0
    for fid, added in json.loads(row[1]).items():
        st = store.get(conn, int(fid))
        keep = tuple(s for s in st.retouch if [s.x, s.y, s.r] not in added or s.kind != "heal")
        if keep != st.retouch:
            store.save(conn, int(fid), replace(st, retouch=keep), commit=False)
            conn.execute("UPDATE files SET thumbnail_path = NULL WHERE id = ?", (int(fid),))
            n += 1
    conn.execute("DELETE FROM dust_heals WHERE id = ?", (row[0],))
    conn.commit()
    return n


def describe(m: DustMap) -> str:
    if not m.spots:
        return f"No dust found on the {m.camera} in {m.frames:,} frames at f/8 or narrower."
    now = [s for s in m.spots if s.state != "cleaned"]
    text = f"{len(m.spots)} dust spot{'s' if len(m.spots) != 1 else ''} on the {m.camera} ({m.frames:,} frames checked)"
    if m.cleanings:
        text += f"; cleaned around {', '.join(m.cleanings)}"
    if any(s.state == "new" for s in m.spots):
        text += "; new dust since then"
    return text + ("." if now else " - none left now.")
