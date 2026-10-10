"""
The Google Takeout page (sidebar > Bring in & organize): tick what comes
into the new Library from a Takeout export (takeout_review.py).

A tree of years, then albums, then photos and videos, each with a tick box -
ticking a year or an album ticks everything in it. Marks: "in your library"
(an identical or near-identical copy outside the Takeout folders - unticked
to begin with), "Google's edit" (-edited copies) and "repeat" ((1) names).
The selected photo is shown on the right. The JSON files never go to the
Library; their dates, places and descriptions are already in the catalog.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton, QSplitter, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from lunelis.importers import takeout_review as tr

ID = Qt.ItemDataRole.UserRole
FILTERS = (("Everything", None), ("Already in your library", "in_library"), ("Google's edits", "edited"),
           ("Repeats - (1) names", "numbered"), ("Unticked", "unticked"))


class TakeoutView(QWidget):
    add_folder = Signal(str)               # a Takeout folder to add as a source (then scanned)
    show_ids = Signal(list, str)

    def __init__(self, conn, parent=None) -> None:
        super().__init__(parent)
        self.conn = conn
        self.items: list[tr.Item] = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        head = QWidget(objectName="Toolbar")
        head.setFixedHeight(64)
        h = QHBoxLayout(head)
        h.setContentsMargins(24, 0, 24, 0)
        h.addWidget(QLabel("Google Takeout", objectName="PageTitle"))
        h.addSpacing(12)
        self.summary = QLabel(objectName="Count")
        from PySide6.QtWidgets import QSizePolicy
        self.summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)   # shrinks, never widens the page
        h.addWidget(self.summary, 1)
        self.filter = QComboBox()
        for label, key in FILTERS:
            self.filter.addItem(label, key)
        self.filter.currentIndexChanged.connect(self._fill)
        h.addWidget(self.filter)
        h.addWidget(QPushButton("Add a Takeout folder…", clicked=self._add))
        outer.addWidget(head)
        self.note = QLabel(
            "Tick what should come into your new Library. Point Lunelis at the folder Unpacker V2 extracted "
            "the export to; once it's scanned, its photos and videos are listed here by year and album. "
            "What's already in your library is unticked to begin with. Only photos and videos are copied - "
            "the JSON files stay behind, their dates and places are already in the catalog.",
            objectName="Help", wordWrap=True)
        self.note.setContentsMargins(24, 8, 24, 4)
        outer.addWidget(self.note)
        split = QSplitter()
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Year / album / photo", "Photos", "Marks"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.setColumnWidth(1, 80)
        self.tree.setColumnWidth(2, 220)
        self.tree.itemChanged.connect(self._changed)
        self.tree.itemExpanded.connect(self._thumbs_for)      # small pictures, made when an album opens
        from PySide6.QtCore import QSize
        self.tree.setIconSize(QSize(48, 48))
        self.tree.currentItemChanged.connect(self._preview)
        split.addWidget(self.tree)
        side = QWidget()
        sv = QVBoxLayout(side)
        sv.setContentsMargins(12, 12, 12, 12)
        self.pic = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.pic.setMinimumSize(260, 200)
        sv.addWidget(self.pic)
        self.info = QLabel(objectName="Help", wordWrap=True)
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        sv.addWidget(self.info)
        sv.addStretch(1)
        split.addWidget(side)
        split.setSizes([700, 320])
        outer.addWidget(split, 1)
        self._filling = False

    def refresh(self) -> None:
        self.items = tr.items(self.conn)
        self._fill()

    def _visible(self) -> list[tr.Item]:
        f = self.filter.currentData()
        if f is None:
            return self.items
        if f == "unticked":
            return [i for i in self.items if not i.included]
        return [i for i in self.items if getattr(i, f)]

    def _fill(self) -> None:
        if getattr(self, "_sync_due", False):
            self._sync()                              # a tick not saved yet goes in first
        self._filling = True
        self.tree.clear()
        c = tr.counts(self.items)
        self.summary.setText(
            f"{c['total']:,} photos and videos · {c['included']:,} ticked · {c['in_library']:,} already in "
            f"your library" if c["total"] else "No Google Takeout folder yet - add the one Unpacker V2 extracted")
        for year, albums in tr.tree(self._visible()).items():
            y = QTreeWidgetItem([year, "", ""])
            y.setFlags(y.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
            self.tree.addTopLevelItem(y)
            ny = 0
            for album, its in albums.items():
                a = QTreeWidgetItem([album, f"{len(its):,}", ""])
                a.setFlags(a.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
                y.addChild(a)
                for it in its:
                    marks = ", ".join(m for m, on in (("in your library", it.in_library), ("Google's edit", it.edited),
                                                       ("repeat", it.numbered)) if on)
                    leaf = QTreeWidgetItem([it.filename, "", marks])
                    if it.thumbnail:
                        leaf.setData(0, Qt.ItemDataRole.UserRole + 1, it.thumbnail)
                    leaf.setFlags(leaf.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    leaf.setData(0, ID, it.file_id)
                    leaf.setCheckState(0, Qt.CheckState.Checked if it.included else Qt.CheckState.Unchecked)
                    a.addChild(leaf)
                ny += len(its)
            y.setText(1, f"{ny:,}")
        self._filling = False

    def _thumbs_for(self, parent: QTreeWidgetItem) -> None:
        from PySide6.QtGui import QIcon
        from lunelis import paths
        self._filling = True
        for k in range(parent.childCount()):
            leaf = parent.child(k)
            rel = leaf.data(0, Qt.ItemDataRole.UserRole + 1)
            if rel and leaf.icon(0).isNull():
                pix = QPixmap(str(paths.THUMBNAIL_CACHE / rel))
                if not pix.isNull():
                    leaf.setIcon(0, QIcon(pix.scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatio,
                                                     Qt.TransformationMode.SmoothTransformation)))
        self._filling = False

    def _leaves(self, item: QTreeWidgetItem) -> list[QTreeWidgetItem]:
        if item.childCount() == 0:
            return [item] if item.data(0, ID) is not None else []
        out = []
        for k in range(item.childCount()):
            out += self._leaves(item.child(k))
        return out

    def _changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._filling or column != 0:
            return
        state = item.checkState(0)
        if state == Qt.CheckState.PartiallyChecked:
            return
        # Ticking a year makes Qt tick every photo in it, one signal each (0.52:
        # 16,000 photos meant 16,000 catalog writes and recounts - the window
        # froze for minutes). Every signal just asks for one sync, run once Qt
        # is done: it reads the boxes and writes what changed in one go.
        if not getattr(self, "_sync_due", False):
            self._sync_due = True
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, self._sync)

    def _sync(self) -> None:
        self._sync_due = False
        by_id = {i.file_id: i for i in self.items}
        changed: dict[bool, list[int]] = {True: [], False: []}
        for k in range(self.tree.topLevelItemCount()):
            for leaf in self._leaves(self.tree.topLevelItem(k)):
                fid = leaf.data(0, ID)
                on = leaf.checkState(0) == Qt.CheckState.Checked
                it = by_id.get(fid)
                if it is not None and it.included != on:
                    it.included = on
                    changed[on].append(fid)
        for on, ids in changed.items():
            if ids:
                tr.set_included(self.conn, ids, on)
        c = tr.counts(self.items)
        self.summary.setText(f"{c['total']:,} photos and videos · {c['included']:,} ticked · "
                             f"{c['in_library']:,} already in your library")

    def _preview(self, cur: QTreeWidgetItem | None, _prev=None) -> None:
        self.pic.clear()
        self.info.setText("")
        fid = cur.data(0, ID) if cur is not None else None
        it = next((i for i in self.items if i.file_id == fid), None) if fid is not None else None
        if it is None:
            return
        if it.thumbnail:
            from lunelis import paths
            pix = QPixmap(str(paths.THUMBNAIL_CACHE / it.thumbnail))
            if not pix.isNull():
                self.pic.setPixmap(pix.scaled(self.pic.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation))
        self.info.setText(f"{it.filename}\n{tr.path_of(self.conn, it)}"
                          + ("\nAn identical or near-identical copy is already in your library." if it.in_library else ""))

    def _add(self) -> None:
        picked = QFileDialog.getExistingDirectory(self, "The folder Unpacker V2 extracted the Takeout export to")
        if picked:
            self.add_folder.emit(picked)
