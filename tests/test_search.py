"""The search box: parsing, the index and its triggers, and the UI."""
import time

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from lunelis import paths, search
from lunelis.catalog.schema import open_catalog
from lunelis.importers.scan import add_root, scan_root
from lunelis.tags import model as tags
from lunelis.ui.library import Filter, LibraryIndex


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DEFAULT_CATALOG_PATH", tmp_path / "catalog.db")
    root = tmp_path / "Photos"
    for folder, name in (("2024/6-18-2024 Myrtle Beach", "DSC00913.JPG"), ("2024/6-18-2024 Myrtle Beach", "A7501341.ARW"),
                         ("2025/Las Vegas", "IMG_0001.jpg"), ("2019/Edits", "20190625-_JCS0011.jpg")):
        (root / folder).mkdir(parents=True, exist_ok=True)
        p = root / folder / name
        if name.endswith(".ARW"):
            p.write_bytes(b"not a real raw" * 20)
        else:
            Image.new("RGB", (40, 30)).save(p, "JPEG")
    conn = open_catalog(paths.DEFAULT_CATALOG_PATH)
    scan_root(conn, add_root(conn, root))
    by = {r[1]: r[0] for r in conn.execute("SELECT id, filename FROM files")}
    for fid, taken, make, model, lens in (
            (by["DSC00913.JPG"], "2024-06-18T10:00:00", "SONY", "ILCE-7RM5", "FE 24-105mm F4 G OSS"),
            (by["A7501341.ARW"], "2024-06-18T10:00:01", "SONY", "ILCE-7RM3", "FE 16-35mm F2.8 GM"),
            (by["IMG_0001.jpg"], "2025-03-02T20:00:00", "Apple", "iPhone 15 Pro Max", None),
            (by["20190625-_JCS0011.jpg"], "2019-06-25T13:43:42", "SONY", "ILCE-7RM3", "FE 85mm F1.8")):
        conn.execute("INSERT OR REPLACE INTO exif (file_id, captured_at, camera_make, camera_model, lens)"
                     " VALUES (?, ?, ?, ?, ?)", (fid, taken, make, model, lens))
    conn.commit()
    search.refresh(conn)
    yield conn, by
    conn.close()


def found(conn, q):
    idx = LibraryIndex()
    idx.collapse = False
    idx.load(conn, filt=Filter(query=q))
    names = {r[0]: r[1] for r in conn.execute("SELECT id, filename FROM files")}
    return sorted(names[idx.file_id(i)] for i in range(len(idx)))


def test_words_fields_and_specials(lib):
    conn, by = lib
    assert found(conn, "myrtle") == ["A7501341.ARW", "DSC00913.JPG"]
    assert found(conn, "myrtle raw") == ["A7501341.ARW"]
    assert found(conn, "a7r v") == ["DSC00913.JPG"]                     # not the a7R III shots
    assert found(conn, "a7rv") == ["DSC00913.JPG"]
    assert found(conn, "a7r iii") == ["20190625-_JCS0011.jpg", "A7501341.ARW"]
    assert found(conn, "24-105") == ["DSC00913.JPG"]
    assert found(conn, "june 2024") == ["A7501341.ARW", "DSC00913.JPG"]
    assert found(conn, "june") == ["20190625-_JCS0011.jpg", "A7501341.ARW", "DSC00913.JPG"]
    assert found(conn, "2025") == ["IMG_0001.jpg"]
    assert found(conn, "camera:iphone") == ["IMG_0001.jpg"]
    assert found(conn, "folder:edits") == ["20190625-_JCS0011.jpg"]
    assert found(conn, "_jcs0011") == ["20190625-_JCS0011.jpg"]
    assert found(conn, "sony -myrtle") == ["20190625-_JCS0011.jpg"]
    assert found(conn, '"las vegas"') == ["IMG_0001.jpg"]
    assert found(conn, "nothing-like-this") == []
    assert found(conn, 'unclosed "quote') == []                         # never an error


def test_index_follows_changes(lib):
    conn, by = lib
    img = by["IMG_0001.jpg"]
    tags.add(conn, [img], ["People > Grandma"])
    assert search.pending(conn) == 1
    search.refresh(conn)
    assert found(conn, "grandma") == ["IMG_0001.jpg"] and found(conn, "tag:people") == ["IMG_0001.jpg"]
    tags.rename(conn, "People|Grandma", "People > Nana")
    search.refresh(conn)
    assert found(conn, "nana") == ["IMG_0001.jpg"] and found(conn, "grandma") == []
    tags.remove(conn, [img], "People|Nana")
    search.refresh(conn)
    assert found(conn, "nana") == []
    from lunelis.albums import model as albums
    aid = albums.create(conn, "Road trip")
    albums.add_files(conn, aid, [by["DSC00913.JPG"]])
    search.refresh(conn)
    assert found(conn, "road trip") == ["DSC00913.JPG"] and found(conn, "album:road") == ["DSC00913.JPG"]
    conn.execute("DELETE FROM files WHERE id = ?", (img,))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM search_fts WHERE rowid = ?", (img,)).fetchone()[0] == 0


def test_search_box(lib, monkeypatch):
    QApplication.instance() or QApplication([])
    from lunelis.ui import main_window as mw
    conn, by = lib
    monkeypatch.setattr(mw, "open_catalog", lambda _p: conn)
    w = mw.MainWindow()
    try:
        w.open_page("Settings")
        w.focus_search()
        assert w.pages.currentWidget() is w.grid and w.search.completer() is not None
        w.search.setText("myrtle raw")
        w._search_changed()
        assert w.filter.query == "myrtle raw" and len(w.index) == 1 and w.clear_all.isVisible()
        w.search.setText("zzzz")
        w._search_changed()
        assert len(w.index) == 0 and "zzzz" in w.grid.empty_text
        w.clear_all.click()                                              # Clear all empties the box too
        assert w.search.text() == "" and w.filter.query is None and len(w.index) == 4
    finally:
        w._quitting = True
        w.close()


def test_suggestions(lib):
    conn, _ = lib
    tags.add(conn, [1], ["Beach"])
    s = search.suggestions(conn)
    assert "Beach" in s and "a7R V" in s and "FE 24-105mm F4 G OSS" in s
