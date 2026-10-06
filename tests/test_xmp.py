"""Step 6: XMP sidecars - finding, reading, surgical writes, and catalog sync."""
import os
from pathlib import Path

import pytest
from PIL import Image

from lunelis.catalog.ratings import pending_count, set_ratings
from lunelis.catalog.schema import open_catalog
from lunelis.importers.scan import add_root, scan_root
from lunelis.settings import Settings
from lunelis.xmp import sync
from lunelis.xmp.sidecar import (
    SidecarError, XmpFields, apply_fields, choose_sidecar, parse_fields, read_sidecar, write_sidecar,
)

# Shaped like the three writers found in the real library.
DARKTABLE = """<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="XMP Core 4.4.0-Exiv2">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:xmp="http://ns.adobe.com/xap/1.0/"
    xmlns:darktable="http://darktable.sf.net/"
   xmp:Rating="1"
   darktable:xmp_version="5"
   darktable:history_end="2">
   <darktable:colorlabels>
    <rdf:Seq>
     <rdf:li>1</rdf:li>
     <rdf:li>3</rdf:li>
    </rdf:Seq>
   </darktable:colorlabels>
   <darktable:history>
    <rdf:Seq>
     <rdf:li darktable:operation="exposure" darktable:enabled="1"/>
    </rdf:Seq>
   </darktable:history>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
"""

CULLING = """<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="XMP Core 6.0.0">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:xmp="http://ns.adobe.com/xap/1.0/"
   xmp:Rating="5"
   xmp:Label="Green"/>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>
"""

ELEMENT_FORM = """<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about="" xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
    crs:Exposure2012="+0.35"/>
  <rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/">
   <xmp:Rating>3</xmp:Rating>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
"""


# --- choosing ---------------------------------------------------------------

def test_choose_sidecar_conventions():
    names = {"a.arw.xmp", "b.xmp", "c.xmp"}
    raws = {"b"}                                   # b.ARW + b.JPG pair; c.JPG alone
    assert choose_sidecar("a.ARW", names, raws) == "a.ARW.xmp"      # darktable style wins
    assert choose_sidecar("b.ARW", names, raws) == "b.xmp"          # Adobe: belongs to the RAW
    assert choose_sidecar("b.JPG", names, raws) is None             # ...not to its JPG
    assert choose_sidecar("c.JPG", names, raws) == "c.xmp"          # no RAW: the JPG's
    assert choose_sidecar("d.JPG", names, raws) is None


# --- reading ----------------------------------------------------------------

def test_parse_the_three_real_shapes():
    assert parse_fields(DARKTABLE) == XmpFields(stars=1, label="Yellow")   # first darktable label
    assert parse_fields(CULLING) == XmpFields(stars=5, label="Green")
    assert parse_fields(ELEMENT_FORM) == XmpFields(stars=3)                # split Descriptions


def test_reject_is_minus_one():
    assert parse_fields(CULLING.replace('Rating="5"', 'Rating="-1"')).rejected


# --- writing ----------------------------------------------------------------

@pytest.mark.parametrize("text", [DARKTABLE, CULLING, ELEMENT_FORM])
def test_writing_current_values_changes_nothing(text):
    assert apply_fields(text, parse_fields(text)) == text


def test_darktable_edit_touches_only_rating():
    out = apply_fields(DARKTABLE, XmpFields(stars=4, label="Yellow"))   # label unchanged
    assert out == DARKTABLE.replace('xmp:Rating="1"', 'xmp:Rating="4"')
    assert "<rdf:li>3</rdf:li>" in out                                  # 2nd label kept


def test_darktable_label_change_updates_its_list():
    out = apply_fields(DARKTABLE, XmpFields(stars=1, label="Green"))
    assert parse_fields(out).label == "Green"
    assert 'xmp:Label="Green"' in out and "<rdf:li>2</rdf:li>" in out
    assert 'darktable:operation="exposure"' in out                      # edit history intact


def test_element_form_updated_in_place():
    out = apply_fields(ELEMENT_FORM, XmpFields(stars=5))
    assert "<xmp:Rating>5</xmp:Rating>" in out and 'crs:Exposure2012="+0.35"' in out
    assert out.count("xmp:Rating") == 2                                  # open + close tag, no dup attr


def test_label_removed_cleanly():
    out = apply_fields(CULLING, XmpFields(stars=5, label=None))
    assert "xmp:Label" not in out and parse_fields(out) == XmpFields(stars=5)


def test_write_creates_minimal_sidecar(tmp_path):
    p = tmp_path / "DSC0001.ARW.xmp"
    write_sidecar(str(p), XmpFields(stars=4, label="Blue"))
    assert read_sidecar(str(p)) == XmpFields(stars=4, label="Blue")
    assert not list(tmp_path.glob(".lunelis-*"))                         # no temp left behind


def test_unparseable_sidecar_is_left_alone(tmp_path):
    p = tmp_path / "bad.xmp"
    p.write_text("<x:xmpmeta><rdf:Description xmp:Rating='1'", encoding="utf-8")
    before = p.read_bytes()
    with pytest.raises(SidecarError):
        write_sidecar(str(p), XmpFields(stars=5))
    assert p.read_bytes() == before


