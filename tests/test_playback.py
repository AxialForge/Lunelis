"""0.23: videos play in the photo view and trim to new files; animated GIFs play
in the photo view and in their grid tile on hover; builds carry Qt Multimedia."""
import hashlib
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import av  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PIL import Image  # noqa: E402
from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lunelis.catalog.schema import open_catalog  # noqa: E402
from lunelis.importers.metadata import extract_pending  # noqa: E402
from lunelis.importers.scan import add_root, scan_root  # noqa: E402
from lunelis.ui.library import Filter, LibraryIndex  # noqa: E402
from lunelis.video import trim as T  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def make_clip(path, seconds=10, fps=30, audio=True):
    """A clip with a keyframe on every whole second, and (optionally) silent audio."""
    with av.open(str(path), "w") as c:
        v = c.add_stream("libx264", rate=fps)
        v.width, v.height, v.pix_fmt = 160, 120, "yuv420p"
        v.options = {"g": str(fps), "keyint_min": str(fps), "sc_threshold": "0", "bf": "0"}
        a = None
        if audio:
            a = c.add_stream("aac", rate=48000)
            a.layout = "mono"
        for i in range(seconds * fps):
            f = av.VideoFrame.from_ndarray(np.full((120, 160, 3), (i * 3) % 255, np.uint8), format="rgb24")
            f.pts = i
            for p in v.encode(f):
                c.mux(p)
        if a is not None:
            t = 0
            for _ in range(seconds * 48000 // 1024):
                af = av.AudioFrame.from_ndarray(np.zeros((1, 1024), np.float32), format="fltp", layout="mono")
                af.sample_rate, af.pts = 48000, t
                t += 1024
                for p in a.encode(af):
                    c.mux(p)
        for s in [v] + ([a] if a else []):
            for p in s.encode():
                c.mux(p)
    return path


def make_gif(path, frames=4):
    ims = [Image.new("RGB", (64, 48), (60 * k, 40, 200 - 40 * k)) for k in range(frames)]
    ims[0].save(path, save_all=True, append_images=ims[1:], duration=40, loop=0)
    return path


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def wait_for(cond, seconds=10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return cond()


# --- trimming -------------------------------------------------------------------------------

def test_a_trim_is_a_new_file_of_the_chosen_length_and_the_original_is_untouched(tmp_path):
    src = make_clip(tmp_path / "clip.mp4")
    before = sha(src)
    assert abs(T.duration(src) - 10) < 0.2
    r = T.trim(src, 3.0, 6.0, tmp_path / "out")
    assert os.path.basename(r.path) == "clip trim 0m03s-0m06s.mp4"
    assert 2.9 <= r.duration <= 3.3                       # whole seconds are keyframes here
    with av.open(r.path) as c:
        assert {s.type for s in c.streams} == {"video", "audio"}
    assert sha(src) == before


def test_a_trim_starts_at_the_keyframe_before_the_start_mark(tmp_path):
    src = make_clip(tmp_path / "clip.mp4", audio=False)
    r = T.trim(src, 3.5, 6.0, tmp_path / "out")           # nearest keyframe before 3.5 is 3.0
    assert 2.9 <= r.duration <= 3.3


def test_trims_never_overwrite_and_failures_leave_nothing(tmp_path):
    src = make_clip(tmp_path / "clip.mp4", seconds=4, audio=False)
    a = T.trim(src, 1, 2, tmp_path / "out")
    b = T.trim(src, 1, 2, tmp_path / "out")
    assert a.path != b.path and b.path.endswith("(2).mp4")
    with pytest.raises(T.TrimError):
        T.trim(src, 2, 2, tmp_path / "out")               # end not after start
    with pytest.raises(T.TrimError):
        T.trim(src, 30, 40, tmp_path / "out")             # past the end: nothing to keep
    assert sorted(os.listdir(tmp_path / "out")) == sorted([os.path.basename(a.path), os.path.basename(b.path)])


# --- the player in the photo view -----------------------------------------------------------

@pytest.fixture
def lib(tmp_path, monkeypatch):
    from lunelis.create import engine
    monkeypatch.setattr(engine, "output_dir", lambda _c: tmp_path / "creations")
    root = tmp_path / "Media"
    root.mkdir()
    Image.effect_noise((120, 80), 40).convert("RGB").save(root / "a_photo.jpg", "JPEG")
    make_clip(root / "b_clip.mp4", seconds=4)
    make_gif(root / "c_anim.gif")
    Image.new("RGB", (40, 30), "red").save(root / "d_still.gif")
    conn = open_catalog(tmp_path / "cat.db")
    scan_root(conn, add_root(conn, root))
    extract_pending(conn)                                  # formats and durations, as the app's scan does
    idx = LibraryIndex()
    idx.load(conn, "name", Filter())
    yield conn, idx
    conn.close()


def test_gifs_are_catalogued(lib):
    conn, idx = lib
    names = [r[0] for r in conn.execute("SELECT filename FROM files ORDER BY filename")]
    assert names == ["a_photo.jpg", "b_clip.mp4", "c_anim.gif", "d_still.gif"]


def test_a_video_plays_in_the_photo_view_and_a_photo_brings_the_canvas_back(app, lib):
    from lunelis.ui.detail_view import DetailView
    conn, idx = lib
    view = DetailView(conn)
    try:
        view.open(idx, 1)                                  # the clip
        assert view.player is not None and view.view_stack.currentWidget() is view.player
        assert not view.edit_b.isEnabled()
        assert wait_for(lambda: view.player.player.duration() > 3000), "the player never read the clip"
        assert view.player.len_l.text() == "0:04"
        view.go(0)                                         # a photo
        assert view.view_stack.currentWidget() is view.canvas and view.player.path is None
    finally:
        view.shut()
        view.deleteLater()


def test_trim_marks_and_save_make_a_new_file(app, lib, tmp_path):
    from lunelis.ui.detail_view import DetailView
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent
    conn, idx = lib
    view = DetailView(conn)
    try:
        view.open(idx, 1)
        p = view.player
        assert wait_for(lambda: p.player.duration() > 3000)
        assert not p.save_b.isEnabled()                    # nothing marked yet
        p.player.setPosition(1000)
        assert wait_for(lambda: p.player.position() >= 900)
        view.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_I, Qt.KeyboardModifier.NoModifier))
        p.player.setPosition(3000)
        assert wait_for(lambda: p.player.position() >= 2900)
        view.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_O, Qt.KeyboardModifier.NoModifier))
        assert p.mark_in is not None and p.mark_out is not None and p.mark_out > p.mark_in
        assert p.save_b.isEnabled()
        made = []
        p.trimmed.connect(made.append)
        p.save_b.click()
        assert wait_for(lambda: made, 20), p.note.text()
        assert os.path.dirname(made[0]) == str(tmp_path / "creations")
        assert "Saved" in p.note.text()
    finally:
        view.shut()
        view.deleteLater()


