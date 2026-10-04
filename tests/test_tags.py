"""Tags: the model, XMP round trips, the filter, and the UI."""
import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from lunelis import paths
from lunelis.catalog.schema import open_catalog
from lunelis.importers.scan import add_root, scan_root
from lunelis.tags import model as tags
from lunelis.ui.library import Filter, LibraryIndex


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DEFAULT_CATALOG_PATH", tmp_path / "catalog.db")
    root = tmp_path / "Photos"
    root.mkdir()
    for i in range(4):
        Image.new("RGB", (40, 30), (i * 50, 90, 90)).save(root / f"IMG_{i}.jpg")
    conn = open_catalog(paths.DEFAULT_CATALOG_PATH)
    scan_root(conn, add_root(conn, root))
    ids = [r[0] for r in conn.execute("SELECT id FROM files ORDER BY filename")]
    yield conn, ids, root, tmp_path
    conn.close()


def test_names():
    assert tags.normalize("  Places >  Ohio | Cleveland ") == "Places|Ohio|Cleveland"
    assert tags.normalize(" > ") is None
    assert tags.split_input("beach, Beach; Places > Ohio") == ["beach", "Places|Ohio"]
    assert tags.ancestors("A|B|C") == ["A", "A|B"] and tags.leaf("A|B|C") == "C"


def test_add_remove_and_hierarchy(lib):
    conn, (a, b, c, d), *_ = lib
    assert tags.add(conn, [a, b], ["Places > Ohio > Cleveland", "Beach"]) == 4
    assert tags.add(conn, [a], ["beach"]) == 0                          # same tag, any case
    assert set(tags.names(conn)) == {"Places", "Places|Ohio", "Places|Ohio|Cleveland", "Beach"}
    assert tags.tags_of(conn, a) == ["Beach", "Places|Ohio|Cleveland"]
    assert tags.counts_in(conn, [a, b, c]) == {"Beach": 2, "Places|Ohio|Cleveland": 2}
    assert conn.execute("SELECT xmp_pending FROM ratings WHERE file_id = ?", (a,)).fetchone()[0] == 1
    tags.add(conn, [c], ["Places|Ohio"])
    idx = LibraryIndex()
    idx.load(conn, filt=Filter(tag="Places"))                           # includes everything inside
    assert {idx.file_id(i) for i in range(len(idx))} == {a, b, c}
    idx.load(conn, filt=Filter(tag="places|ohio|cleveland"))
    assert {idx.file_id(i) for i in range(len(idx))} == {a, b}
    assert tags.remove(conn, [a], "BEACH") == 1 and tags.tags_of(conn, a) == ["Places|Ohio|Cleveland"]


def test_rename_merge_delete(lib):
    conn, (a, b, c, d), *_ = lib
    tags.add(conn, [a], ["Trips|Vegas"])
    tags.add(conn, [b], ["Vacation|Vegas", "Vacation|Myrtle"])
    tags.rename(conn, "Trips", "Vacation")                              # onto an existing tag: merges
    assert tags.tags_of(conn, a) == ["Vacation|Vegas"]
    assert "Trips" not in tags.names(conn) and "Trips|Vegas" not in tags.names(conn)
    tags.rename(conn, "Vacation|Myrtle", "Vacation > Myrtle Beach")
    assert tags.tags_of(conn, b) == ["Vacation|Myrtle Beach", "Vacation|Vegas"]
    with pytest.raises(ValueError):
        tags.rename(conn, "Vacation", "Vacation|Sub")
    tags.merge(conn, "Vacation|Myrtle Beach", "Beach")
    assert tags.tags_of(conn, b) == ["Beach", "Vacation|Vegas"]
    assert tags.delete(conn, "Vacation") == 2                           # a and b lose Vegas
    assert tags.tags_of(conn, a) == [] and tags.tags_of(conn, b) == ["Beach"]


