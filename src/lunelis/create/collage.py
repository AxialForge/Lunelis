"""
Collages: several photos on one canvas.

- **Layout:** a template's cells (fractions of the canvas), or the cells
  you place yourself (`Cell.rect`) - the free layout. Templates take 2-9
  photos.
- **Canvas:** an aspect (1:1, 4:5, 9:16, 16:9, 3:2, 2:3) and a long edge;
  spacing between cells and a border round them, as % of the canvas's
  short side (so a preview and the full-size file look the same);
  rounded corners; a background colour.
- **Each cell** shows its photo cover-fitted (filled, cropped - never
  stretched), with its own zoom (1-4x) and pan (the point of the photo at
  the cell's centre, 0-1 each way). Swapping two cells swaps their photos
  and keeps the cells.
- `render(...)` takes a `source(file_id, edge)` so the page's live preview
  can use thumbnails while the saved file uses the full photos with their
  edits (engine.photo).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable

from PIL import Image, ImageDraw

ASPECTS = {"1:1": (1, 1), "4:5": (4, 5), "9:16": (9, 16), "16:9": (16, 9), "3:2": (3, 2), "2:3": (2, 3)}

# name -> cells (x, y, w, h) as fractions of the area inside the border
TEMPLATES: dict[str, list[tuple[float, float, float, float]]] = {
    "2 side by side": [(0, 0, .5, 1), (.5, 0, .5, 1)],
    "2 stacked": [(0, 0, 1, .5), (0, .5, 1, .5)],
    "3 in a row": [(0, 0, 1 / 3, 1), (1 / 3, 0, 1 / 3, 1), (2 / 3, 0, 1 / 3, 1)],
    "1 big + 2": [(0, 0, 2 / 3, 1), (2 / 3, 0, 1 / 3, .5), (2 / 3, .5, 1 / 3, .5)],
    "1 on top + 2": [(0, 0, 1, .6), (0, .6, .5, .4), (.5, .6, .5, .4)],
    "2 x 2": [(0, 0, .5, .5), (.5, 0, .5, .5), (0, .5, .5, .5), (.5, .5, .5, .5)],
    "1 big + 3": [(0, 0, .65, 1), (.65, 0, .35, 1 / 3), (.65, 1 / 3, .35, 1 / 3), (.65, 2 / 3, .35, 1 / 3)],
    "1 on top + 3": [(0, 0, 1, .6), (0, .6, 1 / 3, .4), (1 / 3, .6, 1 / 3, .4), (2 / 3, .6, 1 / 3, .4)],
    "2 x 3": [(c / 2, r / 3, .5, 1 / 3) for r in range(3) for c in range(2)],
    "3 x 2": [(c / 3, r / 2, 1 / 3, .5) for r in range(2) for c in range(3)],
    "3 x 3": [(c / 3, r / 3, 1 / 3, 1 / 3) for r in range(3) for c in range(3)],
}


@dataclass(frozen=True)
class Cell:
    file_id: int | None = None
    zoom: float = 1.0                       # 1-4
    pan: tuple[float, float] = (0.5, 0.5)   # the point of the photo at the cell's centre
    rect: tuple[float, float, float, float] | None = None   # free layout: overrides the template's cell


@dataclass
class CollageOptions:
    template: str = "2 x 2"
    aspect: str = "1:1"
    long_edge: int = 2048
    spacing: float = 1.5                    # % of the short side, between cells
    border: float = 3.0                     # % of the short side, round the edge
    radius: float = 0.0                     # % of the short side, rounded corners
    background: str = "#ffffff"
    cells: list[Cell] = field(default_factory=list)

    def check(self) -> None:
        if self.template not in TEMPLATES and not all(c.rect for c in self.cells):
            raise ValueError(f"unknown layout {self.template!r}")
        if self.aspect not in ASPECTS:
            raise ValueError(f"aspect must be one of {tuple(ASPECTS)}")
        if not 64 <= self.long_edge <= 12000:
            raise ValueError("the size must be 64-12000 px")
        for v, what in ((self.spacing, "spacing"), (self.border, "border"), (self.radius, "corners")):
            if not 0 <= v <= 20:
                raise ValueError(f"{what} must be 0-20 %")
        if not any(c.file_id for c in self.cells):
            raise ValueError("put at least one photo in the collage")


def canvas_size(opts: CollageOptions, long_edge: int | None = None) -> tuple[int, int]:
    a, b = ASPECTS[opts.aspect]
    edge = long_edge or opts.long_edge
    return (edge, max(1, round(edge * b / a))) if a >= b else (max(1, round(edge * a / b)), edge)


def cell_count(template: str) -> int:
    return len(TEMPLATES[template])


def fit_cells(opts: CollageOptions) -> list[Cell]:
    """As many cells as the template has: extra photos dropped, missing ones empty."""
    n = len(TEMPLATES.get(opts.template, opts.cells))
    cells = list(opts.cells[:n])
    return cells + [Cell() for _ in range(n - len(cells))]


def cell_boxes(opts: CollageOptions, size: tuple[int, int]) -> list[tuple[int, int, int, int]]:
    """Each cell's pixel box (left, top, right, bottom) on a canvas of `size`."""
    w, h = size
    short = min(w, h)
    border = round(short * opts.border / 100)
    gap = short * opts.spacing / 100
    inner_w, inner_h = w - 2 * border, h - 2 * border
    rects = [c.rect for c in opts.cells] if opts.cells and all(c.rect for c in opts.cells) \
        else TEMPLATES[opts.template]
    boxes = []
    for (x, y, cw, ch) in rects:
        # Half a gap off every inside edge: neighbours end up one gap apart and
        # the outer edges stay flush with the border.
        left = border + x * inner_w + (gap / 2 if x > 1e-6 else 0)
        top = border + y * inner_h + (gap / 2 if y > 1e-6 else 0)
        right = border + (x + cw) * inner_w - (gap / 2 if x + cw < 1 - 1e-6 else 0)
        bottom = border + (y + ch) * inner_h - (gap / 2 if y + ch < 1 - 1e-6 else 0)
        boxes.append((round(left), round(top), max(round(left) + 1, round(right)), max(round(top) + 1, round(bottom))))
    return boxes


