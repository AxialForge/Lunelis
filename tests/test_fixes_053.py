"""0.53: the first batch from the 10 October backlog - things that could lose
work or mislead about safety, and rough edges in that week's new work."""
import os

import pytest

from lunelis.catalog.schema import open_catalog
from lunelis.importers.formats import sniff
from lunelis.importers.scan import add_root, scan_root


def test_an_old_quicktime_clip_with_no_ftyp_is_a_video():
    # They were "not a recognisable photo or video", and a migration filed them under Damaged.
    for first in (b"wide", b"mdat", b"moov", b"free"):
        assert sniff(bytes([0, 0, 0, 8]) + first + bytes(8)) == "mov"
    assert sniff(bytes([0, 0, 0, 24]) + b"ftypqt  ") == "mov" and sniff(b"II+" + bytes(5)) == "tiff"
    assert sniff(b"just some text here") is None


def _one_file(tmp_path, data=b"x" * 5000):
    root = tmp_path / "P"
    root.mkdir()
    (root / "a.jpg").write_bytes(data)
    conn = open_catalog(tmp_path / "c.db")
    rid = add_root(conn, root)
    scan_root(conn, rid)
    return conn, rid, root / "a.jpg"


def test_a_photo_edited_since_the_scan_is_not_called_silent_corruption(tmp_path):
    from lunelis.dupes.hashing import Throttle, full_hash
    from lunelis.jobs import engine
    conn, rid, f = _one_file(tmp_path)
    conn.execute("UPDATE files SET content_hash = ?", (full_hash(str(f))[0],))
    conn.commit()
    f.write_bytes(b"y" * 6000)                               # edited in another program: new size and date
    engine._integrity_folder(conn, rid, "", throttle=Throttle(None), should_cancel=None, workers=1)
    assert conn.execute("SELECT COUNT(*) FROM damaged").fetchone()[0] == 0
    # The same bytes changing with the size and date untouched IS reported.
    scan_root(conn, rid)
    conn.execute("UPDATE files SET content_hash = ?", (full_hash(str(f))[0],))
    conn.commit()
    st = os.stat(f)
    f.write_bytes(b"z" * 6000)
    os.utime(f, (st.st_atime, st.st_mtime))
    engine._integrity_folder(conn, rid, "", throttle=Throttle(None), should_cancel=None, workers=1)
    assert conn.execute("SELECT problem FROM damaged").fetchone()[0] == "changed_on_disk"
    conn.close()


def test_one_missing_file_doesnt_stop_a_hash_or_check_job(tmp_path):
    from lunelis.dupes.hashing import Throttle
    from lunelis.jobs import engine
    conn, rid, f = _one_file(tmp_path)
    f.unlink()
    engine._full_hash_folder(conn, rid, "", throttle=Throttle(None), should_cancel=None, workers=1)
    conn.execute("UPDATE files SET content_hash = 'abc'")
    engine._integrity_folder(conn, rid, "", throttle=Throttle(None), should_cancel=None, workers=1)
    conn.close()
