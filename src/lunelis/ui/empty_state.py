"""
A list, tree or table that says what to do when it's empty (0.48), instead of
a blank box - or one word per line, as an empty People grid did.

    box = EmptyStack(self.table)          # put `box` in the layout instead
    box.empty("No damaged files found - ...")   # the message, centred
    box.empty(None)                       # the table again
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QStackedWidget, QWidget


class EmptyStack(QStackedWidget):
    def __init__(self, view: QWidget, parent=None) -> None:
        super().__init__(parent)
        self.view = view
        self.note = QLabel(objectName="EmptyNote", wordWrap=True)
        self.note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.note.setContentsMargins(48, 24, 48, 24)
        self.note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.addWidget(view)
        self.addWidget(self.note)

    def empty(self, message: str | None) -> None:
        if message:
            self.note.setText(message)
            self.setCurrentWidget(self.note)
        else:
            self.setCurrentWidget(self.view)

    @property
    def showing_message(self) -> bool:
        return self.currentWidget() is self.note
