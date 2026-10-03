"""Small shared widget helpers."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidget


def row_toggles(lw: QListWidget) -> None:
    """A tick-box list where clicking anywhere on a row (or Space/Enter) ticks
    or unticks it - not just the 16-pixel box. Items keep their check state
    but aren't Qt-"user checkable", so one click is one toggle."""
    def flip(item) -> None:
        if item is None or item.data(Qt.ItemDataRole.CheckStateRole) is None:
            return
        on = item.checkState() == Qt.CheckState.Checked
        item.setCheckState(Qt.CheckState.Unchecked if on else Qt.CheckState.Checked)

    lw.itemClicked.connect(flip)
    lw.itemActivated.connect(flip)
    lw.setCursor(Qt.CursorShape.PointingHandCursor)
