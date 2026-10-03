"""
Editing a mask on the photo (the canvas delegates to this while a mask is
selected in the Edit panel):

- gradient: drag the start or end handle, or the middle one to move it.
  The effect is full at the start line and gone past the end line;
- radial: drag the centre to move, the right/bottom handles to resize;
- brush: paint with the left button; hold Alt to erase. The circle under
  the cursor shows the brush size.

Positions are fractions of the uncropped frame (edit/masks.py); `crop`
maps them onto the displayed, cropped photo. A red overlay shows where
the mask applies (O toggles it).
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen


class MaskTool(QObject):
    shape_changed = Signal(tuple, bool)     # shape, final
    stroke = Signal(tuple)                  # (radius, feather, flow, erase, points)
    HIT = 11

    def __init__(self, canvas) -> None:
        super().__init__(canvas)
        self.canvas = canvas
        self.kind: str | None = None
        self.shape: tuple = ()
        self.crop = (0.0, 0.0, 1.0, 1.0)
        self.overlay: QImage | None = None
        self.show_overlay = True
        self.brush = (0.03, 0.5, 1.0)       # radius (of the long side), feather, flow
        self.erase = False
        self._grab: str | None = None
        self._at = QPointF()
        self._start: tuple = ()
        self._points: list[tuple[float, float]] = []
        self._painting_erase = False
        self._cursor: QPointF | None = None

    def set(self, kind: str | None, shape: tuple = (), crop=None) -> None:
        self.kind, self.shape = kind, tuple(shape)
        if crop is not None:
            self.crop = crop
        self._points = []
        self.canvas.update()

    # --- coordinates ---

    def _frame_rect(self) -> QRectF:
        """The uncropped frame, in screen pixels (partly off the photo)."""
        r = self.canvas._fit_rect()
        x0, y0, x1, y1 = self.crop
        fw, fh = r.width() / max(1e-6, x1 - x0), r.height() / max(1e-6, y1 - y0)
        return QRectF(r.x() - x0 * fw, r.y() - y0 * fh, fw, fh)

    def to_screen(self, x: float, y: float) -> QPointF:
        f = self._frame_rect()
        return QPointF(f.x() + x * f.width(), f.y() + y * f.height())

    def to_frame(self, p: QPointF) -> tuple[float, float]:
        f = self._frame_rect()
        return ((p.x() - f.x()) / f.width(), (p.y() - f.y()) / f.height())

    def _long(self) -> float:
        f = self._frame_rect()
        return max(f.width(), f.height())

    # --- drawing ---

    def paint(self, p: QPainter) -> None:
        if self.kind is None or self.canvas.pix is None:
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.show_overlay and self.overlay is not None:
            p.drawImage(self.canvas._fit_rect(), self.overlay)
        white, shade = QColor(255, 255, 255), QColor(0, 0, 0, 160)
        if self.kind == "linear" and len(self.shape) == 4:
            a, b = self.to_screen(*self.shape[:2]), self.to_screen(*self.shape[2:])
            d = b - a
            n = QPointF(-d.y(), d.x())
            L = (n.x() ** 2 + n.y() ** 2) ** 0.5 or 1
            n = QPointF(n.x() / L * 2000, n.y() / L * 2000)
            for pt, style in ((a, Qt.PenStyle.SolidLine), (b, Qt.PenStyle.DashLine)):
                for colour, width in ((shade, 3), (white, 1.4)):
                    p.setPen(QPen(colour, width, style))
                    p.drawLine(pt - n, pt + n)
            p.setPen(QPen(white, 1, Qt.PenStyle.DotLine))
            p.drawLine(a, b)
            for pt in (a, b, (a + b) / 2):
                self._handle(p, pt)
        elif self.kind == "radial" and len(self.shape) == 5:
            cx, cy, rx, ry, feather = self.shape
            c = self.to_screen(cx, cy)
            L = self._long()
            for colour, width in ((shade, 3), (white, 1.4)):
                p.setPen(QPen(colour, width))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(c, rx * L, ry * L)
            p.setPen(QPen(white, 1, Qt.PenStyle.DashLine))
            inner = max(0.0, 1 - feather)
            p.drawEllipse(c, rx * L * inner, ry * L * inner)
            for pt in (c, c + QPointF(rx * L, 0), c + QPointF(0, ry * L)):
                self._handle(p, pt)
        elif self.kind == "brush":
            r = self.brush[0] * self._long()
            if self._points:
                path = QPainterPath(self.to_screen(*self._points[0]))
                for pt in self._points[1:]:
                    path.lineTo(self.to_screen(*pt))
                colour = QColor(80, 160, 255, 90) if self._painting_erase else QColor(255, 80, 80, 90)
                p.setPen(QPen(colour, 2 * r, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                              Qt.PenJoinStyle.RoundJoin))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(path)
            if self._cursor is not None:
                for colour, width in ((shade, 3), (white, 1.2)):
                    p.setPen(QPen(colour, width))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawEllipse(self._cursor, r, r)
                p.setPen(QPen(white, 1, Qt.PenStyle.DotLine))
                p.drawEllipse(self._cursor, r * (1 - 0.8 * self.brush[1]), r * (1 - 0.8 * self.brush[1]))

    @staticmethod
    def _handle(p: QPainter, pt: QPointF) -> None:
        p.setPen(QPen(QColor(0, 0, 0, 170), 2))
        p.setBrush(QColor(255, 255, 255))
        p.drawEllipse(pt, 6, 6)

    # --- mouse ---

    def _handles(self) -> dict[str, QPointF]:
        if self.kind == "linear" and len(self.shape) == 4:
            a, b = self.to_screen(*self.shape[:2]), self.to_screen(*self.shape[2:])
            return {"start": a, "end": b, "move": (a + b) / 2}
        if self.kind == "radial" and len(self.shape) == 5:
            cx, cy, rx, ry, _f = self.shape
            c, L = self.to_screen(cx, cy), self._long()
            return {"move": c, "rx": c + QPointF(rx * L, 0), "ry": c + QPointF(0, ry * L)}
        return {}

    def press(self, e) -> bool:
        if self.kind is None or e.button() != Qt.MouseButton.LeftButton:
            return False
        pos = e.position()
        if self.kind == "brush":
            self._painting_erase = self.erase or bool(e.modifiers() & Qt.KeyboardModifier.AltModifier)
            self._points = [self.to_frame(pos)]
            self.canvas.update()
            return True
        for name, pt in self._handles().items():
            if abs(pt.x() - pos.x()) <= self.HIT and abs(pt.y() - pos.y()) <= self.HIT:
                self._grab, self._at, self._start = name, pos, self.shape
                return True
        if self.kind == "radial":                    # drag anywhere inside moves it
            cx, cy, rx, ry, _f = self.shape
            fx, fy = self.to_frame(pos)
            f = self._frame_rect()
            L = self._long()
            dx, dy = (fx - cx) * f.width() / max(1e-6, rx * L), (fy - cy) * f.height() / max(1e-6, ry * L)
            if dx * dx + dy * dy <= 1:
                self._grab, self._at, self._start = "move", pos, self.shape
                return True
        return True                                  # swallow: no zoom/pan while editing a mask

    def move(self, e) -> bool:
        if self.kind is None:
            return False
        pos = e.position()
        if self.kind == "brush":
            self._cursor = pos
            if self._points:
                self._points.append(self.to_frame(pos))
            self.canvas.update()
            return True
        if self._grab is None:
            hit = any(abs(pt.x() - pos.x()) <= self.HIT and abs(pt.y() - pos.y()) <= self.HIT
                      for pt in self._handles().values())
            self.canvas.setCursor(Qt.CursorShape.SizeAllCursor if hit else Qt.CursorShape.ArrowCursor)
            return True
        fx, fy = self.to_frame(pos)
        ax, ay = self.to_frame(self._at)
        dx, dy = fx - ax, fy - ay
        s = self._start
        if self.kind == "linear":
            x0, y0, x1, y1 = s
            if self._grab == "start":
                x0, y0 = x0 + dx, y0 + dy
            elif self._grab == "end":
                x1, y1 = x1 + dx, y1 + dy
            else:
                x0, y0, x1, y1 = x0 + dx, y0 + dy, x1 + dx, y1 + dy
            self.shape = (x0, y0, x1, y1)
        else:
            cx, cy, rx, ry, f = s
            f_rect, L = self._frame_rect(), self._long()
            if self._grab == "move":
                cx, cy = cx + dx, cy + dy
            elif self._grab == "rx":
                rx = max(0.01, abs(fx - cx) * f_rect.width() / L)
            else:
                ry = max(0.01, abs(fy - cy) * f_rect.height() / L)
            self.shape = (cx, cy, rx, ry, f)
        self.shape_changed.emit(self.shape, False)
        self.canvas.update()
        return True

    def release(self, e) -> bool:
        if self.kind is None:
            return False
        if self.kind == "brush" and self._points:
            pts = self._points
            # Thin the points: one every ~1/4 radius is plenty for a soft brush.
            step = self.brush[0] * 0.25
            kept = [pts[0]]
            for pt in pts[1:]:
                if abs(pt[0] - kept[-1][0]) + abs(pt[1] - kept[-1][1]) >= step:
                    kept.append(pt)
            if kept[-1] != pts[-1]:
                kept.append(pts[-1])
            r, feather, flow = self.brush
            self._points = []
            self.stroke.emit((r, feather, flow, self._painting_erase,
                              tuple((round(x, 4), round(y, 4)) for x, y in kept)))
            self.canvas.update()
            return True
        if self._grab is not None:
            self._grab = None
            self.shape_changed.emit(self.shape, True)
        return True

    def leave(self) -> None:
        self._cursor = None
        self.canvas.update()
