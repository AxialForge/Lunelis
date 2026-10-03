"""
Exporting: photos, with their edits, as new files in a folder you choose.

The originals are only read. Each export is a new JPEG / TIFF / PNG:

- size: full size, or fitted to a long edge (a smaller export decodes a
  RAW at half size when that's still big enough);
- metadata: all of it (camera, lens, exposure, date, GPS - rebuilt from
  the catalog, so RAWs export theirs too), everything but the location, or
  none. Orientation is always 1: the pixels are already upright;
- names: a pattern of {name} (the original's name), {date} (capture date),
  {n} (001, 002...) and plain text. An existing file is never overwritten -
  the new one gets " (2)", " (3)"...;
- an sRGB colour profile is embedded (that's what the pixels are).

Full-size exports go through pipeline.apply_tiled, in strips.
"""
from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction

from PIL import Image, ImageCms

from lunelis.edit import pipeline, render, store

FORMATS = {"jpeg": ".jpg", "tiff": ".tif", "png": ".png"}
METADATA = ("all", "no_location", "none")


@dataclass
class ExportOptions:
    folder: str
    format: str = "jpeg"                 # jpeg | tiff | png
    long_edge: int | None = None         # None = full size
    quality: int = 92                    # JPEG only
    metadata: str = "all"                # all | no_location | none
    pattern: str = "{name}"

    def check(self) -> None:
        if self.format not in FORMATS:
            raise ValueError(f"format must be one of {tuple(FORMATS)}")
        if self.metadata not in METADATA:
            raise ValueError(f"metadata must be one of {METADATA}")
        if self.long_edge is not None and not 64 <= self.long_edge <= 30000:
            raise ValueError("the long edge must be 64-30000 px")
        if not 1 <= self.quality <= 100:
            raise ValueError("quality must be 1-100")
        if not self.pattern.strip():
            raise ValueError("the name pattern can't be empty")


# --- names ----------------------------------------------------------------------------

_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def file_name(pattern: str, original: str, captured_at: str | None, n: int, ext: str) -> str:
    stem = os.path.splitext(original)[0]
    date = (captured_at or "")[:10] or "undated"
    name = pattern.replace("{name}", stem).replace("{date}", date).replace("{n}", f"{n:03d}")
    name = _BAD.sub("_", name).strip(" .") or stem
    return name + ext


def free_path(folder: str, name: str) -> str:
    """`name` in `folder`, or 'name (2).ext' etc. - never an existing file."""
    path = os.path.join(folder, name)
    base, ext = os.path.splitext(path)
    k = 2
    while os.path.exists(path):
        path = f"{base} ({k}){ext}"
        k += 1
    return path


# --- metadata -------------------------------------------------------------------------------

def _rational(v: float, limit: int = 10000) -> tuple[int, int]:
    f = Fraction(v).limit_denominator(limit)
    return f.numerator, f.denominator


def _dms(v: float) -> tuple:
    v = abs(v)
    d = int(v)
    m = int((v - d) * 60)
    s = (v - d - m / 60) * 3600
    return ((d, 1), (m, 1), _rational(s, 1000))


