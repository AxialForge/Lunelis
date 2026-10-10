"""0.51: Canon CR3 metadata - read from the CMT1 / CMT2 TIFF blocks in the
file's header. The test file is built here (no real photo in the repo)."""
import struct

from lunelis.importers.metadata import read_file


def _tiff(entries: list[tuple[int, int, bytes]]) -> bytes:
    """A little-endian TIFF with one IFD; entries are (tag, type, value bytes)."""
    n = len(entries)
    data_at = 8 + 2 + n * 12 + 4
    ifd, extra = b"", b""
    for tag, typ, val in sorted(entries):
        size = {2: 1, 3: 2, 5: 8}[typ]
        count = len(val) // size
        if len(val) <= 4:
            ifd += struct.pack("<HHI", tag, typ, count) + val.ljust(4, b"\0")
        else:
            ifd += struct.pack("<HHII", tag, typ, count, data_at + len(extra))
            extra += val
    return b"II*\0" + struct.pack("<I", 8) + struct.pack("<H", n) + ifd + b"\0\0\0\0" + extra


def _box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def _ascii(s: str) -> bytes:
    return s.encode() + b"\0"


def test_a_cr3_gets_its_date_camera_and_exposure(tmp_path):
    cmt1 = _tiff([(0x010F, 2, _ascii("Canon")), (0x0110, 2, _ascii("Canon EOS R5")), (0x0112, 3, struct.pack("<H", 6))])
    cmt2 = _tiff([(0x9003, 2, _ascii("2024:06:19 10:30:00")), (0x829A, 5, struct.pack("<II", 1, 250)),
                  (0x829D, 5, struct.pack("<II", 28, 10)), (0x8827, 3, struct.pack("<H", 400)),
                  (0xA434, 2, _ascii("RF24-70mm F2.8 L IS USM"))])
    uuid = _box(b"uuid", bytes(16) + _box(b"CMT1", cmt1) + _box(b"CMT2", cmt2))
    p = tmp_path / "IMG_0001.CR3"
    p.write_bytes(_box(b"ftyp", b"crx \0\0\0\1crx isom") + _box(b"moov", uuid) + bytes(4096))
    r = read_file(str(p))
    assert r["camera_make"] == "Canon" and r["camera_model"] == "Canon EOS R5"
    assert r["captured_at"].startswith("2024-06-19T10:30:00") and r["date_source"] == "exif"
    assert r["iso"] == 400 and r["aperture"] == 2.8 and r["shutter_speed"] == "1/250"
    assert r["lens"] == "RF24-70mm F2.8 L IS USM" and r["orientation"] == 6


def test_cr3_rows_marked_no_reader_are_read_again_after_the_upgrade(tmp_path):
    import sqlite3
    from lunelis.catalog.schema import MIGRATIONS
    sql = next(s for v, _d, s in MIGRATIONS if v == 45)
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE exif (file_id INTEGER PRIMARY KEY, read_error TEXT)")
    c.executemany("INSERT INTO exif VALUES (?, ?)", [(1, "NotImplementedError: Canon CR3 metadata reader not built yet"),
                                                     (2, None), (3, "ValueError: bad")])
    c.executescript(sql)
    assert [r[0] for r in c.execute("SELECT file_id FROM exif ORDER BY 1")] == [2, 3]


def test_files_waiting_to_be_read_are_read_at_startup_not_at_the_next_scan(tmp_path):
    # 0.52: after the 0.51 upgrade cleared the CR3 "no reader" marks, nothing
    # read them until the next library pass.
    from types import SimpleNamespace
    from lunelis.catalog.schema import open_catalog
    from lunelis.importers.scan import add_root, scan_root
    from lunelis.ui.main_window import MainWindow
    (tmp_path / "Card").mkdir()
    (tmp_path / "Card" / "IMG_1.CR3").write_bytes(b"\0" * 64)
    conn = open_catalog(tmp_path / "c.db")
    rid = add_root(conn, tmp_path / "Card")
    scan_root(conn, rid)                              # cataloged, metadata not read yet
    started = []
    fake = SimpleNamespace(conn=conn, offline_roots={}, start=started.append)
    MainWindow._read_waiting(fake)
    assert started == [[rid]]
    fake.offline_roots = {rid: "asleep"}              # a source that isn't answering waits
    started.clear()
    MainWindow._read_waiting(fake)
    assert started == []
    conn.close()
