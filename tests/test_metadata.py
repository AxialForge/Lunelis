"""Step 3: EXIF extraction maps tags onto `exif`, never stops on a bad file, and is incremental."""
import io
import os
import struct
from fractions import Fraction

import piexif
import pytest
from PIL import Image

from lunelis.catalog.schema import open_catalog
from lunelis.importers.metadata import extract_pending, pending_count, read_file
from lunelis.importers.scan import add_root, scan_root


def _rat(x):
    f = Fraction(x).limit_denominator(10000)
    return (f.numerator, f.denominator)


def _dms(deg):
    d = int(deg); m = int((deg - d) * 60); s = (deg - d - m / 60) * 3600
    return (_rat(d), _rat(m), _rat(round(s, 2)))


def jpeg_bytes(exif: dict | None = None) -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", (32, 24), (90, 120, 150))
    if exif is None:
        img.save(buf, "JPEG")
    else:
        img.save(buf, "JPEG", exif=piexif.dump(exif))
    return buf.getvalue()


FULL_EXIF = {
    "0th": {
        piexif.ImageIFD.Make: b"SONY",
        piexif.ImageIFD.Model: b"ILCE-7RM5",
        piexif.ImageIFD.Orientation: 6,
    },
    "Exif": {
        piexif.ExifIFD.DateTimeOriginal: b"2026:03:14 13:18:03",
        piexif.ExifIFD.SubSecTimeOriginal: b"977",
        piexif.ExifIFD.OffsetTimeOriginal: b"-05:00",
        piexif.ExifIFD.FNumber: (4, 1),
        piexif.ExifIFD.ExposureTime: (1, 640),
        piexif.ExifIFD.FocalLength: (240, 10),
        piexif.ExifIFD.ISOSpeedRatings: 1250,
        piexif.ExifIFD.ExposureBiasValue: (-7, 10),
        piexif.ExifIFD.Flash: 0x19,
        piexif.ExifIFD.LensModel: b"FE 24-105mm F4 G OSS",
        piexif.ExifIFD.PixelXDimension: 9504,
        piexif.ExifIFD.PixelYDimension: 6336,
    },
    "GPS": {
        piexif.GPSIFD.GPSLatitudeRef: b"S",
        piexif.GPSIFD.GPSLatitude: _dms(33.8688),
        piexif.GPSIFD.GPSLongitudeRef: b"W",
        piexif.GPSIFD.GPSLongitude: _dms(70.6693),
    },
}


def test_maps_every_modelled_field(tmp_path):
    p = tmp_path / "a.jpg"
    p.write_bytes(jpeg_bytes(FULL_EXIF))
    row = read_file(str(p))

    assert row["captured_at"] == "2026-03-14T13:18:03.977"
    assert row["captured_offset"] == "-05:00"
    assert (row["camera_make"], row["camera_model"]) == ("SONY", "ILCE-7RM5")
    assert row["lens"] == "FE 24-105mm F4 G OSS"
    assert row["aperture"] == 4.0 and row["focal_length_mm"] == 24.0
    assert row["shutter_speed"] == "1/640" and row["iso"] == 1250
    assert row["exposure_comp"] == pytest.approx(-0.7)
    assert row["flash_fired"] == 1
    assert row["orientation"] == 6
    assert (row["width_px"], row["height_px"]) == (9504, 6336)
    assert row["gps_lat"] == pytest.approx(-33.8688, abs=1e-4)   # S is negative
    assert row["gps_lon"] == pytest.approx(-70.6693, abs=1e-4)   # W is negative
    assert '"Image Make":"SONY"' in row["raw_json"]


def test_zero_date_and_null_island_gps_become_null(tmp_path):
    exif = {
        "0th": {},
        "Exif": {piexif.ExifIFD.DateTimeOriginal: b"0000:00:00 00:00:00"},
        "GPS": {
            piexif.GPSIFD.GPSLatitudeRef: b"N", piexif.GPSIFD.GPSLatitude: _dms(0),
            piexif.GPSIFD.GPSLongitudeRef: b"E", piexif.GPSIFD.GPSLongitude: _dms(0),
        },
    }
    p = tmp_path / "b.jpg"
    p.write_bytes(jpeg_bytes(exif))
    row = read_file(str(p))
    assert row["captured_at"] is None
    assert row["gps_lat"] is None and row["gps_lon"] is None


