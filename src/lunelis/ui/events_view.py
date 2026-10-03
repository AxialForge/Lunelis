"""
The Events page: your events, and suggestions to accept or dismiss.

Suggestions are worked out on a background thread (lunelis.events.suggest,
~0.4 s for the real 159k-file catalog) and never change anything until the
user ticks some and presses Create. Names are editable before creating.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QMessageBox, QPushButton,
    QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from lunelis import paths
from lunelis.catalog.schema import open_catalog
from lunelis.events import model, suggest
from lunelis.settings import Settings

SOURCE_TEXT = {"manual": "Made by you", "folder": "Folder name", "suggested": "Suggested",
               "import": "Named import"}


class SuggestWorker(QObject):
    done = Signal(object)            # SuggestResult | Exception

    def __init__(self, gap_hours: int, min_photos: int) -> None:
        super().__init__()
        self.gap_hours, self.min_photos = gap_hours, min_photos

    def run(self) -> None:
        conn = open_catalog(paths.DEFAULT_CATALOG_PATH)
        try:
            self.done.emit(suggest.suggest(conn, gap_hours=self.gap_hours, min_photos=self.min_photos))
        except Exception as e:       # shown, never fatal
            self.done.emit(e)
        finally:
            conn.close()


def _item(text: str, align_right: bool = False, editable: bool = False) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
    if editable:
        flags |= Qt.ItemFlag.ItemIsEditable
    it.setFlags(flags)
    if align_right:
        it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return it


class EventsView(QWidget):
    show_event = Signal(int, str)    # event id, name -> the library, filtered to it
    back = Signal()                  # to the Albums page
    changed = Signal()               # events were created/removed (grid may show them)

    def __init__(self, conn, parent=None) -> None:
        super().__init__(parent)
        self.conn = conn
        self.events: list[model.Event] = []
        self.suggestions: list[suggest.Suggestion] = []
        self._thread: QThread | None = None
        self._searched = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = QWidget(objectName="Toolbar")
        head.setFixedHeight(64)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(24, 0, 24, 0)
        title = QLabel("Events")
        title.setObjectName("PageTitle")
        back = QPushButton("‹  Albums", clicked=lambda: self.back.emit())
        back.setFlat(True)
        hl.addWidget(back)
        hl.addWidget(title)
        hl.addStretch(1)
        self.count = QLabel(objectName="Count")
        hl.addWidget(self.count)
        outer.addWidget(head)

        split = QSplitter(Qt.Orientation.Vertical)
        split.setChildrenCollapsible(False)

        # --- your events --------------------------------------------------------------
        top = QWidget()
        tv = QVBoxLayout(top)
        tv.setContentsMargins(24, 16, 24, 8)
        cap = QLabel("YOUR EVENTS", objectName="FilterLabel")
        tv.addWidget(cap)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Event", "Dates", "Photos & videos", "Made from"])
        self._setup(self.table, stretch=0)
        self.table.doubleClicked.connect(lambda _: self._show_selected())
        tv.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.show_b = QPushButton("Show photos", clicked=self._show_selected)
        self.rename_b = QPushButton("Rename…", clicked=self._rename)
        self.remove_b = QPushButton("Remove event", clicked=self._remove)
        self.remove_b.setToolTip("Removes the event only - its photos stay exactly as they are")
        for b in (self.show_b, self.rename_b, self.remove_b):
            row.addWidget(b)
        row.addStretch(1)
        hint = QLabel("Make one from photos in the library: select them, then Photo ▸ Event ▸ "
                      "New event from selection (Ctrl+E).", objectName="Help")
        row.addWidget(hint)
        tv.addLayout(row)
        split.addWidget(top)

        # --- suggestions --------------------------------------------------------------
        bottom = QWidget()
        bv = QVBoxLayout(bottom)
        bv.setContentsMargins(24, 8, 24, 16)
        bv.addWidget(QLabel("SUGGESTIONS", objectName="FilterLabel"))
        controls = QHBoxLayout()
        s = Settings(conn)
        controls.addWidget(QLabel("From your folder names, and from photos taken more than"))
        self.gap = QSpinBox(minimum=1, maximum=24 * 14, value=s.get("event_gap_hours"), suffix=" hours")
        self.gap.valueChanged.connect(lambda v: Settings(self.conn).set("event_gap_hours", v))
        controls.addWidget(self.gap)
        controls.addWidget(QLabel("apart, with at least"))
        self.min_photos = QSpinBox(minimum=2, maximum=100_000, value=s.get("event_min_photos"), suffix=" photos")
        self.min_photos.valueChanged.connect(lambda v: Settings(self.conn).set("event_min_photos", v))
        controls.addWidget(self.min_photos)
        self.find_b = QPushButton("Find suggestions", clicked=self.find)
        controls.addWidget(self.find_b)
        controls.addStretch(1)
        bv.addLayout(controls)
        self.status = QLabel(objectName="Help")
        bv.addWidget(self.status)
        self.sug = QTableWidget(0, 5)
        self.sug.setHorizontalHeaderLabels(["", "Name (click to edit)", "Dates", "Photos & videos", "Found in"])
        self._setup(self.sug, stretch=4)
        self.sug.setEditTriggers(QAbstractItemView.EditTrigger.AllEditTriggers)
        self.sug.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.sug.setColumnWidth(1, 260)
        bv.addWidget(self.sug, 1)
        row = QHBoxLayout()
        row.addWidget(QPushButton("Tick all from folder names", clicked=lambda: self._tick("folder")))
        row.addWidget(QPushButton("Tick none", clicked=lambda: self._tick(None)))
        row.addStretch(1)
        self.dismiss_b = QPushButton("Dismiss ticked", clicked=self._dismiss)
        self.dismiss_b.setToolTip("They won't be suggested again")
        row.addWidget(self.dismiss_b)
        self.create_b = QPushButton("Create ticked events", clicked=self._create)
        self.create_b.setObjectName("Primary")
        row.addWidget(self.create_b)
        bv.addLayout(row)
        split.addWidget(bottom)
        split.setSizes([320, 480])
        outer.addWidget(split, 1)
        self.refresh()

    @staticmethod
    def _setup(t: QTableWidget, stretch: int) -> None:
        t.verticalHeader().hide()
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setWordWrap(False)
        t.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        hh = t.horizontalHeader()
        for c in range(t.columnCount()):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(stretch, QHeaderView.ResizeMode.Stretch)

    # --- your events -----------------------------------------------------------------

    def refresh(self) -> None:
        self.events = model.all_events(self.conn)
        self.table.clearContents()
        self.table.setRowCount(len(self.events))
        for i, e in enumerate(self.events):
            self.table.setItem(i, 0, _item(e.name))
            self.table.setItem(i, 1, _item(e.dates()))
            self.table.setItem(i, 2, _item(f"{e.photos:,}", align_right=True))
            self.table.setItem(i, 3, _item(SOURCE_TEXT.get(e.source, e.source)))
        n = len(self.events)
        self.count.setText(f"{n:,} event{'s' if n != 1 else ''}")
        for b in (self.show_b, self.rename_b, self.remove_b):
            b.setEnabled(bool(self.events))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._searched:          # first visit: look for suggestions
            self._searched = True
            self.find()

    def _current(self) -> model.Event | None:
        row = self.table.currentRow()
        return self.events[row] if 0 <= row < len(self.events) else None

    def _show_selected(self) -> None:
        e = self._current()
        if e:
            self.show_event.emit(e.id, e.name)

    def _rename(self) -> None:
        e = self._current()
        if not e:
            return
        name, ok = QInputDialog.getText(self, "Rename event", "Name:", text=e.name)
        if ok and name.strip():
            model.rename(self.conn, e.id, name)
            self.refresh()

    def _remove(self) -> None:
        e = self._current()
        if not e:
            return
        if QMessageBox.question(self, "Remove event?",
                                f"Remove the event \"{e.name}\"?\n\nIts {e.photos:,} photos stay exactly where "
                                "and as they are - only the event goes.") != QMessageBox.StandardButton.Yes:
            return
        model.delete(self.conn, e.id)
        self.refresh()
        self.changed.emit()
        self.find()                  # its photos can be suggested again

    # --- suggestions -------------------------------------------------------------------

    def find(self) -> None:
        if self._thread is not None:
            return
        self.find_b.setEnabled(False)
        self.status.setText("Looking for events in your folders and capture times…")
        self._thread = QThread(self)
        self._worker = SuggestWorker(self.gap.value(), self.min_photos.value())
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.done.connect(self._found)
        self._thread.start()

    def _found(self, result) -> None:
        if isinstance(self._thread, QThread):
            self._thread.quit()
            self._thread.wait()
        self._thread = None
        self.find_b.setEnabled(True)
        if isinstance(result, Exception):
            self.status.setText(f"Couldn't look for suggestions: {result}")
            return
        self.suggestions = result.suggestions
        folders = sum(1 for s in self.suggestions if s.kind == "folder")
        self.status.setText(
            f"{folders:,} from folder names, {len(self.suggestions) - folders:,} from gaps in capture time. "
            "Tick the ones you want, fix any names, then Create.")
        self.sug.blockSignals(True)
        self.sug.clearContents()
        self.sug.setRowCount(len(self.suggestions))
        for i, s in enumerate(self.suggestions):
            tick = QTableWidgetItem()
            tick.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            tick.setCheckState(Qt.CheckState.Unchecked)
            self.sug.setItem(i, 0, tick)
            self.sug.setItem(i, 1, _item(s.name, editable=True))
            self.sug.setItem(i, 2, _item(s.dates()))
            self.sug.setItem(i, 3, _item(f"{len(s.file_ids):,}", align_right=True))
            why = _item(s.why())
            if s.kind == "folder":
                why.setToolTip("\n".join(s.folders))
            self.sug.setItem(i, 4, why)
        self.sug.blockSignals(False)

    def _tick(self, kind: str | None) -> None:
        for i, s in enumerate(self.suggestions):
            on = kind is not None and s.kind == kind
            self.sug.item(i, 0).setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    def _ticked(self) -> list[tuple[int, suggest.Suggestion]]:
        return [(i, s) for i, s in enumerate(self.suggestions)
                if self.sug.item(i, 0).checkState() == Qt.CheckState.Checked]

    def _create(self) -> None:
        picked = self._ticked()
        if not picked:
            QMessageBox.information(self, "Nothing ticked", "Tick the suggestions you want to keep.")
            return
        for i, s in picked:
            name = (self.sug.item(i, 1).text() or "").strip() or s.name
            suggest.accept(self.conn, s, name)
        self.refresh()
        self.changed.emit()
        self.find()

    def _dismiss(self) -> None:
        picked = self._ticked()
        if not picked:
            return
        suggest.dismiss(self.conn, [s.key for _, s in picked])
        self.find()
