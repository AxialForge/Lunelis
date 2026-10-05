"""
Stacking several frames into one picture - Create, round three.

- **Focus stack:** frames focused at different distances; each is lined up
  with the middle one, and every pixel comes from the frame that's sharpest
  there (smoothly, so the joins don't show): front-to-back sharpness.
- **Star trails:** night frames from a tripod combined with "lighten" - each
  pixel keeps its brightest value, so the stars draw their arcs. Not lined up
  (the stars are meant to move).
- **Median stack:** the same scene shot several times, lined up; each pixel
  takes the middle value, so people walking through disappear.

Frames come with their edits (engine.photo) at the chosen size, and the
result is a new file in the Create folder - never over anything.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

from lunelis.create import engine

KINDS = {"focus": "Focus stack", "trails": "Star trails", "median": "Median stack"}
SIZES = {"full": None, "6000": 6000, "4096": 4096, "2048": 2048}


@dataclass
class StackOptions:
    kind: str = "focus"
    size: str = "4096"
    align: bool = True
    format: str = "jpeg"                 # jpeg | tiff

    def check(self, n: int) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"kind must be one of {tuple(KINDS)}")
        if self.size not in SIZES:
            raise ValueError(f"size must be one of {tuple(SIZES)}")
        if self.format not in ("jpeg", "tiff"):
            raise ValueError("save as JPEG or TIFF")
        if n < (3 if self.kind == "median" else 2):
            raise ValueError(f"a {KINDS[self.kind].lower()} needs at least {3 if self.kind == 'median' else 2} photos")


def _gray(a: np.ndarray) -> np.ndarray:
    import cv2
    return cv2.cvtColor(a, cv2.COLOR_RGB2GRAY)


def align_to(ref: np.ndarray, img: np.ndarray) -> np.ndarray:
    """`img` moved (shift, turn, scale) onto `ref` by matching features; unchanged if they don't match."""
    import cv2
    h, w = ref.shape[:2]
    k = max(1, max(h, w) // 1200)
    a, b = _gray(ref)[::k, ::k], _gray(img)[::k, ::k]
    orb = cv2.ORB_create(3000)
    ka, da = orb.detectAndCompute(a, None)
    kb, db = orb.detectAndCompute(b, None)
    if da is None or db is None or len(ka) < 12 or len(kb) < 12:
        return img
    matches = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(da, db)
    if len(matches) < 12:
        return img
    pa = np.float32([ka[m.queryIdx].pt for m in matches]) * k
    pb = np.float32([kb[m.trainIdx].pt for m in matches]) * k
    m, inl = cv2.estimateAffinePartial2D(pb, pa, method=cv2.RANSAC, ransacReprojThreshold=3.0 * k)
    if m is None or inl is None or inl.sum() < 10:
        return img
    return cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def sharpness(a: np.ndarray) -> np.ndarray:
    import cv2
    g = _gray(a).astype(np.float32)
    lap = np.abs(cv2.Laplacian(cv2.GaussianBlur(g, (0, 0), 1.0), cv2.CV_32F, ksize=3))
    return cv2.GaussianBlur(lap, (0, 0), max(2.0, max(a.shape[:2]) / 400))


def focus_stack(frames: list[np.ndarray]) -> np.ndarray:
    """Per pixel, mostly the sharpest frame (soft weights, so joins don't show)."""
    s = np.stack([sharpness(f) for f in frames])                   # (n, h, w)
    w = np.power(s + 1e-3, 4)
    w /= w.sum(axis=0, keepdims=True)
    out = np.zeros(frames[0].shape, np.float32)
    for f, wt in zip(frames, w):
        out += f.astype(np.float32) * wt[..., None]
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def lighten(frames: list[np.ndarray]) -> np.ndarray:
    return np.maximum.reduce(frames)


def median(frames: list[np.ndarray]) -> np.ndarray:
    return np.median(np.stack(frames), axis=0).astype(np.uint8)


def combine(frames: list[np.ndarray], kind: str, align: bool = True,
            progress: Callable[[int, int], None] | None = None) -> np.ndarray:
    if align and kind != "trails":
        ref = frames[len(frames) // 2]
        frames = [f if f is ref else align_to(ref, f) for f in frames]
    if progress:
        progress(1, 1)
    return {"focus": focus_stack, "trails": lighten, "median": median}[kind](frames)


def make(conn: sqlite3.Connection, file_ids: list[int], opts: StackOptions, folder: str | Path, name: str,
         progress: Callable[[int, int], None] | None = None,
         cancelled: Callable[[], bool] | None = None) -> str:
    from lunelis.create.animation import Cancelled
    opts.check(len(file_ids))
    n = len(file_ids)
    frames, size = [], None
    for i, fid in enumerate(file_ids):
        if cancelled and cancelled():
            raise Cancelled()
        img = engine.photo(conn, fid, SIZES[opts.size]).convert("RGB")
        size = size or img.size
        if img.size != size:
            img = img.resize(size, Image.Resampling.LANCZOS)        # a stray frame of another size
        frames.append(np.asarray(img))
        if progress:
            progress(i + 1, n + 1)
    out = combine(frames, opts.kind, opts.align)
    if progress:
        progress(n + 1, n + 1)
    preset = engine.Preset(KINDS[opts.kind], None, None, "inside", opts.format, 95)
    return engine.save(Image.fromarray(out, "RGB"), preset, folder, name)