def exif_bytes(conn: sqlite3.Connection, file_id: int, metadata: str, size: tuple[int, int]) -> bytes | None:
    """EXIF for an export, built from the catalog."""
    if metadata == "none":
        return None
    import piexif
    row = conn.execute("SELECT captured_at, camera_make, camera_model, lens, focal_length_mm, aperture,"
                       " shutter_speed, iso, gps_lat, gps_lon, exposure_comp, captured_offset"
                       " FROM exif WHERE file_id = ?", (file_id,)).fetchone()
    zeroth = {piexif.ImageIFD.Software: b"Lunelis", piexif.ImageIFD.Orientation: 1}
    ex: dict = {piexif.ExifIFD.PixelXDimension: size[0], piexif.ExifIFD.PixelYDimension: size[1]}
    gps: dict = {}
    if row:
        taken, make, model, lens, focal, aperture, shutter, iso, lat, lon, comp, offset = row
        if make:
            zeroth[piexif.ImageIFD.Make] = make.encode("utf-8", "replace")
        if model:
            zeroth[piexif.ImageIFD.Model] = model.encode("utf-8", "replace")
        if taken:
            try:
                dt = datetime.fromisoformat(taken[:19]).strftime("%Y:%m:%d %H:%M:%S").encode()
                ex[piexif.ExifIFD.DateTimeOriginal] = dt
                zeroth[piexif.ImageIFD.DateTime] = dt
                if offset:
                    ex[piexif.ExifIFD.OffsetTimeOriginal] = offset.encode()
            except ValueError:
                pass
        if lens:
            ex[piexif.ExifIFD.LensModel] = lens.encode("utf-8", "replace")
        if focal:
            ex[piexif.ExifIFD.FocalLength] = _rational(focal)
        if aperture:
            ex[piexif.ExifIFD.FNumber] = _rational(aperture)
        if shutter:
            try:
                ex[piexif.ExifIFD.ExposureTime] = _rational(float(Fraction(shutter.rstrip("s"))))
            except (ValueError, ZeroDivisionError):
                pass
        if iso:
            ex[piexif.ExifIFD.ISOSpeedRatings] = int(iso)
        if comp is not None:
            f = Fraction(comp).limit_denominator(100)
            ex[piexif.ExifIFD.ExposureBiasValue] = (f.numerator, f.denominator)
        if metadata == "all" and lat is not None and lon is not None:
            gps = {piexif.GPSIFD.GPSLatitudeRef: b"N" if lat >= 0 else b"S", piexif.GPSIFD.GPSLatitude: _dms(lat),
                   piexif.GPSIFD.GPSLongitudeRef: b"E" if lon >= 0 else b"W", piexif.GPSIFD.GPSLongitude: _dms(lon)}
    return piexif.dump({"0th": zeroth, "Exif": ex, "GPS": gps, "1st": {}, "thumbnail": None})


_SRGB = None


def _srgb_icc() -> bytes:
    global _SRGB
    if _SRGB is None:
        _SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    return _SRGB


# --- one photo ----------------------------------------------------------------------------------

def export_one(conn: sqlite3.Connection, file_id: int, opts: ExportOptions, n: int = 1) -> str:
    """Export one photo; returns the new file's path."""
    row = conn.execute("SELECT r.path, f.rel_path, f.filename, f.is_raw, e.captured_at FROM files f"
                       " JOIN roots r ON r.id = f.root_id LEFT JOIN exif e ON e.file_id = f.id"
                       " WHERE f.id = ?", (file_id,)).fetchone()
    if row is None:
        raise ValueError("not in the catalog")
    root, rel, filename, is_raw, taken = row
    src_path = os.path.join(root, *rel.split("/"))
    stack = store.get(conn, file_id)
    g = stack.geometry
    # A crop or straighten throws pixels away: decode at full size then.
    whole = g.crop == (0.0, 0.0, 1.0, 1.0) and not g.angle
    edge = opts.long_edge if (opts.long_edge and whole) else None
    src = render.load_source(src_path, bool(is_raw), edge)
    from lunelis.edit import ai, lens
    out = pipeline.apply_tiled(src, stack, store.filter_params(conn, stack.filter),
                               ai_maps=ai.maps_for(file_id, stack, src),
                               lens_info=lens.info_for(conn, file_id) if stack.lens else None)
    del src
    img = Image.fromarray(out, "RGB")
    if opts.long_edge and max(img.size) > opts.long_edge:
        img.thumbnail((opts.long_edge, opts.long_edge), Image.Resampling.LANCZOS)
    os.makedirs(opts.folder, exist_ok=True)
    dest = free_path(opts.folder, file_name(opts.pattern, filename, taken, n, FORMATS[opts.format]))
    kwargs: dict = {"icc_profile": _srgb_icc()}
    exif = exif_bytes(conn, file_id, opts.metadata, img.size)
    if exif:
        kwargs["exif"] = exif
    if opts.format == "jpeg":
        kwargs.update(quality=opts.quality, subsampling=0 if opts.quality >= 90 else 2, optimize=True)
        img.save(dest, "JPEG", **kwargs)
    elif opts.format == "tiff":
        # Pillow can't write a piexif block into a TIFF ("Error setting from
        # dictionary"): TIFF gets the main tags as TIFF tags instead.
        kwargs.pop("exif", None)
        if exif:
            import piexif
            zeroth = piexif.load(exif)["0th"]
            kwargs["tiffinfo"] = {tag: (v.decode("utf-8", "replace") if isinstance(v, bytes) else v)
                                  for tag, v in zeroth.items()
                                  if tag in (piexif.ImageIFD.Make, piexif.ImageIFD.Model, piexif.ImageIFD.Software,
                                             piexif.ImageIFD.DateTime, piexif.ImageIFD.Orientation)}
        img.save(dest, "TIFF", compression="tiff_lzw", **kwargs)
    else:
        img.save(dest, "PNG", **kwargs)
    return dest
