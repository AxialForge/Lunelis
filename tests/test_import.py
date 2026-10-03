"""Step 11: storage templates and card import (stage -> verify -> place -> verify)."""
import os
import shutil
from datetime import datetime

import piexif
import pytest
from PIL import Image

from lunelis.catalog.schema import open_catalog
from lunelis.importers.metadata import extract_pending
from lunelis.importers.scan import add_root, scan_root
from lunelis.importing import ingest
from lunelis.importing.templates import (
    DEFAULT_TEMPLATE, PRESETS, Context, TemplateError, render, sibling, validate,
)

# --- templates --------------------------------------------------------------------

T = datetime(2026, 6, 19, 14, 3)


def test_default_template_matches_the_existing_library_layout():
    assert render(DEFAULT_TEMPLATE, Context(T)) == "2026\\6-19-2026"
    assert render(DEFAULT_TEMPLATE, Context(T, import_name="Cleveland Air Show")) == \
        "2026\\6-19-2026 Cleveland Air Show"


def test_undated_files_and_presets():
    assert render(DEFAULT_TEMPLATE, Context(None)) == "Undated"
    assert render(DEFAULT_TEMPLATE, Context(None, import_name="Card dump")) == "Undated\\Card dump"
    assert render(PRESETS["Year \\ Month \\ Day"], Context(T)) == "2026\\06\\19"
    assert render(PRESETS["Camera \\ Year"], Context(T, camera="ILCE-7RM5")) == "ILCE-7RM5\\2026"


def test_names_are_made_safe_for_windows():
    assert render(r"{YYYY}[ {import_name}]", Context(T, import_name='Trip: "NYC"/day 2?')) == \
        "2026 Trip_ _NYC__day 2_"
    assert render(r"{camera}", Context(T, camera="CON")) == "CON_"


def test_invalid_templates_are_refused():
    for bad in (r"{YYYY}\{nope}", r"{YYYY}\[{M}", r"C:\{YYYY}", r"\{YYYY}"):
        with pytest.raises(TemplateError):
            validate(bad)


def test_sibling_folder_for_name_clashes():
    assert sibling("2026\\6-19-2026", 2) == "2026\\6-19-2026 (2)"
    assert sibling("Undated", 3) == "Undated (3)"


# --- a fake camera card -----------------------------------------------------------------

def jpeg(path, when, seed=1, size=(48, 32)):
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.effect_noise(size, 30 + seed).convert("RGB")
    exif = piexif.dump({"0th": {piexif.ImageIFD.Model: b"ILCE-7RM5"},
                        "Exif": {piexif.ExifIFD.DateTimeOriginal: when.encode()}})
    img.save(path, "JPEG", exif=exif, quality=90)


@pytest.fixture
def card(tmp_path):
    c = tmp_path / "card"
    jpeg(c / "DCIM/100MSDCF/DSC00001.JPG", "2026:06:19 10:00:00", 1)
    jpeg(c / "DCIM/100MSDCF/DSC00002.JPG", "2026:06:19 10:00:01", 2)
    jpeg(c / "DCIM/100MSDCF/DSC00003.JPG", "2026:06:20 09:00:00", 3)       # next day
    (c / "PRIVATE/M4ROOT/CLIP").mkdir(parents=True)
    (c / "PRIVATE/M4ROOT/CLIP/C0001.MP4").write_bytes(b"\0\0\0\x18ftypXAVC" + os.urandom(4000))
    (c / "MISC").mkdir()
    jpeg(c / "MISC/thumbnail_cache.JPG", "2026:06:19 10:00:00", 9)         # not camera media
    return c


@pytest.fixture
def env(tmp_path, card):
    conn = open_catalog(tmp_path / "cat.db")
    lib = tmp_path / "Library"
    lib.mkdir()
    cfg = ingest.Settings_(destination=str(lib), template=DEFAULT_TEMPLATE,
                           staging_local=str(tmp_path / "staging"),
                           staging_network=str(tmp_path / "net_staging"), reserve_bytes=0)
    yield conn, card, lib, cfg, tmp_path
    conn.close()


