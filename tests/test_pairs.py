"""v0.19: RAW+JPEG pairs show as one photo; what you do to one happens to both."""
import pytest
from PIL import Image

from lunelis import pairs
from lunelis.catalog.schema import open_catalog
from lunelis.importers.scan import add_root, scan_root
from lunelis.ui.library import Filter, LibraryIndex


def raw(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"II*\0" + b"\0" * 2000)          # scanned as RAW by its extension


@pytest.fixture
def lib(tmp_path):
    conn = open_catalog(tmp_path / "c.db")
    root = tmp_path / "Photos"
    for n in (1, 2):
        raw(root / f"DSC0000{n}.ARW")
        Image.new("RGB", (30, 20)).save(root / f"DSC0000{n}.JPG")
    raw(root / "DSC00003.ARW")                            # RAW only
    Image.new("RGB", (30, 20)).save(root / "DSC00004.JPG")  # JPEG only
    raw(root / "other" / "DSC00001.ARW")                  # same name, another folder: its own shot
    raw(root / "DSC00005.ARW")
    Image.new("RGB", (30, 20)).save(root / "DSC00005.JPG")
    Image.new("RGB", (30, 20)).save(root / "DSC00005.jpeg")  # a third file: never guessed
    rid = add_root(conn, root)
    scan_root(conn, rid)
    conn.execute("UPDATE files SET is_raw = 1, format = 'arw' WHERE ext = 'arw'")
    conn.execute("UPDATE files SET format = 'jpeg' WHERE ext IN ('jpg', 'jpeg')")
    conn.commit()
    ids = {r: i for i, r in conn.execute("SELECT id, rel_path FROM files")}
    return conn, ids


def test_pairs_are_found_both_ways_and_nothing_is_guessed(lib):
    conn, ids = lib
    assert pairs.rebuild(conn) == 2
    assert pairs.partner(conn, ids["DSC00001.ARW"]) == ids["DSC00001.JPG"]
    assert pairs.partner(conn, ids["DSC00001.JPG"]) == ids["DSC00001.ARW"]
    for alone in ("DSC00003.ARW", "DSC00004.JPG", "other/DSC00001.ARW", "DSC00005.ARW", "DSC00005.JPG"):
        assert pairs.partner(conn, ids[alone]) is None, alone


def test_the_library_shows_one_tile_per_pair(lib):
    conn, ids = lib
    pairs.rebuild(conn)
    idx = LibraryIndex()
    idx.load(conn, "name", Filter(hide_pairs=True))
    shown = {idx.file_id(i) for i in range(len(idx))}
    assert ids["DSC00001.JPG"] not in shown and ids["DSC00001.ARW"] in shown
    tile = idx.tile(idx.position(ids["DSC00001.ARW"]))
    assert tile.badge == "ARW+JPG"
    idx.load(conn, "name", Filter())
    assert len(idx) == 10                                 # turned off: every file


def test_actions_reach_both_and_the_setting_turns_it_off(lib):
    conn, ids = lib
    pairs.rebuild(conn)
    both = pairs.with_partners(conn, [ids["DSC00001.ARW"], ids["DSC00003.ARW"]])
    assert both == [ids["DSC00001.ARW"], ids["DSC00003.ARW"], ids["DSC00001.JPG"]]
    from lunelis.settings import Settings
    Settings(conn).set("pair_raw_jpeg", False)
    assert pairs.rebuild_from_settings(conn) is None
    assert pairs.partner(conn, ids["DSC00001.ARW"]) is None


def test_rating_a_paired_tile_rates_both(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from lunelis.ui import main_window as mw
    w = mw.MainWindow()
    try:
        root = tmp_path / "P"
        raw(root / "A1.ARW")
        Image.new("RGB", (30, 20)).save(root / "A1.JPG")
        rid = add_root(w.conn, root)
        scan_root(w.conn, rid)
        w.conn.execute("UPDATE files SET is_raw = 1, format = 'arw' WHERE root_id = ? AND ext = 'arw'", (rid,))
        w.conn.execute("UPDATE files SET format = 'jpeg' WHERE root_id = ? AND ext = 'jpg'", (rid,))
        w.conn.commit()
        pairs.rebuild(w.conn)
        a, j = (w.conn.execute("SELECT id FROM files WHERE root_id = ? AND ext = ?", (rid, e)).fetchone()[0]
                for e in ("arw", "jpg"))
        w.reload()
        w.grid.selected = {a}
        w.rate(stars=4)
        got = dict(w.conn.execute("SELECT file_id, stars FROM ratings WHERE file_id IN (?, ?)", (a, j)).fetchall())
        assert got == {a: 4, j: 4}
    finally:
        w._quitting = True
        w.close()
