"""
A slider whose handle is a true circle (0.52).

Qt's style sheets draw the handle with no anti-aliasing and size it from the
groove, so `border-radius` gave a lumpy shape a pixel or two taller than wide
on every track height. The style sheet now only reserves the handle's place
(transparent, for hit-testing); this paints the circle itself, smooth, in the
theme's colours: an outline in the muted text colour, the accent on hover or
while dragged.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QSlider, QStyle, QStyleOptionSlider

DIAMETER = 16
BORDER = 2.0


class RoundSlider(QSlider):
    def __init__(self, *a, **k) -> None:
        super().__init__(*a, **k)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def paintEvent(self, e) -> None:
        super().paintEvent(e)                      # the track (the handle there is transparent)
        from lunelis.ui import theme
        t = theme.current()
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        r = self.style().subControlRect(QStyle.ComplexControl.CC_Slider, opt,
                                        QStyle.SubControl.SC_SliderHandle, self)
        c = QPointF(r.center()) + QPointF(0.5, 0.5)
        if self.orientation() == Qt.Orientation.Horizontal:
            c.setY(self.height() / 2)
        else:
            c.setX(self.width() / 2)
        hot = self.isSliderDown() or (self.underMouse() and r.contains(self.mapFromGlobal(self.cursor().pos())))
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(t.accent if hot else t.text_muted), BORDER))
        p.setBrush(QColor(t.surface))
        rad = (DIAMETER - BORDER) / 2
        p.drawEllipse(c, rad, rad)
        p.end()
