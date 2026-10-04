"""0.16.2: the rest of the interface audit - words for errors, text size, small windows."""
from PySide6.QtWidgets import QApplication, QLabel


def _app():
    return QApplication.instance() or QApplication([])


def test_system_errors_are_said_in_words_and_our_own_kept():
    from lunelis.ui.widgets import plain
    e = PermissionError(13, "Access is denied", r"\nas\photos\a.ARW")
    assert "another program" in plain(e) and r"\nas\photos\a.ARW" in plain(e)
    assert "isn't there" in plain(FileNotFoundError(2, "No such file", "x.jpg"))
    assert plain(ValueError("That folder isn't inside any of your sources.")) == \
        "That folder isn't inside any of your sources."


def test_font_sizes_follow_windows_text_size(monkeypatch):
    app = _app()
    from lunelis.ui import theme
    assert theme.font_pt(12) == 9.0 * theme.text_scale()
    qss = theme.stylesheet()
    import re
    assert "font-size:" in qss and not re.search(r"font-size:\s*[\d.]+px", qss)    # every size in points
    big = app.font()
    big.setPointSizeF(13.5)                                 # "Make text bigger" at 150 %
    monkeypatch.setattr(app, "font", lambda: big)
    assert theme.text_scale() == 1.5 and theme.font_pt(12) == 13.5


def test_status_message_keeps_its_whole_text_as_a_tooltip():
    _app()
    from lunelis.ui.main_window import StatusLabel
    s = StatusLabel()
    s.set_link("Found 3 damaged files - ", "see them", "Damaged files")
    assert s.toolTip() == "Found 3 damaged files - see them"


def test_a_sharp_thumbnail_has_the_screens_pixels():
    _app()
    from PySide6.QtGui import QPixmap
    from lunelis.ui.widgets import sharp
    pm = QPixmap(400, 300)
    label = QLabel()
    out = sharp(pm, 72, label)
    r = out.devicePixelRatio()
    assert out.width() == round(72 * r) and abs(out.width() / r - 72) < 1
