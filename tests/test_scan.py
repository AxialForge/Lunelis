"""Step 2: folder root + scan keeps `files` in line with the disk, and never deletes."""
import os
import shutil

import pytest

from lunelis.catalog.schema import open_catalog
from lunelis.importers.scan import (
    RootOverlap, RootUnavailable, add_root, catalog_stats, scan_root,
)


def _touch(path, data=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "Photos"
    _touch(root / "a.jpg")
    _touch(root / "B.JPG")                         # extension case must not matter
    _touch(root / "2026" / "trip" / "DSC0001.ARW", b"raw" * 10)
    _touch(root / "2026" / "trip" / "clip.mp4")
    _touch(root / "notes.txt")                     # not a photo
    _touch(root / ".hidden" / "secret.jpg")        # dot-dir skipped
    _touch(root / "$RECYCLE.BIN" / "old.jpg")      # system dir skipped
    return root


@pytest.fixture
def conn(tmp_path):
    c = open_catalog(tmp_path / "lunelis.db")
    yield c
    c.close()


def _rows(conn):
    return {
        r["rel_path"]: r
        for r in conn.execute("SELECT * FROM files ORDER BY rel_path")
    }


def test_first_scan_catalogs_photos_only(conn, library):
    root_id = add_root(conn, library)
    r = scan_root(conn, root_id)

    rows = _rows(conn)
    assert set(rows) == {"a.jpg", "B.JPG", "2026/trip/DSC0001.ARW", "2026/trip/clip.mp4"}
    assert (r.added, r.skipped, r.missing) == (4, 1, 0)

    raw = rows["2026/trip/DSC0001.ARW"]
    assert raw["ext"] == "arw" and raw["is_raw"] == 1 and raw["size_bytes"] == 30
    assert rows["B.JPG"]["ext"] == "jpg" and rows["B.JPG"]["is_raw"] == 0
    assert raw["content_hash"] is None             # hashing is Step 7, not the scan


def test_rescan_is_idempotent(conn, library):
    root_id = add_root(conn, library)
    scan_root(conn, root_id)
    r = scan_root(conn, root_id)
    assert (r.added, r.updated, r.unchanged, r.missing) == (0, 0, 4, 0)
    assert catalog_stats(conn)["files"] == 4


def test_changed_file_updates_and_clears_derived_data(conn, library):
    root_id = add_root(conn, library)
    scan_root(conn, root_id)
    conn.execute("UPDATE files SET content_hash='old', thumbnail_path='t.jpg' WHERE rel_path='a.jpg'")
    conn.commit()

    (library / "a.jpg").write_bytes(b"different, longer")
    r = scan_root(conn, root_id)

    row = _rows(conn)["a.jpg"]
    assert r.updated == 1
    assert row["size_bytes"] == len(b"different, longer")
    assert row["content_hash"] is None and row["thumbnail_path"] is None


def test_deleted_file_is_flagged_not_removed_and_can_come_back(conn, library):
    root_id = add_root(conn, library)
    scan_root(conn, root_id)
    conn.execute("INSERT INTO ratings (file_id, stars) SELECT id, 5 FROM files WHERE rel_path='a.jpg'")
    conn.commit()

    data = (library / "a.jpg").read_bytes()
    stat = os.stat(library / "a.jpg")
    (library / "a.jpg").unlink()
    r = scan_root(conn, root_id)
    row = _rows(conn)["a.jpg"]
    assert r.missing == 1 and row["missing_since"] is not None
    assert conn.execute("SELECT stars FROM ratings WHERE file_id=?", (row["id"],)).fetchone()[0] == 5

    (library / "a.jpg").write_bytes(data)
    os.utime(library / "a.jpg", ns=(stat.st_atime_ns, stat.st_mtime_ns))
    r = scan_root(conn, root_id)
    assert r.restored == 1 and _rows(conn)["a.jpg"]["missing_since"] is None


def test_offline_root_is_refused_not_treated_as_empty(conn, library, tmp_path):
    root_id = add_root(conn, library)
    scan_root(conn, root_id)
    shutil.move(library, tmp_path / "unplugged")

    with pytest.raises(RootUnavailable):
        scan_root(conn, root_id)
    assert catalog_stats(conn)["missing"] == 0


def test_cancelled_scan_marks_nothing_missing(conn, library):
    root_id = add_root(conn, library)
    scan_root(conn, root_id)
    r = scan_root(conn, root_id, should_cancel=lambda: True)
    assert r.cancelled and r.missing == 0
    assert catalog_stats(conn)["missing"] == 0


def test_rel_paths_use_forward_slashes(conn, library):
    scan_root(conn, add_root(conn, library))
    assert all("\\" not in p for p in _rows(conn))


def test_add_root_is_idempotent_and_rejects_overlap(conn, library):
    first = add_root(conn, library)
    assert add_root(conn, str(library) + os.sep) == first
    with pytest.raises(RootOverlap):
        add_root(conn, library / "2026")            # nested inside
    with pytest.raises(RootOverlap):
        add_root(conn, library.parent)              # contains it


def test_add_root_refuses_missing_folder(conn, tmp_path):
    with pytest.raises(RootUnavailable):
        add_root(conn, tmp_path / "nope")


@pytest.mark.skipif(os.name != "nt", reason="8.3 short names are Windows-only")
def test_short_name_spelling_is_the_same_root(conn, tmp_path):
    import ctypes
    long_dir = tmp_path / "Long Folder Name"
    long_dir.mkdir()
    buf = ctypes.create_unicode_buffer(32768)
    ctypes.windll.kernel32.GetShortPathNameW(str(long_dir), buf, len(buf))
    if buf.value == str(long_dir):
        pytest.skip("8.3 names disabled on this volume")
    assert add_root(conn, buf.value) == add_root(conn, long_dir)
