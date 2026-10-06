"""Step 9: moved files keep their catalog entry; damaged files are found with their surviving copies."""
import os
import shutil

import piexif
import pytest
from PIL import Image

from lunelis.catalog.ratings import set_ratings
from lunelis.catalog.schema import open_catalog
from lunelis.damage.check import check, survivors
from lunelis.dupes import detect
from lunelis.importers.metadata import extract_pending
from lunelis.importers.relink import relink
from lunelis.importers.scan import add_root, scan_root
from lunelis.jobs import engine
from lunelis.raw.thumbnails import generate_pending
from lunelis.xmp import sync


def jpeg(path, seed=1, when="2026:06:19 10:00:00"):
    img = Image.effect_noise((320 + seed, 240), 40 + seed).convert("RGB")
    exif = piexif.dump({"0th": {piexif.ImageIFD.Make: b"SONY"},
                        "Exif": {piexif.ExifIFD.DateTimeOriginal: when.encode()}})
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", exif=exif, quality=90)


@pytest.fixture
def lib(tmp_path):
    root = tmp_path / "Photos"
    jpeg(root / "2019/a.jpg", 1, "2019:07:24 10:00:01")
    jpeg(root / "2019/b.jpg", 2, "2019:07:24 10:00:02")
    conn = open_catalog(tmp_path / "cat.db")
    rid = add_root(conn, root)
    scan_root(conn, rid)
    extract_pending(conn)
    yield conn, root, rid, tmp_path
    conn.close()


def _row(conn, fid):
    return conn.execute("SELECT rel_path, missing_since FROM files WHERE id = ?", (fid,)).fetchone()


def _id(conn, rel):
    return conn.execute("SELECT id FROM files WHERE rel_path = ?", (rel,)).fetchone()[0]


# --- re-linking ---------------------------------------------------------------

def test_move_keeps_rating_exif_and_id(lib):
    conn, root, rid, _ = lib
    a = _id(conn, "2019/a.jpg")
    set_ratings(conn, [a], stars=5, label="Green")
    (root / "Trips").mkdir()
    shutil.move(root / "2019/a.jpg", root / "Trips/renamed.jpg")       # move + rename keeps mtime
    scan_root(conn, rid)
    r = relink(conn)
    assert r.linked == 1 and r.by_method == {"size+mtime": 1}
    assert tuple(_row(conn, a)) == ("Trips/renamed.jpg", None)          # same entry, new place
    assert tuple(conn.execute("SELECT stars, color_label FROM ratings WHERE file_id = ?", (a,)).fetchone()) \
        == (5, "Green")
    assert conn.execute("SELECT captured_at FROM exif WHERE file_id = ?", (a,)).fetchone()[0].startswith("2019-07-24")
    assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 2  # no blank duplicate entry
    assert tuple(conn.execute("SELECT method, from_path, to_path FROM file_moves").fetchone()) == \
        ("size+mtime", "2019/a.jpg", "Trips/renamed.jpg")


def test_copy_then_delete_is_matched_by_exif(lib):
    conn, root, rid, _ = lib
    a = _id(conn, "2019/a.jpg")
    set_ratings(conn, [a], stars=3)
    other = root.parent / "Elsewhere"
    add_root(conn, other.mkdir() or other)
    shutil.copyfile(root / "2019/a.jpg", other / "a.jpg")               # copyfile: new mtime
    (root / "2019/a.jpg").unlink()
    for r_id, in conn.execute("SELECT id FROM roots").fetchall():
        scan_root(conn, r_id)
    assert relink(conn).linked == 0                                      # no mtime match, no EXIF yet
    extract_pending(conn)
    r = relink(conn)
    assert r.by_method == {"exif": 1}
    assert _row(conn, a)[0] == "a.jpg"


def test_hash_tier_when_name_and_date_changed(lib, tmp_path):
    conn, root, rid, _ = lib
    b = _id(conn, "2019/b.jpg")
    set_ratings(conn, [b], stars=2)
    conn.execute("UPDATE files SET sample_hash = ? WHERE id = ?",
                 (detect.sample_hash(str(root / "2019/b.jpg"), (root / "2019/b.jpg").stat().st_size)[0], b))
    conn.execute("DELETE FROM exif WHERE file_id = ?", (b,))            # say its EXIF was never read
    conn.commit()
    data = (root / "2019/b.jpg").read_bytes()
    (root / "2019/b.jpg").unlink()
    (root / "2019/zz_new_name.jpg").write_bytes(data)                   # new name, new mtime
    scan_root(conn, rid)
    extract_pending(conn)
    r = relink(conn)
    assert r.by_method == {"hash": 1} and _row(conn, b)[0] == "2019/zz_new_name.jpg"


