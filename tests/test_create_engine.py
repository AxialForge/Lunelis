"""create/engine.py: presets, fitting, never-overwrite saving, the folder filter."""
import json

import pytest
from PIL import Image

from lunelis.catalog.schema import open_catalog
from lunelis.create import engine
from lunelis.importers.scan import add_root, scan_root
from lunelis.ui.library import Filter, LibraryIndex


def test_builtin_presets_load_and_original_size_is_always_there(tmp_path):
    names = [p.name for p in engine.presets(tmp_path)]
    assert "Original size" in names and "Instagram portrait (1080 x 1350)" in names
    for p in engine.presets(tmp_path):
        p.check()


def test_the_users_preset_file_adds_and_replaces(tmp_path):
    (tmp_path / engine.USER_FILE).write_text(json.dumps({"presets": [
        {"name": "Web (2048 px)", "width": 1600, "height": 1600, "fit": "inside", "format": "webp", "quality": 70},
        {"name": "Print 6x4", "width": 1800, "height": 1200, "fit": "fill", "format": "jpeg", "quality": 95},
    ]}), encoding="utf-8")
    got = {p.name: p for p in engine.presets(tmp_path)}
    assert got["Web (2048 px)"].width == 1600 and got["Web (2048 px)"].format == "webp"
    assert got["Print 6x4"].fit == "fill"


def test_a_broken_preset_file_is_reported(tmp_path):
    (tmp_path / engine.USER_FILE).write_text('{"presets": [{"name": "Bad", "format": "bmp"}]}', encoding="utf-8")
    with pytest.raises(engine.PresetError):
        engine.presets(tmp_path)


def test_a_user_preset_file_to_edit_is_made_from_the_builtins(tmp_path):
    made = engine.write_user_presets(tmp_path)
    assert made.exists() and [p.name for p in engine.presets(tmp_path)] == [p.name for p in engine.presets()]


def test_fit_inside_never_enlarges_and_fill_crops_exactly():
    img = Image.new("RGB", (4000, 3000))
    inside = engine.fit(img, engine.Preset("x", 2048, 2048, "inside"))
    assert inside.size == (2048, 1536)
    fill = engine.fit(img, engine.Preset("x", 1080, 1350, "fill"))
    assert fill.size == (1080, 1350)
    small = Image.new("RGB", (800, 600))
    assert engine.fit(small, engine.Preset("x", 2048, 2048, "inside")).size == (800, 600)


def test_save_never_overwrites_and_writes_each_format(tmp_path):
    img = Image.new("RGB", (64, 48), "red")
    paths = [engine.save(img, engine.Preset("x", format=f), tmp_path, "Out") for f in engine.FORMATS]
    assert [p.rsplit(".", 1)[1] for p in paths] == ["jpg", "png", "webp", "tif"]
    again = engine.save(img, engine.Preset("x"), tmp_path, "Out")
    assert again.endswith("Out (2).jpg")
    assert engine.save(img, engine.Preset("x"), tmp_path, 'a:b*c?').endswith("a_b_c_.jpg")


def test_output_folder_defaults_to_pictures(tmp_path):
    conn = open_catalog(tmp_path / "c.db")
    assert engine.output_dir(conn).name == "Lunelis creations"
    from lunelis.settings import Settings
    Settings(conn).set("create_output_dir", str(tmp_path / "mine"))
    assert engine.output_dir(conn) == tmp_path / "mine"


def test_folder_filter_takes_a_folder_and_below_only(tmp_path):
    conn = open_catalog(tmp_path / "c.db")
    root = tmp_path / "Photos"
    for rel in ("2026/Trip/a.jpg", "2026/Trip/day 2/b.jpg", "2026/Trip_x/c.jpg", "2025/d.jpg"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (32, 32)).save(p)
    rid = add_root(conn, root)
    scan_root(conn, rid)
    idx = LibraryIndex()
    idx.load(conn, None, Filter(folder=(rid, "2026/Trip")))
    names = sorted(conn.execute("SELECT filename FROM files WHERE id = ?", (idx.file_id(i),)).fetchone()[0]
                   for i in range(len(idx)))
    assert names == ["a.jpg", "b.jpg"]                      # not Trip_x (an escaped "_")
    idx.load(conn, None, Filter(folder=(rid, "")))
    assert len(idx) == 4
