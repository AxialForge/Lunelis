"""
Thumbnail / proxy pipeline (Phase 1, Step 4).

One 512px JPEG per file in cache/thumbnails/, referenced by
`files.thumbnail_path`. The library grid only ever loads these: no RAW decode
and no NAS read in the scroll path.

Source, by what the bytes are (files.format / sniff, never the extension):
  RAW in a TIFF container  smallest embedded JPEG >= 512px (raw/previews.py)
  Fuji RAF                 its embedded JPEG
  anything RAW left over   rawpy: embedded thumb, then a half-size decode
  JPEG/PNG/WebP/BMP/GIF/   Pillow (JPEG via DCT-scaled draft decode);
  HEIC/AVIF/TIFF           HEIC/AVIF through pillow-heif
  video                    not yet - recorded in thumb_error

The cache is disposable: delete the folder and every thumbnail is regenerated
on the next pass. A file that can't be thumbnailed gets `thumb_error`
("preview unavailable") and isn't retried until it changes.
"""
from __future__ import annotations

import io
import os
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pillow_heif
import rawpy
from PIL import Image, ImageCms, ImageOps

from lunelis.importers.formats import SNIFF_BYTES, is_raw_content, sniff
from lunelis.raw.previews import best_preview, raf_jpeg_candidate, tiff_jpeg_candidates

pillow_heif.register_heif_opener()
Image.MAX_IMAGE_PIXELS = 400_000_000        # 100MP+ panoramas are legitimate here
# A huge image costs bytes-per-pixel times its size to decode (a 300-byte PNG
# can declare 19000 x 19000: ~3 GB). Past BIG_PIXELS only one decodes at a
# time, so eight workers can't add that up; past the cap it's refused before
# any decoding (Pillow itself only refuses at twice MAX_IMAGE_PIXELS).
BIG_PIXELS = 60_000_000
_BIG = threading.Lock()

THUMB_EDGE = 512
JPEG_QUALITY = 80
WORKERS = 8
BATCH_SIZE = 200

VIDEO_FORMATS = {"mp4", "mov", "mpeg-ts"}
ProgressFn = Callable[[int, int, str], None]
CancelFn = Callable[[], bool]

_SRGB = ImageCms.createProfile("sRGB")


@dataclass
class ThumbResult:
    made: int = 0
    failed: int = 0
    offline: int = 0                    # left for later: the share or drive was unreachable
    cancelled: bool = False
    seconds: float = 0.0


# --- one file ----------------------------------------------------------------

def _orient(img: Image.Image, orientation: int | None) -> Image.Image:
    """Apply an EXIF orientation (1-8) that lives outside the image data."""
    ops = {
        2: [Image.Transpose.FLIP_LEFT_RIGHT],
        3: [Image.Transpose.ROTATE_180],
        4: [Image.Transpose.FLIP_TOP_BOTTOM],
        5: [Image.Transpose.TRANSPOSE],
        6: [Image.Transpose.ROTATE_270],
        7: [Image.Transpose.TRANSVERSE],
        8: [Image.Transpose.ROTATE_90],
    }.get(orientation or 1, [])
    for op in ops:
        img = img.transpose(op)
    return img


def _to_srgb(img: Image.Image) -> Image.Image:
    icc = img.info.get("icc_profile")
    if icc and img.mode in ("RGB", "CMYK"):
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            return ImageCms.profileToProfile(img, src, _SRGB, outputMode="RGB")
        except Exception:
            pass                                  # a broken profile shouldn't cost the thumbnail
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (24, 24, 26))  # matches the app background
        bg.paste(img, mask=img.getchannel("A"))
        return bg
    return img.convert("RGB")


