"""v0.18: phones and USB sticks - iPhone / Android layouts, Live Photos kept
together, .AAE edit files, motion photos, duplicates skipped before copying,
and the offer when a stick appears."""
import os
from pathlib import Path

import piexif
import pytest
from PIL import Image

from lunelis.catalog.schema import open_catalog
from lunelis.importing import ingest, profiles
from lunelis.importing.templates import DEFAULT_TEMPLATE


def photo(path, when, seed=1, make=b"Apple", model=b"iPhone 15 Pro", tail=b""):
    path.parent.mkdir(parents=True, exist_ok=True)
    exif = piexif.dump({"0th": {piexif.ImageIFD.Make: make, piexif.ImageIFD.Model: model},
                        "Exif": {piexif.ExifIFD.DateTimeOriginal: when.encode()}})
    Image.effect_noise((48, 32), 30 + seed).convert("RGB").save(path, "JPEG", exif=exif, quality=90)
    if tail:
        with open(path, "ab") as f:
            f.write(tail)


def movie(path, seed=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0\0\0\x14ftypqt  " + bytes([seed]) * 3000)


@pytest.fixture
def iphone(tmp_path):
    c = tmp_path / "iphone"
    photo(c / "DCIM/100APPLE/IMG_0001.JPG", "2026:07:04 18:00:00", 1)
    movie(c / "DCIM/100APPLE/IMG_0001.MOV", 1)                          # its Live Photo video
    (c / "DCIM/100APPLE/IMG_0001.AAE").write_text("<plist/>", encoding="utf-8")   # its edit
    photo(c / "DCIM/100APPLE/IMG_0002.JPG", "2026:07:04 18:01:00", 2)
    movie(c / "DCIM/101APPLE/IMG_0100.MOV", 5)                          # a plain video, no photo
    return c


@pytest.fixture
def env(tmp_path):
    conn = open_catalog(tmp_path / "cat.db")
    lib = tmp_path / "Library"
    lib.mkdir()
    cfg = ingest.Settings_(destination=str(lib), template=DEFAULT_TEMPLATE, staging_local=str(tmp_path / "staging"),
                           staging_network=None, reserve_bytes=0)
    yield conn, lib, cfg
    conn.close()


def _items(conn, imp):
    return {rel: (state, dest, parent, kind) for rel, state, dest, parent, kind in conn.execute(
        "SELECT source_rel, state, dest_path, parent_id, kind FROM import_items WHERE import_id = ?", (imp,))}


def test_phone_layouts_are_recognised(tmp_path, iphone):
    assert profiles.detect(str(iphone)).id == "apple"
    android = tmp_path / "android"
    photo(android / "DCIM/Camera/PXL_20260704_180000123.jpg", "2026:07:04 18:00:00", make=b"Google", model=b"Pixel 9")
    assert profiles.detect(str(android)).id == "android"


def test_a_copied_phone_folder_is_recognised_from_its_photos(tmp_path, iphone):
    """No DCIM above it (a folder copied off the phone): the photos' own Make decides."""
    folder = iphone / "DCIM" / "100APPLE"
    assert profiles.detect(str(folder)).id == "generic"
    assert ingest.profile_for(str(folder)).id == "apple"


def test_live_photo_videos_and_aae_files_travel_with_their_photo(env, iphone):
    conn, lib, cfg = env
    imp = ingest.create_import(conn, str(iphone), cfg)
    items = _items(conn, imp)
    parent = conn.execute("SELECT id FROM import_items WHERE import_id = ? AND source_rel = ?",
                          (imp, "DCIM/100APPLE/IMG_0001.JPG")).fetchone()[0]
    assert items["DCIM/100APPLE/IMG_0001.MOV"][2:] == (parent, "companion")
    assert items["DCIM/100APPLE/IMG_0001.AAE"][2:] == (parent, "sidecar")
    assert items["DCIM/101APPLE/IMG_0100.MOV"][2] is None              # a video on its own stays one
    s = ingest.run(conn, imp, cfg)
    assert s["safe_to_format"]
    items = _items(conn, imp)
    folder = os.path.dirname(items["DCIM/100APPLE/IMG_0001.JPG"][1])
    assert os.path.dirname(items["DCIM/100APPLE/IMG_0001.MOV"][1]) == folder
    assert os.path.dirname(items["DCIM/100APPLE/IMG_0001.AAE"][1]) == folder
    assert sorted(os.listdir(folder)) == ["IMG_0001.AAE", "IMG_0001.JPG", "IMG_0001.MOV", "IMG_0002.JPG"]


def test_a_live_photo_is_never_split_by_a_name_clash(env, iphone):
    """A different IMG_0001.MOV already in the folder: the photo AND its video
    go to the sibling folder together (names are never changed)."""
    conn, lib, cfg = env
    target = lib / "2026" / "7-4-2026"
    movie(target / "IMG_0001.MOV", 99)                                 # someone else's video
    imp = ingest.create_import(conn, str(iphone), cfg)
    ingest.run(conn, imp, cfg)
    items = _items(conn, imp)
    jpg_dir = os.path.dirname(items["DCIM/100APPLE/IMG_0001.JPG"][1])
    mov_dir = os.path.dirname(items["DCIM/100APPLE/IMG_0001.MOV"][1])
    assert jpg_dir == mov_dir and jpg_dir.endswith("7-4-2026 (2)")
    assert (target / "IMG_0001.MOV").read_bytes()[-1] == 99            # theirs untouched
    assert os.path.dirname(items["DCIM/100APPLE/IMG_0002.JPG"][1]).endswith("7-4-2026")


def test_a_different_companion_beside_an_existing_photo_is_kept_on_the_card(env, iphone):
    """The photo is already in the library but a different video has its
    Live Photo's name there: the card's video isn't counted as safe."""
    conn, lib, cfg = env
    first = ingest.create_import(conn, str(iphone), cfg)
    ingest.run(conn, first, cfg)
    folder = os.path.dirname(_items(conn, first)["DCIM/100APPLE/IMG_0001.JPG"][1])
    movie(Path(folder) / "IMG_0001.MOV", 77)                            # a different video there now
    movie(iphone / "DCIM/100APPLE/IMG_0001.MOV", 42)                    # and the card's changed
    again = ingest.create_import(conn, str(iphone), cfg)
    s = ingest.run(conn, again, cfg)
    state = _items(conn, again)["DCIM/100APPLE/IMG_0001.MOV"][0]
    assert state == "failed" and not s["safe_to_format"]
    assert "DCIM/100APPLE/IMG_0001.MOV" not in ingest.clearable(conn, again)


def test_motion_photos_are_detected(tmp_path):
    from lunelis.importers.metadata import motion_video_bytes
    video = b"\0\0\0\x18ftypmp42" + b"v" * 5000
    xmp = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
           b'<rdf:Description xmlns:GCamera="http://ns.google.com/photos/1.0/camera/" GCamera:MotionPhoto="1"'
           b' GCamera:MicroVideoOffset="%d"/></rdf:RDF></x:xmpmeta>' % len(video))
    p = tmp_path / "PXL_1.MP.jpg"
    photo(p, "2026:07:04 18:00:00", make=b"Google", model=b"Pixel 9")
    data = p.read_bytes()
    app1 = b"http://ns.adobe.com/xap/1.0/\0" + xmp
    seg = b"\xff\xe1" + (len(app1) + 2).to_bytes(2, "big") + app1
    p.write_bytes(data[:2] + seg + data[2:] + video)                    # XMP after SOI, the video at the end
    assert motion_video_bytes(str(p)) == len(video)
    plain = tmp_path / "plain.jpg"
    photo(plain, "2026:07:04 18:00:00")
    assert motion_video_bytes(str(plain)) is None


