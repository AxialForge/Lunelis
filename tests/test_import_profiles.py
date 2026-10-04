"""v0.16.0: camera profiles, sidecars filed with their clip, re-inserted
cards, and clearing a card once everything is verified."""
import json
import os
from datetime import datetime

import piexif
import pytest
from PIL import Image

from lunelis.catalog.schema import open_catalog
from lunelis.importing import ingest
from lunelis.importing import profiles
from lunelis.importing.templates import DEFAULT_TEMPLATE


def jpeg(path, when, seed=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    exif = piexif.dump({"0th": {piexif.ImageIFD.Model: b"ILCE-7RM5"},
                        "Exif": {piexif.ExifIFD.DateTimeOriginal: when.encode()}})
    Image.effect_noise((48, 32), 30 + seed).convert("RGB").save(path, "JPEG", exif=exif, quality=90)


def clip(path, seed=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0\0\0\x18ftypXAVC" + bytes([seed]) * 4000)


@pytest.fixture
def sony(tmp_path):
    c = tmp_path / "card"
    jpeg(c / "DCIM/100MSDCF/DSC00001.JPG", "2026:06:19 10:00:00", 1)
    jpeg(c / "DCIM/101MSDCF/DSC00002.JPG", "2026:06:19 10:05:00", 2)              # a second DCIM folder
    clip(c / "PRIVATE/M4ROOT/CLIP/C0001.MP4", 1)
    (c / "PRIVATE/M4ROOT/CLIP/C0001M01.XML").write_text("<NonRealTimeMeta/>", encoding="utf-8")
    clip(c / "PRIVATE/M4ROOT/SUB/C0001S03.MP4", 2)                                 # proxy: never imported
    (c / "PRIVATE/M4ROOT/THMBNL").mkdir(parents=True)
    jpeg(c / "PRIVATE/M4ROOT/THMBNL/C0001T01.JPG", "2026:06:19 10:00:00", 3)       # thumbnail: never imported
    return c


@pytest.fixture
def env(tmp_path, sony):
    conn = open_catalog(tmp_path / "cat.db")
    lib = tmp_path / "Library"
    lib.mkdir()
    cfg = ingest.Settings_(destination=str(lib), template=DEFAULT_TEMPLATE, staging_local=str(tmp_path / "staging"),
                           staging_network=None, reserve_bytes=0)
    yield conn, sony, lib, cfg
    conn.close()


def _items(conn, imp):
    return {rel: (state, dest, parent) for rel, state, dest, parent in conn.execute(
        "SELECT source_rel, state, dest_path, parent_id FROM import_items WHERE import_id = ?", (imp,))}


def test_profiles_are_detected_from_the_card_layout(tmp_path, sony):
    assert profiles.detect(str(sony)).id == "sony"
    canon = tmp_path / "canon"
    jpeg(canon / "DCIM/100CANON/IMG_0001.JPG", "2026:06:19 10:00:00")
    assert profiles.detect(str(canon)).id == "canon"
    plain = tmp_path / "folder"
    jpeg(plain / "holiday/a.jpg", "2026:06:19 10:00:00")
    assert profiles.detect(str(plain)).id == "generic"
    assert profiles.for_make("SONY").id == "sony" and profiles.for_make("NIKON CORPORATION").id == "nikon"
    assert profiles.for_make("Leica") is None


def test_a_profile_in_the_data_folder_adds_or_replaces_one(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "camera_profiles.json").write_text(json.dumps({"profiles": [
        {"id": "pentax", "name": "Pentax", "markers": ["DCIM/*PENTX"], "media_dirs": ["DCIM"]},
        {"id": "sony", "name": "Sony (mine)", "markers": ["PRIVATE/M4ROOT"], "media_dirs": ["DCIM"]}]}),
        encoding="utf-8")
    loaded = profiles.load(data)
    by_id = {p.id: p for p in loaded}
    assert by_id["pentax"].name == "Pentax" and by_id["sony"].name == "Sony (mine)"
    assert loaded[-1].id == "generic"                                              # always the fallback
    (data / "camera_profiles.json").write_text("not json", encoding="utf-8")
    assert {p.id for p in profiles.load(data)} >= {"sony", "generic"}            # a broken file is ignored


def test_discovery_skips_proxies_and_thumbnails_and_finds_sidecars(sony):
    media = ingest.discover(str(sony))
    assert [r for r, _, _ in media] == ["DCIM/100MSDCF/DSC00001.JPG", "DCIM/101MSDCF/DSC00002.JPG",
                                        "PRIVATE/M4ROOT/CLIP/C0001.MP4"]
    side = ingest.sidecars_of(str(sony), media)
    assert [r for r, _, _ in side["PRIVATE/M4ROOT/CLIP/C0001.MP4"]] == ["PRIVATE/M4ROOT/CLIP/C0001M01.XML"]


def test_a_clip_and_its_sidecar_are_filed_together(env):
    conn, card, lib, cfg = env
    imp = ingest.create_import(conn, str(card), cfg)
    assert conn.execute("SELECT profile FROM imports WHERE id = ?", (imp,)).fetchone()[0] == "sony"
    s = ingest.run(conn, imp, cfg)
    assert s["safe_to_format"]
    items = _items(conn, imp)
    mp4 = items["PRIVATE/M4ROOT/CLIP/C0001.MP4"]
    xml = items["PRIVATE/M4ROOT/CLIP/C0001M01.XML"]
    assert xml[0] == "placed" and xml[2] is not None
    assert os.path.dirname(xml[1]) == os.path.dirname(mp4[1])                     # same folder as its clip
    assert os.path.basename(xml[1]) == "C0001M01.XML"                            # never renamed
    assert open(xml[1], encoding="utf-8").read() == "<NonRealTimeMeta/>"


def test_a_sidecar_written_late_is_still_picked_up(env):
    conn, card, lib, cfg = env
    (card / "PRIVATE/M4ROOT/CLIP/C0001M01.XML").unlink()
    imp = ingest.create_import(conn, str(card), cfg)
    assert "PRIVATE/M4ROOT/CLIP/C0001M01.XML" not in _items(conn, imp)
    (card / "PRIVATE/M4ROOT/CLIP/C0001M01.XML").write_text("<late/>", encoding="utf-8")   # the camera finishes
    ingest.stage(conn, imp, cfg)
    assert _items(conn, imp)["PRIVATE/M4ROOT/CLIP/C0001M01.XML"][0] == "staged"


def test_a_reinserted_card_is_not_copied_again(env):
    conn, card, lib, cfg = env
    first = ingest.create_import(conn, str(card), cfg)
    ingest.run(conn, first, cfg)
    jpeg(card / "DCIM/101MSDCF/DSC00003.JPG", "2026:06:19 11:00:00", 4)            # one new photo since
    second = ingest.create_import(conn, str(card), cfg)
    items = _items(conn, second)
    assert items["DCIM/101MSDCF/DSC00003.JPG"][0] == "pending"
    seen_before = [r for r, (st, _, _) in items.items() if st == "already_in_library"]
    assert len(seen_before) == 4                                                   # 2 photos, clip, sidecar
    ingest.run(conn, second, cfg)
    assert _items(conn, second)["DCIM/101MSDCF/DSC00003.JPG"][0] == "placed"


def test_clearing_the_card_only_after_everything_is_verified(env):
    conn, card, lib, cfg = env
    imp = ingest.create_import(conn, str(card), cfg)
    ingest.stage(conn, imp, cfg)
    assert ingest.clearable(conn, imp) == []
    assert ingest.clear_card(conn, imp)[0] == 0                                   # not yet: nothing deleted
    assert (card / "DCIM/100MSDCF/DSC00001.JPG").exists()
    ingest.place(conn, imp, cfg)
    # A photo changed on the card since it was imported is left alone.
    os.utime(card / "DCIM/101MSDCF/DSC00002.JPG", (0, 0))
    deleted, skipped = ingest.clear_card(conn, imp)
    assert deleted == 3 and skipped == ["DCIM/101MSDCF/DSC00002.JPG"]
    assert not (card / "PRIVATE/M4ROOT/CLIP/C0001.MP4").exists()
    assert (card / "PRIVATE/M4ROOT/SUB/C0001S03.MP4").exists()                    # never imported: kept
    assert all(os.path.exists(d) for (_, d, _) in _items(conn, imp).values())   # the library copies remain


def test_a_card_pulled_mid_preview_is_said_not_shown_as_empty(tmp_path):
    from lunelis.ui.import_view import PreviewWorker
    got = []
    w = PreviewWorker(str(tmp_path / "gone"))
    w.done.connect(got.append)
    w.run()
    assert isinstance(got[0], OSError) and "removed" in str(got[0])
