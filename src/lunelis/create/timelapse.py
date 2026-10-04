"""
Timelapses: a sequence of photos as a smooth MP4.

Two passes, so a thousand frames never sit in memory together:

1. **Measure** from the thumbnails (quick, local): each frame's brightness
   and, if stabilising, how far it moved against the one before (phase
   correlation on a grey 512 px copy).
2. **Make:** each frame with its edits at the chosen size, cropped to fill
   16:9 (or the first frame's shape), corrected, and encoded straight away.

- **Deflicker:** exposure jumps between frames (auto exposure, a cloud,
  aperture flicker) are evened out - each frame is brought to the moving
  average of its neighbours' brightness, not to one fixed level, so a sunset
  still gets darker.
- **Stabilise:** the camera's drift and knocks are taken out; the picture is
  enlarged just enough that the moved edges never show.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageOps

from lunelis.create import engine

SIZES = {"720p": (1280, 720), "1080p": (1920, 1080), "4k": (3840, 2160)}


@dataclass
class TimelapseOptions:
    fps: int = 24
    size: str = "1080p"
    deflicker: bool = True
    window: int = 15                  # frames averaged for deflicker
    stabilize: bool = False
    quality: int = 80

    def check(self, n: int) -> None:
        if not 1 <= self.fps <= 60:
            raise ValueError("1-60 frames a second")
        if self.size not in SIZES:
            raise ValueError(f"size must be one of {tuple(SIZES)}")
        if not 3 <= self.window <= 99:
            raise ValueError("deflicker over 3-99 frames")
        if n < 2:
            raise ValueError("a timelapse needs at least two photos")


def luminance(img: Image.Image) -> float:
    return float(np.asarray(img.convert("L"), dtype=np.float32).mean())


def gains(lums: list[float], window: int) -> np.ndarray:
    """Each frame's multiplier: its neighbours' average brightness / its own."""
    a = np.asarray(lums, dtype=np.float64)
    k = max(1, window // 2)
    padded = np.pad(a, k, mode="edge")
    smooth = np.convolve(padded, np.ones(2 * k + 1) / (2 * k + 1), mode="valid")
    return smooth / np.maximum(a, 1e-3)


def shifts(images: list[Image.Image]) -> np.ndarray:
    """Each frame's (dx, dy) from the first, in the images' own pixels (frame
    to frame by phase correlation, accumulated)."""
    import cv2
    out = [(0.0, 0.0)]
    prev = None
    acc = np.zeros(2)
    for img in images:
        g = np.asarray(img.convert("L"), dtype=np.float32)
        if prev is not None and g.shape == prev.shape:
            win = cv2.createHanningWindow(g.shape[::-1], cv2.CV_32F)
            (dx, dy), _ = cv2.phaseCorrelate(prev, g, win)
            acc += (dx, dy)
            out.append((float(acc[0]), float(acc[1])))
        elif prev is not None:
            out.append(out[-1])
        prev = g
    return np.array(out[:len(images)])


def make(conn: sqlite3.Connection, file_ids: list[int], opts: TimelapseOptions, folder: str | Path, name: str,
         progress: Callable[[int, int], None] | None = None,
         cancelled: Callable[[], bool] | None = None) -> str:
    import av
    from lunelis.create.animation import Cancelled
    from lunelis.edit.export import _BAD, free_path
    opts.check(len(file_ids))
    n = len(file_ids)
    # 1. measure, from thumbnails
    thumbs = []
    for i, fid in enumerate(file_ids):
        if cancelled and cancelled():
            raise Cancelled()
        try:
            thumbs.append(engine.thumb(conn, fid, 512))
        except OSError:
            thumbs.append(engine.photo(conn, fid, 512))
        if progress:
            progress(i + 1, 2 * n)
    gain = gains([luminance(t) for t in thumbs], opts.window) if opts.deflicker else np.ones(n)
    W, H = SIZES[opts.size]
    move = np.zeros((n, 2))
    zoom = 1.0
    if opts.stabilize:
        sized = [ImageOps.fit(t, (512, 288)) for t in thumbs]
        move = shifts(sized) * (W / 512.0)
        move -= move.mean(axis=0)                           # centre the path: the least enlargement
        margin_x, margin_y = np.abs(move[:, 0]).max(), np.abs(move[:, 1]).max()
        zoom = max(1.0, (W + 2 * margin_x) / W, (H + 2 * margin_y) / H)
    del thumbs
    # 2. make
    os.makedirs(folder, exist_ok=True)
    dest = free_path(str(folder), (_BAD.sub("_", name).strip(" .") or "Timelapse") + ".mp4")
    crf = round(51 - opts.quality * 0.4)
    try:
        with av.open(dest, "w") as out:
            s = out.add_stream("libx264", rate=Fraction(opts.fps, 1))
            s.width, s.height = W, H
            s.pix_fmt = "yuv420p"
            s.options = {"crf": str(crf), "preset": "medium"}
            big = (round(W * zoom), round(H * zoom))
            for i, fid in enumerate(file_ids):
                if cancelled and cancelled():
                    raise Cancelled()
                img = ImageOps.fit(engine.photo(conn, fid, max(big)), big, Image.Resampling.LANCZOS)
                if abs(gain[i] - 1.0) > 1e-3:
                    a = np.asarray(img, dtype=np.float32) * float(gain[i])
                    img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
                cx = (big[0] - W) / 2 + move[i, 0]
                cy = (big[1] - H) / 2 + move[i, 1]
                left, top = int(round(min(max(cx, 0), big[0] - W))), int(round(min(max(cy, 0), big[1] - H)))
                frame = img.crop((left, top, left + W, top + H))
                for pkt in s.encode(av.VideoFrame.from_image(frame)):
                    out.mux(pkt)
                if progress:
                    progress(n + i + 1, 2 * n)
            for pkt in s.encode():
                out.mux(pkt)
    except Cancelled:
        Path(dest).unlink(missing_ok=True)                 # nothing half-made is kept
        raise
    return dest
