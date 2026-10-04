"""
Metadata extraction (Phase 1, Step 3).

Reads EXIF from every cataloged file that doesn't have a current `exif` row
and writes the modelled fields plus a filtered JSON of everything else
(stored zlib-compressed in `raw_exif`, see catalog/exifblob.py),
including manufacturer maker notes (Sony focus mode, creative style, ...).

Reader: exifread. It's pure Python and reads through a Python file handle, so
it takes any path Windows can open - pyexiv2 was measured and rejected because
Exiv2 can't open non-ASCII paths on Windows (see CLAUDE.md, Gotchas). It
covers TIFF-based RAW (ARW, NEF, NRW, CR2, DNG, PEF, SRW, ORF, RW2), JPEG,
HEIC, PNG and WebP. Fuji RAF is read through its embedded full-size JPEG.
Canon CR3 and video have no reader yet: they get a row with `read_error` set,
stay in the library, and are picked up once a reader exists.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import sqlite3
import struct
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable

import exifread

from lunelis.catalog.exifblob import pack
from lunelis.importers.formats import SNIFF_BYTES, is_raw_content, sniff

logging.getLogger("exifread").setLevel(logging.CRITICAL)   # it logs every odd tag

# Formats with no reader yet, by sniffed content. When one lands, add a
# migration deleting exif rows whose read_error starts with
# 'NotImplementedError' so those files are re-read.
NO_READER = {
    "cr3": "Canon CR3 metadata reader not built yet",
}
VIDEO_FORMATS = {"mp4", "mov", "mpeg-ts"}      # read by importers/video.py

BATCH_SIZE = 200
WORKERS = 8          # parallel reads; see extract_pending
ProgressFn = Callable[[int, int, str], None]    # (done, total, current file)
CancelFn = Callable[[], bool]


@dataclass
class ExtractResult:
    read: int = 0
    failed: int = 0          # stored with read_error; not retried until the file changes
    cancelled: bool = False
    seconds: float = 0.0


# --- reading one file ------------------------------------------------------

class _Window(io.RawIOBase):
    """A read-only view of `fh` starting at `offset`, so a parser that seeks
    to absolute positions sees an embedded file as if it stood alone."""

    def __init__(self, fh, offset: int) -> None:
        self.fh, self.offset = fh, offset
        fh.seek(offset)

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def seek(self, pos: int, whence: int = 0) -> int:
        if whence == 0:
            pos += self.offset
        return self.fh.seek(pos, whence) - self.offset

    def tell(self) -> int:
        return self.fh.tell() - self.offset

    def read(self, n: int = -1) -> bytes:
        return self.fh.read(n)

    def readinto(self, b) -> int:
        data = self.fh.read(len(b))
        b[: len(data)] = data
        return len(data)


def _raf_jpeg(fh) -> _Window:
    # RAF header: 16-byte magic, then at byte 84 a big-endian uint32 offset
    # to the embedded full-size JPEG, which carries the camera's EXIF.
    fh.seek(84)
    (jpeg_offset,) = struct.unpack(">I", fh.read(4))
    return _Window(fh, jpeg_offset)


def read_tags(fh, fmt: str | None, filename: str) -> dict:
    """Every tag exifread finds, keyed like 'EXIF FNumber'. `fh` at offset 0.

    Maker notes are decoded only when the bytes really are a RAW container.
    A RAW is exactly what the camera wrote; a JPEG has usually been through a
    phone, Google Photos or an editor that moved the maker-note block without
    fixing its internal offsets, and exifread then chases tens of thousands of
    garbage entries one small read at a time (measured: 831ms and climbing,
    vs 1ms without). Content, not extension: Takeout's fake .ARW files are
    exactly those re-saved JPEGs.
    """
    if fmt in NO_READER:
        raise NotImplementedError(NO_READER[fmt])
    if fmt is None:
        raise ValueError("unrecognised file contents")
    src = _raf_jpeg(fh) if fmt == "raf" else fh
    return exifread.process_file(
        src, details=is_raw_content(filename, fmt), extract_thumbnail=False
    )


# --- turning tags into columns --------------------------------------------

def _num(tag) -> float | None:
    if tag is None:
        return None
    try:
        v = tag.values[0] if isinstance(tag.values, list) else tag.values
        f = float(v.num) / float(v.den) if hasattr(v, "den") else float(v)
    except (IndexError, ValueError, TypeError, ZeroDivisionError):
        return None
    return f if f == f else None           # drop NaN from 0/0 rationals


def _int(tag) -> int | None:
    f = _num(tag)
    return int(f) if f is not None else None


def _text(tag) -> str | None:
    if tag is None:
        return None
    s = str(tag.printable).strip().strip("\x00").strip()
    return s or None


def _first(tags: dict, *names: str):
    for n in names:
        if n in tags and _text(tags[n]):
            return tags[n]
    return None


_DATETIME = re.compile(r"(\d{4})[:\-/.](\d{2})[:\-/.](\d{2})[ T](\d{2}):(\d{2}):(\d{2})")


def _datetime(tags: dict) -> str | None:
    """'2026:03:14 13:18:03' + subsec '977' -> '2026-03-14T13:18:03.977'.

    The spec says colons in the date; some phones write dashes. Anything
    else unparseable is dropped rather than failing the whole file.
    """
    raw = _text(_first(tags, "EXIF DateTimeOriginal", "EXIF DateTimeDigitized", "Image DateTime"))
    m = _DATETIME.match(raw or "")
    if not m or m.group(1) == "0000" or not (1 <= int(m.group(2)) <= 12):
        return None
    y, mo, d, hh, mm, ss = m.groups()
    iso = f"{y}-{mo}-{d}T{hh}:{mm}:{ss}"
    sub = _text(tags.get("EXIF SubSecTimeOriginal"))
    if sub and sub.isdigit():
        iso += "." + sub
    return iso


def _gps(tags: dict, axis: str) -> float | None:
    tag, ref = tags.get(f"GPS GPS{axis}"), _text(tags.get(f"GPS GPS{axis}Ref"))
    if tag is None or not isinstance(tag.values, list) or len(tag.values) < 3:
        return None
    try:
        d, m, s = (float(v.num) / float(v.den) for v in tag.values[:3])
    except (ZeroDivisionError, AttributeError):
        return None
    val = d + m / 60 + s / 3600
    if ref in ("S", "W"):
        val = -val
    return round(val, 7)


def _keep_in_raw_json(key: str, tag) -> bool:
    # Named tags only: unknown 'Tag 0x...' entries, strip/tile offsets, the
    # embedded thumbnail and multi-KB binary blobs are noise, and at 80k+
    # photos they'd be most of the catalog's size.
    if key.startswith(("JPEGThumbnail", "Thumbnail", "EXIF SubIFD", "IFD ", "Interoperability")):
        return False
    if " Tag 0x" in key or key.endswith(("MakerNote", "PrintIM", "ApplicationNotes", "UserComment")):
        return False
    return hasattr(tag, "printable") and len(str(tag.printable)) <= 200


def to_row(tags: dict) -> dict:
    """Map exifread tags onto the `exif` columns."""
    lat, lon = _gps(tags, "Latitude"), _gps(tags, "Longitude")
    if lat == 0 and lon == 0:              # "no fix" written as 0,0
        lat = lon = None
    flash = _int(tags.get("EXIF Flash"))
    raw = {k: _text(v) for k, v in tags.items() if _keep_in_raw_json(k, v)}
    return {
        "captured_at": _datetime(tags),
        "captured_offset": _text(_first(tags, "EXIF OffsetTimeOriginal", "EXIF OffsetTime")),
        "camera_make": _text(tags.get("Image Make")),
        "camera_model": _text(tags.get("Image Model")),
        "lens": _text(_first(tags, "EXIF LensModel", "Image LensModel", "MakerNote LensModel",
                             "MakerNote LensType", "MakerNote Lens")),
        "focal_length_mm": _num(tags.get("EXIF FocalLength")),
        "aperture": _num(tags.get("EXIF FNumber")),
        "shutter_speed": _text(tags.get("EXIF ExposureTime")),
        "iso": _int(_first(tags, "EXIF ISOSpeedRatings", "EXIF PhotographicSensitivity",
                           "EXIF RecommendedExposureIndex")),
        "exposure_comp": _num(tags.get("EXIF ExposureBiasValue")),
        "flash_fired": (flash & 1) if flash is not None else None,
        "orientation": _int(_first(tags, "Image Orientation")),
        "gps_lat": lat,
        "gps_lon": lon,
        "width_px": _int(_first(tags, "EXIF ExifImageWidth")),
        "height_px": _int(_first(tags, "EXIF ExifImageLength")),
        "duration_s": None,
        "raw_json": json.dumps({k: v for k, v in raw.items() if v}, ensure_ascii=False,
                               separators=(",", ":")),
    }


MOTION_SCAN = 256 * 1024


def motion_video_bytes(path: str) -> int | None:
    """An Android motion photo's embedded video length (it sits at the end of
    the JPEG), or None. Read from the XMP: GCamera:MicroVideoOffset (older
    Pixels) or the Container directory's MotionPhoto item (newer phones)."""
    try:
        with open(path, "rb") as fh:
            return _motion_bytes(fh)
    except OSError:
        return None


