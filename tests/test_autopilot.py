"""0.28 Autopilot Import: after an import, the shoot is sorted out in stages -
best burst frames, scene tags, an event, edits in your style, a draft album,
a highlight reel - and nothing is applied or removed before the review."""
import json
import os
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PIL import Image, ImageFilter  # noqa: E402

from lunelis.catalog.schema import open_catalog  # noqa: E402
from lunelis.importing import autopilot as ap  # noqa: E402

T0 = datetime(2026, 9, 12, 16, 0, 0)


@pytest.fixture
def shoot(tmp_path):
    from lunelis.importers.scan import add_root, scan_root
    from lunelis.raw.thumbnails import generate_pending
    from lunelis.settings import Settings
    root = tmp_path / "Photos"
    folder = root / "2026-09-12"
    folder.mkdir(parents=True)
    rng = np.random.default_rng(1)
    sharp = Image.fromarray((rng.random((300, 400, 3)) * 255).astype(np.uint8))
    names = []
    for k in range(3):                                   # a burst: frame 1 is the sharp one
        img = sharp if k == 1 else sharp.filter(ImageFilter.GaussianBlur(4))
        names.append((f"DSC0{k}.jpg", img, T0 + timedelta(seconds=0.3 * k)))
    for k in range(3, 7):
        names.append((f"DSC0{k}.jpg", Image.fromarray((rng.random((300, 400, 3)) * 255).astype(np.uint8)),
                      T0 + timedelta(minutes=5 * k)))
    for name, img, _ in names:
        img.save(folder / name, quality=92)
    conn = open_catalog(tmp_path / "cat.db")
    Settings(conn).set("stack_bursts", True)
    scan_root(conn, add_root(conn, root))
    ids = dict(conn.execute("SELECT filename, id FROM files").fetchall())
    for name, _img, when in names:
        conn.execute("INSERT OR REPLACE INTO exif (file_id, captured_at, camera_model) VALUES (?, ?, 'ILCE-7RM5')",
                     (ids[name], when.isoformat(timespec="milliseconds")))
    imp = conn.execute("INSERT INTO imports (source, template, destination, state) VALUES ('F:\\', 't', ?, 'done')",
                       (str(root),)).lastrowid
    for name, _img, _w in names:
        conn.execute("INSERT INTO import_items (import_id, source_rel, size, mtime, dest_path, state)"
                     " VALUES (?, ?, 1, 0, ?, 'placed')", (imp, name, str(folder / name)))
    conn.commit()
    generate_pending(conn, tmp_path / "thumbs")
    yield conn, ids, imp, tmp_path
    conn.close()


def _run(conn, imp, tmp, **kw):
    rid = ap.create_run(conn, imp)
    return rid, ap.run(conn, rid, tmp / "thumbs", tmp / "data", **kw)


def test_a_shoot_is_sorted_out_and_nothing_is_applied_or_removed(shoot):
    conn, ids, imp, tmp = shoot
    files_before = conn.execute("SELECT id, root_id, rel_path FROM files ORDER BY id").fetchall()
    rid, r = _run(conn, imp, tmp)
    st = r["stages"]
    assert r["state"] == "review" and sorted(r["file_ids"]) == sorted(ids.values())
    # Bursts: the sharp frame became the cover.
    assert st["bursts"]["status"] == "done"
    sid = conn.execute("SELECT stack_id FROM stack_files WHERE file_id = ?", (ids["DSC00.jpg"],)).fetchone()[0]
    assert conn.execute("SELECT cover_file_id FROM stacks WHERE id = ?", (sid,)).fetchone()[0] == ids["DSC01.jpg"]
    # No scene model and no learned look here: said why, skipped.
    assert st["scenes"]["status"] == "skipped" and "Settings" in st["scenes"]["summary"]
    assert st["edits"]["status"] == "skipped" and "15 edited photos" in st["edits"]["summary"]
    # An event and a draft album of the best frames (a burst counts once).
    assert st["event"]["status"] == "done" and "Shoot · Sep 12, 2026" in st["event"]["summary"]
    album = st["album"]["data"]["album_id"]
    in_album = {r[0] for r in conn.execute("SELECT file_id FROM album_files WHERE album_id = ?", (album,))}
    assert ids["DSC01.jpg"] in in_album and ids["DSC00.jpg"] not in in_album and len(in_album) == 5
    # The reel waits for a click.
    assert st["reel"]["status"] == "waiting" and len(st["reel"]["data"]["ids"]) == 5
    # Nothing destructive: same files, same places, no edits, nothing set aside.
    assert conn.execute("SELECT id, root_id, rel_path FROM files ORDER BY id").fetchall() == files_before
    assert conn.execute("SELECT COUNT(*) FROM edits").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM files WHERE quarantined_at IS NOT NULL").fetchone()[0] == 0


