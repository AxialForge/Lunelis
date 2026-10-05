"""0.33 hardening, from the October 2026 audit (docs/Audit-2026-10.md)."""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PIL import Image  # noqa: E402


# S3: text from the file is text, never markup ---------------------------------------------------

def test_metadata_from_a_file_is_shown_as_text(tmp_path):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from lunelis.catalog.schema import open_catalog
    from lunelis.importers.scan import add_root, scan_root
    from lunelis.ui import photoinfo
    from lunelis.ui.detail_view import InfoPanel
    root = tmp_path / "P"
    root.mkdir()
    Image.new("RGB", (20, 20)).save(root / "a.jpg")
    conn = open_catalog(tmp_path / "c.db")
    scan_root(conn, add_root(conn, root))
    fid = conn.execute("SELECT id FROM files").fetchone()[0]
    evil = '<a href="file://evil/share/x.exe">Open map</a><img src="file://evil/a.png">'
    conn.execute("INSERT OR REPLACE INTO exif (file_id, lens, camera_model) VALUES (?, ?, ?)", (fid, evil, evil))
    conn.commit()
    panel = InfoPanel()
    panel.show_info(photoinfo.load(conn, fid))
    for key in ("lens", "camera"):
        assert "<a " not in panel.fields[key].text() and "&lt;a href" in panel.fields[key].text()
    opened = []
    from PySide6.QtGui import QDesktopServices
    import lunelis.ui.detail_view as dv
    real = dv.QDesktopServices.openUrl
    dv.QDesktopServices.openUrl = lambda url: opened.append(url.toString())
    try:
        panel._link("file://evil/share/x.exe")
        panel._link("https://www.openstreetmap.org/?mlat=1&mlon=2")
    finally:
        dv.QDesktopServices.openUrl = real
    assert opened == ["https://www.openstreetmap.org/?mlat=1&mlon=2"]
    panel.deleteLater()
    conn.close()


# S5 / S6: emptying quarantine -------------------------------------------------------------------

def test_a_sidecar_listed_twice_is_one_file(tmp_path):
    from lunelis.dupes import manage
    q = tmp_path / "q"
    q.mkdir()
    (q / "a.jpg").write_bytes(b"x")
    (q / "a.jpg.xmp").write_text("<x/>")
    e = type("E", (), {"now": str(q / "a.jpg")})()
    found = manage._sidecars_near(e)
    assert len(found) == 1                         # a.jpg.xmp and a.jpg.XMP are the same file here


def test_a_permanent_delete_needs_the_same_bytes(tmp_path):
    from lunelis.dupes import manage
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    a.write_bytes(b"photo bytes 1")
    b.write_bytes(b"photo bytes 1")
    c.write_bytes(b"photo bytes 2")                # same size, different picture
    assert manage._same_bytes(str(a), str(b))
    assert not manage._same_bytes(str(a), str(c))
    assert not manage._same_bytes(None, str(b)) and not manage._same_bytes(str(tmp_path / "gone"), str(b))


# S10: a sidecar clash is settled before anything moves -------------------------------------------

def test_quarantine_renames_photo_and_sidecar_together(tmp_path):
    from lunelis.catalog.schema import open_catalog
    from lunelis.dupes import quarantine
    from lunelis.importers.scan import add_root, scan_root
    root = tmp_path / "Photos"
    (root / "x").mkdir(parents=True)
    Image.new("RGB", (20, 20), "red").save(root / "x" / "a.jpg")
    Image.new("RGB", (20, 20), "red").save(root / "x" / "keep.jpg")
    (root / "x" / "a.jpg.xmp").write_text("<mine/>")
    conn = open_catalog(tmp_path / "c.db")
    scan_root(conn, add_root(conn, root))
    ids = dict(conn.execute("SELECT filename, id FROM files").fetchall())
    fid = ids["a.jpg"]
    conn.execute("UPDATE files SET sidecar = 'a.jpg.xmp' WHERE id = ?", (fid,))
    gid = conn.execute("INSERT INTO duplicate_groups (method, verified) VALUES ('exact', 1)").lastrowid
    conn.executemany("INSERT INTO duplicate_group_files (group_id, file_id) VALUES (?, ?)",
                     [(gid, fid), (gid, ids["keep.jpg"])])
    conn.commit()
    qdir = root / quarantine.QUARANTINE_DIR / "x"
    qdir.mkdir(parents=True)
    (qdir / "a.jpg.xmp").write_text("<someone else's/>")     # only the sidecar's name is taken
    quarantine.quarantine(conn, gid, [fid])
    moved = conn.execute("SELECT quarantine_path FROM files WHERE id = ?", (fid,)).fetchone()[0]
    assert moved and os.path.exists(moved) and os.path.exists(moved + ".xmp")
    assert (qdir / "a.jpg.xmp").read_text() == "<someone else's/>"


