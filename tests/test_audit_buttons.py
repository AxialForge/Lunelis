"""0.37.4 (button audit): the keep-safe pages' buttons do what they say."""
import hashlib
import os
import time

import pytest
from PIL import Image

from lunelis.catalog.schema import open_catalog
from lunelis.importers.scan import add_root, scan_root
from lunelis.importing import ingest
from lunelis.importing.templates import DEFAULT_TEMPLATE


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_reimporting_a_card_whose_library_copies_were_deleted_imports_them_again(tmp_path, monkeypatch):
    # D1: "Imported before from this card" must not be trusted once the copy is gone,
    # and Clear the card never deletes a file whose library copy isn't there.
    monkeypatch.setattr(ingest, "volume_info", lambda source: ("SERIAL1", "CARD"))
    lib = tmp_path / "Library"
    lib.mkdir()
    card = tmp_path / "card" / "DCIM" / "100MSDCF"
    card.mkdir(parents=True)
    for i in range(3):
        Image.new("RGB", (40, 30), (i * 70, 10, 10)).save(card / f"DSC0000{i}.JPG")
    cfg = ingest.Settings_(destination=str(lib), template=DEFAULT_TEMPLATE, staging_local=str(tmp_path / "st"),
                           staging_network=None, reserve_bytes=0)
    conn = open_catalog(tmp_path / "c.db")
    first = ingest.create_import(conn, str(tmp_path / "card"), cfg)
    assert ingest.run(conn, first, cfg)["placed"] == 3
    for p in lib.rglob("*.JPG"):
        p.unlink()                                         # the library copies are deleted in Explorer
    again = ingest.create_import(conn, str(tmp_path / "card"), cfg)
    s = ingest.run(conn, again, cfg)
    assert s.get("placed") == 3 and s.get("already_in_library", 0) == 0
    ingest.clear_card(conn, again)
    assert len(list(lib.rglob("*.JPG"))) == 3              # whatever happened, the photos exist somewhere


def test_clear_the_card_skips_a_file_whose_library_copy_vanished(tmp_path):
    lib = tmp_path / "Library"
    lib.mkdir()
    card = tmp_path / "card" / "DCIM" / "100MSDCF"
    card.mkdir(parents=True)
    Image.new("RGB", (40, 30), (200, 10, 10)).save(card / "DSC00001.JPG")
    cfg = ingest.Settings_(destination=str(lib), template=DEFAULT_TEMPLATE, staging_local=str(tmp_path / "st"),
                           staging_network=None, reserve_bytes=0)
    conn = open_catalog(tmp_path / "c.db")
    imp = ingest.create_import(conn, str(tmp_path / "card"), cfg)
    ingest.run(conn, imp, cfg)
    next(lib.rglob("*.JPG")).unlink()
    deleted, skipped = ingest.clear_card(conn, imp)
    assert deleted == 0 and skipped and (card / "DSC00001.JPG").exists()


def test_setting_aside_an_exact_duplicate_keeps_its_stars_albums_and_tags(tmp_path):
    # D3
    from lunelis.albums import model as albums
    from lunelis.dupes import quarantine as q
    from lunelis.jobs import engine
    root = tmp_path / "L"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir()
    data = os.urandom(5000)
    (root / "a" / "x.jpg").write_bytes(data)
    (root / "b" / "x.jpg").write_bytes(data)
    conn = open_catalog(tmp_path / "c.db")
    rid = add_root(conn, root)
    scan_root(conn, rid)
    for kind in ("duplicates", "verify"):
        engine.run_job(conn, engine.create_job(conn, kind, kind, [(rid, None)]))
    gid = conn.execute("SELECT id FROM duplicate_groups WHERE verified = 1").fetchone()[0]
    a = conn.execute("SELECT id FROM files WHERE rel_path = 'a/x.jpg'").fetchone()[0]
    b = conn.execute("SELECT id FROM files WHERE rel_path = 'b/x.jpg'").fetchone()[0]
    conn.execute("INSERT INTO ratings (file_id, stars) VALUES (?, 5)", (b,))
    tag = conn.execute("INSERT INTO tags (name) VALUES ('Trip')").lastrowid
    conn.execute("INSERT INTO file_tags (file_id, tag_id) VALUES (?, ?)", (b, tag))
    conn.commit()
    album = albums.create(conn, "Trip") if hasattr(albums, "create") else None
    if album is not None:
        albums.add_files(conn, album, [b])
    q.quarantine(conn, gid, [b])
    assert conn.execute("SELECT stars FROM ratings WHERE file_id = ?", (a,)).fetchone()[0] == 5
    assert conn.execute("SELECT 1 FROM file_tags WHERE file_id = ? AND tag_id = ?", (a, tag)).fetchone()
    if album is not None:
        assert conn.execute("SELECT 1 FROM album_files WHERE file_id = ? AND album_id = ?", (a, album)).fetchone()
    conn.close()


