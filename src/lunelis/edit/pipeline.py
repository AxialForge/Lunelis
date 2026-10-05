"""
Applying an edit stack to pixels.

Images are float32 numpy arrays (H x W x 3, sRGB-encoded, 0..1). Order:

1. geometry - quarter turns, flips, straighten (then cropped to the largest
   rectangle of the same shape inside, so no empty corners), crop;
2. in linear light - white balance (temp/tint as channel gains), exposure,
   whites/blacks as white and black points;
3. back in sRGB - highlights/shadows as gains on a blurred luminance mask
   (local, so shadows lift without flattening the whole picture), a soft
   shoulder instead of hard clipping, contrast as an S-curve, fade;
4. colour - vibrance (more for muted colours), saturation, hue rotation;
5. vignette, noise reduction (chroma blur + gentle luma smoothing),
   sharpening (unsharp mask on luminance).

Blur radii scale with the image, so a 1600 px preview and a 9504 px export
of the same stack look the same.
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image

from lunelis.edit.stack import Geometry, Stack, effective

LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


# --- helpers ---------------------------------------------------------------------------

# sRGB <-> linear through lookup tables: exact enough (1/4096 steps) and ~10x
# faster than the power functions on a few million values.
_G = np.linspace(0, 1, 4096, dtype=np.float64)
_TO_LIN = np.where(_G <= 0.04045, _G / 12.92, ((_G + 0.055) / 1.055) ** 2.4).astype(np.float32)
_L = np.arange(8 * 4096, dtype=np.float64) / 4096            # linear 0..8 (exposure can push past 1)
_TO_SRGB = np.where(_L <= 0.0031308, _L * 12.92, 1.055 * np.power(_L, 1 / 2.4) - 0.055).astype(np.float32)


def srgb_to_linear(x: np.ndarray) -> np.ndarray:
    return _TO_LIN[np.clip(x * 4095 + 0.5, 0, 4095).astype(np.uint16)]


def linear_to_srgb(x: np.ndarray) -> np.ndarray:
    return _TO_SRGB[np.clip(x * 4096 + 0.5, 0, 8 * 4096 - 1).astype(np.uint16)]


def _binomial(a: np.ndarray, passes: int) -> np.ndarray:
    # [1 2 1]/4 along both axes, `passes` times (each pass adds variance 0.5).
    for _ in range(passes):
        b = a.copy()
        b[1:-1] = 0.25 * a[:-2] + 0.5 * a[1:-1] + 0.25 * a[2:]
        a = b.copy()
        a[:, 1:-1] = 0.25 * b[:, :-2] + 0.5 * b[:, 1:-1] + 0.25 * b[:, 2:]
    return a


def _resize(a: np.ndarray, w: int, h: int, method) -> np.ndarray:
    return np.asarray(Image.fromarray(a, "F").resize((w, h), method), dtype=np.float32)


def blur(a: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian-like blur of a 2-D float32 array. Wide blurs run on a
    shrunken copy (the result is smooth anyway), narrow ones directly."""
    if sigma < 0.5:
        return a
    a = np.ascontiguousarray(a, dtype=np.float32)
    h, w = a.shape
    k = math.ceil(sigma)
    if sigma > 1.5 and min(h, w) // k >= 4:
        small = _resize(a, max(1, w // k), max(1, h // k), Image.Resampling.BOX)
        small = _binomial(small, max(1, round(2 * (sigma / k) ** 2)))
        return _resize(small, w, h, Image.Resampling.BILINEAR)
    return _binomial(a, max(1, round(2 * sigma * sigma)))


def _smoothstep(e0: float, e1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def to_array(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0


def to_image(a: np.ndarray) -> Image.Image:
    return Image.fromarray((np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8), "RGB")


# --- geometry --------------------------------------------------------------------------

def straighten_scale(w: int, h: int, angle: float) -> float:
    """How much of a w x h image survives a rotation by `angle` degrees when
    cropped to the biggest same-shaped rectangle inside it."""
    t = math.radians(abs(angle))
    c, s = math.cos(t), math.sin(t)
    return min(w / (w * c + h * s), h / (w * s + h * c))


def apply_geometry(a: np.ndarray, g: Geometry) -> np.ndarray:
    if g.rotate:
        a = np.rot90(a, k=-(g.rotate // 90))          # clockwise
    if g.flip_h:
        a = a[:, ::-1]
    if g.flip_v:
        a = a[::-1]
    if g.angle:
        h, w = a.shape[:2]
        chans = [np.asarray(Image.fromarray(np.ascontiguousarray(a[..., c]), "F")
                            .rotate(-g.angle, resample=Image.Resampling.BICUBIC))
                 for c in range(3)]
        a = np.stack(chans, axis=-1)
        s = straighten_scale(w, h, g.angle)
        cw, ch = max(1, int(w * s)), max(1, int(h * s))
        x0, y0 = (w - cw) // 2, (h - ch) // 2
        a = a[y0:y0 + ch, x0:x0 + cw]
    if g.crop != (0.0, 0.0, 1.0, 1.0):
        h, w = a.shape[:2]
        x0, y0, x1, y1 = g.crop
        a = a[int(round(y0 * h)):max(int(round(y0 * h)) + 1, int(round(y1 * h))),
              int(round(x0 * w)):max(int(round(x0 * w)) + 1, int(round(x1 * w)))]
    return np.ascontiguousarray(a, dtype=np.float32)


# --- tone and colour -----------------------------------------------------------------------

TONE_KEYS = ("temp", "tint", "exposure", "whites", "blacks", "contrast", "fade", "_curves")


def curve_values(points, xs: np.ndarray) -> np.ndarray:
    """A monotone cubic (Fritsch-Carlson) through `points`, at `xs` - no
    overshoot, so a curve never makes a tone darker than a darker one."""
    px = np.array([p[0] for p in points], dtype=np.float64)
    py = np.array([p[1] for p in points], dtype=np.float64)
    if len(px) < 2:
        return xs.copy()
    h = np.diff(px)
    d = np.diff(py) / h
    m = np.empty_like(py)
    m[0], m[-1] = d[0], d[-1]
    for i in range(1, len(px) - 1):
        if d[i - 1] * d[i] <= 0:
            m[i] = 0.0                                # a peak or a flat: no overshoot
        else:
            w1, w2 = 2 * h[i] + h[i - 1], h[i] + 2 * h[i - 1]
            m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i])   # weighted harmonic mean of the slopes
    i = np.clip(np.searchsorted(px, xs, side="right") - 1, 0, len(px) - 2)
    t = (xs - px[i]) / h[i]
    t2, t3 = t * t, t * t * t
    y = ((2 * t3 - 3 * t2 + 1) * py[i] + (t3 - 2 * t2 + t) * h[i] * m[i]
         + (-2 * t3 + 3 * t2) * py[i + 1] + (t3 - t2) * h[i] * m[i + 1])
    return np.clip(y, 0, 1)


def tone_lut(p: dict) -> np.ndarray | None:
    """(3, 4096) table: sRGB channel value -> toned value, or None if no
    tone adjustment is set."""
    g = p.get
    if not any(g(k) for k in TONE_KEYS):
        return None
    lin = _TO_LIN.astype(np.float64)
    t, m = g("temp", 0) / 100, g("tint", 0) / 100
    gains = np.array([2 ** (0.45 * t), 2 ** (-0.35 * m), 2 ** (-0.45 * t)])
    gains /= float(gains @ LUMA)                      # white balance shouldn't change brightness
    gains *= 2 ** g("exposure", 0)
    bp = -0.04 * g("blacks", 0) / 100                 # blacks -100 -> black point up to 0.04
    wp = 1 - 0.35 * g("whites", 0) / 100              # whites +100 -> white point at 0.65
    out = np.empty((3, 4096), dtype=np.float32)
    c, f = g("contrast", 0) / 100, g("fade", 0) / 100
    knee = 0.85
    for ch in range(3):
        v = (lin * gains[ch] - bp) / max(1e-3, wp - bp)
        x = np.where(v <= 0.0031308, v * 12.92, 1.055 * np.power(np.maximum(v, 0), 1 / 2.4) - 0.055)
        # Pushed past white: roll off instead of clipping.
        x = np.where(x > knee, knee + (1 - knee) * np.tanh((x - knee) / (1 - knee)), x)
        x = np.clip(x, 0, 1)
        if c:
            x = x + c * 0.9 * (x * x * (3 - 2 * x) - x)
        if f:
            x = 0.09 * f + x * (1 - 0.15 * f)
        curves = g("_curves") or {}
        if "rgb" in curves:
            x = curve_values(curves["rgb"], x)
        if "rgb"[ch] in curves:
            x = curve_values(curves["rgb"[ch]], x)
        out[ch] = x
    return out


def apply_adjustments(a: np.ndarray, p: dict, *, yb: np.ndarray | None = None,
                      frame: tuple[int, int, int] | None = None) -> np.ndarray:
    """`p` = effective adjustments (stack.effective). Returns a new array.

    For a strip of a bigger image (apply_tiled): `frame` = (first row,
    full height, full width) and `yb` = the strip's rows of the whole
    image's blurred luminance, so every strip matches the whole."""
    if not p:
        return a
    g = p.get
    h, w = a.shape[:2]
    y0, full_h, full_w = frame or (0, h, w)
    scale = max(full_h, full_w) / 2000.0              # blur radii follow the image size

    # 2-3. everything that maps a channel value to a new value on its own -
    # white balance, exposure, black/white points, the highlight shoulder,
    # contrast, fade - composed into one lookup table per channel.
    lut = tone_lut(p)
    if lut is not None:
        idx = np.clip(a * 4095 + 0.5, 0, 4095).astype(np.uint16)
        x = np.stack([lut[c][idx[..., c]] for c in range(3)], axis=-1)
    else:
        x = a.copy()

    # Highlights / shadows: gains on a blurred luminance mask - local, so
    # shadows lift without flattening the whole picture.
    sh, hi = g("shadows", 0) / 100, g("highlights", 0) / 100
    if sh or hi:
        Yb = yb if yb is not None else blur(x @ LUMA, 25 * scale)
        ev = sh * 1.3 * (1 - _smoothstep(0.0, 0.55, Yb)) + hi * 1.3 * _smoothstep(0.45, 1.0, Yb)
        x *= np.exp2(ev, dtype=np.float32)[..., None]
        np.clip(x, 0, 1, out=x)

    # 4. colour
    vib, sat, hue = g("vibrance", 0) / 100, g("saturation", 0) / 100, g("hue", 0)
    if vib or sat or hue:
        x = _colour(x, vib, sat, hue)

    # 5. vignette, noise reduction, sharpening
    x = _finish(x, g, (y0, h, full_h, full_w), scale)
    return np.clip(x, 0, 1).astype(np.float32)


def _colour(x: np.ndarray, vib: float, sat: float, hue: float) -> np.ndarray:
    Y = x @ LUMA
    chroma = x - Y[..., None]
    if vib:
        cur = x.max(axis=-1) - x.min(axis=-1)
        chroma = chroma * (1 + vib * (1 - np.clip(cur * 1.6, 0, 1)))[..., None]
    if sat:
        chroma = chroma * (1 + sat)
    if hue:
        # Rotate about the grey axis (Rodrigues), which keeps luminance close.
        th = math.radians(hue)
        k = np.full(3, 1 / math.sqrt(3), dtype=np.float32)
        cos, sin = math.cos(th), math.sin(th)
        chroma = chroma * cos + np.cross(k, chroma) * sin + k * (chroma @ k)[..., None] * (1 - cos)
    return Y[..., None] + chroma


def _finish(x: np.ndarray, g, rows: tuple[int, int, int, int], scale: float) -> np.ndarray:
    y0, h, full_h, w = rows
    v = g("vignette", 0) / 100
    if v:
        yy = (np.arange(y0, y0 + h, dtype=np.float32) / max(1, full_h - 1) * 2 - 1)[:, None]
        xx = np.linspace(-1, 1, w, dtype=np.float32)[None, :]
        r = np.sqrt(xx * xx + yy * yy) / math.sqrt(2)
        x = x * (1 + v * 0.8 * _smoothstep(0.35, 1.0, r))[..., None].astype(np.float32)
    d = g("denoise", 0) / 100
    if d:
        Y = x @ LUMA
        chroma = x - Y[..., None]
        chroma = np.stack([blur(chroma[..., i], (1 + 4 * d) * scale) for i in range(3)], axis=-1)
        Y = Y + 0.6 * d * (blur(Y, 1.2 * scale) - Y)
        x = Y[..., None] + chroma
    s = g("sharpen", 0) / 100
    if s:
        Y = x @ LUMA
        x = x + (1.2 * s * (Y - blur(Y, 1.0 * max(scale, 0.5))))[..., None]
    return x


def split_noise(p: dict) -> tuple[dict, dict]:
    """(everything else, the noise-reduction settings) - noise reduction
    runs on the source (edit/denoise.py), not with the other adjustments."""
    from lunelis.edit.denoise import NR_KEYS
    return {k: v for k, v in p.items() if k not in NR_KEYS}, {k: v for k, v in p.items() if k in NR_KEYS}


def prepare_source(a: np.ndarray, stack: Stack, p_nr: dict, lens_info=None, *, strips: bool = False) -> np.ndarray:
    """Lens corrections, then noise reduction: the per-photo work that the
    other sliders never change (the edit session caches its result)."""
    if stack.lens:
        from lunelis.edit import lens
        a = lens.apply(a, stack.lens, lens_info)
    from lunelis.edit import denoise
    if denoise.active(p_nr):
        a = denoise.apply_strips(a, p_nr) if strips else denoise.apply(a, p_nr)
    return a


def apply(a: np.ndarray, stack: Stack, filter_params: dict | None = None,
          ai_maps: dict | None = None, lens_info=None, prepared: bool = False) -> np.ndarray:
    p, p_nr = split_noise(effective(stack, filter_params))
    if not prepared:
        a = prepare_source(a, stack, p_nr, lens_info)
    x = apply_geometry(a, stack.geometry)
    if stack.retouch:
        from lunelis.edit import retouch
        x = retouch.apply(x, stack.retouch, stack.geometry.crop)
    x = apply_adjustments(x, p)
    if stack.masks:
        x = apply_masks(x, stack.masks, stack.geometry.crop, ai_maps=ai_in_frame(ai_maps, stack.geometry))
    return x


def ai_in_frame(ai_maps: dict | None, geometry) -> dict | None:
    """AI masks are made on the upright photo; masks live in the rotated,
    flipped, straightened (uncropped) frame - turn the maps the same way."""
    if not ai_maps:
        return ai_maps
    from dataclasses import replace
    g = replace(geometry, crop=(0.0, 0.0, 1.0, 1.0))
    if g.is_identity():
        return ai_maps
    return {k: apply_geometry(np.repeat(m[..., None], 3, axis=2), g)[..., 0].copy() for k, m in ai_maps.items()}


def local_params(adjust: dict) -> dict:
    from lunelis.edit.masks import LOCAL_KEYS
    return {k: v for k, v in adjust.items() if k in LOCAL_KEYS and v}


def apply_masks(x: np.ndarray, masks: tuple, crop, *, frame: tuple[int, int, int] | None = None,
                yb: np.ndarray | None = None, cache: dict | None = None,
                ai_maps: dict | None = None) -> np.ndarray:
    """Blend each mask's adjustments over `x` by its alpha. For a strip,
    `frame` = (first row, full height, full width) and `yb` = the strip's
    rows of the whole image's blurred luminance (local shadows/highlights)."""
    from lunelis.edit import masks as M
    h, w = x.shape[:2]
    y0, full_h, full_w = frame or (0, h, w)
    fr = M.Frame(crop, full_h, full_w, y0, h)
    cache = {} if cache is None else cache
    for m in masks:
        lp = local_params(m.adjust)
        if not lp:
            continue
        a = M.alpha(m, fr, cache, ai_maps)
        if not a.any():
            continue
        if (lp.get("shadows") or lp.get("highlights")) and yb is None:
            yb = blur(x @ LUMA, 25 * max(full_h, full_w) / 2000.0)
        local = apply_adjustments(x, lp, yb=yb, frame=(y0, full_h, full_w))
        x = x + (local - x) * a[..., None]
    return x


def apply_tiled(a: np.ndarray, stack: Stack, filter_params: dict | None = None,
                rows: int = 512, ai_maps: dict | None = None, lens_info=None) -> np.ndarray:
    """apply() for a full-size export, as uint8, in strips of `rows` rows -
    a 60 MP photo in one piece would need several GB of temporary arrays.
    The wide shadows/highlights mask is made once from a reduced copy; the
    small blurs (sharpening, noise) get overlapping margins."""
    p, p_nr = split_noise(effective(stack, filter_params))
    a = prepare_source(a, stack, p_nr, lens_info, strips=True)
    a = apply_geometry(a, stack.geometry)
    if stack.retouch:
        from lunelis.edit import retouch
        a = retouch.apply(a, stack.retouch, stack.geometry.crop, copy=False)
    H, W = a.shape[:2]
    out = np.empty((H, W, 3), dtype=np.uint8)
    if not p and not stack.masks:
        for y in range(0, H, rows):
            out[y:y + rows] = (np.clip(a[y:y + rows], 0, 1) * 255 + 0.5).astype(np.uint8)
        return out
    scale = max(H, W) / 2000.0
    yb_full = None
    local_sh = any(m.adjust.get("shadows") or m.adjust.get("highlights") for m in stack.masks)
    if p.get("shadows") or p.get("highlights") or local_sh:
        k = max(1, max(H, W) // 1000)
        small = np.ascontiguousarray(a[::k, ::k])
        lut = tone_lut(p)
        if lut is not None:
            idx = np.clip(small * 4095 + 0.5, 0, 4095).astype(np.uint16)
            small = np.stack([lut[c][idx[..., c]] for c in range(3)], axis=-1)
        yb_small = blur(np.ascontiguousarray(small @ LUMA), 25 * scale / k)
        yb_full = _resize(yb_small, W, H, Image.Resampling.BILINEAR)
    pad = int(3 * 5 * scale) + 8                       # the widest small blur (noise reduction) + some
    mask_cache: dict = {}                              # brush rasters, made once for all strips
    ai_maps = ai_in_frame(ai_maps, stack.geometry)
    for y in range(0, H, rows):
        s0, s1 = max(0, y - pad), min(H, y + rows + pad)
        yb = None if yb_full is None else yb_full[s0:s1]
        strip = apply_adjustments(a[s0:s1], p, yb=yb, frame=(s0, H, W))
        if stack.masks:
            strip = apply_masks(strip, stack.masks, stack.geometry.crop, frame=(s0, H, W), yb=yb,
                                cache=mask_cache, ai_maps=ai_maps)
            np.clip(strip, 0, 1, out=strip)
        out[y:y + rows] = (strip[y - s0:y - s0 + rows] * 255 + 0.5).astype(np.uint8)
    return out


# --- auto-enhance ----------------------------------------------------------------------------

def auto(a: np.ndarray) -> dict:
    """A starting point from the histogram: exposure to put the middle tones
    near mid-grey, whites/blacks to use the full range, a little vibrance."""
    small = a[:: max(1, a.shape[0] // 400), :: max(1, a.shape[1] // 400)]
    Y = small @ LUMA
    lin_med = float(np.median(srgb_to_linear(Y)))
    exposure = 0.0 if lin_med <= 0 else max(-2.0, min(2.0, 0.8 * math.log2(0.18 / max(lin_med, 1e-4))))
    p1, p99 = (float(v) for v in np.percentile(Y, (1, 99)))
    out = {"exposure": round(exposure, 2), "vibrance": 15.0, "contrast": 10.0}
    if p99 < 0.92:
        out["whites"] = float(round(min(60, (0.92 - p99) * 120)))
    if p1 > 0.04:
        out["blacks"] = float(-round(min(50, (p1 - 0.04) * 200)))
    if p99 > 0.99:
        out["highlights"] = -25.0
    if p1 < 0.01:
        out["shadows"] = 15.0
    return {k: v for k, v in out.items() if v}