def test_raf_is_read_through_its_embedded_jpeg(tmp_path):
    # RAF layout: magic, header, big-endian offset to the full JPEG at byte 84.
    jpeg = jpeg_bytes(FULL_EXIF)
    header = bytearray(b"FUJIFILMCCD-RAW 0201FF383501X-T5".ljust(160, b"\0"))
    struct.pack_into(">II", header, 84, len(header), len(jpeg))
    p = tmp_path / "DSCF0001.RAF"
    p.write_bytes(bytes(header) + jpeg + b"\0" * 64)
    row = read_file(str(p))
    assert row["camera_model"] == "ILCE-7RM5" and row["captured_at"].startswith("2026-03-14")


def test_non_ascii_path(tmp_path):
    d = tmp_path / "Café 日本"
    d.mkdir()
    p = d / "été.jpg"
    p.write_bytes(jpeg_bytes(FULL_EXIF))
    assert read_file(str(p))["camera_make"] == "SONY"


@pytest.fixture
def cataloged(tmp_path):
    root = tmp_path / "Photos"
    root.mkdir()
    (root / "good.jpg").write_bytes(jpeg_bytes(FULL_EXIF))
    (root / "noexif.jpg").write_bytes(jpeg_bytes(None))
    (root / "junk.jpg").write_bytes(b"not a jpeg at all")
    (root / "IMG_0001.CR3").write_bytes(b"\0\0\0\x18ftypcrx ")
    (root / "clip.mp4").write_bytes(b"\0\0\0\x18ftypisom")
    conn = open_catalog(tmp_path / "lunelis.db")
    root_id = add_root(conn, root)
    scan_root(conn, root_id)
    yield conn, root, root_id
    conn.close()


def _exif(conn, name):
    return conn.execute(
        "SELECT e.* FROM exif e JOIN files f ON f.id = e.file_id WHERE f.filename = ?", (name,)
    ).fetchone()


def test_bad_files_are_recorded_not_fatal(cataloged):
    conn, _, _ = cataloged
    r = extract_pending(conn)
    assert (r.read, r.failed) == (1, 4)
    assert _exif(conn, "good.jpg")["read_error"] is None
    for name in ("noexif.jpg", "junk.jpg", "IMG_0001.CR3", "clip.mp4"):
        assert _exif(conn, name)["read_error"], name
    assert "CR3" in _exif(conn, "IMG_0001.CR3")["read_error"]


