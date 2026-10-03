"""
Merging several photos into a new one: HDR and panoramas (OpenCV).

- HDR: bracketed exposures of one scene. The frames are aligned (median
  threshold bitmaps - robust to exposure differences, handles hand-held
  shifts) and blended by exposure fusion (Mertens): each pixel comes
  mostly from the frames where it's well exposed. No exposure times are
  needed, and the result is a normal, viewable picture - saved as a
  16-bit TIFF (or JPEG), ready to edit like any photo.
- Panorama: overlapping frames stitched by OpenCV's panorama stitcher
  (features, bundle adjustment, seam finding, blending), then cropped to
  the largest rectangle with no empty edges (optional).

The sources are only read. The result is a new file where you choose to
save it; if that's inside a library folder it's cataloged at once, with
the sources' date and camera, and recorded in `merges`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

import numpy as np

HDR_MAX, PANO_MAX = 9, 30


@dataclass
class MergeOptions:
    kind: str                              # hdr | panorama
    path: str                              # where to save
    format: str = "tiff"                   # tiff | jpeg
    align: bool = True                     # HDR
    half_size: bool = True                 # panorama: work at half size (much faster, less memory)
    crop: bool = True                      # panorama: crop the empty edges away


class MergeFailed(Exception):
    """The photos couldn't be merged (said in words a person can act on)."""


def _u8(a: np.ndarray) -> np.ndarray:
    return (np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8)


