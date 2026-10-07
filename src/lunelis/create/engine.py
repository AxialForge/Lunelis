"""
The Create tab's shared export engine - every Create tool writes through it.

- `photo(conn, file_id, long_edge)`: a photo with its edits, as a PIL image
  (edit/export.rendered - the same pixels Export makes).
- **Presets** (`presets(data_dir)`): size, fit, format and quality, from
  create/presets.json plus the user's own `<data>/create_presets.json`
  (same format; a same-named preset replaces a built-in one). "Original
  size" is always there.
- `fit(img, preset)`: inside the preset's box, or cropped to fill it.
- **Where:** one folder for everything Create makes - Settings
  `create_output_dir`, else Pictures\\Lunelis creations (Windows' own
  Pictures folder, which may be on OneDrive).
- `save(img, preset, folder, name)`: a NEW file, never an existing one
  overwritten ("name (2).jpg"), sRGB profile embedded, optional EXIF.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps

FORMATS = {"jpeg": ".jpg", "png": ".png", "webp": ".webp", "tiff": ".tif"}
FITS = ("inside", "fill")
BUILTIN = Path(__file__).with_name("presets.json")
USER_FILE = "create_presets.json"
OUTPUT_FOLDER_NAME = "Lunelis creations"


@dataclass(frozen=True)
class Preset:
    name: str
    width: int | None = None              # the box (None: the original size)
    height: int | None = None
    fit: str = "inside"                   # inside | fill
    format: str = "jpeg"
    quality: int = 92

    def check(self) -> None:
        if self.format not in FORMATS:
            raise ValueError(f"{self.name}: format must be one of {tuple(FORMATS)}")
        if self.fit not in FITS:
            raise ValueError(f"{self.name}: fit must be inside or fill")
        if (self.width is None) != (self.height is None):
            raise ValueError(f"{self.name}: give both width and height, or neither")
        if self.width is not None and not (16 <= self.width <= 30000 and 16 <= self.height <= 30000):
            raise ValueError(f"{self.name}: width and height must be 16-30000 px")
        if not 1 <= self.quality <= 100:
            raise ValueError(f"{self.name}: quality must be 1-100")

    @property
    def ext(self) -> str:
        return FORMATS[self.format]


ORIGINAL = Preset("Original size")


def _load(path: Path) -> list[Preset]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for d in data.get("presets", []):
        p = Preset(**{k: d[k] for k in ("name", "width", "height", "fit", "format", "quality") if k in d})
        p.check()
        out.append(p)
    return out


def presets(data_dir: str | Path | None = None) -> list[Preset]:
    """Built-in presets, then the user's (which replace same-named ones). A
    broken user file is reported, not silently ignored: PresetError."""
    found = {p.name: p for p in _load(BUILTIN)}
    if data_dir is not None:
        user = Path(data_dir) / USER_FILE
        if user.exists():
            try:
                for p in _load(user):
                    found[p.name] = p
            except (ValueError, TypeError, KeyError, json.JSONDecodeError) as e:
                raise PresetError(f"{user} can't be read: {e}") from e
    found.setdefault(ORIGINAL.name, ORIGINAL)
    return list(found.values())


class PresetError(ValueError):
    pass


def write_user_presets(data_dir: str | Path) -> Path:
    """Make <data>/create_presets.json (a copy of the built-ins) to edit, if missing."""
    user = Path(data_dir) / USER_FILE
    if not user.exists():
        user.write_text(BUILTIN.read_text(encoding="utf-8"), encoding="utf-8")
    return user


def preset_dict(p: Preset) -> dict:
    return asdict(p)


# --- where -------------------------------------------------------------------------------

def pictures_folder() -> Path:
    """Windows' Pictures folder (it may have been moved, e.g. to OneDrive)."""
    if sys.platform == "win32":
        try:
            import ctypes
            from uuid import UUID
            guid = UUID("{33E28130-4E1E-4676-835A-98395C3BC3BB}")      # FOLDERID_Pictures
            buf = (ctypes.c_byte * 16).from_buffer_copy(guid.bytes_le)
            out = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(buf), 0, None, ctypes.byref(out)) == 0:
                path = out.value
                ctypes.windll.ole32.CoTaskMemFree(out)
                if path:
                    return Path(path)
        except Exception:
            pass
    return Path.home() / "Pictures"