def test_the_same_photos_under_new_names_are_not_copied_again(env, iphone, tmp_path, monkeypatch):
    conn, lib, cfg = env
    first = ingest.create_import(conn, str(iphone), cfg)
    ingest.run(conn, first, cfg)
    from lunelis.importers.scan import add_root, scan_root
    scan_root(conn, add_root(conn, lib))                               # the library knows them now
    renamed = tmp_path / "exported"
    for src, new in (("DCIM/100APPLE/IMG_0001.JPG", "Vacation-1.jpg"), ("DCIM/100APPLE/IMG_0002.JPG", "Vacation-2.jpg")):
        renamed.mkdir(exist_ok=True)
        (renamed / new).write_bytes((iphone / src).read_bytes())
    copies = []
    real = ingest._copy_hashed
    monkeypatch.setattr(ingest, "_copy_hashed", lambda *a, **k: copies.append(a[0]) or real(*a, **k))
    again = ingest.create_import(conn, str(renamed), cfg)
    s = ingest.run(conn, again, cfg)
    assert s.get("already_in_library") == 2 and not copies             # recognised before any copy
    assert not any("Vacation" in f for _, _, files in os.walk(lib) for f in files)


def test_a_usb_stick_gets_an_import_offer(monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from lunelis.ui import tray
    drives = {"E:\\": True}
    monkeypatch.setattr(tray, "removable_drives", lambda: dict(drives))
    w = tray.CardWatcher(interval_ms=60_000)
    cards, sticks = [], []
    w.inserted.connect(cards.append)
    w.stick_inserted.connect(sticks.append)
    drives["F:\\"] = False                                             # a stick: no DCIM
    drives["G:\\"] = True                                              # a card reader
    w.poll()
    assert cards == ["G:\\"] and sticks == ["F:\\"]
    w.poll()
    assert cards == ["G:\\"] and sticks == ["F:\\"]                    # once each