def test_extraction_is_incremental(cataloged):
    conn, root, root_id = cataloged
    extract_pending(conn)
    assert pending_count(conn) == 0                       # failures aren't retried forever
    assert extract_pending(conn).read == 0

    good = root / "good.jpg"
    good.write_bytes(jpeg_bytes({**FULL_EXIF, "0th": {piexif.ImageIFD.Model: b"ILCE-1"}}))
    st = os.stat(good)
    os.utime(good, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    scan_root(conn, root_id)
    assert pending_count(conn) == 1                       # changed file is stale
    extract_pending(conn)
    assert _exif(conn, "good.jpg")["camera_model"] == "ILCE-1"


def test_missing_files_are_not_read(cataloged):
    conn, root, root_id = cataloged
    (root / "good.jpg").unlink()
    scan_root(conn, root_id)
    extract_pending(conn)
    assert _exif(conn, "good.jpg") is None


def test_cancel_leaves_rest_pending(cataloged):
    conn, _, _ = cataloged
    r = extract_pending(conn, should_cancel=lambda: True)
    assert r.cancelled and pending_count(conn) == 5


REAL_ARW = r"E:\3-14-2026\_A756538.ARW"


@pytest.mark.skipif(not os.path.exists(REAL_ARW), reason="real sample library not attached")
def test_real_sony_arw():
    row = read_file(REAL_ARW)
    assert row["camera_model"] == "ILCE-7RM5"
    assert row["lens"] == "FE 24-105mm F4 G OSS"
    assert row["captured_at"] == "2026-03-14T13:18:03.977"
    assert (row["aperture"], row["shutter_speed"], row["iso"]) == (4.0, "1/640", 1250)
    assert '"MakerNote FocusMode":"AF-C"' in row["raw_json"]   # maker notes decoded for RAW


@pytest.mark.skipif(not os.path.exists(REAL_ARW), reason="real sample library not attached")
def test_real_arw_keeps_raw_flag(tmp_path):
    import shutil
    root = tmp_path / "Photos"
    root.mkdir()
    shutil.copy(REAL_ARW, root)
    conn = open_catalog(tmp_path / "lunelis.db")
    scan_root(conn, add_root(conn, root))
    extract_pending(conn)
    f = conn.execute("SELECT is_raw, format FROM files").fetchone()
    assert (f["is_raw"], f["format"]) == (1, "tiff")
    conn.close()


def test_dash_separated_dates_are_accepted(tmp_path):
    exif = {"0th": {}, "Exif": {piexif.ExifIFD.DateTimeOriginal: b"2018-11-05 18:46:06"}}
    p = tmp_path / "phone.jpg"
    p.write_bytes(jpeg_bytes(exif))
    assert read_file(str(p))["captured_at"] == "2018-11-05T18:46:06"


def test_garbage_date_drops_the_field_not_the_file(tmp_path):
    exif = {"0th": {piexif.ImageIFD.Make: b"SONY"},
            "Exif": {piexif.ExifIFD.DateTimeOriginal: b"sometime last year"}}
    p = tmp_path / "odd.jpg"
    p.write_bytes(jpeg_bytes(exif))
    row = read_file(str(p))
    assert row["captured_at"] is None and row["camera_make"] == "SONY"


@pytest.mark.parametrize("head, fmt", [
    (b"\xff\xd8\xff\xe0\x00\x10JFIF", "jpeg"),
    (b"II*\x00\x08\x00\x00\x00", "tiff"),
    (b"MM\x00*\x00\x00\x00\x08", "tiff"),
    (b"IIRO\x08\x00\x00\x00", "tiff"),              # Olympus ORF
    (b"FUJIFILMCCD-RAW 0201", "raf"),
    (b"\x00\x00\x00\x18ftypcrx \x00\x00", "cr3"),
    (b"\x00\x00\x00\x18ftypheic\x00\x00", "heic"),
    (b"\x00\x00\x00\x18ftypisom\x00\x00", "mp4"),
    (b"\x00\x00\x00\x14ftypqt  \x00\x00", "mov"),
    (b"\x89PNG\r\n\x1a\n\x00\x00", "png"),
    (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "webp"),
    (b"GIF89ah\x01\x00\x01\x80\x00\x00", "gif"),     # starts with the TS sync byte 'G'
    (b"\x47\x40\x00\x10\x00\x00\xb0\x11", "mpeg-ts"),
    (b"not an image at all", None),
])
def test_sniff(head, fmt):
    from lunelis.importers.formats import sniff
    assert sniff(head) == fmt


def test_jpeg_named_arw_is_not_raw(tmp_path):
    # Google Takeout ships Picasa-re-saved JPEGs still named .ARW.
    root = tmp_path / "Takeout"
    root.mkdir()
    (root / "_MAR6692.ARW").write_bytes(jpeg_bytes(FULL_EXIF))
    conn = open_catalog(tmp_path / "lunelis.db")
    scan_root(conn, add_root(conn, root))
    assert conn.execute("SELECT is_raw FROM files").fetchone()[0] == 1   # scan guesses by extension
    extract_pending(conn)
    f = conn.execute("SELECT is_raw, format FROM files").fetchone()
    assert (f["is_raw"], f["format"]) == (0, "jpeg")                       # contents win
    conn.close()


def test_parallel_reads_match_serial(tmp_path):
    root = tmp_path / "Photos"
    root.mkdir()
    for i in range(30):
        exif = {"0th": {piexif.ImageIFD.Model: f"CAM-{i}".encode()}, "Exif": {}}
        (root / f"{i:03}.jpg").write_bytes(jpeg_bytes(exif))
    (root / "junk.jpg").write_bytes(b"nope")
    out = {}
    for workers in (1, 8):
        conn = open_catalog(tmp_path / f"w{workers}.db")
        scan_root(conn, add_root(conn, root))
        r = extract_pending(conn, workers=workers)
        out[workers] = (r.read, r.failed, conn.execute(
            "SELECT f.filename, e.camera_model, e.read_error IS NOT NULL"
            " FROM files f JOIN exif e ON e.file_id = f.id ORDER BY f.filename").fetchall())
        conn.close()
    assert out[1][:2] == (30, 1)
    assert [tuple(r) for r in out[1][2]] == [tuple(r) for r in out[8][2]]
