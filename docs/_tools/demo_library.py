"""
Build a FAKE demo library for the manual's screenshots - generated images
with invented EXIF. No real photo, person, place or company data.

    .venv/Scripts/python docs/_tools/demo_library.py <work folder>

Creates <work>/Demo Library (the main source), <work>/Old Backup Drive
(copies, for Duplicates), <work>/Backup Drive (a backup destination) and
<work>/data (Lunelis's data folder: catalog, thumbnails, caches). Point
LUNELIS_DATA_DIR at <work>/data before importing lunelis.
"""
from __future__ import annotations

import math
import os
import random
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import piexif
from PIL import Image, ImageDraw, ImageFilter

W, H = 1800, 1200


def _sky(rng, top, bottom):
    t = np.linspace(0, 1, H, dtype=np.float32)[:, None, None]
    return (np.array(top, np.float32) * (1 - t) + np.array(bottom, np.float32) * t) * np.ones((1, W, 1), np.float32)


def _hills(img: np.ndarray, rng, colour, base, amp, freq):
    x = np.arange(W)
    phase = rng.uniform(0, 6)
    ridge = base + amp * np.sin(x / W * freq * math.pi + phase) + amp * 0.4 * np.sin(x / W * freq * 3.1 + phase * 2)
    yy = np.arange(H)[:, None]
    mask = yy > ridge[None, :]
    shade = (np.array(colour, np.float32) * (0.85 + 0.15 * rng.random((H, W, 1)))).astype(np.float32)
    img[mask] = shade[mask]


def landscape(rng) -> Image.Image:
    img = _sky(rng, (60 + rng.integers(40), 120 + rng.integers(60), 200 + rng.integers(50)), (230, 200, 170))
    for colour, base, amp in (((70, 90, 120), 620, 90), ((50, 90, 60), 760, 70), ((35, 70, 40), 900, 50)):
        _hills(img, rng, colour, base + rng.integers(-40, 40), amp, rng.uniform(1.5, 3.5))
    pil = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(pil)
    sx, sy = rng.integers(200, W - 200), rng.integers(120, 420)
    d.ellipse([sx - 60, sy - 60, sx + 60, sy + 60], fill=(255, 235, 180))
    return pil.filter(ImageFilter.GaussianBlur(1.2))


def city(rng) -> Image.Image:
    img = _sky(rng, (120, 150, 190), (220, 215, 210))
    pil = Image.fromarray(img.astype(np.uint8))
    d = ImageDraw.Draw(pil)
    x = 0
    while x < W:
        w, h = int(rng.integers(80, 220)), int(rng.integers(250, 800))
        tone = int(rng.integers(60, 140))
        d.rectangle([x, H - h, x + w, H], fill=(tone, tone + 5, tone + 15))
        for wy in range(H - h + 20, H - 20, 40):
            for wx in range(x + 12, x + w - 20, 30):
                if rng.random() < 0.55:
                    d.rectangle([wx, wy, wx + 14, wy + 20], fill=(255, 230, 150) if rng.random() < 0.4 else (40, 50, 70))
        x += w + int(rng.integers(4, 20))
    return pil


def lights(rng) -> Image.Image:
    pil = Image.new("RGB", (W, H), (12, 14, 30))
    d = ImageDraw.Draw(pil)
    for _ in range(260):
        x, y, r = int(rng.integers(0, W)), int(rng.integers(0, H)), int(rng.integers(4, 22))
        c = [(255, 80, 80), (80, 255, 120), (255, 210, 90), (120, 160, 255)][int(rng.integers(4))]
        d.ellipse([x - r, y - r, x + r, y + r], fill=c)
    return pil.filter(ImageFilter.GaussianBlur(3))


