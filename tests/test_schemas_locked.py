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

GOLDEN_V1 = ("v=1;f=Vivid@40;exposure=0.3;contrast=12;rotate=90;flip_h=1;angle=1.5;crop=0.1,0.05,0.9,0.95;"
             "curve=0,0 0.5,0.6 1,1;lens=1;lens_distortion=10;mask=radial|0.5,0.5,0.22,0.28,0.5||exposure:0.6|")
GOLDEN_STACK = ("v=2;f=Vivid@40;exposure=0.3;contrast=12;rotate=90;flip_h=1;angle=1.5;crop=0.1,0.05,0.9,0.95;"
                "curve=0,0 0.5,0.6 1,1;lens=1;lens_distortion=10;mask=radial|0.5,0.5,0.22,0.28,0.5||exposure:0.6|;"
                "spot=heal|0.41,0.33,0.012|;spot=clone|0.2,0.7,0.02|0.25,0.7")


def _full_stack(spots=True):
    from lunelis.edit.retouch import Spot
    return st.Stack(filter="Vivid", amount=40, adjust={"exposure": 0.3, "contrast": 12},
                    geometry=st.Geometry(rotate=90, flip_h=True, angle=1.5, crop=(0.1, 0.05, 0.9, 0.95)),
                    curves={"rgb": ((0.0, 0.0), (0.5, 0.6), (1.0, 1.0))},
                    lens={"profile": True, "distortion": 10},
                    masks=(Mask("radial", (0.5, 0.5, 0.22, 0.28, 0.5), adjust={"exposure": 0.6}),),
                    retouch=(Spot("heal", 0.41, 0.33, 0.012), Spot("clone", 0.2, 0.7, 0.02, 0.25, 0.7)) if spots else ())


def test_edit_stack_text_is_locked():
    assert st.VERSION == 2
    assert st.dumps(_full_stack()) == GOLDEN_STACK
    assert st.loads(GOLDEN_STACK) == _full_stack()
    assert st.dumps(st.Stack()) == "v=2"                                   # the original
    # A newer Lunelis's keys are ignored, never an error.
    assert st.loads(GOLDEN_STACK + ";heal=0.2,0.3,0.01;future_tool=7") == _full_stack()


def test_version_1_stacks_still_read_and_the_catalog_is_migrated(tmp_path):
    # 0.29 bumped the stack to version 2 (retouch spots). A v1 stack - in a
    # sidecar, or a catalog from before - reads as the same edit, with no spots.
    assert st.loads(GOLDEN_V1) == _full_stack(spots=False)
    assert st.dumps(st.loads(GOLDEN_V1)) == GOLDEN_V1.replace("v=1", "v=2", 1)
    import sqlite3
    from lunelis.catalog import schema
    db = tmp_path / "old.db"
    conn = schema.open_catalog(db)
    conn.execute("INSERT INTO roots (id, path) VALUES (1, 'X:\\')")
    conn.execute("INSERT INTO files (id, root_id, rel_path, filename, ext, size_bytes, mtime)"
                 " VALUES (1, 1, 'a.jpg', 'a.jpg', 'jpg', 1, 0)")
    conn.execute("INSERT INTO edits (file_id, stack) VALUES (1, ?)", (GOLDEN_V1,))
    conn.execute("DELETE FROM schema_version WHERE version >= 34")
    conn.commit()
    conn.close()
    conn = schema.open_catalog(db)                                          # runs migration 34 again
    stored = conn.execute("SELECT stack FROM edits").fetchone()[0]
    assert stored.startswith("v=2;") and st.loads(stored) == _full_stack(spots=False)
    conn.close()


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
