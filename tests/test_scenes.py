"""v0.20: scene tags - a local model's suggestions, kept apart until you accept them."""
import json

import numpy as np
import pytest
from PIL import Image

from lunelis import paths
from lunelis.catalog.schema import open_catalog
from lunelis.importers.scan import add_root, scan_root
from lunelis.raw.thumbnails import generate_pending
from lunelis.recognize import clip, scenes
from lunelis.tags import model as tags

COLOURS = {"Beach": (230, 200, 120), "Forest": (30, 140, 40), "Night sky": (10, 10, 40)}


class FakeModel:
    """Embeds a photo by its average colour; each label prompt by its colour."""
    model_id = "fake-v1"
    dim = 3

    def __init__(self):
        self.images = 0

    def embed_images(self, images):
        self.images += len(images)
        v = np.array([np.asarray(i, dtype=np.float32).reshape(-1, 3).mean(0) + 1 for i in images])
        return v / np.linalg.norm(v, axis=1, keepdims=True)

    def embed_texts(self, texts):
        out = []
        for t in texts:
            name = next((n for n in COLOURS if n.lower() in t.lower()), None)
            c = np.array(COLOURS[name], dtype=np.float32) + 1 if name else np.array([128, 0, 128], np.float32)
            out.append(c / np.linalg.norm(c))
        return np.array(out)


@pytest.fixture
def lib(tmp_path, monkeypatch):
    conn = open_catalog(tmp_path / "c.db")
    root = tmp_path / "Photos"
    root.mkdir()
    for name, colour in (("beach1", "Beach"), ("beach2", "Beach"), ("woods", "Forest"), ("stars", "Night sky")):
        Image.new("RGB", (300, 200), COLOURS[colour]).save(root / f"{name}.jpg")
    rid = add_root(conn, root)
    scan_root(conn, rid)
    generate_pending(conn, paths.THUMBNAIL_CACHE)
    ids = {r: i for i, r in conn.execute("SELECT id, filename FROM files WHERE root_id = ?", (rid,))}
    data = tmp_path / "data"
    data.mkdir()
    (data / scenes.USER_FILE).write_text(json.dumps({"labels": [
        {"name": n, "off": True} for n in (l.name for l in scenes.labels()) if n not in COLOURS]}), encoding="utf-8")
    return conn, ids, rid, data


def test_photos_get_suggestions_kept_apart_from_tags_and_sidecars(lib):
    conn, ids, rid, data = lib
    model = FakeModel()
    done, made = scenes.tag_files(conn, list(ids.values()), model, data)
    assert done == 4 and made >= 4
    assert [s[0] for s in scenes.suggestions_of(conn, ids["beach1.jpg"])][0] == "Scene|Beach"
    assert tags.tags_of(conn, ids["beach1.jpg"]) == []                  # not a tag until accepted
    assert all(n != "Scene|Beach" for n, _ in tags.all_tags(conn))
    from lunelis.ui.library import Filter, LibraryIndex
    idx = LibraryIndex()
    idx.load(conn, "name", Filter(tag="Scene|Beach"))
    assert len(idx) == 0
    from lunelis.xmp.sync import EXPORT_SQL
    assert "confidence IS NULL" in EXPORT_SQL
    assert {t: n for t, n, _ in scenes.queue(conn)}["Scene|Beach"] == 2


def test_the_job_reads_thumbnails_only_and_only_new_photos(lib, monkeypatch):
    conn, ids, rid, data = lib
    import lunelis.edit.render as render
    import lunelis.raw.thumbnails as thumbs
    monkeypatch.setattr(render, "load_source", lambda *a, **k: pytest.fail("decoded a photo"))
    monkeypatch.setattr(thumbs, "render", lambda *a, **k: pytest.fail("decoded a photo"))
    model = FakeModel()
    monkeypatch.setattr(scenes, "backend", lambda: model)
    monkeypatch.setattr(paths, "DATA_DIR", data)
    from lunelis.jobs import engine
    job = engine.create_job(conn, "scene_tags", "Scene tags", [(rid, None)])
    assert engine.run_job(conn, job, idle=lambda: 1e9).state == "done"
    assert model.images == 4
    assert scenes.pending_in_folder(conn, rid, "", model.model_id) == []
    job2 = engine.create_job(conn, "scene_tags", "Scene tags", [(rid, None)])
    engine.run_job(conn, job2, idle=lambda: 1e9)
    assert model.images == 4                                            # nothing read twice


