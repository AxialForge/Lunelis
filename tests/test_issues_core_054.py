"""0.54: engine and data fixes from the 10 October backlog - sidecars that
must not wipe what the catalog or another program knows, a full library
drive during import, bad backups, the same folder under two names, and a
set of smaller edges (shutter rules, version order, settings, GPS)."""
import errno
import json
import os
import sqlite3
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from lunelis.catalog import backup, schema
from lunelis.catalog.schema import open_catalog
from lunelis.importers.scan import RootOverlap, add_root, scan_root
from lunelis.settings import DEFAULTS, Settings


def _lib(tmp_path, names=("a.jpg",), sizes=None):
    root = tmp_path / "P"
    root.mkdir()
    for i, n in enumerate(names):
        (root / n).write_bytes(b"x" * (sizes[i] if sizes else 5000 + i))
    conn = open_catalog(tmp_path / "c.db")
    rid = add_root(conn, root)
    scan_root(conn, rid)
    return conn, rid, root


def _id(conn, rel="a.jpg"):
    return conn.execute("SELECT id FROM files WHERE rel_path = ?", (rel,)).fetchone()[0]


def _bump(path, seconds=5):
    st = os.stat(path)
    os.utime(path, (st.st_atime + seconds, st.st_mtime + seconds))


# --- large 7: deleting a file doesn't read whole tables -----------------------------------------

def test_file_columns_looked_up_on_delete_are_indexed_and_the_migration_reruns(tmp_path):
    conn, rid, root = _lib(tmp_path)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"idx_migration_items_file", "idx_dupgroup_files_file", "idx_stacks_cover", "idx_albums_cover"} <= names
    for table in ("migration_items", "duplicate_group_files"):
        plan = " ".join(r[3] for r in conn.execute(f"EXPLAIN QUERY PLAN SELECT 1 FROM {table} WHERE file_id = ?", (1,)))
        assert "USING" in plan and "INDEX" in plan, plan             # a search, not a scan of the table
    sql = next(s for v, _d, s in schema.MIGRATIONS if v == 47)
    conn.executescript(sql)                                          # safe to run twice
    conn.executescript(sql)
    conn.close()


# --- medium 1: a sidecar with no rating has no opinion ------------------------------------------

NO_RATING = """<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about="" xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
    xmlns:xmp="http://ns.adobe.com/xap/1.0/"
    crs:Exposure2012="+0.35"%s/>
 </rdf:RDF>
</x:xmpmeta>
"""


def test_a_sidecar_without_a_rating_keeps_the_stars_and_label(tmp_path):
    from lunelis.xmp import sync
    from lunelis.xmp.sidecar import XmpFields, parse_fields
    conn, rid, root = _lib(tmp_path)
    side = root / "a.jpg.xmp"
    side.write_text(NO_RATING % "", encoding="utf-8")
    scan_root(conn, rid)
    fid = _id(conn)
    conn.execute("INSERT INTO ratings (file_id, stars, flag, color_label) VALUES (?, 4, 'reject', 'Red')", (fid,))
    conn.commit()
    assert sync.import_sidecars(conn).done == 1
    assert tuple(conn.execute("SELECT stars, flag, color_label FROM ratings").fetchone()) == (4, "reject", "Red")
    # A label and still no rating: the label is taken, the stars stay.
    side.write_text(NO_RATING % '\n    xmp:Label="Green"', encoding="utf-8")
    _bump(side)
    scan_root(conn, rid)
    sync.import_sidecars(conn)
    assert tuple(conn.execute("SELECT stars, flag, color_label FROM ratings").fetchone()) == (4, "reject", "Green")
    # A rating of 0 that is really there still clears the stars (and the reject).
    side.write_text(NO_RATING % '\n    xmp:Rating="0"', encoding="utf-8")
    _bump(side, 10)
    scan_root(conn, rid)
    sync.import_sidecars(conn)
    assert tuple(conn.execute("SELECT stars, flag, color_label FROM ratings").fetchone()) == (0, None, None)
    assert parse_fields(NO_RATING % "") == XmpFields() and not parse_fields(NO_RATING % "").has_rating
    assert parse_fields(NO_RATING % ' xmp:Rating="0"').has_rating
    conn.close()


# --- medium 2: keywords another program added are not wiped -------------------------------------