def _items(conn, imp):
    return {rel: (state, dest) for rel, state, dest in conn.execute(
        "SELECT source_rel, state, dest_path FROM import_items WHERE import_id = ?", (imp,))}


def test_discover_reads_only_camera_media_folders(card):
    rels = [r for r, _, _ in ingest.discover(str(card))]
    assert rels == ["DCIM/100MSDCF/DSC00001.JPG", "DCIM/100MSDCF/DSC00002.JPG",
                    "DCIM/100MSDCF/DSC00003.JPG", "PRIVATE/M4ROOT/CLIP/C0001.MP4"]


def test_full_import_keeps_names_and_dates(env):
    conn, card, lib, cfg, tmp = env
    imp = ingest.create_import(conn, str(card), cfg, name="Air Show")
    s = ingest.run(conn, imp, cfg)
    assert s["safe_to_format"] and s["placed"] == 4
    assert (lib / "2026/6-19-2026 Air Show/DSC00001.JPG").read_bytes() == \
        (card / "DCIM/100MSDCF/DSC00001.JPG").read_bytes()
    # A named import is an event, filed by its start date: the next day's
    # shots and the undated clip stay with it instead of splitting off.
    assert (lib / "2026/6-19-2026 Air Show/DSC00003.JPG").exists()
    assert (lib / "2026/6-19-2026 Air Show/C0001.MP4").exists()
    assert conn.execute("SELECT event_start FROM imports WHERE id = ?", (imp,)).fetchone()[0][:10] == "2026-06-19"
    src = card / "DCIM/100MSDCF/DSC00002.JPG"
    dst = lib / "2026/6-19-2026 Air Show/DSC00002.JPG"
    assert int(os.stat(dst).st_mtime) == int(os.stat(src).st_mtime)            # date kept
    assert not os.path.exists(tmp / "staging" / f"import-{imp}")                # staging cleaned up
    assert conn.execute("SELECT state FROM imports WHERE id = ?", (imp,)).fetchone()[0] == "done"
    assert list((card / "DCIM/100MSDCF").iterdir())                             # the card is untouched


def test_card_removable_after_staging_before_placing(env):
    conn, card, lib, cfg, _ = env
    imp = ingest.create_import(conn, str(card), cfg)
    s = ingest.stage(conn, imp, cfg)
    assert s["card_removable"] and not s["safe_to_format"]
    shutil.rmtree(card)                                                          # card pulled out
    s = ingest.place(conn, imp, cfg)                                             # placing doesn't need it
    assert s["safe_to_format"]


def test_spills_to_network_staging_when_local_reserve_reached(env):
    conn, card, lib, cfg, tmp = env
    cfg.reserve_bytes = 10 ** 18                                                 # "local disk is full"
    imp = ingest.create_import(conn, str(card), cfg)
    ingest.stage(conn, imp, cfg)
    staged = [p for (p,) in conn.execute("SELECT staged_path FROM import_items WHERE import_id = ?", (imp,))]
    assert staged and all(str(tmp / "net_staging") in p for p in staged)

    cfg.staging_network = None
    imp2 = ingest.create_import(conn, str(card), cfg)
    with pytest.raises(ingest.NoStagingSpace):
        ingest.stage(conn, imp2, cfg)


def test_name_clash_goes_to_a_sibling_folder_never_renamed(env):
    conn, card, lib, cfg, _ = env
    taken = lib / "2026/6-19-2026/DSC00001.JPG"
    jpeg(taken, "2025:01:01 00:00:00", seed=42)                                   # a different photo
    identical = lib / "2026/6-19-2026/DSC00002.JPG"
    identical.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(card / "DCIM/100MSDCF/DSC00002.JPG", identical)                  # the same photo
    imp = ingest.create_import(conn, str(card), cfg)
    ingest.run(conn, imp, cfg)
    items = _items(conn, imp)
    assert items["DCIM/100MSDCF/DSC00001.JPG"][1].endswith(os.path.join("6-19-2026 (2)", "DSC00001.JPG"))
    assert items["DCIM/100MSDCF/DSC00002.JPG"][0] == "already_in_library"
    assert taken.exists()                                                        # nothing overwritten


