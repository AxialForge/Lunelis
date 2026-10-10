"""
The mouse wheel scrolls a panel; it doesn't change what it passes over (0.53).

Qt gives the wheel to the control under the pointer first: scrolling down the
Edit panel changed whichever slider, number box or drop-down the pointer
crossed - an edit just made was quietly altered by scrolling past it ("my
edits don't stick"). Inside a scroll area, the wheel now always goes to the
scroll area. Controls are still changed by dragging, clicking, typing and
the arrow keys. A slider that isn't in a scroll area (the library's grid
size) keeps its wheel.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QAbstractScrollArea, QAbstractSlider, QAbstractSpinBox, QApplication, QComboBox, QScrollBar

GUARDED = (QAbstractSlider, QAbstractSpinBox, QComboBox)


def scroll_area_of(widget) -> QAbstractScrollArea | None:
    """The scrolling panel a control sits in (not a list or a text box of its own)."""
    from PySide6.QtWidgets import QScrollArea
    p = widget.parentWidget()
    while p is not None:
        if isinstance(p, QScrollArea):
            return p
        p = p.parentWidget()
    return None


class WheelGuard(QObject):
    def eventFilter(self, obj, event) -> bool:
        if event.type() != QEvent.Type.Wheel or not isinstance(obj, GUARDED) or isinstance(obj, QScrollBar):
            return False
        if isinstance(obj, QComboBox) and obj.view().isVisible():
            return False                              # an open drop-down list scrolls itself
        area = scroll_area_of(obj)
        if area is None:
            return False
        QApplication.sendEvent(area.viewport(), event)
        return True


def install(app: QApplication | None = None) -> WheelGuard | None:
    """Once per application."""
    app = app or QApplication.instance()
    if app is None:
        return None
    guard = getattr(app, "_lunelis_wheel_guard", None)
    if guard is None:
        guard = WheelGuard(app)
        app.installEventFilter(guard)
        app._lunelis_wheel_guard = guard
    return guard
