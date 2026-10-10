"""0.54: the services batch from the 10 October backlog - background jobs,
duplicates, backups, the family gallery, faces, export and thumbnails."""
import logging
import os
import threading
import urllib.error
import urllib.request

import numpy as np
import pytest
from PIL import Image

from lunelis import paths
from lunelis.catalog.schema import open_catalog
from lunelis.dupes import detect
from lunelis.dupes.hashing import Throttle
from lunelis.importers.scan import add_root, scan_root


def _library(tmp_path, files: dict[str, bytes]):
    root = tmp_path / "Photos"
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    conn = open_catalog(tmp_path / "c.db")
    rid = add_root(conn, root)
    scan_root(conn, rid)
    return conn, rid, root


def _blob(seed: int, size: int = 300_000) -> bytes:
    block = bytes((seed * 31 + i * 7) % 251 for i in range(4096))
    return (block * (size // 4096 + 1))[:size]


# --- folder-by-folder jobs read one folder, not the whole source -----------------------------------

def test_a_folders_rows_are_found_through_the_index(tmp_path):
    conn = open_catalog(tmp_path / "c.db")
    rid = conn.execute("INSERT INTO roots (path) VALUES ('X:/none')").lastrowid
    other = conn.execute("INSERT INTO roots (path) VALUES ('Y:/none')").lastrowid
    rels = ["top.jpg", "a/x.jpg", "a/y.jpg", "a/b/deep.jpg", "a b/z.jpg", "a0/q.jpg", "a.jpg", "A/w.jpg",
            "a/b/c/deeper.jpg", "\u00e9t\u00e9/s.jpg", "\u00e9t\u00e9/sub/t.jpg"]
    for r, rels_ in ((rid, rels), (other, ["a/elsewhere.jpg"])):
        conn.executemany("INSERT INTO files (root_id, rel_path, filename, ext, size_bytes, mtime) VALUES (?,?,?,?,1,'m')",
                         [(r, rel, rel.rsplit("/", 1)[-1], "jpg") for rel in rels_])
    conn.commit()
    for folder in ("", "a", "a/b", "a b", "a0", "A", "\u00e9t\u00e9", "nothing"):
        where, args = detect.folder_sql(folder)
        got = sorted(r[0] for r in conn.execute(
            f"SELECT f.rel_path FROM files f WHERE f.root_id = ? AND {where}", (rid, *args)))
        assert got == sorted(r for r in rels if detect._dir_of(r) == folder), folder
    where, args = detect.folder_sql("a/b")
    plan = " ".join(r[3] for r in conn.execute(
        f"EXPLAIN QUERY PLAN SELECT f.id FROM files f WHERE f.root_id = ? AND {where}", (rid, *args)))
    assert "rel_path>" in plan and "rel_path<" in plan, plan        # a range of the index, not every row
    conn.close()


def test_folder_jobs_still_do_exactly_their_folder(tmp_path):
    from lunelis.jobs import engine
    conn, rid, root = _library(tmp_path, {"2019/a.jpg": _blob(1), "2019/sub/b.jpg": _blob(2),
                                          "2019 extra/c.jpg": _blob(3), "d.jpg": _blob(4)})
    kw = dict(throttle=Throttle(None), should_cancel=None, workers=2)
    assert engine._full_hash_folder(conn, rid, "2019", **kw).hashed == 1
    assert engine._full_hash_folder(conn, rid, "", **kw).hashed == 1
    hashed = {r[0] for r in conn.execute("SELECT rel_path FROM files WHERE content_hash IS NOT NULL")}
    assert hashed == {"2019/a.jpg", "d.jpg"}
    assert engine._integrity_folder(conn, rid, "2019", **kw).hashed == 1
    conn.close()


# --- one missing or unreadable file doesn't stop or hide ----------------------------------------------

def _two_copies(tmp_path):
    conn, rid, root = _library(tmp_path, {"one/a.jpg": _blob(1), "two/a-copy.jpg": _blob(1),
                                          "one/other.jpg": _blob(2, 310_000)})
    detect.process_folder(conn, rid, "one")
    gid = conn.execute("SELECT id FROM duplicate_groups WHERE method = 'sampled'").fetchone()[0]
    return conn, rid, root, gid


def test_verifying_duplicates_skips_a_file_that_has_gone(tmp_path, caplog):
    from lunelis.jobs import engine
    conn, rid, root, gid = _two_copies(tmp_path)
    (root / "two/a-copy.jpg").unlink()                       # deleted since the scan
    with caplog.at_level(logging.WARNING):
        assert detect.verify_group(conn, gid) == []          # it used to raise FileNotFoundError
        engine._verify_folder(conn, rid, "one", throttle=Throttle(None), should_cancel=None, workers=2)
    assert "not verified" in caplog.text and "a-copy.jpg" in caplog.text
    assert conn.execute("SELECT COUNT(*) FROM duplicate_groups WHERE method = 'exact'").fetchone()[0] == 0
    conn.close()


def test_the_duplicates_job_logs_a_file_it_couldnt_read(tmp_path, caplog, monkeypatch):
    from lunelis.jobs import engine
    conn, rid, root = _library(tmp_path, {"one/a.jpg": _blob(1), "two/a-copy.jpg": _blob(1)})
    real = detect.sample_hash

    def locked(path, size, throttle=None):
        if path.endswith("a-copy.jpg"):
            raise PermissionError(13, "Permission denied", path)
        return real(path, size, throttle)
    monkeypatch.setattr(detect, "sample_hash", locked)
    job = engine.create_job(conn, "duplicates", "Find duplicates", [(rid, None)])
    with caplog.at_level(logging.WARNING):
        assert engine.run_job(conn, job).state == "done"
    assert "two/a-copy.jpg" in caplog.text and "not compared" in caplog.text
    assert "had a problem" in caplog.text                    # ...and the job's own line says how many
    conn.close()


# --- thumbnails ---------------------------------------------------------------------------------------

def test_a_passing_read_problem_is_tried_again_before_preview_unavailable(tmp_path, monkeypatch):
    from lunelis.raw import thumbnails
    root = tmp_path / "Photos"
    root.mkdir()
    Image.new("RGB", (64, 48), (200, 10, 10)).save(root / "a.jpg")
    Image.new("RGB", (64, 48), (10, 200, 10)).save(root / "b.jpg")
    (root / "broken.jpg").write_bytes(b"\xff\xd8\xff\xe0 not really a picture")
    conn = open_catalog(tmp_path / "c.db")
    scan_root(conn, add_root(conn, root))
    monkeypatch.setattr(thumbnails, "_tries", {})
    real = thumbnails.render
    busy = {"a.jpg": PermissionError(13, "The process cannot access the file"), "b.jpg": MemoryError()}

    def render(path, *a, **kw):
        err = busy.get(os.path.basename(path))
        if err:
            raise err
        return real(path, *a, **kw)
    monkeypatch.setattr(thumbnails, "render", render)

    def state():
        return {n: (bool(t), e) for n, t, e in conn.execute("SELECT filename, thumbnail_path, thumb_error FROM files")}
    r = thumbnails.generate_pending(conn, tmp_path / "thumbs")
    assert r.offline == 2 and r.failed == 1
    s = state()
    assert s["a.jpg"] == (False, None) and s["b.jpg"] == (False, None)      # still pending, not written off
    assert s["broken.jpg"][1]                                               # a really bad file is marked at once
    del busy["a.jpg"]                                                       # the other program let go of it
    thumbnails.generate_pending(conn, tmp_path / "thumbs")
    assert state()["a.jpg"] == (True, None) and state()["b.jpg"] == (False, None)
    for _ in range(thumbnails.TRANSIENT_TRIES):                             # never readable: marked in the end
        thumbnails.generate_pending(conn, tmp_path / "thumbs")
    assert state()["b.jpg"][1].startswith("MemoryError")
    conn.close()


@pytest.mark.parametrize("ext", ["png", "tif"])
def test_a_16_bit_grey_scan_is_grey_not_white(tmp_path, ext):
    from lunelis.edit.render import load_source
    from lunelis.raw import thumbnails
    ramp = np.tile(np.linspace(2000, 30000, 200).astype(np.uint16), (100, 1))    # dark to mid grey, 16-bit
    p = tmp_path / f"scan.{ext}"
    Image.fromarray(ramp).save(p)
    img = thumbnails.render(str(p))
    px = np.asarray(img.convert("L")).astype(float)
    assert img.mode == "RGB" and 40 < px.mean() < 90, px.mean()               # it was 255 everywhere
    assert px[:, 0].mean() < 20 and 100 < px[:, -1].mean() < 130
    a = load_source(str(p), False, 256)
    assert 0.15 < float(a.mean()) < 0.35
    # An ordinary 8-bit grey picture is untouched.
    Image.new("L", (50, 50), 90).save(tmp_path / "plain.png")
    assert abs(np.asarray(thumbnails.render(str(tmp_path / "plain.png"))).mean() - 90) < 2


def test_two_writers_of_one_picture_dont_share_a_temp_file(tmp_path, monkeypatch):
    from lunelis.raw import thumbnails
    names, errors = set(), []
    real = Image.Image.save

    def save(self, fp, *a, **kw):
        names.add(str(fp))
        return real(self, fp, *a, **kw)
    monkeypatch.setattr(Image.Image, "save", save)
    img = Image.effect_noise((400, 300), 60).convert("RGB")
    gate = threading.Barrier(6)

    def work():
        try:
            gate.wait(5)
            for _ in range(5):
                thumbnails.write_thumbnail(tmp_path, 12345, img)
        except Exception as e:                                  # noqa: BLE001
            errors.append(e)
    threads = [threading.Thread(target=work) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert len(names) == 6                                      # one temp name per thread
    folder = tmp_path / "0012"
    assert [p.name for p in folder.iterdir()] == ["12345.jpg"]  # and none left behind
    Image.open(folder / "12345.jpg").verify()


# --- export -------------------------------------------------------------------------------------------

@pytest.fixture
def one_photo(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DEFAULT_CATALOG_PATH", tmp_path / "catalog.db")
    root = tmp_path / "Photos"
    root.mkdir()
    Image.new("RGB", (600, 400), (100, 120, 140)).save(root / "IMG_1.jpg")
    conn = open_catalog(paths.DEFAULT_CATALOG_PATH)
    scan_root(conn, add_root(conn, root))
    fid = conn.execute("SELECT id FROM files").fetchone()[0]
    yield conn, fid, tmp_path
    conn.close()


def _set_iso(conn, fid, iso):
    conn.execute("INSERT OR REPLACE INTO exif (file_id, captured_at, camera_make, iso) VALUES (?, '2024-06-18T13:43:06',"
                 " 'SONY', ?)", (fid, iso))
    conn.commit()


def test_a_photo_above_iso_65535_exports_with_its_metadata(one_photo):
    import piexif
    from lunelis.edit.export import ExportOptions, export_one
    conn, fid, tmp = one_photo
    _set_iso(conn, fid, 102400)
    out = export_one(conn, fid, ExportOptions(str(tmp / "out")))        # it raised: the field holds 65,535
    ex = piexif.load(out)["Exif"]
    assert ex[piexif.ExifIFD.ISOSpeedRatings] == 65535
    assert ex[piexif.ExifIFD.SensitivityType] == 2 and ex[piexif.ExifIFD.RecommendedExposureIndex] == 102400
    for fmt in ("tiff", "png"):
        assert os.path.getsize(export_one(conn, fid, ExportOptions(str(tmp / "out"), format=fmt))) > 0
    _set_iso(conn, fid, 800)                                            # an ordinary ISO is written as before
    ex = piexif.load(export_one(conn, fid, ExportOptions(str(tmp / "out"))))["Exif"]
    assert ex[piexif.ExifIFD.ISOSpeedRatings] == 800 and piexif.ExifIFD.SensitivityType not in ex


def test_a_failed_export_leaves_no_half_written_file(one_photo, monkeypatch):
    from lunelis.edit.export import ExportOptions, export_one
    conn, fid, tmp = one_photo
    out = tmp / "out"

    def cut_short(self, fp, *a, **kw):
        with open(fp, "wb") as f:
            f.write(b"\xff\xd8 half a picture")
        raise OSError("the disk is full")
    with monkeypatch.context() as m:
        m.setattr(Image.Image, "save", cut_short)
        with pytest.raises(OSError, match="disk is full"):
            export_one(conn, fid, ExportOptions(str(out)))
    assert os.listdir(out) == []                                        # neither IMG_1.jpg nor a temp file
    done = export_one(conn, fid, ExportOptions(str(out)))
    assert os.listdir(out) == ["IMG_1.jpg"] and done.endswith("IMG_1.jpg")
    Image.open(done).verify()
    assert export_one(conn, fid, ExportOptions(str(out))).endswith("IMG_1 (2).jpg")   # still never overwrites


def test_lens_details_are_read_when_the_data_folder_has_odd_characters(tmp_path, monkeypatch):
    import sqlite3
    from lunelis.edit import lens
    folder = tmp_path / "data #1 100% (new)"
    folder.mkdir()
    conn = open_catalog(folder / "catalog.db")
    rid = conn.execute("INSERT INTO roots (path) VALUES ('X:/none')").lastrowid
    fid = conn.execute("INSERT INTO files (root_id, rel_path, filename, ext, size_bytes, mtime)"
                       " VALUES (?, 'a.jpg', 'a.jpg', 'jpg', 1, 'm')", (rid,)).lastrowid
    conn.execute("INSERT INTO exif (file_id, camera_make, lens) VALUES (?, 'SONY', 'FE 24-105mm F4 G OSS')", (fid,))
    conn.commit()
    conn.close()
    monkeypatch.setattr(paths, "DEFAULT_CATALOG_PATH", folder / "catalog.db")
    info = lens.info_for_id(fid)
    assert info is not None and info.lens == "FE 24-105mm F4 G OSS"     # None before: the address was cut at '#'
    ro = sqlite3.connect(lens.catalog_uri(folder / "catalog.db"), uri=True)
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("DELETE FROM exif")                                  # and it is still read-only
    ro.close()
    assert not (tmp_path / "data ").exists()                            # no stray catalog at the cut-off path


# --- migration ----------------------------------------------------------------------------------------

def test_a_dismissed_damage_flag_doesnt_count_in_a_migration(tmp_path):
    from lunelis.importing.templates import DEFAULT_TEMPLATE
    from lunelis.migrate.plan import Options, plan
    root, target = tmp_path / "A", tmp_path / "T"
    root.mkdir()
    target.mkdir()
    Image.new("RGB", (64, 48), (9, 9, 9)).save(root / "IMG_0001.JPG")
    conn = open_catalog(tmp_path / "c.db")
    rid = add_root(conn, root)
    scan_root(conn, rid)
    fid = conn.execute("SELECT id FROM files").fetchone()[0]
    conn.execute("INSERT INTO damaged (file_id, problem, dismissed) VALUES (?, 'corrupt', 1)", (fid,))
    conn.commit()
    plan(conn, str(target), DEFAULT_TEMPLATE, Options([rid]))
    act, dest = conn.execute("SELECT action, dest_rel FROM migration_items WHERE file_id = ?", (fid,)).fetchone()
    assert act == "move" and "Damaged" not in dest                      # you said it's fine: it goes with the rest
    conn.execute("DELETE FROM migration_items")
    conn.execute("DELETE FROM migrations")
    conn.execute("UPDATE damaged SET dismissed = 0")
    conn.commit()
    plan(conn, str(target), DEFAULT_TEMPLATE, Options([rid]))
    dest = conn.execute("SELECT dest_rel FROM migration_items WHERE file_id = ?", (fid,)).fetchone()[0]
    assert "Damaged" in dest                                            # a flag still standing does count
    conn.close()


# --- duplicates: setting aside and emptying -----------------------------------------------------------

def test_setting_a_duplicate_aside_checks_the_kept_copy_is_still_there(tmp_path):
    from lunelis.dupes.quarantine import QuarantineRefused, quarantine
    conn, rid, root, gid = _two_copies(tmp_path)
    (exact,) = detect.verify_group(conn, gid)
    keep, go = [r[0] for r in conn.execute("SELECT id FROM files WHERE filename LIKE 'a%' ORDER BY rel_path")]
    kept_path, going = root / "one/a.jpg", root / "two/a-copy.jpg"
    data = kept_path.read_bytes()
    kept_path.unlink()                                       # deleted outside Lunelis since the comparison
    with pytest.raises(QuarantineRefused, match="no longer there"):
        quarantine(conn, exact, [go])
    assert going.exists()                                    # the only good copy stayed where it was
    assert conn.execute("SELECT quarantined_at FROM files WHERE id = ?", (go,)).fetchone()[0] is None
    kept_path.write_bytes(data[:1000])                       # or cut short / replaced: another size
    with pytest.raises(QuarantineRefused, match="has changed"):
        quarantine(conn, exact, [go])
    assert going.exists()
    kept_path.write_bytes(data)                              # there, and the right size: as before
    (dst,) = quarantine(conn, exact, [go])
    assert os.path.exists(dst) and not going.exists() and kept_path.exists()
    conn.close()


def test_emptying_quarantine_leaves_another_files_sidecar(tmp_path):
    from types import SimpleNamespace
    from lunelis.dupes import manage
    q = tmp_path / "_Lunelis Quarantine"
    q.mkdir()
    for name in ("IMG_1.JPG", "IMG_1.ARW", "IMG_1.xmp", "IMG_1.JPG.xmp", "IMG_2.JPG", "IMG_2.xmp"):
        (q / name).write_bytes(b"x")
    near = lambda name: sorted(os.path.basename(p) for p in manage._sidecars_near(SimpleNamespace(now=str(q / name))))
    assert near("IMG_1.JPG") == ["IMG_1.JPG.xmp"]            # IMG_1.xmp may be the RAW's: it stays
    assert near("IMG_2.JPG") == ["IMG_2.xmp"]                # nothing else is called IMG_2: it's this one's
    (q / "IMG_1.JPG").unlink()
    assert near("IMG_1.ARW") == ["IMG_1.xmp"]                # once the JPEG has gone, the RAW takes it along


# --- backups ------------------------------------------------------------------------------------------

@pytest.fixture
def backed_up(tmp_path):
    from lunelis.backups import core
    conn, rid, root = _library(tmp_path, {"2026/a.jpg": b"A" * 5000, "2026/b.jpg": b"B" * 7000,
                                          "2026/c.jpg": b"C" * 6000, "2025/x.jpg": b"X" * 3000})
    usb = tmp_path / "USB"
    usb.mkdir()
    sid = core.create_set(conn, "USB", str(usb), [rid])
    yield conn, rid, root, usb, sid
    conn.close()


def test_a_big_folder_is_looked_up_in_pieces(backed_up, monkeypatch):
    from lunelis.backups import core
    from lunelis.jobs import engine
    conn, rid, root, usb, sid = backed_up
    monkeypatch.setattr(core, "IN_CHUNK", 2)                 # three files in 2026: two pieces
    assert engine.run_job(conn, core.start(conn, sid)).state == "done"
    assert conn.execute("SELECT COUNT(*) FROM backup_files").fetchone()[0] == 4
    stamps = {p: p.stat().st_mtime_ns for p in usb.rglob("*.jpg")}
    r = core.backup_folder(conn, rid, "2026", throttle=Throttle(None), should_cancel=None, workers=1,
                           options={"set_id": sid})
    assert r.hashed == 0                                     # all three were found as already backed up
    assert {p: p.stat().st_mtime_ns for p in usb.rglob("*.jpg")} == stamps
    v = core.verify_folder(conn, rid, "2026", throttle=Throttle(None), should_cancel=None, workers=1,
                           options={"set_id": sid})
    assert v.hashed == 3                                     # the verify pass reads only its folder's copies


def test_a_restored_file_no_longer_looks_broken(backed_up):
    from lunelis.backups import core
    from lunelis.jobs import engine
    conn, rid, root, usb, sid = backed_up
    assert engine.run_job(conn, core.start(conn, sid)).state == "done"
    fid, old_mtime = conn.execute("SELECT id, mtime FROM files WHERE filename = 'a.jpg'").fetchone()
    (root / "2026/a.jpg").write_bytes(b"\0" * 5000)          # it went bad on disk...
    conn.execute("INSERT INTO damaged (file_id, problem) VALUES (?, 'zero_filled')", (fid,))
    conn.execute("UPDATE files SET thumb_error = 'OSError: cannot identify image file',"
                 " thumbnail_path = '0000/1.jpg', mtime = '1999-01-01T00:00:00+00:00' WHERE id = ?", (fid,))
    conn.execute("INSERT OR REPLACE INTO exif (file_id, extracted_mtime, read_error) VALUES (?, ?, 'unreadable')",
                 (fid, "1999-01-01T00:00:00+00:00"))
    conn.commit()
    res = core.restore_files(conn, sid, [fid])
    assert res.restored == 1 and (root / "2026/a.jpg").read_bytes() == b"A" * 5000
    err, thumb, mtime = conn.execute("SELECT thumb_error, thumbnail_path, mtime FROM files WHERE id = ?",
                                     (fid,)).fetchone()
    assert err is None and thumb is None                     # the next pass makes its picture afresh
    assert mtime != "1999-01-01T00:00:00+00:00"              # the file date is the restored file's
    from lunelis.importers import metadata
    from lunelis.raw import thumbnails
    assert fid in [r[0] for r in conn.execute(thumbnails.PENDING_SQL)]
    assert fid in [r[0] for r in conn.execute(metadata.PENDING_SQL)]      # ...and reads its details again
    before = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    scan_root(conn, rid)                                     # a scan finds nothing changed about it
    assert conn.execute("SELECT content_hash IS NOT NULL FROM files WHERE id = ?", (fid,)).fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == before


# --- family gallery -----------------------------------------------------------------------------------

def test_the_gallery_prefers_the_home_network_over_a_vpn():
    from lunelis.gallery import pick_lan_address
    # NordVPN is on: ordinary traffic leaves through 10.5.0.2, but the phones are on 192.168.1.x.
    assert pick_lan_address(["10.5.0.2", "192.168.1.50"], routed="10.5.0.2") == "192.168.1.50"
    assert pick_lan_address(["172.20.3.4", "10.5.0.2"], routed="10.5.0.2") == "172.20.3.4"
    assert pick_lan_address(["192.168.56.1", "192.168.1.50"], routed="192.168.1.50") == "192.168.1.50"
    assert pick_lan_address(["10.0.0.7"], routed="10.0.0.7") == "10.0.0.7"           # a 10.x home network still works
    assert pick_lan_address(["169.254.3.3", "8.8.8.8", "bogus"], routed=None) == "127.0.0.1"
    assert pick_lan_address([], None) == "127.0.0.1"
    assert pick_lan_address(["192.168.1.50"], routed="100.64.0.9") == "192.168.1.50"   # Tailscale's isn't home


@pytest.fixture
def served(tmp_path):
    from lunelis import gallery
    from lunelis.albums import model as albums
    root = tmp_path / "Photos"
    root.mkdir()
    for i in range(3):
        Image.fromarray(np.full((300, 400, 3), 60 * i + 40, np.uint8)).save(root / f"P{i}.jpg")
    db = tmp_path / "cat.db"
    conn = open_catalog(db)
    scan_root(conn, add_root(conn, root))
    ids = [r[0] for r in conn.execute("SELECT id FROM files ORDER BY filename")]
    aid = albums.create(conn, "Birthday", ids[:2])
    g = gallery.Gallery(db, tmp_path / "gcache", port=0, host="127.0.0.1")
    g.start()
    yield conn, ids, aid, g, tmp_path
    g.stop()
    conn.close()


def _get(g, path):
    try:
        r = urllib.request.urlopen(f"http://127.0.0.1:{g.port}{path}", timeout=20)
        return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def test_gallery_pages_arent_kept_by_the_browser_but_pictures_may_be(served):
    from lunelis import gallery
    conn, ids, aid, g, tmp = served
    token = gallery.share(conn, aid, pin="2468")
    code, _, h = _get(g, f"/s/{token}/")
    assert code == 200 and h["Cache-Control"] == "no-store"                 # the PIN page
    gallery.share(conn, aid, pin=None)
    for path in (f"/s/{token}/", f"/s/{token}/tv"):
        code, _, h = _get(g, path)
        assert code == 200 and h["Cache-Control"] == "no-store", path
    code, body, h = _get(g, f"/s/{token}/thumb/{ids[0]}.jpg")
    assert code == 200 and h["Cache-Control"] == "private, max-age=300" and body[:2] == b"\xff\xd8"
    code, _, h = _get(g, f"/s/{token}/img/{ids[2]}.jpg")                    # not in the album
    assert code == 404 and h["Cache-Control"] == "no-store"
    gallery.stop_sharing(conn, aid)
    code, _, h = _get(g, f"/s/{token}/")
    assert code == 404 and h["Cache-Control"] == "no-store"


def test_a_gallery_request_opens_the_catalog_once(served, monkeypatch):
    from lunelis import gallery
    conn, ids, aid, g, tmp = served
    token = gallery.share(conn, aid)
    opened, listed = [], []
    real_conn, real_ids = g._conn, g.ids
    monkeypatch.setattr(g, "_conn", lambda: (opened.append(1), real_conn())[1])
    monkeypatch.setattr(g, "ids", lambda *a, **k: (listed.append(1), real_ids(*a, **k))[1])
    for path, lists in ((f"/s/{token}/img/{ids[0]}.jpg", 0), (f"/s/{token}/thumb/{ids[1]}.jpg", 0),
                        (f"/s/{token}/", 1), (f"/s/{token}/img/{ids[2]}.jpg", 0), ("/s/nope/", 0)):
        opened.clear()
        listed.clear()
        _get(g, path)
        assert len(opened) == 1, path                        # it was two or three
        assert len(listed) == lists, path                    # a picture no longer lists the whole album
    assert g.in_album(aid, ids[0]) and not g.in_album(aid, ids[2]) and g.ids(aid) == ids[:2]


def test_gallery_copies_belong_to_one_catalog_and_one_version_of_the_file(served):
    from lunelis import gallery
    from lunelis.catalog.cachecheck import catalog_id
    conn, ids, aid, g, tmp = served
    cache = tmp / "gcache"
    cache.mkdir()
    stale = Image.new("RGB", (40, 30), (255, 0, 255))
    stale.save(cache / f"{ids[0]}_{gallery.THUMB}_0.jpg")                # a copy from before 0.54, of some other photo
    (cache / ("0" * 32)).mkdir()                                         # another catalog's copies
    stale.save(cache / ("0" * 32) / f"{ids[0]}_{gallery.THUMB}_0_abc.jpg")
    (cache / "not ours").mkdir()
    (cache / "not ours" / "keep.txt").write_text("x")
    (cache / "notes.txt").write_text("x")

    data = g.resized(ids[0], gallery.THUMB)
    got = Image.open(__import__("io").BytesIO(data))
    assert got.size == (400, 300) and abs(got.getpixel((5, 5))[0] - 40) < 6    # the real photo, not the stale copy
    cid = catalog_id(conn)
    mine = sorted(p.name for p in (cache / cid).iterdir())
    assert len(mine) == 1 and mine[0].startswith(f"{ids[0]}_{gallery.THUMB}_0_") and mine[0].endswith(".jpg")
    assert sorted(p.name for p in cache.iterdir()) == sorted([cid, "not ours", "notes.txt"])   # old copies cleared
    assert (cache / "not ours" / "keep.txt").exists()                    # and nothing that isn't a copy

    # The file is replaced (another size and date): the old copy isn't served for it.
    Image.fromarray(np.full((300, 400, 3), 220, np.uint8)).save(tmp / "Photos" / "P0.jpg", quality=30)
    scan_root(conn, conn.execute("SELECT id FROM roots").fetchone()[0])
    got = Image.open(__import__("io").BytesIO(g.resized(ids[0], gallery.THUMB)))
    assert abs(got.getpixel((5, 5))[0] - 220) < 6
    assert len(list((cache / cid).iterdir())) == 1                       # the earlier version's copy was removed

    # The same folder used with another catalog (a restore): nothing of the first one is served.
    conn.execute("UPDATE settings SET value = ? WHERE key = 'catalog_uuid'", ('"' + "1" * 32 + '"',))
    conn.commit()
    g2 = gallery.Gallery(g.db_path, cache, port=0, host="127.0.0.1")
    assert g2.cache_path(conn, ids[0], gallery.THUMB).parent.name == "1" * 32
    assert not (cache / cid).exists()


# --- faces --------------------------------------------------------------------------------------------

class _Colours:
    """Each coloured half of a photo is a face; its fingerprint is its colour."""
    model_id = "fake-faces"

    def detect(self, rgb):
        w = rgb.shape[1]
        out = []
        for i, half in enumerate((rgb[:, : w // 2], rgb[:, w // 2:])):
            c = half.reshape(-1, 3).mean(0)
            if c.max() - c.min() < 40:
                continue
            v = np.zeros(128, np.float32)
            v[:3] = c
            v /= np.linalg.norm(v)
            out.append(([0.1 + 0.5 * i, 0.2, 0.3, 0.4], 0.95, v))
        return out


RED, BLUE, GREEN = (200, 30, 30), (30, 30, 200), (30, 200, 30)


def _face_library(tmp_path, name="c.db"):
    from lunelis.raw.thumbnails import generate_pending
    root = tmp_path / "People"
    if not root.exists():
        for folder, shots in (("one", [RED, RED, BLUE]), ("two", [RED, BLUE, GREEN]), ("three", [GREEN, RED])):
            (root / folder).mkdir(parents=True)
            for i, colour in enumerate(shots):
                img = Image.new("RGB", (400, 300), (128, 128, 128))
                img.paste(Image.new("RGB", (200, 300), colour), (0, 0))
                img.save(root / folder / f"{i}.jpg")
    conn = open_catalog(tmp_path / name)
    rid = add_root(conn, root)
    scan_root(conn, rid)
    generate_pending(conn, paths.THUMBNAIL_CACHE)
    return conn, rid


def _partition(conn):
    """Which photos' faces share a group, whatever the group numbers are."""
    by: dict = {}
    for cluster, rel in conn.execute("SELECT fa.cluster, f.rel_path FROM faces fa JOIN files f ON f.id = fa.file_id"
                                     " WHERE fa.cluster IS NOT NULL"):
        by.setdefault(cluster, []).append(rel)
    return sorted(sorted(v) for v in by.values())


def test_the_faces_job_reads_the_known_faces_once_not_after_every_folder(tmp_path, monkeypatch):
    from lunelis.recognize import faces
    monkeypatch.setattr(faces, "backend", lambda: _Colours())
    monkeypatch.setattr(faces, "_JOB_MEMO", faces.Memo())
    conn, rid = _face_library(tmp_path)
    kw = dict(throttle=None, should_cancel=None, workers=1)
    faces.job_folder(conn, rid, "one", **kw)
    assert conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0] == 3        # only that folder's photos
    red = [r[0] for r in conn.execute("SELECT fa.id FROM faces fa JOIN files f ON f.id = fa.file_id"
                                      " WHERE f.rel_path IN ('one/0.jpg', 'one/1.jpg')")]
    faces.name_faces(conn, red, "Ann")

    read_people, read_groups = [], []
    real_people, real_groups = faces._centroids, faces._groups
    monkeypatch.setattr(faces, "_centroids", lambda c: (read_people.append(1), real_people(c))[1])
    monkeypatch.setattr(faces, "_groups", lambda c, leave_out=None: (read_groups.append(1), real_groups(c, leave_out))[1])
    faces.job_folder(conn, rid, "two", **kw)
    faces.job_folder(conn, rid, "three", **kw)
    assert len(read_people) == 1 and len(read_groups) == 1                      # once, not once per folder
    # The answers are the ones a from-scratch pass gives.
    suggested = {rel for (rel,) in conn.execute(
        "SELECT f.rel_path FROM faces fa JOIN files f ON f.id = fa.file_id WHERE fa.suggested_person_id IS NOT NULL")}
    assert suggested == {"two/0.jpg", "three/1.jpg"}                            # the other red faces: "Ann?"
    assert _partition(conn) == [["one/2.jpg", "two/1.jpg"], ["three/0.jpg", "two/2.jpg"]]

    # Something else changes the catalog (a face named in the People page): read again.
    other = open_catalog(tmp_path / "c.db")
    other.execute("UPDATE people SET name = 'Anne' WHERE name = 'Ann'")
    other.commit()
    other.close()
    conn.execute("DELETE FROM face_scans WHERE file_id IN (SELECT id FROM files WHERE rel_path = 'three/1.jpg')")
    conn.commit()
    faces.job_folder(conn, rid, "three", **kw)
    assert len(read_people) == 2
    conn.close()


def test_grouping_in_pieces_gives_the_same_groups_as_all_at_once(tmp_path):
    from lunelis.recognize import faces
    rng = np.random.default_rng(7)
    people = rng.normal(size=(40, 128)).astype(np.float32)
    people /= np.linalg.norm(people, axis=1, keepdims=True)
    vecs = []
    for i in range(400):                                    # 40 people, ten faces each, shuffled
        v = people[i % 40] + rng.normal(scale=0.02, size=128).astype(np.float32)
        vecs.append((v / np.linalg.norm(v)).astype(np.float32))
    results = []
    for name, step in (("all.db", None), ("pieces.db", 37)):
        conn = open_catalog(tmp_path / name)
        rid = conn.execute("INSERT INTO roots (path) VALUES ('X:/none')").lastrowid
        fid = conn.execute("INSERT INTO files (root_id, rel_path, filename, ext, size_bytes, mtime)"
                           " VALUES (?, 'a.jpg', 'a.jpg', 'jpg', 1, 'm')", (rid,)).lastrowid
        ids = [conn.execute("INSERT INTO faces (file_id, bbox_json, embedding, confidence, model, created_at)"
                            " VALUES (?, '[0,0,1,1]', ?, 0.9, 'm', datetime('now'))", (fid, v.tobytes())).lastrowid
               for v in vecs]
        conn.commit()
        if step is None:
            assert faces.group(conn) == 400
        else:
            memo = faces.Memo()
            for start in range(0, 400, step):
                faces.group(conn, ids[start:start + step], memo)
        clusters = [c for (c,) in conn.execute("SELECT cluster FROM faces ORDER BY id")]
        assert len(set(clusters)) == 40                     # more groups than the table's first 64...
        results.append([clusters.index(c) for c in clusters])
        conn.close()
    assert results[0] == results[1]
    cents = faces._Cents()
    for k in range(200):                                    # the table grows past its first size in place
        cents.new(k + 1, people[k % 40])
    assert len(cents) == 200 and cents.cents.shape[0] >= 200 and cents.nearest(people[3])[0] == 3