def _raw_via_rawpy(path: str) -> Image.Image:
    with rawpy.imread(path) as raw:
        try:
            th = raw.extract_thumb()
            if th.format == rawpy.ThumbFormat.JPEG:
                img = Image.open(io.BytesIO(th.data))
                img.draft("RGB", (THUMB_EDGE, THUMB_EDGE))
                img.load()
                return img
            return Image.fromarray(th.data)
        except (rawpy.LibRawNoThumbnailError, rawpy.LibRawUnsupportedThumbnailError):
            # No usable preview: demosaic at half size. Slow (~300ms on a
            # 61MP file), which is why it's the last resort.
            return Image.fromarray(raw.postprocess(half_size=True, use_camera_wb=True))


def _mpf_preview(img: Image.Image) -> Image.Image | None:
    """The MPF 'Large Thumbnail' of a camera JPEG, upright, or None.

    Sony (and most camera) JPEGs append a ~1616px preview after the main
    image, indexed by an MPF block in APP2. Reading it is 200-750 KB instead
    of the 15-35 MB main image - the difference between a disk/NAS-bound
    pass and a fast one (see CLAUDE.md, Gotchas). The preview carries no
    orientation of its own, so the primary image's is applied.
    """
    if img.format != "MPO" or getattr(img, "n_frames", 1) < 2:
        return None
    try:
        entries = img.mpinfo.get(0xB002) or []
        orientation = img.getexif().get(0x0112, 1)
        for i, entry in enumerate(entries):
            kind = entry.get("Attribute", {}).get("MPType", "")
            if i == 0 or "Thumbnail" not in kind:
                continue
            img.seek(i)
            if max(img.size) < THUMB_EDGE:
                continue
            img.draft("RGB", (THUMB_EDGE, THUMB_EDGE))
            img.load()
            frame = img.copy()
            return _orient(frame, orientation)
    except Exception:
        pass                                      # odd MPF: fall back to the main image
    # The caller falls back to the main image - which is frame 0, but seek()
    # above may have left the file on a (too small) preview frame.
    try:
        img.seek(0)
    except Exception:
        pass
    return None


def render(path: str, orientation: int | None = None, edge: int = THUMB_EDGE,
           log_preview: str = "builtin") -> Image.Image:
    """An `edge`-bounded, upright, sRGB image for one file (THUMB_EDGE for
    thumbnails; the detail view asks for about a screen's worth - a RAW then
    uses its biggest embedded preview). Raises if none. An S-Log3 clip's
    frame goes through the log preview (video/slog.py) unless that's "off"."""
    with open(path, "rb") as fh:
        fmt = sniff(fh.read(SNIFF_BYTES))
        if fmt in VIDEO_FORMATS:
            from lunelis.importers.video import poster_frame
            fh.seek(0)
            img = poster_frame(fh, edge)
            img = img.convert("RGB")
            img.thumbnail((edge, edge), Image.Resampling.LANCZOS)
            if log_preview != "off":
                from lunelis.video import slog
                lut = slog.preview_lut(path, log_preview)
                if lut is not None:
                    img = lut.apply_image(img)
            return img

        if is_raw_content(path, fmt):
            img = None
            if fmt in ("tiff", "raf"):
                cands = raf_jpeg_candidate(fh) if fmt == "raf" else tiff_jpeg_candidates(fh)
                img = best_preview(fh, cands, edge)
            if img is None:
                img = _raw_via_rawpy(path)
            # Embedded previews are stored as the sensor saw them; the
            # orientation comes from the RAW's own EXIF (catalog value).
            img = _orient(img, orientation)
        else:
            fh.seek(0)
            img = Image.open(fh)
            preview = _mpf_preview(img)
            if preview is not None:
                img = preview
            else:
                if img.format in ("JPEG", "MPO"):
                    img.draft("RGB", (edge, edge))
                w, h = img.size
                if w * h > Image.MAX_IMAGE_PIXELS:
                    raise ValueError(f"{w} x {h} pixels is more than Lunelis opens")
                if w * h > BIG_PIXELS:
                    with _BIG:                    # one huge decode at a time
                        img.load()
                        img = ImageOps.exif_transpose(img)
                        img = _to_srgb(img)
                        img.thumbnail((edge, edge), Image.Resampling.LANCZOS)
                    return img
                img.load()                        # before the file handle closes
                img = ImageOps.exif_transpose(img)  # standard images carry their own

    img = _to_srgb(img)
    img.thumbnail((edge, edge), Image.Resampling.LANCZOS)
    return img


