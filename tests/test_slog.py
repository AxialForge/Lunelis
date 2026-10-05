"""0.24: S-Log3 clips found from their Sony XML and shown through a preview look
(built-in to Rec.709, or your own .cube LUT) in the player and thumbnails."""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PIL import Image  # noqa: E402
from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lunelis.video import lut as L  # noqa: E402
from lunelis.video import slog  # noqa: E402

SONY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<NonRealTimeMeta xmlns="urn:schemas-professionalDisc:nonRealTimeMeta:ver.2.00">
  <AcquisitionRecord>
    <Group name="CameraUnitMetadataSet">
      <Item name="CaptureGammaEquation" value="{gamma}"/>
      <Item name="CaptureColorPrimaries" value="{prim}"/>
    </Group>
  </AcquisitionRecord>
</NonRealTimeMeta>
"""


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def cube_text(n, fn=lambda r, g, b: (r, g, b), title="Test"):
    lines = [f'TITLE "{title}"', f"LUT_3D_SIZE {n}"]
    v = np.linspace(0, 1, n)
    for b in v:                      # red changes fastest
        for g in v:
            for r in v:
                lines.append("%.6f %.6f %.6f" % fn(r, g, b))
    return "\n".join(lines)


# --- LUTs ------------------------------------------------------------------------------------

def test_a_cube_file_is_read_with_its_size_title_and_red_fastest():
    lut = L.parse_cube(cube_text(5, lambda r, g, b: (r, 0.0, 0.0)))
    assert lut.size == 5 and lut.title == "Test"
    out = lut.apply_float(np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], np.float32))
    assert out[0, 0] == pytest.approx(1.0) and out[1, 0] == pytest.approx(0.0)


def test_bad_cubes_are_refused():
    with pytest.raises(L.LutError):
        L.parse_cube("LUT_1D_SIZE 4\n0 0 0\n")
    with pytest.raises(L.LutError):
        L.parse_cube("LUT_3D_SIZE 2\n0 0 0\n")             # too few lines
    with pytest.raises(L.LutError):
        L.parse_cube("TITLE x\n")


def test_an_identity_lut_leaves_pixels_alone():
    rgb = np.random.default_rng(1).integers(0, 256, (40, 60, 3), dtype=np.uint8)
    ident = L.parse_cube(cube_text(9))
    assert np.abs(ident.apply(rgb).astype(int) - rgb).max() <= 1
    assert np.abs(ident.apply_fast(rgb).astype(int) - rgb).max() <= 2    # 6 bits a channel in


def test_domain_scaling():
    text = cube_text(3).replace("LUT_3D_SIZE 3", "LUT_3D_SIZE 3\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 2 2 2")
    lut = L.parse_cube(text)
    assert lut.apply_float(np.array([1.0, 1.0, 1.0], np.float32)) == pytest.approx([0.5, 0.5, 0.5])


# --- S-Log3 ----------------------------------------------------------------------------------

def test_slog3_mid_grey_lands_where_rec709_puts_it():
    grey = slog.linear_to_slog3(np.float32(0.18))
    assert float(grey) == pytest.approx(420 / 1023, abs=1e-4)
    assert float(slog.slog3_to_linear(grey)) == pytest.approx(0.18, abs=1e-4)
    for cine in (True, False):
        out = slog.builtin_lut(cine).apply_float(np.array([grey, grey, grey], np.float32))
        assert out == pytest.approx([0.409] * 3, abs=0.01)     # Rec.709 of 18 % grey
    assert float(slog.rec709_oetf(np.float32(0.18))) == pytest.approx(0.409, abs=0.002)


def test_the_look_keeps_highlights_from_clipping_and_blacks_black():
    # Two and three stops over grey still differ (rolled off, not clipped).
    two, three = (slog.to_rec709(np.array([[c, c, c]], np.float32))[0, 0]
                  for c in slog.linear_to_slog3(np.array([0.72, 1.44], np.float32)))
    assert 0.75 < two < three < 0.99
    assert slog.to_rec709(np.array([[0.0, 0.0, 0.0]], np.float32))[0] == pytest.approx([0, 0, 0], abs=1e-3)


def test_clips_are_found_from_their_sony_sidecar(tmp_path):
    clip = tmp_path / "C0001.MP4"
    clip.write_bytes(b"x")
    assert slog.detect(clip) is None                          # no sidecar
    (tmp_path / "C0001M01.XML").write_text(SONY_XML.format(gamma="s-log3-cine", prim="S-Gamut3Cine"))
    assert slog.detect(clip) == "s-log3-cine"
    (tmp_path / "C0001M01.XML").write_text(SONY_XML.format(gamma="s-log3", prim="S-Gamut3"))
    assert slog.detect(clip) == "s-log3"
    (tmp_path / "C0001M01.XML").write_text(SONY_XML.format(gamma="rec709", prim="rec709"))
    assert slog.detect(clip) is None                          # another picture profile
    other = tmp_path / "c0002.mp4"
    other.write_bytes(b"x")
    (tmp_path / "C0002.xml").write_text(SONY_XML.format(gamma="s-log3-cine", prim="S-Gamut3Cine"))
    assert slog.detect(other) == "s-log3-cine"                # any case, either name


def test_the_preview_choice(tmp_path):
    clip = tmp_path / "C0001.MP4"
    clip.write_bytes(b"x")
    (tmp_path / "C0001M01.XML").write_text(SONY_XML.format(gamma="s-log3-cine", prim="S-Gamut3Cine"))
    assert slog.preview_lut(clip, "off") is None
    assert "Cine" in slog.preview_lut(clip, "builtin").title
    cube = tmp_path / "mine.cube"
    cube.write_text(cube_text(3, title="Mine"))
    assert slog.preview_lut(clip, f"cube:{cube}").title == "Mine"
    assert "Cine" in slog.preview_lut(clip, f"cube:{tmp_path / 'gone.cube'}").title   # falls back
    plain = tmp_path / "plain.mp4"
    plain.write_bytes(b"x")
    assert slog.preview_lut(plain, "builtin") is None


# --- thumbnails and the player ----------------------------------------------------------------

def _grey_clip(path, seconds=2):
    import av
    code = int(round(420 / 1023 * 255))                      # S-Log3 mid grey
    with av.open(str(path), "w") as c:
        v = c.add_stream("libx264", rate=10)
        v.width, v.height, v.pix_fmt = 64, 48, "yuv420p"
        v.options = {"crf": "0"}
        for i in range(seconds * 10):
            f = av.VideoFrame.from_ndarray(np.full((48, 64, 3), code, np.uint8), format="rgb24")
            f.pts = i
            for p in v.encode(f):
                c.mux(p)
        for p in v.encode():
            c.mux(p)
    return path


def test_a_log_clips_thumbnail_goes_through_the_look(tmp_path):
    from lunelis.raw.thumbnails import render
    clip = _grey_clip(tmp_path / "C0001.MP4")
    flat = np.asarray(render(str(clip)))
    (tmp_path / "C0001M01.XML").write_text(SONY_XML.format(gamma="s-log3-cine", prim="S-Gamut3Cine"))
    looked = np.asarray(render(str(clip)))
    off = np.asarray(render(str(clip), log_preview="off"))
    assert np.array_equal(flat, off)
    # mid grey stays near mid grey (the look is about contrast and colour, not exposure)
    assert abs(float(looked.mean()) - 0.409 * 255) < 8


def test_changing_the_look_remakes_only_log_thumbnails(tmp_path):
    from lunelis.catalog.schema import open_catalog
    from lunelis.importers.metadata import extract_pending
    from lunelis.importers.scan import add_root, scan_root
    root = tmp_path / "Media"
    root.mkdir()
    _grey_clip(root / "C0001.MP4")
    _grey_clip(root / "C0002.MP4")
    (root / "C0001M01.XML").write_text(SONY_XML.format(gamma="s-log3-cine", prim="S-Gamut3Cine"))
    conn = open_catalog(tmp_path / "cat.db")
    scan_root(conn, add_root(conn, root))
    extract_pending(conn)
    conn.execute("UPDATE files SET thumbnail_path = 'x/' || id || '.jpg'")
    conn.commit()
    assert slog.refresh_thumbnails(conn) == 1
    rows = dict(conn.execute("SELECT filename, thumbnail_path FROM files").fetchall())
    assert rows["C0001.MP4"] is None and rows["C0002.MP4"] is not None
    conn.close()


def test_the_player_shows_a_log_clip_through_the_lut_and_can_show_the_log(app, tmp_path):
    from lunelis.catalog.schema import open_catalog
    from lunelis.ui.video_player import VideoPlayer
    clip = _grey_clip(tmp_path / "C0001.MP4")
    (tmp_path / "C0001M01.XML").write_text(SONY_XML.format(gamma="s-log3-cine", prim="S-Gamut3Cine"))
    plain = _grey_clip(tmp_path / "plain.mp4")
    conn = open_catalog(tmp_path / "cat.db")
    p = VideoPlayer(conn)
    try:
        p.resize(320, 240)
        p.load(str(clip))
        assert p.lut is not None and p.screen.currentWidget() is p.lut_view
        assert p.log_b.isVisibleTo(p) and "S-Log3" in p.note.text()
        p.log_b.setChecked(True)
        assert p.lut_view.lut is None                         # as recorded
        p.log_b.setChecked(False)
        assert p.lut_view.lut is p.lut
        p.load(str(plain))
        assert p.lut is None and p.screen.currentWidget() is p.video and not p.log_b.isVisibleTo(p)
        from lunelis.settings import Settings
        Settings(conn).set("log_preview", "off")
        p.stop()
        p.load(str(clip))
        assert p.lut is None and "off in Settings" in p.note.text()
    finally:
        p.stop()
        p.deleteLater()
        conn.close()


def test_lut_view_applies_the_table_to_frames(app):
    from PySide6.QtGui import QImage
    from lunelis.ui.video_player import LutView
    view = LutView()
    view.resize(64, 48)
    invert = L.from_function(lambda rgb: 1.0 - rgb, 9, "invert")
    view.lut = invert

    class Frame:
        def toImage(self):
            img = QImage(64, 48, QImage.Format.Format_RGB888)
            img.fill(0)
            return img
    view._frame(Frame())
    assert view.image is not None and view.image.pixelColor(10, 10).red() > 250
    view.deleteLater()


def test_the_settings_choice_saves_and_remakes_log_thumbnails(app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from lunelis.catalog.schema import open_catalog
    from lunelis.settings import Settings
    from lunelis.ui.settings_view import SettingsView
    conn = open_catalog(tmp_path / "s.db")
    view = SettingsView(conn)
    view.refresh()
    assert view.log_look.currentData() == "builtin"
    rescans = []
    view.rescan.connect(rescans.append)
    monkeypatch.setattr(slog, "refresh_thumbnails", lambda c: 3)
    view.log_look.setCurrentIndex(view.log_look.findData("off"))
    view._bg().wait()
    assert Settings(conn).get("log_preview") == "off" and "3 S-Log3" in view.saved.text()
    cube = tmp_path / "mine.cube"
    cube.write_text(cube_text(3, title="Mine"))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(cube), "")))
    view.log_look.setCurrentIndex(view.log_look.findData("cube"))
    view._bg().wait()
    assert Settings(conn).get("log_preview") == f"cube:{cube}" and view.log_lut_label.text() == "mine.cube"
    bad = tmp_path / "bad.cube"
    bad.write_text("LUT_1D_SIZE 2\n")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(bad), "")))
    view.log_look.setCurrentIndex(view.log_look.findData("builtin"))
    view._bg().wait()
    view.log_look.setCurrentIndex(view.log_look.findData("cube"))
    assert "Not saved" in view.saved.text() and Settings(conn).get("log_preview") == "builtin"
    assert view.log_look.currentData() == "builtin"
    view.deleteLater()
    conn.close()
