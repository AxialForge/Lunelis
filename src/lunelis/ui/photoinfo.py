"""
Everything worth showing about one photo, formatted - shared by the grid's
hover card and the detail view's Info panel, so they always agree.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime

# Sony writes model codes; people know the names.
CAMERA_NAMES = {
    "ILCE-1": "α1", "ILCE-1M2": "α1 II", "ILCE-9": "α9", "ILCE-9M2": "α9 II", "ILCE-9M3": "α9 III",
    "ILCE-7": "α7", "ILCE-7M2": "α7 II", "ILCE-7M3": "α7 III", "ILCE-7M4": "α7 IV", "ILCE-7M5": "α7 V",
    "ILCE-7R": "α7R", "ILCE-7RM2": "α7R II", "ILCE-7RM3": "α7R III", "ILCE-7RM3A": "α7R IIIA",
    "ILCE-7RM4": "α7R IV", "ILCE-7RM4A": "α7R IVA", "ILCE-7RM5": "α7R V",
    "ILCE-7S": "α7S", "ILCE-7SM2": "α7S II", "ILCE-7SM3": "α7S III", "ILCE-7C": "α7C",
    "ILCE-7CM2": "α7C II", "ILCE-7CR": "α7CR", "ILCE-6000": "α6000", "ILCE-6100": "α6100",
    "ILCE-6300": "α6300", "ILCE-6400": "α6400", "ILCE-6500": "α6500", "ILCE-6600": "α6600",
    "ILCE-6700": "α6700", "ILME-FX3": "FX3", "ILME-FX30": "FX30", "ZV-E10": "ZV-E10",
    # Samsung phones write model codes too (the letter at the end is the region).
    "SM-G973U": "Galaxy S10", "SM-G975U": "Galaxy S10+", "SM-G991U": "Galaxy S21", "SM-G996U": "Galaxy S21+",
    "SM-G998U": "Galaxy S21 Ultra", "SM-G998B": "Galaxy S21 Ultra", "SM-S908U": "Galaxy S22 Ultra",
    "SM-S918U": "Galaxy S23 Ultra", "SM-S928U": "Galaxy S24 Ultra", "SM-S938U": "Galaxy S25 Ultra",
    "SM-N960U": "Galaxy Note9", "SM-N975U": "Galaxy Note10+", "SM-N986U": "Galaxy Note20 Ultra",
}


def breakable(path: str) -> str:
    # A long NAS path wraps at its separators instead of pushing the panel wide.
    lead = "\\\\" if path.startswith("\\\\") else ""        # keep a share's leading \\ together
    return lead + path[len(lead):].replace("\\", "\\\u200b").replace("/", "/\u200b")


INFO_SQL = """
SELECT f.id, f.filename, r.path, f.rel_path, f.size_bytes, f.format, f.is_raw, f.sidecar,
       e.captured_at, e.captured_offset, e.date_source, e.camera_make, e.camera_model, e.lens,
       e.focal_length_mm, e.aperture, e.shutter_speed, e.iso, e.exposure_comp, e.flash_fired,
       e.gps_lat, e.gps_lon, e.width_px, e.height_px, e.orientation, e.duration_s,
       COALESCE(rt.stars, 0), rt.flag, rt.color_label,
       ev.id, ev.name,
       d.problem, f.mtime, f.motion_video