# --- catalog sync -----------------------------------------------------------

def _jpeg(p: Path):
    Image.new("RGB", (32, 24), (90, 90, 90)).save(p, "JPEG")


@pytest.fixture
def lib(tmp_path):
    root = tmp_path / "Photos"
    root.mkdir()
    for n in ("dt.JPG", "cull.ARW", "cull.JPG", "plain.JPG", "untouched.JPG"):
        _jpeg(root / n)
    (root / "dt.JPG.xmp").write_text(DARKTABLE, encoding="utf-8")
    (root / "cull.xmp").write_text(CULLING, encoding="utf-8")              # Adobe style, RAW+JPG pair
    (root / "untouched.JPG.xmp").write_text(DARKTABLE.replace('xmp:Rating="1"', 'xmp:Rating="0"')
                                            .replace("<rdf:li>1</rdf:li>\n     <rdf:li>3</rdf:li>", ""),
                                            encoding="utf-8")
    conn = open_catalog(tmp_path / "lunelis.db")
    Settings(conn).set("sidecar_mode", "beside")     # these tests are about sidecars next to photos
    Settings(conn).set("sidecar_store_dir", str(tmp_path / "store"))
    root_id = add_root(conn, root)
    scan_root(conn, root_id)
    yield conn, root, root_id
    conn.close()


def _rating(conn, name):
    return conn.execute(
        "SELECT r.stars, r.flag, r.color_label, r.xmp_pending FROM ratings r"
        " JOIN files f ON f.id = r.file_id WHERE f.filename = ?", (name,)).fetchone()


def _ids(conn, *names):
    return [conn.execute("SELECT id FROM files WHERE filename = ?", (n,)).fetchone()[0] for n in names]


def test_scan_records_sidecars(lib):
    conn, _, _ = lib
    side = dict(conn.execute("SELECT filename, sidecar FROM files"))
    assert side == {"dt.JPG": "dt.JPG.xmp", "cull.ARW": "cull.xmp", "cull.JPG": None,
                    "plain.JPG": None, "untouched.JPG": "untouched.JPG.xmp"}


def test_import_brings_in_existing_ratings(lib):
    conn, _, _ = lib
    r = sync.import_sidecars(conn)
    assert r.failed == 0
    assert tuple(_rating(conn, "dt.JPG")) == (1, None, "Yellow", 0)
    assert tuple(_rating(conn, "cull.ARW")) == (5, None, "Green", 0)
    assert _rating(conn, "untouched.JPG") is None        # rating 0, no label: no row
    assert sync.import_pending_count(conn) == 0           # nothing re-read next time


def test_export_writes_existing_sidecar_or_darktable_style(lib):
    conn, root, root_id = lib
    sync.import_sidecars(conn)
    set_ratings(conn, _ids(conn, "cull.ARW", "plain.JPG"), stars=3)
    r = sync.export_pending(conn)
    assert (r.done, r.failed) == (2, 0) and pending_count(conn) == 0
    assert read_sidecar(str(root / "cull.xmp")) == XmpFields(stars=3, label="Green")
    assert read_sidecar(str(root / "plain.JPG.xmp")) == XmpFields(stars=3)       # new, darktable style
    assert conn.execute("SELECT sidecar FROM files WHERE filename='plain.JPG'").fetchone()[0] \
        == "plain.JPG.xmp"
    # Our own write isn't re-imported as an outside change.
    scan_root(conn, root_id)
    assert sync.import_pending_count(conn) == 0


def test_pick_only_creates_no_sidecar(lib):
    conn, root, _ = lib
    set_ratings(conn, _ids(conn, "plain.JPG"), flag="pick")
    sync.export_pending(conn)
    assert not (root / "plain.JPG.xmp").exists() and pending_count(conn) == 0


def test_reject_round_trips(lib):
    conn, root, root_id = lib
    set_ratings(conn, _ids(conn, "cull.ARW"), flag="reject")
    sync.export_pending(conn)
    assert read_sidecar(str(root / "cull.xmp")).rejected


def test_outside_edit_is_reimported(lib):
    conn, root, root_id = lib
    sync.import_sidecars(conn)
    p = root / "cull.xmp"
    p.write_text(CULLING.replace('Rating="5"', 'Rating="2"'), encoding="utf-8")
    st = os.stat(p)
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 2 * 10**9))
    scan_root(conn, root_id)
    sync.import_sidecars(conn)
    assert _rating(conn, "cull.ARW")[0] == 2


def test_unsaved_local_change_beats_sidecar(lib):
    conn, root, root_id = lib
    sync.import_sidecars(conn)
    set_ratings(conn, _ids(conn, "cull.ARW"), stars=4)             # not exported yet
    p = root / "cull.xmp"
    p.write_text(CULLING.replace('Rating="5"', 'Rating="1"'), encoding="utf-8")
    st = os.stat(p)
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 2 * 10**9))
    scan_root(conn, root_id)
    sync.import_sidecars(conn)
    assert _rating(conn, "cull.ARW")[0] == 4                        # local wins...
    sync.export_pending(conn)
    assert read_sidecar(str(p)).stars == 4                           # ...and is written out