def exif(taken: datetime, subsec: str, camera: str, lens: str, iso: int, gps: tuple[float, float] | None) -> bytes:
    def rat(v, d=100):
        return (int(round(v * d)), d)

    def dms(v):
        v = abs(v)
        d = int(v)
        m = int((v - d) * 60)
        return ((d, 1), (m, 1), rat((v - d - m / 60) * 3600, 100))
    zeroth = {piexif.ImageIFD.Make: b"SONY", piexif.ImageIFD.Model: camera.encode(),
              piexif.ImageIFD.Artist: b"Demo Photographer", piexif.ImageIFD.Software: b"Demo firmware 1.00"}
    ex = {piexif.ExifIFD.DateTimeOriginal: taken.strftime("%Y:%m:%d %H:%M:%S").encode(),
          piexif.ExifIFD.SubSecTimeOriginal: subsec.encode(), piexif.ExifIFD.OffsetTimeOriginal: b"-05:00",
          piexif.ExifIFD.LensModel: lens.encode(), piexif.ExifIFD.FNumber: rat(4.0),
          piexif.ExifIFD.ExposureTime: (1, 250), piexif.ExifIFD.ISOSpeedRatings: iso,
          piexif.ExifIFD.FocalLength: rat(35), piexif.ExifIFD.ExposureProgram: 3,
          piexif.ExifIFD.MeteringMode: 5, piexif.ExifIFD.Flash: 16, piexif.ExifIFD.WhiteBalance: 0}
    gps_ifd = {}
    if gps:
        gps_ifd = {piexif.GPSIFD.GPSLatitudeRef: b"N" if gps[0] >= 0 else b"S", piexif.GPSIFD.GPSLatitude: dms(gps[0]),
                   piexif.GPSIFD.GPSLongitudeRef: b"E" if gps[1] >= 0 else b"W", piexif.GPSIFD.GPSLongitude: dms(gps[1])}
    return piexif.dump({"0th": zeroth, "Exif": ex, "GPS": gps_ifd, "1st": {}, "thumbnail": None})


SHOOTS = [  # folder, first frame, count, scene, camera, lens, iso, gps (invented)
    ("2024/06-14 Lakeside Weekend", datetime(2024, 6, 14, 9, 12), 8, landscape, "ILCE-7RM5", "FE 24-105mm F4 G OSS", 100, (45.10, -85.20)),
    ("2024/12-24 Holiday Lights", datetime(2024, 12, 24, 19, 40), 6, lights, "ILCE-7RM5", "FE 85mm F1.8", 3200, None),
    ("2025/03-02 City Walk", datetime(2025, 3, 2, 14, 5), 8, city, "ILCE-7M4", "FE 16-35mm F2.8 GM", 200, (40.10, -83.00)),
    ("2025/08-19 Mountain Trip", datetime(2025, 8, 19, 7, 30), 10, landscape, "ILCE-7RM5", "FE 100-400mm F4.5-5.6 GM OSS", 400, (44.30, -110.60)),
]