def test_recent(lib):
    conn, (a, *_), *_ = lib
    tags.add(conn, [a], ["One", "Two"])
    tags.remember_recent(conn, ["Two"])
    tags.remember_recent(conn, ["One"])
    assert tags.recent(conn) == ["One", "Two"]


def test_sidecar_round_trip(lib):
    from lunelis.xmp.sidecar import XmpFields, read_sidecar, write_sidecar
    from lunelis.xmp.sync import central_path, export_pending, import_sidecars
    conn, (a, b, c, d), root, tmp = lib
    tags.add(conn, [a], ["Places|Ohio", "Beach"])
    store = tmp / "sidecars"
    export_pending(conn, mode="central", update_existing=False, store_dir=store)
    rid, rpath = conn.execute("SELECT id, path FROM roots").fetchone()
    xmp = central_path(store, rid, rpath, "IMG_0.jpg", "IMG_0.jpg")
    assert read_sidecar(xmp).keywords == ("Beach", "Places|Ohio")
    # A sidecar next to a photo, tagged in darktable: its tags are added, darktable's own ignored.
    side = root / "IMG_1.jpg.xmp"
    write_sidecar(str(side), XmpFields(keywords=("Family|Grandma", "darktable|format|jpg")))
    conn.execute("UPDATE files SET sidecar = 'IMG_1.jpg.xmp', sidecar_mtime = 'x' WHERE id = ?", (b,))
    tags.add(conn, [b], ["Mine"])
    conn.execute("UPDATE ratings SET xmp_pending = 0 WHERE file_id = ?", (b,))
    conn.commit()
    import_sidecars(conn)
    assert tags.tags_of(conn, b) == ["Family|Grandma", "Mine"]          # added, never removed


def test_tag_dialog_and_info_panel(app, lib):
    from lunelis.ui.detail_view import DetailView
    from lunelis.ui.tag_editor import TagDialog
    conn, ids, *_ = lib
    d = TagDialog(conn, ids[:3])
    d.box.edit.setText("Beach, Places > Ohio")
    d.box.edit.returnPressed.emit()
    assert tags.counts_in(conn, ids[:3]) == {"Beach": 3, "Places|Ohio": 3} and d.changed
    d._removed("Beach")
    assert tags.counts_in(conn, ids[:3]) == {"Places|Ohio": 3}
    idx = LibraryIndex()
    idx.load(conn, "name")
    v = DetailView(conn)
    v.open(idx, 3)
    box = v.panel.tag_box
    box.edit.setText("Grandma")
    box.edit.returnPressed.emit()
    assert tags.tags_of(conn, ids[3]) == ["Grandma"] and box.flow.count() == 1
    box.removed.emit("Grandma")
    assert tags.tags_of(conn, ids[3]) == []


def test_tags_page_and_filter(app, lib, monkeypatch):
    from lunelis.ui import main_window as mw
    from lunelis.ui import tags_view
    conn, ids, *_ = lib
    tags.add(conn, ids[:2], ["Trips|Vegas"])
    tags.add(conn, ids[2:3], ["Trips|Ohio"])
    monkeypatch.setattr(mw, "open_catalog", lambda _p: conn)
    w = mw.MainWindow()
    try:
        w.open_page("Tags")
        w.tags_page.bg.wait()                          # counted on a worker
        top = w.tags_page.tree.topLevelItem(0)
        assert top.text(0) == "Trips" and top.text(1) == "3" and top.childCount() == 2
        w.tags_page.open_tag.emit("Trips|Vegas")
        assert w.filter.tag == "Trips|Vegas" and len(w.index) == 2
        monkeypatch.setattr(tags_view.QInputDialog, "getText", lambda *a, **k: ("Trips > Las Vegas", True))
        w.tags_page.rename("Trips|Vegas")
        assert "Trips|Las Vegas" in tags.names(conn)
        monkeypatch.setattr(tags_view.QMessageBox, "question", lambda *a, **k: tags_view.QMessageBox.StandardButton.Yes)
        w.tags_page.delete("Trips")
        assert tags.names(conn) == []
    finally:
        w._quitting = True
        w.close()