def test_ambiguous_or_user_data_is_never_merged(lib):
    conn, root, rid, _ = lib
    a = _id(conn, "2019/a.jpg")
    data, st = (root / "2019/a.jpg").read_bytes(), os.stat(root / "2019/a.jpg")
    (root / "2019/a.jpg").unlink()
    for name in ("x.jpg", "y.jpg"):                                      # two identical candidates
        (root / name).write_bytes(data)
        os.utime(root / name, ns=(st.st_atime_ns, st.st_mtime_ns))
    scan_root(conn, rid)
    extract_pending(conn)
    assert relink(conn, use_hashes=False).linked == 0                    # can't know which
    assert _row(conn, a)[1] is not None

    (root / "y.jpg").unlink()
    scan_root(conn, rid)
    set_ratings(conn, [_id(conn, "x.jpg")], stars=1)                     # newcomer has its own rating
    assert relink(conn).linked == 0


def test_central_sidecar_follows_the_move(lib):
    conn, root, rid, tmp_path = lib
    store = tmp_path / "store"
    a = _id(conn, "2019/a.jpg")
    set_ratings(conn, [a], stars=4)
    sync.export_pending(conn, mode="central", update_existing=False, store_dir=store)
    old = sync.central_path(store, rid, str(root), "2019/a.jpg", "a.jpg")
    assert os.path.exists(old)
    shutil.move(root / "2019/a.jpg", root / "moved.jpg")
    scan_root(conn, rid)
    relink(conn, sidecar_store=store)
    assert not os.path.exists(old)
    assert os.path.exists(sync.central_path(store, rid, str(root), "moved.jpg", "moved.jpg"))


# --- damaged files ------------------------------------------------------------

def test_damage_categories_and_survivors(lib):
    conn, root, rid, tmp_path = lib
    good = root / "2019/a.jpg"
    size = good.stat().st_size
    (root / "Old").mkdir()
    (root / "Old/a.jpg").write_bytes(b"\0" * size)                      # zero-filled twin
    (root / "Old/empty.jpg").write_bytes(b"")
    (root / "Old/junk.jpg").write_bytes(b"this is not an image at all")
    full = good.read_bytes()
    (root / "Old/cut.jpg").write_bytes(full[: len(full) // 2])           # truncated
    (root / "2019/b.ARW").write_bytes(b"II*\x00" + b"\0" * 64)          # not important for damage
    scan_root(conn, rid)
    extract_pending(conn)
    generate_pending(conn, tmp_path / "thumbs")
    r = check(conn)
    problems = dict(conn.execute("SELECT f.rel_path, d.problem FROM damaged d JOIN files f ON f.id = d.file_id"))
    assert problems["Old/a.jpg"] == "zero_filled"
    assert problems["Old/empty.jpg"] == "zero_bytes"
    assert problems["Old/junk.jpg"] == "unrecognised"
    assert problems["Old/cut.jpg"] in ("truncated", "corrupt")
    assert "2019/a.jpg" not in problems
    assert r.read_files <= 3                                             # only unrecognised headers read

    (best, path, why), *_ = survivors(conn, _id(conn, "Old/a.jpg"))
    assert best == _id(conn, "2019/a.jpg") and why == "Same name, size and date, another location"

    # Dismissed stays dismissed while the problem persists; fixed drops off.
    conn.execute("UPDATE damaged SET dismissed = 1 WHERE file_id = ?", (_id(conn, "Old/junk.jpg"),))
    conn.commit()
    shutil.copyfile(good, root / "Old/a.jpg")
    scan_root(conn, rid)
    extract_pending(conn)
    check(conn)
    left = dict(conn.execute("SELECT f.rel_path, d.dismissed FROM damaged d JOIN files f ON f.id = d.file_id"))
    assert "Old/a.jpg" not in left and left["Old/junk.jpg"] == 1


def test_raw_original_is_a_survivor(lib):
    conn, root, rid, _ = lib
    (root / "2019/DSC1.ARW").write_bytes(b"II*\x00\x08\x00\x00\x00" + b"\x00" * 200)
    (root / "2019/DSC1.JPG").write_bytes(b"\0" * 5000)
    scan_root(conn, rid)
    extract_pending(conn)
    check(conn)
    assert [why for _, _, why in survivors(conn, _id(conn, "2019/DSC1.JPG"))] == ["The RAW original"]


def test_integrity_job_flags_silent_changes(lib):
    conn, root, rid, _ = lib
    job = engine.create_job(conn, "full_hash", "baseline", [(rid, None)])
    engine.run_job(conn, job)
    p = root / "2019/b.jpg"
    st = os.stat(p)
    data = bytearray(p.read_bytes())
    data[len(data) // 2] ^= 0xFF                                         # one flipped byte...
    p.write_bytes(bytes(data))
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))                    # ...same size, same date
    job = engine.create_job(conn, "integrity", "check", [(rid, None)])
    engine.run_job(conn, job)
    check(conn)
    assert [tuple(r) for r in conn.execute(
        "SELECT f.rel_path, d.problem FROM damaged d JOIN files f ON f.id = d.file_id"
        " WHERE d.problem = 'changed_on_disk'")] == [("2019/b.jpg", "changed_on_disk")]
