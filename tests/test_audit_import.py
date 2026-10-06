"""0.37.1 (audit LRA-001/002): Clear the card only ever deletes a card file
whose exact bytes are in the library."""
import hashlib
import os

from lunelis.catalog.schema import open_catalog
from lunelis.dupes.hashing import SLICE
from lunelis.importers.scan import add_root, scan_root
from lunelis.importing import ingest
from lunelis.importing.templates import DEFAULT_TEMPLATE


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _setup(tmp_path):
    lib = tmp_path / "Library"
    lib.mkdir()
    cfg = ingest.Settings_(destination=str(lib), template=DEFAULT_TEMPLATE, staging_local=str(tmp_path / "staging"),
                           staging_network=None, reserve_bytes=0)
    return lib, cfg


def test_a_file_matching_only_the_sampled_hash_is_imported_not_cleared(tmp_path):
    lib, cfg = _setup(tmp_path)
    size = 600_000
    base = bytearray((i * 7) % 251 for i in range(size))
    (lib / "2026").mkdir()
    (lib / "2026" / "DSC00001.JPG").write_bytes(bytes(base))
    card = tmp_path / "card" / "DCIM" / "100MSDCF"
    card.mkdir(parents=True)
    other = bytearray(base)
    other[SLICE + 5000:SLICE + 6000] = b"\xAB" * 1000               # differs only between the sampled slices
    (card / "DSC00001.JPG").write_bytes(bytes(other))
    want = _sha(card / "DSC00001.JPG")
    conn = open_catalog(tmp_path / "cat.db")
    scan_root(conn, add_root(conn, lib))
    imp = ingest.create_import(conn, str(tmp_path / "card"), cfg)
    s = ingest.run(conn, imp, cfg)
    assert s.get("already_in_library", 0) == 0 and s.get("placed") == 1
    assert any(_sha(p) == want for p in lib.rglob("*") if p.is_file())
    ingest.clear_card(conn, imp)
    assert any(_sha(p) == want for p in lib.rglob("*") if p.is_file())
    conn.close()


def test_an_identical_file_still_counts_as_already_in_the_library(tmp_path):
    lib, cfg = _setup(tmp_path)
    data = os.urandom(300_000)
    (lib / "a.jpg").write_bytes(data)
    card = tmp_path / "card" / "DCIM" / "100MSDCF"
    card.mkdir(parents=True)
    (card / "a.jpg").write_bytes(data)
    conn = open_catalog(tmp_path / "cat.db")
    scan_root(conn, add_root(conn, lib))
    imp = ingest.create_import(conn, str(tmp_path / "card"), cfg)
    s = ingest.run(conn, imp, cfg)
    assert s.get("already_in_library") == 1 and s["safe_to_format"]
    conn.close()


def test_a_sidecar_kept_out_by_a_different_file_stays_on_the_card(tmp_path):
    lib, cfg = _setup(tmp_path)
    (lib / "Undated").mkdir()
    (lib / "Undated" / "C0001M01.XML").write_bytes(b"<old different xml/>")
    clip = tmp_path / "card" / "PRIVATE" / "M4ROOT" / "CLIP"
    clip.mkdir(parents=True)
    (clip / "C0001.MP4").write_bytes(b"\0\0\0\x18ftypXAVC" + os.urandom(4000))
    (clip / "C0001M01.XML").write_bytes(b"<NonRealTimeMeta>the card's own</NonRealTimeMeta>")
    conn = open_catalog(tmp_path / "cat.db")
    scan_root(conn, add_root(conn, lib))
    imp = ingest.create_import(conn, str(tmp_path / "card"), cfg)
    s = ingest.run(conn, imp, cfg)
    assert not s["safe_to_format"] and s.get("failed") == 1
    assert ingest.clearable(conn, imp) == []
    ingest.clear_card(conn, imp)
    assert (clip / "C0001M01.XML").exists() and (clip / "C0001.MP4").exists()
    assert (lib / "Undated" / "C0001M01.XML").read_bytes() == b"<old different xml/>"
    conn.close()
