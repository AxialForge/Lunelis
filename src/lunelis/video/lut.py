"""
3D colour look-up tables (.cube) for previewing log footage.

- `parse_cube(text)` reads the common Adobe / Resolve .cube format: a
  LUT_3D_SIZE, optional DOMAIN_MIN / DOMAIN_MAX, then size^3 lines of
  "r g b" with red changing fastest. 1D LUTs aren't supported.
- `Lut3D.apply(rgb)` maps an 8-bit RGB array through the table with
  trilinear interpolation (thumbnails, stills).
- `Lut3D.fast()` bakes a 64^3 8-bit table for live video: one lookup per
  pixel, about 15 ms for a 720p frame.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


class LutError(ValueError):
    pass


@dataclass
class Lut3D:
    table: np.ndarray                      # (n, n, n, 3) float32, indexed [r, g, b], output 0-1
    title: str = ""
    domain_min: tuple[float, float, float] = (0.0, 0.0, 0.0)
    domain_max: tuple[float, float, float] = (1.0, 1.0, 1.0)
    _fast: np.ndarray | None = field(default=None, repr=False)

    @property
    def size(self) -> int:
        return self.table.shape[0]

    def apply_float(self, rgb: np.ndarray) -> np.ndarray:
        """rgb: (..., 3) floats in the LUT's domain -> (..., 3) floats 0-1."""
        lo = np.asarray(self.domain_min, np.float32)
        hi = np.asarray(self.domain_max, np.float32)
        n = self.size
        x = np.clip((rgb.astype(np.float32) - lo) / (hi - lo), 0.0, 1.0) * (n - 1)
        i0 = np.minimum(np.floor(x).astype(np.int32), n - 2)
        f = x - i0
        r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
        fr, fg, fb = f[..., 0:1], f[..., 1:2], f[..., 2:3]
        t = self.table
        c000, c100 = t[r0, g0, b0], t[r0 + 1, g0, b0]
        c010, c110 = t[r0, g0 + 1, b0], t[r0 + 1, g0 + 1, b0]
        c001, c101 = t[r0, g0, b0 + 1], t[r0 + 1, g0, b0 + 1]
        c011, c111 = t[r0, g0 + 1, b0 + 1], t[r0 + 1, g0 + 1, b0 + 1]
        c00 = c000 + (c100 - c000) * fr
        c10 = c010 + (c110 - c010) * fr
        c01 = c001 + (c101 - c001) * fr
        c11 = c011 + (c111 - c011) * fr
        c0 = c00 + (c10 - c00) * fg
        c1 = c01 + (c11 - c01) * fg
        return np.clip(c0 + (c1 - c0) * fb, 0.0, 1.0)

    def apply(self, rgb8: np.ndarray) -> np.ndarray:
        """(h, w, 3) uint8 -> uint8, trilinear."""
        out = self.apply_float(rgb8.astype(np.float32) / 255.0)
        return (out * 255.0 + 0.5).astype(np.uint8)

    def apply_image(self, img):
        """A PIL image through the LUT (RGB out)."""
        from PIL import Image
        return Image.fromarray(self.apply(np.asarray(img.convert("RGB"))), "RGB")

    def fast(self) -> np.ndarray:
        """A (64*64*64, 3) uint8 table for `apply_fast`."""
        if self._fast is None:
            v = (np.arange(64, dtype=np.float32) * 4 + 2) / 255.0      # the middle of each 4-level bucket
            r, g, b = np.meshgrid(v, v, v, indexing="ij")
            grid = np.stack([r, g, b], axis=-1).reshape(-1, 3)
            self._fast = (self.apply_float(grid) * 255.0 + 0.5).astype(np.uint8)
        return self._fast

    def apply_fast(self, rgb8: np.ndarray) -> np.ndarray:
        """(h, w, 3) uint8 -> uint8 by one lookup per pixel (6 bits a channel in)."""
        q = (rgb8 >> 2).astype(np.int32)
        return self.fast()[(q[..., 0] << 12) | (q[..., 1] << 6) | q[..., 2]]


def identity(n: int = 17) -> Lut3D:
    v = np.linspace(0.0, 1.0, n, dtype=np.float32)
    r, g, b = np.meshgrid(v, v, v, indexing="ij")
    return Lut3D(np.stack([r, g, b], axis=-1), "Identity")


def from_function(fn, n: int = 33, title: str = "") -> Lut3D:
    """A LUT sampling fn((..., 3) floats 0-1) -> (..., 3) floats 0-1."""
    v = np.linspace(0.0, 1.0, n, dtype=np.float32)
    r, g, b = np.meshgrid(v, v, v, indexing="ij")
    out = np.clip(fn(np.stack([r, g, b], axis=-1)), 0.0, 1.0).astype(np.float32)
    return Lut3D(out, title)


def parse_cube(text: str) -> Lut3D:
    title, size = "", None
    lo, hi = (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)
    rows: list[list[float]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key = line.split(None, 1)[0].upper()
        if key == "TITLE":
            title = line[5:].strip().strip('"')
        elif key == "LUT_3D_SIZE":
            try:
                size = int(line.split()[1])
            except (IndexError, ValueError):
                raise LutError("LUT_3D_SIZE needs a number.") from None
            if not 2 <= size <= 129:
                raise LutError("LUT_3D_SIZE must be 2-129.")
        elif key == "LUT_1D_SIZE":
            raise LutError("1D LUTs aren't supported - use a 3D .cube LUT.")
        elif key in ("DOMAIN_MIN", "DOMAIN_MAX"):
            vals = tuple(float(x) for x in line.split()[1:4])
            if len(vals) != 3 or not all(np.isfinite(vals)):
                raise LutError(f"{key} needs three numbers.")
            lo, hi = (vals, hi) if key == "DOMAIN_MIN" else (lo, vals)
        elif key[0].isdigit() or key[0] in "-.":
            parts = line.split()
            if len(parts) < 3:
                raise LutError(f"Not a LUT line: {line!r}")
            try:
                rows.append([float(parts[0]), float(parts[1]), float(parts[2])])
            except ValueError:
                raise LutError(f"Not a LUT line: {line!r}") from None
        # other keywords (LUT_3D_INPUT_RANGE, comments in other tools) are ignored
    if not size or size < 2:
        raise LutError("No LUT_3D_SIZE in this file.")
    if len(rows) != size ** 3:
        raise LutError(f"Expected {size ** 3} colour lines for a size-{size} LUT, found {len(rows)}.")
    if any(h <= l for l, h in zip(lo, hi)):
        raise LutError("DOMAIN_MAX must be above DOMAIN_MIN.")
    # Red changes fastest: the flat list is ordered [b][g][r].
    table = np.asarray(rows, np.float32).reshape(size, size, size, 3).transpose(2, 1, 0, 3)
    if not np.isfinite(table).all():
        raise LutError("The LUT has values that aren't numbers.")
    return Lut3D(np.ascontiguousarray(table), title, lo, hi)


def load_cube(path: str | Path) -> Lut3D:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise LutError(f"Couldn't read {path}: {e}") from e
    lut = parse_cube(text)
    lut.title = lut.title or Path(path).stem
    return lut
