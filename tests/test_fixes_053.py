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


def test_an_import_says_what_it_left_on_the_card(tmp_path):
    # "Safe to format the card" was shown with AVI / WAV / GoPro files still only on it.
    from lunelis.importing import ingest
    card = tmp_path / "card" / "DCIM" / "100MSDCF"
    card.mkdir(parents=True)
    (card / "DSC0001.JPG").write_bytes(b"\xff\xd8\xff" + b"0" * 200)
    (card / "._DSC0001.JPG").write_bytes(b"mac resource fork")
    (card / "MOVIE.AVI").write_bytes(b"RIFF....AVI ")
    (card / "SOUND.WAV").write_bytes(b"RIFF....WAVE")
    (card / "SOUND2.WAV").write_bytes(b"RIFF....WAVE")
    found = [rel.rsplit("/", 1)[-1] for rel, _s, _m in ingest.discover(str(tmp_path / "card"))]
    assert found == ["DSC0001.JPG"]                               # not the Mac's ._ file
    left = ingest.not_copied(str(tmp_path / "card"))
    assert left == {"AVI": 1, "WAV": 2}
    text = ingest.not_copied_text(left)
    assert "3 other files" in text and "NOT copied" in text and "WAV x 2" in text
    assert ingest.not_copied(str(tmp_path / "gone")) == {}        # the card was removed: nothing to say


def test_photos_that_leave_the_view_leave_the_selection(tmp_path):
    # Select, then search: the hidden ones stayed selected, and a rating key changed them.
    from test_audit_navigation import _window
    from lunelis.ui.library import Filter
    w, ids = _window(tmp_path, 8)
    try:
        w.open_page("Library")
        w.grid.selected = set(ids)
        w.set_filter(Filter(ids=tuple(ids[:2])))
        assert w.grid.selected == set(ids[:2])
        assert w._targets() and set(w._targets()) <= set(ids[:2])
    finally:
        w._quitting = True
        w.close()


def test_the_focus_photo_survives_a_reload_that_adds_photos(tmp_path):
    from test_audit_navigation import _window
    w, ids = _window(tmp_path, 8)
    try:
        w.open_page("Library")
        g = w.grid
        pos = 3
        fid = w.index.file_id(pos)
        g.current = g.anchor = pos
        g._remember_focus()                                   # what a paint does
        rows = list(w.index.rows)
        w.index.apply([rows[-1], rows[-2], *rows[:-2]], 0.0)   # two photos now come first
        g.set_index(w.index)
        assert w.index.file_id(g.current) == fid and g.current == pos + 2
    finally:
        w._quitting = True
        w.close()