def output_dir(conn: sqlite3.Connection, tool: str | None = None) -> Path:
    """Where a Create tool writes: the folder chosen for Create, else the
    tool's folder in the Lunelis folder, else Pictures/Lunelis creations."""
    from lunelis import lunelis_folder
    from lunelis.settings import Settings
    s = Settings(conn)
    chosen = s.get("create_output_dir")
    if chosen:
        return Path(chosen)
    return lunelis_folder.create(s, tool) or pictures_folder() / OUTPUT_FOLDER_NAME


def stamp(what: str, when: datetime | None = None) -> str:
    """'Animation 2026-10-04 1432' - a name for a new creation."""
    return f"{what} {(when or datetime.now()):%Y-%m-%d %H%M}"


# --- pixels --------------------------------------------------------------------------------

def photo(conn: sqlite3.Connection, file_id: int, long_edge: int | None = None) -> Image.Image:
    """The photo with its edits (exactly what Export makes), RGB."""
    from lunelis.edit.export import rendered
    return rendered(conn, file_id, long_edge)


def thumb(conn: sqlite3.Connection, file_id: int, edge: int = 512) -> Image.Image:
    """The cached thumbnail (with the photo's edits), at most `edge` - quick, for
    previews and small prints like contact sheets. OSError when there's none;
    falls back to the photo itself only when asked for more than it holds."""
    from lunelis import paths
    from lunelis.raw.thumbnails import cache_rel_path
    row = conn.execute("SELECT thumbnail_path FROM files WHERE id = ?", (file_id,)).fetchone()
    p = paths.THUMBNAIL_CACHE / ((row[0] if row else None) or cache_rel_path(file_id))
    with Image.open(p) as im:
        img = im.convert("RGB")
    if max(img.size) > edge:
        img.thumbnail((edge, edge), Image.Resampling.LANCZOS)
    return img


def fit(img: Image.Image, preset: Preset) -> Image.Image:
    if preset.width is None:
        return img
    box = (preset.width, preset.height)
    if preset.fit == "fill":
        return ImageOps.fit(img, box, Image.Resampling.LANCZOS)
    if img.width <= box[0] and img.height <= box[1]:
        return img                                    # never enlarged
    out = img.copy()
    out.thumbnail(box, Image.Resampling.LANCZOS)
    return out


def needed_edge(preset: Preset) -> int | None:
    """The long edge to decode at for this preset (None: full size)."""
    return None if preset.width is None else max(preset.width, preset.height)


# --- files ---------------------------------------------------------------------------------

def save(img: Image.Image, preset: Preset, folder: str | Path, name: str, exif: bytes | None = None) -> str:
    """Write `img` as a new file `name` + the preset's extension in `folder`."""
    from lunelis.edit.export import _BAD, _srgb_icc, free_path
    os.makedirs(folder, exist_ok=True)
    clean = _BAD.sub("_", name).strip(" .") or "Lunelis"
    dest = free_path(str(folder), clean + preset.ext)
    img = img.convert("RGB") if img.mode not in ("RGB", "RGBA") or preset.format == "jpeg" else img
    kwargs: dict = {"icc_profile": _srgb_icc()}
    if exif and preset.format in ("jpeg", "webp", "png"):
        kwargs["exif"] = exif
    if preset.format == "jpeg":
        kwargs.update(quality=preset.quality, subsampling=0 if preset.quality >= 90 else 2, optimize=True)
        img.save(dest, "JPEG", **kwargs)
    elif preset.format == "webp":
        img.save(dest, "WEBP", quality=preset.quality, method=5, **kwargs)
    elif preset.format == "tiff":
        kwargs.pop("exif", None)
        img.save(dest, "TIFF", compression="tiff_lzw", **kwargs)
    else:
        img.save(dest, "PNG", optimize=True, **kwargs)
    return dest
