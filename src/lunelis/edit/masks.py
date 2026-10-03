"""
Local adjustments: masks, each with its own adjustments.

A mask says HOW MUCH (0..1, its alpha) of its adjustments apply at each
pixel; the result is blended over the globally edited photo:

    out = x + alpha * (adjusted(x) - x)

Kinds:
- linear  - a gradient: full effect at the start line, none past the end
            line. shape = (x0, y0, x1, y1).
- radial  - an ellipse: full effect inside, fading out over `feather` to
            nothing at its edge. shape = (cx, cy, rx, ry, feather).
- brush   - painted strokes (strokes = ((radius, feather, flow, erase,
            ((x, y), ...)), ...); radius is a fraction of the long side).
- subject / sky - from a local AI model (edit/ai.py), cached per photo.

Coordinates are fractions of the photo AFTER rotation/flip/straighten and
BEFORE the crop, so changing the crop never moves a mask. `invert` swaps
inside and outside.

Text form (inside the stack's `mask=` entries, one per mask):
    kind|shape|flags|key:value,key:value|stroke/stroke
with flags "inv", and a stroke "radius,feather,flow,erase:x y x y ...".
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

KINDS = ("linear", "radial", "brush", "subject", "sky")
# What a mask can adjust (the global-only ones - hue, fade, vignette - aren't here).
LOCAL_KEYS = ("exposure", "contrast", "highlights", "shadows", "whites", "blacks",
              "temp", "tint", "vibrance", "saturation", "sharpen", "denoise")


@dataclass(frozen=True)
class Mask:
    kind: str
    shape: tuple = ()
    adjust: dict = field(default_factory=dict)
    invert: bool = False
    strokes: tuple = ()

    def with_adjust(self, key: str, value: float) -> "Mask":
        from lunelis.edit.stack import clamp
        adj = dict(self.adjust)
        v = clamp(key, value)
        if v:
            adj[key] = v
        else:
            adj.pop(key, None)
        return replace(self, adjust=adj)


def default(kind: str) -> Mask:
    if kind == "linear":
        return Mask("linear", (0.5, 0.15, 0.5, 0.55))          # a sky gradient from the top
    if kind == "radial":
        return Mask("radial", (0.5, 0.5, 0.22, 0.28, 0.5))
    return Mask(kind)


# --- text ---------------------------------------------------------------------------------

def _n(v: float) -> str:
    return f"{round(v, 4):g}"


def dumps(m: Mask) -> str:
    from lunelis.edit.stack import PARAMS
    adjust = ",".join(f"{p.key}:{_n(m.adjust[p.key])}" for p in PARAMS if m.adjust.get(p.key))
    strokes = "/".join(
        f"{_n(r)},{_n(f)},{_n(fl)},{int(e)}:" + " ".join(f"{_n(x)} {_n(y)}" for x, y in pts)
        for r, f, fl, e, pts in m.strokes)
    return "|".join((m.kind, ",".join(_n(v) for v in m.shape), "inv" if m.invert else "", adjust, strokes))


def loads(text: str) -> Mask | None:
    from lunelis.edit.stack import clamp
    parts = text.split("|") + [""] * 5
    kind = parts[0]
    if kind not in KINDS:
        return None
    try:
        shape = tuple(float(v) for v in parts[1].split(",") if v)
        adjust = {}
        for kv in parts[3].split(","):
            if ":" in kv:
                k, v = kv.split(":", 1)
                if k in LOCAL_KEYS and clamp(k, float(v)):
                    adjust[k] = clamp(k, float(v))
        strokes = []
        for s in parts[4].split("/"):
            if ":" not in s:
                continue
            head, pts = s.split(":", 1)
            r, f, fl, e = (float(v) for v in head.split(","))
            nums = [float(v) for v in pts.split()]
            strokes.append((r, f, fl, bool(e), tuple(zip(nums[0::2], nums[1::2]))))
    except ValueError:
        return None
    need = {"linear": 4, "radial": 5}.get(kind, 0)
    if len(shape) < need:
        shape = default(kind).shape
    return Mask(kind, shape[:need] if need else (), adjust, parts[2] == "inv", tuple(strokes))


# --- alpha ------------------------------------------------------------------------------------

def _smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


class Frame:
    """Where the rows being rendered sit: the final (cropped) image is
    full_w x full_h; the rows are y0..y0+h; `crop` places it inside the
    uncropped frame the masks are drawn in."""

    def __init__(self, crop, full_h: int, full_w: int, y0: int = 0, h: int | None = None) -> None:
        self.crop, self.full_h, self.full_w, self.y0 = crop, full_h, full_w, y0
        self.h = full_h if h is None else h
        cx0, cy0, cx1, cy1 = crop
        self.fw = full_w / max(1e-6, cx1 - cx0)          # the uncropped frame, in pixels
        self.fh = full_h / max(1e-6, cy1 - cy0)

    def grid(self) -> tuple[np.ndarray, np.ndarray]:
        """Pixel coordinates (uncropped frame) of this strip's pixels."""
        cx0, cy0, _, _ = self.crop
        xs = (np.arange(self.full_w, dtype=np.float32) + 0.5) + cx0 * self.fw
        ys = (np.arange(self.y0, self.y0 + self.h, dtype=np.float32) + 0.5) + cy0 * self.fh
        return xs[None, :], ys[:, None]