def _sidecar(rating, keywords=()):
    bag = ""
    if keywords:
        lis = "".join(f"<rdf:li>{k}</rdf:li>" for k in keywords)
        bag = f"\n   <dc:subject>\n    <rdf:Bag>{lis}</rdf:Bag>\n   </dc:subject>\n  "
    return ('<x:xmpmeta xmlns:x="adobe:ns:meta/">\n <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
            '  <rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/"'
            f' xmlns:dc="http://purl.org/dc/elements/1.1/" xmp:Rating="{rating}">{bag}</rdf:Description>\n'
            ' </rdf:RDF>\n</x:xmpmeta>\n')


def test_keywords_added_outside_survive_a_rating_made_before_the_next_scan(tmp_path):
    from lunelis.catalog.ratings import set_ratings
    from lunelis.tags import model as tags
    from lunelis.xmp import sync
    from lunelis.xmp.sidecar import read_sidecar
    conn, rid, root = _lib(tmp_path)
    side = root / "a.jpg.xmp"
    side.write_text(_sidecar(2), encoding="utf-8")
    scan_root(conn, rid)
    sync.import_sidecars(conn)
    fid = _id(conn)
    # Another program adds a keyword; Lunelis hasn't scanned since.
    side.write_text(_sidecar(2, ["Harbour"]), encoding="utf-8")
    _bump(side)
    tags.add(conn, [fid], ["Family"])
    set_ratings(conn, [fid], stars=5)
    r = sync.export_pending(conn, mode="beside")
    assert r.done == 1 and r.failed == 0
    got = read_sidecar(str(side))
    assert got.stars == 5 and {k.lower() for k in got.keywords} == {"harbour", "family"}
    assert {t.lower() for t in tags.tags_of(conn, fid)} == {"harbour", "family"}       # the catalog learned it
    # Once Lunelis has read the sidecar, taking a tag off here takes it off there.
    tags.remove(conn, [fid], "Harbour")
    sync.export_pending(conn, mode="beside")
    assert {k.lower() for k in read_sidecar(str(side)).keywords} == {"family"}
    conn.close()


# --- medium 11 / small 7: import ------------------------------------------------------------------

def _card(tmp_path, n=3):
    card = tmp_path / "card"
    (card / "DCIM/100TEST").mkdir(parents=True)
    for i in range(n):
        (card / f"DCIM/100TEST/IMG_{i:04d}.JPG").write_bytes(b"\xff\xd8\xff" + os.urandom(3000 + i))
    return card


def test_a_full_library_drive_pauses_the_import_instead_of_failing_every_file(tmp_path, monkeypatch):
    from lunelis.importing import ingest
    from lunelis.importing.templates import DEFAULT_TEMPLATE
    assert ingest.disk_full(OSError(errno.ENOSPC, "No space left on device"))
    if sys.platform == "win32":
        assert ingest.disk_full(OSError(errno.EIO, "There is not enough space on the disk", None, 112))
        assert ingest.disk_full(OSError(errno.EIO, "The disk is full", None, 39))
    assert not ingest.disk_full(PermissionError(errno.EACCES, "denied")) and not ingest.disk_full(ValueError("x"))

    conn = open_catalog(tmp_path / "cat.db")
    lib = tmp_path / "Library"
    lib.mkdir()
    cfg = ingest.Settings_(destination=str(lib), template=DEFAULT_TEMPLATE, staging_local=str(tmp_path / "staging"),
                           staging_network=None, reserve_bytes=0)
    imp = ingest.create_import(conn, str(_card(tmp_path)), cfg)
    ingest.stage(conn, imp, cfg)
    real, calls = ingest._copy_hashed, []

    def full(src, dst, should_stop=None):
        calls.append(dst)
        raise OSError(errno.ENOSPC, "No space left on device")
    monkeypatch.setattr(ingest, "_copy_hashed", full)
    with pytest.raises(ingest.DestinationFull) as e:
        ingest.place(conn, imp, cfg)
    assert "full" in str(e.value) and len(calls) == 1                 # stopped at the first file
    states = [s for s, in conn.execute("SELECT state FROM import_items WHERE import_id = ?", (imp,))]
    assert states == ["staged"] * 3                                   # nothing marked failed
    state, status = conn.execute("SELECT state, status FROM imports WHERE id = ?", (imp,)).fetchone()
    assert state == "waiting" and "full" in status
    # With room again, importing again carries on from where it stopped.
    monkeypatch.setattr(ingest, "_copy_hashed", real)
    assert ingest.place(conn, imp, cfg)["placed"] == 3
    conn.close()


