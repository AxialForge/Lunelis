"""
Decoding a photo for editing, and the cached look of an edited photo.

Editing works on a real decode: a RAW goes through rawpy (camera white
balance, 16-bit) - not its embedded JPEG - and a JPEG/HEIC/TIFF at its own
pixels, not the small MPF preview the grid uses.

When an edit is saved, two things are rendered once and cached:

- the proxy: a 2560 px JPEG of the edited photo (cache/edits/), which the
  photo view shows instead of decoding + re-editing every time;
- the grid thumbnail: overwritten with the edited look (same path as any
  thumbnail, so the grid needs nothing special).

Both are caches, never the source of truth (the stack in the catalog and
the sidecar is): safe to delete, made again from the stack.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from lunelis.edit import pipeline
from lunelis.edit.stack import Stack

PROXY_EDGE = 2560
PROXY_QUALITY = 90


def proxy_path(edit_cache: Path, file_id: int) -> Path:
    from lunelis.raw.thumbnails import cache_rel_path
    return edit_cache / cache_rel_path(file_id)


def _resize(a: np.ndarray, edge: int) -> np.ndarray:
    h, w = a.shape[:2]
    if max(h, w) <= edge:
        return a
    k = edge / max(h, w)
    size = (max(1, round(w * k)), max(1, round(h * k)))
    return np.stack([np.asarray(Image.fromarray(np.ascontiguousarray(a[..., c]), "F")
                                .resize(size, Image.Resampling.LANCZOS)) for c in range(3)], axis=-1)


def load_source(path: str, is_raw: bool, edge: int | None = None) -> np.ndarray:
    """The photo as a float32 sRGB array, upright, at most `edge` px on its
    long side (None = full size, for export)."""
    if is_raw:
        import rawpy
        with rawpy.imread(path) as raw:
            s = raw.sizes
            half = edge is not None and edge * 2 <= max(s.width, s.height)    # half-size decode is plenty
            rgb = raw.postprocess(use_camera_wb=True, half_size=half, output_bps=16)
        a = rgb.astype(np.float32) / 65535.0                 # rawpy applies the RAW's own rotation
        return _resize(a, edge) if edge else a
    from lunelis.raw.thumbnails import _to_srgb
    with Image.open(path) as im:
        if im.format in ("JPEG", "MPO") and edge:
            im.draft("RGB", (edge, edge))
        im.load()
        img = ImageOps.exif_transpose(im)
        img = _to_srgb(img)
    if edge:
        img.thumbnail((edge, edge), Image.Resampling.LANCZOS)
    return pipeline.to_array(img)


def render_outputs(path: str, is_raw: bool, file_id: int, stack: Stack, filter_params: dict | None,
                   thumb_cache: Path, edit_cache: Path, base: np.ndarray | None = None) -> str:
    """Render the proxy and the grid thumbnail of an edited photo. `base` is
    an already-decoded PROXY_EDGE source (the Develop view has one). Returns
    the thumbnail's cache path."""
    from lunelis.raw.thumbnails import THUMB_EDGE, write_thumbnail
    if base is None:
        base = load_source(path, is_raw, PROXY_EDGE)
    from lunelis.edit import ai, lens
    info = lens.info_for_id(file_id) if stack.lens else None
    img = pipeline.to_image(pipeline.apply(base, stack, filter_params, ai.maps_for(file_id, stack, base), info))
    dest = proxy_path(edit_cache, file_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    img.save(tmp, "JPEG", quality=PROXY_QUALITY)
    os.replace(tmp, dest)
    thumb = img.copy()
    thumb.thumbnail((THUMB_EDGE, THUMB_EDGE), Image.Resampling.LANCZOS)
    return write_thumbnail(thumb_cache, file_id, thumb)


def clear_outputs(path: str, file_id: int, orientation: int | None, thumb_cache: Path, edit_cache: Path) -> str | None:
    """Back to the original: drop the proxy, re-make the plain thumbnail."""
    from lunelis.raw.thumbnails import render, write_thumbnail
    try:
        proxy_path(edit_cache, file_id).unlink()
    except FileNotFoundError:
        pass
    try:
        return write_thumbnail(thumb_cache, file_id, render(path, orientation))
    except Exception:
        return None


def edited_thumbnail(path: str, is_raw: bool, file_id: int, stack: Stack, filter_params: dict | None,
                     thumb_cache: Path, edit_cache: Path) -> str:
    """For the thumbnail pass: an edited photo's thumbnail comes from its
    proxy when that's still there, else from a fresh render."""
    from lunelis.raw.thumbnails import THUMB_EDGE, write_thumbnail
    proxy = proxy_path(edit_cache, file_id)
    if proxy.exists():
        with Image.open(proxy) as im:
            im.draft("RGB", (THUMB_EDGE, THUMB_EDGE))
            img = im.convert("RGB")
        img.thumbnail((THUMB_EDGE, THUMB_EDGE), Image.Resampling.LANCZOS)
        return write_thumbnail(thumb_cache, file_id, img)
    return render_outputs(path, is_raw, file_id, stack, filter_params, thumb_cache, edit_cache)
