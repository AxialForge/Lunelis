"""0.52: animal faces - marked by hand or by the scene model, named as pets
(tagged Pets|<name>), and kept out of the people suggestions."""
import numpy as np

from lunelis.recognize import animals, faces
from lunelis.tags import model as tags
from test_faces import FakeBackend, lib, scan  # noqa: F401 - the shared fixture


def test_an_animal_face_leaves_the_people_suggestions_and_groups(lib):
    conn, ids, rid = lib
    scan(conn)
    blue = faces.faces_of(conn, ids["bob1.jpg"])[0]
    faces.mark_animal(conn, [blue.id])
    assert all(blue.id not in [f.id for f in faces.faces_in_group(conn, c)] for c, _, _ in faces.groups(conn, 1))
    f = faces.faces_of(conn, ids["bob1.jpg"], with_strangers=True)[0]
    assert f.animal and not f.pet and f.name is None
    assert [a.id for a in faces.animal_faces(conn)] == [blue.id]


def test_a_named_pet_is_tagged_pets_and_isnt_a_person(lib):
    conn, ids, rid = lib
    scan(conn)
    blue = [f.id for n in ("bob1.jpg", "both.jpg") for f in faces.faces_of(conn, ids[n]) if f.box[0] > 0.5 or n == "bob1.jpg"]
    pid = faces.name_pet(conn, blue, "Max")
    assert "Pets|Max" in tags.tags_of(conn, ids["bob1.jpg"])
    assert not any(t.startswith("People|") for t in tags.tags_of(conn, ids["bob1.jpg"]))
    assert [p.name for p in faces.people(conn)] == []                    # not among the people
    assert [p.name for p in faces.people(conn, faces.PET)] == ["Max"]
    f = faces.faces_of(conn, ids["bob1.jpg"], with_strangers=True)[0]
    assert f.pet and f.animal and f.name == "Max"
    # A person can't take a pet's name (or the other way round): said plainly.
    import pytest
    with pytest.raises(ValueError, match="already the name of a pet"):
        faces.name_faces(conn, [faces.faces_of(conn, ids["ann1.jpg"])[0].id], "max")
    assert pid


def test_a_pet_is_never_suggested_for_a_person(lib):
    conn, ids, rid = lib
    scan(conn)
    faces.name_pet(conn, [faces.faces_of(conn, ids["bob1.jpg"])[0].id], "Max")
    faces.suggest(conn)
    right = [f for f in faces.faces_of(conn, ids["both.jpg"]) if f.box[0] > 0.5][0]
    assert right.suggested is None                       # same blue fingerprint, but Max is a pet


def test_renaming_and_forgetting_a_pet_follow_its_tag(lib):
    conn, ids, rid = lib
    scan(conn)
    face = faces.faces_of(conn, ids["bob1.jpg"])[0].id
    pid = faces.name_pet(conn, [face], "Max")
    faces.rename_person(conn, pid, "Maxie")
    assert "Pets|Maxie" in tags.tags_of(conn, ids["bob1.jpg"])
    faces.delete_person(conn, pid)
    assert not any(t.startswith("Pets|") for t in tags.tags_of(conn, ids["bob1.jpg"]))
    assert [a.id for a in faces.animal_faces(conn)] == [face]  # still an animal, just unnamed


def test_the_scene_model_sorts_out_animal_faces(lib, monkeypatch):
    conn, ids, rid = lib
    scan(conn)
    blue = {f.id for n in ("bob1.jpg", "both.jpg") for f in faces.faces_of(conn, ids[n]) if f.box[0] > 0.5
            or n == "bob1.jpg"}
    monkeypatch.setattr(animals, "available", lambda: True)

    def share(images):                                   # "blue crops are dogs"
        return np.array([1.0 if np.asarray(i)[..., 2].mean() > np.asarray(i)[..., 0].mean() else 0.0
                         for i in images], np.float32)
    monkeypatch.setattr(animals, "animal_share", share)
    assert animals.sort_out(conn) == len(blue)
    assert {a.id for a in faces.animal_faces(conn)} == blue
    # Named faces are never second-guessed.
    assert animals.sort_out(conn) == 0


def test_without_the_scene_model_nothing_is_marked(lib, monkeypatch):
    conn, ids, rid = lib
    scan(conn)
    monkeypatch.setattr(animals, "available", lambda: False)
    assert animals.sort_out(conn) == 0 and faces.animal_faces(conn) == []


def test_the_pets_tab_and_the_photo_overlay(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QInputDialog
    QApplication.instance() or QApplication([])
    from lunelis import paths
    from lunelis.importers.scan import add_root, scan_root
    from lunelis.raw.thumbnails import generate_pending
    from lunelis.ui import main_window as mw
    from test_faces import BLUE, RED, photo
    w = mw.MainWindow()
    try:
        root = tmp_path / "P"
        root.mkdir()
        photo(root / "dog.jpg", BLUE)
        photo(root / "both.jpg", RED, BLUE)
        rid = add_root(w.conn, root)
        scan_root(w.conn, rid)
        generate_pending(w.conn, paths.THUMBNAIL_CACHE)
        faces.scan_files(w.conn, faces.pending(w.conn, FakeBackend.model_id, root_id=rid), FakeBackend())
        ids = {n: i for i, n in w.conn.execute("SELECT id, filename FROM files WHERE root_id = ?", (rid,))}
        dog = faces.faces_of(w.conn, ids["dog.jpg"])[0].id
        w.reload()
        # Right-click > An animal... in the photo view, named Rex.
        w.open_detail(ids["dog.jpg"])
        d = w.detail
        d.set_faces_overlay(True)
        monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: ("Rex", True)))
        d._name_pet(dog, ask_first=True)
        assert "Pets|Rex" in tags.tags_of(w.conn, ids["dog.jpg"])
        assert [(lab, st) for _, _, lab, st in d.canvas.faces] == [("Rex", "animal")]
        d.set_faces_overlay(False)
        # The People page's Pets tab: Rex, and an unnamed animal marked there.
        page = w.people_page
        both_dog = [f.id for f in faces.faces_of(w.conn, ids["both.jpg"]) if f.box[0] > 0.5]
        monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: ("", True)))
        page._pet(both_dog)
        page._show_pets((faces.people(w.conn, faces.PET), faces.animal_faces(w.conn)))
        assert page.pet_list.count() == 1 and page.pet_list.item(0).text().startswith("Rex")
        assert page.animals.count() == 1
        assert page.tabs.tabText(page.tabs.count() - 1).startswith("Pets")
    finally:
        w._quitting = True
        w.close()
