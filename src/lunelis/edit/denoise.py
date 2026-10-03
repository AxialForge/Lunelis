"""
Noise reduction - the Detail section's Noise reduction, Color noise and
Detail sliders.

1. The photo's own noise is MEASURED (median absolute deviation of a
   high-pass of the brightness, on a central patch), at the resolution
   being processed - so a 1600 px preview and a 9504 px export get the
   strength right for their own pixels.
2. Brightness noise: non-local means (OpenCV) on 16-bit luminance. Each
   pixel is averaged with pixels whose NEIGHBOURHOODS look alike, found
   anywhere nearby - so texture survives where a blur would smear it.
3. Detail: some of the removed high frequencies are blended back
   (0 = balanced, + keeps more texture, - smoother).
4. Colour noise (the blotches in dark areas): non-local means on the two
   colour channels, at full size - at half size thin coloured detail (a
   gold chain) averaged away to grey.

It runs early - on the source, after lens corrections, before any tone
work - so the edit session can cache it: moving any other slider never
re-runs it. Exports run it in strips with overlaps.
"""
from __future__ import annotations

import numpy as np

NR_KEYS = ("denoise", "denoise_color", "denoise_detail")


def active(p: dict) -> bool:
    return bool(p.get("denoise") or p.get("denoise_color"))


def estimate_sigma(y: np.ndarray) -> float:
    """Noise standard deviation of a 0..1 luminance image."""
    import cv2
    h, w = y.shape
    ch, cw = min(h, 800), min(w, 800)
    patch = y[(h - ch) // 2:(h + ch) // 2, (w - cw) // 2:(w + cw) // 2].astype(np.float32)
    hp = patch - cv2.GaussianBlur(patch, (0, 0), 1.0)
    return float(1.4826 * np.median(np.abs(hp - np.median(hp)))) * 1.25   # the blur keeps ~80 % of it


def _nlm16(chan: np.ndarray, h: float) -> np.ndarray:
    """Non-local means of a 0..1 float channel, in 16 bits."""
    import cv2
    u16 = (np.clip(chan, 0, 1) * 65535 + 0.5).astype(np.uint16)
    out = cv2.fastNlMeansDenoising(u16, h=[max(1.0, h * 65535)], templateWindowSize=7,
                                   searchWindowSize=21, normType=cv2.NORM_L1)   # 16-bit: needs L1
    return out.astype(np.float32) / 65535


def apply(a: np.ndarray, p: dict, sigma: float | None = None) -> np.ndarray:
    """Noise-reduced copy of a float32 sRGB image (H x W x 3)."""
    if not active(p):
        return a
    import cv2
    lum, col = p.get("denoise", 0) / 100, p.get("denoise_color", 0) / 100
    detail = 0.3 + 0.25 * p.get("denoise_detail", 0) / 100          # share of the removed texture put back
    ycc = cv2.cvtColor(np.clip(a, 0, 1).astype(np.float32), cv2.COLOR_RGB2YCrCb)
    y = ycc[..., 0]
    if sigma is None:
        sigma = estimate_sigma(y)
    if lum:
        yd = _nlm16(y, sigma * (0.5 + 2.0 * lum))
        y = yd + detail * (1 - 0.5 * lum) * (y - yd)
        ycc[..., 0] = y
    if col:
        # Full resolution: at half size a thin coloured detail (a gold chain)
        # averaged away to grey. Colour noise is weaker per channel than
        # brightness noise, hence the smaller h.
        for i in (1, 2):
            ycc[..., i] = _nlm16(ycc[..., i], sigma * (0.4 + 1.6 * col))
    return np.clip(cv2.cvtColor(ycc, cv2.COLOR_YCrCb2RGB), 0, 1).astype(np.float32)


def apply_strips(a: np.ndarray, p: dict, rows: int = 1024, pad: int = 24) -> np.ndarray:
    """apply() for a full-size export: strips with overlapping margins, one
    noise measurement for the whole photo."""
    if not active(p):
        return a
    import cv2
    H, W = a.shape[:2]
    c = a[max(0, H // 2 - 512):H // 2 + 512, max(0, W // 2 - 512):W // 2 + 512]   # full-res pixels, centre
    sigma = estimate_sigma(cv2.cvtColor(np.clip(c, 0, 1).astype(np.float32), cv2.COLOR_RGB2YCrCb)[..., 0])
    out = np.empty_like(a)
    for y0 in range(0, H, rows):
        s0, s1 = max(0, y0 - pad), min(H, y0 + rows + pad)
        strip = apply(a[s0:s1], p, sigma)
        out[y0:y0 + rows] = strip[y0 - s0:y0 - s0 + min(rows, H - y0)]
    return out
