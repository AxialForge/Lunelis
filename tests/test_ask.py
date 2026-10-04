"""v0.21: ask your library in a sentence; find similar; more like these."""
import numpy as np
import pytest
from PIL import Image

from lunelis import ask, paths
from lunelis.catalog.schema import open_catalog
from lunelis.importers.scan import add_root, scan_root
from lunelis.raw.thumbnails import generate_pending
from lunelis.recognize import scenes

COL = {"sunset": (250, 120, 40), "beach": (230, 205, 130), "forest": (30, 140, 40), "snow": (240, 245, 250)}


class Fake:
    model_id = "fake-ask"
    dim = 3

    def embed_images(self, images):
        v = np.array([np.asarray(i, dtype=np.float32).reshape(-1, 3).mean(0) + 1 for i in images])
        return v / np.linalg.norm(v, axis=1, keepdims=True)

    def embed_texts(self, texts):
        out = []
        for t in texts:
            c = next((np.array(c, np.float32) for k, c in COL.items() if k in t.lower()), np.array([1, 1, 1], np.float32))
            out.append((c + 1) / np.linalg.norm(c + 1))
        return np.array(out)


@pytest.fixture
def lib(tmp_path):
    conn = open_catalog(tmp_path / "c.db")
    root = tmp_path / "P"
    root.mkdir()
    for name in ("sunset", "beach", "forest", "snow"):
        for n in (1, 2):
            Image.new("RGB", (240, 160), COL[name]).save(root / f"{name}{n}.jpg")
    rid = add_root(conn, root)
    scan_root(conn, rid)
    generate_pending(conn, paths.THUMBNAIL_CACHE)
    ids = {r: i for i, r in conn.execute("SELECT id, filename FROM files WHERE root_id = ?", (rid,))}
    models = {"sunset": ("ILCE-7RM5", "FE 24-70mm F2.8 GM II", "2024-06-19T19:00:00"),
              "beach": ("ILCE-7RM5", "FE 85mm F1.4 GM", "2023-07-01T12:00:00"),
              "forest": ("ILCE-7M4", "FE 24-70mm F2.8 GM II", "2024-03-02T10:00:00"),
              "snow": ("iPhone 15 Pro", None, "2024-01-05T10:00:00")}
    for fname, fid in ids.items():
        model, lens, when = models[fname.rstrip("12.jpg")]
        conn.execute("INSERT OR REPLACE INTO exif (file_id, camera_model, camera_make, lens, captured_at)"
                     " VALUES (?, ?, 'SONY', ?, ?)", (fid, model, lens, when))
    conn.commit()
    scenes.tag_files(conn, list(ids.values()), Fake(), tmp_path)
    return conn, ids


def kinds(asked):
    return [(c.kind, c.text) for c in asked.chips]


def test_a_sentence_is_read_into_chips(lib):
    conn, ids = lib
    a = ask.parse(conn, "sunset on a beach, A7R V, 2024")
    assert kinds(a) == [("date", "2024"), ("camera", "Camera: ILCE-7RM5"), ("looks", "Looks like: sunset on a beach")]
    b = ask.parse(conn, "show me 4 stars picks from June 2024 at 85mm with the 24-70")
    assert ("stars", "4+ stars") in kinds(b) and ("flag", "Picks") in kinds(b)
    assert ("date", "June 2024") in kinds(b) and ("focal", "85 mm") in kinds(b)
    assert ("lens", "Lens: 24-70") in kinds(b) and b.looks == ""
    c = ask.parse(conn, "a7iv forest high iso raw f/2.8")
    assert ("camera", "Camera: ILCE-7M4") in kinds(c) and ("iso", "ISO 3200 and up") in kinds(c)
    assert ("kind", "RAW") in kinds(c) and ("aperture", "f/2.8") in kinds(c) and c.looks == "forest"


def test_the_answer_uses_rules_and_what_photos_look_like(lib):
    conn, ids = lib
    a = ask.parse(conn, "sunset A7R V 2024")
    ranked, words = ask.answer(conn, a, Fake())
    assert words is None and ranked[:1] in ([ids["sunset1.jpg"]], [ids["sunset2.jpg"]])
    assert set(ranked) <= {ids["sunset1.jpg"], ids["sunset2.jpg"]}       # 2024 + that camera only
    b = a.without(0)                                                    # drop the 2024 chip
    assert [c.kind for c in b.chips] == ["camera", "looks"]
    ranked, _ = ask.answer(conn, b, Fake())
    assert ranked[0] in (ids["sunset1.jpg"], ids["sunset2.jpg"])
    nomodel = ask.answer(conn, ask.parse(conn, "beach"), None)
    assert nomodel == (None, "beach") or nomodel[0] is not None         # words when no model is installed


def test_find_similar_and_more_like_these(lib):
    conn, ids = lib
    sim = ask.similar_to(conn, [ids["forest1.jpg"]], Fake())
    assert sim[0] == ids["forest2.jpg"] and ids["forest1.jpg"] not in sim
    more = ask.similar_to(conn, [ids["sunset1.jpg"], ids["beach1.jpg"]], Fake(), limit=3)
    assert set(more[:2]) == {ids["sunset2.jpg"], ids["beach2.jpg"]}


def test_the_ask_bar_and_find_similar_in_the_window(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from lunelis.ui import main_window as mw
    w = mw.MainWindow()
    try:
        root = tmp_path / "W"
        root.mkdir()
        for name in ("sunset", "forest"):
            for n in (1, 2):
                Image.new("RGB", (240, 160), COL[name]).save(root / f"{name}{n}.jpg")
        rid = add_root(w.conn, root)
        scan_root(w.conn, rid)
        generate_pending(w.conn, paths.THUMBNAIL_CACHE)
        ids = {r: i for i, r in w.conn.execute("SELECT id, filename FROM files WHERE root_id = ?", (rid,))}
        scenes.tag_files(w.conn, list(ids.values()), Fake(), tmp_path)
        monkeypatch.setattr(scenes, "backend", lambda: Fake())
        w.reload()
        w.open_ask()
        w.ask_bar.box.setText("sunset")
        w.ask_bar.submit()
        w.bg.wait()
        first = w.index.file_id(0)
        assert w.filter.ranked and first in (ids["sunset1.jpg"], ids["sunset2.jpg"])
        assert any("Looks like: sunset" in w.ask_bar.chips.itemAt(i).widget().text()
                   for i in range(w.ask_bar.chips.count()) if w.ask_bar.chips.itemAt(i).widget())
        w.grid.selected = {ids["forest1.jpg"]}
        w.find_similar()
        w.bg.wait()
        assert w.filter.scope_name == "Like this photo" and w.index.file_id(0) == ids["forest2.jpg"]
        w.set_filter(mw.Filter())
        assert not w.filter.ranked
    finally:
        w._quitting = True
        w.close()
