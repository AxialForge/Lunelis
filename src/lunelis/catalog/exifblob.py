"""
The full extracted EXIF (everything not modelled as a column) is stored
zlib-compressed in `exif.raw_exif`. As plain JSON it was two thirds of the
catalog (320 of 487 MB at 159k files); compressed it's ~40% of that, which
matters at the 500k-1M file libraries Lunelis is meant to scale to.
"""
from __future__ import annotations

import json
import zlib


def pack(raw_json: str | None) -> bytes | None:
    if not raw_json:
        return None
    return zlib.compress(raw_json.encode("utf-8"), 6)


def unpack(blob: bytes | None) -> str | None:
    return zlib.decompress(blob).decode("utf-8") if blob else None


def unpack_dict(blob: bytes | None) -> dict:
    text = unpack(blob)
    return json.loads(text) if text else {}