def _motion_bytes(fh) -> int | None:
    import re as _re
    fh.seek(0)
    head = fh.read(MOTION_SCAN)
    fh.seek(0)
    if b"MotionPhoto" not in head and b"MicroVideo" not in head:
        return None
    m = _re.search(rb'MicroVideoOffset="(\d+)"', head) or _re.search(rb"<GCamera:MicroVideoOffset>(\d+)<", head)
    if m:
        return int(m.group(1)) or None
    m = _re.search(rb'Item:Semantic="MotionPhoto"[^>]*?Item:Length="(\d+)"', head, _re.S) \
        or _re.search(rb'Item:Length="(\d+)"[^>]*?Item:Semantic="MotionPhoto"', head, _re.S)
    return int(m.group(1)) if m and int(m.group(1)) else None


def read_open(fh, filename: str) -> tuple[str | None, dict]:
    """(sniffed format, exif columns) for an open file. The format is set on
    the exception as `.fmt` too, so a failed read still reports what it saw."""
    fmt = sniff(fh.read(SNIFF_BYTES))
    fh.seek(0)
    try:
        if fmt in VIDEO_FORMATS:
            from lunelis.importers.video import probe
            row = probe(fh)
            row["date_source"] = "video" if row["captured_at"] else None
            return fmt, row
        tags = read_tags(fh, fmt, filename)
        if not tags:
            raise ValueError("no EXIF found")
        row = to_row(tags)
        row["date_source"] = "exif" if row["captured_at"] else None
        if fmt == "jpeg":
            row["motion_video"] = _motion_bytes(fh)
        return fmt, row
    except Exception as e:
        e.fmt = fmt
        raise


