"""
Your shooting stats: what you shoot with, how, and what you keep.

A **keeper** is a photo flagged Pick (docs/Schemas.md §4) - stars don't
count. Every figure is over photos (not videos) still in the library,
archived ones included (they were shot), and only those whose camera
details are known for a breakdown by them.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

PHOTOS = ("f.missing_since IS NULL AND f.excluded = 0 AND f.quarantined_at IS NULL"
          " AND COALESCE(f.format, '') NOT IN ('mp4', 'mov', 'mpeg-ts')")
FROM = "FROM files f LEFT JOIN exif e ON e.file_id = f.id LEFT JOIN ratings rt ON rt.file_id = f.id"
KEEP = "SUM(CASE WHEN rt.flag = 'pick' THEN 1 ELSE 0 END)"

ISO_STEPS = (100, 200, 400, 800, 1600, 3200, 6400, 12800, 25600)
SHUTTER_STEPS = ((1 / 4000, "1/4000 s or faster"), (1 / 1000, "1/1000 s"), (1 / 250, "1/250 s"),
                 (1 / 60, "1/60 s"), (1 / 15, "1/15 s"), (1 / 2, "1/2 s"), (float("inf"), "1 s or longer"))
STOPS = (1.0, 1.2, 1.4, 1.8, 2.0, 2.8, 4.0, 5.6, 8.0, 11.0, 16.0, 22.0)


@dataclass
class Row:
    bucket: str
    photos: int
    keepers: int

    @property
    def rate(self) -> float:
        return self.keepers / self.photos if self.photos else 0.0


def _shutter_seconds(s: str | None) -> float | None:
    if not s:
        return None
    s = s.strip().rstrip("s").strip()
    try:
        if "/" in s:
            a, b = s.split("/", 1)
            return float(a) / float(b)
        return float(s)
    except (ValueError, ZeroDivisionError):
        return None


def _iso_bucket(iso: int) -> str:
    for step in ISO_STEPS:
        if iso <= step * 1.19:
            return f"ISO {step:,}"
    return f"ISO {ISO_STEPS[-1]:,}+"


def _shutter_bucket(sec: float) -> str:
    for limit, name in SHUTTER_STEPS:
        if sec <= limit * 1.01:
            return name
    return SHUTTER_STEPS[-1][1]


def _stop(f: float) -> str:
    return f"f/{min(STOPS, key=lambda s: abs(s - f)):g}"


BY = {"lens": "Lens", "camera": "Camera", "focal": "Focal length", "aperture": "Aperture", "iso": "ISO",
      "shutter": "Shutter speed"}


def keeper_rate(conn: sqlite3.Connection, by: str) -> list[Row]:
    """Photos and keepers per lens / camera / focal length / aperture / ISO / shutter."""
    if by in ("lens", "camera"):
        col = "e.lens" if by == "lens" else "e.camera_model"
        rows = conn.execute(f"SELECT {col}, COUNT(*), {KEEP} {FROM} WHERE {PHOTOS} AND {col} IS NOT NULL"
                            f" AND TRIM({col}) != '' GROUP BY {col} ORDER BY COUNT(*) DESC, {col}").fetchall()
        return [Row(b, n, k or 0) for b, n, k in rows]
    col = {"focal": "e.focal_length_mm", "aperture": "e.aperture", "iso": "e.iso", "shutter": "e.shutter_speed"}[by]
    acc: dict[str, list[int]] = {}
    order: dict[str, float] = {}
    for v, n, k in conn.execute(f"SELECT {col}, COUNT(*), {KEEP} {FROM} WHERE {PHOTOS} AND {col} IS NOT NULL"
                                f" GROUP BY {col}"):
        if by == "focal":
            if not v:
                continue
            key, sort = f"{round(v):g} mm", round(v)
        elif by == "aperture":
            if not v:
                continue
            key = _stop(float(v))
            sort = float(key[2:])
        elif by == "iso":
            if not v:
                continue
            key = _iso_bucket(int(v))
            sort = int(key[4:].replace(",", "").rstrip("+"))
        else:
            sec = _shutter_seconds(v)
            if sec is None:
                continue
            key = _shutter_bucket(sec)
            sort = next(i for i, (_, name) in enumerate(SHUTTER_STEPS) if name == key)
        a = acc.setdefault(key, [0, 0])
        a[0] += n
        a[1] += k or 0
        order[key] = sort
    return [Row(b, n, k) for b, (n, k) in sorted(acc.items(), key=lambda kv: order[kv[0]])]


def focal_use(conn: sqlite3.Connection, lens: str) -> list[Row]:
    """How often each focal length was used on this lens (zooms: where you really shoot)."""
    rows = conn.execute(f"SELECT ROUND(e.focal_length_mm), COUNT(*), {KEEP} {FROM} WHERE {PHOTOS} AND e.lens = ?"
                        f" AND e.focal_length_mm IS NOT NULL GROUP BY ROUND(e.focal_length_mm)"
                        f" ORDER BY ROUND(e.focal_length_mm)", (lens,)).fetchall()
    return [Row(f"{fl:g} mm", n, k or 0) for fl, n, k in rows]


def per_month(conn: sqlite3.Connection, years: int = 3) -> list[tuple[str, int, int]]:
    """[(YYYY-MM, photos, shoots)] for the last `years` years that have photos.
    A shoot is a day you took photos."""
    rows = conn.execute(f"SELECT SUBSTR(e.captured_at, 1, 7), COUNT(*), COUNT(DISTINCT SUBSTR(e.captured_at, 1, 10))"
                        f" {FROM} WHERE {PHOTOS} AND e.captured_at IS NOT NULL GROUP BY 1 ORDER BY 1").fetchall()
    if not rows:
        return []
    last_year = int(rows[-1][0][:4])
    return [tuple(r) for r in rows if int(r[0][:4]) > last_year - years]


@dataclass
class Habits:
    photos: int
    keepers: int
    camera: str | None
    lens: str | None
    focal: str | None
    hour: int | None              # busiest hour of the day, 0-23
    weekday: str | None
    days: int                     # days with photos

    @property
    def rate(self) -> float:
        return self.keepers / self.photos if self.photos else 0.0


WEEKDAYS = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")


def habits(conn: sqlite3.Connection, year: int | None = None) -> Habits:
    where = PHOTOS + (" AND SUBSTR(e.captured_at, 1, 4) = ?" if year else "")
    p = (str(year),) if year else ()

    def top(col: str):
        r = conn.execute(f"SELECT {col}, COUNT(*) {FROM} WHERE {where} AND {col} IS NOT NULL AND {col} != ''"
                         f" GROUP BY {col} ORDER BY COUNT(*) DESC LIMIT 1", p).fetchone()
        return r[0] if r else None
    n, k, days = conn.execute(f"SELECT COUNT(*), {KEEP}, COUNT(DISTINCT SUBSTR(e.captured_at, 1, 10)) {FROM}"
                              f" WHERE {where}", p).fetchone()
    focal = top("ROUND(e.focal_length_mm)")
    hour = top("CAST(SUBSTR(e.captured_at, 12, 2) AS INTEGER)")
    wd = top("CAST(STRFTIME('%w', SUBSTR(e.captured_at, 1, 10)) AS INTEGER)")
    return Habits(n or 0, k or 0, top("e.camera_model"), top("e.lens"),
                  f"{focal:g} mm" if focal else None, hour, WEEKDAYS[wd] if wd is not None else None, days or 0)


def years(conn: sqlite3.Connection) -> list[int]:
    return [int(r[0]) for r in conn.execute(
        f"SELECT DISTINCT SUBSTR(e.captured_at, 1, 4) {FROM} WHERE {PHOTOS} AND e.captured_at IS NOT NULL"
        f" ORDER BY 1 DESC") if r[0] and r[0].isdigit()]


def recap_image(conn: sqlite3.Connection, year: int, size: tuple[int, int] = (1080, 1350)):
    """A picture of the year in numbers - shared like any photo (Create's folder)."""
    from PIL import Image, ImageDraw
    from lunelis.create.batch import _font
    h = habits(conn, year)
    lenses = [r for r in keeper_rate_year(conn, "lens", year)][:5]
    W, H = size
    img = Image.new("RGB", size, (24, 24, 32))
    d = ImageDraw.Draw(img)
    accent = (139, 140, 242)
    d.text((80, 90), f"{year}", font=_font(150), fill=(255, 255, 255))
    d.text((84, 270), "your year in photos", font=_font(44), fill=(170, 170, 190))
    y = 380
    for big, small in ((f"{h.photos:,}", "photos"), (f"{h.days:,}", "days out shooting"),
                       (f"{h.keepers:,}", f"keepers ({h.rate * 100:.0f} % picked)")):
        d.text((80, y), big, font=_font(84), fill=accent)
        d.text((80, y + 100), small, font=_font(34), fill=(200, 200, 215))
        y += 170
    y += 10
    d.text((80, y), "Most used lenses", font=_font(38), fill=(255, 255, 255))
    y += 60
    most = max((r.photos for r in lenses), default=1)
    for r in lenses:
        d.rounded_rectangle((80, y, 80 + int((W - 160) * r.photos / most), y + 34), 8, fill=accent)
        d.text((92, y + 2), f"{r.bucket}  ·  {r.photos:,}", font=_font(26), fill=(20, 20, 28))
        y += 50
    foot = " · ".join(x for x in (h.camera, h.focal and f"favourite {h.focal}",
                                   h.weekday and f"busiest on {h.weekday}s") if x)
    d.text((80, H - 90), foot, font=_font(28), fill=(170, 170, 190))
    return img


def keeper_rate_year(conn: sqlite3.Connection, by: str, year: int) -> list[Row]:
    col = {"lens": "e.lens", "camera": "e.camera_model"}[by]
    rows = conn.execute(f"SELECT {col}, COUNT(*), {KEEP} {FROM} WHERE {PHOTOS} AND {col} IS NOT NULL"
                        f" AND TRIM({col}) != '' AND SUBSTR(e.captured_at, 1, 4) = ? GROUP BY {col}"
                        f" ORDER BY COUNT(*) DESC, {col}", (str(year),)).fetchall()
    return [Row(b, n, k or 0) for b, n, k in rows]
