"""0.53: the Temperature slider's kelvin reading."""
from lunelis.edit import kelvin as k


def test_a_higher_slider_is_a_higher_kelvin_and_zero_is_the_shots_own():
    assert round(k.kelvin(5500, 0)) == 5500
    values = [k.kelvin(5500, s) for s in (-100, -50, 0, 50, 100)]
    assert values == sorted(values) and values[0] < 4500 and values[-1] > 7000
    # There and back: the slider that sets 6500 K gives 6500 K.
    assert abs(k.kelvin(5500, k.slider_for(5500, 6500)) - 6500) < 5


def test_the_label_marks_a_guess():
    assert k.label(5350, 0) == "5,350 K"
    assert k.label(None, 0) == "≈ 5,500 K"            # a file that doesn't record its white balance
    assert k.as_shot("nothing-here.jpg", False) is None and k.as_shot("nothing-here.arw", True) is None


def test_the_temperature_slider_shows_kelvin():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from lunelis.ui.develop import DevelopPanel
    p = DevelopPanel()
    s = p.sliders["temp"]
    assert s.note.text() == "≈ 5,500 K"
    p.set_as_shot(5350.0)
    assert s.note.text() == "5,350 K"
    s.set_value(50)
    assert s.note.text().endswith(" K") and int(s.note.text().replace(",", "").split()[0]) > 5350
    assert p.sliders["tint"].note.text() == ""           # only Temperature has one


def test_scrolling_the_edit_panel_never_changes_a_slider():
    # 0.53: the wheel changed whatever slider it crossed - "my edits don't stick".
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication, QSlider
    app = QApplication.instance() or QApplication([])
    from lunelis.ui import wheel_guard
    from lunelis.ui.develop import DevelopPanel
    wheel_guard.install(app)
    p = DevelopPanel()
    p.resize(340, 400)
    p.show()
    s = p.sliders["exposure"]
    s.set_value(0.5)
    bar = p.verticalScrollBar()
    assert bar.maximum() > 0

    def wheel(target):
        c = QPointF(target.rect().center())
        QApplication.sendEvent(target, QWheelEvent(c, target.mapToGlobal(c), QPoint(), QPoint(0, -120),
                                                   Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                                                   Qt.ScrollPhase.NoScrollPhase, False))
    for target in (s.slider, s.number):
        before = bar.value()
        wheel(target)
        assert s.current() == 0.5                       # the edit is as it was
        assert bar.value() > before                     # and the panel scrolled
    free = QSlider(Qt.Orientation.Horizontal, minimum=0, maximum=100, value=50)   # not in a scroll area
    free.show()
    wheel(free)
    assert free.value() != 50                           # e.g. the grid-size slider keeps its wheel
    p.close()
    free.close()
