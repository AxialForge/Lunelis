"""
The Import page (Import mockup): pick a card or folder, preview which library
folders its photos will land in, then copy -> verify -> file them.

Two moments matter and are said plainly: "You can remove the card" (every
file staged and verified) and "Safe to format the card" (every file in the
library and verified again). Files are never renamed.
"""
from __future__ import annotations

import os
from collections import Counter
from datetime import date, datetime

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from lunelis import paths
from lunelis.catalog.schema import open_catalog
from lunelis.importing import ingest
from lunelis.importing.templates import PRESETS, Context, TemplateError, render
from lunelis.settings import Settings


def _gb(n: int) -> str:
    return f"{n / 1e9:,.1f} GB" if n >= 1e8 else f"{n / 1e6:,.0f} MB"


class PreviewWorker(QObject):
    """Reads capture dates from the card to show where everything will go."""

    done = Signal(object)          # list of (rel, size, taken datetime | None)

    def __init__(self, source: str) -> None:
        super().__init__()
        self.source = source

    def run(self) -> None:
        from lunelis.importers.metadata import read_file
        out = []
        try:
            for rel, size, _ in ingest.discover(self.source):
                taken = None
                try:
                    at = read_file(os.path.join(self.source, *rel.split("/"))).get("captured_at")
                    taken = datetime.fromisoformat(at[:19]) if at else None
                except Exception:
                    pass
                out.append((rel, size, taken))
        except OSError:
            pass
        self.done.emit(out)


class ImportWorker(QObject):
    progress = Signal(str, int, int, str)       # phase, done, total, file
    finished = Signal(int, object)              # import id, summary or exception

    def __init__(self, import_id: int | None, source: str | None = None, name: str = "") -> None:
        super().__init__()
        self.import_id = import_id
        self.source, self.name = source, name      # a new import: created here, not on the GUI thread
        self.stop = False

    def run(self) -> None:
        conn = open_catalog(paths.DEFAULT_CATALOG_PATH)
        try:
            cfg = ingest.load_settings(conn, paths.DATA_DIR)
            if self.import_id is None:
                self.progress.emit("Reading the card", 0, 0, "")
                self.import_id = ingest.create_import(conn, self.source, cfg, self.name)
            s = ingest.run(conn, self.import_id, cfg, should_stop=lambda: self.stop,
                           on_progress=self.progress.emit)
            self.finished.emit(self.import_id, s)
        except Exception as e:                   # waiting / no space / anything else: say so
            self.finished.emit(self.import_id or 0, e)
        finally:
            conn.close()


