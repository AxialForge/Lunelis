"""0.46: Edit - round slider handles, a quicker live preview, social crop ratios, a white balance picker."""
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionSlider


def test_slider_handles_are_circles():
    app = QApplication.instance() or QApplication([])
    from lunelis.ui import theme
    from lunelis.ui.develop import ParamSlider
    app.setStyleSheet(theme.stylesheet(theme.current()))
    for key in ("exposure", "temp", "sharpen"):                     # coloured tracks and a plain one
        w = ParamSlider(key, key, -5, 5, 0.01)
        w.resize(300, 50)
        w.show()
        app.processEvents()
        o = QStyleOptionSlider()
        w.slider.initStyleOption(o)
        r = w.slider.style().subControlRect(QStyle.ComplexControl.CC_Slider, o, QStyle.SubControl.SC_SliderHandle, w.slider)
        assert r.width() == r.height(), (key, r.width(), r.height())
        w.deleteLater()
    app.setStyleSheet("")


def test_the_live_preview_is_capped():
    from lunelis.ui.develop import FAST_EDGE
    assert FAST_EDGE <= 1024


def test_social_media_crop_ratios(tmp_path):
    QApplication.instance() or QApplication([])
    from lunelis.ui.develop import SOCIAL_ASPECTS, DevelopPanel
    p = DevelopPanel()
    labels = [p.aspect_box.itemText(i) for i in range(p.aspect_box.count())]
    assert "Stories / Reels / TikTok 9 : 16" in labels and "Instagram landscape 1.91 : 1" in labels
    i = labels.index("Stories / Reels / TikTok 9 : 16")
    got = []
    p.aspect_changed.connect(got.append)
    p.aspect_box.setCurrentIndex(i)
    assert abs(got[-1] - 9 / 16) < 1e-9
    assert len(SOCIAL_ASPECTS) == 9
    p.deleteLater()


def test_white_balance_from_a_grey_spot():
    import numpy as np
    from lunelis.edit import pipeline
    assert pipeline.white_balance_from((0.5, 0.5, 0.5)) == (0.0, 0.0)          # already neutral
    for cast in ((0.55, 0.5, 0.45), (0.48, 0.52, 0.47), (0.45, 0.5, 0.56)):     # warm, green, cool
        t, m = pipeline.white_balance_from(cast)
        lut = pipeline.tone_lut({"temp": t, "tint": m})
        out = [lut[c][int(round(cast[c] * 4095))] for c in range(3)]
        assert max(out) - min(out) < 0.01, (cast, out)              # neutral in the real pipeline
    assert pipeline.white_balance_from((0.6, 0.5, 0.35))[0] == -100   # beyond the slider: as far as it goes
