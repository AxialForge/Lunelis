"""
Animations from photos: a burst or a selection as a GIF, MP4 or WebP.

- **Frames** are the photos in the order given (the page lets you reorder
  and leave some out - "trim"), each with its edits, at `long_edge`. They
  all take the first frame's size; a photo of another shape is cropped to
  fill it (centred), never stretched.
- **Speed:** `frame_ms` per frame. **Loop:** GIF / WebP repeat forever
  (`loops` = 0) or `loops` times; an MP4 has no loop flag, so its frames are
  written `loops` times (0 = once). **Bounce:** forward then back.
- **Formats:** MP4 (H.264, best quality and smallest), WebP (animated, good
  quality, plays in browsers), GIF (plays everywhere; 256 colours a frame,
  so it's kept smaller).
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Callable

from PIL import Image, ImageOps

from lunelis.create import engine

KINDS = {"mp4": ".mp4", "webp": ".webp", "gif": ".gif"}
MAX_FRAMES = 300
GIF_MAX_EDGE = 1080


@dataclass
class AnimOptions:
    kind: str = "mp4"                 # mp4 | webp | gif
    frame_ms: int = 150
    loops: int = 0                    # GIF/WebP: 0 = forever; MP4: times the frames are repeated (0 = once)
    bounce: bool = False
    long_edge: int = 1080
    quality: int = 80                 # 1-100

    def check(self, n: int) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"kind must be one of {tuple(KINDS)}")
        if not 20 <= self.frame_ms <= 10_000:
            raise ValueError("a frame must last 20 ms to 10 s")
        if not 0 <= self.loops <= 100:
            raise ValueError("loops must be 0-100")
        if not 64 <= self.long_edge <= 4096:
            raise ValueError("the size must be 64-4096 px")
        if not 1 <= self.quality <= 100:
            raise ValueError("quality must be 1-100")
        if n < 2:
            raise ValueError("an animation needs at least two photos")
        if n > MAX_FRAMES:
            raise ValueError(f"an animation can have at most {MAX_FRAMES} photos")


class Cancelled(Exception):
    pass


def sequence(frames: list, bounce: bool) -> list:
    """Bounce: 1 2 3 4 -> 1 2 3 4 3 2 (the ends aren't shown twice in a row when it loops)."""
    return frames + frames[-2:0:-1] if bounce and len(frames) > 2 else list(frames)


def frames_of(conn: sqlite3.Connection, file_ids: list[int], long_edge: int,
              progress: Callable[[int, int], None] | None = None,
              cancelled: Callable[[], bool] | None = None) -> list[Image.Image]:
    out: list[Image.Image] = []
    for i, fid in enumerate(file_ids):
        if cancelled and cancelled():
            raise Cancelled()
        img = engine.photo(conn, fid, long_edge)
        if out and img.size != out[0].size:
            img = ImageOps.fit(img, out[0].size, Image.Resampling.LANCZOS)
        out.append(img)
        if progress:
            progress(i + 1, len(file_ids))
    return out


def _even(img: Image.Image) -> Image.Image:
    w, h = img.width - img.width % 2, img.height - img.height % 2       # H.264 4:2:0 needs even sides
    return img if (w, h) == img.size else img.crop((0, 0, w, h))


def write(frames: list[Image.Image], opts: AnimOptions, folder: str | Path, name: str) -> str:
    """Encode `frames` (already in order) to a new file; returns its path."""
    from lunelis.edit.export import _BAD, free_path
    os.makedirs(folder, exist_ok=True)
    dest = free_path(str(folder), (_BAD.sub("_", name).strip(" .") or "Animation") + KINDS[opts.kind])
    seq = sequence(frames, opts.bounce)
    if opts.kind == "gif":
        edge = min(opts.long_edge, GIF_MAX_EDGE)
        small = [f if max(f.size) <= edge else ImageOps.contain(f, (edge, edge), Image.Resampling.LANCZOS)
                 for f in seq]
        pal = [f.convert("RGB").quantize(colors=256, method=Image.Quantize.MEDIANCUT,
                                         dither=Image.Dither.FLOYDSTEINBERG) for f in small]
        pal[0].save(dest, "GIF", save_all=True, append_images=pal[1:], duration=opts.frame_ms,
                    loop=opts.loops, optimize=False, disposal=1)
    elif opts.kind == "webp":
        seq[0].save(dest, "WEBP", save_all=True, append_images=seq[1:], duration=opts.frame_ms,
                    loop=opts.loops, quality=opts.quality, method=4)
    else:
        import av
        seq = [_even(f) for f in seq] * max(1, opts.loops)
        crf = round(51 - opts.quality * 0.4)                             # quality 80 -> CRF 19
        with av.open(dest, "w") as out:
            s = out.add_stream("libx264", rate=Fraction(1000, opts.frame_ms).limit_denominator(1000))
            s.width, s.height = seq[0].size
            s.pix_fmt = "yuv420p"
            s.options = {"crf": str(crf), "preset": "medium"}
            for f in seq:
                for pkt in s.encode(av.VideoFrame.from_image(f.convert("RGB"))):
                    out.mux(pkt)
            for pkt in s.encode():
                out.mux(pkt)
    return dest


def make(conn: sqlite3.Connection, file_ids: list[int], opts: AnimOptions, folder: str | Path, name: str,
         progress: Callable[[int, int], None] | None = None,
         cancelled: Callable[[], bool] | None = None) -> str:
    opts.check(len(file_ids))
    frames = frames_of(conn, file_ids, opts.long_edge, progress, cancelled)
    if cancelled and cancelled():
        raise Cancelled()
    return write(frames, opts, folder, name)