def cover(img: Image.Image, box_size: tuple[int, int], zoom: float = 1.0,
          pan: tuple[float, float] = (0.5, 0.5)) -> Image.Image:
    """The part of `img` that fills `box_size` at this zoom and pan."""
    bw, bh = box_size
    iw, ih = img.size
    scale = max(bw / iw, bh / ih) * max(1.0, min(4.0, zoom))
    vw, vh = bw / scale, bh / scale                       # the window on the photo, in its pixels
    cx = min(max(pan[0] * iw, vw / 2), iw - vw / 2)
    cy = min(max(pan[1] * ih, vh / 2), ih - vh / 2)
    crop = (cx - vw / 2, cy - vh / 2, cx + vw / 2, cy + vh / 2)
    return img.resize((bw, bh), Image.Resampling.LANCZOS, box=crop)


def render(opts: CollageOptions, source: Callable[[int, int], Image.Image], long_edge: int | None = None,
           progress: Callable[[int, int], None] | None = None) -> Image.Image:
    """The collage as an RGB image (at `long_edge`, default the options')."""
    size = canvas_size(opts, long_edge)
    canvas = Image.new("RGB", size, opts.background)
    cells = fit_cells(opts) if not (opts.cells and all(c.rect for c in opts.cells)) else opts.cells
    boxes = cell_boxes(replace(opts, cells=cells), size)
    radius = round(min(size) * opts.radius / 100)
    for i, (cell, box) in enumerate(zip(cells, boxes)):
        if progress:
            progress(i, len(cells))
        if not cell.file_id:
            continue
        bw, bh = box[2] - box[0], box[3] - box[1]
        need = round(max(bw, bh) * max(1.0, cell.zoom) * 1.5)     # enough pixels after the crop
        piece = cover(source(cell.file_id, need), (bw, bh), cell.zoom, cell.pan).convert("RGB")
        if radius:
            mask = Image.new("L", (bw, bh), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, bw - 1, bh - 1), radius, fill=255)
            canvas.paste(piece, box[:2], mask)
        else:
            canvas.paste(piece, box[:2])
    return canvas


def swap(opts: CollageOptions, a: int, b: int) -> CollageOptions:
    """Swap the photos (with their zoom and pan) of cells a and b; the cells stay."""
    cells = fit_cells(opts) if opts.template in TEMPLATES else list(opts.cells)
    ca, cb = cells[a], cells[b]
    cells[a] = replace(cb, rect=ca.rect)
    cells[b] = replace(ca, rect=cb.rect)
    return replace(opts, cells=cells)
