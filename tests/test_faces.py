"""v0.35: faces - found, grouped, suggested and named; only named faces tag their photo."""
import hashlib

import numpy as np
import pytest
from PIL import Image

from lunelis import paths
from lunelis.catalog.schema import open_catalog
from lunelis.importers.scan import add_root, scan_root
from lunelis.raw.thumbnails import generate_pending
from lunelis.recognize import faces
from lunelis.settings import Settings
from lunelis.tags import model as tags

RED, BLUE, GREEN = (200, 30, 30), (30, 30, 200), (30, 200, 30)


class FakeBackend:
    """Each coloured half of a photo is a face; its fingerprint is its colour."""
    model_id = "fake-faces"

    def detect(self, rgb):
        h, w = rgb.shape[:2]
        out = []
        for i, half in enumerate((rgb[:, : w // 2], rgb[:, w // 2:])):
            c = half.reshape(-1, 3).mean(0)
            if c.max() - c.min() < 40:                      # grey: nobody there
                continue
            v = np.zeros(128, np.float32)
            v[:3] = c
            v /= np.linalg.norm(v)
            out.append(([0.1 + 0.5 * i, 0.2, 0.3, 0.4], 0.95, v))
        return out


def photo(path, left, right=None):
    img = Image.new("RGB", (400, 300), (128, 128, 128))
    if left:
        img.paste(Image.new("RGB", (200, 300), left), (0, 0))
    if right:
        img.paste(Image.new("RGB", (200, 300), right), (200, 0))
    img.save(path)


@pytest.fixture
def lib(tmp_path):
    conn = open_catalog(tmp_path / "c.db")
    root = tmp_path / "Photos"
    root.mkdir()
    photo(root / "ann1.jpg", RED)
    photo(root / "ann2.jpg", RED)
    photo(root / "bob1.jpg", BLUE)
    photo(root / "both.jpg", RED, BLUE)
    photo(root / "empty.jpg", None)
    rid = add_root(conn, root)
    scan_root(conn, rid)
    generate_pending(conn, paths.THUMBNAIL_CACHE)
    ids = {n: i for i, n in conn.execute("SELECT id, filename FROM files WHERE root_id = ?", (rid,))}
    return conn, ids, rid


def scan(conn):
    return faces.scan_files(conn, faces.pending(conn, FakeBackend.model_id), FakeBackend())


def test_faces_are_found_once_and_grouped(lib):
    conn, ids, rid = lib
    assert scan(conn) == (5, 5)
    assert scan(conn) == (0, 0)                              # each photo once per model
    groups = faces.groups(conn, min_faces=1)
    assert sorted(n for _, n, _ in groups) == [2, 3]         # three red faces, two blue
    f = faces.faces_of(conn, ids["both.jpg"])
    assert len(f) == 2 and f[0].box[0] < f[1].box[0]         # left to right, as fractions
    assert faces.crop_path(f[0].id).exists()
    assert not tags.tags_of(conn, ids["ann1.jpg"])           # nothing tagged before a name


def test_naming_tags_photos_and_suggests_the_rest(lib):
    conn, ids, rid = lib
    scan(conn)
    red = faces.faces_of(conn, ids["ann1.jpg"])[0]
    ann = faces.name_faces(conn, [red.id], "Ann")
    assert tags.tags_of(conn, ids["ann1.jpg"]) == ["People|Ann"]
    # The other red faces are suggested, not named: no tag yet.
    waiting = faces.suggested_for(conn, ann)
    assert {w.file_id for w in waiting} == {ids["ann2.jpg"], ids["both.jpg"]}
    assert "People|Ann" not in tags.tags_of(conn, ids["ann2.jpg"])
    faces.confirm(conn, [w.id for w in waiting], ann)
    assert "People|Ann" in tags.tags_of(conn, ids["both.jpg"])
    assert [p.name for p in faces.people(conn)] == ["Ann"] and faces.people(conn)[0].photos == 3


def test_a_group_is_named_at_once_and_mistakes_are_corrected(lib):
    conn, ids, rid = lib
    scan(conn)
    blue_group = next(c for c, n, _ in faces.groups(conn, 1) if n == 2)
    bob = faces.name_group(conn, blue_group, "Bob")
    assert set(faces.photos_of(conn, bob)) == {ids["bob1.jpg"], ids["both.jpg"]}
    # "Not Bob": untagged, and Bob is never suggested for that face again.
    wrong = next(f for f in faces.faces_of(conn, ids["both.jpg"]) if f.name == "Bob")
    faces.reject(conn, [wrong.id])
    assert "People|Bob" not in tags.tags_of(conn, ids["both.jpg"])
    again = next(f for f in faces.faces_of(conn, ids["both.jpg"]) if f.id == wrong.id)
    assert again.name is None and again.suggested is None
    # Move it to someone else; "not a face" takes a face out of everything.
    cara = faces.name_faces(conn, [wrong.id], "Cara")
    assert "People|Cara" in tags.tags_of(conn, ids["both.jpg"])
    faces.ignore(conn, [wrong.id])
    assert "People|Cara" not in tags.tags_of(conn, ids["both.jpg"])
    assert faces.faces_of(conn, ids["both.jpg"], with_ignored=True)[-1].ignored or \
        any(f.ignored for f in faces.faces_of(conn, ids["both.jpg"], with_ignored=True))
    assert faces.photos_of(conn, cara) == []


def test_rename_merge_and_delete_people_follow_their_tags(lib):
    conn, ids, rid = lib
    scan(conn)
    red = [f.id for f in faces.faces_where(conn, "fa.cluster IS NOT NULL") if f.file_id != ids["bob1.jpg"]]
    reds = [i for i in red if faces.faces_where(conn, "fa.id = ?", (i,))[0].box[0] < 0.5]
    ann = faces.name_faces(conn, reds, "Ann")
    faces.rename_person(conn, ann, "Anne")
    assert "People|Anne" in tags.tags_of(conn, ids["ann1.jpg"])
    assert "People|Ann" not in tags.tags_of(conn, ids["ann1.jpg"])
    other = faces.name_faces(conn, [f.id for f in faces.faces_of(conn, ids["bob1.jpg"])], "Annie")
    survivor = faces.rename_person(conn, other, "Anne")            # same name: merged
    assert survivor == ann and "People|Anne" in tags.tags_of(conn, ids["bob1.jpg"])
    faces.delete_person(conn, ann)
    assert not [t for t in tags.tags_of(conn, ids["ann1.jpg"]) if t.startswith("People")]
    assert faces.people(conn) == []


def test_sure_matches_name_themselves_only_when_turned_on(lib):
    conn, ids, rid = lib
    scan(conn)
    first = faces.faces_of(conn, ids["ann1.jpg"])[0]
    ann = faces.name_faces(conn, [first.id], "Ann")
    assert "People|Ann" not in tags.tags_of(conn, ids["ann2.jpg"])   # off: only suggested
    Settings(conn).set("faces_auto_confirm", True)
    Settings(conn).set("faces_auto_threshold", 0.9)
    faces.suggest(conn)
    assert "People|Ann" in tags.tags_of(conn, ids["ann2.jpg"])
    assert faces.suggested_for(conn, ann) == []


def test_a_hand_drawn_face_is_kept_through_rescans(lib, monkeypatch):
    conn, ids, rid = lib
    monkeypatch.setattr(faces, "backend", lambda: None)
    fid = faces.add_face(conn, ids["empty.jpg"], [0.4, 0.3, 0.2, 0.2], "Dee")
    assert "People|Dee" in tags.tags_of(conn, ids["empty.jpg"])
    scan(conn)
    assert [f.id for f in faces.faces_of(conn, ids["empty.jpg"])] == [fid]
    faces.delete_face(conn, fid)
    assert "People|Dee" not in tags.tags_of(conn, ids["empty.jpg"])


def test_the_model_files_are_pinned(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    assert not faces.available() and faces.backend() is None
    for f in faces.FILES:
        assert f.url.startswith("https://github.com/opencv/opencv_zoo/raw/") and len(f.sha256) == 64

    class Resp:
        def __init__(self):
            self.done = False

        def read(self, n):
            if self.done:
                return b""
            self.done = True
            return b"not the model"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(faces.urllib.request, "urlopen", lambda *a, **k: Resp())
    with pytest.raises(RuntimeError, match="checksum"):
        faces.download()
    assert not any(faces.folder().iterdir())
    assert hashlib.sha256(b"x").hexdigest()


def test_people_page_and_the_photo_overlay(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from lunelis.ui import main_window as mw
    monkeypatch.setattr(faces, "backend", lambda: None)          # hand-drawn faces without the models
    w = mw.MainWindow()
    try:
        root = tmp_path / "P"
        root.mkdir()
        photo(root / "a1.jpg", RED)
        photo(root / "both.jpg", RED, BLUE)
        rid = add_root(w.conn, root)
        scan_root(w.conn, rid)
        generate_pending(w.conn, paths.THUMBNAIL_CACHE)
        faces.scan_files(w.conn, faces.pending(w.conn, FakeBackend.model_id, root_id=rid), FakeBackend())
        ids = {n: i for i, n in w.conn.execute("SELECT id, filename FROM files WHERE root_id = ?", (rid,))}
        red = faces.faces_of(w.conn, ids["a1.jpg"])[0].cluster       # the window's catalog may hold other tests' faces
        ann = faces.name_group(w.conn, red, "Ann")
        w.reload()

        # The People page: Ann, her faces, and "Not Ann" taking the tag away again.
        w.open_page("People")
        page = w.people_page
        page._show_people(faces.people(w.conn))
        assert page.people_list.count() == 1 and page.people_list.item(0).text().startswith("Ann")
        page.open_person(ann)
        assert page.person_faces.count() >= 2
        before = len(faces.photos_of(w.conn, ann))
        page.person_faces.item(0).setSelected(True)
        wrong = page.person_faces.chosen()
        page._reject(wrong)
        assert len(faces.photos_of(w.conn, ann)) == before - 1

        # The overlay: boxes where the faces are; clicking one finds it; Ctrl+drag adds one.
        w.open_detail(ids["both.jpg"])
        d = w.detail
        d.set_faces_overlay(True)
        assert len(d.canvas.faces) == 2 and d.canvas.show_faces
        d.canvas.resize(800, 600)
        fid, box, _, _ = d.canvas.faces[0]
        assert d.canvas.face_at(d.canvas.face_rect(box).center()) == fid
        monkeypatch.setattr(d, "_ask_name", lambda title: "Eve")
        d._face_drawn([0.05, 0.05, 0.1, 0.1])
        assert "People|Eve" in tags.tags_of(w.conn, ids["both.jpg"])
        assert len(d.canvas.faces) == 3
        assert Settings(w.conn).get("faces_overlay") is True
        d.set_faces_overlay(False)
        assert d.canvas.faces == []
    finally:
        w._quitting = True
        w.close()


def test_merge_with_on_the_person_page(lib, monkeypatch):
    """0.47: two names for one person - Merge with... on their page."""
    from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox
    QApplication.instance() or QApplication([])
    from lunelis.ui.people_view import PeopleView
    conn, ids, rid = lib
    scan(conn)
    a = faces.name_faces(conn, [f.id for f in faces.faces_of(conn, ids["ann1.jpg"])][:1], "Ann")
    b = faces.name_faces(conn, [f.id for f in faces.faces_of(conn, ids["bob1.jpg"])], "Annie")
    view = PeopleView(conn)
    view.bg.wait()                                  # its own loads finish before the catalog closes
    view.person = b
    assert [n for _, n in view.merge_choices()] == ["Ann"]
    monkeypatch.setattr(QInputDialog, "getItem", lambda *x, **k: ("Ann", True))
    monkeypatch.setattr(QMessageBox, "question", lambda *x, **k: QMessageBox.StandardButton.Yes)
    view._merge()
    assert view.person == a and [p.name for p in faces.people(conn)] == ["Ann"]
    assert "People|Ann" in tags.tags_of(conn, ids["bob1.jpg"])
    assert not [t for t in tags.tags_of(conn, ids["bob1.jpg"]) if t == "People|Annie"]
    view.bg.wait()