# S12: crafted files ------------------------------------------------------------------------------

def test_crafted_takeout_json_is_skipped_not_fatal(tmp_path):
    from lunelis.importers import takeout
    bad = [{"title": 5}, {"title": "a.jpg", "geoData": "x", "people": "y", "photoTakenTime": {"timestamp": "9" * 30}},
           {"title": "a.jpg", "geoData": {"latitude": "41", "longitude": 1e308}}, [1, 2]]
    for n, d in enumerate(bad):
        p = tmp_path / f"{n}.json"
        p.write_text(json.dumps(d))
        out = takeout._parse(str(p))
        assert out is None or (out["taken"] is None and out["lat"] is None)


def test_crafted_cube_files_are_refused_cleanly():
    from lunelis.video.lut import LutError, parse_cube
    for text in ("LUT_3D_SIZE\n", "LUT_3D_SIZE 100000\n", "LUT_3D_SIZE 2\nDOMAIN_MIN nan 0 0\n" + "0 0 0\n" * 8,
                 "LUT_3D_SIZE 2\n" + "0 0 x\n" * 8):
        with pytest.raises(LutError):
            parse_cube(text)


# S13: autopilot's Undo takes off only what it suggested -------------------------------------------

def test_autopilot_scene_undo_keeps_earlier_suggestions(tmp_path):
    from lunelis.catalog.schema import open_catalog
    from lunelis.importing import autopilot as ap
    conn = open_catalog(tmp_path / "c.db")
    conn.execute("INSERT INTO roots (id, path) VALUES (1, 'X:\\\\')")
    for i in (1, 2):
        conn.execute("INSERT INTO files (id, root_id, rel_path, filename, ext, size_bytes, mtime)"
                     " VALUES (?, 1, ?, ?, 'jpg', 1, 0)", (i, f"{i}.jpg", f"{i}.jpg"))
    conn.execute("INSERT INTO tags (id, name) VALUES (1, 'Scene|Beach'), (2, 'Scene|Sunset')")
    conn.execute("INSERT INTO file_tags (file_id, tag_id, confidence) VALUES (1, 1, 0.9)")   # from before
    conn.execute("INSERT INTO file_tags (file_id, tag_id, confidence) VALUES (1, 2, 0.8)")   # this run's
    stages = {s: {"status": "done" if s == "scenes" else "off", "summary": "", "data": {}} for s in ap.STAGES}
    stages["scenes"]["data"] = {"ids": [1, 2], "added": [[1, 2]]}
    rid = conn.execute("INSERT INTO autopilot_runs (import_id, state, stages) VALUES (1, 'review', ?)",
                       (json.dumps(stages),)).lastrowid
    conn.commit()
    ap.undo(conn, rid, "scenes")
    assert [tuple(r) for r in conn.execute("SELECT file_id, tag_id FROM file_tags")] == [(1, 1)]
    conn.close()


# Space plays a video; culling has undo and ? -----------------------------------------------------

def test_the_shortcut_sheet_has_culling_and_video_keys():
    from PySide6.QtWidgets import QApplication, QMainWindow
    QApplication.instance() or QApplication([])
    from lunelis.ui.shortcuts import ShortcutSheet
    w = QMainWindow()
    sheet = ShortcutSheet(w, "Culling")
    groups = [r[0] for r in sheet.rows]
    assert groups[0].startswith("Culling") and "Video" in groups
    assert any(k == "Space or K" for _g, k, _d in sheet.rows)
    sheet.deleteLater()
    w.deleteLater()
