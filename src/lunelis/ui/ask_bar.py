"""
Ask (Library > Ask, Ctrl+Shift+F): a sentence in, photos out, with chips that
show how the sentence was read. Click a chip's x to ask again without that
part. Esc - or the Ask button again - closes it and goes back to the whole
library.

Since 0.55 Ask is a mode of the library's one search box, not a second row
under the filters. Two parts, both placed in the toolbar by the main window:

- `box`    the line you type in. It stands where the search box stands (the
           window swaps the two), so there is one box to look at.
- the bar  (this widget) what came back: the chips and a short note, beside
           the Ask button.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QWidget

from lunelis import ask

PLACEHOLDER = "Ask in a sentence: sunset on a beach, A7R V, 2024  ·  4 stars picks from June 2024"


class AskBar(QWidget):
    asked = Signal(object)              # ask.Asked
    closed = Signal()

    def __init__(self, conn, parent=None) -> None:
        super().__init__(parent)
        self.conn = conn
        self.setObjectName("AskBar")
        self.current: ask.Asked | None = None
        # Styled as the search box it takes the place of; "ask" lets the theme
        # mark the mode with an accent edge.
        self.box = QLineEdit(self, objectName="Search", placeholderText=PLACEHOLDER)
        self.box.setProperty("ask", True)
        self.box.setClearButtonEnabled(True)
        self.box.setMinimumWidth(120)
        self.box.setToolTip("Say what you're looking for and press Enter. Esc goes back to searching.")
        self.box.returnPressed.connect(self.submit)
        self.box.hide()
        # Esc in the box: it isn't this widget's child once it is in the toolbar,
        # so the key no longer reaches keyPressEvent below.
        QShortcut(QKeySequence("Escape"), self.box, activated=self.close_bar,
                  context=Qt.ShortcutContext.WidgetShortcut)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        self.chips = QHBoxLayout()
        self.chips.setSpacing(6)
        h.addLayout(self.chips)
        self.note = QLabel(objectName="Help")
        h.addWidget(self.note)
        h.addStretch(1)
        # Many chips are cut short at the bar's edge rather than widening the window.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def open(self) -> None:
        self.show()
        self.box.show()
        self.box.setFocus()
        self.box.selectAll()

    def close_bar(self) -> None:
        self.hide()
        self.box.hide()
        self.current = None
        self._clear_chips()
        self.note.setText("")
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
        self.asked.emit(asked)

    def _clear_chips(self) -> None:
        while self.chips.count():
            it = self.chips.takeAt(0)
            if it.widget() is not None:
                it.widget().deleteLater()

    def say(self, text: str) -> None:
        self.note.setText(text)
        self.note.setToolTip(text)             # the whole of it, when the bar is too narrow
