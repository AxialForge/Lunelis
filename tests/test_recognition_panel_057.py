"""0.57: the recognition panel beside the photo, and the dialog that asks for a name."""
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QToolButton

from lunelis.recognize import faces
from lunelis.tags import model as tags
from lunelis.ui import recognition_panel as rp
from test_faces import FakeBackend, lib, scan  # noqa: F401 - the shared fixture


def _rows(panel):
    return {fid: w for fid, w in panel.rows.items()}


def _button(row, name):
    return next(b for b in row.findChildren(QToolButton) if b.objectName() == name)


def test_a_suggested_name_is_answered_yes_or_no_in_the_panel(lib):
    QApplication.instance() or QApplication([])
    conn, ids, rid = lib
    scan(conn)
    red = faces.faces_of(conn, ids["ann1.jpg"])[0]
    faces.name_faces(conn, [red.id], "Ann")
    faces.suggest(conn)
    f = faces.faces_of(conn, ids["ann2.jpg"])[0]
    assert f.suggested == "Ann" and f.name is None
    p = rp.RecognitionPanel(conn)
    seen = []
    p.changed.connect(lambda: seen.append(1))
    p.show_photo(ids["ann2.jpg"])
    _button(p.rows[f.id], "Yes").click()
    assert faces.faces_of(conn, ids["ann2.jpg"])[0].name == "Ann" and seen
    assert "People|Ann" in tags.tags_of(conn, ids["ann2.jpg"])
    # "no" on the other photo: unnamed again and never suggested for that face
    other = next(x for x in faces.faces_of(conn, ids["both.jpg"]) if x.suggested == "Ann")
    p.show_photo(ids["both.jpg"])
    _button(p.rows[other.id], "No").click()
    again = next(x for x in faces.faces_of(conn, ids["both.jpg"]) if x.id == other.id)
    assert again.name is None and again.suggested != "Ann"


def test_the_panel_lists_people_animals_and_the_ways_to_add_one(lib):
    QApplication.instance() or QApplication([])
    conn, ids, rid = lib
    scan(conn)
    two = faces.faces_of(conn, ids["both.jpg"])
    faces.name_pet(conn, [two[1].id], "Max")
    p = rp.RecognitionPanel(conn)
    asked = []
    p.add_face.connect(asked.append)
    p.show_photo(ids["both.jpg"])
    texts = [b.text() for b in p.findChildren(QPushButton)]
    assert "Add a face by hand…" in texts and "Add a pet or animal by hand…" in texts
    assert "Look for faces again" in texts
    assert set(p.rows) == {two[0].id, two[1].id}
    for t in ("Add a face by hand…", "Add a pet or animal by hand…"):
        next(b for b in p.findChildren(QPushButton) if b.text() == t).click()
    assert asked == ["person", "pet"]
    p.show_photo(ids["empty.jpg"])
    assert not p.rows                                              # and it says so, with the buttons still there
    assert "Add a face by hand…" in [b.text() for b in p.findChildren(QPushButton)]
    p.show_photo(ids["empty.jpg"], video=True)
    assert not p.findChildren(QPushButton)


def test_scene_suggestions_are_answered_in_the_panel(lib):
    QApplication.instance() or QApplication([])
    conn, ids, rid = lib
    fid = ids["empty.jpg"]
    for name, conf in (("Scenes|Beach", 0.91), ("Scenes|Snow", 0.4)):
        tid = tags.tag_id(conn, name, create=True)
        conn.execute("INSERT INTO file_tags (file_id, tag_id, confidence) VALUES (?, ?, ?)", (fid, tid, conf))
    conn.commit()
    p = rp.RecognitionPanel(conn)
    p.show_photo(fid)
    p._scene("Scenes|Beach", True)
    p._scene("Scenes|Snow", False)
    from lunelis.recognize import scenes
    assert scenes.suggestions_of(conn, fid) == []
    assert "Scenes|Beach" in tags.tags_of(conn, fid) and "Scenes|Snow" not in tags.tags_of(conn, fid)


def test_the_name_dialog_narrows_as_you_type_and_offers_the_other_answers(lib):
    QApplication.instance() or QApplication([])
    conn, ids, rid = lib
    scan(conn)
    red = faces.faces_of(conn, ids["ann1.jpg"])[0]
    blue = faces.faces_of(conn, ids["bob1.jpg"])[0]
    faces.name_faces(conn, [red.id], "Ann")
    faces.name_faces(conn, [blue.id], "Bob")
    d = rp.NameDialog(conn, red.id, suggested="Bob")
    assert d.list.item(0).text() == "Bob" and d.box.text() == "Bob"       # the suggestion first, ready to accept
    d.box.setText("an")
    assert [d.list.item(i).text() for i in range(d.list.count())] == ["Ann"]
    assert d.ok_b.text().startswith("Add")                                 # "an" itself would be a new person
    QTest.keyClick(d.box, Qt.Key.Key_Down)
    assert d.box.text() == "Ann" and d.ok_b.text() == "Name"
    d._accept_name()
    assert (d.choice, d.name) == ("name", "Ann")
    d = rp.NameDialog(conn, red.id)
    d.box.setText("Unknown")
    d._accept_name()
    assert d.choice is None and "strangers" in d.new_hint.text()
    assert {b.text() for b in d.findChildren(QPushButton)} >= {"Stranger", "It's an animal", "Not a face", "Cancel"}
    d._done("stranger")
    assert d.choice == "stranger"
    assert not d.ok_b.isEnabled() or d.box.text()                          # an empty name can't be accepted


def test_the_panel_comes_with_the_face_boxes_in_the_photo_view(tmp_path, monkeypatch):
    from test_issues_ui_054 import _shut, _window
    w, ids, _ = _window(tmp_path, 3)
    try:
        w.open_detail(ids[0])
        d = w.detail
        d.set_faces_overlay(False)
        assert not d.recog.isVisibleTo(d)
        d.set_faces_overlay(True)                                  # the Faces button / F
        assert d.recog.isVisibleTo(d) and d.recog.file_id == ids[0]
        d.go(d.pos + 1)
        assert d.recog.file_id == d.info.file_id                   # it follows the photo
        # a face added by hand shows up in the panel, and its row lights its box
        fid = faces.add_face(w.conn, d.info.file_id, [0.1, 0.1, 0.2, 0.2], None)
        d._face_changed()
        assert fid in d.recog.rows
        d.recog.hover.emit(fid)
        assert d.canvas.hot_face == fid
        # naming from the panel goes through the one dialog and reaches the photo
        monkeypatch.setattr(rp, "ask_name", lambda *a, **k: ("name", "Eve"))
        d._name_face(fid)
        assert any(lab == "Eve" for _, _, lab, _ in d.canvas.faces)
        asked = []
        monkeypatch.setattr(d, "start_face_draw", asked.append)
        d.recog.add_face.emit("pet")
        d.set_editing(True)
        assert not d.recog.isVisibleTo(d)                          # away while editing
        d.set_editing(False)
        d.set_faces_overlay(False)
        w.close_detail()
    finally:
        _shut(w)