def test_accept_reject_and_bulk_accept(lib):
    conn, ids, rid, data = lib
    model = FakeModel()
    scenes.tag_files(conn, list(ids.values()), model, data)
    assert scenes.accept(conn, "Scene|Beach", [ids["beach1.jpg"]]) == 1
    assert tags.tags_of(conn, ids["beach1.jpg"]) == ["Scene|Beach"]     # a tag now
    assert scenes.reject(conn, "Scene|Beach", [ids["beach2.jpg"]]) == 1
    scenes.rescore(conn, model, data)
    assert all(t != "Scene|Beach" for t, _ in scenes.suggestions_of(conn, ids["beach2.jpg"]))  # stays rejected
    conf = dict(scenes.suggested_photos(conn, "Scene|Forest"))[ids["woods.jpg"]]
    assert scenes.accept_above(conn, "Scene|Forest", conf + 0.01) == 0
    assert scenes.accept_above(conn, "Scene|Forest", conf - 0.01) == 1
    tags.add(conn, [ids["stars.jpg"]], ["Scene|Night sky"])              # tagging it yourself accepts it
    assert tags.tags_of(conn, ids["stars.jpg"]) == ["Scene|Night sky"]
    assert not scenes.suggestions_of(conn, ids["stars.jpg"])


def test_a_user_tag_is_never_turned_into_a_suggestion(lib):
    conn, ids, rid, data = lib
    tags.add(conn, [ids["woods.jpg"]], ["Scene|Forest"])
    scenes.tag_files(conn, [ids["woods.jpg"]], FakeModel(), data)
    assert tags.tags_of(conn, ids["woods.jpg"]) == ["Scene|Forest"]
    assert not scenes.suggestions_of(conn, ids["woods.jpg"])


def test_labels_file_adds_changes_and_turns_off(tmp_path):
    (tmp_path / scenes.USER_FILE).write_text(json.dumps({"labels": [
        {"name": "Beach", "off": True}, {"name": "Skatepark", "prompt": "a photo of a skatepark"},
        {"name": "Food", "prompt": "a photo of a meal on a plate"}]}), encoding="utf-8")
    labs = {l.name: l for l in scenes.labels(tmp_path)}
    assert "Beach" not in labs and labs["Skatepark"].tag == "Scene|Skatepark"
    assert labs["Food"].prompt == "a photo of a meal on a plate"


def test_a_model_file_with_the_wrong_checksum_is_refused(tmp_path, monkeypatch):
    src = tmp_path / "bad.onnx"
    src.write_bytes(b"not the model")
    monkeypatch.setattr(clip, "folder", lambda: tmp_path / "models")
    monkeypatch.setattr(clip, "FILES", (clip.File("x.onnx", src.as_uri(), "0" * 64, 13),))
    monkeypatch.setattr(clip, "TOTAL", 13)
    with pytest.raises(RuntimeError, match="checksum"):
        clip.download()
    assert not (tmp_path / "models" / "x.onnx").exists()


def test_the_tokenizer_matches_clip(tmp_path):
    """CLIP's own ids for a known sentence (49406 start, 49407 end)."""
    if not clip.available():
        pytest.skip("the scene model isn't downloaded on this PC")
    tok = clip._tokenizer()
    assert tok.encode("a photo of a cat") == [49406, 320, 1125, 539, 320, 2368, 49407]


def test_the_review_tab_accepts_and_rejects(lib, monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    conn, ids, rid, data = lib
    scenes.tag_files(conn, list(ids.values()), FakeModel(), data)
    from lunelis.ui.scene_review import SceneReview
    page = SceneReview(conn)
    page.refresh()
    page.bg.wait()
    tags_shown = [page.tags.item(i).data(0x0100) for i in range(page.tags.count())]
    assert "Scene|Beach" in tags_shown
    page.tags.setCurrentRow(tags_shown.index("Scene|Beach"))
    assert sorted(page.ticked() + [page.photos.item(i).data(0x0100) for i in range(page.photos.count())
                                   if page.photos.item(i).data(0x0100) not in page.ticked()]) == \
        sorted([ids["beach1.jpg"], ids["beach2.jpg"]])
    page._tick_all(False)
    page.photos.item(0).setCheckState(page.photos.item(0).checkState().Checked)
    first = page.photos.item(0).data(0x0100)
    page.accept_ticked()
    page.bg.wait()
    assert tags.tags_of(conn, first) == ["Scene|Beach"]
    page.tags.setCurrentRow([page.tags.item(i).data(0x0100) for i in range(page.tags.count())].index("Scene|Beach"))
    page._tick_all(True)
    page.reject_ticked()
    page.bg.wait()
    assert not [t for t, _ in scenes.suggestions_of(conn, ids["beach2.jpg"] if first == ids["beach1.jpg"] else ids["beach1.jpg"]) if t == "Scene|Beach"]


def test_settings_card_shows_the_model_state(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from lunelis.ui.settings_view import SettingsView
    monkeypatch.setattr(clip, "available", lambda: False)
    conn = open_catalog(tmp_path / "s.db")
    view = SettingsView(conn)
    assert view.scene_b.text() == "Download and turn on" and not view.scene_job_b.isEnabled()
    monkeypatch.setattr(clip, "available", lambda: True)
    view._load_scene_tags()
    assert view.scene_b.text() == "Remove" and view.scene_job_b.isEnabled() and view.scene_status.text().startswith("On")
