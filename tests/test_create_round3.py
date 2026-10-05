"""0.32 Create, round three: focus stacking, star trails, median stacks, and
panorama / HDR merges as Create tools."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PIL import Image, ImageFilter  # noqa: E402

from lunelis.create import stacking  # noqa: E402


def scene(seed=1, h=240, w=320):
    rng = np.random.default_rng(seed)
    small = (rng.random((h // 8, w // 8, 3)) * 255).astype(np.uint8)
    return np.asarray(Image.fromarray(small).resize((w, h), Image.Resampling.NEAREST))


def test_a_focus_stack_keeps_the_sharp_half_of_each_frame():
    sharp = scene()
    blurred = np.asarray(Image.fromarray(sharp).filter(ImageFilter.GaussianBlur(5)))
    left = sharp.copy()
    left[:, 160:] = blurred[:, 160:]                      # sharp on the left
    right = sharp.copy()
    right[:, :160] = blurred[:, :160]                     # sharp on the right
    out = stacking.combine([left, right], "focus", align=False)
    err = np.abs(out.astype(int) - sharp.astype(int))
    assert err[:, 20:140].mean() < 6 and err[:, 180:300].mean() < 6        # both halves sharp
    assert np.abs(blurred.astype(int) - sharp.astype(int)).mean() > 15


def test_lighten_is_the_per_pixel_maximum():
    rng = np.random.default_rng(2)
    frames = [(rng.random((20, 30, 3)) * 255).astype(np.uint8) for _ in range(5)]
    assert np.array_equal(stacking.combine(frames, "trails"), np.maximum.reduce(frames))


def test_a_median_removes_what_is_in_one_frame_only():
    base = scene(3)
    frames = [base.copy() for _ in range(5)]
    frames[2][100:140, 150:190] = (255, 0, 0)               # someone walking through
    out = stacking.combine(frames, "median", align=False)
    assert np.array_equal(out, base)


def test_frames_are_lined_up_before_stacking():
    base = scene(4, 300, 400)
    moved = np.roll(base, (6, -9), axis=(0, 1))
    aligned = stacking.align_to(base, moved)
    assert np.abs(aligned[30:-30, 30:-30].astype(int) - base[30:-30, 30:-30].astype(int)).mean() < 3


def test_options_are_checked():
    with pytest.raises(ValueError):
        stacking.StackOptions("median").check(2)
    with pytest.raises(ValueError):
        stacking.StackOptions("glitter").check(4)
    stacking.StackOptions("focus").check(2)


def test_make_saves_a_new_file(tmp_path):
    from lunelis.catalog.schema import open_catalog
    from lunelis.importers.scan import add_root, scan_root
    root = tmp_path / "Photos"
    root.mkdir()
    for i in range(3):
        Image.fromarray(np.roll(scene(5), i * 3, axis=1)).save(root / f"F{i}.jpg", quality=95)
    conn = open_catalog(tmp_path / "c.db")
    scan_root(conn, add_root(conn, root))
    ids = [r[0] for r in conn.execute("SELECT id FROM files ORDER BY filename")]
    before = sorted(os.listdir(root))
    path = stacking.make(conn, ids, stacking.StackOptions("median", "2048"), tmp_path / "out", "Median stack test")
    assert os.path.exists(path) and path.endswith(".jpg") and sorted(os.listdir(root)) == before
    again = stacking.make(conn, ids, stacking.StackOptions("median", "2048"), tmp_path / "out", "Median stack test")
    assert again != path                                       # never over an earlier one
    conn.close()


def test_the_create_page_has_the_new_tools_and_merges_go_to_the_window(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from lunelis.catalog.schema import open_catalog
    from lunelis.ui.create_page import CreatePage
    conn = open_catalog(tmp_path / "c.db")
    page = CreatePage(conn)
    try:
        for key in ("focus", "trails", "median", "panorama", "hdr"):
            assert key in page.tools
        assert not page.tools["trails"].align.isEnabled()
        asked = []
        page.merge_requested.connect(lambda k, ids: asked.append((k, ids)))
        tool = page.tools["panorama"]
        monkeypatch.setattr(tool.picker, "ids", lambda: [3, 4, 5])
        tool.make()
        assert asked == [("panorama", [3, 4, 5])] and tool.make_b.text() == "Merge…"
    finally:
        page.deleteLater()
        conn.close()
