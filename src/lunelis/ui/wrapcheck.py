"""
A check box whose label wraps (0.48). Qt's own never wraps: one long label on
Settings or Migrate made the whole page wider than a 1024 px window.

Drop-in for QCheckBox (same signals and methods); the label wraps to the
width the layout gives it, and clicking anywhere on it toggles the box.
"""
from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QCheckBox, QComboBox, QRadioButton, QSizePolicy, QStyle, QStyleOptionButton

GAP = 6


class _Wrap:
    def __init__(self, text: str = "", parent=None, **kw) -> None:
        super().__init__(text, parent, **kw)  # type: ignore[call-arg]
        sp = self.sizePolicy()
        sp.setHorizontalPolicy(QSizePolicy.Policy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)

    def _indicator(self) -> QSize:
        st = self.style()
        return QSize(st.pixelMetric(QStyle.PixelMetric.PM_IndicatorWidth, None, self),
                     st.pixelMetric(QStyle.PixelMetric.PM_IndicatorHeight, None, self))

    def _text_rect(self, width: int) -> QRect:
        ind = self._indicator()
        left = ind.width() + GAP
        return self.fontMetrics().boundingRect(QRect(0, 0, max(40, width - left), 10_000),
                                               int(Qt.TextFlag.TextWordWrap), self.text())

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return max(self._indicator().height(), self._text_rect(width).height()) + 4

    def sizeHint(self) -> QSize:
        full = self.fontMetrics().horizontalAdvance(self.text()) + self._indicator().width() + GAP + 4
        w = min(full, 640)
        return QSize(w, self.heightForWidth(w))

    def minimumSizeHint(self) -> QSize:
        w = self._indicator().width() + GAP + 120
        return QSize(w, self.heightForWidth(w))

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        opt = QStyleOptionButton()
        self.initStyleOption(opt)
        ind = self._indicator()
        top = 2
        opt.rect = QRect(0, top, ind.width(), ind.height())
        self.style().drawPrimitive(self.INDICATOR, opt, p, self)
        left = ind.width() + GAP
        p.setPen(self.palette().color(self.foregroundRole()))
        p.drawText(QRect(left, 1, self.width() - left, self.height()),
                   int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop), self.text())
        p.end()


class WrapCheckBox(_Wrap, QCheckBox):
    INDICATOR = QStyle.PrimitiveElement.PE_IndicatorCheckBox


class WrapRadioButton(_Wrap, QRadioButton):
    """The same for a radio button (0.48: Migrate's choices were 670 px wide)."""
    INDICATOR = QStyle.PrimitiveElement.PE_IndicatorRadioButton

    def _indicator(self) -> QSize:
        st = self.style()
        return QSize(st.pixelMetric(QStyle.PixelMetric.PM_ExclusiveIndicatorWidth, None, self),
                     st.pixelMetric(QStyle.PixelMetric.PM_ExclusiveIndicatorHeight, None, self))


def narrow_combos(root, chars: int = 18) -> None:
    """Drop-downs size to their longest choice; cap them so one long item
    can't make a page wider than the window (0.48)."""
    for c in root.findChildren(QComboBox):
        if not c.isEditable():
            c.setMinimumContentsLength(chars)
            c.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