def test_back_up_now_redoes_a_copy_that_verify_flagged(tmp_path):
    # D4
    from lunelis.backups import core
    from lunelis.jobs import engine
    lib, dest = tmp_path / "Lib", tmp_path / "Backup"
    lib.mkdir()
    dest.mkdir()
    (lib / "a.jpg").write_bytes(os.urandom(4000))
    conn = open_catalog(tmp_path / "c.db")
    rid = add_root(conn, lib)
    scan_root(conn, rid)
    sid = core.create_set(conn, "t", str(dest), [rid])
    engine.run_job(conn, core.start(conn, sid))
    copy = next(p for p in dest.rglob("a.jpg"))
    copy.write_bytes(b"\0" * 4000)                          # bit rot on the backup drive
    engine.run_job(conn, core.start(conn, sid, verify=True))
    assert conn.execute("SELECT problem FROM backup_files").fetchone()[0]
    engine.run_job(conn, core.start(conn, sid))
    assert _sha(copy) == _sha(lib / "a.jpg")
    assert conn.execute("SELECT problem FROM backup_files").fetchone()[0] is None
    conn.close()


def test_start_migration_on_the_page_queues_the_job(tmp_path, monkeypatch):
    # D2: the Start button read a setting on the worker thread and always failed
    from PySide6.QtWidgets import QApplication, QMessageBox
    QApplication.instance() or QApplication([])
    from lunelis.migrate.plan import Options, plan
    from lunelis.ui.migrate_view import MigrateView
    src, target = tmp_path / "S", tmp_path / "T"
    src.mkdir()
    target.mkdir()
    Image.new("RGB", (20, 20)).save(src / "a.jpg")
    from lunelis import paths
    conn = open_catalog(paths.DEFAULT_CATALOG_PATH)        # the page's workers open the app's catalog
    rid = add_root(conn, src)
    scan_root(conn, rid)
    page = MigrateView(conn)
    page.migration_id = plan(conn, str(target), "{YYYY}", Options([rid]))
    page.refresh()
    end = time.monotonic() + 10
    while page.bg.busy() and time.monotonic() < end:
        QApplication.processEvents()
    page.bg.wait() if hasattr(page.bg, "wait") else None
    QApplication.processEvents()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a))
    page._start()
    end = time.monotonic() + 20
    while page._thread is not None and time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.02)
    assert not warnings, warnings
    mid = page.migration_id
    assert conn.execute("SELECT state FROM migrations WHERE id = ?", (mid,)).fetchone()[0] != "planned"
    job = conn.execute("SELECT job_id FROM migrations WHERE id = ?", (mid,)).fetchone()[0]
    assert job
    from lunelis.jobs import engine
    engine.cancel(conn, job)                               # the shared test catalog: leave nothing queued
    conn.close()


