"""Step 4: thumbnails come from the cheapest adequate source, upright, and failures are recorded."""
import io
import os
import shutil
import struct

import piexif
import pytest
from PIL import Image

from lunelis.catalog.schema import open_catalog
from lunelis.importers.metadata import extract_pending
from lunelis.importers.scan import add_root, scan_root
from lunelis.raw.previews import best_preview, tiff_jpeg_candidates
from lunelis.raw.thumbnails import THUMB_EDGE, generate_pending, pending_count, render


def jpeg(size, color=(200, 80, 40), exif=None) -> bytes:
    """A JPEG that compresses like a photo: flat colour plus noise. (A flat
    fill compresses to <1 KB, below the picker's floor for icon-sized previews.)"""
    buf = io.BytesIO()
    kw = {"exif": piexif.dump(exif)} if exif else {}
    noise = Image.effect_noise(size, 40).convert("RGB")
    Image.blend(Image.new("RGB", size, color), noise, 0.25).save(buf, "JPEG", quality=90, **kw)
    return buf.getvalue()


def fake_tiff_raw(previews: list[bytes]) -> bytes:
    """A little-endian TIFF whose IFD0 points at previews[0] via
    JPEGInterchangeFormat, and whose SubIFDs point at the rest - the layout
    Sony/Nikon RAWs use."""
    blobs_at = 8 + 2 + 12 * 3 + 4 + 4 * len(previews)  # header + IFD0 + SubIFD pointer array
    sub_ifds_at = []
    body = b""
    offsets = []
    cursor = blobs_at
    for p in previews:
        offsets.append(cursor)
        body += p
        cursor += len(p)
    sub_bytes = b""
    for off, p in list(zip(offsets, previews))[1:]:
        sub_ifds_at.append(cursor + len(sub_bytes))
        sub_bytes += struct.pack("<H", 2)
        sub_bytes += struct.pack("<HHII", 0x0201, 4, 1, off)
        sub_bytes += struct.pack("<HHII", 0x0202, 4, 1, len(p))
        sub_bytes += struct.pack("<I", 0)
    ptr_array_at = 8 + 2 + 12 * 3 + 4
    ifd0 = struct.pack("<H", 3)
    ifd0 += struct.pack("<HHII", 0x0201, 4, 1, offsets[0])
    ifd0 += struct.pack("<HHII", 0x0202, 4, 1, len(previews[0]))
    n = len(sub_ifds_at)
    ifd0 += struct.pack("<HHII", 0x014A, 4, n, sub_ifds_at[0] if n == 1 else ptr_array_at)
    ifd0 += struct.pack("<I", 0)
    ptrs = struct.pack(f"<{len(previews)}I", *(sub_ifds_at + [0] * (len(previews) - n)))
    return b"II*\x00" + struct.pack("<I", 8) + ifd0 + ptrs + body + sub_bytes


@pytest.fixture
def three_previews():
    # Deliberately out of size order in the file.
    return [jpeg((1600, 1200), (10, 200, 10)), jpeg((160, 120)), jpeg((640, 480), (10, 10, 200))]


def test_picks_smallest_preview_that_is_big_enough(tmp_path, three_previews):
    p = tmp_path / "DSC0001.ARW"
    p.write_bytes(fake_tiff_raw(three_previews))
    with open(p, "rb") as fh:
        cands = tiff_jpeg_candidates(fh)
        assert len(cands) == 3                     # all three previews found, IFD0 + SubIFDs
        img = best_preview(fh, cands, 512)
        img.load()
    assert img.getpixel((5, 5))[2] > 150           # the blue 640x480 one, not the 1600 green one


def test_raw_thumbnail_uses_catalog_orientation(tmp_path, three_previews):
    p = tmp_path / "DSC0001.ARW"
    p.write_bytes(fake_tiff_raw(three_previews))
    assert render(str(p)).size == (512, 384)
    assert render(str(p), orientation=8).size == (384, 512)
    assert render(str(p), orientation=6).size == (384, 512)


def test_raw_with_only_a_tiny_preview_falls_back_to_largest_decodable(tmp_path):
    p = tmp_path / "old.NEF"
    p.write_bytes(fake_tiff_raw([jpeg((320, 240)), jpeg((160, 120))]))
    assert render(str(p)).size == (320, 240)       # upscaling a preview would just blur it


def test_standard_jpeg_obeys_its_own_orientation(tmp_path):
    p = tmp_path / "phone.jpg"
    p.write_bytes(jpeg((2000, 1500), exif={"0th": {piexif.ImageIFD.Orientation: 6}}))
    assert render(str(p)).size == (384, 512)


def test_png_with_alpha_becomes_rgb(tmp_path):
    p = tmp_path / "icon.png"
    Image.new("RGBA", (800, 400), (255, 0, 0, 0)).save(p)
    img = render(str(p))
    assert img.mode == "RGB" and img.size == (512, 256)


def test_jpeg_named_arw_is_rendered_as_jpeg(tmp_path):
    p = tmp_path / "_MAR6692.ARW"                 # Takeout's re-saved JPEG
    p.write_bytes(jpeg((1024, 768)))
    assert render(str(p)).size == (512, 384)


def test_heic(tmp_path):
    pillow_heif = pytest.importorskip("pillow_heif")
    p = tmp_path / "IMG_0001.HEIC"
    try:
        pillow_heif.from_pillow(Image.new("RGB", (1024, 768), (0, 120, 255))).save(p, quality=80)
    except Exception as e:                         # encoder not in this build
        pytest.skip(f"no HEIC encoder: {e}")
    assert render(str(p)).size == (512, 384)


