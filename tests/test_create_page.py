"""The Create page: the picker, and each tool making a real file on a worker."""
import time

import pytest
from PIL import Image
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from lunelis.importers.scan import add_root, scan_root
from lunelis.settings import Settings


def pump(until, timeout=30.0):
    t = time.time()
    while time.time() - t < timeout:
        QApplication.processEvents()
        if until():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def window(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    from lunelis.ui.main_window import MainWindow
    root = tmp_path / "Photos"
    root.mkdir()
    for i in range(5):
        Image.effect_noise((240, 160), 30 + 10 * i).convert("RGB").save(root / f"IMG_{i:04d}.jpg", "JPEG")
    w = MainWindow()
    rid = add_root(w.conn, root)
    scan_root(w.conn, rid)
    Settings(w.conn).set("create_output_dir", str(tmp_path / "made"))
    w.reload()
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: pytest.fail(f"warning: {a[2]}")))
    ids = [r[0] for r in w.conn.execute("SELECT id FROM files WHERE root_id = ? ORDER BY filename", (rid,))]
    yield w, ids, tmp_path / "made"
    w._quitting = True
    w.close()


def test_create_home_lists_the_tools_and_the_folder(window):
    w, ids, made = window
    w.open_page("Create")
    page = w.create_page
    assert {"animation", "collage", "batch"} <= set(page.cards)
    assert str(made) in page.where.text()


def test_picker_takes_the_selection_reorders_and_leaves_out(window):
    w, ids, made = window
    w.grid.selected = set(ids[:4])
    w.open_page("Create")
    w.create_page.open_tool("animation")
    picker = w.create_page.tools["animation"].picker
    assert picker.source.currentData() == "selection" and sorted(picker.ids()) == sorted(ids[:4])
    picker.strip.item(1).setCheckState(Qt.CheckState.Unchecked)
    assert len(picker.ids()) == 3
    picker._folder = (w.conn.execute("SELECT root_id FROM files WHERE id = ?", (ids[0],)).fetchone()[0], "")
    picker.source.setCurrentIndex(picker.source.findData("folder"))
    picker.load()
    assert sorted(picker.ids()) == sorted(ids)


def test_animation_makes_a_gif(window):
    w, ids, made = window
    w.grid.selected = set(ids[:3])
    w.open_page("Create")
    w.create_page.open_tool("animation")
    tool = w.create_page.tools["animation"]
    tool.kind.setCurrentIndex(tool.kind.findData("gif"))
    tool.make()
    assert pump(lambda: tool._thread is None and "Made" in tool.result.text())
    gifs = list(made.glob("Animation *.gif"))
    assert len(gifs) == 1
    with Image.open(gifs[0]) as im:
        assert im.n_frames == 3


def test_collage_swaps_on_drag_and_saves(window):
    w, ids, made = window
    w.grid.selected = set(ids[:4])
    w.open_page("Create")
    w.create_page.open_tool("collage")
    tool = w.create_page.tools["collage"]
    tool.resize(1000, 700)
    tool.show()
    tool.canvas.resize(500, 500)
    tool.canvas.rebuild()
    before = [c.file_id for c in tool.opts.cells]
    a, b = tool.canvas._boxes[0].center(), tool.canvas._boxes[3].center()

    class Ev:
        def __init__(self, p):
            self._p = p

        def position(self):
            return QPointF(self._p)
    tool.canvas.mousePressEvent(Ev(a))
    tool.canvas.mouseMoveEvent(Ev(b))
    tool.canvas.mouseReleaseEvent(Ev(b))
    after = [c.file_id for c in tool.opts.cells]
    assert after[0] == before[3] and after[3] == before[0]
    tool.edge.setCurrentIndex(tool.edge.findData(1080))
    tool.make()
    assert pump(lambda: tool._thread is None and "Made" in tool.result.text())
    out = list(made.glob("Collage *.jpg"))
    with Image.open(out[0]) as im:
        assert im.size == (1080, 1080)


def test_batch_makes_copies_in_a_new_folder(window):
    w, ids, made = window
    w.grid.selected = set(ids)
    w.open_page("Create")
    w.create_page.open_tool("batch")
    tool = w.create_page.tools["batch"]
    tool.preset.setCurrentIndex(tool.preset.findText("Email (1280 px)"))
    tool.pattern.setText("Copy {n}")
    tool.wm_text.setText("Lunelis")
    tool.make()
    assert pump(lambda: tool._thread is None and "Made" in tool.result.text())
    folders = [p for p in made.iterdir() if p.name.startswith("Batch ")]
    assert len(folders) == 1
    assert sorted(p.name for p in folders[0].iterdir()) == [f"Copy {n:03d}.jpg" for n in range(1, 6)]


def test_the_create_page_fits_the_smallest_window(window):
    w, ids, made = window
    w.show()
    w.open_page("Create")
    w.create_page.open_tool("collage")
    w.resize(900, 350)
    for _ in range(20):
        QApplication.processEvents()
    assert (w.width(), w.height()) == (900, 350)