def merge_hdr(images: list[np.ndarray], align: bool = True) -> np.ndarray:
    """Exposure-fuse float32 sRGB frames of the same size -> float32."""
    import cv2
    if len(images) < 2:
        raise MergeFailed("HDR needs at least two photos of the same scene at different exposures.")
    h, w = images[0].shape[:2]
    if any(im.shape[:2] != (h, w) for im in images):
        raise MergeFailed("The photos aren't all the same size - HDR needs frames from one camera setting.")
    frames = [np.ascontiguousarray(im, dtype=np.float32) for im in images]
    if align:
        mtb = cv2.createAlignMTB(max_bits=6, exclude_range=4, cut=True)
        ref = cv2.cvtColor(_u8(frames[len(frames) // 2]), cv2.COLOR_RGB2GRAY)
        for i, f in enumerate(frames):
            if i == len(frames) // 2:
                continue
            dx, dy = mtb.calculateShift(ref, cv2.cvtColor(_u8(f), cv2.COLOR_RGB2GRAY))
            if dx or dy:
                m = np.float32([[1, 0, -dx], [0, 1, -dy]])
                frames[i] = cv2.warpAffine(f, m, (w, h), borderMode=cv2.BORDER_REPLICATE)
    # OpenCV divides float input by 255 (as if it were 8-bit): scale up, keep the precision.
    fused = cv2.createMergeMertens().process([f * 255 for f in frames])
    return np.clip(fused, 0, 1).astype(np.float32)


def _crop_to_fill(img: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """The biggest axis-aligned rectangle made only of valid pixels,
    found by shrinking the side with the most invalid pixels."""
    y0, y1, x0, x1 = 0, valid.shape[0], 0, valid.shape[1]
    while y1 - y0 > 10 and x1 - x0 > 10:
        sub = valid[y0:y1, x0:x1]
        bad = {"top": (~sub[0]).mean(), "bottom": (~sub[-1]).mean(),
               "left": (~sub[:, 0]).mean(), "right": (~sub[:, -1]).mean()}
        side, worst = max(bad.items(), key=lambda kv: kv[1])
        if worst == 0:
            break
        step = max(1, int(min(y1 - y0, x1 - x0) * 0.004))
        if side == "top":
            y0 += step
        elif side == "bottom":
            y1 -= step
        elif side == "left":
            x0 += step
        else:
            x1 -= step
    return img[y0:y1, x0:x1]


def merge_panorama(images: list[np.ndarray], crop: bool = True) -> np.ndarray:
    import cv2
    if len(images) < 2:
        raise MergeFailed("A panorama needs at least two overlapping photos.")
    stitcher = cv2.Stitcher.create(cv2.Stitcher_PANORAMA)
    status, pano = stitcher.stitch([cv2.cvtColor(_u8(im), cv2.COLOR_RGB2BGR) for im in images])
    if status != cv2.Stitcher_OK:
        why = {cv2.Stitcher_ERR_NEED_MORE_IMGS: "the photos don't overlap enough (aim for about a third)",
               cv2.Stitcher_ERR_HOMOGRAPHY_EST_FAIL: "the overlaps didn't line up - is it one scene?",
               cv2.Stitcher_ERR_CAMERA_PARAMS_ADJUST_FAIL: "the camera positions couldn't be worked out"}
        raise MergeFailed(f"Couldn't stitch these photos: {why.get(status, f'error {status}')}.")
    rgb = cv2.cvtColor(pano, cv2.COLOR_BGR2RGB).astype(np.float32) / 255
    if crop:
        valid = pano.max(axis=2) > 0
        valid = cv2.erode(valid.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
        rgb = _crop_to_fill(rgb, valid)
    return rgb


def save(img: np.ndarray, path: str, fmt: str, exif: bytes | None = None, tiffinfo: dict | None = None) -> None:
    """16-bit TIFF (lossless, headroom for editing) or a high-quality JPEG."""
    from PIL import Image
    from lunelis.edit.export import _srgb_icc
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    if os.path.exists(path):
        raise MergeFailed(f"{os.path.basename(path)} already exists - choose another name.")
    tmp = path + ".part"
    if fmt == "tiff":
        import cv2
        u16 = (np.clip(img, 0, 1) * 65535 + 0.5).astype(np.uint16)
        # Pillow can't write 16-bit RGB TIFFs; OpenCV can (BGR order).
        ok = cv2.imwrite(tmp + ".tif", cv2.cvtColor(u16, cv2.COLOR_RGB2BGR),
                         [cv2.IMWRITE_TIFF_COMPRESSION, 5])       # LZW
        if not ok:
            raise MergeFailed("Couldn't write the TIFF file.")
        os.replace(tmp + ".tif", path)
        return
    kwargs = {"quality": 95, "subsampling": 0, "icc_profile": _srgb_icc()}
    if exif:
        kwargs["exif"] = exif
    Image.fromarray(_u8(img), "RGB").save(tmp, "JPEG", **kwargs)
    os.replace(tmp, path)


def run(conn, file_ids: list[int], opts: MergeOptions,
        on_progress: Callable[[str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None) -> int | None:
    """Load, merge, save, catalog. Returns the new file's id (None if it
    was saved outside the library's folders)."""
    from lunelis.edit.render import load_source

    def say(text):
        if on_progress:
            on_progress(text)

    rows = []
    for fid in file_ids:
        r = conn.execute("SELECT r.path, f.rel_path, f.filename, f.is_raw, f.format FROM files f"
                         " JOIN roots r ON r.id = f.root_id WHERE f.id = ?", (fid,)).fetchone()
        if r and r[4] not in ("mp4", "mov", "mpeg-ts"):
            rows.append((fid, os.path.join(r[0], *r[1].split("/")), bool(r[3])))
    limit = HDR_MAX if opts.kind == "hdr" else PANO_MAX
    if not 2 <= len(rows) <= limit:
        raise MergeFailed(f"Choose 2 to {limit} photos (videos don't count).")
    images = []
    for i, (fid, path, raw) in enumerate(rows, 1):
        if should_cancel and should_cancel():
            raise MergeFailed("cancelled")
        say(f"Reading photo {i} of {len(rows)}…")
        edge = None
        if opts.kind == "panorama" and opts.half_size:
            edge = 3000
        images.append(load_source(path, raw, edge))
    say("Aligning and blending…" if opts.kind == "hdr" else "Stitching…")
    out = merge_hdr(images, opts.align) if opts.kind == "hdr" else merge_panorama(images, opts.crop)
    del images
    say("Saving…")
    from lunelis.edit.export import exif_bytes
    exif = exif_bytes(conn, rows[0][0], "all", (out.shape[1], out.shape[0])) if opts.format == "jpeg" else None
    save(out, opts.path, opts.format, exif)
    say("Adding it to the library…")
    return catalog(conn, opts.path, opts.kind, [r[0] for r in rows])


def catalog(conn, path: str, kind: str, source_ids: list[int]) -> int | None:
    from lunelis.importers.metadata import extract_pending
    from lunelis.importers.scan import catalog_file
    fid = catalog_file(conn, path)
    if fid is None:
        return None
    extract_pending(conn)
    # A TIFF from OpenCV has no EXIF: the date, camera and lens come from the first source.
    conn.execute(
        "UPDATE exif SET captured_at = COALESCE(exif.captured_at, s.captured_at),"
        " captured_offset = COALESCE(exif.captured_offset, s.captured_offset),"
        " camera_make = COALESCE(exif.camera_make, s.camera_make),"
        " camera_model = COALESCE(exif.camera_model, s.camera_model), lens = COALESCE(exif.lens, s.lens)"
        " FROM (SELECT * FROM exif WHERE file_id = ?) AS s WHERE exif.file_id = ?", (source_ids[0], fid))
    conn.execute("INSERT OR REPLACE INTO merges (file_id, kind, source_ids) VALUES (?, ?, ?)",
                 (fid, kind, ",".join(map(str, source_ids))))
    conn.commit()
    return fid
