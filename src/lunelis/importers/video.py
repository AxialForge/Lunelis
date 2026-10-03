"""
Video metadata and poster frames, via PyAV (the FFmpeg libraries as a Python
package - no ffmpeg.exe to install or find).

Opened through a Python file handle, like photos, so any path Windows can
open works. Seeking to the poster frame reads only a few MB even of a 1.5 GB
4K clip (measured on the NAS: 120-415 ms per video).

Times: `creation_time` in MP4/MOV is UTC (checked against same-shoot photos:
Sony XAVC clips land inside the shoot's EXIF times once converted), so it's
converted to the PC's local time zone for `captured_at`, with the offset in
`captured_offset` - the same wall-clock convention as photo EXIF.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import BinaryIO

import av
from PIL import Image

_ISO6709 = re.compile(r"([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)")


def _local(utc: datetime) -> tuple[str, str]:
    local = utc.astimezone()                                   # the PC's zone, DST-aware
    off = local.utcoffset()
    mins = int(off.total_seconds() // 60) if off else 0
    sign = "+" if mins >= 0 else "-"
    return local.replace(tzinfo=None).isoformat(timespec="seconds"), \
        f"{sign}{abs(mins) // 60:02d}:{abs(mins) % 60:02d}"


def _parse_time(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if dt.year < 1990:                                         # 1904/1970 "unset" epochs
        return None
    return dt


def probe(fh: BinaryIO) -> dict:
    """Columns for the `exif` table from a video container."""
    with av.open(fh) as ctr:
        tags = {k.lower(): v for k, v in ctr.metadata.items()}
        vs = ctr.streams.video[0] if ctr.streams.video else None
        for s in ctr.streams:
            for k, v in s.metadata.items():
                tags.setdefault(k.lower(), v)
        duration = ctr.duration / 1e6 if ctr.duration else None
        width = vs.codec_context.width if vs else None
        height = vs.codec_context.height if vs else None

    captured = captured_offset = None
    # Apple writes local time with its offset; prefer it over the UTC field.
    apple = tags.get("com.apple.quicktime.creationdate")
    if apple:
        dt = _parse_time(apple)
        if dt:
            captured = dt.replace(tzinfo=None).isoformat(timespec="seconds")
            off = dt.utcoffset()
            if off is not None:
                m = int(off.total_seconds() // 60)
                captured_offset = f"{'+' if m >= 0 else '-'}{abs(m) // 60:02d}:{abs(m) % 60:02d}"
    if not captured and tags.get("creation_time"):
        dt = _parse_time(tags["creation_time"])
        if dt:
            captured, captured_offset = _local(dt)

    lat = lon = None
    loc = tags.get("location") or tags.get("com.apple.quicktime.location.iso6709")
    if loc:
        m = _ISO6709.match(loc)
        if m:
            lat, lon = float(m.group(1)), float(m.group(2))
            if lat == 0 and lon == 0:
                lat = lon = None

    return {
        "captured_at": captured,
        "captured_offset": captured_offset,
        "camera_make": tags.get("com.apple.quicktime.make") or tags.get("make"),
        "camera_model": tags.get("com.apple.quicktime.model") or tags.get("model"),
        "lens": None, "focal_length_mm": None, "aperture": None, "shutter_speed": None,
        "iso": None, "exposure_comp": None, "flash_fired": None, "orientation": None,
        "gps_lat": lat, "gps_lon": lon, "width_px": width, "height_px": height,
        "duration_s": duration,
        "raw_json": json.dumps({f"Video {k}": str(v)[:200] for k, v in tags.items()},
                               ensure_ascii=False, separators=(",", ":")),
    }


def poster_frame(fh: BinaryIO, edge: int) -> Image.Image:
    """An upright frame ~10% in (at most 1 s) - the first frame is often black."""
    with av.open(fh) as ctr:
        vs = ctr.streams.video[0]
        vs.thread_type = "AUTO"
        duration = ctr.duration / 1e6 if ctr.duration else 10.0
        target = min(1.0, duration * 0.1)
        try:
            ctr.seek(int(target * av.time_base), any_frame=False, backward=True)
        except av.error.FFmpegError:
            ctr.seek(0)
        frame = next(ctr.decode(video=0))
        img = frame.to_image()
        rotation = getattr(frame, "rotation", 0) or 0
    img.thumbnail((edge * 2, edge * 2))
    # FFmpeg's display matrix angle is counter-clockwise; PIL rotates
    # counter-clockwise for positive angles too.
    if rotation % 360:
        img = img.rotate(rotation % 360, expand=True)
    return img