def read_file(path: str) -> dict:
    """Exif columns for one file. Raises on anything unreadable."""
    with open(path, "rb") as fh:
        return read_open(fh, path)[1]


# --- the pending queue -----------------------------------------------------

COLUMNS = (
    "captured_at", "captured_offset", "camera_make", "camera_model", "lens",
    "focal_length_mm", "aperture", "shutter_speed", "iso", "exposure_comp",
    "flash_fired", "orientation", "gps_lat", "gps_lon", "width_px", "height_px",
    "duration_s", "date_source", "raw_exif",       # raw_exif must stay last (see _db_values)
)


def _db_values(row: dict) -> list:
    """to_row() output in COLUMNS order; the raw JSON is stored compressed."""
    return [row[c] for c in COLUMNS[:-1]] + [pack(row["raw_json"])]


_UPSERT = (
    f"INSERT OR REPLACE INTO exif (file_id, {', '.join(COLUMNS)}, extracted_mtime, read_error)"
    f" VALUES ({', '.join('?' * (len(COLUMNS) + 3))})"
)

PENDING_SQL = """
    SELECT f.id, r.path AS root, f.rel_path, f.mtime
    FROM files f
    JOIN roots r ON r.id = f.root_id
    LEFT JOIN exif e ON e.file_id = f.id
    WHERE f.missing_since IS NULL AND f.excluded = 0
      AND r.enabled = 1
      AND (e.file_id IS NULL OR e.extracted_mtime IS NOT f.mtime)
"""