class ImportView(QWidget):
    imported = Signal(str)                       # destination folder that received files

    def __init__(self, conn, parent=None) -> None:
        super().__init__(parent)
        self.conn = conn
        self.source: str | None = None
        self.preview: list = []
        self._thread = None
        self._worker = None
        self._import_id: int | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = QWidget(objectName="Toolbar")
        head.setFixedHeight(64)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(24, 0, 24, 0)
        title = QLabel("Import")
        title.setObjectName("PageTitle")
        hl.addWidget(title)
        hl.addStretch(1)
        self.found = QLabel(objectName="Count")
        hl.addWidget(self.found)
        outer.addWidget(head)

        body = QHBoxLayout()
        body.setSpacing(0)
        left = QWidget(objectName="Toolbar")
        left.setFixedWidth(340)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(20, 20, 20, 20)
        cap = QLabel("MEMORY CARDS")
        cap.setObjectName("FilterLabel")
        lv.addWidget(cap)
        self.cards = QListWidget()
        self.cards.setMaximumHeight(110)
        self.cards.itemClicked.connect(lambda it: self.choose(it.data(Qt.ItemDataRole.UserRole)))
        lv.addWidget(self.cards)
        lv.addSpacing(8)
        cap = QLabel("DRIVES")
        cap.setObjectName("FilterLabel")
        lv.addWidget(cap)
        self.drives = QListWidget()
        self.drives.itemClicked.connect(self._drive_clicked)
        lv.addWidget(self.drives, 1)
        lv.addSpacing(8)
        cap = QLabel("RECENT FOLDERS")
        cap.setObjectName("FilterLabel")
        lv.addWidget(cap)
        self.recent = QListWidget()
        self.recent.setMaximumHeight(150)
        self.recent.itemClicked.connect(lambda it: self.choose(it.data(Qt.ItemDataRole.UserRole)))
        lv.addWidget(self.recent)
        lv.addWidget(QPushButton("Import from a folder…", clicked=self._choose_folder))
        body.addWidget(left)

        center = QVBoxLayout()
        center.setContentsMargins(24, 20, 24, 20)
        self.message = QLabel("Insert a memory card, or choose a folder to import from.")
        self.message.setWordWrap(True)
        self.message.setStyleSheet("font-size: 15px;")
        center.addWidget(self.message)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Will be filed into", "Files", "Size"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        center.addWidget(self.table, 1)
        self.bar = QProgressBar()
        self.bar.hide()
        center.addWidget(self.bar)
        body.addLayout(center, 1)
        wrap = QWidget()
        wrap.setLayout(body)
        outer.addWidget(wrap, 1)

        foot = QWidget(objectName="Toolbar")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(24, 12, 24, 12)
        fl.setSpacing(16)
        dest_box = QVBoxLayout()
        dest_box.addWidget(QLabel("Destination", objectName="FilterLabel"))
        self.dest = QLabel()
        self.dest.setStyleSheet("font-weight: 600;")
        dest_box.addWidget(self.dest)
        fl.addLayout(dest_box)
        fl.addWidget(QPushButton("Change…", clicked=self._choose_destination))
        tmpl_box = QVBoxLayout()
        tmpl_box.addWidget(QLabel("Folders", objectName="FilterLabel"))
        self.template = QComboBox()
        self.template.setEditable(True)
        self.template.setMinimumWidth(300)
        tmpl_box.addWidget(self.template)
        self.example = QLabel(objectName="FilterLabel")
        tmpl_box.addWidget(self.example)
        fl.addLayout(tmpl_box)
        name_box = QVBoxLayout()
        name_box.addWidget(QLabel("Event name (optional)", objectName="FilterLabel"))
        self.name = QLineEdit(placeholderText="e.g. Cleveland Air Show")
        self.name.textChanged.connect(self._update_preview)
        name_box.addWidget(self.name)
        fl.addLayout(name_box, 1)
        self.go = QPushButton(clicked=self._start)
        self.go.setObjectName("Primary")
        fl.addWidget(self.go)
        self.stop_b = QPushButton("Stop", clicked=self._stop)
        self.stop_b.hide()
        fl.addWidget(self.stop_b)
        self.clear_b = QPushButton("Clear the card…", clicked=self._clear_card)
        self.clear_b.setToolTip("Delete from the card the files that are now verified in your library")
        self.clear_b.hide()
        fl.addWidget(self.clear_b)
        self._clear_id: int | None = None
        self.profile_name = ""
        outer.addWidget(foot)
        self.template.currentTextChanged.connect(self._template_changed)
        self.refresh()

    # --- settings-backed fields ---------------------------------------------------

    def refresh(self) -> None:
        s = Settings(self.conn)
        self.dest.setText(s.get("import_destination") or "Not chosen yet - the first import asks")
        current = s.get("import_template")
        self.template.blockSignals(True)
        self.template.clear()
        for label, t in PRESETS.items():
            self.template.addItem(t, label)
            self.template.setItemData(self.template.count() - 1, label, Qt.ItemDataRole.ToolTipRole)
        if self.template.findText(current) < 0:
            self.template.addItem(current)
        self.template.setCurrentText(current)
        self.template.blockSignals(False)
        self.update_cards()
        self._update_preview()

    def _template_changed(self, text: str) -> None:
        try:
            render(text, Context(datetime(2026, 6, 19)))
        except TemplateError as e:
            self.found.setText(f"Folder template: {e}")
            return
        Settings(self.conn).set("import_template", text)
        self._update_preview()

    def _destination(self) -> str:
        return Settings(self.conn).get("import_destination") or ""

    def _choose_destination(self) -> None:
        picked = QFileDialog.getExistingDirectory(self, "Import into which library folder?",
                                                  self._destination())
        if picked:
            Settings(self.conn).set("import_destination", os.path.normpath(picked))
            self.refresh()

    # --- sources ---------------------------------------------------------------------

    def update_cards(self) -> None:
        drives = ingest.removable_drives_with_media()
        self.cards.clear()
        for d in drives:
            serial, label = ingest.volume_info(d)
            item = QListWidgetItem(f"{label or 'Memory card'} ({d.rstrip(chr(92))})")
            item.setData(Qt.ItemDataRole.UserRole, d)
            self.cards.addItem(item)
        if not drives:
            self.cards.addItem("No memory card detected")
        self._fill_drives()

    def _fill_drives(self) -> None:
        # Every other drive: USB drives import whole; internal and network
        # drives open a folder picker on them (all of C:\ is never a photo import).
        self.drives.clear()
        cards = set(ingest.removable_drives_with_media())
        names = {"removable": "USB drive", "fixed": "Drive", "network": "Network drive"}
        for root, kind, label, free, total in ingest.all_drives():
            if root in cards or kind == "card":
                continue
            letter = root.rstrip(chr(92))
            size = f" · {free / 1e9:,.0f} of {total / 1e9:,.0f} GB free" if total else ""
            item = QListWidgetItem(f"{label or names.get(kind, 'Drive')} ({letter}){size}")
            item.setData(Qt.ItemDataRole.UserRole, (root, kind))
            item.setToolTip("Imports everything on it" if kind == "removable" else "Choose a folder on it")
            self.drives.addItem(item)
        self.recent.clear()
        for folder in Settings(self.conn).get("import_recent"):
            item = QListWidgetItem(os.path.basename(folder.rstrip(chr(92))) or folder)
            item.setToolTip(folder)
            item.setData(Qt.ItemDataRole.UserRole, folder)
            self.recent.addItem(item)
        if not self.recent.count():
            self.recent.addItem("Folders you import from appear here")

    def _drive_clicked(self, item) -> None:
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        root, kind = data
        if kind == "removable":
            self.choose(root)
            return
        picked = QFileDialog.getExistingDirectory(self, "Import photos from which folder?", root)
        if picked:
            self.choose(os.path.normpath(picked))

    def choose(self, source: str | None) -> None:
        if not source or self._thread is not None:
            return
        self.source = source
        if source not in ingest.removable_drives_with_media():
            s = Settings(self.conn)                  # remember it for Recent folders
            recent = [source] + [f for f in s.get("import_recent") if os.path.normcase(f) != os.path.normcase(source)]
            s.set("import_recent", recent[:6])
            self._fill_drives()
        self.preview = []
        self.table.setRowCount(0)
        self.clear_b.hide()
        self._clear_id = None
        self.message.setText(f"Reading {source}…")
        self._run(PreviewWorker(source), self._preview_ready)

    def _choose_folder(self) -> None:
        picked = QFileDialog.getExistingDirectory(self, "Import photos from which folder?")
        if picked:
            self.choose(os.path.normpath(picked))

    def _preview_ready(self, items: list) -> None:
        self._end_thread()
        self.preview = items
        try:
            prof = ingest._profile(self.source)
            self.profile_name = "" if prof.id == "generic" else f"{prof.name} card. "
        except OSError:
            self.profile_name = ""
        if not items:
            self.message.setText(f"No photos or videos found in {self.source}.")
        self._update_preview()

    def _update_example(self) -> None:
        try:
            ex = render(self.template.currentText(), Context(datetime(2026, 6, 19),
                                                             import_name=self.name.text(),
                                                             event=self.name.text() or None))
            self.example.setText(f"e.g.  {ex}\\IMG_0001.JPG  (file names are never changed)")
        except TemplateError as e:
            self.example.setText(str(e))

    def _update_preview(self) -> None:
        self._update_example()
        if not self.preview:
            self.go.setText("Import")
            self.go.setEnabled(False)
            self.found.setText("")
            return
        tmpl = self.template.currentText()
        counts, sizes = Counter(), Counter()
        # A named import is an event: everything is filed by its start date.
        event = self.name.text().strip() or None
        dated = [t for _, _, t in self.preview if t]
        start = min(dated) if event and dated else None
        try:
            for rel, size, taken in self.preview:
                folder = render(tmpl, Context(taken, import_name=self.name.text(),
                                              import_date=date.today(),
                                              original_folder=os.path.dirname(rel).rsplit("/", 1)[-1],
                                              event=event, event_start=start))
                counts[folder] += 1
                sizes[folder] += size
        except TemplateError as e:
            self.found.setText(f"Folder template: {e}")
            return
        self.table.clearContents()
        self.table.setRowCount(len(counts))
        for i, folder in enumerate(sorted(counts)):
            self.table.setItem(i, 0, QTableWidgetItem(os.path.join(self._destination(), folder)))
            self.table.setItem(i, 1, QTableWidgetItem(f"{counts[folder]:,}"))
            self.table.setItem(i, 2, QTableWidgetItem(_gb(sizes[folder])))
        n, total = len(self.preview), sum(s for _, s, _ in self.preview)
        self.message.setText(f"{self.profile_name}{n:,} photos and videos ({_gb(total)}) on {self.source}. File names are "
                             "kept exactly as they are; photos already in your library are skipped.")
        self.found.setText(f"{n:,} files · {len(counts):,} folders")
        self.go.setText(f"Import {n:,} files")
        self.go.setEnabled(True)

    # --- importing ---------------------------------------------------------------------

    def _start(self) -> None:
        if not self.source:
            return
        if not Settings(self.conn).get("import_destination"):
            self._choose_destination()               # first import: ask where imports go
            if not Settings(self.conn).get("import_destination"):
                return
        self.resume(None, self.source, self.name.text())

    def resume(self, import_id: int | None, source: str | None = None, name: str = "") -> None:
        """Carry on with an import - or, with no id, create one from `source` (on the worker)."""
        if self._thread is not None:
            return
        self._import_id = import_id
        self.bar.show()
        self.stop_b.show()
        self.go.setEnabled(False)
        w = ImportWorker(import_id, source, name)
        w.progress.connect(self._progress)
        self._run(w, self._finished)

    def _stop(self) -> None:
        if self._worker is not None and hasattr(self._worker, "stop"):
            self._worker.stop = True
            self.message.setText("Stopping after the current file…")

    def _progress(self, phase: str, done: int, total: int, current: str) -> None:
        self.bar.setRange(0, total)                   # 0..0: busy, no count yet
        self.bar.setValue(done)
        verb = {"stage": "Copying from the card", "place": "Filing into the library"}.get(phase, phase)
        self.bar.setFormat(f"{verb}: {done:,} / {total:,}")
        self.message.setText(f"{verb}… {os.path.basename(current)}")

    def _finished(self, import_id: int, result) -> None:
        self._end_thread()
        self._import_id = import_id or self._import_id
        self.bar.hide()
        self.stop_b.hide()
        if isinstance(result, ingest.WaitingForSource):
            self.message.setText(f"Paused - waiting for {result}. It carries on by itself when it's back.")
        elif isinstance(result, ingest.NoStagingSpace):
            QMessageBox.warning(self, "No room to stage", str(result))
            self.message.setText(str(result))
        elif isinstance(result, Exception):
            self.message.setText(f"Import stopped: {result}")
        else:
            s = result
            lines = []
            if s["safe_to_format"]:
                lines.append("✔  All in the library and verified. It's safe to format the card.")
            elif s["card_removable"]:
                lines.append("✔  Everything is copied and verified - you can remove the card.")
            lines.append(f"{s.get('placed', 0):,} imported, {s.get('already_in_library', 0):,} were already "
                         f"in your library" + (f", {s['failed']:,} failed (Retry on the next import)"
                                                if s.get("failed") else "") + ".")
            self.message.setText("\n".join(lines))
            if s.get("placed"):
                self.imported.emit(self._destination())
            if s["safe_to_format"]:
                # Done: don't invite importing the same card twice.
                self.preview = []
                self.table.setRowCount(0)
                self.go.setText("Imported ✓")
                self.go.setEnabled(False)
                # Only a memory card can be cleared - never a folder you imported from.
                if self.source in ingest.removable_drives_with_media() and ingest.clearable(self.conn, import_id):
                    self._clear_id = import_id
                    self.clear_b.show()
                return
        self.go.setEnabled(bool(self.preview))

    def _clear_card(self) -> None:
        if self._clear_id is None:
            return
        n = len(ingest.clearable(self.conn, self._clear_id))
        if not n:
            self.clear_b.hide()
            return
        if QMessageBox.warning(
                self, "Clear the card?",
                f"Delete the {n:,} imported files from the card in {self.source}?\n\nEvery one is verified in "
                "your library. A memory card has no Recycle Bin, so this can't be undone. Files Lunelis didn't "
                "import stay on the card. (Formatting the card in the camera does the same and more.)",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        deleted, skipped = ingest.clear_card(self.conn, self._clear_id)
        self.clear_b.hide()
        self._clear_id = None
        self.message.setText(f"Cleared {deleted:,} files from the card."
                             + (f" {len(skipped):,} changed on the card since the import, so they were left."
                                if skipped else ""))

    # --- thread plumbing -----------------------------------------------------------------

    def _run(self, worker: QObject, done_slot) -> None:
        self._thread = QThread(self)
        self._worker = worker
        worker.moveToThread(self._thread)
        self._thread.started.connect(worker.run)
        signal = worker.done if hasattr(worker, "done") else worker.finished
        signal.connect(done_slot)
        self._thread.start()

    def _end_thread(self) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait()
            self._thread = None
            self._worker = None

    def busy(self) -> bool:
        return self._thread is not None
