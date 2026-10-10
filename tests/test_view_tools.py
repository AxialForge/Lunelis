"""0.52: the photo view's tool rail - focus peaking (and the AF point),
exposure warnings, false colour, zones, histogram and guides."""
import numpy as np
from PIL import Image

from lunelis import view_tools as vt


def _scene():
    """Sharp black-and-white bars on the left half, a smooth ramp on the right;
    a blown patch and a black patch."""
    a = np.zeros((200, 300, 3), np.uint8)
    a[:, :150] = np.where((np.arange(150) // 6) % 2, 220, 30)[None, :, None]
    a[:, 150:] = np.linspace(40, 200, 150).astype(np.uint8)[None, :, None]
    a[10:30, 200:230] = 255
    a[170:190, 200:230] = 0
    return a


def test_focus_peaking_lights_the_sharp_edges_not_the_smooth_ramp():
    o = vt.focus_peaking(_scene())
    on = o[..., 3] > 0
    assert on[:, 5:145].mean() > 0.2 and on[40:160, 160:290].mean() < 0.01


def test_clipping_shows_blown_and_crushed_areas():
    o = vt.clipping(_scene())
    assert tuple(o[20, 215, :3]) == vt.HIGH_COLOUR and tuple(o[180, 215, :3]) == vt.LOW_COLOUR
    assert o[100, 220, 3] == 0                                     # a middle grey: nothing drawn


def test_false_colour_zones_and_histogram():
    s = _scene()
    fc = vt.false_colour(s)
    assert tuple(fc[20, 215, :3]) == vt.FALSE_COLOUR[-1][1]        # clipped: red
    assert tuple(fc[180, 215, :3]) == vt.FALSE_COLOUR[0][1]        # crushed
    z = vt.zones(s)
    assert z[20, 215, 0] == 255 and z[180, 215, 0] == 0
    h = vt.histogram(s)
    assert h.shape == (4, 256) and h[0].sum() == s.shape[0] * s.shape[1]


def test_the_af_point_comes_from_the_file_and_turns_with_it(tmp_path):
    import piexif
    p = tmp_path / "a.jpg"
    exif = piexif.dump({"Exif": {piexif.ExifIFD.PixelXDimension: 400, piexif.ExifIFD.PixelYDimension: 200,
                                 piexif.ExifIFD.SubjectArea: (100, 50)}})
    Image.new("RGB", (400, 200)).save(p, exif=exif)
    assert vt.af_point(str(p), 1) == (0.25, 0.25)
    assert vt.af_point(str(p), 6) == (0.75, 0.25)                   # turned a quarter clockwise
    q = tmp_path / "b.jpg"
    Image.new("RGB", (40, 20)).save(q)
    assert vt.af_point(str(q)) is None


def test_the_rail_turns_tools_on_and_off(tmp_path):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from PySide6.QtGui import QPixmap
    from lunelis.ui.develop import EditCanvas
    from lunelis.ui.detail_view import DetailView
    c = EditCanvas()
    c.resize(600, 400)
    c.show_pixmap(QPixmap.fromImage(__import__("PIL.ImageQt", fromlist=["x"]).ImageQt(Image.fromarray(_scene()))),
                  sharp=True)
    fake = type("D", (), {})()
    fake.canvas, fake.info, fake.GUIDES = c, None, DetailView.GUIDES
    from PySide6.QtWidgets import QToolButton
    fake.tool_b = {n: QToolButton(checkable=True) for n, _ in DetailView.TOOLS}
    fake._load_af = lambda: None
    for name in ("clipping", "histogram", "guides", "guides"):
        DetailView.toggle_tool(fake, name)
    assert c.view_tool == "clipping" and c.show_histogram and c.guide == "golden"
    assert fake.tool_b["clipping"].isChecked() and fake.tool_b["guides"].isChecked()
    c.grab()                                                         # paints without an error
    DetailView.toggle_tool(fake, "false_colour")                     # one overlay at a time
    assert c.view_tool == "false_colour" and not fake.tool_b["clipping"].isChecked()
    c.grab()