def cache_rel_path(file_id: int) -> str:
    # ~1000 files per folder keeps Explorer and NTFS lookups fast at 160k+.
    return f"{file_id // 1000:04d}/{file_id}.jpg"


def write_thumbnail(cache_dir: Path, file_id: int, img: Image.Image) -> str:
    rel = cache_rel_path(file_id)
    dest = cache_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    img.save(tmp, "JPEG", quality=JPEG_QUALITY)
    os.replace(tmp, dest)                         # never leave a half-written thumbnail
    return rel


# --- the pending queue -----------------------------------------------------

PENDING_SQL = """
    SELECT f.id, r.path AS root, f.rel_path, e.orientation, f.is_raw, ed.stack
    FROM files f
    JOIN roots r ON r.id = f.root_id
    LEFT JOIN exif e ON e.file_id = f.id
    LEFT JOIN edits ed ON ed.file_id = f.id
    WHERE f.missing_since IS NULL AND f.excluded = 0
      AND r.enabled = 1
      AND f.thumbnail_path IS NULL
      AND f.thumb_error IS NULL
"""


def pending_count(conn: sqlite3.Connection) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM ({PENDING_SQL})").fetchone()[0]


def forget_purged_cache(conn: sqlite3.Connection, cache_dir: Path) -> int:
    """If the cache folder was deleted, clear every thumbnail_path so the
    thumbnails get rebuilt. Returns how many were cleared."""
    if cache_dir.is_dir() and any(p.is_dir() for p in cache_dir.iterdir()):
        return 0
    cur = conn.execute("UPDATE files SET thumbnail_path = NULL WHERE thumbnail_path IS NOT NULL")
    conn.commit()
    return cur.rowcount


def _make_one(cache_dir: Path, file_id: int, root: str, rel_path: str,
              orientation: int | None, is_raw: bool = False, stack: str | None = None,
              filter_params: dict | None = None, edit_cache: Path | None = None,
              log_preview: str = "builtin") -> tuple[str | None, str | None, str | None]:
    """(thumbnail rel path, error, near-duplicate fingerprint). Never raises.
    An edited photo's thumbnail shows the edit (edit/render.py). The
    fingerprint is taken from the picture still in memory (0.45), so
    "Comparing photos" doesn't read every new thumbnail back from disk."""
    from lunelis import pace
    pace.breathe()                                # a video is playing: wait for it
    try:
        path = os.path.join(root, *rel_path.split("/"))
        if stack and edit_cache is not None:
            from lunelis.edit import render as edit_render
            from lunelis.edit.stack import loads
            return edit_render.edited_thumbnail(path, bool(is_raw), file_id, loads(stack), filter_params,
                                                cache_dir, edit_cache), None, None
        img = render(path, orientation, log_preview=log_preview)
        rel = write_thumbnail(cache_dir, file_id, img)
        try:
            from lunelis.dupes.similar import dhash
            ph = dhash(img)
        except Exception:
            ph = None
        return rel, None, ph
    except Exception as e:
        from lunelis.dupes.hashing import OFFLINE, offline_error
        if offline_error(e, root):
            return None, OFFLINE + str(e), None
        return None, f"{type(e).__name__}: {e}"[:300], None


def _filter_params(conn: sqlite3.Connection, stack: str | None) -> dict | None:
    if not stack or "f=" not in stack:
        return None
    from lunelis.edit import store
    from lunelis.edit.stack import loads
    return store.filter_params(conn, loads(stack).filter)