def test_an_animated_gif_plays_in_the_photo_view_and_a_still_one_does_not(app, lib):
    from lunelis.ui.detail_view import DetailView
    conn, idx = lib
    view = DetailView(conn)
    try:
        view.open(idx, 2)                                  # c_anim.gif
        assert view._movie is not None and not view.edit_b.isEnabled()
        seen = set()
        view._movie.frameChanged.connect(seen.add)
        assert wait_for(lambda: len(seen) >= 2, 5), "the GIF didn't advance"
        view.go(3)                                         # d_still.gif
        assert view._movie is None and view.edit_b.isEnabled()
    finally:
        view.shut()
        view.deleteLater()


def test_hovering_an_animated_gif_tile_plays_it(app, lib, tmp_path):
    from lunelis.ui import photoinfo
    from lunelis.ui.grid import PhotoGrid
    conn, idx = lib
    from lunelis.ui.thumbcache import ThumbCache
    grid = PhotoGrid(ThumbCache(tmp_path / "thumbs"))
    try:
        grid.resize(800, 600)
        grid.set_index(idx)
        grid.info_provider = lambda fid: photoinfo.load(conn, fid)
        grid._hover_i = 2
        grid._start_anim()
        assert grid._anim is not None and grid._anim_i == 2
        assert wait_for(lambda: not grid._anim.currentPixmap().isNull(), 5)
        grid._hover_i = 3                                  # a still GIF: nothing to play
        grid._stop_anim()
        grid._start_anim()
        assert grid._anim is None
    finally:
        grid._stop_anim()
        grid.deleteLater()


def test_the_self_test_checks_video_playback(tmp_path, app):
    from lunelis import selftest
    report = tmp_path / "report.txt"
    selftest.run(str(report))
    text = report.read_text(encoding="utf-8")
    line = next(l for l in text.splitlines() if "Video playback" in l)
    assert "formats" in line, line