def test_import_date_is_the_day_on_this_pcs_clock():
    from lunelis.importing import ingest
    created = "2026-06-20 02:30:00"                                    # as SQLite's datetime('now'): UTC
    want = datetime(2026, 6, 20, 2, 30, tzinfo=timezone.utc).astimezone().date()
    assert ingest._local_date(created) == want


# --- medium 18: Takeout JSON files are read once --------------------------------------------------

def test_takeout_json_files_already_read_are_not_opened_again(tmp_path, monkeypatch):
    from lunelis.importers import takeout
    root = tmp_path / "Takeout"
    root.mkdir()
    for n in ("a.jpg", "b.jpg"):
        (root / n).write_bytes(b"x" * 100)
        (root / f"{n}.supplemental-metadata.json").write_text(
            json.dumps({"title": n, "photoTakenTime": {"timestamp": "1700000000"}}), encoding="utf-8")
    (root / "odd.jpg.json").write_text("not json at all", encoding="utf-8")
    conn = open_catalog(tmp_path / "c.db")
    rid = add_root(conn, root)
    scan_root(conn, rid)
    opened = []
    real = takeout._parse
    monkeypatch.setattr(takeout, "_parse", lambda p: (opened.append(p), real(p))[1])
    first = takeout.import_root(conn, rid)
    assert (first.json_files, first.json_read, first.matched) == (3, 3, 2) and len(opened) == 3
    opened.clear()
    again = takeout.import_root(conn, rid)
    assert (again.json_files, again.json_read, again.matched) == (3, 0, 2) and opened == []   # the bad one too
    # A changed JSON is read again, and what it says now is used.
    j = root / "a.jpg.supplemental-metadata.json"
    j.write_text(json.dumps({"title": "a.jpg", "photoTakenTime": {"timestamp": "1600000000"},
                             "description": "changed"}), encoding="utf-8")
    third = takeout.import_root(conn, rid)
    assert third.json_read == 1 and [os.path.basename(p) for p in opened] == [j.name]
    assert conn.execute("SELECT taken_utc, description FROM takeout_meta WHERE file_id = ?",
                        (_id(conn),)).fetchone()[:] == (1600000000, "changed")
    # A JSON that's gone is forgotten.
    j.unlink()
    takeout.import_root(conn, rid)
    assert conn.execute("SELECT COUNT(*) FROM takeout_jsons").fetchone()[0] == 2
    conn.close()


# --- small 1: a bad backup zip ---------------------------------------------------------------------

def test_a_bad_backup_zip_is_a_message_and_leaves_nothing_behind(tmp_path):
    cat = tmp_path / "catalog.db"
    open_catalog(cat).close()
    before = cat.read_bytes()
    not_zip = tmp_path / "catalog-20260101-000000-manual.zip"
    not_zip.write_bytes(b"this is not a zip file")
    no_catalog = tmp_path / "other.zip"
    with zipfile.ZipFile(no_catalog, "w") as z:
        z.writestr("readme.txt", "hello")
    not_db = tmp_path / "notdb.zip"
    with zipfile.ZipFile(not_db, "w") as z:
        z.writestr("catalog.db", b"definitely not sqlite" * 400)
    for bad in (not_zip, no_catalog, not_db):
        with pytest.raises(ValueError):
            backup.restore(bad, cat)
        assert not cat.with_suffix(".db.restoring").exists(), bad.name
        assert cat.read_bytes() == before                             # the catalog is untouched
    # At start-up: the request is cleared and the error is one main() shows as a warning.
    (tmp_path / backup.PENDING_RESTORE).write_text(json.dumps({"zip": str(not_zip)}), encoding="utf-8")
    with pytest.raises((OSError, ValueError)):
        backup.finish_pending_restore(tmp_path, cat)
    assert not (tmp_path / backup.PENDING_RESTORE).exists() and not cat.with_suffix(".db.restoring").exists()
    (tmp_path / backup.PENDING_RESTORE).write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        backup.finish_pending_restore(tmp_path, cat)