FROM files f JOIN roots r ON r.id = f.root_id
LEFT JOIN exif e ON e.file_id = f.id
LEFT JOIN ratings rt ON rt.file_id = f.id
LEFT JOIN event_files ef ON ef.file_id = f.id LEFT JOIN events ev ON ev.id = ef.event_id
LEFT JOIN damaged d ON d.file_id = f.id
WHERE f.id = ?
"""


DATE_FORMATS = {
    "long": "Jun 19, 2026 · 2:03 PM",
    "iso": "2026-06-19 14:03",
    "day_first": "19 Jun 2026 · 14:03",
    "short": "6/19/2026 2:03 PM",
}
_date_format = "long"


def set_date_format(key: str) -> None:
    global _date_format
    _date_format = key if key in DATE_FORMATS else "long"


def format_date(t: datetime) -> str:
    if _date_format == "iso":
        return f"{t:%Y-%m-%d %H:%M}"
    if _date_format == "day_first":
        return f"{t.day} {t:%b} {t.year} · {t:%H:%M}"
    hour = t.strftime("%I:%M %p").lstrip("0")
    if _date_format == "short":
        return f"{t.month}/{t.day}/{t.year} {hour}"
    return f"{t:%b} {t.day}, {t.year} · {hour}"


@dataclass
class PhotoInfo:
    file_id: int
    filename: str
    root: str
    rel_path: str
    size: int
    format: str | None
    is_raw: bool
    sidecar: str | None
    captured_at: str | None
    captured_offset: str | None
    date_source: str | None
    make: str | None
    model: str | None
    lens: str | None
    focal: float | None
    aperture: float | None
    shutter: str | None
    iso: int | None
    exposure_comp: float | None
    flash: int | None
    lat: float | None
    lon: float | None
    width: int | None
    height: int | None
    orientation: int | None
    duration: float | None
    stars: int
    flag: str | None
    label: str | None
    event_id: int | None
    event: str | None
    damaged: str | None
    mtime: str | None
    motion_video: int | None = None    # an Android motion photo's embedded video, in bytes
    protection: str = ""               # the Backup line (backups/protection.py), filled by load()

    @property
    def path(self) -> str:
        return os.path.join(self.root, *self.rel_path.split("/"))

    @property
    def is_video(self) -> bool:
        return (self.format or "") in ("mp4", "mov", "mpeg-ts")

    def when(self) -> str:
        """'Jun 19, 2026 · 2:03 PM' (camera time, in the Settings date format),
        or the file date marked as such."""
        iso = self.captured_at
        note = ""
        if not iso and self.mtime:
            iso, note = self.mtime, " (file date)"
        if not iso:
            return "No date"
        try:
            t = datetime.fromisoformat(iso[:19])
        except ValueError:
            return iso
        return format_date(t) + note

    def camera(self) -> str:
        make, model = (self.make or "").strip(), (self.model or "").strip()
        model = CAMERA_NAMES.get(model.upper(), model)
        if make and model.lower().startswith(make.lower().split()[0]):
            return model                              # "Canon Canon EOS R5" -> "Canon EOS R5"
        return " ".join(p for p in (make.title() if make.isupper() else make, model) if p)

    def exposure(self) -> str:
        """'50mm  f/2.8  1/500s  ISO 400  +0.3 EV'."""
        parts = []
        if self.focal:
            parts.append(f"{self.focal:g}mm")
        if self.aperture:
            parts.append(f"f/{self.aperture:g}")
        if self.shutter:
            s = str(self.shutter)
            parts.append(s if s.endswith("s") else f"{s}s")
        if self.iso:
            parts.append(f"ISO {self.iso}")
        if self.exposure_comp:
            parts.append(f"{self.exposure_comp:+.1f} EV")
        return "  ".join(parts)

    def dimensions(self) -> str:
        if not (self.width and self.height):
            return ""
        w, h = (self.height, self.width) if (self.orientation or 1) >= 5 else (self.width, self.height)
        mp = w * h / 1e6
        return f"{w:,} × {h:,}" + (f"  ({mp:.1f} MP)" if mp >= 1 else "")

    def size_text(self) -> str:
        n = self.size or 0
        return f"{n / 1e9:.2f} GB" if n >= 1e9 else f"{n / 1e6:.1f} MB" if n >= 1e5 else f"{n / 1e3:.0f} KB"

    def kind(self) -> str:
        ext = self.filename.rsplit(".", 1)[-1].upper() if "." in self.filename else ""
        if self.is_video:
            return f"Video ({ext})"
        return f"RAW ({ext})" if self.is_raw else ext or (self.format or "").upper()

    def length(self) -> str:
        if not self.duration:
            return ""
        s = int(round(self.duration))
        return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"

    def map_url(self) -> str | None:
        if self.lat is None or self.lon is None:
            return None
        return f"https://www.openstreetmap.org/?mlat={self.lat:.6f}&mlon={self.lon:.6f}#map=15/{self.lat:.5f}/{self.lon:.5f}"


def load(conn: sqlite3.Connection, file_id: int) -> PhotoInfo | None:
    row = conn.execute(INFO_SQL, (file_id,)).fetchone()
    if row is None:
        return None
    r = tuple(row)
    info = PhotoInfo(r[0], r[1], r[2], r[3], r[4], r[5], bool(r[6]), r[7], *r[8:])
    from lunelis.backups.protection import of
    info.protection = of(conn, file_id).text()
    return info