def test_deleting_or_unpacking_a_filter_keeps_the_rest_of_each_edit(tmp_path):
    # photo audit 1-3: masks, curves, lens and crop survive; the look stays the same
    from dataclasses import replace
    from lunelis.edit import store
    from lunelis.edit.stack import Stack, effective
    root = tmp_path / "P"
    root.mkdir()
    Image.new("RGB", (40, 30)).save(root / "a.jpg")
    conn = open_catalog(tmp_path / "c.db")
    scan_root(conn, add_root(conn, root))
    fid = conn.execute("SELECT id FROM files").fetchone()[0]
    store.save_filter(conn, "My warm look", {"temp": 20.0, "exposure": 0.5})
    curves = {"rgb": ((0.0, 0.0), (0.5, 0.6), (1.0, 1.0))}
    stack = replace(Stack("My warm look", 80, {"vibrance": 10.0}), curves=curves, lens={"vignette": 20.0})
    store.save(conn, fid, stack)
    look = effective(stack, store.filter_params(conn, "My warm look"))
    baked = store.baked(conn, stack)
    assert baked.curves == curves and baked.lens == stack.lens and baked.filter is None
    assert "_curves" not in baked.adjust
    assert effective(baked, None) == look                    # the same picture
    store.delete_filter(conn, "My warm look")
    after = store.get(conn, fid)
    assert after.curves == curves and after.lens == stack.lens and after.filter is None
    conn.close()


def test_a_tag_after_show_photos_shows_the_tag_and_views_have_a_chip(tmp_path):
    # browse 1-2
    from PySide6.QtWidgets import QApplication, QPushButton
    QApplication.instance() or QApplication([])
    from lunelis.tags import model as tags
    from lunelis.ui import main_window as mw
    w = mw.MainWindow()
    try:
        root = tmp_path / "T"
        root.mkdir()
        for i in range(4):
            Image.new("RGB", (30, 20), (i * 50, 0, 0)).save(root / f"t{i}.jpg")
        rid = add_root(w.conn, root)
        scan_root(w.conn, rid)
        ids = [r[0] for r in w.conn.execute("SELECT id FROM files WHERE root_id = ? ORDER BY id", (rid,))]
        tags.add(w.conn, ids[2:], ["AuditHiking"])
        w.reload()
        w.show_photos(ids[:2], "Bob")
        chips = [w.chips.itemAt(i).widget().text() for i in range(w.chips.count())]
        assert any("Bob" in c for c in chips)
        w.show_tag("AuditHiking")
        shown = {w.index.file_id(i) for i in range(len(w.index))}
        assert shown == set(ids[2:])
    finally:
        w._quitting = True
        w.close()


def test_adding_to_an_album_counts_the_photos_added(tmp_path):
    # browse 8
    from lunelis.albums import model as albums
    root = tmp_path / "A"
    root.mkdir()
    for i in range(3):
        Image.new("RGB", (20, 20), (i * 60, 0, 0)).save(root / f"a{i}.jpg")
    conn = open_catalog(tmp_path / "c.db")
    scan_root(conn, add_root(conn, root))
    ids = [r[0] for r in conn.execute("SELECT id FROM files")]
    album = albums.create(conn, "Trip")
    assert albums.add_files(conn, album, ids) == 3
    assert albums.add_files(conn, album, ids) == 0
    conn.close()


def test_a_set_aside_duplicates_sidecar_goes_beside_the_copy_that_stays(tmp_path):
    # migration rehearsal: the copy with darktable's XMP may not be the one kept
    from lunelis.dupes import quarantine as q
    from lunelis.jobs import engine
    root = tmp_path / "L"
    (root / "a").mkdir(parents=True)
    (root / "deep" / "b").mkdir(parents=True)
    data = os.urandom(6000)
    (root / "a" / "x.jpg").write_bytes(data)
    (root / "deep" / "b" / "x.jpg").write_bytes(data)
    (root / "deep" / "b" / "x.jpg.xmp").write_text("<x:xmpmeta xmlns:x='adobe:ns:meta/'/>", encoding="utf-8")
    conn = open_catalog(tmp_path / "c.db")
    rid = add_root(conn, root)
    scan_root(conn, rid)
    for kind in ("duplicates", "verify"):
        engine.run_job(conn, engine.create_job(conn, kind, kind, [(rid, None)]))
    gid = conn.execute("SELECT id FROM duplicate_groups WHERE verified = 1").fetchone()[0]
    b = conn.execute("SELECT id FROM files WHERE rel_path = 'deep/b/x.jpg'").fetchone()[0]
    q.quarantine(conn, gid, [b])
    assert (root / "a" / "x.jpg.xmp").exists()
    assert conn.execute("SELECT sidecar FROM files WHERE rel_path = 'a/x.jpg'").fetchone()[0] == "x.jpg.xmp"
    conn.close()