# --- small 2: one folder, two names ---------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="junctions are a Windows thing")
def test_the_same_folder_under_another_name_is_refused(tmp_path):
    import _winapi
    conn, rid, root = _lib(tmp_path)
    alias = tmp_path / "Alias"
    try:
        _winapi.CreateJunction(str(root), str(alias))
    except OSError:
        pytest.skip("can't make a junction here")
    with pytest.raises(RootOverlap) as e:
        add_root(conn, alias)
    assert "same folder" in str(e.value)
    (root / "sub").mkdir()
    with pytest.raises(RootOverlap):
        add_root(conn, alias / "sub")                                 # inside it, by the other name
    assert add_root(conn, root) == rid                                # the same spelling is still just "already there"
    assert conn.execute("SELECT COUNT(*) FROM roots").fetchone()[0] == 1
    other = tmp_path / "Other"
    other.mkdir()
    assert add_root(conn, other) != rid                               # a different folder is fine
    conn.close()


# --- small 3: what was worked out from the old picture goes when it changes -----------------------

def test_a_changed_file_drops_its_unanswered_faces_scene_and_damage_record(tmp_path):
    conn, rid, root = _lib(tmp_path, ("a.jpg", "b.jpg"))
    a, b = _id(conn, "a.jpg"), _id(conn, "b.jpg")
    pid = conn.execute("INSERT INTO people (name) VALUES ('Ada')").lastrowid
    for fid in (a, b):
        conn.execute("INSERT INTO faces (file_id, bbox_json) VALUES (?, '[0,0,1,1]')", (fid,))               # found, unanswered
        conn.execute("INSERT INTO faces (file_id, bbox_json, person_id, confirmed) VALUES (?, '[0,0,1,1]', ?, 1)",
                     (fid, pid))                                                                             # named
        conn.execute("INSERT INTO faces (file_id, bbox_json, ignored) VALUES (?, '[0,0,1,1]', 2)", (fid,))   # a stranger
        conn.execute("INSERT INTO faces (file_id, bbox_json, source) VALUES (?, '[0,0,1,1]', 'user')", (fid,))  # hand-drawn
        conn.execute("INSERT INTO face_scans (file_id, model, faces) VALUES (?, 'm', 1)", (fid,))
        conn.execute("INSERT INTO embeddings (file_id, model, dim, vector) VALUES (?, 'm', 1, x'00000000')", (fid,))
        conn.execute("INSERT INTO damaged (file_id, problem) VALUES (?, 'changed_on_disk')", (fid,))
    conn.commit()
    (root / "a.jpg").write_bytes(b"y" * 7000)                         # edited: new size
    assert scan_root(conn, rid).updated == 1

    def n(table, fid):
        return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE file_id = ?", (fid,)).fetchone()[0]
    assert [n(t, a) for t in ("faces", "face_scans", "embeddings", "damaged")] == [3, 0, 0, 0]
    kept = conn.execute("SELECT person_id, ignored, source FROM faces WHERE file_id = ? ORDER BY id", (a,)).fetchall()
    assert [tuple(k) for k in kept] == [(pid, 0, "auto"), (None, 2, "auto"), (None, 0, "user")]
    assert [n(t, b) for t in ("faces", "face_scans", "embeddings", "damaged")] == [4, 1, 1, 1]   # untouched file
    conn.close()


# --- small 5 + 6: smart albums ---------------------------------------------------------------------

def test_shutter_rules_read_any_fraction_and_decimals(tmp_path):
    from lunelis.albums import smart
    speeds = ["3/5", "1/250", "0.6", "2", "30/1", "2.5s", "1/0", None]
    conn, rid, root = _lib(tmp_path, [f"{i}.jpg" for i in range(len(speeds))])
    for i, s in enumerate(speeds):
        conn.execute("INSERT INTO exif (file_id, shutter_speed) VALUES (?, ?)", (_id(conn, f"{i}.jpg"), s))
    conn.commit()

    def which(op, value):
        cond, params = smart.condition({"rules": [{"field": "shutter_s", "op": op, "value": value}]})
        return sorted(r[0] for r in conn.execute(
            "SELECT e.shutter_speed FROM files f LEFT JOIN exif e ON e.file_id = f.id"
            f" LEFT JOIN ratings rt ON rt.file_id = f.id WHERE {cond}", params))
    assert which("<", 1) == ["0.6", "1/250", "3/5"]                   # 3/5 was read as 3 seconds
    assert which(">=", 2) == ["2", "2.5s", "30/1"]
    assert which("=", 0.6) == ["0.6", "3/5"]
    conn.close()


