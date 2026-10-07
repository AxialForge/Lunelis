"""0.26: offline sources stay browsable and are marked, a vanished drive is
explained, each photo shows whether it's backed up, and regular file checks
re-read the library a little at a time."""
import errno
import os
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PIL import Image  # noqa: E402

from lunelis import reach  # noqa: E402
from lunelis.catalog.schema import open_catalog  # noqa: E402
from lunelis.importers.scan import add_root, scan_root  # noqa: E402


@pytest.fixture
def lib(tmp_path):
    root = tmp_path / "Photos"
    (root / "2024").mkdir(parents=True)
    for i in range(4):
        Image.new("RGB", (40, 30), (50 * i, 80, 120)).save(root / "2024" / f"P{i}.jpg")
    conn = open_catalog(tmp_path / "cat.db")
    rid = add_root(conn, root)
    scan_root(conn, rid)
    ids = dict(conn.execute("SELECT filename, id FROM files").fetchall())
    yield conn, ids, root, rid
    conn.close()


# --- reachable / explained ----------------------------------------------------------------------

def test_reachable(tmp_path):
    assert reach.reachable(str(tmp_path))
    assert not reach.reachable(str(tmp_path / "gone"))


def test_a_vanished_drive_is_explained_in_words(tmp_path):
    from lunelis.dupes.hashing import SourceOffline
    text = reach.explain(SourceOffline(errno.EIO, "gone"), "exporting", r"\\nas\photos\2024\a.jpg")
    assert text.startswith(r"\\nas\photos stopped answering while exporting") and "Nothing was changed" in text
    gone = tmp_path / "Z" / "x.jpg"
    assert reach.gone_offline(FileNotFoundError(2, "no"), str(gone)) is False      # its drive is there
    plain = reach.explain(ValueError("bad"), "exporting")
    assert plain == "Couldn't finish exporting: bad"


def test_share_and_drive_roots():
    assert reach._root_of(r"\\server\share\a\b.jpg") == r"\\server\share"
    assert reach._root_of(r"D:\Photos\a.jpg") == "D:\\"


# --- offline marks -------------------------------------------------------------------------------

