"""
View tools for the photo view (0.52): drawn over the picture on screen,
never written to the photo.

- focus peaking: the sharpest edges glow, so the plane of focus shows; and
  the camera's AF point when the file records one (Sony's maker note, or the
  standard EXIF SubjectArea / SubjectLocation)
- exposure warnings: blown highlights (any channel at the top) in red, crushed
  shadows (every channel at the bottom) in blue - Lightroom's J key
- false colour: the picture painted by brightness, as a video monitor does
  (IRE bands), to read exposure at a glance
- zones: Ansel Adams' zones 0-X, eleven greys from black to white
- histogram: RGB and luminance counts, 256 bins each

Everything works on the picture as it's shown (the preview or the edit's
render), shrunk to at most WORK_EDGE px - fast enough to follow a slider.
"""
from __future__ import annotations

import numpy as np

WORK_EDGE = 1600
PEAK_COLOUR = (40, 255, 120)          # a bright green, like most cameras
PEAK_SHARE = 0.06                     # the sharpest 6 % of edges glow
HIGH, LOW = 250, 5                    # 8-bit clipping limits
HIGH_COLOUR, LOW_COLOUR = (255, 40, 40), (40, 110, 255)

# False colour, by luminance in % (IRE): (upper limit, colour).
FALSE_COLOUR = (
    (2, (90, 0, 120)),        # crushed
    (10, (20, 40, 160)),      # deep shadow
    (20, (40, 110, 220)),
    (38, (90, 90, 90)),       # shadow grey
    (42, (40, 170, 70)),      # middle grey (18 %)
    (52, (130, 130, 130)),
    (56, (240, 130, 180)),    # skin (one stop over middle grey)
    (70, (170, 170, 170)),
    (80, (210, 210, 210)),
    (94, (240, 220, 40)),     # bright
    (98, (255, 140, 0)),
    (101, (230, 30, 30)),     # clipped
)
LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)


def shrink(rgb: np.ndarray, edge: int = WORK_EDGE) -> np.ndarray:
    h, w = rgb.shape[:2]
    k = max(1, int(np.ceil(max(h, w) / edge)))
    return rgb[::k, ::k] if k > 1 else rgb


def _luma(rgb: np.ndarray) -> np.ndarray:
    return rgb[..., :3].astype(np.float32) @ LUMA


def _rgba(mask: np.ndarray, colour, alpha: int = 255) -> np.ndarray:
    out = np.zeros(mask.shape + (4,), np.uint8)
    out[mask] = (*colour, alpha)
    return out


def focus_peaking(rgb: np.ndarray, share: float = PEAK_SHARE) -> np.ndarray:
    """RGBA overlay: the strongest local contrast (a Laplacian of the
    luminance), the top `share` of edges, thickened by a pixel."""
    y = _luma(rgb)
    lap = np.abs(4 * y[1:-1, 1:-1] - y[:-2, 1:-1] - y[2:, 1:-1] - y[1:-1, :-2] - y[1:-1, 2:])
    full = np.zeros_like(y)
    full[1:-1, 1:-1] = lap
    nonflat = full[full > 2]
    if nonflat.size == 0:
        return np.zeros(y.shape + (4,), np.uint8)
    limit = max(12.0, float(np.quantile(nonflat, 1 - share)))
    m = full >= limit
    m[1:, :] |= m[:-1, :]                    # a pixel thicker, so it reads on a big screen
    m[:, 1:] |= m[:, :-1]
    return _rgba(m, PEAK_COLOUR)


def clipping(rgb: np.ndarray) -> np.ndarray:
    """RGBA overlay: red where any channel is blown, blue where all are crushed."""
    c = rgb[..., :3]
    high = (c >= HIGH).any(axis=-1)
    low = (c <= LOW).all(axis=-1)
    out = _rgba(high, HIGH_COLOUR)
    out[low] = (*LOW_COLOUR, 255)
    return out


def false_colour(rgb: np.ndarray) -> np.ndarray:
    ire = _luma(rgb) / 255.0 * 100
    out = np.zeros(ire.shape + (4,), np.uint8)
    lo = -1.0
    for hi, colour in FALSE_COLOUR:
        out[(ire > lo) & (ire <= hi)] = (*colour, 255)
        lo = hi
    return out


def zones(rgb: np.ndarray) -> np.ndarray:
    """Eleven zones, 0 (black) to X (white), as flat greys."""
    z = np.clip(np.round(_luma(rgb) / 255.0 * 10), 0, 10)
    g = (z * 25.5).astype(np.uint8)
    out = np.empty(g.shape + (4,), np.uint8)
    out[..., 0] = out[..., 1] = out[..., 2] = g
    out[..., 3] = 255
    return out


def histogram(rgb: np.ndarray) -> np.ndarray:
    """4 x 256 counts: red, green, blue, luminance."""
    c = rgb[..., :3].reshape(-1, 3)
    h = [np.bincount(c[:, i], minlength=256)[:256] for i in range(3)]
    h.append(np.bincount(np.clip(c.astype(np.float32) @ LUMA, 0, 255).astype(np.int64), minlength=256)[:256])
    return np.stack(h).astype(np.float32)


OVERLAYS = {"peaking": focus_peaking, "clipping": clipping, "false_colour": false_colour, "zones": zones}


# --- the camera's AF point -----------------------------------------------------------------------

def _numbers(v) -> list[float]:
    vals = getattr(v, "values", v)
    if not isinstance(vals, (list, tuple)):
        vals = [vals]
    out = []
    for x in vals:
        try:
            out.append(float(x.num) / float(x.den) if hasattr(x, "den") else float(x))
        except (TypeError, ValueError, ZeroDivisionError):
            return []
    return out


def af_point(path: str, orientation: int | None = None) -> tuple[float, float] | None:
    """Where the camera focused, as fractions of the upright picture - or None.
    Sony writes [width, height, x, y] in its maker note (0x204A, else 0x2027);
    other cameras may fill the standard SubjectArea / SubjectLocation."""
    import exifread
    try:
        with open(path, "rb") as fh:
            tags = exifread.process_file(fh, details=True, extract_thumbnail=False)
    except Exception:                             # noqa: BLE001 - no point to show, nothing else
        return None
    x = y = None
    for key in ("MakerNote Tag 0x204A", "MakerNote Tag 0x2027"):
        n = _numbers(tags.get(key)) if key in tags else []
        if len(n) == 4 and n[0] > 0 and n[1] > 0 and (n[2] or n[3]):
            x, y = n[2] / n[0], n[3] / n[1]
            break
    if x is None:
        w = _numbers(tags.get("EXIF ExifImageWidth")) or _numbers(tags.get("Image ImageWidth"))
        h = _numbers(tags.get("EXIF ExifImageLength")) or _numbers(tags.get("Image ImageLength"))
        s = _numbers(tags.get("EXIF SubjectArea")) or _numbers(tags.get("EXIF SubjectLocation"))
        if w and h and len(s) >= 2 and w[0] and h[0]:
            x, y = s[0] / w[0], s[1] / h[0]
    if x is None or not (0 <= x <= 1 and 0 <= y <= 1):
        return None
    # The point is in the sensor's own frame: turn it with the picture.
    return {6: (1 - y, x), 8: (y, 1 - x), 3: (1 - x, 1 - y)}.get(orientation or 1, (x, y))
