"""
The Ask bar (Library > Ask, Ctrl+Shift+F): a sentence in, photos out, with
chips that show how the sentence was read. Click a chip's x to ask again
without that part. Esc closes it and goes back to the whole library.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget

from lunelis import ask


class AskBar(QWidget):
    asked = Signal(object)              # ask.Asked
    closed = Signal()

    def __init__(self, conn, parent=None) -> None:
        super().__init__(parent)
        self.conn = conn
        self.setObjectName("AskBar")
        self.current: ask.Asked | None = None
        h = QHBoxLayout(self)
        h.setContentsMargins(16, 6, 16, 6)
        h.addWidget(QLabel("Ask:", objectName="ToolLabel"))
        self.box = QLineEdit(placeholderText="e.g. sunset on a beach, A7R V, 2024  ·  4 stars picks from June 2024")
        self.box.setClearButtonEnabled(True)
        self.box.returnPressed.connect(self.submit)
        h.addWidget(self.box, 2)
        self.chips = QHBoxLayout()
        self.chips.setSpacing(6)
        h.addLayout(self.chips, 3)
        self.note = QLabel(objectName="Help")
        h.addWidget(self.note)
        close = QPushButton("Close", clicked=self.close_bar)
        close.setFlat(True)
        h.addWidget(close)

    def open(self) -> None:
        self.show()
        self.box.setFocus()
        self.box.selectAll()

    def close_bar(self) -> None:
        self.hide()
        self.current = None
        self._clear_chips()
        self.closed.emit()

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.close_bar()
        else:
            super().keyPressEvent(e)

    def submit(self) -> None:
        text = self.box.text().strip()
        if not text:
            return
        self.show_asked(ask.parse(self.conn, text))

    def show_asked(self, asked: ask.Asked) -> None:
        self.current = asked
        self._clear_chips()
        for i, chip in enumerate(asked.chips):
            b = QPushButton(f"{chip.text}  ✕", objectName="Chip")
            b.setToolTip("Ask again without this")
            b.clicked.connect(lambda _=False, n=i: self.show_asked(self.current.without(n)))
            self.chips.addWidget(b)
        self.chips.addStretch(1)
        self.asked.emit(asked)

    def _clear_chips(self) -> None:
        while self.chips.count():
            it = self.chips.takeAt(0)
            if it.widget() is not None:
                it.widget().deleteLater()

    def say(self, text: str) -> None:
        self.note.setText(text)
