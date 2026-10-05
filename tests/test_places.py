"""v0.36: places - offline place tags, pins for photos without GPS, unknown places and strangers."""
import pytest
from PIL import Image

from lunelis.catalog.schema import open_catalog
from lunelis.geo import places
from lunelis.importers.scan import add_root, scan_root
from lunelis.settings import Settings
from lunelis.tags import model as tags

ROME, CLEVELAND, AT_SEA, GRAND_CANYON = (41.9028, 12.4964), (41.4993, -81.6944), (0.0, -30.0), (36.0, -112.1)


def test_lookup_names_the_city_not_its_district():
    assert places.lookup(*ROME).tag == "Places|Italy|Lazio|Rome"
    assert places.lookup(48.8566, 2.3522).tag.endswith("|Paris")           # not "Paris 04 ..."
    assert places.lookup(41.24, -81.35).tag.endswith("|Ohio|Streetsboro")   # a small town keeps its name
    assert places.lookup(*GRAND_CANYON).tag == "Places|United States|Unknown"
    assert places.lookup(*AT_SEA).tag == "Places|Unknown"
    assert places.lookup(*AT_SEA).label == "Unknown place"


@pytest.fixture
def lib(tmp_path):
    conn = open_catalog(tmp_path / "c.db")
    root = tmp_path / "Photos"
    root.mkdir()
    for n in ("rome", "home", "nowhere", "sea"):
        Image.new("RGB", (60, 40), (90, 120, 150)).save(root / f"{n}.jpg")
    rid = add_root(conn, root)
    scan_root(conn, rid)
    ids = {n: i for i, n in conn.execute("SELECT id, filename FROM files WHERE root_id = ?", (rid,))}
    for name, (la, lo) in (("rome.jpg", ROME), ("sea.jpg", AT_SEA)):
        conn.execute("INSERT INTO exif (file_id, gps_lat, gps_lon) VALUES (?, ?, ?) ON CONFLICT(file_id) DO UPDATE"
                     " SET gps_lat = excluded.gps_lat, gps_lon = excluded.gps_lon", (ids[name], la, lo))
    conn.commit()
    return conn, ids


def test_photos_with_a_location_are_tagged_once(lib):
    conn, ids = lib
    assert places.tag_pending(conn) == 2
    assert tags.tags_of(conn, ids["rome.jpg"]) == ["Places|Italy|Lazio|Rome"]
    assert tags.tags_of(conn, ids["sea.jpg"]) == ["Places|Unknown"]
    assert tags.tags_of(conn, ids["home.jpg"]) == []
    assert places.pending(conn) == []                              # nothing to do until a location changes
    # A place tag removed by hand isn't put back while the location stays the same.
    tags.remove(conn, [ids["rome.jpg"]], "Places|Italy|Lazio|Rome")
    places.tag_pending(conn)
    assert tags.tags_of(conn, ids["rome.jpg"]) == []


def test_a_pin_places_photos_without_gps_and_moves_the_tag(lib):
    conn, ids = lib
    places.tag_pending(conn)
    assert set(places.without_location(conn)) == {ids["home.jpg"], ids["nowhere.jpg"]}
    places.set_location(conn, [ids["home.jpg"], ids["nowhere.jpg"]], *CLEVELAND)
    for n in ("home.jpg", "nowhere.jpg"):
        assert tags.tags_of(conn, ids[n]) == ["Places|United States|Ohio|Cleveland"]
    assert places.without_location(conn) == []
    assert places.location_of(conn, ids["home.jpg"])[2] == "pin"
    # A pin wins over the camera's GPS; taking it off goes back to the GPS.
    places.set_location(conn, [ids["rome.jpg"]], *CLEVELAND)
    assert tags.tags_of(conn, ids["rome.jpg"]) == ["Places|United States|Ohio|Cleveland"]
    places.clear_location(conn, [ids["rome.jpg"], ids["home.jpg"]])
    assert tags.tags_of(conn, ids["rome.jpg"]) == ["Places|Italy|Lazio|Rome"]
    assert tags.tags_of(conn, ids["home.jpg"]) == []
    assert {fid for fid, _, _ in places.located(conn)} == {ids["rome.jpg"], ids["sea.jpg"], ids["nowhere.jpg"]}
    with pytest.raises(ValueError):
        places.set_location(conn, [ids["home.jpg"]], 120.0, 0.0)


