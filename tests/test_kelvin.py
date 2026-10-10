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
