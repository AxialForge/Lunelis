"""
The tone curve editor in the Edit panel.

A square with the photo's histogram behind it. Click on the curve (or
anywhere) to add a point, drag points to bend it, double-click a point to
remove it; the two end points slide up and down only. RGB bends all
channels; R, G and B bend one each. Emits `changed(channel, points, final)`
- final when the mouse is released.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from lunelis.edit.pipeline import curve_values
from lunelis.edit.stack import LINEAR, clean_curve
from lunelis.ui import theme as themes
from lunelis.ui.theme import qcolor

CHANNEL_COLOURS = {"rgb": None, "r": "#e05252", "g": "#3fae5a", "b": "#4a7fe0"}


class CurveCanvas(QWidget):
    changed = Signal(str, tuple, bool)
    HIT = 9

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(200, 200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(240)
        self.channel = "rgb"
        self.curves: dict = {}
        self.hist: np.ndarray | None = None          # 64 bins, 0..1
        self._drag: int | None = None
        self._pts: list[tuple[float, float]] = list(LINEAR)
        self.setMouseTracking(True)

    # --- geometry ---

    def _box(self) -> QRectF:
        s = min(self.width(), self.height()) - 12
        return QRectF((self.width() - s) / 2, 6, s, s)

    def _to_screen(self, x: float, y: float) -> QPointF:
        b = self._box()
        return QPointF(b.left() + x * b.width(), b.bottom() - y * b.height())

    def _to_curve(self, p: QPointF) -> tuple[float, float]:
        b = self._box()
        return (min(1.0, max(0.0, (p.x() - b.left()) / b.width())),
                min(1.0, max(0.0, (b.bottom() - p.y()) / b.height())))

    # --- state ---

    def set_state(self, curves: dict, channel: str) -> None:
        self.curves, self.channel = dict(curves), channel
        self._pts = list(self.curves.get(channel, LINEAR))
        self.update()

    def _emit(self, final: bool) -> None:
        pts = clean_curve(self._pts)
        self.changed.emit(self.channel, pts, final)

    # --- drawing ---

    def paintEvent(self, e) -> None:
        t = themes.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        b = self._box()
        p.fillRect(b, qcolor(t.field_bg))
        if self.hist is not None and self.hist.max() > 0:
            h = self.hist / self.hist.max()
            path = QPainterPath(QPointF(b.left(), b.bottom()))
            for i, v in enumerate(h):
                path.lineTo(b.left() + (i + 0.5) / len(h) * b.width(), b.bottom() - v * b.height() * 0.9)
            path.lineTo(b.right(), b.bottom())
            path.closeSubpath()
            fill = qcolor(t.text_faint)
            fill.setAlpha(70)
            p.fillPath(path, fill)
        grid = qcolor(t.border)
        p.setPen(QPen(grid, 1))
        for k in (1, 2, 3):
            p.drawLine(QPointF(b.left() + b.width() * k / 4, b.top()), QPointF(b.left() + b.width() * k / 4, b.bottom()))
            p.drawLine(QPointF(b.left(), b.top() + b.height() * k / 4), QPointF(b.right(), b.top() + b.height() * k / 4))
        p.setPen(QPen(grid, 1, Qt.PenStyle.DashLine))
        p.drawLine(self._to_screen(0, 0), self._to_screen(1, 1))
        # The other channels' curves, faintly.
        for ch, pts in self.curves.items():
            if ch != self.channel:
                self._draw_curve(p, pts, ch, faint=True)
        self._draw_curve(p, clean_curve(self._pts), self.channel, faint=False)
        colour = QColor(CHANNEL_COLOURS[self.channel] or t.text)
        for x, y in clean_curve(self._pts):
            p.setPen(QPen(colour, 2))
            p.setBrush(qcolor(t.field_bg))
            p.drawEllipse(self._to_screen(x, y), 4.5, 4.5)
        p.setPen(QPen(grid, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(b)

    def _draw_curve(self, p: QPainter, pts, ch: str, faint: bool) -> None:
        xs = np.linspace(0, 1, 97)
        ys = curve_values(pts, xs)
        colour = QColor(CHANNEL_COLOURS[ch] or themes.current().text)
        if faint:
            colour.setAlpha(90)
        p.setPen(QPen(colour, 1.2 if faint else 2))
        path = QPainterPath(self._to_screen(xs[0], ys[0]))
        for x, y in zip(xs[1:], ys[1:]):
            path.lineTo(self._to_screen(x, y))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

    # --- mouse ---

    def _hit(self, pos: QPointF) -> int | None:
        for i, (x, y) in enumerate(self._pts):
            s = self._to_screen(x, y)
            if abs(s.x() - pos.x()) <= self.HIT and abs(s.y() - pos.y()) <= self.HIT:
                return i
        return None

    def mousePressEvent(self, e) -> None:
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self._pts = list(clean_curve(self._pts))
        i = self._hit(e.position())
        if i is None:
            x, y = self._to_curve(e.position())
            if 0.01 < x < 0.99:
                self._pts.append((x, y))
                self._pts.sort()
                i = self._pts.index((x, y))
                self._emit(False)
                self.update()
        self._drag = i

    def mouseMoveEvent(self, e) -> None:
        if self._drag is None:
            self.setCursor(Qt.CursorShape.PointingHandCursor if self._hit(e.position()) is not None
                           else Qt.CursorShape.CrossCursor)
            return
        i = self._drag
        x, y = self._to_curve(e.position())
        if i == 0:
            x = 0.0
        elif i == len(self._pts) - 1:
            x = 1.0
        else:
            x = min(self._pts[i + 1][0] - 0.02, max(self._pts[i - 1][0] + 0.02, x))
        self._pts[i] = (x, y)
        self._emit(False)
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        if self._drag is not None:
            self._drag = None
            self._emit(True)

    def mouseDoubleClickEvent(self, e) -> None:
        i = self._hit(e.position())
        if i is not None and 0 < i < len(self._pts) - 1:
            del self._pts[i]
            self._drag = None
            self._emit(True)
            self.update()


class CurveEditor(QWidget):
    changed = Signal(str, tuple, bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(4)
        self.group = QButtonGroup(self)
        self.buttons = {}
        # Short labels, shrinkable buttons: the Edit panel is only 300 px inside.
        for ch, label in (("rgb", "RGB"), ("r", "R"), ("g", "G"), ("b", "B")):
            b = QPushButton(label, checkable=True)
            b.setMinimumWidth(0)
            b.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            b.clicked.connect(lambda _=False, c=ch: self.set_channel(c))
            self.group.addButton(b)
            row.addWidget(b)
            self.buttons[ch] = b
        self.reset_b = QPushButton("Reset", clicked=self._reset)
        self.reset_b.setToolTip("Straighten this channel's curve")
        self.reset_b.setMinimumWidth(0)
        self.reset_b.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        row.addWidget(self.reset_b)
        v.addLayout(row)
        self.canvas = CurveCanvas()
        self.canvas.changed.connect(self.changed.emit)
        v.addWidget(self.canvas)
        self.buttons["rgb"].setChecked(True)

    def set_channel(self, ch: str) -> None:
        self.buttons[ch].setChecked(True)
        self.canvas.set_state(self.canvas.curves, ch)

    def set_curves(self, curves: dict) -> None:
        self.canvas.set_state(curves, self.canvas.channel)

    def set_histogram(self, hist: np.ndarray | None) -> None:
        self.canvas.hist = hist
        self.canvas.update()

    def _reset(self) -> None:
        self.changed.emit(self.canvas.channel, LINEAR, True)