def test_one_malformed_smart_album_does_not_break_the_list(tmp_path):
    from lunelis.albums import smart
    conn, rid, root = _lib(tmp_path)
    good = {"rules": [{"field": "stars", "op": ">=", "value": 0}]}
    bad = ["not json at all", '{"rules": [{"field": "iso", "op": ">=", "value": "abc"}]}', "[1, 2]",
           '{"rules": [5]}', '{"rules": "iso"}', '{"rules": [{"field": ["x"], "op": "=", "value": 1}]}',
           '{"rules": [{"field": "iso", "op": ">=", "value": null}]}', "null"]
    conn.execute("INSERT INTO albums (name, is_smart, smart_rule_json) VALUES ('Good', 1, ?)", (json.dumps(good),))
    for i, rule in enumerate(bad):
        conn.execute("INSERT INTO albums (name, is_smart, smart_rule_json) VALUES (?, 1, ?)", (f"Bad {i}", rule))
    conn.commit()
    listed = smart.smart_albums(conn)
    assert len(listed) == 1 + len(bad)
    by_name = {a.name: a for a in listed}
    assert by_name["Good"].count == 1 and all(by_name[f"Bad {i}"].count == 0 for i in range(len(bad)))
    assert "can't be read" in by_name["Bad 0"].blurb
    for rule in bad[1:]:
        smart.describe(json.loads(rule))                              # never raises
        with pytest.raises(smart.RuleError):
            smart.check(json.loads(rule))
    conn.close()


# --- small 8: version order -------------------------------------------------------------------------

def test_test_versions_are_in_order():
    from lunelis import updater
    newer = updater.is_newer
    assert newer("0.54.0-beta.2", "0.54.0-beta.1") and not newer("0.54.0-beta.1", "0.54.0-beta.2")
    assert not newer("0.54.0-beta.1", "0.54.0-beta.1")
    assert newer("0.54.0-beta.10", "0.54.0-beta.9")                   # by number, not by text
    assert newer("0.54.0-rc.1", "0.54.0-beta.3") and newer("0.54.0-beta.1", "0.54.0-alpha.4")
    assert newer("0.54.0", "0.54.0-rc.2") and not newer("0.54.0-rc.2", "0.54.0")
    assert newer("0.54.0-beta.1", "0.53.9") and newer("0.54.1-beta.1", "0.54.0")
    assert newer("v0.54.0-beta2", "v0.54.0-beta1")
    order = ["0.53.0", "0.54.0-alpha.1", "0.54.0-beta.1", "0.54.0-beta.2", "0.54.0-rc.1", "0.54.0", "0.54.1-beta.1"]
    assert sorted(reversed(order), key=updater.parse_version) == order
    assert updater.parse_version("1.2") == (1, 2, 0, 1)


# --- small 11: pre-upgrade backups don't pile up ----------------------------------------------------

def test_only_the_newest_few_pre_upgrade_backups_are_kept(tmp_path):
    db = tmp_path / "catalog.db"
    conn = open_catalog(db)
    folder = tmp_path / "backups"
    folder.mkdir()
    old = [folder / f"catalog-2026010{i}-000000-before-upgrade-v4{i}.zip" for i in range(1, 8)]
    others = [folder / "catalog-20260101-000000-daily.zip", folder / "catalog-20260102-000000-manual.zip",
              folder / "catalog-20260103-000000-before-migration.zip", folder / "holiday.zip"]
    for p in old + others:
        p.write_bytes(b"zip")
    schema._snapshot_before_upgrade(conn, db, 46)
    left = sorted(p.name for p in folder.iterdir() if "-before-upgrade-v" in p.name)
    assert len(left) == schema.PRE_UPGRADE_KEEP == 5
    assert left[:4] == [p.name for p in old[3:]] and left[-1].endswith("-before-upgrade-v46.zip")   # the newest
    assert all(p.exists() for p in others)                            # nothing else is ever removed
    conn.close()


# --- small 13: settings -----------------------------------------------------------------------------