def test_change_during_export_stays_pending(lib, monkeypatch):
    conn, root, _ = lib
    fid = _ids(conn, "plain.JPG")
    set_ratings(conn, fid, stars=2)
    real_write = sync.write_sidecar
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]   # (conn is thread-bound)

    def write_then_user_changes_rating(path, fields):
        real_write(path, fields)
        # The user rates again while this write is in flight (other connection).
        other = open_catalog(db_path)
        set_ratings(other, fid, stars=5)
        other.close()

    monkeypatch.setattr(sync, "write_sidecar", write_then_user_changes_rating)
    sync.export_pending(conn, workers=1)
    assert _rating(conn, "plain.JPG")[3] == 1                       # still pending: 5 not written
    monkeypatch.setattr(sync, "write_sidecar", real_write)
    sync.export_pending(conn)
    assert read_sidecar(str(root / "plain.JPG.xmp")).stars == 5


def test_unwritable_folder_keeps_change_pending(lib, monkeypatch):
    conn, _, _ = lib
    set_ratings(conn, _ids(conn, "plain.JPG"), stars=2)

    def offline(path, fields):
        raise OSError("The network path was not found")

    monkeypatch.setattr(sync, "write_sidecar", offline)
    r = sync.export_pending(conn)
    assert r.failed == 1 and pending_count(conn) == 1
    assert "network path" in conn.execute("SELECT xmp_error FROM ratings").fetchone()[0]


def test_set_ratings_validates(lib):
    conn, _, _ = lib
    with pytest.raises(ValueError):
        set_ratings(conn, [1], stars=6)
    with pytest.raises(ValueError):
        set_ratings(conn, [1], label="Orange")


# --- central store ------------------------------------------------------------

def test_central_mode_keeps_photo_folders_clean(lib, tmp_path):
    conn, root, root_id = lib
    Settings(conn).set("sidecar_mode", "central")
    Settings(conn).set("update_existing_sidecars", False)
    before = sorted(p.name for p in root.iterdir())
    set_ratings(conn, _ids(conn, "plain.JPG", "cull.ARW"), stars=4, label="Red")
    r = sync.export_pending(conn)
    assert r.failed == 0 and pending_count(conn) == 0
    assert sorted(p.name for p in root.iterdir()) == before          # nothing new, nothing changed
    assert read_sidecar(str(root / "cull.xmp")) == XmpFields(stars=5, label="Green")   # untouched
    central = sync.central_path(tmp_path / "store", root_id, str(root), "plain.JPG", "plain.JPG")
    assert read_sidecar(central) == XmpFields(stars=4, label="Red")
    assert os.path.basename(os.path.dirname(central)) == f"{root_id}-Photos"


def test_central_plus_update_existing(lib, tmp_path):
    conn, root, root_id = lib
    Settings(conn).set("sidecar_mode", "central")                   # update_existing defaults on
    set_ratings(conn, _ids(conn, "cull.ARW", "plain.JPG"), stars=2)
    sync.export_pending(conn)
    assert read_sidecar(str(root / "cull.xmp")).stars == 2          # existing one kept in step
    assert not (root / "plain.JPG.xmp").exists()                    # but none created
    for name in ("cull.ARW", "plain.JPG"):
        rel = name
        assert read_sidecar(sync.central_path(tmp_path / "store", root_id, str(root), rel, name)).stars == 2


def test_catalog_only_mode_writes_nothing_new(lib, tmp_path):
    conn, root, _ = lib
    Settings(conn).set("sidecar_mode", "catalog")
    Settings(conn).set("update_existing_sidecars", False)
    set_ratings(conn, _ids(conn, "plain.JPG", "cull.ARW"), stars=1)
    sync.export_pending(conn)
    assert read_sidecar(str(root / "cull.xmp")).stars == 5
    assert not (tmp_path / "store").exists() and pending_count(conn) == 0


def test_root_key_handles_drive_roots():
    assert sync.root_key(3, "D:\\") == "3-D"
    assert sync.root_key(2, r"\\nas\share\Photos") == "2-Photos"


def test_an_unreadable_sidecar_beside_the_photo_doesnt_block_the_central_store(lib, tmp_path):
    # audit LRA-007
    conn, root, root_id = lib
    Settings(conn).set("sidecar_mode", "central")                   # update_existing defaults on
    (root / "cull.xmp").write_bytes(b"<x:xmpmeta \xff\xfe not xml")
    set_ratings(conn, _ids(conn, "cull.ARW"), stars=3)
    sync.export_pending(conn)
    assert read_sidecar(sync.central_path(tmp_path / "store", root_id, str(root), "cull.ARW", "cull.ARW")).stars == 3
    assert pending_count(conn) == 0
    err = conn.execute("SELECT xmp_error FROM ratings WHERE file_id = ?", (_ids(conn, "cull.ARW")[0],)).fetchone()[0]
    assert err and "beside the photo" in err
    from lunelis.ui.photoinfo import problems
    assert any("beside the photo" in p for p in problems(conn, _ids(conn, "cull.ARW")[0]))
