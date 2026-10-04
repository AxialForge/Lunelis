"""
Batch tools: copies of many photos at once - always NEW files in a new
folder, the originals only read.

Per photo, in this order: the photo with its edits -> resized (a preset's
box and fit) -> watermark -> saved in the preset's format with the metadata
you chose (all / everything but the location / none, rebuilt from the
catalog like Export) under a name from the pattern.

- **Watermark:** your text, in a corner or the centre, its size as % of the
  photo's short side, an opacity, white or black (a soft shadow keeps it
  readable on any photo).
- **Rename:** {name} (the original's name), {n} (001...), {date} (capture
  date) and plain text - as Export. Names never collide: " (2)".
"""
from __future__ import annotations

import os
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from lunelis.create import engine

POSITIONS = ("bottom right", "bottom left", "top right", "top left", "centre")
METADATA = ("all", "no_location", "none")


@dataclass
class Watermark:
    text: str = ""
    position: str = "bottom right"
    size: float = 4.0                   # % of the short side
    opacity: int = 70                   # %
    color: str = "white"                # white | black

    def active(self) -> bool:
        return bool(self.text.strip())


@dataclass
class BatchOptions:
    preset: engine.Preset = field(default_factory=lambda: engine.ORIGINAL)
    pattern: str = "{name}"
    metadata: str = "all"
    watermark: Watermark = field(default_factory=Watermark)

    def check(self) -> None:
        self.preset.check()
        if self.metadata not in METADATA:
            raise ValueError("metadata must be all, no_location or none")
        if not self.pattern.strip():
            raise ValueError("the name pattern can't be empty")
        w = self.watermark
        if w.active():
            if w.position not in POSITIONS:
                raise ValueError(f"the watermark position must be one of {POSITIONS}")
            if not 0.5 <= w.size <= 30 or not 1 <= w.opacity <= 100:
                raise ValueError("watermark size must be 0.5-30 % and opacity 1-100 %")


class Cancelled(Exception):
    pass


def _font(px: int) -> ImageFont.ImageFont:
    for name in ("segoeuib.ttf", "segoeui.ttf", "arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf"):
        for folder in ((os.environ.get("WINDIR", r"C:\Windows") + r"\Fonts") if sys.platform == "win32" else "",
                       ""):
            try:
                return ImageFont.truetype(os.path.join(folder, name) if folder else name, px)
            except OSError:
                continue
    return ImageFont.load_default(px)


def watermark(img: Image.Image, w: Watermark) -> Image.Image:
    if not w.active():
        return img
    base = img.convert("RGBA")
    short = min(base.size)
    px = max(8, round(short * w.size / 100))
    font = _font(px)
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    l, t, r, b = d.textbbox((0, 0), w.text, font=font)
    tw, th = r - l, b - t
    margin = round(short * 0.03)
    x = {"bottom right": base.width - tw - margin, "top right": base.width - tw - margin,
         "bottom left": margin, "top left": margin, "centre": (base.width - tw) // 2}[w.position]
    y = {"bottom right": base.height - th - margin, "bottom left": base.height - th - margin,
         "top right": margin, "top left": margin, "centre": (base.height - th) // 2}[w.position]
    alpha = round(255 * w.opacity / 100)
    ink, shade = ((255, 255, 255), (0, 0, 0)) if w.color == "white" else ((0, 0, 0), (255, 255, 255))
    shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).text((x - l + max(1, px // 20), y - t + max(1, px // 20)), w.text, font=font,
                                fill=(*shade, alpha // 2))
    layer = Image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(max(1, px // 15))), layer)
    ImageDraw.Draw(layer).text((x - l, y - t), w.text, font=font, fill=(*ink, alpha))
    return Image.alpha_composite(base, layer).convert("RGB")


def run(conn: sqlite3.Connection, file_ids: list[int], opts: BatchOptions, folder: str | Path,
        progress: Callable[[int, int], None] | None = None,
        cancelled: Callable[[], bool] | None = None) -> list[str]:
    """Make the copies; returns their paths. A photo that fails is skipped
    and named in BatchError.failed after the rest are done."""
    from lunelis.edit.export import exif_bytes, file_name
    opts.check()
    made, failed = [], []
    for n, fid in enumerate(file_ids, 1):
        if cancelled and cancelled():
            raise Cancelled()
        row = conn.execute("SELECT f.filename, e.captured_at FROM files f LEFT JOIN exif e ON e.file_id = f.id"
                           " WHERE f.id = ?", (fid,)).fetchone()
        try:
            if row is None:
                raise ValueError("not in the catalog")
            img = engine.fit(engine.photo(conn, fid, engine.needed_edge(opts.preset)), opts.preset)
            img = watermark(img, opts.watermark)
            name = os.path.splitext(file_name(opts.pattern, row[0], row[1], n, ""))[0]
            made.append(engine.save(img, opts.preset, folder, name, exif_bytes(conn, fid, opts.metadata, img.size)))
        except Exception as e:                       # keep going: the rest still get made
            failed.append((row[0] if row else str(fid), str(e)))
        if progress:
            progress(n, len(file_ids))
    if failed:
        raise BatchError(made, failed)
    return made


class BatchError(Exception):
    def __init__(self, made: list[str], failed: list[tuple[str, str]]) -> None:
        super().__init__(f"{len(failed):,} of {len(made) + len(failed):,} couldn't be made: "
                         + "; ".join(f"{n} ({why})" for n, why in failed[:5]) + (" ..." if len(failed) > 5 else ""))
        self.made, self.failed = made, failed