def test_the_review_undoes_applies_and_makes(shoot, tmp_path):
    conn, ids, imp, tmp = shoot
    from lunelis.edit import look
    # A learned look: everything a bit brighter.
    look.Model(n=40, mu=[0.0] * 10, sd=[1.0] * 10,
               sliders={"exposure": {"mean": 0.5, "w": [0.0] * 10, "use": 1.0}}).save(tmp / "data")
    rid, r = _run(conn, imp, tmp)
    assert r["stages"]["edits"]["status"] == "waiting" and "learned from 40 edits" in r["stages"]["edits"]["summary"]
    eid = r["stages"]["event"]["data"]["event_id"]
    ap.undo(conn, rid, "event")
    assert conn.execute("SELECT COUNT(*) FROM events WHERE id = ?", (eid,)).fetchone()[0] == 0
    sid = conn.execute("SELECT stack_id FROM stack_files WHERE file_id = ?", (ids["DSC00.jpg"],)).fetchone()[0]
    ap.undo(conn, rid, "bursts")
    assert conn.execute("SELECT cover_file_id FROM stacks WHERE id = ?", (sid,)).fetchone()[0] == ids["DSC00.jpg"]
    n = ap.apply_edits(conn, rid)
    assert n == 7 and conn.execute("SELECT COUNT(*) FROM edits").fetchone()[0] == 7
    path = ap.make_reel(conn, rid, tmp / "creations")
    assert os.path.exists(path) and path.endswith("highlights.mp4")
    ap.finish(conn, rid)
    assert ap.get(conn, rid)["state"] == "done" and ap.to_review(conn) == []


def test_an_interrupted_run_carries_on_and_stages_can_be_off(shoot):
    conn, ids, imp, tmp = shoot
    from lunelis.settings import Settings
    Settings(conn).set("autopilot_skip", ["album"])
    calls = []
    rid = ap.create_run(conn, imp)
    stop_after_one = lambda: len(calls) >= 1                       # noqa: E731
    r = ap.run(conn, rid, tmp / "thumbs", tmp / "data", progress=calls.append, stop=stop_after_one)
    assert r["state"] == "running" and r["stages"]["bursts"]["status"] == "done"
    assert r["stages"]["event"]["status"] == "pending" and ap.unfinished(conn) == [rid]
    r = ap.run(conn, rid, tmp / "thumbs", tmp / "data")
    assert r["state"] == "review" and r["stages"]["album"]["status"] == "off"
    assert calls == ["Best frame of each burst"]                 # the first stage wasn't done twice


def test_the_review_page(shoot, monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from lunelis.ui.autopilot_view import AutopilotView
    conn, ids, imp, tmp = shoot
    view = AutopilotView(conn)
    try:
        view.load()
        assert "No shoot waiting" in view.rows.itemAt(0).widget().text()
        rid, _ = _run(conn, imp, tmp)
        view.load()
        assert view.run_id == rid and "7 photos" in view.info.text()
        assert set(view.buttons["event"]) == {"undo"} and set(view.buttons["reel"]) == {"go", "skip"}
        shown = []
        view.show_ids.connect(lambda i, n: shown.append((sorted(i), n)))
        view.show_b.click()
        assert shown == [(sorted(ids.values()), "This shoot")]
        view.buttons["event"]["undo"].click()
        assert ap.get(conn, rid)["stages"]["event"]["status"] == "undone" and "undo" not in view.buttons["event"]
        view.buttons["reel"]["skip"].click()
        assert ap.get(conn, rid)["stages"]["reel"]["status"] == "skipped"
        view.done_b.click()
        assert ap.get(conn, rid)["state"] == "done" and view.run_id is None
    finally:
        view.deleteLater()


def test_the_import_page_asks_for_the_autopilot(shoot, monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from lunelis.settings import Settings
    from lunelis.ui import main_window as mw
    conn, ids, imp, tmp = shoot
    monkeypatch.setattr(mw, "open_catalog", lambda _p: conn)
    w = mw.MainWindow()
    try:
        w.importer.autopilot_cb.setChecked(True)
        assert Settings(conn).get("autopilot") is True
        ran = []
        monkeypatch.setattr(w, "_run_autopilot", lambda: ran.append(1))
        w.importer.autopilot.emit(imp)
        assert ap.unfinished(conn) == [conn.execute("SELECT MAX(id) FROM autopilot_runs").fetchone()[0]]
    finally:
        w._quitting = True
        w.close()