def generate_pending(conn: sqlite3.Connection, cache_dir: Path, *,
                     on_progress: ProgressFn | None = None,
                     should_cancel: CancelFn | None = None,
                     workers: int = WORKERS, only: list[int] | None = None) -> ThumbResult:
    """Make a thumbnail for every file that needs one (or just `only` these
    files - e.g. a merge's result, without the rest of a pending pass). Same
    shape as metadata.extract_pending: parallel reads, writes on this thread,
    batch commits, resumable."""
    started = time.perf_counter()
    result = ThumbResult()
    if only is None:
        forget_purged_cache(conn, cache_dir)
        todo = conn.execute(PENDING_SQL + " ORDER BY f.root_id, f.rel_path").fetchall()
    else:
        ids = set(only)
        todo = [r for r in conn.execute(PENDING_SQL + " ORDER BY f.root_id, f.rel_path").fetchall()
                if r[0] in ids]
    edit_cache = cache_dir.parent / "edits"
    done = 0
    from lunelis.settings import Settings
    settings = Settings(conn)
    log_preview = settings.get("log_preview")
    if only is None and settings.get("log_thumbs_rev") < 1:
        # Clips filmed in S-Log3 before 0.24 have flat grey thumbnails: once, remake them.
        from lunelis.video import slog
        if slog.refresh_thumbnails(conn):
            todo = conn.execute(PENDING_SQL + " ORDER BY f.root_id, f.rel_path").fetchall()
        settings.set("log_thumbs_rev", 1)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for start in range(0, len(todo), BATCH_SIZE):
            if should_cancel and should_cancel():
                result.cancelled = True
                break
            chunk = todo[start:start + BATCH_SIZE]
            futures = [pool.submit(_make_one, cache_dir, fid, root, rel, orient, is_raw, stack,
                                   _filter_params(conn, stack), edit_cache, log_preview)
                       for fid, root, rel, orient, is_raw, stack in chunk]
            updates: list[tuple] = []
            for (file_id, _, rel_path, *_), fut in zip(chunk, futures):
                if should_cancel and should_cancel():
                    result.cancelled = True
                    for f in futures:
                        f.cancel()
                    break
                thumb, err, ph = fut.result()
                if err and err.startswith("offline: "):
                    result.offline += 1              # left pending: made when the share is back
                    done += 1
                    continue
                updates.append((thumb, err, ph, file_id))
                if thumb:
                    result.made += 1
                else:
                    result.failed += 1
                done += 1
                if on_progress and (done % 25 == 0 or done == len(todo)):
                    on_progress(done, len(todo), rel_path)
            conn.executemany(
                "UPDATE files SET thumbnail_path = ?, thumb_error = ?,"
                " perceptual_hash = COALESCE(?, perceptual_hash) WHERE id = ?", updates
            )
            conn.commit()
            if result.cancelled:
                break

    result.seconds = time.perf_counter() - started
    return result


def _main(argv: list[str]) -> int:
    import argparse

    from lunelis.catalog.schema import open_catalog
    from lunelis.paths import DEFAULT_CATALOG_PATH, THUMBNAIL_CACHE

    ap = argparse.ArgumentParser(prog="python -m lunelis.raw.thumbnails",
                                 description="Make thumbnails for every file that needs one.")
    ap.add_argument("--db", default=str(DEFAULT_CATALOG_PATH), help="catalog path")
    ap.add_argument("--cache", default=str(THUMBNAIL_CACHE), help="thumbnail cache folder")
    ap.add_argument("--workers", type=int, default=WORKERS)
    args = ap.parse_args(argv)
    conn = open_catalog(args.db)
    try:
        r = generate_pending(conn, Path(args.cache), workers=args.workers,
                             on_progress=lambda d, t, f: print(
                                 f"\r  {d:>7,}/{t:,}  {f[-60:]:<60}", end="", flush=True))
        rate = (r.made + r.failed) / r.seconds if r.seconds else 0
        print(f"\nmade {r.made:,}, preview unavailable {r.failed:,}, "
              f"in {r.seconds:.1f}s ({rate:.0f}/s)")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
