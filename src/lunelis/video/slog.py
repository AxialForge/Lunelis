"""
S-Log3 footage: finding it, and the preview look that makes it watchable.

Sony cameras record the picture profile in the clip's XML sidecar
(C0001M01.XML next to C0001.MP4), e.g.

    <Item name="CaptureGammaEquation" value="s-log3-cine"/>
    <Item name="CaptureColorPrimaries" value="S-Gamut3Cine"/>

`detect(clip)` reads that. The built-in preview turns S-Log3 into normal
Rec.709 video: S-Log3 decoded to linear light (Sony's published curve),
S-Gamut3 / S-Gamut3.Cine converted to Rec.709 primaries, a gentle shoulder
so bright skies roll off instead of clipping, then the Rec.709 curve. 18 %
grey lands where it would on a normal Rec.709 clip (about 41 %).

The preview setting (`log_preview`): "builtin", "off", or "cube:<path>"
for your own .cube LUT (expected to take the camera's log as input).
Only previews and thumbnails change - never the file.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import numpy as np

from lunelis.video.lut import Lut3D, LutError, from_function, load_cube

# Sony, "Technical Summary for S-Gamut3.Cine/S-Log3 and S-Gamut3/S-Log3".
SGAMUT3_CINE_TO_709 = np.array([[1.6269474, -0.5401385, -0.0868089],
                                [-0.1785155, 1.4179409, -0.2394254],
                                [-0.0444361, -0.1959199, 1.2403560]], np.float32)
SGAMUT3_TO_709 = np.array([[1.8467789, -0.5259861, -0.3207928],
                           [-0.4441532, 1.2594429, 0.1847103],
                           [0.0408554, -0.0156426, 0.9747872]], np.float32)

SHOULDER = 0.5            # linear light above this rolls off towards 1.0


def slog3_to_linear(v: np.ndarray) -> np.ndarray:
    """S-Log3 code values (0-1) -> scene linear (0.18 = mid grey)."""
    v = np.asarray(v, np.float32)
    code = v * 1023.0
    hi = (10.0 ** ((code - 420.0) / 261.5)) * (0.18 + 0.01) - 0.01
    lo = (code - 95.0) * 0.01125 / (171.2102946929 - 95.0)
    return np.where(code >= 171.2102946929, hi, lo)


def linear_to_slog3(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, np.float32)
    hi = (420.0 + np.log10(np.maximum(x + 0.01, 1e-6) / (0.18 + 0.01)) * 261.5) / 1023.0
    lo = (x * (171.2102946929 - 95.0) / 0.01125 + 95.0) / 1023.0
    return np.where(x >= 0.01125000, hi, lo)


def rec709_oetf(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0.0, 1.0)
    return np.where(x < 0.018, 4.5 * x, 1.099 * np.power(x, 0.45) - 0.099)


def _shoulder(x: np.ndarray) -> np.ndarray:
    x = np.maximum(x, 0.0)
    over = x - SHOULDER
    return np.where(over <= 0, x, SHOULDER + (1.0 - SHOULDER) * (1.0 - np.exp(-over / (1.0 - SHOULDER))))


def to_rec709(rgb: np.ndarray, cine: bool = True) -> np.ndarray:
    """S-Log3 RGB (..., 3, 0-1) -> Rec.709 display RGB (0-1)."""
    lin = slog3_to_linear(rgb)
    m = SGAMUT3_CINE_TO_709 if cine else SGAMUT3_TO_709
    lin = lin @ m.T
    return rec709_oetf(_shoulder(lin))


@lru_cache(maxsize=2)
def builtin_lut(cine: bool = True) -> Lut3D:
    title = "S-Log3 / S-Gamut3.Cine to Rec.709" if cine else "S-Log3 / S-Gamut3 to Rec.709"
    return from_function(lambda rgb: to_rec709(rgb, cine), 33, title)


# --- finding log clips -------------------------------------------------------------------

_ITEM = re.compile(r'<Item\s+name="(CaptureGammaEquation|CaptureColorPrimaries)"\s+value="([^"]*)"', re.I)


def sidecar_of(clip: str | Path) -> Path | None:
    """The clip's Sony XML: C0001M01.XML (cards) or C0001.XML, any case."""
    p = Path(clip)
    try:
        names = {n.lower(): n for n in (x.name for x in p.parent.iterdir())}
    except OSError:
        return None
    for cand in (f"{p.stem}M01.XML", f"{p.stem}.XML"):
        hit = names.get(cand.lower())
        if hit:
            return p.parent / hit
    return None


def detect(clip: str | Path) -> str | None:
    """'s-log3-cine' / 's-log3' for an S-Log3 clip, else None (no sidecar, or another profile)."""
    xml = sidecar_of(clip)
    if xml is None:
        return None
    try:
        with open(xml, "rb") as f:
            head = f.read(200_000).decode("utf-8", "replace")
    except OSError:
        return None
    items = {k.lower(): v.lower() for k, v in _ITEM.findall(head)}
    gamma = items.get("capturegammaequation", "")
    if not gamma.startswith("s-log3"):
        return None
    cine = "cine" in gamma or "cine" in items.get("capturecolorprimaries", "")
    return "s-log3-cine" if cine else "s-log3"


def describe(kind: str | None) -> str:
    return {"s-log3-cine": "S-Log3 / S-Gamut3.Cine", "s-log3": "S-Log3 / S-Gamut3"}.get(kind or "", "")


def preview_lut(clip: str | Path, choice: str) -> Lut3D | None:
    """The LUT to show this clip through, or None (not log, or previews off)."""
    if not choice or choice == "off":
        return None
    kind = detect(clip)
    if kind is None:
        return None
    if choice.startswith("cube:"):
        try:
            return _cube(choice[5:])
        except LutError:
            return builtin_lut(kind == "s-log3-cine")     # a missing / broken LUT falls back
    return builtin_lut(kind == "s-log3-cine")


@lru_cache(maxsize=4)
def _cube(path: str) -> Lut3D:
    return load_cube(path)


def refresh_thumbnails(conn) -> int:
    """Clear the thumbnails of log clips, so the next pass remakes them with the
    current preview choice. Returns how many."""
    from lunelis.raw.thumbnails import VIDEO_FORMATS
    rows = conn.execute(
        "SELECT f.id, r.path, f.rel_path FROM files f JOIN roots r ON r.id = f.root_id"
        f" WHERE f.format IN ({','.join('?' * len(VIDEO_FORMATS))})", tuple(VIDEO_FORMATS)).fetchall()
    ids = [fid for fid, root, rel in rows if detect(Path(root, *rel.split("/")))]
    if ids:
        conn.executemany("UPDATE files SET thumbnail_path = NULL WHERE id = ?", [(i,) for i in ids])
        conn.commit()
    return len(ids)
