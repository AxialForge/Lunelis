"""
Retouching: heal, clone and red-eye spots, stored in the edit stack.

A spot is a circle in the uncropped frame - the same coordinates as masks
(fractions of the frame; the radius a fraction of its long side) - so a
later crop or re-crop doesn't move it. Spots are painted after geometry,
before the sliders, in order:

- **heal:** cover the spot with a patch from nearby (`src`, or the best of
  eight neighbouring patches when not given), shifted to the brightness and
  colour around the spot and feathered in - a dust spot or a blemish
  disappears without a visible edge.
- **clone:** copy the patch from `src` as it is (feathered edge).
- **redeye:** inside the circle, red that clearly outweighs green and blue
  is pulled down to their level; the rest of the eye is untouched.

Text form inside the stack: `spot=heal|0.41,0.33,0.012|0.45,0.33`
(kind | x,y,r | source x,y - empty for "choose for me").
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

KINDS = ("heal", "clone", "redeye")


@dataclass(frozen=True)
class Spot:
    kind: str
    x: float
    y: float
    r: float
    sx: float | None = None
    sy: float | None = None


def dumps(s: Spot) -> str:
    src = "" if s.sx is None else f"{s.sx:.5g},{s.sy:.5g}"
    return f"{s.kind}|{s.x:.5g},{s.y:.5g},{s.r:.5g}|{src}"


def loads(text: str) -> Spot | None:
    try:
        kind, pos, src = (text.split("|") + ["", ""])[:3]
        if kind not in KINDS:
            return None
        x, y, r = (float(v) for v in pos.split(","))
        sx = sy = None
        if src:
            sx, sy = (float(v) for v in src.split(","))
        return Spot(kind, max(0.0, min(1.0, x)), max(0.0, min(1.0, y)), max(0.0005, min(0.25, r)), sx, sy)
    except ValueError:
        return None


def _px(s_x, s_y, crop, w, h):
    cx0, cy0, cx1, cy1 = crop
    fw, fh = w / max(1e-6, cx1 - cx0), h / max(1e-6, cy1 - cy0)
    return (s_x - cx0) * fw, (s_y - cy0) * fh, max(fw, fh)


def _disc(r: float) -> np.ndarray:
    """A feathered disc, 1 inside, fading over the outer third."""
    n = int(np.ceil(r)) * 2 + 1
    yy, xx = np.mgrid[:n, :n] - n // 2
    d = np.sqrt(xx * xx + yy * yy) / max(r, 1e-6)
    return np.clip((1.0 - d) / 0.33, 0.0, 1.0).astype(np.float32)


def _box(cx, cy, r, w, h):
    k = int(np.ceil(r))
    return int(round(cx)) - k, int(round(cy)) - k, 2 * k + 1


def _patch(x, x0, y0, n):
    """x[y0:y0+n, x0:x0+n] with edges repeated where it leaves the image."""
    h, w = x.shape[:2]
    ys = np.clip(np.arange(y0, y0 + n), 0, h - 1)
    xs = np.clip(np.arange(x0, x0 + n), 0, w - 1)
    return x[np.ix_(ys, xs)]


def _ring_mean(p: np.ndarray, disc: np.ndarray) -> np.ndarray:
    ring = (disc < 0.05)
    return p[ring].mean(axis=0) if ring.any() else p.reshape(-1, 3).mean(axis=0)


def choose_source(x: np.ndarray, cx: float, cy: float, r: float) -> tuple[float, float]:
    """Of eight patches around the spot (2.2 radii away), the one whose
    surroundings look most like the spot's."""
    h, w = x.shape[:2]
    disc = _disc(r)
    x0, y0, n = _box(cx, cy, r, w, h)
    here = _ring_mean(_patch(x, x0, y0, n), disc)
    best, cost = (cx + 2.2 * r, cy), 1e9
    for k in range(8):
        a = k * np.pi / 4
        sx, sy = cx + 2.2 * r * np.cos(a), cy + 2.2 * r * np.sin(a)
        if not (r <= sx <= w - r and r <= sy <= h - r):
            continue
        bx, by, _ = _box(sx, sy, r, w, h)
        p = _patch(x, bx, by, n)
        c = float(np.abs(_ring_mean(p, disc) - here).sum() + p.std(axis=(0, 1)).sum())
        if c < cost:
            best, cost = (sx, sy), c
    return best


def apply(x: np.ndarray, spots: tuple, crop=(0.0, 0.0, 1.0, 1.0), copy: bool = True) -> np.ndarray:
    """x: the cropped image (h, w, 3) floats; returns it retouched (a copy unless
    copy=False - a full-size export paints into its own array)."""
    if not spots:
        return x
    if copy or x.dtype != np.float32:
        x = np.array(x, dtype=np.float32, copy=True)
    h, w = x.shape[:2]
    for s in spots:
        cx, cy, long_px = _px(s.x, s.y, crop, w, h)
        r = max(1.0, s.r * long_px)
        if not (-r < cx < w + r and -r < cy < h + r):
            continue                                   # cropped away
        disc = _disc(r)
        x0, y0, n = _box(cx, cy, r, w, h)
        ys, xs = slice(max(0, y0), min(h, y0 + n)), slice(max(0, x0), min(w, x0 + n))
        dy, dx = ys.start - y0, xs.start - x0
        alpha = disc[dy:dy + (ys.stop - ys.start), dx:dx + (xs.stop - xs.start)][..., None]
        dest = x[ys, xs]
        if s.kind == "redeye":
            r_, g_, b_ = dest[..., 0], dest[..., 1], dest[..., 2]
            gb = (g_ + b_) / 2
            red = np.clip((r_ - 1.4 * np.maximum(g_, b_)) / 0.15, 0.0, 1.0)[..., None]
            fixed = dest.copy()
            fixed[..., 0] = gb
            x[ys, xs] = dest + (fixed - dest) * red * alpha
            continue
        if s.sx is None:
            sx, sy = choose_source(x, cx, cy, r)
        else:
            sx, sy, _ = _px(s.sx, s.sy, crop, w, h)
        bx, by, _ = _box(sx, sy, r, w, h)
        src = _patch(x, bx, by, n)[dy:dy + (ys.stop - ys.start), dx:dx + (xs.stop - xs.start)]
        if s.kind == "heal":
            whole = _patch(x, x0, y0, n)
            src = src + (_ring_mean(whole, disc) - _ring_mean(_patch(x, bx, by, n), disc))
        x[ys, xs] = dest + (src - dest) * alpha
    return x
