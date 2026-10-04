"""
Contact sheets: photos in a grid on pages, with captions - to print, or to
send someone to choose from.

- **Pages:** Letter or A4, portrait or landscape, at 150 dots per inch
  (sharp on paper, a small file). As many pages as the photos need.
- **Grid:** 2-10 columns; the rows follow from the page shape. Each photo
  fits its cell (never cropped - a contact sheet shows the whole frame).
- **Captions:** any of file name, capture date, stars; a title and page
  numbers at the top.
- **PDF** (one file, every page) or **PNG** (a file per page).

The pictures come from the thumbnails (which carry your edits), so even a
thousand-photo sheet is quick.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw

from lunelis.create import engine

DPI = 150
PAGES = {"letter": (8.5, 11.0), "a4": (8.27, 11.69)}


@dataclass
class SheetOptions:
    page: str = "letter"
    landscape: bool = False
    columns: int = 5
    title: str = ""
    names: bool = True
    dates: bool = True
    stars: bool = False
    format: str = "pdf"             # pdf | png

    def check(self, n: int) -> None:
        if self.page not in PAGES:
            raise ValueError(f"page must be one of {tuple(PAGES)}")
        if not 2 <= self.columns <= 10:
            raise ValueError("2-10 columns")
        if self.format not in ("pdf", "png"):
            raise ValueError("format must be pdf or png")
        if n < 1:
            raise ValueError("pick at least one photo")

    def page_px(self) -> tuple[int, int]:
        w, h = PAGES[self.page]
        if self.landscape:
            w, h = h, w
        return round(w * DPI), round(h * DPI)


def layout(opts: SheetOptions) -> tuple[int, int, int, int, int]:
    """(cell width, cell height incl. caption, rows per page, margin, top band)."""
    W, H = opts.page_px()
    margin = round(0.4 * DPI)
    top = round(0.45 * DPI)
    gap = round(0.08 * DPI)
    cell_w = (W - 2 * margin - (opts.columns - 1) * gap) // opts.columns
    lines = sum((opts.names, opts.dates, opts.stars))
    cap = lines * round(0.14 * DPI)
    cell_h = cell_w + cap
    rows = max(1, (H - 2 * margin - top + gap) // (cell_h + gap))
    return cell_w, cell_h, rows, margin, top


def pages_needed(opts: SheetOptions, n: int) -> int:
    _, _, rows, _, _ = layout(opts)
    per = rows * opts.columns
    return (n + per - 1) // per


def _captions(conn, fid: int, opts: SheetOptions) -> list[str]:
    row = conn.execute("SELECT f.filename, e.captured_at, COALESCE(rt.stars, 0) FROM files f"
                       " LEFT JOIN exif e ON e.file_id = f.id LEFT JOIN ratings rt ON rt.file_id = f.id"
                       " WHERE f.id = ?", (fid,)).fetchone()
    if row is None:
        return []
    name, taken, stars = row
    out = []
    if opts.names:
        out.append(name)
    if opts.dates:
        out.append((taken or "")[:16].replace("T", " ") or "no date")
    if opts.stars:
        out.append("★" * stars if stars else "-")
    return out


def render_pages(conn: sqlite3.Connection, file_ids: list[int], opts: SheetOptions,
                 progress: Callable[[int, int], None] | None = None,
                 cancelled: Callable[[], bool] | None = None) -> list[Image.Image]:
    from lunelis.create.batch import _font
    opts.check(len(file_ids))
    W, H = opts.page_px()
    cell_w, cell_h, rows, margin, top = layout(opts)
    gap = round(0.08 * DPI)
    per = rows * opts.columns
    total_pages = pages_needed(opts, len(file_ids))
    small, title_font = _font(round(0.085 * DPI)), _font(round(0.16 * DPI))
    pages = []
    for p in range(total_pages):
        if cancelled and cancelled():
            from lunelis.create.animation import Cancelled
            raise Cancelled()
        page = Image.new("RGB", (W, H), "white")
        d = ImageDraw.Draw(page)
        d.text((margin, margin - round(0.1 * DPI)), opts.title or "Contact sheet", font=title_font, fill=(20, 20, 20))
        d.text((W - margin, margin - round(0.05 * DPI)), f"page {p + 1} of {total_pages}", font=small,
               fill=(110, 110, 110), anchor="ra")
        for k, fid in enumerate(file_ids[p * per:(p + 1) * per]):
            r, c = divmod(k, opts.columns)
            x = margin + c * (cell_w + gap)
            y = margin + top + r * (cell_h + gap)
            try:
                img = engine.thumb(conn, fid, cell_w)
            except OSError:
                img = Image.new("RGB", (cell_w, round(cell_w * 2 / 3)), (200, 200, 200))
            img.thumbnail((cell_w, cell_w), Image.Resampling.LANCZOS)
            page.paste(img, (x + (cell_w - img.width) // 2, y + (cell_w - img.height) // 2))
            ty = y + cell_w + 2
            for line in _captions(conn, fid, opts):
                d.text((x + cell_w // 2, ty), line, font=small, fill=(40, 40, 40), anchor="ma")
                ty += round(0.14 * DPI)
            if progress:
                progress(p * per + k + 1, len(file_ids))
        pages.append(page)
    return pages


def make(conn: sqlite3.Connection, file_ids: list[int], opts: SheetOptions, folder: str | Path, name: str,
         progress=None, cancelled=None) -> list[str]:
    from lunelis.edit.export import _BAD, free_path
    pages = render_pages(conn, file_ids, opts, progress, cancelled)
    os.makedirs(folder, exist_ok=True)
    clean = _BAD.sub("_", name).strip(" .") or "Contact sheet"
    if opts.format == "pdf":
        dest = free_path(str(folder), clean + ".pdf")
        pages[0].save(dest, "PDF", save_all=True, append_images=pages[1:], resolution=DPI)
        return [dest]
    out = []
    for i, page in enumerate(pages, 1):
        dest = free_path(str(folder), f"{clean}{'' if len(pages) == 1 else f' - page {i}'}.png")
        page.save(dest, "PNG", dpi=(DPI, DPI), optimize=True)
        out.append(dest)
    return out