def pending_count(conn: sqlite3.Connection) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM ({PENDING_SQL})").fetchone()[0]


def _read_one(root: str, rel_path: str) -> tuple[str | None, dict | None, str | None]:
    """(format, columns, error) for one file. Never raises."""
    path = os.path.join(root, *rel_path.split("/"))
    try:
        with open(path, "rb") as fh:
            fmt, row = read_open(fh, rel_path)
        return fmt, row, None
    except Exception as e:                # one bad file must never stop the run
        return getattr(e, "fmt", None), None, f"{type(e).__name__}: {e}"[:300]


def extract_pending(conn: sqlite3.Connection, *,
                    on_progress: ProgressFn | None = None,
                    should_cancel: CancelFn | None = None,
                    workers: int = WORKERS) -> ExtractResult:
    """Read metadata for every file without a current `exif` row.

    Files are read `workers` at a time: over SMB each read is mostly waiting
    on round trips, so parallel reads hide the latency (measured on the NAS:
    21 files/s with 1, 90 with 8, 129 with 16). All catalog writes stay on
    this thread. Resumable: rows are committed per batch, and an interrupted
    run leaves the rest pending for next time.
    """
    started = time.perf_counter()
    result = ExtractResult()
    todo = conn.execute(PENDING_SQL + " ORDER BY f.root_id, f.rel_path").fetchall()
    done = 0

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for start in range(0, len(todo), BATCH_SIZE):
            if should_cancel and should_cancel():
                result.cancelled = True
                break
            chunk = todo[start:start + BATCH_SIZE]
            futures = [pool.submit(_read_one, root, rel) for _, root, rel, _ in chunk]
            batch: list[tuple] = []
            formats: list[tuple] = []
            motion: list[tuple] = []
            for (file_id, _, rel_path, mtime), fut in zip(chunk, futures):
                if should_cancel and should_cancel():
                    result.cancelled = True
                    for f in futures:
                        f.cancel()
                    break
                fmt, row, err = fut.result()
                if row is not None:
                    batch.append((file_id, *_db_values(row), mtime, None))
                    if row.get("motion_video"):
                        motion.append((row["motion_video"], file_id))
                    result.read += 1
                else:
                    batch.append((file_id, *([None] * len(COLUMNS)), mtime, err))
                    result.failed += 1
                if fmt is not None:
                    # Correct the scan's extension-based guess with what the bytes say.
                    formats.append((fmt, int(is_raw_content(rel_path, fmt)), file_id))
                done += 1
                if on_progress and (done % 25 == 0 or done == len(todo)):
                    on_progress(done, len(todo), rel_path)
            conn.executemany(_UPSERT, batch)
            conn.executemany("UPDATE files SET format = ?, is_raw = ? WHERE id = ?", formats)
            conn.executemany("UPDATE files SET motion_video = ? WHERE id = ?", motion)
            conn.commit()
            if result.cancelled:
                break

    result.seconds = time.perf_counter() - started
    return result


def _main(argv: list[str]) -> int:
    import argparse

    from lunelis.catalog.schema import open_catalog
    from lunelis.paths import DEFAULT_CATALOG_PATH

    ap = argparse.ArgumentParser(prog="python -m lunelis.importers.metadata",
                                 description="Read EXIF for every file that needs it.")
    ap.add_argument("--db", default=str(DEFAULT_CATALOG_PATH), help="catalog path")
    args = ap.parse_args(argv)
    conn = open_catalog(args.db)
    try:
        r = extract_pending(conn, on_progress=lambda d, t, f: print(
            f"\r  {d:>7,}/{t:,}  {f[-60:]:<60}", end="", flush=True))
        print(f"\nread {r.read:,}, unreadable {r.failed:,}, in {r.seconds:.1f}s")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