def alpha(m: Mask, fr: Frame, cache: dict | None = None, ai_maps: dict | None = None) -> np.ndarray:
    """0..1 per pixel of the strip, float32 (h x full_w)."""
    if m.kind == "linear":
        a = _linear(m.shape, fr)
    elif m.kind == "radial":
        a = _radial(m.shape, fr)
    elif m.kind == "brush":
        a = _raster(m, fr, cache)
    else:
        a = _ai(m.kind, fr, ai_maps)
    if m.invert:
        a = 1 - a
    return np.ascontiguousarray(a, dtype=np.float32)


def _linear(shape, fr: Frame) -> np.ndarray:
    x0, y0, x1, y1 = shape
    X, Y = fr.grid()
    px0, py0, px1, py1 = x0 * fr.fw, y0 * fr.fh, x1 * fr.fw, y1 * fr.fh
    dx, dy = px1 - px0, py1 - py0
    L2 = max(1e-6, dx * dx + dy * dy)
    t = ((X - px0) * dx + (Y - py0) * dy) / L2
    return 1 - _smoothstep(0.0, 1.0, t)


def _radial(shape, fr: Frame) -> np.ndarray:
    cx, cy, rx, ry, feather = shape
    X, Y = fr.grid()
    # Radii are fractions of the uncropped frame's long side (so a circle stays round).
    long = max(fr.fw, fr.fh)
    d = np.sqrt(((X - cx * fr.fw) / max(1e-6, rx * long)) ** 2 + ((Y - cy * fr.fh) / max(1e-6, ry * long)) ** 2)
    f = max(0.02, min(1.0, feather))
    return 1 - _smoothstep(1 - f, 1.0, d)


RASTER_EDGE = 1024          # brush strokes are drawn at this size, then scaled: they're soft anyway


def raster_strokes(strokes, fw: float, fh: float) -> np.ndarray:
    """The painted mask over the whole uncropped frame, small (RASTER_EDGE)."""
    k = RASTER_EDGE / max(fw, fh)
    w, h = max(1, round(fw * k)), max(1, round(fh * k))
    acc = np.zeros((h, w), dtype=np.float32)
    long = max(w, h)
    for radius, feather, flow, erase, pts in strokes:
        layer = Image.new("L", (w, h), 0)
        d = ImageDraw.Draw(layer)
        r = max(1.0, radius * long)
        core = r * (1 - 0.8 * feather)                # soft brushes paint a smaller core, then blur
        prev = None
        for x, y in pts:
            px, py = x * w, y * h
            if prev is not None:
                d.line([prev, (px, py)], fill=255, width=max(1, int(2 * core)))
            d.ellipse([px - core, py - core, px + core, py + core], fill=255)
            prev = (px, py)
        if feather > 0:
            layer = layer.filter(ImageFilter.GaussianBlur(max(0.5, r * feather * 0.5)))
        a = np.asarray(layer, dtype=np.float32) / 255 * flow
        acc = acc * (1 - a) if erase else np.maximum(acc, a)
    return acc


def _raster(m: Mask, fr: Frame, cache: dict | None) -> np.ndarray:
    key = ("brush", m.strokes, round(fr.fw), round(fr.fh))
    small = cache.get(key) if cache is not None else None
    if small is None:
        small = raster_strokes(m.strokes, fr.fw, fr.fh)
        if cache is not None:
            cache[key] = small
    return _sample(small, fr)


def _sample(small: np.ndarray, fr: Frame) -> np.ndarray:
    """Bilinear-sample a whole-frame map at this strip's pixels."""
    h, w = small.shape
    X, Y = fr.grid()
    sx = np.clip(X / fr.fw * w - 0.5, 0, w - 1)
    sy = np.clip(Y / fr.fh * h - 0.5, 0, h - 1)
    x0, y0 = np.floor(sx).astype(np.int32), np.floor(sy).astype(np.int32)
    x1, y1 = np.minimum(x0 + 1, w - 1), np.minimum(y0 + 1, h - 1)
    tx, ty = sx - x0, sy - y0
    top = small[y0, x0] * (1 - tx) + small[y0, x1] * tx
    bot = small[y1, x0] * (1 - tx) + small[y1, x1] * tx
    return top * (1 - ty) + bot * ty


def _ai(kind: str, fr: Frame, ai_maps: dict | None) -> np.ndarray:
    m = (ai_maps or {}).get(kind)
    if m is None:
        return np.zeros((fr.h, fr.full_w), dtype=np.float32)   # not computed (yet): no effect
    return _sample(m, fr)


def overlay(m: Mask, crop, h: int, w: int, ai_maps: dict | None = None) -> np.ndarray:
    """The mask over a displayed (cropped) image of h x w, for drawing."""
    return alpha(m, Frame(crop, h, w), {}, ai_maps)
