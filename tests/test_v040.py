"""v0.4.0: photo info formatting, the detail view, the hover card, the smooth
size slider and the timeline scrubber."""
import os
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PIL import Image  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lunelis.catalog.schema import open_catalog  # noqa: E402
from lunelis.importers.scan import add_root, scan_root  # noqa: E402
from lunelis.ui import photoinfo  # noqa: E402
from lunelis.ui.library import Filter, LibraryIndex  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def lib(tmp_path):
    root = tmp_path / "Photos"
    root.mkdir()
    conn = open_catalog(tmp_path / "cat.db")
    for i in range(6):
        Image.effect_noise((120, 80), 40 + i).convert("RGB").save(root / f"IMG_{i:04d}.jpg", "JPEG")
    rid = add_root(conn, root)
    scan_root(conn, rid)
    base = datetime(2024, 6, 19, 14, 3)
    for n, (fid,) in enumerate(conn.execute("SELECT id FROM files ORDER BY filename").fetchall()):
        taken = base - timedelta(days=40 * n)                      # spread over several months
        conn.execute("INSERT OR REPLACE INTO exif (file_id, captured_at, camera_make, camera_model, lens,"
                     " focal_length_mm, aperture, shutter_speed, iso, width_px, height_px, orientation)"
                     " VALUES (?, ?, 'SONY', 'ILCE-7RM5', 'FE 24-70mm F2.8 GM II', 50, 2.8, '1/500', 400,"
                     " 9504, 6336, 1)", (fid, taken.isoformat()))
    conn.commit()
    idx = LibraryIndex()
    idx.load(conn, "date_desc", Filter())
    yield conn, idx
    conn.close()


def test_photo_info_reads_like_a_person_wrote_it(lib):
    conn, idx = lib
    info = photoinfo.load(conn, idx.file_id(0))
    assert info.camera() == "Sony α7R V"                            # model code -> name, SONY -> Sony
    assert info.exposure() == "50mm  f/2.8  1/500s  ISO 400"
    assert info.dimensions() == "9,504 × 6,336  (60.2 MP)"
    assert info.when() == "Jun 19, 2024 · 2:03 PM"
    broken = photoinfo.breakable(r"\\nas\share\a.ARW")
    assert broken.startswith("\\\\nas") and broken.count("\u200b") == 2


def test_index_knows_where_a_photo_is(lib):
    conn, idx = lib
    assert idx.position(idx.file_id(3)) == 3 and idx.position(999999) == -1


def test_detail_view_steps_through_the_library_and_rates(app, lib):
    from lunelis.ui.detail_view import DetailView
    conn, idx = lib
    view = DetailView(conn)
    moved, rated = [], []
    view.current_changed.connect(moved.append)
    view.rate.connect(rated.append)
    view.open(idx, 2)
    assert view.counter.text() == "3 of 6" and view.panel.title.text() == view.info.filename
    view.go(view.pos + 1)
    view.go(99)                                                      # clamps to the last photo
    assert view.pos == 5 and moved[-1] == idx.file_id(5)
    assert not view.next_b.isEnabled()
    view.panel.star_buttons[3].click()                               # 4 stars
    view.panel.label_buttons["Green"].click()
    view.panel.reject_b.click()
    assert rated == [{"stars": 4}, {"label": "Green"}, {"flag": "reject"}]
    view.strip.picked.emit(0)
    assert view.pos == 0


def test_main_window_opens_and_closes_the_detail_view(app, lib, monkeypatch):
    from lunelis.ui import main_window as mw
    conn, idx = lib
    monkeypatch.setattr(mw, "open_catalog", lambda _p: conn)
    w = mw.MainWindow()
    try:
        fid = w.index.file_id(1)
        w.open_detail(fid)
        assert w.pages.currentWidget() is w.detail and w.grid.selected == {fid}
        w.rate(stars=3)                                              # the keys rate the photo on screen
        assert conn.execute("SELECT stars FROM ratings WHERE file_id = ?", (fid,)).fetchone()[0] == 3
        assert "★★★" in w.detail.stars.text()
        w.close_detail()
        assert w.pages.currentWidget() is w.grid and w.grid.current == w.index.position(fid)
    finally:
        w._quitting = True
        w.close()


def test_timeline_marks_months_and_snaps(app, lib):
    from lunelis.ui.timeline import TimelineScrubber
    conn, idx = lib
    s = TimelineScrubber()
    s.resize(64, 600)
    s.set_rows(idx.rows)
    assert [(y, m) for _, y, m in s.marks][:2] == [(2024, 6), (2024, 5)]
    assert len(s.marks) == 6                                         # each photo in its own month
    jumped = []
    s.jump.connect(jumped.append)
    s._jump(int(s._y(3) + 3))                                        # just below a month's start
    assert jumped == [3]
    assert s.label(s.marks[0]) == "June 2024"


def test_hover_card_and_smooth_resizing(app, lib):
    from lunelis.ui.grid import PhotoGrid
    from lunelis.ui.thumbcache import ThumbCache
    conn, idx = lib
    from pathlib import Path
    g = PhotoGrid(ThumbCache(Path("."), None))
    g.resize(900, 600)
    g.set_index(idx)
    g.info_provider = lambda fid: photoinfo.load(conn, fid)
    g._hover_i = 0
    g._show_card()
    assert "Sony α7R V" in g.card.lines.text()
    before = g.thumbs.px
    g.set_target_tile(300)                                           # like dragging the slider
    assert g.thumbs.px == before and g._decode_timer.isActive()      # scaled now, decoded when it settles
    g._apply_decode_size()
    assert g.thumbs.px == g._decode_px


def test_tilting_the_wheel_steps_through_photos(app, lib):
    """Wheel tilt left / right = previous / next photo, once per tilt even
    though a held tilt sends a stream of events; the plain wheel still zooms."""
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from lunelis.ui.detail_view import DetailView
    conn, idx = lib
    view = DetailView(conn)
    view.resize(900, 600)
    view.open(idx, 2)
    c = view.canvas

    def wheel(dx, dy):
        ev = QWheelEvent(QPointF(100, 100), QPointF(100, 100), QPoint(0, 0), QPoint(dx, dy),
                         Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        c.wheelEvent(ev)

    wheel(-120, 0)                                   # tilt right: next
    assert view.pos == 3
    wheel(-120, 0)                                   # the same held tilt: no second step yet
    assert view.pos == 3
    c._last_tilt = 0.0                               # a moment later
    wheel(120, 0)                                    # tilt left: previous
    assert view.pos == 2
    if c.pix is not None:                            # the vertical wheel still zooms
        wheel(0, 120)
        assert c.zoomed and view.pos == 2
