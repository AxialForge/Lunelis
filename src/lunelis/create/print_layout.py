"""
Print layouts: photos at real print sizes, ready for a printer or a lab.

- **Print sizes:** wallet (2.5 x 3.5 in), 4 x 6, 5 x 7, 8 x 10.
- **On sheets** (Letter or A4): as many prints as fit, at 300 dots per inch,
  with thin cut marks at the corners - a PDF, every sheet in it.
- **For a lab** ("one print per file"): each print its own JPEG at exactly
  its size (4 x 6 = 1800 x 1200), to upload to a printing service.
- Each photo is turned to suit the print (a tall photo on a tall print) and
  either fills it (cropped, like a lab's default) or fits inside it whole.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from lunelis.create import engine

DPI = 300
PRINTS = {"wallet": (2.5, 3.5), "4x6": (4.0, 6.0), "5x7": (5.0, 7.0), "8x10": (8.0, 10.0)}
PAPERS = {"letter": (8.5, 11.0), "a4": (8.27, 11.69), "lab": None}
MARGIN = 0.25                   # inches round a sheet
GUTTER = 0.125                  # between prints (cut marks sit in it)


@dataclass
class PrintOptions:
    size: str = "4x6"
    paper: str = "letter"         # letter | a4 | lab (a file per print)
    fill: bool = True
    copies: int = 1
    cut_marks: bool = True

    def check(self, n: int) -> None:
        if self.size not in PRINTS:
            raise ValueError(f"print size must be one of {tuple(PRINTS)}")
        if self.paper not in PAPERS:
            raise ValueError(f"paper must be one of {tuple(PAPERS)}")
        if not 1 <= self.copies <= 20:
            raise ValueError("1-20 copies")
        if n < 1:
            raise ValueError("pick at least one photo")


def px(inches: float) -> int:
    return round(inches * DPI)


def grid(opts: PrintOptions) -> tuple[int, int, bool]:
    """(columns, rows, prints turned sideways) - the most prints per sheet."""
    pw, ph = PAPERS[opts.paper]
    w, h = PRINTS[opts.size]
    best = (0, 0, False)
    for turned in (False, True):
        cw, ch = (h, w) if turned else (w, h)
        cols = int((pw - 2 * MARGIN + GUTTER) // (cw + GUTTER))
        rows = int((ph - 2 * MARGIN + GUTTER) // (ch + GUTTER))
        if cols * rows > best[0] * best[1]:
            best = (cols, rows, turned)
    if best[0] * best[1] == 0:
        raise ValueError(f"a {opts.size} print doesn't fit on {opts.paper} paper")
    return best


def print_image(img: Image.Image, size_in: tuple[float, float], fill: bool) -> Image.Image:
    """The photo at this print size (turned to match its shape)."""
    w, h = px(size_in[0]), px(size_in[1])
    if (img.width > img.height) != (w > h) and img.width != img.height:
        w, h = h, w
    if fill:
        return ImageOps.fit(img, (w, h), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (w, h), "white")
    inner = ImageOps.contain(img, (w, h), Image.Resampling.LANCZOS)
    canvas.paste(inner, ((w - inner.width) // 2, (h - inner.height) // 2))
    return canvas


def _marks(d: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int) -> None:
    m = px(GUTTER) // 2
    for cx, cy in ((x, y), (x + w, y), (x, y + h), (x + w, y + h)):
        dx = -1 if cx == x else 1
        dy = -1 if cy == y else 1
        d.line((cx, cy + dy * 2, cx, cy + dy * m), fill=(120, 120, 120), width=2)
        d.line((cx + dx * 2, cy, cx + dx * m, cy), fill=(120, 120, 120), width=2)


def sheets(conn: sqlite3.Connection, file_ids: list[int], opts: PrintOptions, progress=None, cancelled=None):
    from lunelis.create.animation import Cancelled
    cols, rows, turned = grid(opts)
    pw, ph = (px(v) for v in PAPERS[opts.paper])
    w_in, h_in = PRINTS[opts.size]
    cw, ch = (px(h_in), px(w_in)) if turned else (px(w_in), px(h_in))
    used_w = cols * cw + (cols - 1) * px(GUTTER)
    used_h = rows * ch + (rows - 1) * px(GUTTER)
    x0, y0 = (pw - used_w) // 2, (ph - used_h) // 2
    jobs = [fid for fid in file_ids for _ in range(opts.copies)]
    per = cols * rows
    pages = []
    for p in range(0, len(jobs), per):
        page = Image.new("RGB", (pw, ph), "white")
        d = ImageDraw.Draw(page)
        for k, fid in enumerate(jobs[p:p + per]):
            if cancelled and cancelled():
                raise Cancelled()
            r, c = divmod(k, cols)
            x, y = x0 + c * (cw + px(GUTTER)), y0 + r * (ch + px(GUTTER))
            img = print_image(engine.photo(conn, fid, max(cw, ch)), (cw / DPI, ch / DPI), opts.fill)
            if img.size != (cw, ch):                    # turned the other way than the cell: rotate to fit
                img = img.rotate(90, expand=True)
            page.paste(img, (x, y))
            if opts.cut_marks:
                _marks(d, x, y, cw, ch)
            if progress:
                progress(p + k + 1, len(jobs))
        pages.append(page)
    return pages


def make(conn: sqlite3.Connection, file_ids: list[int], opts: PrintOptions, folder: str | Path, name: str,
         progress=None, cancelled=None) -> list[str]:
    from lunelis.create.animation import Cancelled
    from lunelis.edit.export import _BAD, free_path
    opts.check(len(file_ids))
    os.makedirs(folder, exist_ok=True)
    clean = _BAD.sub("_", name).strip(" .") or "Prints"
    if opts.paper == "lab":
        out = []
        for i, fid in enumerate(file_ids):
            if cancelled and cancelled():
                raise Cancelled()
            img = print_image(engine.photo(conn, fid, px(max(PRINTS[opts.size]))), PRINTS[opts.size], opts.fill)
            for c in range(opts.copies):
                dest = free_path(str(folder), f"{clean} {i + 1:03d}{f'-{c + 1}' if opts.copies > 1 else ''}.jpg")
                img.save(dest, "JPEG", quality=95, dpi=(DPI, DPI), subsampling=0)
                out.append(dest)
            if progress:
                progress(i + 1, len(file_ids))
        return out
    pages = sheets(conn, file_ids, opts, progress, cancelled)
    dest = free_path(str(folder), clean + ".pdf")
    pages[0].save(dest, "PDF", save_all=True, append_images=pages[1:], resolution=DPI)
    return [dest]
