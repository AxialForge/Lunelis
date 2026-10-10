"""
The stack tray (0.45): select a burst, stack or timelapse tile in the
Library and its frames appear in a strip above the grid - without opening
the stack. Double-click a frame to open it; right-click to make it the cover.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu, QPushButton, QVBoxLayout, QWidget

from lunelis.raw.thumbnails import cache_rel_path

EDGE = 96
ID = Qt.ItemDataRole.UserRole
KIND_WORDS = {"burst": "Burst", "chosen_burst": "Burst", "timelapse": "Timelapse"}


class StackTray(QWidget):
    open_photo = Signal(int)
    make_cover = Signal(int)
    open_stack = Signal()                  # Open / close stack (S)

    def __init__(self, conn, thumbs, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Toolbar")
        self.conn, self.thumbs = conn, thumbs
        self.stack_id: int | None = None
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 6, 24, 6)
        v.setSpacing(4)
        head = QHBoxLayout()
        self.title = QLabel(objectName="SectionTitle")
        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(QPushButton("Open the stack (S)", clicked=lambda: self.open_stack.emit()))
        # A drawn icon (0.53): the "×" was squeezed out of a 30 px button by its own padding.
        from lunelis.ui import icons, theme
        close = QPushButton(clicked=lambda: self.hide())
        close.setIcon(icons.icon("close", theme.current().text, size=16))
        close.setIconSize(QSize(16, 16))
        close.setFixedSize(34, 34)
        close.setToolTip("Hide the tray")
        self.close_b = close
        head.addWidget(close)
        v.addLayout(head)
        self.strip = QListWidget()
        self.strip.setViewMode(QListWidget.ViewMode.IconMode)
        self.strip.setFlow(QListWidget.Flow.LeftToRight)
        self.strip.setWrapping(False)
        self.strip.setIconSize(QSize(EDGE, EDGE))
        self.strip.setFixedHeight(EDGE + 26)
        self.strip.setMovement(QListWidget.Movement.Static)
        self.strip.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.strip.itemDoubleClicked.connect(lambda it: self.open_photo.emit(it.data(ID)))
        self.strip.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.strip.customContextMenuRequested.connect(self._menu)
        v.addWidget(self.strip)
        self.thumbs.ready.connect(self._thumb_ready)
        self.hide()

    def show_stack(self, stack_id: int | None) -> None:
        if stack_id is None:
            self.stack_id = None
            self.hide()
            return
        if stack_id == self.stack_id and self.isVisible():
            return
        self.stack_id = stack_id
        from lunelis import stacks
        ids = stacks.members(self.conn, stack_id)
        kind = self.conn.execute("SELECT kind FROM stacks WHERE id = ?", (stack_id,)).fetchone()
        word = KIND_WORDS.get(kind[0] if kind else "", "Stack")
        self.title.setText(f"{word} of {len(ids):,} frames - double-click one to open it")
        self.strip.clear()
        rel = dict(self.conn.execute(
            f"SELECT id, thumbnail_path FROM files WHERE id IN ({','.join('?' * len(ids))})", ids)) if ids else {}
        for n, fid in enumerate(ids, 1):
            it = QListWidgetItem(str(n))
            it.setData(ID, fid)
            pix = self.thumbs.get(fid, rel.get(fid) or cache_rel_path(fid))
            if pix is not None:
                it.setIcon(QIcon(pix))
            self.strip.addItem(it)
        self.show()

    def _thumb_ready(self, fid: int) -> None:
        if not self.isVisible():
            return
        for i in range(self.strip.count()):
            it = self.strip.item(i)
            if it.data(ID) == fid:
                pix = self.thumbs.get(fid, cache_rel_path(fid))
                if isinstance(pix, QPixmap):
                    it.setIcon(QIcon(pix))
                return

    def _menu(self, pos) -> None:
        it = self.strip.itemAt(pos)
        if it is None:
            return
        m = QMenu(self)
        m.addAction("Open", lambda: self.open_photo.emit(it.data(ID)))
        m.addAction("Make this the stack cover", lambda: self.make_cover.emit(it.data(ID)))
        m.exec(self.strip.mapToGlobal(pos))
