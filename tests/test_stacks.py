"""Burst stacks: detection, what survives a rebuild, and the collapsed grid."""
import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from lunelis import stacks
from lunelis.catalog.schema import open_catalog
from lunelis.importers.scan import add_root, scan_root
from lunelis.ui.library import Filter, LibraryIndex

# name -> (capture time, camera). Folder "Burst": four frames 0.2 s apart,
# then a lone shot 5 s later. "Pairs": two RAW+JPEG shots (2 shots, no burst).
# "Phone": three frames in one second, no sub-seconds; then two frames a
# second apart each (an interval timer, not a burst).
SHOTS = {
    "Burst/A001.jpg": ("2024-06-18T13:43:06.100", "ILCE-7RM5"),
    "Burst/A002.jpg": ("2024-06-18T13:43:06.300", "ILCE-7RM5"),
    "Burst/A003.jpg": ("2024-06-18T13:43:06.500", "ILCE-7RM5"),
    "Burst/A004.jpg": ("2024-06-18T13:43:06.700", "ILCE-7RM5"),
    "Burst/A005.jpg": ("2024-06-18T13:43:11.700", "ILCE-7RM5"),
    "Pairs/B001.jpg": ("2024-06-18T14:00:00.100", "ILCE-7RM5"),
    "Pairs/B001.arw": ("2024-06-18T14:00:00.100", "ILCE-7RM5"),
    "Pairs/B002.jpg": ("2024-06-18T14:00:00.400", "ILCE-7RM5"),
    "Pairs/B002.arw": ("2024-06-18T14:00:00.400", "ILCE-7RM5"),
    "Phone/P1.jpg": ("2024-07-01T09:00:00", "SM-G998U"),
    "Phone/P2.jpg": ("2024-07-01T09:00:00", "SM-G998U"),
    "Phone/P3.jpg": ("2024-07-01T09:00:00", "SM-G998U"),
    "Phone/P4.jpg": ("2024-07-01T09:00:05", "SM-G998U"),
    "Phone/P5.jpg": ("2024-07-01T09:00:06", "SM-G998U"),
}


@pytest.fixture
def lib(tmp_path):
    root = tmp_path / "Photos"
    for rel in SHOTS:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        if rel.endswith(".jpg"):
            Image.new("RGB", (60, 40), (len(rel) * 9 % 255, 80, 120)).save(root / rel)
        else:
            (root / rel).write_bytes(b"not really a raw" * 10)
    conn = open_catalog(tmp_path / "cat.db")
    scan_root(conn, add_root(conn, root))
    for fid, rel in conn.execute("SELECT id, rel_path FROM files").fetchall():
        taken, camera = SHOTS[rel]
        conn.execute("INSERT OR REPLACE INTO exif (file_id, captured_at, camera_model) VALUES (?, ?, ?)",
                     (fid, taken, camera))
    conn.commit()
    yield conn
    conn.close()


def ids(conn, *rels):
    return [conn.execute("SELECT id FROM files WHERE rel_path = ?", (r,)).fetchone()[0] for r in rels]


def test_detects_bursts_only(lib):
    runs = stacks.detect(lib)
    assert sorted(map(sorted, runs)) == sorted([
        sorted(ids(lib, "Burst/A001.jpg", "Burst/A002.jpg", "Burst/A003.jpg", "Burst/A004.jpg")),
        sorted(ids(lib, "Phone/P1.jpg", "Phone/P2.jpg", "Phone/P3.jpg")),
    ])                                   # not the RAW+JPEG pairs, not the 1-second interval frames


def test_rebuild_keeps_choices(lib):
    assert stacks.rebuild(lib) == 2
    a1, a2, a3, a4, a5 = ids(lib, *(f"Burst/A00{i}.jpg" for i in range(1, 6)))
    sid = stacks.stack_of(lib, a1)
    assert stacks.members(lib, sid) == [a1, a2, a3, a4]
    assert lib.execute("SELECT cover_file_id FROM stacks WHERE id = ?", (sid,)).fetchone()[0] == a1
    assert stacks.set_cover(lib, a3)
    stacks.rebuild(lib)
    assert stacks.stack_of(lib, a1) == sid                               # unchanged: same stack
    # A fifth frame joins the burst: a new stack, but the chosen cover stays.
    lib.execute("UPDATE exif SET captured_at = '2024-06-18T13:43:06.900' WHERE file_id = ?", (a5,))
    stacks.rebuild(lib)
    sid2 = stacks.stack_of(lib, a5)
    assert stacks.members(lib, sid2) == [a1, a2, a3, a4, a5]
    assert lib.execute("SELECT cover_file_id FROM stacks WHERE id = ?", (sid2,)).fetchone()[0] == a3
    # Unstacked for good.
    assert stacks.unstack(lib, sid2) == 5
    assert stacks.rebuild(lib) == 1 and stacks.stack_of(lib, a1) is None


def test_best_rated_frame_is_the_cover(lib):
    a2 = ids(lib, "Burst/A002.jpg")[0]
    lib.execute("INSERT INTO ratings (file_id, stars) VALUES (?, 4)", (a2,))
    stacks.rebuild(lib)
    assert lib.execute("SELECT cover_file_id FROM stacks WHERE id = ?",
                       (stacks.stack_of(lib, a2),)).fetchone()[0] == a2


def test_grid_collapses_and_opens_stacks(lib):
    stacks.rebuild(lib)
    a1, a2, a3 = ids(lib, "Burst/A001.jpg", "Burst/A002.jpg", "Burst/A003.jpg")
    idx = LibraryIndex()
    idx.load(lib)
    assert len(idx.all_rows) == 14 and len(idx) == 14 - 3 - 2          # 3 + 2 frames under covers
    cover = idx.tile(idx.position(a1))
    assert cover.stack_size == 4 and idx.position(a2) == -1
    assert idx.toggle_stack(idx.stack_id(idx.position(a1)))
    assert len(idx) == 12 and idx.tile(idx.position(a2)).stack_open and idx.tile(idx.position(a1)).stack_size == 0
    idx.toggle_stack(idx.stack_id(idx.position(a1)))
    assert len(idx) == 9
    # A filter that leaves the cover out shows the stack's first frame still in.
    lib.execute("INSERT INTO ratings (file_id, stars) VALUES (?, 5)", (a3,))
    idx.load(lib, filt=Filter(min_stars=5))
    assert [r[0] for r in idx.rows] == [a3] and idx.tile(0).stack_size == 4
    idx.set_collapse(False)
    idx.load(lib, filt=Filter())
    assert len(idx) == 14


def test_window_opens_a_stack_on_double_click(lib, monkeypatch):
    QApplication.instance() or QApplication([])
    from lunelis.ui import main_window as mw
    stacks.rebuild(lib)
    monkeypatch.setattr(mw, "open_catalog", lambda _p: lib)
    w = mw.MainWindow()
    try:
        a1, a2 = ids(lib, "Burst/A001.jpg", "Burst/A002.jpg")
        assert w.index.position(a2) == -1
        w.open_detail(a1)
        assert w.index.position(a2) >= 0 and w.pages.currentWidget() is w.detail
        w.close_detail()
        w.toggle_stack()                                                  # S closes it again
        assert w.index.position(a2) == -1 and w.grid.selected == {a1}
        w.stack_cb.setChecked(False)
        assert len(w.index) == 14
    finally:
        w._quitting = True
        w.close()
