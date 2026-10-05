"""
Which files Lunelis catalogs, by extension.

One table, used by the scanner now and by the EXIF / proxy steps later, so
"is this a photo" and "is this a RAW" are answered in exactly one place.
Extensions are lowercase with no dot, matching `files.ext`.
"""
from __future__ import annotations

RAW_EXTS: frozenset[str] = frozenset({
    "arw", "sr2", "arq",        # Sony
    "cr2", "cr3",               # Canon
    "nef", "nrw",               # Nikon
    "raf",                      # Fujifilm
    "dng",                      # Adobe / Leica / Pentax / Ricoh
    "rw2",                      # Panasonic
    "orf",                      # Olympus / OM System
    "pef",                      # Pentax native
    "srw",                      # Samsung
})

IMAGE_EXTS: frozenset[str] = frozenset({
    "jpg", "jpeg", "png", "tif", "tiff", "heic", "heif", "webp", "bmp",
    "hif",                      # Sony/Canon/Fuji in-camera HEIF
    "gif",                      # animated ones play in the photo view and on hover (0.23)
})

# Catalog + thumbnail only; not an edit target (see design doc, File format table).
VIDEO_EXTS: frozenset[str] = frozenset({
    "mp4", "mov", "mts", "m2ts",  # m2ts/mts = AVCHD
})

CATALOGED_EXTS: frozenset[str] = RAW_EXTS | IMAGE_EXTS | VIDEO_EXTS


SNIFF_BYTES = 16

# ISO-BMFF brands (the 4 bytes after 'ftyp') and what they mean here.
_FTYP = {
    b"crx ": "cr3",
    b"heic": "heic", b"heix": "heic", b"heim": "heic", b"heis": "heic",
    b"hevc": "heic", b"mif1": "heic", b"msf1": "heic", b"avif": "avif",
    b"qt  ": "mov",
}


def sniff(head: bytes) -> str | None:
    """What a file actually is, from its first SNIFF_BYTES bytes.

    Extensions lie: Google Takeout ships Picasa-re-saved JPEGs still named
    .ARW. Anything that decides how to decode a file must ask this, not ext.
    """
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head[:4] in (b"II*\x00", b"MM\x00*", b"IIRO", b"IIRS", b"MMOR", b"IIU\x00"):
        return "tiff"              # TIFF container: ARW/NEF/CR2/DNG/PEF/SRW/ORF/RW2/TIFF
    if head.startswith(b"FUJIFILMCCD-RAW"):
        return "raf"
    if head[4:8] == b"ftyp":
        return _FTYP.get(head[8:12], "mp4")
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:2] == b"BM":
        return "bmp"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"               # checked before MPEG-TS: 'G' is the TS sync byte
    if head[:1] == b"\x47" or head[4:5] == b"\x47":
        return "mpeg-ts"           # AVCHD .mts / .m2ts
    return None


RAW_CONTAINERS = frozenset({"tiff", "raf", "cr3"})


def is_raw_content(filename: str, fmt: str | None) -> bool:
    """A RAW extension whose bytes are really a RAW container.

    A plain .tif is a TIFF container too, which is why the extension still
    has to agree.
    """
    return ext_of(filename) in RAW_EXTS and fmt in RAW_CONTAINERS


def ext_of(filename: str) -> str:
    """Lowercase extension without the dot ('' if none)."""
    dot = filename.rfind(".")
    return filename[dot + 1:].lower() if dot > 0 else ""


def is_cataloged(filename: str) -> bool:
    return ext_of(filename) in CATALOGED_EXTS


def is_raw(filename: str) -> bool:
    return ext_of(filename) in RAW_EXTS