def test_numeric_settings_are_range_checked_and_a_damaged_value_reads_as_the_default(tmp_path):
    conn = open_catalog(tmp_path / "c.db")
    s = Settings(conn)
    for key, bad in (("gallery_port", 80), ("gallery_port", 70000), ("gallery_port", "8735"), ("gallery_port", True),
                     ("integrity_gb", 0), ("trash_keep_days", -1), ("grid_default_size", 5),
                     ("grid_default_size", 100000)):
        with pytest.raises(ValueError):
            s.set(key, bad)
    for key, good in (("gallery_port", 9000), ("integrity_gb", 50), ("trash_keep_days", 0), ("trash_keep_days", 365),
                      ("grid_default_size", 260), ("detail_strip_height", 88), ("side_panel_width", 760)):
        s.set(key, good)
        assert s.get(key) == good
    # Stored values damaged behind its back: the default, never an exception.
    for key, raw in (("gallery_port", "999999"), ("integrity_gb", '"lots"'), ("trash_keep_days", "-5"),
                     ("grid_default_size", "{not json"), ("theme", "{not json"), ("catalog_backups_keep", "null")):
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (key, raw))
        assert s.get(key) == DEFAULTS[key], key
    assert s.all()["gallery_port"] == DEFAULTS["gallery_port"] and s.all()["theme"] == DEFAULTS["theme"]
    conn.close()


# --- small 14: GPS -----------------------------------------------------------------------------------

def _gps_tag(*parts):
    return SimpleNamespace(values=[SimpleNamespace(num=n, den=d) for n, d in parts], printable="x")


def _ref(letter):
    return SimpleNamespace(printable=letter, values=letter)


def test_gps_with_one_or_two_parts_and_a_missing_or_wrong_letter():
    from lunelis.importers.metadata import _gps
    full = _gps_tag((41, 1), (30, 1), (36, 1))
    assert _gps({"GPS GPSLatitude": full, "GPS GPSLatitudeRef": _ref("N")}, "Latitude") == 41.51
    assert _gps({"GPS GPSLatitude": full, "GPS GPSLatitudeRef": _ref("S")}, "Latitude") == -41.51
    assert _gps({"GPS GPSLongitude": full, "GPS GPSLongitudeRef": _ref("w")}, "Longitude") == -41.51
    # Degrees and decimal minutes, or decimal degrees alone, used to be dropped.
    assert _gps({"GPS GPSLatitude": _gps_tag((41, 1), (306, 10)), "GPS GPSLatitudeRef": _ref("N")}, "Latitude") == 41.51
    assert _gps({"GPS GPSLongitude": _gps_tag((8151, 100)), "GPS GPSLongitudeRef": _ref("W")}, "Longitude") == -81.51
    # No letter: a sign in the number is kept; an unsigned one is north / east.
    assert _gps({"GPS GPSLatitude": _gps_tag((-3351, 100))}, "Latitude") == -33.51
    assert _gps({"GPS GPSLatitude": full}, "Latitude") == 41.51
    # A letter and a signed number never cancel out.
    assert _gps({"GPS GPSLatitude": _gps_tag((-3351, 100)), "GPS GPSLatitudeRef": _ref("S")}, "Latitude") == -33.51
    # A letter that belongs to the other axis, an empty value or x/0: no place rather than a wrong one.
    assert _gps({"GPS GPSLatitude": full, "GPS GPSLatitudeRef": _ref("W")}, "Latitude") is None
    assert _gps({"GPS GPSLatitude": _gps_tag()}, "Latitude") is None
    assert _gps({"GPS GPSLatitude": _gps_tag((41, 0))}, "Latitude") is None
    assert _gps({}, "Latitude") is None


# --- small 17: sidecars reach the disk before they replace the old one ------------------------------

def test_a_sidecar_is_flushed_to_disk_before_it_replaces_the_old_one(tmp_path, monkeypatch):
    from lunelis.xmp import sidecar
    path = tmp_path / "a.jpg.xmp"
    sidecar.write_sidecar(str(path), sidecar.XmpFields(stars=2))
    events = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(sidecar.os, "fsync", lambda fd: (events.append("fsync"), real_fsync(fd))[1])
    monkeypatch.setattr(sidecar.os, "replace", lambda a, b: (events.append("replace"), real_replace(a, b))[1])
    sidecar.write_sidecar(str(path), sidecar.XmpFields(stars=4))
    assert events == ["fsync", "replace"]
    assert sidecar.read_sidecar(str(path)).stars == 4
    assert [p.name for p in tmp_path.iterdir()] == ["a.jpg.xmp"]      # no temp file left


# --- small 19: relinking saves in batches -----------------------------------------------------------

