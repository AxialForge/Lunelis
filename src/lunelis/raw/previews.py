"""
Embedded JPEG previews inside RAW files.

Every camera RAW carries one or more ready-made JPEGs. For a grid thumbnail
the right one is the SMALLEST preview that's still big enough - not the
largest: on an A7R V the IFD0 preview is 1616x1080 in ~280 KB while the full
embedded JPEG is 9504x6336 in ~9.4 MB. Reading the small one is 34x less I/O
(what matters over SMB) and ~12x faster end to end (measured 6.3 vs 80 ms).
rawpy's extract_thumb() always returns the largest, so this walks the TIFF
directory tree itself.

Covers TIFF-container RAWs (ARW, NEF, NRW, CR2, DNG, PEF, SRW, ORF, RW2) and
Fuji RAF. CR3 is left to rawpy.
"""
from __future__ import annotations

import io
import struct
from typing import BinaryIO

from PIL import Image

MAX_IFDS = 64            # guards against corrupt files with IFD loops
MAX_ENTRIES = 1000
MIN_PREVIEW_BYTES = 2048

TAG_COMPRESSION = 0x0103
TAG_STRIP_OFFSETS = 0x0111
TAG_STRIP_BYTE_COUNTS = 0x0117
TAG_SUB_IFDS = 0x014A
TAG_JPEG_OFFSET = 0x0201
TAG_JPEG_LENGTH = 0x0202
TAG_EXIF_IFD = 0x8769

_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4}


def _read_ifd(fh: BinaryIO, offset: int, e: str, file_size: int) -> tuple[dict[int, list[int]], int]:
    """Integer-valued tags of one IFD, and the offset of the next IFD (0 = none)."""
    fh.seek(offset)
    (count,) = struct.unpack(e + "H", fh.read(2))
    if count > MAX_ENTRIES:
        raise ValueError("implausible IFD")
    raw = fh.read(12 * count)
    tags: dict[int, list[int]] = {}
    for i in range(count):
        tag, typ, n, val = struct.unpack(e + "HHI4s", raw[12 * i: 12 * i + 12])
        size = _TYPE_SIZE.get(typ)
        if size not in (2, 4) or n == 0 or n > 4096:
            continue                      # only SHORT/LONG/IFD arrays matter here
        fmt = e + ("H" if size == 2 else "I") * n
        if size * n <= 4:
            data = val[: size * n]
        else:
            (ptr,) = struct.unpack(e + "I", val)
            if ptr + size * n > file_size:
                continue
            here = fh.tell()
            fh.seek(ptr)
            data = fh.read(size * n)
            fh.seek(here)
        tags[tag] = list(struct.unpack(fmt, data))
    (nxt,) = struct.unpack(e + "I", fh.read(4) or b"\0\0\0\0")
    return tags, nxt


def tiff_jpeg_candidates(fh: BinaryIO, base: int = 0) -> list[tuple[int, int]]:
    """(offset, length) of every JPEG-looking blob in a TIFF container at `base`."""
    fh.seek(0, 2)
    file_size = fh.tell()
    fh.seek(base)
    head = fh.read(8)
    e = "<" if head[:2] == b"II" else ">"
    (first,) = struct.unpack(e + "I", head[4:8])

    found: set[tuple[int, int]] = set()
    seen: set[int] = set()
    queue = [first]
    while queue and len(seen) < MAX_IFDS:
        off = queue.pop()
        if not off or off in seen or base + off >= file_size:
            continue
        seen.add(off)
        try:
            tags, nxt = _read_ifd(fh, base + off, e, file_size)
        except (struct.error, ValueError):
            continue
        queue.append(nxt)
        queue.extend(tags.get(TAG_SUB_IFDS, []))
        queue.extend(tags.get(TAG_EXIF_IFD, [])[:1])
        if TAG_JPEG_OFFSET in tags and TAG_JPEG_LENGTH in tags:
            found.add((tags[TAG_JPEG_OFFSET][0], tags[TAG_JPEG_LENGTH][0]))
        # Old-style/new-style JPEG compression stored as a single strip
        # (CR2 IFD0, DNG preview IFDs). The raw sensor data is often a
        # lossless JPEG too - Pillow can't decode that, and the caller skips it.
        if tags.get(TAG_COMPRESSION, [0])[0] in (6, 7):
            offs, lens = tags.get(TAG_STRIP_OFFSETS, []), tags.get(TAG_STRIP_BYTE_COUNTS, [])
            if len(offs) == 1 and len(lens) == 1:
                found.add((offs[0], lens[0]))

    return sorted(
        ((base + o, n) for o, n in found
         if n >= MIN_PREVIEW_BYTES and base + o + n <= file_size),
        key=lambda c: c[1],
    )


def raf_jpeg_candidate(fh: BinaryIO) -> list[tuple[int, int]]:
    """Fuji RAF: big-endian offset/length of the embedded full JPEG at byte 84."""
    fh.seek(84)
    off, length = struct.unpack(">II", fh.read(8))
    return [(off, length)] if length >= MIN_PREVIEW_BYTES else []


def best_preview(fh: BinaryIO, candidates: list[tuple[int, int]], min_edge: int) -> Image.Image | None:
    """Open the smallest candidate whose long edge is >= min_edge (or the
    largest decodable one if none is that big). Returns a lazy PIL image,
    already set to a DCT-scaled draft decode where that helps."""
    fallback = None
    for off, length in candidates:                # ascending by byte size
        fh.seek(off)
        data = fh.read(length)
        if not data.startswith(b"\xff\xd8"):
            continue
        try:
            img = Image.open(io.BytesIO(data))
            if img.mode not in ("RGB", "L", "CMYK", "YCbCr"):
                continue                             # e.g. lossless sensor JPEG
        except Exception:
            continue
        if max(img.size) >= min_edge:                # header size, before any draft
            img.draft("RGB", (min_edge, min_edge))
            return img
        fallback = img
    return fallback
