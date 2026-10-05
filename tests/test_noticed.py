"""0.25 Lunelis noticed: brackets, panoramas, focus stacks, timelapses and star
trails found from EXIF and checked on the pictures; offered, never built by
themselves; dismissals remembered."""
import json
import os
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PIL import Image, ImageFilter  # noqa: E402

from lunelis import noticed  # noqa: E402
from lunelis.catalog.schema import open_catalog  # noqa: E402
from lunelis.importers.scan import add_root, scan_root  # noqa: E402

T0 = datetime(2025, 7, 4, 18, 0, 0)


def scene(w=2600, h=700, seed=3):
    """A textured landscape: blobs and lines, so features can be matched."""
    rng = np.random.default_rng(seed)
    img = Image.fromarray((rng.random((h // 8, w // 8, 3)) * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC)
    from PIL import ImageDraw
    d = ImageDraw.Draw(img)
    for _ in range(400):
        x, y = rng.integers(0, w), rng.integers(0, h)
        r = int(rng.integers(4, 30))
        d.ellipse((x - r, y - r, x + r, y + r), fill=tuple(int(v) for v in rng.integers(0, 255, 3)))
    for _ in range(80):
        d.line([tuple(rng.integers(0, [w, h])), tuple(rng.integers(0, [w, h]))],
               fill=tuple(int(v) for v in rng.integers(0, 255, 3)), width=3)
    return img


def brighten(img, k):
    a = np.asarray(img).astype(np.float32) * k
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def focus_frame(img, sharp_col, cols=3):
    """Everything blurred except one third of the frame."""
    blurred = img.filter(ImageFilter.GaussianBlur(6))
    w = img.width // cols
    blurred.paste(img.crop((sharp_col * w, 0, (sharp_col + 1) * w, img.height)), (sharp_col * w, 0))
    return blurred


class Shoot:
    def __init__(self, tmp_path):
        self.root = tmp_path / "Photos"
        self.root.mkdir()
        self.rows = []          # (name, when, shutter, aperture, iso, focal)
        self.n = 0

    def add(self, img, when, shutter="1/125", aperture=8.0, iso=100, focal=24.0):
        name = f"IMG_{self.n:04d}.jpg"
        self.n += 1
        img.convert("RGB").save(self.root / name, "JPEG", quality=92)
        self.rows.append((name, when, shutter, aperture, iso, focal))
        return name

    def catalog(self, tmp_path):
        conn = open_catalog(tmp_path / "cat.db")
        scan_root(conn, add_root(conn, self.root))
        ids = dict(conn.execute("SELECT filename, id FROM files").fetchall())
        for name, when, sh, ap, iso, focal in self.rows:
            conn.execute("INSERT OR REPLACE INTO exif (file_id, captured_at, camera_make, camera_model, lens,"
                         " focal_length_mm, aperture, shutter_speed, iso) VALUES (?, ?, 'SONY', 'ILCE-7RM5',"
                         " 'FE 24-70mm F2.8 GM II', ?, ?, ?, ?)", (ids[name], when.isoformat(), focal, ap, sh, iso))
        conn.commit()
        return conn, ids


@pytest.fixture
def shoot(tmp_path):
    return Shoot(tmp_path)


def kinds(conn):
    return sorted((k, len(json.loads(ids))) for k, ids in conn.execute("SELECT kind, file_ids FROM suggestions"))


def test_shutter_and_exposure_values():
    assert noticed.shutter_seconds("1/250") == pytest.approx(0.004)
    assert noticed.shutter_seconds("2.5") == 2.5 and noticed.shutter_seconds("") is None
    assert noticed._ev(1 / 125, 100, 8) - noticed._ev(1 / 250, 100, 8) == pytest.approx(1.0)


def test_a_panorama_sweep_is_found(shoot, tmp_path):
    big = scene()
    for k, x in enumerate((0, 550, 1100, 1650)):
        shoot.add(big.crop((x, 0, x + 900, 700)), T0 + timedelta(seconds=3 * k))
    conn, _ = shoot.catalog(tmp_path)
    assert noticed.find(conn, None) == 1
    assert kinds(conn) == [("panorama", 4)]


def test_an_hdr_bracket_is_found_and_a_lone_burst_is_not(shoot, tmp_path):
    base = scene().crop((300, 0, 1200, 700))
    for k, (sh, b) in enumerate((("1/250", 0.5), ("1/125", 1.0), ("1/60", 1.9))):
        shoot.add(brighten(base, b), T0 + timedelta(seconds=0.4 * k), shutter=sh)
    later = T0 + timedelta(minutes=10)
    for k in range(5):                                   # a burst: same exposure, same framing
        shoot.add(base, later + timedelta(seconds=0.2 * k))
    conn, _ = shoot.catalog(tmp_path)
    noticed.find(conn, None)
    assert kinds(conn) == [("hdr", 3)]


def test_a_focus_stack_is_found(shoot, tmp_path):
    base = scene().crop((300, 0, 1200, 700))
    for k in range(3):
        shoot.add(focus_frame(base, k), T0 + timedelta(seconds=0.8 * k))
    conn, _ = shoot.catalog(tmp_path)
    noticed.find(conn, None)
    assert kinds(conn) == [("focus", 3)]


def test_a_timelapse_and_star_trails_are_found(shoot, tmp_path):
    base = scene().crop((300, 0, 1200, 700))
    rng = np.random.default_rng(5)
    for k in range(20):
        noisy = np.clip(np.asarray(base).astype(np.int16) + rng.integers(-6, 6, (700, 900, 3)), 0, 255)
        shoot.add(Image.fromarray(noisy.astype(np.uint8)), T0 + timedelta(seconds=5 * k))
    night = T0 + timedelta(hours=4)
    dark = brighten(base, 0.35)
    for k in range(10):
        shoot.add(dark, night + timedelta(seconds=32 * k), shutter="30", aperture=2.8, iso=3200)
    conn, _ = shoot.catalog(tmp_path)
    noticed.find(conn, None)
    assert kinds(conn) == [("startrails", 10), ("timelapse", 20)]


def test_frames_that_dont_overlap_are_not_offered(shoot, tmp_path):
    for k in range(4):                                   # four different scenes in quick succession
        shoot.add(scene(seed=10 + k).crop((0, 0, 900, 700)), T0 + timedelta(seconds=3 * k))
    conn, _ = shoot.catalog(tmp_path)
    assert noticed.find(conn, None) == 0


def test_dismissals_are_remembered_and_only_new_shoots_are_looked_at(shoot, tmp_path, monkeypatch):
    big = scene()
    for k, x in enumerate((0, 550, 1100)):
        shoot.add(big.crop((x, 0, x + 900, 700)), T0 + timedelta(seconds=3 * k))
    conn, _ = shoot.catalog(tmp_path)
    noticed.find(conn, None)
    [(sid, sug)] = noticed.open_suggestions(conn)
    assert sug.text() == "3 frames look like a panorama."
    noticed.dismiss(conn, sid)
    assert noticed.open_suggestions(conn) == []
    assert noticed.find(conn, None, everything=True) == 0          # the same frames: not again
    assert noticed.open_suggestions(conn) == []
    seen = []
    monkeypatch.setattr(noticed, "detect", lambda fs, *a, **k: seen.append(fs) or [])
    noticed.find(conn, None)
    assert seen == [[]]                                            # nothing new since the last look


def test_suggestions_with_a_missing_photo_are_not_shown(shoot, tmp_path):
    big = scene()
    for k, x in enumerate((0, 550, 1100)):
        shoot.add(big.crop((x, 0, x + 900, 700)), T0 + timedelta(seconds=3 * k))
    conn, ids = shoot.catalog(tmp_path)
    noticed.find(conn, None)
    conn.execute("UPDATE files SET missing_since = datetime('now') WHERE id = ?", (ids["IMG_0001.jpg"],))
    assert noticed.open_suggestions(conn) == []


# --- on screen ---------------------------------------------------------------------------------

def _panorama_catalog(shoot, tmp_path):
    big = scene()
    for k, x in enumerate((0, 550, 1100)):
        shoot.add(big.crop((x, 0, x + 900, 700)), T0 + timedelta(seconds=3 * k))
    conn, ids = shoot.catalog(tmp_path)
    noticed.find(conn, None)
    return conn, ids


def test_the_status_page_offers_it_and_dismiss_takes_it_away(shoot, tmp_path):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from lunelis.ui.status_view import StatusView
    conn, ids = _panorama_catalog(shoot, tmp_path)
    page = StatusView(conn, ("Scanning folders",))
    try:
        page.refresh()
        page.bg.wait()
        [(sid, build_b, show_b, dismiss_b)] = page.noticed_rows
        assert build_b.text() == "Build the panorama…"
        shown, built = [], []
        page.show_ids.connect(shown.append)
        page.build.connect(lambda *a: built.append(a))
        show_b.click()
        build_b.click()
        assert shown == [[ids["IMG_0000.jpg"], ids["IMG_0001.jpg"], ids["IMG_0002.jpg"]]]
        assert built == [(sid, "panorama", shown[0])]
        dismiss_b.click()
        page.bg.wait()
        assert page.noticed_rows == []
        assert conn.execute("SELECT status FROM suggestions").fetchone()[0] == "dismissed"
    finally:
        page.deleteLater()


def test_build_it_hands_the_frames_to_the_merge_and_only_a_finished_merge_counts(shoot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from lunelis.ui import main_window as mw
    conn, ids = _panorama_catalog(shoot, tmp_path)
    monkeypatch.setattr(mw, "open_catalog", lambda _p: conn)
    w = mw.MainWindow()
    try:
        [(sid, sug)] = noticed.open_suggestions(conn)
        asked = []
        monkeypatch.setattr(w, "merge_photos", lambda kind: asked.append((kind, set(w.grid.selected))))
        w.build_noticed(sid, sug.kind, sug.file_ids)
        assert asked == [("panorama", set(sug.file_ids))]
        assert w._noticed_pending is None                         # nothing started: still on offer
        assert noticed.open_suggestions(conn)
        w._noticed_pending = sid                                  # as if the merge had started
        w._merge_thread = type("T", (), {"quit": lambda s: None, "wait": lambda s: None})()
        w._merge_progress = type("P", (), {"close": lambda s: None})()
        monkeypatch.setattr(mw, "generate_pending", lambda *a, **k: None, raising=False)
        w._merge_done(None, str(tmp_path / "elsewhere.tif"), "cancelled")
        assert noticed.open_suggestions(conn)                     # cancelled: still on offer
        w._noticed_pending = sid
        w._merge_thread = type("T", (), {"quit": lambda s: None, "wait": lambda s: None})()
        opened = []
        monkeypatch.setattr(mw.os, "startfile", lambda p: opened.append(p), raising=False)
        monkeypatch.setattr(mw.QMessageBox, "exec", lambda self: 0)
        w._merge_done(None, str(tmp_path / "elsewhere.tif"), "")
        assert noticed.open_suggestions(conn) == []
        assert conn.execute("SELECT status FROM suggestions").fetchone()[0] == "built"
    finally:
        w._quitting = True
        w.close()


def test_a_timelapse_opens_the_create_tool_with_its_frames(shoot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from lunelis.ui import main_window as mw
    conn, _ = shoot.catalog(tmp_path)
    monkeypatch.setattr(mw, "open_catalog", lambda _p: conn)
    w = mw.MainWindow()
    try:
        cur = conn.execute("INSERT INTO suggestions (key, kind, file_ids) VALUES ('k', 'timelapse', '[]')")
        conn.commit()
        opened = []
        monkeypatch.setattr(w.create_page, "open_tool", lambda key: opened.append(key))
        w.build_noticed(cur.lastrowid, "timelapse", [])
        assert opened == ["timelapse"] and w.pages.currentWidget() is w.create_page
        assert conn.execute("SELECT status FROM suggestions").fetchone()[0] == "open"     # nothing made yet
        w.create_page.tools["timelapse"].made.emit("C:/x/Timelapse.mp4")
        assert conn.execute("SELECT status FROM suggestions").fetchone()[0] == "built"
    finally:
        w._quitting = True
        w.close()


def test_the_scan_looks_only_when_turned_on(shoot, tmp_path, monkeypatch):
    from lunelis.settings import Settings
    from lunelis.ui import main_window as mw
    conn, _ = shoot.catalog(tmp_path)
    calls = []
    monkeypatch.setattr(noticed, "find", lambda c, thumbs, stop: calls.append(thumbs) or 2)
    worker = mw.LibraryWorker([])
    got = []
    worker.noticed_done.connect(got.append)
    worker._noticed(conn, lambda: False)
    assert len(calls) == 1 and got == [2]
    Settings(conn).set("noticed_auto", False)
    worker._noticed(conn, lambda: False)
    assert len(calls) == 1
