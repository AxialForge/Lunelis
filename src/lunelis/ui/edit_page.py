"""
The Edit page (sidebar > Photos > Edit): an editing workspace.

The photo large with the Edit panel full height - a DetailView in
`workspace` mode, always editing - and a bar to choose which photos to work
through and apply things to all of them at once:

- **Photos:** the library's selection, whatever the library shows now (its
  search and filters), edited photos, picks, 4-5 stars, or recent imports.
  The filmstrip under the photo holds that set; videos are left out.
- **Batch tools:** copy this photo's edit, paste it onto every photo in the
  set, reset them all, export them all. MainWindow does the work (the same
  code as Photo > Edit and Photo > Export), so undo, re-rendering and the
  confirmations behave the same everywhere.
"""
from __future__ import annotations

import sqlite3

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from lunelis.ui.detail_view import DetailView
from lunelis.ui.library import Filter, LibraryIndex

SOURCES = (
    ("selection", "Selected in the library"),
    ("view", "What the library shows now"),
    ("edited", "Edited photos"),
    ("picks", "Picks"),
    ("favorites", "4 and 5 stars"),
    ("recent", "Recently imported"),
)


class EditPage(QWidget):
    copy_edit = Signal(int)              # file id whose edit to copy
    paste_all = Signal(list)             # file ids
    reset_all = Signal(list)
    export_all = Signal(list)

    def __init__(self, conn: sqlite3.Connection, parent=None) -> None:
        super().__init__(parent)
        self.conn = conn
        self._selection: list[int] = []
        self._view_filter = Filter()
        self._sort: str | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        bar = QWidget(objectName="Toolbar")
        bar.setFixedHeight(56)
        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 0, 16, 0)
        h.setSpacing(10)
        h.addWidget(QLabel("Edit", objectName="PageTitle"))
        h.addSpacing(10)
        h.addWidget(QLabel("Photos:", objectName="ToolLabel"))
        self.source = QComboBox()
        for key, label in SOURCES:
            self.source.addItem(label, key)
        self.source.currentIndexChanged.connect(lambda _i: self.load())
        h.addWidget(self.source)
        self.count = QLabel(objectName="Count")
        h.addWidget(self.count)
        h.addStretch(1)
        self.copy_b = QPushButton("Copy this edit", clicked=self._copy)
        self.copy_b.setToolTip("Copy this photo's edit settings (Ctrl+Shift+C)")
        self.paste_b = QPushButton("Paste to all", clicked=lambda: self.paste_all.emit(self.ids()))
        self.paste_b.setToolTip("Paste the copied edit onto every photo here; each keeps its own crop and rotation")
        self.reset_b = QPushButton("Reset all…", clicked=lambda: self.reset_all.emit(self.ids()))
        self.export_b = QPushButton("Export all…", objectName="Primary",
                                    clicked=lambda: self.export_all.emit(self.ids()))
        for b in (self.copy_b, self.paste_b, self.reset_b, self.export_b):
            h.addWidget(b)
        outer.addWidget(bar)

        self.view = DetailView(conn, workspace=True)
        outer.addWidget(self.view, 1)

        self.empty = QLabel(objectName="Help")
        self.empty.setWordWrap(True)
        self.empty.setContentsMargins(24, 24, 24, 24)
        outer.addWidget(self.empty)
        self.empty.hide()

    # --- the set of photos --------------------------------------------------------------------

    def set_library_context(self, selection: list[int], view_filter: Filter, sort: str | None) -> None:
        """What 'Selected in the library' and 'What the library shows now' mean right now."""
        self._selection = list(selection)
        self._view_filter = view_filter
        self._sort = sort

    def choose_default(self) -> None:
        """Opening the page: the library's selection if there is one, else what it shows."""
        key = "selection" if self._selection else "view"
        self.source.blockSignals(True)
        self.source.setCurrentIndex(self.source.findData(key))
        self.source.blockSignals(False)

    def _filter(self) -> Filter:
        key = self.source.currentData()
        if key == "selection":
            return Filter(ids=tuple(self._selection))
        if key == "view":
            return self._view_filter
        return {"edited": Filter(query="edited"), "picks": Filter(flag="pick"),
                "favorites": Filter(min_stars=4), "recent": Filter(auto="recent")}[key]

    def load(self) -> None:
        from dataclasses import replace
        flt = replace(self._filter(), hide_videos=True)           # videos can't be edited
        idx = LibraryIndex()
        idx.load(self.conn, self._sort, flt)
        n = len(idx)
        self.count.setText(f"{n:,} photo{'s' if n != 1 else ''}")
        for b in (self.copy_b, self.paste_b, self.reset_b, self.export_b):
            b.setEnabled(n > 0)
        self.view.setVisible(n > 0)
        self.empty.setVisible(n == 0)
        if not n:
            self.view.edit.finish()
            self.empty.setText("Nothing to edit here. Select photos in the Library and come back, or choose "
                               "other photos above.")
            return
        keep = self.view.info.file_id if self.view.info else None
        pos = idx.position(keep) if keep is not None else -1
        self.view.open(idx, pos if pos >= 0 else 0)
        self.view.set_editing(True)

    def ids(self) -> list[int]:
        idx = self.view.index
        return [idx.file_id(i) for i in range(len(idx))]

    def current(self) -> int | None:
        return self.view.info.file_id if self.view.info else None

    def _copy(self) -> None:
        fid = self.current()
        if fid is not None:
            self.view.edit.save()
            self.copy_edit.emit(fid)

    def leave(self) -> None:
        """Another page is opening: save the photo being edited."""
        self.view.edit.finish()
