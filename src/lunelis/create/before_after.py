"""
Before and after: a photo as it was shot next to how you edited it.

- **Side by side** (or one above the other), labelled, as a picture.
- **Slider:** an MP4 or GIF where a line sweeps across, the "before" on its
  left and the "after" on its right, there and back.

The "before" keeps the photo's crop and rotation, so the two line up.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

from lunelis.create import engine

LAYOUTS = ("side", "stacked", "slider")


@dataclass
class BeforeAfterOptions:
    layout: str = "side"              # side | stacked | slider
    long_edge: int = 2048
    labels: bool = True
    slider_kind: str = "mp4"          # mp4 | gif (slider only)
    seconds: float = 4.0              # slider: one sweep there and back

    def check(self) -> None:
        if self.layout not in LAYOUTS:
            raise ValueError(f"layout must be one of {LAYOUTS}")
        if self.slider_kind not in ("mp4", "gif"):
            raise ValueError("the slider is an MP4 or a GIF")
        if not 64 <= self.long_edge <= 8000:
            raise ValueError("the size must be 64-8000 px")


def pair(conn: sqlite3.Connection, file_id: int, edge: int) -> tuple[Image.Image, Image.Image]:
    from lunelis.edit.export import rendered
    after = rendered(conn, file_id, edge)
    before = rendered(conn, file_id, edge, geometry_only=True)
    if before.size != after.size:
        before = before.resize(after.size, Image.Resampling.LANCZOS)
    return before, after


def _label(img: Image.Image, text: str) -> None:
    from lunelis.create.batch import _font
    d = ImageDraw.Draw(img)
    px = max(14, round(min(img.size) * 0.045))
    font = _font(px)
    l, t, r, b = d.textbbox((0, 0), text, font=font)
    pad = px // 2
    d.rounded_rectangle((pad, pad, pad + r - l + 2 * pad, pad + b - t + 2 * pad), pad // 2, fill=(0, 0, 0))
    d.text((2 * pad - l, 2 * pad - t), text, font=font, fill=(255, 255, 255))


def combined(before: Image.Image, after: Image.Image, stacked: bool, labels: bool) -> Image.Image:
    b, a = before.copy(), after.copy()
    if labels:
        _label(b, "Before")
        _label(a, "After")
    gap = max(4, round(min(a.size) * 0.01))
    if stacked:
        out = Image.new("RGB", (a.width, a.height * 2 + gap), "white")
        out.paste(b, (0, 0))
        out.paste(a, (0, a.height + gap))
    else:
        out = Image.new("RGB", (a.width * 2 + gap, a.height), "white")
        out.paste(b, (0, 0))
        out.paste(a, (a.width + gap, 0))
    return out


def slider_frames(before: Image.Image, after: Image.Image, n: int, labels: bool = True) -> list[Image.Image]:
    """The line goes left to right and back over n frames."""
    w, h = after.size
    b, a = before.copy(), after.copy()
    if labels:
        _label(b, "Before")
    out = []
    for i in range(n):
        t = i / max(1, n - 1)
        x = round(w * (2 * t if t <= 0.5 else 2 * (1 - t)))
        x = min(max(x, 0), w)
        f = a.copy()
        if x:
            f.paste(b.crop((0, 0, x, h)), (0, 0))
        ImageDraw.Draw(f).rectangle((max(0, x - 2), 0, min(w, x + 1), h), fill=(255, 255, 255))
        out.append(f)
    return out


def make(conn: sqlite3.Connection, file_ids: list[int], opts: BeforeAfterOptions, folder: str | Path, name: str,
         progress=None, cancelled=None) -> list[str]:
    from lunelis.create import animation
    opts.check()
    made = []
    for i, fid in enumerate(file_ids):
        if cancelled and cancelled():
            raise animation.Cancelled()
        edge = opts.long_edge if opts.layout != "slider" else min(opts.long_edge, 1920)
        before, after = pair(conn, fid, edge)
        stem = name if len(file_ids) == 1 else f"{name} {i + 1:03d}"
        if opts.layout == "slider":
            fps = 24 if opts.slider_kind == "mp4" else 12
            frames = slider_frames(before, after, max(8, round(opts.seconds * fps)), opts.labels)
            made.append(animation.write(frames, animation.AnimOptions(opts.slider_kind, round(1000 / fps), 0,
                                                                      False, edge, 82), folder, stem))
        else:
            made.append(engine.save(combined(before, after, opts.layout == "stacked", opts.labels),
                                    engine.Preset("Before and after", format="jpeg", quality=92), folder, stem))
        if progress:
            progress(i + 1, len(file_ids))
    return made
