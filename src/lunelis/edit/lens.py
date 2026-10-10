"""
Lens corrections: the lens's own profile, and manual sliders.

- Profile (lensfun's free database, via lensfunpy): distortion, colour
  fringing (lateral chromatic aberration) and vignetting, looked up from
  the photo's EXIF - camera, lens, focal length, aperture. Matches 98 % of
  the real library's lens-tagged photos (2026-09-27). Off by default:
  camera JPEGs are usually corrected in the camera already, so this is
  mostly for RAWs.
- Manual: distortion (barrel/pincushion), vignetting, and red/blue
  fringing, for lenses without a profile or to fine-tune.

Profiles you add (Settings > Edit > Lens profiles) are lensfun XML files in
<data folder>/lens profiles, read beside the built-in database. lensfunpy
1.18 reads database format version 1 only - a version 2 file is refused
with a reason, never half-loaded.

Settings are the stack's `lens` dict: profile (bool), distortion,
vignette, ca_red, ca_blue (-100..100). Corrections run first, on the
upright photo, in linear light, and the picture is scaled to fill the
frame so corrected edges never show empty corners.
"""
from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

LENS_KEYS = ("distortion", "vignette", "ca_red", "ca_blue")


@dataclass(frozen=True)
class LensInfo:
    make: str | None
    model: str | None
    lens: str | None
    focal: float | None
    aperture: float | None


def info_for(conn: sqlite3.Connection, file_id: int) -> LensInfo | None:
    row = conn.execute("SELECT camera_make, camera_model, lens, focal_length_mm, aperture FROM exif"
                       " WHERE file_id = ?", (file_id,)).fetchone()
    return LensInfo(*row) if row else None


def catalog_uri(path) -> str:
    """A read-only SQLite address (file: URI) for a catalog file. Not
    Path.as_uri(): for a network path that gives file://server/share/...,
    which SQLite refuses (it allows no server name there)."""
    from urllib.parse import quote
    p = os.path.abspath(str(path)).replace("\\", "/")
    if p.startswith("//"):
        p = "//" + p                               # a network share: an empty server part, then the path
    elif not p.startswith("/"):
        p = "/" + p                                # C:/... -> /C:/...
    return "file:" + quote(p, safe="/:") + "?mode=ro"


def info_for_id(file_id: int) -> LensInfo | None:
    """From the catalog on disk (for workers that have no connection)."""
    from lunelis import paths
    try:
        # The path is escaped: a data folder with # ? or % in its name made a
        # different address, the catalog didn't open and the saved preview
        # lost its lens corrections (0.54).
        conn = sqlite3.connect(catalog_uri(paths.DEFAULT_CATALOG_PATH), uri=True, timeout=5)
    except sqlite3.Error:
        return None
    try:
        return info_for(conn, file_id)
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def active(settings: dict | None) -> bool:
    return bool(settings) and (bool(settings.get("profile")) or any(settings.get(k) for k in LENS_KEYS))


# --- the profile ---------------------------------------------------------------------------

def profile_dir():
    from lunelis import paths
    return paths.DATA_DIR / "lens profiles"


def check_profile(path) -> tuple[list[str], str | None]:
    """(lens names in it, why it can't be used or None) for one XML file."""
    import lensfunpy
    try:
        text = open(path, encoding="utf-8").read()
    except (OSError, UnicodeDecodeError) as e:
        return [], f"Can't read it: {e}"
    m = re.search(r'<lensdatabase[^>]*version\s*=\s*"(\d+)"', text)
    if m and int(m.group(1)) > 1:
        return [], (f"It's a version {m.group(1)} lensfun file; Lunelis reads version 1. Take the file from "
                    "lensfun's version_1 database instead (see Where to find profiles).")
    try:
        db = lensfunpy.Database(xml=text, load_common=False, load_bundled=False)
    except Exception as e:
        return [], f"Not a lensfun profile ({type(e).__name__})"
    names = [f"{x.maker} {x.model}" for x in db.lenses]
    return names, None if names else "No lenses in it"


def user_profiles() -> list:
    d = profile_dir()
    return sorted(d.glob("*.xml"), key=lambda p: p.name.lower()) if d.is_dir() else []


def reload() -> None:
    """After a profile is added or removed: look lenses up again."""
    _db_cached.cache_clear()
    _find.cache_clear()
    unavailable.cache_clear()


@lru_cache(maxsize=1)
def unavailable() -> str | None:
    """Why lens corrections can't work at all (the lensfun library or its lens
    database didn't load), or None. Told apart from "no profile for this lens"."""
    try:
        _db()
        return None
    except Exception as e:
        return f"{type(e).__name__}: {e}"


def _db():
    return _db_cached()


@lru_cache(maxsize=1)
def _db_cached():
    import lensfunpy
    good = [str(p) for p in user_profiles() if check_profile(p)[1] is None]
    return lensfunpy.Database(paths=good) if good else lensfunpy.Database()


def _clean(lens: str) -> list[str]:
    # "Sony FE 24-105mm F4 G OSS (SEL24105G)" -> also try "FE 24-105mm F4 G OSS".
    out = [lens]
    s = re.sub(r"\s*\([^)]*\)", "", lens).strip()
    s = re.sub(r"^(sony|sigma|tamron|samyang|zeiss|canon|nikon|fujifilm)\s+", "", s, flags=re.I)
    if s and s != lens:
        out.append(s)
    return out