def test_photos_already_in_the_library_are_skipped(env):
    conn, card, lib, cfg, tmp = env
    other = tmp / "Elsewhere"                                                     # imported years ago
    other.mkdir()
    shutil.copy2(card / "DCIM/100MSDCF/DSC00003.JPG", other / "old-name.jpg")
    rid = add_root(conn, other)
    scan_root(conn, rid)
    extract_pending(conn)
    imp = ingest.create_import(conn, str(card), cfg)
    s = ingest.run(conn, imp, cfg)
    assert _items(conn, imp)["DCIM/100MSDCF/DSC00003.JPG"][0] == "already_in_library"
    assert s["safe_to_format"] and not (lib / "2026/6-20-2026/DSC00003.JPG").exists()


def test_resumes_after_an_interruption(env):
    conn, card, lib, cfg, _ = env
    imp = ingest.create_import(conn, str(card), cfg)
    calls = {"n": 0}

    def stop_after_first():
        calls["n"] += 1
        return calls["n"] > 3
    ingest.stage(conn, imp, cfg, should_stop=stop_after_first)
    states = [s for s, _ in _items(conn, imp).values()]
    assert "pending" in states and "staged" in states
    assert ingest.run(conn, imp, cfg)["safe_to_format"]


def test_card_removed_mid_staging_waits(env):
    conn, card, lib, cfg, _ = env
    imp = ingest.create_import(conn, str(card), cfg)
    shutil.rmtree(card)
    with pytest.raises(ingest.WaitingForSource):
        ingest.stage(conn, imp, cfg)
    assert conn.execute("SELECT state FROM imports WHERE id = ?", (imp,)).fetchone()[0] == "waiting"


def test_failed_library_verification_keeps_the_staged_copy(env, monkeypatch):
    conn, card, lib, cfg, _ = env
    imp = ingest.create_import(conn, str(card), cfg)
    ingest.stage(conn, imp, cfg)
    real = ingest._sha256

    def corrupt_library_reads(path):
        return "0" * 64 if str(lib) in path else real(path)
    monkeypatch.setattr(ingest, "_sha256", corrupt_library_reads)
    s = ingest.place(conn, imp, cfg)
    assert s.get("failed", 0) == 4 and not s["safe_to_format"]
    staged = [p for (p,) in conn.execute("SELECT staged_path FROM import_items WHERE import_id = ?", (imp,))]
    assert all(os.path.exists(p) for p in staged)                                 # nothing lost


def test_retry_failed_after_a_verification_problem(env, monkeypatch):
    conn, card, lib, cfg, _ = env
    imp = ingest.create_import(conn, str(card), cfg)
    ingest.stage(conn, imp, cfg)
    real = ingest._sha256
    monkeypatch.setattr(ingest, "_sha256", lambda p: "0" * 64 if str(lib) in p else real(p))
    ingest.place(conn, imp, cfg)
    monkeypatch.setattr(ingest, "_sha256", real)                                  # the glitch passes
    assert ingest.retry_failed(conn, imp) == 4
    assert ingest.run(conn, imp, cfg)["safe_to_format"]
    assert ingest.unfinished(conn) == []


def test_an_import_needs_a_destination_first(tmp_path):
    """There's no built-in destination: an import can't start until one is chosen."""
    from lunelis.settings import Settings
    conn = open_catalog(tmp_path / "catalog.db")
    assert Settings(conn).get("import_destination") is None
    assert Settings(conn).get("import_staging_network") is None
    card = tmp_path / "card"
    card.mkdir()
    cfg = ingest.load_settings(conn, tmp_path)
    with pytest.raises(ValueError, match="library folder"):
        ingest.create_import(conn, str(card), cfg)
    assert conn.execute("SELECT COUNT(*) FROM imports").fetchone()[0] == 0
    conn.close()
