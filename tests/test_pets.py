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
        assert [(lab, st) for _, _, lab, st in d.canvas.faces] == [("Rex", "pet")]    # named: a solid box (0.52.1)
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


def test_the_tags_page_shows_groups_and_picture_cards(lib):
    # 0.52: People / Pets / Scene groups, a card per tag with a picture.
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from lunelis.ui.tags_view import TagsView, _counts
    conn, ids, rid = lib
    scan(conn)
    faces.name_faces(conn, [faces.faces_of(conn, ids["ann1.jpg"])[0].id], "Ann")
    faces.name_pet(conn, [faces.faces_of(conn, ids["bob1.jpg"])[0].id], "Rex")
    tags.add(conn, [ids["empty.jpg"]], ["Holidays"])
    page = TagsView(conn)
    page._show(_counts(conn))
    groups = [page.groups.item(i).text().split()[0] for i in range(page.groups.count())]
    assert groups[:3] == ["All", "People", "Pets"] and "Your" in groups
    assert {page.cards.item(i).data(0x0100) for i in range(page.cards.count())} >= {"People|Ann", "Pets|Rex", "Holidays"}
    assert all(not page.cards.item(i).icon().isNull() for i in range(page.cards.count()))   # every card has a picture
    page.groups.setCurrentRow(groups.index("Pets"))
    assert [page.cards.item(i).text().split("\n")[0] for i in range(page.cards.count())] == ["Rex"]
    page.view_group.button(1).click()
    assert page.views.currentWidget() is page.tree


def test_the_faces_menu_adds_redraws_and_removes_by_hand(tmp_path, monkeypatch):
    # 0.53: a menu beside Faces - add a person or a pet by dragging a box,
    # redraw a box, remove any box.
    from PySide6.QtWidgets import QApplication, QInputDialog
    QApplication.instance() or QApplication([])
    from lunelis import paths
    from lunelis.importers.scan import add_root, scan_root
    from lunelis.raw.thumbnails import generate_pending
    from lunelis.ui import main_window as mw
    from test_faces import RED, photo
    monkeypatch.setattr(faces, "backend", lambda: None)
    w = mw.MainWindow()
    try:
        root = tmp_path / "P"
        root.mkdir()
        photo(root / "one.jpg", RED)
        rid = add_root(w.conn, root)
        scan_root(w.conn, rid)
        generate_pending(w.conn, paths.THUMBNAIL_CACHE)
        faces.scan_files(w.conn, faces.pending(w.conn, FakeBackend.model_id, root_id=rid), FakeBackend())
        fid = w.conn.execute("SELECT id FROM files WHERE root_id = ?", (rid,)).fetchone()[0]
        w.reload()
        w.open_detail(fid)
        d = w.detail
        found = faces.faces_of(w.conn, fid)[0]
        # Add a pet: drag a box (no Ctrl), name it.
        d.start_face_draw("pet")
        assert d.canvas.draw_mode == "pet" and d.faces_b.isChecked()
        monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: ("Rex", True)))
        d._face_drawn([0.6, 0.1, 0.2, 0.2])
        assert d.canvas.draw_mode is None and "Pets|Rex" in tags.tags_of(w.conn, fid)
        # Add a person.
        d.start_face_draw("person")
        monkeypatch.setattr(d, "_ask_name", lambda title: "Eve")
        d._face_drawn([0.05, 0.6, 0.2, 0.2])
        assert "People|Eve" in tags.tags_of(w.conn, fid)
        eve = next(f for f in faces.faces_of(w.conn, fid) if f.name == "Eve")
        # Redraw Eve's box: she keeps her name.
        d.start_face_draw(("redraw", eve.id))
        d._face_drawn([0.1, 0.5, 0.3, 0.3])
        again = next(f for f in faces.faces_of(w.conn, fid) if f.id == eve.id)
        assert again.name == "Eve" and again.box == [0.1, 0.5, 0.3, 0.3]
        # Remove: a hand-drawn box goes; a found one is remembered as "not a face".
        faces.remove_face(w.conn, eve.id)
        faces.remove_face(w.conn, found.id)
        left = faces.faces_of(w.conn, fid, with_ignored=True)
        assert eve.id not in [f.id for f in left] and next(f for f in left if f.id == found.id).ignored
        assert "People|Eve" not in tags.tags_of(w.conn, fid)
        # The menu lists what's there, and Esc backs out of drawing.
        d._fill_faces_menu()
        texts = [a.text() for a in d.faces_menu.actions()]
        assert any(t.startswith("Add a person") for t in texts) and any("Rex" in t for t in texts)
        d.start_face_draw("person")
        d._end_face_draw()
        assert d.canvas.draw_mode is None
    finally:
        for kind in (faces.PERSON, faces.PET):               # the window's catalog is shared with other tests
            for p in faces.people(w.conn, kind):
                if p.name in ("Eve", "Rex"):
                    faces.delete_person(w.conn, p.id)
        w._quitting = True
        w.close()
