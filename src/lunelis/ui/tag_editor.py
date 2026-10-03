"""
Tag widgets:

- TagBox: a photo's (or a selection's) tags as chips with an x, and a box
  to type new ones - autocompleting from existing tags; commas add several;
  "Places > Ohio" (or "Places|Ohio") nests.
- TagDialog: Photo > Tags > Tag photos... (Ctrl+T) for a selection: every
  tag any of them has, "3 of 5" where only some do, and the box.
- TagFilter: the filter bar's Tag button - a searchable list of tags.
"""
from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QCompleter, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLayout, QLineEdit, QListWidget,
    QListWidgetItem, QMenu, QPushButton, QToolButton, QVBoxLayout, QWidget, QWidgetAction,
)

from lunelis.tags import model as tags


class FlowLayout(QLayout):
    """Chips left to right, wrapping (Qt has no flow layout of its own)."""

    def __init__(self, parent=None, spacing: int = 4) -> None:
        super().__init__(parent)
        self._items = []
        self._space = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._place(QRect(0, 0, width, 0), dry=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._place(rect, dry=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _place(self, rect: QRect, dry: bool) -> int:
        x, y, line = rect.x(), rect.y(), 0
        for it in self._items:
            hint = it.sizeHint()
            if x + hint.width() > rect.right() and line > 0:
                x, y, line = rect.x(), y + line + self._space, 0
            if not dry:
                it.setGeometry(QRect(x, y, hint.width(), hint.height()))
            x += hint.width() + self._space
            line = max(line, hint.height())
        return y + line - rect.y()


def _completer(conn, parent) -> QCompleter:
    c = QCompleter(tags.names(conn), parent)
    c.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
    c.setFilterMode(Qt.MatchFlag.MatchContains)
    return c


class TagBox(QWidget):
    added = Signal(list)                   # tag names
    removed = Signal(str)

    def __init__(self, conn, parent=None) -> None:
        super().__init__(parent)
        self.conn = conn
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        self.chips = QWidget()
        self.flow = FlowLayout(self.chips)
        v.addWidget(self.chips)
        self.edit = QLineEdit(placeholderText="Add a tag…  (commas for several, > to nest)")
        self.edit.setCompleter(_completer(conn, self.edit))
        self.edit.returnPressed.connect(self._add)
        v.addWidget(self.edit)

    def set_tags(self, counts: dict[str, int] | list[str], of: int = 1) -> None:
        """counts: tag -> how many of `of` photos have it (or a plain list)."""
        while self.flow.count():
            w = self.flow.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        if isinstance(counts, list):
            counts = {t: of for t in counts}
        for name, n in counts.items():
            text = name.replace(tags.SEP, " › ")
            if n < of:
                text += f"  ({n} of {of})"
            chip = QPushButton(f"{text}  ✕".replace("&", "&&"), objectName="Chip")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setToolTip("Remove this tag" + (" from all of them" if of > 1 else ""))
            chip.clicked.connect(lambda _=False, t=name: self.removed.emit(t))
            self.flow.addWidget(chip)
        self.chips.setVisible(bool(counts))
        self.edit.setCompleter(_completer(self.conn, self.edit))
        self.chips.updateGeometry()

    def _add(self) -> None:
        names = tags.split_input(self.edit.text())
        self.edit.clear()
        if names:
            self.added.emit(names)


class TagDialog(QDialog):
    """Tag a selection of photos."""

    def __init__(self, conn, file_ids: list[int], parent=None) -> None:
        super().__init__(parent)
        self.conn, self.file_ids = conn, list(file_ids)
        self.setWindowTitle("Tags")
        self.setMinimumWidth(460)
        v = QVBoxLayout(self)
        n = len(self.file_ids)
        v.addWidget(QLabel(f"Tags on the {n:,} selected photo{'s' if n != 1 else ''}:"))
        self.box = TagBox(conn)
        self.box.added.connect(self._added)
        self.box.removed.connect(self._removed)
        v.addWidget(self.box)
        recent = tags.recent(conn)
        if recent:
            row = QHBoxLayout()
            row.addWidget(QLabel("Recent:", objectName="Help"))
            for t in recent[:6]:
                b = QPushButton(tags.leaf(t).replace("&", "&&"), objectName="Chip")
                b.setToolTip(t.replace(tags.SEP, " › "))
                b.clicked.connect(lambda _=False, t=t: self._added([t]))
                row.addWidget(b)
            row.addStretch(1)
            v.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)
        v.addWidget(buttons)
        self.changed = False
        self._refresh()

    def _refresh(self) -> None:
        self.box.set_tags(tags.counts_in(self.conn, self.file_ids), len(self.file_ids))

    def _added(self, names: list[str]) -> None:
        tags.add(self.conn, self.file_ids, names)
        tags.remember_recent(self.conn, names)
        self.changed = True
        self._refresh()

    def _removed(self, name: str) -> None:
        tags.remove(self.conn, self.file_ids, name)
        self.changed = True
        self._refresh()


class TagFilter(QToolButton):
    """The filter bar's Tag button: pick a tag to show only its photos."""

    chosen = Signal(str)

    def __init__(self, conn, parent=None) -> None:
        super().__init__(parent)
        self.conn = conn
        self.setObjectName("FilterMenu")
        self.setText("Tag ▾")
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu_ = QMenu(self)
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(8, 8, 8, 8)
        self.search = QLineEdit(placeholderText="Find a tag…")
        self.search.textChanged.connect(self._fill)
        self.list = QListWidget()
        self.list.setMinimumSize(260, 280)
        self.list.itemActivated.connect(self._pick)
        self.list.itemClicked.connect(self._pick)
        v.addWidget(self.search)
        v.addWidget(self.list)
        act = QWidgetAction(self.menu_)
        act.setDefaultWidget(box)
        self.menu_.addAction(act)
        self.menu_.aboutToShow.connect(self._opened)
        self.setMenu(self.menu_)
        self._all: list[tuple[str, int]] = []

    def _opened(self) -> None:
        self._all = tags.all_tags(self.conn)
        self.search.clear()
        self._fill("")
        self.search.setFocus()

    def _fill(self, text: str) -> None:
        self.list.clear()
        q = text.strip().lower()
        for name, n in self._all:
            if q and q not in name.lower():
                continue
            depth = name.count(tags.SEP)
            item = QListWidgetItem(("    " * depth) + tags.leaf(name) + (f"   {n:,}" if n else ""))
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setToolTip(name.replace(tags.SEP, " › "))
            self.list.addItem(item)
        if not self._all:
            self.list.addItem(QListWidgetItem("No tags yet - add some with Ctrl+T"))

    def _pick(self, item: QListWidgetItem) -> None:
        name = item.data(Qt.ItemDataRole.UserRole)
        if name:
            self.menu_.close()
            self.chosen.emit(name)
