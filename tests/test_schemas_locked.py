"""v0.16.0: formats other work builds on are locked (docs/Schemas.md).

If one of these fails, a stored format changed: older catalogs and sidecars
would be misread. Change the format only with a version bump and a loader
for the old form - then update docs/Schemas.md and these golden values."""
import json

from lunelis.catalog.schema import open_catalog
from lunelis.edit import stack as st
from lunelis.edit.masks import Mask
from lunelis.importing import profiles
from lunelis.xmp.sync import EXPORT_SQL

GOLDEN_STACK = ("v=1;f=Vivid@40;exposure=0.3;contrast=12;rotate=90;flip_h=1;angle=1.5;crop=0.1,0.05,0.9,0.95;"
                "curve=0,0 0.5,0.6 1,1;lens=1;lens_distortion=10;mask=radial|0.5,0.5,0.22,0.28,0.5||exposure:0.6|")


def _full_stack():
    return st.Stack(filter="Vivid", amount=40, adjust={"exposure": 0.3, "contrast": 12},
                    geometry=st.Geometry(rotate=90, flip_h=True, angle=1.5, crop=(0.1, 0.05, 0.9, 0.95)),
                    curves={"rgb": ((0.0, 0.0), (0.5, 0.6), (1.0, 1.0))},
                    lens={"profile": True, "distortion": 10},
                    masks=(Mask("radial", (0.5, 0.5, 0.22, 0.28, 0.5), adjust={"exposure": 0.6}),))


def test_edit_stack_text_is_locked():
    assert st.VERSION == 1
    assert st.dumps(_full_stack()) == GOLDEN_STACK
    assert st.loads(GOLDEN_STACK) == _full_stack()
    assert st.dumps(st.Stack()) == "v=1"                                   # the original
    # A newer Lunelis's keys are ignored, never an error.
    assert st.loads(GOLDEN_STACK + ";heal=0.2,0.3,0.01;future_tool=7") == _full_stack()


def test_only_user_tags_reach_sidecars(tmp_path):
    conn = open_catalog(tmp_path / "c.db")
    conn.execute("INSERT INTO roots (id, path) VALUES (1, 'X:\\\\')")
    conn.execute("INSERT INTO files (id, root_id, rel_path, filename, ext, size_bytes, mtime) VALUES (1, 1, 'a.jpg', 'a.jpg', 'jpg', 1, 0)")
    conn.execute("INSERT INTO tags (id, name) VALUES (1, 'Places|Ohio'), (2, 'Scene|Beach')")
    conn.execute("INSERT INTO file_tags (file_id, tag_id, confidence) VALUES (1, 1, NULL), (1, 2, 0.83)")
    conn.execute("INSERT INTO ratings (file_id, stars, xmp_pending) VALUES (1, 3, 1)")
    row = conn.execute(EXPORT_SQL).fetchone()
    assert row[-1] == "Places|Ohio"                                        # the suggestion stays out
    conn.close()


def test_camera_profile_format_is_locked():
    raw = json.loads(profiles.BUILT_IN.read_text(encoding="utf-8"))
    allowed = {"id", "name", "makes", "markers", "media_dirs", "skip_dirs", "sidecars", "companions", "note"}
    for p in raw["profiles"]:
        assert set(p) <= allowed and "id" in p
        for s in p.get("sidecars", []) + p.get("companions", []):
            assert set(s) == {"for", "names"}
    loaded = profiles.load()
    assert loaded[-1].id == "generic"
    sony = next(p for p in loaded if p.id == "sony")
    assert sony.sidecar_names("C0001.MP4")[:2] == ["C0001M01.XML", "C0001M01.xml"]
    apple = next(p for p in loaded if p.id == "apple")
    assert apple.companion_names("IMG_0001.HEIC") == ["IMG_0001.MOV", "IMG_0001.mov"]
    assert "IMG_0001.AAE" in apple.sidecar_names("IMG_0001.HEIC")