def test_no_location_tag_only_when_turned_on(lib):
    conn, ids = lib
    places.tag_pending(conn)
    assert tags.tags_of(conn, ids["home.jpg"]) == []
    Settings(conn).set("places_tag_no_location", True)
    places.tag_pending(conn)
    assert tags.tags_of(conn, ids["home.jpg"]) == ["Places|No location"]
    places.set_location(conn, [ids["home.jpg"]], *ROME)
    assert tags.tags_of(conn, ids["home.jpg"]) == ["Places|Italy|Lazio|Rome"]
    Settings(conn).set("places_tag_no_location", False)
    places.tag_pending(conn)
    assert tags.tags_of(conn, ids["nowhere.jpg"]) == []           # turned off: the tag goes again


def test_strangers_tag_their_photos_people_unknown(tmp_path):
    from test_faces import BLUE, RED, FakeBackend, photo
    from lunelis import paths
    from lunelis.raw.thumbnails import generate_pending
    from lunelis.recognize import faces
    conn = open_catalog(tmp_path / "c.db")
    root = tmp_path / "Crowd"
    root.mkdir()
    photo(root / "street.jpg", RED, BLUE)
    rid = add_root(conn, root)
    scan_root(conn, rid)
    generate_pending(conn, paths.THUMBNAIL_CACHE)
    faces.scan_files(conn, faces.pending(conn, FakeBackend.model_id), FakeBackend())
    fid = conn.execute("SELECT id FROM files WHERE root_id = ?", (rid,)).fetchone()[0]
    left, right = faces.faces_of(conn, fid)
    faces.name_faces(conn, [left.id], "Ann")
    assert faces.rest_are_strangers(conn, [fid]) == 1               # only the unnamed one
    assert set(tags.tags_of(conn, fid)) == {"People|Ann", "People|Unknown"}
    assert [f.stranger for f in faces.faces_of(conn, fid, with_strangers=True)] == [False, True]
    assert faces.groups(conn, 1) == [] and faces.counts(conn)["strangers"] == 1
    faces.name_faces(conn, [right.id], "Bob")                       # someone you do know after all
    assert set(tags.tags_of(conn, fid)) == {"People|Ann", "People|Bob"}


def test_placing_photos_from_the_library_on_the_map(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox
    QApplication.instance() or QApplication([])
    from lunelis.ui import main_window as mw
    w = mw.MainWindow()
    try:
        root = tmp_path / "Trip"
        root.mkdir()
        for n in ("a", "b"):
            Image.new("RGB", (60, 40), (10, 20, 30)).save(root / f"{n}.jpg")
        rid = add_root(w.conn, root)
        scan_root(w.conn, rid)
        w.reload()
        ids = [r[0] for r in w.conn.execute("SELECT id FROM files WHERE root_id = ?", (rid,))]
        w.grid.selected = set(ids)
        w.set_location()
        assert w.map_page.canvas.placing and w.map_page.place_bar.isVisibleTo(w.map_page)
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
        w.map_page.canvas.placed.emit(*ROME)
        assert not w.map_page.canvas.placing
        assert all(tags.tags_of(w.conn, i) == ["Places|Italy|Lazio|Rome"] for i in ids)
        # Dropping photos dragged from the library places them where they land.
        w.map_page.canvas.dropped.emit([ids[0]], *CLEVELAND)
        assert tags.tags_of(w.conn, ids[0]) == ["Places|United States|Ohio|Cleveland"]
        w.grid.selected = {ids[0]}
        w.clear_location()
        assert tags.tags_of(w.conn, ids[0]) == []
        # The canvas turns a screen point into a place and back.
        c = w.map_page.canvas
        c.resize(800, 600)
        lat, lon = c.lat_lon_at(c.rect().center())
        assert -85 <= lat <= 85 and -180 <= lon <= 180
    finally:
        w._quitting = True
        w.close()