@pytest.fixture
def library(tmp_path, three_previews):
    root = tmp_path / "Photos"
    root.mkdir()
    (root / "a.jpg").write_bytes(jpeg((1200, 900)))
    (root / "b.ARW").write_bytes(fake_tiff_raw(three_previews))
    (root / "clip.mp4").write_bytes(b"\0\0\0\x18ftypisom" + b"\0" * 32)
    (root / "junk.jpg").write_bytes(b"definitely not an image")
    conn = open_catalog(tmp_path / "lunelis.db")
    root_id = add_root(conn, root)
    scan_root(conn, root_id)
    extract_pending(conn)
    yield conn, root, root_id, tmp_path / "thumbs"
    conn.close()


def test_generate_writes_cache_and_records_failures(library):
    conn, _, _, cache = library
    r = generate_pending(conn, cache)
    assert (r.made, r.failed) == (2, 2)
    rows = {f: (t, e) for f, t, e in conn.execute(
        "SELECT filename, thumbnail_path, thumb_error FROM files")}
    for name in ("a.jpg", "b.ARW"):
        thumb, err = rows[name]
        assert err is None and (cache / thumb).is_file()
        with Image.open(cache / thumb) as im:
            assert max(im.size) == THUMB_EDGE
    assert rows["clip.mp4"][0] is None and rows["clip.mp4"][1]      # junk bytes: a real decode error
    assert rows["junk.jpg"][0] is None and rows["junk.jpg"][1]
    assert pending_count(conn) == 0                # failures are not retried


def test_changed_file_gets_a_new_thumbnail(library):
    conn, root, root_id, cache = library
    generate_pending(conn, cache)
    (root / "junk.jpg").write_bytes(jpeg((600, 600)))  # the broken file gets fixed
    scan_root(conn, root_id)
    assert pending_count(conn) == 1
    assert generate_pending(conn, cache).made == 1


def test_purged_cache_is_rebuilt(library):
    conn, _, _, cache = library
    generate_pending(conn, cache)
    shutil.rmtree(cache)
    r = generate_pending(conn, cache)
    assert r.made == 2 and len(list(cache.rglob("*.jpg"))) == 2


def test_parallel_matches_serial(library, tmp_path):
    conn, _, _, cache = library
    generate_pending(conn, cache, workers=1)
    serial = sorted(p.name for p in cache.rglob("*.jpg"))
    shutil.rmtree(cache)
    generate_pending(conn, cache, workers=8)
    assert sorted(p.name for p in cache.rglob("*.jpg")) == serial


REAL_ARW = r"E:\3-14-2026\_A756538.ARW"


@pytest.mark.skipif(not os.path.exists(REAL_ARW), reason="real sample library not attached")
def test_real_portrait_arw():
    # Shot in portrait: EXIF orientation 8, embedded preview stored landscape.
    assert render(REAL_ARW, orientation=8).size == (342, 512)


def camera_jpeg_with_mpf(main_size, preview_size, orientation=1) -> bytes:
    """A JPEG with an MPF 'Large Thumbnail', like Sony in-camera JPEGs.
    Pillow writes the second frame's MPType as Undefined, so the 4-byte
    attribute is patched to 0x010002 (Large Thumbnail, Full HD)."""
    main = Image.new("RGB", main_size, (200, 30, 30))           # red main image
    prev = Image.new("RGB", preview_size, (30, 30, 200))        # blue preview
    buf = io.BytesIO()
    exif = piexif.dump({"0th": {piexif.ImageIFD.Orientation: orientation}})
    main.save(buf, "MPO", save_all=True, append_images=[prev], exif=exif)
    data = bytearray(buf.getvalue())
    tiff = data.find(b"MPF\x00") + 4                             # MPF's own little TIFF header
    ifd = tiff + struct.unpack_from("<I", data, tiff + 4)[0]
    for i in range(struct.unpack_from("<H", data, ifd)[0]):
        tag, _, _, value = struct.unpack_from("<HHII", data, ifd + 2 + 12 * i)
        if tag == 0xB002:                                        # MP Entry array
            struct.pack_into("<I", data, tiff + value + 16, 0x010002)   # entry 1 -> Large Thumbnail
            return bytes(data)
    raise AssertionError("no MP Entry tag")


def test_camera_jpeg_uses_mpf_preview_not_main_image(tmp_path):
    p = tmp_path / "DSC01513.JPG"
    p.write_bytes(camera_jpeg_with_mpf((4000, 3000), (1600, 1200)))
    img = render(str(p))
    assert img.size == (512, 384)
    assert img.getpixel((10, 10))[2] > 150                       # blue: came from the preview


def test_mpf_preview_gets_the_main_images_orientation(tmp_path):
    p = tmp_path / "portrait.JPG"
    p.write_bytes(camera_jpeg_with_mpf((4000, 3000), (1600, 1200), orientation=8))
    assert render(str(p)).size == (384, 512)


def test_mpf_preview_too_small_falls_back_to_main_image(tmp_path):
    p = tmp_path / "old.JPG"
    p.write_bytes(camera_jpeg_with_mpf((4000, 3000), (320, 240)))
    img = render(str(p))
    assert img.size == (512, 384) and img.getpixel((10, 10))[0] > 150   # red: main image
