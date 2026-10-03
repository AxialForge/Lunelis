"""
The timeline scrubber: years (and months, where there's room) down the
right edge of the library when it's sorted by date - Google Photos style.
Hover to see which month is there; click or drag to jump, snapping to the
start of a month. It replaces the plain scroll bar in date sorts (in other
sorts the scroll bar comes back).
"""
from __future__ import annotations

from bisect import bisect_right
from datetime import date

from PySide6.QtCore import QRect, QRectF, Qt, Signal
from PySide6.QtGui import QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from lunelis.ui import theme as themes
from lunelis.ui.library import SORT_DATE
from lunelis.ui.theme import qcolor

WIDTH = 64
MIN_YEAR_GAP = 18          # px between year labels
MIN_MONTH_GAP = 7          # px between month ticks


class TimelineScrubber(QWidget):
    jump = Signal(int)             # index position to scroll to (the start of a month)
    hovered = Signal(str, int)     # month label + y for the bubble ("" = hide); the grid draws it

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedWidth(WIDTH)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.marks: list[tuple[int, int, int]] = []    # (first index, year, month); month 0 = undated
        self.n = 0
        self.view_frac = (0.0, 0.0)                     # visible part of the library, 0-1
        self.hover_y: int | None = None
        self._dragging = False
        self._font = QFont(self.font())
        self._font.setPixelSize(11)
        self._bubble_font = QFont(self.font())
        self._bubble_font.setPixelSize(12)
        self._bubble_font.setBold(True)

    # --- data ------------------------------------------------------------------

    def set_rows(self, rows: list[tuple]) -> None:
        """Month boundaries of the (date-sorted) library rows."""
        self.n = len(rows)
        marks = []
        last = None
        for i, r in enumerate(rows):
            d = r[SORT_DATE]
            key = (int(d[:4]), int(d[5:7])) if d and len(d) >= 7 and d[:4].isdigit() else (0, 0)
            if key != last:
                marks.append((i, *key))
                last = key
        self.marks = marks
        self.update()

    def set_view(self, first_frac: float, last_frac: float) -> None:
        self.view_frac = (first_frac, last_frac)
        self.update()

    # --- geometry ----------------------------------------------------------------

    def _y(self, index: int) -> float:
        top, h = 10, max(1, self.height() - 20)
        return top + h * index / max(1, self.n)

    def _index_at(self, y: float) -> int:
        top, h = 10, max(1, self.height() - 20)
        return min(max(0, self.n - 1), int(max(0.0, min(1.0, (y - top) / h)) * self.n))

    def mark_at(self, y: float) -> tuple[int, int, int] | None:
        """The month under a y position, snapped to its first photo."""
        if not self.marks:
            return None
        starts = [m[0] for m in self.marks]
        k = max(0, bisect_right(starts, self._index_at(y)) - 1)
        return self.marks[k]

    @staticmethod
    def label(mark: tuple[int, int, int]) -> str:
        _, year, month = mark
        return "Undated" if year == 0 else f"{date(2000, month, 1):%B} {year}"

    # --- painting ----------------------------------------------------------------

    def paintEvent(self, e) -> None:
        t = themes.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.n:
            return
        # Where you are.
        f0, f1 = self.view_frac
        top, h = 10, max(1, self.height() - 20)
        band = QRectF(WIDTH - 6, top + h * f0, 4, max(6, h * (f1 - f0)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcolor(t.text_muted))
        p.drawRoundedRect(band, 2, 2)
        # Months as ticks, years as labels - thinned out to what fits.
        p.setFont(self._font)
        last_year_y = -1e9
        last_tick_y = -1e9
        prev_year = None
        for i, year, month in self.marks:
            y = self._y(i)
            if year and month and y - last_tick_y >= MIN_MONTH_GAP:
                p.setPen(QPen(qcolor(t.border), 1))
                p.drawLine(WIDTH - 16, int(y), WIDTH - 11, int(y))
                last_tick_y = y
            is_year_start = year and year != prev_year
            prev_year = year
            if (is_year_start or year == 0) and y - last_year_y >= MIN_YEAR_GAP:
                p.setPen(qcolor(t.text_muted))
                text = "Undated" if year == 0 else str(year)
                p.drawText(QRect(0, int(y) - 8, WIDTH - 20, 16),
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, text)
                last_year_y = y

    # --- input -------------------------------------------------------------------

    def mouseMoveEvent(self, e) -> None:
        self.hover_y = int(e.position().y())
        if self._dragging:
            self._jump(self.hover_y)
        m = self.mark_at(self.hover_y)
        if m:
            self.hovered.emit(self.label(m), int(self._y(m[0])))
        self.update()

    def leaveEvent(self, e) -> None:
        self.hover_y = None
        self.hovered.emit("", 0)
        self.update()

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._jump(int(e.position().y()))

    def mouseReleaseEvent(self, e) -> None:
        self._dragging = False

    def _jump(self, y: int) -> None:
        m = self.mark_at(y)
        if m:
            self.jump.emit(m[0])