@lru_cache(maxsize=256)
def _find(make: str | None, model: str | None, lens: str | None):
    if not make or not model or not lens:
        return None
    try:
        db = _db()
        cams = db.find_cameras(make, model)
        if not cams:
            return None
        for name in _clean(lens):
            found = db.find_lenses(cams[0], None, name)
            if found:
                return cams[0], found[0]
    except Exception:
        return None
    return None


def profile_name(info: LensInfo | None) -> str | None:
    """The matched lens profile's name, or None."""
    hit = _find(info.make, info.model, info.lens) if info else None
    return f"{hit[1].maker} {hit[1].model}" if hit else None


# --- applying --------------------------------------------------------------------------------

def _remap(a: np.ndarray, coords: np.ndarray) -> np.ndarray:
    """Per-channel resampling: coords (h, w, 3, 2) = where each output pixel
    of each channel comes from in `a`."""
    import cv2
    out = np.empty(coords.shape[:2] + (3,), dtype=np.float32)
    for c in range(3):
        mx = np.ascontiguousarray(coords[..., c, 0])
        my = np.ascontiguousarray(coords[..., c, 1])
        out[..., c] = cv2.remap(np.ascontiguousarray(a[..., c]), mx, my, cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_REPLICATE)
    return out


def _manual_coords(settings: dict, h: int, w: int, y0: int = 0, rows: int | None = None) -> np.ndarray | None:
    """Radial distortion k and per-channel scales for red/blue fringing,
    with the scale that keeps the frame filled."""
    k = settings.get("distortion", 0) / 100 * 0.12       # +: corrects barrel (pulls corners in)
    sr = 1 + settings.get("ca_red", 0) / 100 * 0.003
    sb = 1 + settings.get("ca_blue", 0) / 100 * 0.003
    if not k and sr == 1 and sb == 1:
        return None
    rows = h if rows is None else rows
    cx, cy = (w - 1) / 2, (h - 1) / 2
    norm = np.hypot(cx, cy)
    fill = 1 / (1 + max(0.0, k)) if k > 0 else 1.0       # barrel correction: zoom so corners stay filled
    ys, xs = np.mgrid[y0:y0 + rows, 0:w].astype(np.float32)
    dx, dy = (xs - cx) / norm, (ys - cy) / norm
    r2 = dx * dx + dy * dy
    f = (1 + k * r2) * fill
    out = np.empty((rows, w, 3, 2), dtype=np.float32)
    for c, s in enumerate((sr, 1.0, sb)):
        out[..., c, 0] = cx + dx * f * s * norm
        out[..., c, 1] = cy + dy * f * s * norm
    return out


def _vignette_gain(settings: dict, h: int, w: int, y0: int, rows: int) -> np.ndarray | None:
    v = settings.get("vignette", 0) / 100
    if not v:
        return None
    ys, xs = np.mgrid[y0:y0 + rows, 0:w].astype(np.float32)
    r2 = ((xs - (w - 1) / 2) ** 2 + (ys - (h - 1) / 2) ** 2) / (((w - 1) / 2) ** 2 + ((h - 1) / 2) ** 2)
    return (1 + v * 1.5 * r2 * r2 ** 0.5)[..., None]      # + brightens the corners (about 1.3 stops at 100)


def apply(a: np.ndarray, settings: dict | None, info: LensInfo | None, rows: int = 1024) -> np.ndarray:
    """Lens-corrected copy of an upright float32 sRGB photo (same size)."""
    if not active(settings):
        return a
    from lunelis.edit.pipeline import linear_to_srgb, srgb_to_linear
    h, w = a.shape[:2]
    lin = srgb_to_linear(np.clip(a, 0, 1))
    mod = None
    if settings.get("profile"):
        hit = _find(info.make, info.model, info.lens) if info else None
        if hit is not None and info.focal:
            import lensfunpy
            cam, lens = hit
            mod = lensfunpy.Modifier(lens, cam.crop_factor, w, h)
            mod.initialize(float(info.focal), float(info.aperture or 5.6), 1000.0, scale=0.0,
                           pixel_format=np.float32)
            mod.apply_color_modification(lin)              # vignetting, in place (linear light)
    out = np.empty_like(lin)
    for y0 in range(0, h, rows):                       # strips: a 60 MP export's coords would be GBs
        n = min(rows, h - y0)
        manual = _manual_coords(settings, h, w, y0, n)
        coords = mod.apply_subpixel_geometry_distortion(0, y0, w, n) if mod is not None else None
        if manual is not None and coords is not None:
            strip = _remap(*_profile_rows(lin, mod, w, h, manual))   # manual on top of the profile
        elif manual is not None:
            strip = _remap(lin, manual)
        elif coords is not None:
            strip = _remap(lin, coords)
        else:
            strip = lin[y0:y0 + n].copy()
        gain = _vignette_gain(settings, h, w, y0, n)
        if gain is not None:
            strip *= gain
        out[y0:y0 + n] = strip
    return linear_to_srgb(out)


def _profile_rows(lin, mod, w, h, manual):
    """Manual + profile together: the manual coords point into the
    profile-corrected image, so render just the rows of it they reach."""
    ymin = int(max(0, np.floor(manual[..., 1].min()) - 2))
    ymax = int(min(h, np.ceil(manual[..., 1].max()) + 3))
    coords = mod.apply_subpixel_geometry_distortion(0, ymin, w, ymax - ymin)
    block = _remap(lin, coords) if coords is not None else lin[ymin:ymax]
    shifted = manual.copy()
    shifted[..., 1] -= ymin
    return block, shifted