def test_offline_tiles_and_the_photo_view_say_so(lib, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from lunelis.ui import main_window as mw
    conn, ids, root, rid = lib
    monkeypatch.setattr(mw, "open_catalog", lambda _p: conn)
    w = mw.MainWindow()
    try:
        monkeypatch.setattr(reach, "reachable", lambda p, timeout=4.0: False)
        w.check_sources()
        w._reach_bg.wait()
        assert w.offline_roots == {rid: str(root)} and w.grid.offline_roots == {rid}
        assert "isn't answering" in w.status.text()
        w.grid.resize(600, 400)
        w.grid.grab()                                            # paints the OFFLINE pills without error
        w.open_detail(ids["P1.jpg"])
        assert "isn't answering" in w.detail.canvas.message
        monkeypatch.setattr(reach, "reachable", lambda p, timeout=4.0: True)
        w.check_sources()
        w._reach_bg.wait()
        assert w.offline_roots == {} and "is back" in w.status.text()
    finally:
        w._quitting = True
        w.close()


# --- protection ----------------------------------------------------------------------------------

def _backup(conn, fid, name="NAS backup", problem=None, verified=None, same_mtime=True):
    sid = conn.execute("SELECT id FROM backup_sets WHERE name = ?", (name,)).fetchone()
    sid = sid[0] if sid else conn.execute(
        "INSERT INTO backup_sets (name, dest_path, sources) VALUES (?, 'E:\\\\B', '[]')", (name,)).lastrowid
    mtime = conn.execute("SELECT mtime FROM files WHERE id = ?", (fid,)).fetchone()[0]
    conn.execute("INSERT INTO backup_files (set_id, file_id, rel, size, mtime, sha256, verified_at, problem)"
                 " VALUES (?, ?, 'x', 1, ?, 'h', ?, ?)",
                 (sid, fid, mtime if same_mtime else "1999-01-01", verified, problem))
    conn.commit()


def test_protection_per_photo(lib):
    from lunelis.backups import protection as P
    conn, ids, *_ = lib
    assert P.of(conn, ids["P0.jpg"]).text() == "Not backed up"
    _backup(conn, ids["P0.jpg"], verified="2026-10-03T10:00:00")
    _backup(conn, ids["P0.jpg"], name="USB", same_mtime=False)
    p = P.of(conn, ids["P0.jpg"])
    assert p.protected and p.copies == 2 and p.current == 1
    assert p.text() == "Backed up · 2 copies (NAS backup, USB) · changed since the last backup · checked Oct 3, 2026"
    _backup(conn, ids["P1.jpg"], problem="hash mismatch")
    assert P.of(conn, ids["P1.jpg"]).text() == "Not backed up - 1 backup copy is damaged"
    assert P.unprotected_count(conn) == 3


def test_the_backup_filter_and_info_line(lib):
    from lunelis.ui import photoinfo
    from lunelis.ui.library import Filter, LibraryIndex
    conn, ids, *_ = lib
    _backup(conn, ids["P2.jpg"])
    idx = LibraryIndex()
    idx.load(conn, "name", Filter(backup="ok"))
    assert [idx.file_id(i) for i in range(len(idx))] == [ids["P2.jpg"]]
    idx.load(conn, "name", Filter(backup="none"))
    assert len(idx) == 3
    assert photoinfo.load(conn, ids["P2.jpg"]).protection.startswith("Backed up · 1 copy")


# --- regular file checks -------------------------------------------------------------------------

def test_the_rolling_check_reads_the_oldest_checked_first_and_catches_damage(lib):
    from lunelis.jobs import engine, rolling
    from lunelis.settings import Settings
    conn, ids, root, rid = lib
    s = Settings(conn)
    now = datetime(2026, 10, 4, 12)
    assert rolling.due(conn, now)
    size = conn.execute("SELECT size_bytes FROM files WHERE id = ?", (ids["P0.jpg"],)).fetchone()[0]
    s.set("integrity_gb", 1)
    first = rolling.plan(conn, size * 2 + 1)
    assert [r[0] for r in first] == sorted(ids.values())[:2]               # never checked, then by id
    job = rolling.start(conn, now)
    assert not rolling.due(conn, now)                                      # one at a time
    engine.run_job(conn, job, idle=lambda: 10_000)
    hashed = dict(conn.execute("SELECT id, content_hash FROM files WHERE checked_at IS NOT NULL").fetchall())
    assert set(hashed) == set(ids.values()) and all(hashed.values())       # 1 GB: all four, baselines made
    assert not rolling.due(conn, now + timedelta(days=3))
    assert rolling.due(conn, now + timedelta(days=8))
    # Next week: a file whose bytes changed while its date didn't is flagged.
    p = root / "2024" / "P3.jpg"
    st = p.stat()
    data = bytearray(p.read_bytes())
    data[-3] ^= 0xFF
    p.write_bytes(bytes(data))
    os.utime(p, (st.st_atime, st.st_mtime))
    engine.run_job(conn, rolling.start(conn, now + timedelta(days=8)), idle=lambda: 10_000)
    problem = conn.execute("SELECT problem FROM damaged WHERE file_id = ?", (ids["P3.jpg"],)).fetchone()
    assert tuple(problem) == ("changed_on_disk",)
    assert rolling.summary(conn)[:2] == (4, 4)


def test_regular_checks_can_be_turned_off(lib):
    from lunelis.jobs import rolling
    from lunelis.settings import Settings
    conn, *_ = lib
    Settings(conn).set("integrity_every", "off")
    assert not rolling.due(conn)


# --- map -----------------------------------------------------------------------------------------

def test_mercator_round_trip_and_clusters():
    from lunelis.ui import map_view as M
    x, y = M.lonlat_to_world(-81.69, 41.50, 10)
    lon, lat = M.world_to_lonlat(x, y, 10)
    assert lon == pytest.approx(-81.69) and lat == pytest.approx(41.50)
    pts = [(1, 41.50, -81.69), (2, 41.5001, -81.6901), (3, 36.11, -115.17)]
    groups = sorted(len(ids) for _, _, ids in M.clusters(pts, 4))
    assert groups == [1, 2]                                        # Cleveland pair, Las Vegas alone
    assert sorted(len(ids) for _, _, ids in M.clusters(pts, 18)) == [1, 1, 1]


def _located(conn, ids):
    for name, (lat, lon) in {"P0.jpg": (41.5, -81.69), "P1.jpg": (41.5001, -81.6901), "P2.jpg": (0, 0)}.items():
        conn.execute("INSERT OR REPLACE INTO exif (file_id, gps_lat, gps_lon) VALUES (?, ?, ?)", (ids[name], lat, lon))
    conn.commit()


def test_the_map_makes_no_network_request_until_turned_on(lib, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from lunelis.settings import Settings
    from lunelis.ui import map_view as M
    conn, ids, *_ = lib
    _located(conn, ids)
    view = M.MapView(conn)
    try:
        view.canvas.tiles_dir = tmp_path / "tiles"
        view.resize(800, 600)
        view.refresh()
        view.bg.wait()
        assert view.count.text() == "2 photos with a location"          # 0,0 is "no fix"
        view.canvas.grab()
        assert view.canvas.net is None and not view.canvas._asked
        picked = []
        view.show_ids.connect(picked.append)
        x, y, members = view.canvas._clusters[0]
        ox, oy = view.canvas._origin()
        from PySide6.QtCore import QPointF
        assert sorted(view.canvas.cluster_at(QPointF(x - ox, y - oy))) == sorted(members)
        view.canvas.picked.emit(members)
        assert picked == [members]
        # Turned on: tiles are asked for, but a cached one is used without asking.
        z = int(view.canvas.z)
        tx, ty = int(x // M.TILE) % (2 ** z), int(y // M.TILE)
        tile = view.canvas._tile_path(z, tx, ty)
        tile.parent.mkdir(parents=True)
        from PySide6.QtGui import QImage
        img = QImage(256, 256, QImage.Format.Format_RGB32)
        img.fill(0x88aa88)
        img.save(str(tile))
        asked = []
        view._toggle_online()
        assert Settings(conn).get("map_online") is True and view.canvas.net is not None
        monkeypatch.setattr(view.canvas.net, "get", lambda req: asked.append(req.url().toString()))
        view.canvas.grab()
        assert asked and all("tile.openstreetmap.org" in u for u in asked)
        assert not any(u.endswith(f"/{z}/{tx}/{ty}.png") for u in asked)  # cached
    finally:
        view.deleteLater()


# --- on this day ---------------------------------------------------------------------------------

def test_on_this_day_groups_earlier_years(lib):
    from datetime import date
    from lunelis.ui.calendar_view import day_keys, on_this_day
    conn, ids, *_ = lib
    for name, when in {"P0.jpg": "2019-10-04T09:00:00", "P1.jpg": "2023-10-04T23:30:00",
                       "P2.jpg": "2023-10-06T08:00:00", "P3.jpg": "2024-03-01T12:00:00"}.items():
        conn.execute("INSERT OR REPLACE INTO exif (file_id, captured_at) VALUES (?, ?)", (ids[name], when))
    conn.commit()
    assert on_this_day(conn, date(2026, 10, 4)) == [(2023, [ids["P1.jpg"]]), (2019, [ids["P0.jpg"]])]
    assert on_this_day(conn, date(2026, 10, 4), spread=3)[0] == (2023, [ids["P1.jpg"], ids["P2.jpg"]])
    assert day_keys(date(2028, 3, 1), 1) == ["02-29", "03-01", "03-02"]


def test_the_on_this_day_page(lib):
    from datetime import date
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from lunelis.ui.calendar_view import CalendarView
    conn, ids, *_ = lib
    conn.execute("INSERT OR REPLACE INTO exif (file_id, captured_at) VALUES (?, '2021-07-04T10:00:00')", (ids["P2.jpg"],))
    conn.commit()
    view = CalendarView(conn)
    try:
        shown = []
        view.show_ids.connect(lambda ids_, name: shown.append((ids_, name)))
        view.set_day(date(2026, 7, 4))
        view.bg.wait()
        [(year, got, show_b)] = view.year_rows
        assert year == 2021 and got == [ids["P2.jpg"]]
        show_b.click()
        assert shown == [([ids["P2.jpg"]], "July 4, 2021")]
        # 0.38: the strip's photos select and open, and can be shown in their folder.
        from PySide6.QtCore import Qt
        opened, located = [], []
        view.open_photo.connect(opened.append)
        view.show_in_library.connect(located.append)
        [tile] = view.tiles
        view._click(tile.fid, Qt.KeyboardModifier.NoModifier)
        assert view.selected == {ids["P2.jpg"]}
        tile.mouseDoubleClickEvent(None)
        assert opened == [ids["P2.jpg"]]
        view.set_day(date(2026, 7, 5))
        view.bg.wait()
        assert view.year_rows == [] and view.summary.text() == ""
    finally:
        view.deleteLater()
