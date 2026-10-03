"""The Albums page: your albums, events as albums, automatic albums, and
opening any of them as a library filter."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PIL import Image  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lunelis.albums import model as albums  # noqa: E402
from lunelis.catalog.ratings import set_ratings  # noqa: E402
from lunelis.catalog.schema import open_catalog  # noqa: E402
from lunelis.events import model as events  # noqa: E402
from lunelis.importers.scan import add_root, scan_root  # noqa: E402
from lunelis.ui.library import Filter, LibraryIndex  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def lib(tmp_path):
    root = tmp_path / "Photos"
    root.mkdir()
    names = ["a.jpg", "b.jpg", "c.jpg", "Screenshot_2026-01-02.png", "clip.mp4", "d.ARW"]
    for n in names:
        if n.endswith(".mp4"):
            (root / n).write_bytes(b"\0\0\0\x18ftypmp42" + os.urandom(200))
        else:
            Image.effect_noise((40, 30), 50).convert("RGB").save(root / n, "PNG" if n.endswith(".png") else "JPEG")
    conn = open_catalog(tmp_path / "c.db")
    scan_root(conn, add_root(conn, root))
    # what the metadata pass would have set
    ids = {n: i for i, n in conn.execute("SELECT id, filename FROM files")}
    conn.execute("UPDATE files SET format = 'mp4' WHERE filename = 'clip.mp4'")
    conn.execute("UPDATE files SET is_raw = 1 WHERE filename = 'd.ARW'")
    for n in ("a.jpg", "b.jpg", "d.ARW"):
        conn.execute("INSERT INTO exif (file_id, captured_at, camera_model) VALUES (?, '2026-06-19T10:00:00',"
                     " 'ILCE-7RM5')", (ids[n],))
    conn.commit()
    yield conn, ids
    conn.close()


def visible(conn, f: Filter) -> set[int]:
    idx = LibraryIndex()
    idx.load(conn, "name", f)
    return {r[0] for r in idx.rows}


def test_your_albums_hold_any_photos_and_never_touch_them(lib):
    conn, ids = lib
    trip = albums.create(conn, "  Best of 2026 ", [ids["a.jpg"], ids["b.jpg"]])
    also = albums.create(conn, "Prints", [ids["a.jpg"]])               # a photo can be in several
    assert albums.add_files(conn, trip, [ids["a.jpg"], ids["c.jpg"]]) == 1
    assert [n for _, n in albums.albums_of(conn, ids["a.jpg"])] == ["Best of 2026", "Prints"]
    assert visible(conn, Filter(album_id=trip)) == {ids["a.jpg"], ids["b.jpg"], ids["c.jpg"]}
    albums.remove_files(conn, trip, [ids["b.jpg"]])
    albums.delete(conn, also)
    mine = albums.your_albums(conn)
    assert [(a.name, a.count) for a in mine] == [("Best of 2026", 2)]
    assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 6
    with pytest.raises(ValueError):
        albums.create(conn, "   ")


def test_automatic_albums_and_their_filters(lib):
    conn, ids = lib
    set_ratings(conn, [ids["a.jpg"]], stars=5)
    set_ratings(conn, [ids["b.jpg"]], flag="pick")
    auto = {a.key: a.count for a in albums.auto_albums(conn)}
    assert auto["favorites"] == 1 and auto["picks"] == 1 and auto["videos"] == 1 and auto["raw"] == 1
    assert auto["screenshots"] == 1 and "recent" not in auto          # all 6 just cataloged: says nothing
    assert auto["camera:ILCE-7RM5"] == 3
    names = {a.key: a.name for a in albums.auto_albums(conn)}
    assert names["camera:ILCE-7RM5"] == "α7R V"
    assert visible(conn, Filter(auto="camera:ILCE-7RM5")) == {ids["a.jpg"], ids["b.jpg"], ids["d.ARW"]}
    assert visible(conn, Filter(auto="favorites")) == {ids["a.jpg"]}


def test_events_show_up_as_albums(lib):
    conn, ids = lib
    events.create(conn, "Air Show", [ids["a.jpg"], ids["b.jpg"]])
    (ev,) = albums.event_albums(conn)
    assert (ev.kind, ev.name, ev.count) == ("event", "Air Show", 2) and ev.cover_id in (ids["a.jpg"], ids["b.jpg"])


def test_albums_page_and_window_wiring(app, lib, monkeypatch):
    from lunelis.ui import main_window as mw
    conn, ids = lib
    trip = albums.create(conn, "Trip", [ids["a.jpg"], ids["c.jpg"]])
    monkeypatch.setattr(mw, "open_catalog", lambda _p: conn)
    w = mw.MainWindow()
    try:
        w.open_page("Albums")
        page = w.albums_page
        assert [t.album.name for t in page.yours.widgets[1:]] == ["Trip"]   # after the "New album" tile
        w.show_album(page.yours.widgets[1].album)
        assert w.filter.album_id == trip and len(w.index) == 2
        chips = [w.chips.itemAt(i).widget().text() for i in range(w.chips.count())]
        assert any("Album: Trip" in c for c in chips)
        w.grid.selected = {ids["a.jpg"]}
        w.remove_from_album()
        assert len(w.index) == 1
        w.grid.selected = {ids["b.jpg"]}
        monkeypatch.setattr(w, "_pick_album", lambda title: (trip, "Trip"))
        w.add_to_album()
        assert albums.your_albums(conn)[0].count == 2
    finally:
        w._quitting = True
        w.close()


def test_import_page_lists_drives_and_remembers_folders(app, tmp_path):
    from lunelis.importing import ingest
    from lunelis.settings import Settings
    from lunelis.ui.import_view import ImportView
    for root, kind, label, free, total in ingest.all_drives():
        assert root.endswith(":\\") and kind in ("card", "removable", "fixed", "network")
    conn = open_catalog(tmp_path / "i.db")
    view = ImportView(conn)
    folder = tmp_path / "Card dump"
    folder.mkdir()
    view.choose(str(folder))
    import time
    t = time.time()
    while view._thread is not None and time.time() - t < 10:     # the preview reads the (empty) folder
        app.processEvents()
        time.sleep(0.02)
    assert view._thread is None
    assert Settings(conn).get("import_recent")[0] == str(folder)
    assert view.recent.item(0).data(0x0100) == str(folder)            # Qt.UserRole
    conn.close()
