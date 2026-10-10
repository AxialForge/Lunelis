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
ISO_FIELD_MAX = 65535                    # what EXIF's ISO field can hold
METADATA = ("all", "no_location", "none")


@dataclass
class ExportOptions:
    folder: str
    format: str = "jpeg"                 # jpeg | tiff | png
    long_edge: int | None = None         # None = full size
    quality: int = 92                    # JPEG only
    metadata: str = "all"                # all | no_location | none
    pattern: str = "{name}"
    sharpen: str = "none"                # output sharpening for: none | screen | matte | glossy
    sharpen_amount: str = "standard"     # low | standard | high
    profile: str | None = None           # an output .icc profile (None: sRGB)
    intent: str = "perceptual"           # perceptual | relative (rendering intent into `profile`)

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
        if self.sharpen not in SHARPEN:
            raise ValueError(f"sharpening must be one of {tuple(SHARPEN)}")
        if self.sharpen_amount not in AMOUNTS:
            raise ValueError(f"sharpening amount must be one of {tuple(AMOUNTS)}")
        if self.profile and not self.profile.startswith("builtin:") and not os.path.isfile(self.profile):
            raise ValueError(f"the colour profile isn't there: {self.profile}")


# Output sharpening: radius (px at the output size), strength (%), threshold - per target.
SHARPEN = {"none": None, "screen": (0.6, 70, 2), "matte": (1.2, 110, 2), "glossy": (0.9, 90, 2)}
AMOUNTS = {"low": 0.65, "standard": 1.0, "high": 1.4}


def output_sharpen(img: Image.Image, target: str, amount: str = "standard") -> Image.Image:
    """Sharpening for where the picture is going, at its final size - never part of the edit."""
    spec = SHARPEN.get(target)
    if not spec:
        return img
    from PIL import ImageFilter
    radius, percent, threshold = spec
    return img.filter(ImageFilter.UnsharpMask(radius, round(percent * AMOUNTS[amount]), threshold))


def to_profile(img: Image.Image, profile: str | None, intent: str = "perceptual") -> tuple[Image.Image, bytes]:
    """The image converted from sRGB into `profile` (an .icc file) and that profile's bytes."""
    if not profile:
        return img, _srgb_icc()
    if profile.startswith("builtin:"):
        from lunelis import paths
        from lunelis.edit import icc
        profile = str(icc.profile_path(profile[8:], paths.DATA_DIR / "profiles"))
    out_p = ImageCms.getOpenProfile(profile)
    mode = ImageCms.Intent.PERCEPTUAL if intent == "perceptual" else ImageCms.Intent.RELATIVE_COLORIMETRIC
    converted = ImageCms.profileToProfile(img, ImageCms.createProfile("sRGB"), out_p, renderingIntent=mode,
                                          outputMode="RGB")
    return converted, out_p.tobytes()


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
        if iso and int(iso) > 0:
            # The ISO field is 16 bits: ISO 102,400 and up made the whole
            # export fail. The standard way (EXIF 2.3) is 65535 there, with
            # the real figure in the 32-bit Recommended Exposure Index, which
            # is what the cameras themselves write (0.54).
            iso = int(iso)
            ex[piexif.ExifIFD.ISOSpeedRatings] = min(iso, ISO_FIELD_MAX)
            if iso > ISO_FIELD_MAX:
                ex[piexif.ExifIFD.SensitivityType] = 2          # 2 = Recommended Exposure Index
                ex[piexif.ExifIFD.RecommendedExposureIndex] = min(iso, 0xFFFFFFFF)
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

def rendered(conn: sqlite3.Connection, file_id: int, long_edge: int | None = None,
             geometry_only: bool = False, stack=None) -> Image.Image:
    """The photo with its edits, no bigger than `long_edge` (None: full size).
    Export and every Create tool make their pixels here. `geometry_only`: the
    original look with just its crop and rotation (a "before")."""
    row = conn.execute("SELECT r.path, f.rel_path, f.is_raw FROM files f JOIN roots r ON r.id = f.root_id"
                       " WHERE f.id = ?", (file_id,)).fetchone()
    if row is None:
        raise ValueError("not in the catalog")
    root, rel, is_raw = row
    src_path = os.path.join(root, *rel.split("/"))
    stack = store.get(conn, file_id) if stack is None else stack
    if geometry_only:
        from lunelis.edit.stack import Stack
        stack = Stack(geometry=stack.geometry)
    g = stack.geometry
    # A crop or straighten throws pixels away: decode at full size then.
    whole = g.crop == (0.0, 0.0, 1.0, 1.0) and not g.angle
    edge = long_edge if (long_edge and whole) else None
    src = render.load_source(src_path, bool(is_raw), edge)
    from lunelis.edit import ai, lens
    out = pipeline.apply_tiled(src, stack, store.filter_params(conn, stack.filter),
                               ai_maps=ai.maps_for(file_id, stack, src),
                               lens_info=lens.info_for(conn, file_id) if stack.lens else None)
    del src
    img = Image.fromarray(out, "RGB")
    if long_edge and max(img.size) > long_edge:
        img.thumbnail((long_edge, long_edge), Image.Resampling.LANCZOS)
    return img


def export_one(conn: sqlite3.Connection, file_id: int, opts: ExportOptions, n: int = 1, stack=None) -> str:
    """Export one photo (with `stack` instead of its own edit - a virtual copy's);
    returns the new file's path."""
    row = conn.execute("SELECT f.filename, e.captured_at FROM files f LEFT JOIN exif e ON e.file_id = f.id"
                       " WHERE f.id = ?", (file_id,)).fetchone()
    if row is None:
        raise ValueError("not in the catalog")
    filename, taken = row
    img = rendered(conn, file_id, opts.long_edge, stack=stack)
    img = output_sharpen(img, opts.sharpen, opts.sharpen_amount)
    img, icc = to_profile(img, opts.profile, opts.intent)
    os.makedirs(opts.folder, exist_ok=True)
    dest = free_path(opts.folder, file_name(opts.pattern, filename, taken, n, FORMATS[opts.format]))
    kwargs: dict = {"icc_profile": icc}
    exif = exif_bytes(conn, file_id, opts.metadata, img.size)
    if exif:
        kwargs["exif"] = exif
    # Written under a temporary name and renamed when it's whole: a cancelled
    # or crashed export left a half-written file under its final name, which
    # looked like a finished photo (0.54).
    tmp = f"{dest}.{os.getpid()}.part"
    try:
        _save(img, tmp, opts, kwargs, exif)
        try:
            os.rename(tmp, dest)
        except FileExistsError:                   # the name was taken meanwhile: never overwrite
            dest = free_path(opts.folder, os.path.basename(dest))
            os.rename(tmp, dest)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return dest


def _save(img: Image.Image, dest: str, opts: ExportOptions, kwargs: dict, exif: bytes | None) -> None:
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
