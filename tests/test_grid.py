"""Step 5: the library grid's index, layout arithmetic, hit-testing, selection and loader."""
import io
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PIL import Image  # noqa: E402
from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lunelis.catalog.schema import open_catalog  # noqa: E402
from lunelis.importers.metadata import extract_pending  # noqa: E402
from lunelis.importers.scan import add_root, scan_root  # noqa: E402
from lunelis.ui.grid import GAP, PAD_X, PAD_Y, PhotoGrid  # noqa: E402
from lunelis.ui.library import LibraryIndex  # noqa: E402
from lunelis.ui.thumbcache import ThumbCache, load_square  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _jpeg(path, size=(64, 48)):
    Image.new("RGB", size, (120, 90, 60)).save(path, "JPEG")


@pytest.fixture
def catalog(tmp_path):
    root = tmp_path / "Photos"
    root.mkdir()
    for name in ("b.jpg", "a.jpg", "c.jpg"):
        _jpeg(root / name)
    (root / "c.jpg").write_bytes((root / "c.jpg").read_bytes() * 3)   # biggest file
    conn = open_catalog(tmp_path / "lunelis.db")
    root_id = add_root(conn, root)
    scan_root(conn, root_id)
    extract_pending(conn)
    yield conn, root, root_id
    conn.close()


def _names(conn, idx):
    by_id = dict(conn.execute("SELECT id, filename FROM files"))
    return [by_id[r[0]] for r in idx.rows]


def test_index_sorts(catalog):
    conn, _, _ = catalog
    idx = LibraryIndex()
    idx.load(conn, "name")
    assert _names(conn, idx) == ["a.jpg", "b.jpg", "c.jpg"]
    idx.load(conn, "size")
    assert _names(conn, idx)[0] == "c.jpg"


def test_index_hides_missing_files_and_disabled_roots(catalog):
    conn, root, root_id = catalog
    (root / "a.jpg").unlink()
    scan_root(conn, root_id)
    idx = LibraryIndex()
    idx.load(conn)
    assert sorted(_names(conn, idx)) == ["b.jpg", "c.jpg"]
    conn.execute("UPDATE roots SET enabled = 0")
    idx.load(conn)
    assert len(idx) == 0


class _FakeIndex(LibraryIndex):
    def __init__(self, n):
        super().__init__()
        self.rows = [(i + 1, None, 0, "jpg", "jpeg", 0, 0, None) for i in range(n)]


@pytest.fixture
def grid(app, tmp_path):
    g = PhotoGrid(ThumbCache(tmp_path))
    g.resize(1200 + 2 * PAD_X, 800)
    g.show()
    g.set_target_tile(180)
    g.set_index(_FakeIndex(100))
    yield g
    g.close()


def test_layout_fills_width(grid):
    avail = grid.viewport().width() - 2 * PAD_X
    assert grid.cols * grid.tile + (grid.cols - 1) * GAP <= avail
    assert avail - (grid.cols * grid.tile + (grid.cols - 1) * GAP) < grid.cols   # no big right margin
    rows = -(-100 // grid.cols)
    content = 2 * PAD_Y + rows * grid.tile + (rows - 1) * GAP
    assert grid.verticalScrollBar().maximum() == content - grid.viewport().height()


def test_hit_testing_ignores_gaps(grid):
    x = PAD_X + grid.tile + GAP + 5                 # inside the second tile
    assert grid.position_at(QPoint(x, PAD_Y + 5)) == 1
    assert grid.position_at(QPoint(PAD_X + grid.tile + GAP // 2, PAD_Y + 5)) == -1   # the gap
    assert grid.position_at(QPoint(5, PAD_Y + 5)) == -1                              # left padding


def test_selection_click_ctrl_shift(grid):
    none, ctrl, shift = (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ControlModifier,
                         Qt.KeyboardModifier.ShiftModifier)
    grid._set_current(2, none)
    assert grid.selected == {3}
    grid._set_current(5, ctrl)
    assert grid.selected == {3, 6}
    grid._set_current(8, shift)                     # range from the anchor (5) to 8
    assert grid.selected == {6, 7, 8, 9}
    grid._set_current(5, ctrl)                      # ctrl toggles off
    assert 6 not in grid.selected


def test_selection_survives_resort(grid):
    grid._set_current(10, Qt.KeyboardModifier.NoModifier)
    fid = grid.index.file_id(10)
    grid.index.rows.reverse()
    grid.set_index(grid.index)
    assert grid.selected == {fid}


def test_keyboard_navigation(grid):
    grid._set_current(0, Qt.KeyboardModifier.NoModifier)
    grid.keyPressEvent(_key(Qt.Key.Key_Down))
    assert grid.current == grid.cols
    grid.keyPressEvent(_key(Qt.Key.Key_End))
    assert grid.current == 99
    grid.keyPressEvent(_key(Qt.Key.Key_Right))      # clamps at the end
    assert grid.current == 99


def _key(k):
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent
    return QKeyEvent(QEvent.Type.KeyPress, k, Qt.KeyboardModifier.NoModifier)


def test_resize_changes_columns(grid):
    before = grid.cols
    grid.resize(grid.width() // 2, grid.height())
    assert grid.cols < before


def test_load_square_cover_crops(tmp_path):
    p = tmp_path / "wide.jpg"
    img = Image.new("RGB", (400, 200), (255, 0, 0))
    img.paste((0, 0, 255), (100, 0, 300, 200))       # blue middle band
    img.save(p, "JPEG", quality=95)
    q = load_square(p, 64)
    assert (q.width(), q.height()) == (64, 64)
    assert q.pixelColor(32, 32).blue() > 200          # centre crop kept the middle
    assert q.pixelColor(2, 32).blue() > 200           # ...and cut off the red edges


def test_load_square_bad_file_is_null(tmp_path):
    p = tmp_path / "bad.jpg"
    p.write_bytes(b"nope")
    assert load_square(p, 64).isNull()


def test_filters_and_in_place_rating_refresh(catalog):
    from lunelis.catalog.ratings import set_ratings
    from lunelis.ui.library import UNRATED, Filter
    conn, _, _ = catalog
    ids = dict(conn.execute("SELECT filename, id FROM files"))
    set_ratings(conn, [ids["a.jpg"]], stars=4, label="Green")
    set_ratings(conn, [ids["b.jpg"]], stars=2, flag="reject")
    idx = LibraryIndex()
    idx.load(conn, "name", Filter(min_stars=3))
    assert _names(conn, idx) == ["a.jpg"]
    idx.load(conn, "name", Filter(min_stars=UNRATED))
    assert _names(conn, idx) == ["c.jpg"]
    idx.load(conn, "name", Filter(flag="reject"))
    assert _names(conn, idx) == ["b.jpg"]
    idx.load(conn, "name", Filter(label="Green", min_stars=1))
    assert _names(conn, idx) == ["a.jpg"]

    idx.load(conn, "name", Filter())
    set_ratings(conn, [ids["c.jpg"]], stars=5, label="Blue")
    idx.refresh_ratings(conn, [ids["c.jpg"]])
    tile = idx.tile(2)
    assert (tile.stars, tile.label) == (5, "Blue")