class _Counting:
    """A connection that counts its commits (sqlite3's own can't be patched)."""
    def __init__(self, conn):
        self._conn, self.commits = conn, 0

    def commit(self):
        self.commits += 1
        self._conn.commit()

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_relinking_moved_files_saves_in_batches(tmp_path, monkeypatch):
    from lunelis.catalog.ratings import set_ratings
    from lunelis.importers import relink
    names = [f"{i}.jpg" for i in range(12)]
    conn, rid, root = _lib(tmp_path, names)
    ids = [_id(conn, n) for n in names]
    set_ratings(conn, ids, stars=3)
    (root / "moved").mkdir()
    for n in names:
        os.replace(root / n, root / "moved" / n)
    scan_root(conn, rid)
    assert conn.execute("SELECT COUNT(*) FROM files WHERE missing_since IS NOT NULL").fetchone()[0] == 12
    counting = _Counting(conn)
    r = relink.relink(counting, use_hashes=False)
    assert r.linked == 12 and counting.commits < 12                   # it was one save per file
    rows = conn.execute("SELECT f.id, f.rel_path, f.missing_since, rt.stars FROM files f"
                        " JOIN ratings rt ON rt.file_id = f.id ORDER BY f.id").fetchall()
    assert [(r[0], r[2], r[3]) for r in rows] == [(i, None, 3) for i in sorted(ids)]   # the same entries, ratings kept
    assert all(r[1].startswith("moved/") for r in rows)
    assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 12
    assert not conn.in_transaction                                    # everything was saved
    # A small batch size still links everything.
    for n in names:
        os.replace(root / "moved" / n, root / n)
    scan_root(conn, rid)
    monkeypatch.setattr(relink, "COMMIT_EVERY", 5)
    assert relink.relink(conn, use_hashes=False).linked == 12 and not conn.in_transaction
    conn.close()


# --- small 10: more ids than one statement takes -----------------------------------------------------

def test_a_burst_and_the_autopilot_take_more_photos_than_one_statement_holds(tmp_path):
    from lunelis import stacks, timelapses
    from lunelis.importing import autopilot
    conn = open_catalog(tmp_path / "c.db")
    rid = conn.execute("INSERT INTO roots (path) VALUES (?)", (str(tmp_path),)).lastrowid
    n = 33_500                                                        # SQLite takes 32,766 values at most
    conn.executemany("INSERT INTO files (id, root_id, rel_path, filename, ext, size_bytes, mtime) VALUES (?, ?, ?, ?, 'jpg', 1, 'x')",
                     [(i, rid, f"{i:06d}.jpg", f"{i:06d}.jpg") for i in range(1, n + 1)])
    # Shot in reverse order of their names, and the first two have no time at all.
    conn.executemany("INSERT INTO exif (file_id, captured_at) VALUES (?, ?)",
                     [(i, f"2026-01-01T00:00:00.{n - i:06d}") for i in range(3, n + 1)])
    conn.execute("UPDATE files SET pair_of = 10 WHERE id = 11")       # a RAW+JPEG pair is one shot
    conn.commit()
    ids = list(range(1, n + 1))

    assert stacks.shooting_order(conn, ids)[:4] == [1, 2, n, n - 1] and len(stacks.shooting_order(conn, ids)) == n - 1
    assert autopilot._photos(conn, ids)[:4] == [1, 2, n, n - 1] and len(autopilot._photos(conn, ids)) == n
    seq = timelapses.make_manual(conn, ids)
    assert timelapses.stack(conn, seq) > 0
    st = stacks.make_burst(conn, ids)
    assert conn.execute("SELECT kind, size FROM stacks WHERE id = ?", (st,)).fetchone()[:] == ("chosen_burst", n - 1)
    assert conn.execute("SELECT COUNT(*) FROM stack_files WHERE stack_id = ?", (st,)).fetchone()[0] == n
    assert conn.execute("SELECT COUNT(*) FROM stacks").fetchone()[0] == 1          # the timelapse's stack went
    assert stacks.stacks_holding(conn, ids) == [st]
    best = autopilot.best_frames(conn, ids)                           # of a stack, only its cover
    assert best == [conn.execute("SELECT cover_file_id FROM stacks WHERE id = ?", (st,)).fetchone()[0]]
    assert autopilot._top_scene(conn, ids) is None and " · " in autopilot._event_name(conn, ids)
    conn.close()


# --- small 25: the test run sets its own Qt platform -------------------------------------------------

def test_conftest_sets_the_offscreen_platform_itself():
    text = (Path(__file__).parent / "conftest.py").read_text(encoding="utf-8")
    head = text.split("import pytest")[0]
    assert 'os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")' in head     # before anything imports Qt
    assert os.environ.get("QT_QPA_PLATFORM")