def build(work: Path) -> dict:
    rng = np.random.default_rng(42)
    random.seed(42)
    if work.exists():
        shutil.rmtree(work)
    lib, old, backup = work / "Demo Library", work / "Old Backup Drive", work / "Backup Drive"
    for d in (lib, old, backup):
        d.mkdir(parents=True)
    n = 1
    made: list[Path] = []
    for folder, start, count, scene, camera, lens, iso, gps in SHOOTS:
        (lib / folder).mkdir(parents=True, exist_ok=True)
        for i in range(count):
            taken = start + timedelta(minutes=7 * i)
            p = lib / folder / f"DSC{n:05d}.JPG"
            scene(rng).save(p, "JPEG", quality=88, exif=exif(taken, "000", camera, lens, iso, gps))
            made.append(p)
            n += 1
    # A burst: five frames a fifth of a second apart.
    base = landscape(rng)
    burst_t = datetime(2024, 6, 14, 11, 0, 5)
    for k in range(5):
        p = lib / "2024/06-14 Lakeside Weekend" / f"DSC{n:05d}.JPG"
        base.rotate(k * 0.6, resample=Image.Resampling.BICUBIC).save(
            p, "JPEG", quality=88, exif=exif(burst_t, f"{k * 200:03d}", "ILCE-7RM5", "FE 24-105mm F4 G OSS", 100, (45.10, -85.20)))
        n += 1
    # An old backup drive: three exact copies and one resized (near-duplicate) copy.
    (old / "2024/06-14 Lakeside Weekend").mkdir(parents=True)
    for p in made[:3]:
        shutil.copy2(p, old / "2024/06-14 Lakeside Weekend" / p.name)
    with Image.open(made[3]) as im:
        im.resize((W // 2, H // 2)).save(old / "2024/06-14 Lakeside Weekend" / made[3].name, "JPEG", quality=80,
                                         exif=im.info.get("exif"))
    # A damaged file: a camera JPEG whose contents are all zeros.
    (lib / "2025/03-02 City Walk" / "DSC09999.JPG").write_bytes(b"\0" * 200_000)
    return {"lib": lib, "old": old, "backup": backup}


def catalog(work: Path, where: dict) -> None:
    """Catalog the demo library through Lunelis's own code (LUNELIS_DATA_DIR
    must already point at <work>/data)."""
    from lunelis import paths, search, stacks
    from lunelis.albums import model as albums
    from lunelis.backups.core import create_set
    from lunelis.catalog import ratings
    from lunelis.catalog.schema import open_catalog
    from lunelis.damage.check import check as check_damage
    from lunelis.dupes import detect, similar
    from lunelis.dupes.quarantine import quarantine
    from lunelis.edit import render, store
    from lunelis.edit.stack import Geometry, Stack
    from lunelis.events import model as events
    from lunelis.importers.metadata import extract_pending
    from lunelis.importers.scan import add_root, scan_root
    from lunelis.raw import thumbnails
    from lunelis.settings import Settings
    from lunelis.tags import model as tags

    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = open_catalog(paths.DEFAULT_CATALOG_PATH)
    s = Settings(conn)
    s.set("import_destination", str(where["lib"]))           # never the real library's default
    s.set("import_staging_network", r"\\demo-nas\photos\Lunelis staging")   # invented, never a real share
    s.set("theme", "graphite")
    lib_id, old_id = add_root(conn, where["lib"]), add_root(conn, where["old"])
    for rid in (lib_id, old_id):
        scan_root(conn, rid)
    extract_pending(conn)
    thumbnails.generate_pending(conn, paths.THUMBNAIL_CACHE)
    detect.process_folder(conn, lib_id, "2024/06-14 Lakeside Weekend")
    for (gid,) in conn.execute("SELECT id FROM duplicate_groups").fetchall():
        detect.verify_group(conn, gid)
    similar.refresh(conn, paths.THUMBNAIL_CACHE)
    stacks.rebuild(conn)
    check_damage(conn)

    def ids(folder):
        return [r[0] for r in conn.execute(
            "SELECT id FROM files WHERE root_id = ? AND rel_path LIKE ? ORDER BY filename", (lib_id, folder + "/%"))]
    lake, lights_, city_, mountain = (ids(f) for f in (SHOOTS[0][0], SHOOTS[1][0], SHOOTS[2][0], SHOOTS[3][0]))
    ratings.set_ratings(conn, lake[:3], stars=5)
    ratings.set_ratings(conn, lake[3:6], stars=4, label="Green")
    ratings.set_ratings(conn, mountain[:4], stars=3, flag="pick")
    ratings.set_ratings(conn, city_[:1], flag="reject")
    ratings.set_ratings(conn, lights_[:2], label="Red")
    tags.add(conn, lake, ["Places > Lakeside", "Seasons > Summer"])
    tags.add(conn, mountain, ["Places > Mountains", "Seasons > Summer", "Hiking"])
    tags.add(conn, lights_, ["Seasons > Winter", "Holidays"])
    tags.add(conn, city_[:5], ["Places > City", "Architecture"])
    tags.remember_recent(conn, ["Hiking", "Places|Mountains"])
    a = albums.create(conn, "Best of 2024", lake[:4] + lights_[:2])
    albums.create(conn, "Wallpapers", mountain[:5])
    albums.set_cover(conn, a, lake[1])
    events.create(conn, "Lakeside Weekend", lake)
    events.create(conn, "Mountain Trip", mountain)
    # One edited photo: a filter, some light, a crop.
    edited = mountain[1]
    store.save(conn, edited, Stack("Vivid", 70, {"exposure": 0.3, "shadows": 20},
                                   Geometry(crop=(0.05, 0.05, 0.95, 0.9))))
    path = conn.execute("SELECT r.path, f.rel_path FROM files f JOIN roots r ON r.id = f.root_id WHERE f.id = ?",
                        (edited,)).fetchone()
    render.render_outputs(os.path.join(path[0], *path[1].split("/")), False, edited, store.get(conn, edited),
                          store.filter_params(conn, "Vivid"), paths.THUMBNAIL_CACHE, paths.EDIT_CACHE)
    # Set aside one exact copy (for the Quarantine page).
    g = conn.execute("SELECT id FROM duplicate_groups WHERE method = 'exact' ORDER BY id LIMIT 1").fetchone()
    if g:
        copy = conn.execute("SELECT f.id FROM duplicate_group_files m JOIN files f ON f.id = m.file_id"
                            " WHERE m.group_id = ? AND f.root_id = ?", (g[0], old_id)).fetchone()
        if copy:
            quarantine(conn, g[0], [copy[0]])
    create_set(conn, "USB backup drive", str(where["backup"]), [lib_id])
    search.refresh(conn)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    work = Path(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.environ["TEMP"], "lunelis-docs-demo"))
    os.environ["LUNELIS_DATA_DIR"] = str(work / "data")
    where = build(work)
    catalog(work, where)
    print("demo library ready:", work)
