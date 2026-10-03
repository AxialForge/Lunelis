"""
Photo > Export... (Ctrl+Shift+E): the selected photos (or the one on screen)
as new files, with their edits, in a folder you pick. Settings are
remembered (Settings `export_last`). Runs on a worker thread with a
progress window you can cancel; videos are skipped.
"""
from __future__ import annotations

import os
from dataclasses import asdict

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from lunelis.edit.export import ExportOptions, export_one
from lunelis.settings import Settings


class ExportDialog(QDialog):
    def __init__(self, conn, count: int, parent=None) -> None:
        super().__init__(parent)
        self.conn = conn
        self.setWindowTitle("Export")
        self.setMinimumWidth(520)
        last = Settings(conn).get("export_last") or {}
        v = QVBoxLayout(self)
        head = QLabel(f"Export {count:,} photo{'s' if count != 1 else ''} with {'their' if count != 1 else 'its'} "
                      "edits as new files. The originals aren't touched.")
        head.setWordWrap(True)
        v.addWidget(head)
        form = QFormLayout()
        row = QHBoxLayout()
        self.folder = QLineEdit(last.get("folder") or os.path.join(os.path.expanduser("~"), "Pictures", "Lunelis exports"))
        row.addWidget(self.folder, 1)
        row.addWidget(QPushButton("Change…", clicked=self._pick))
        w = QWidget()
        w.setLayout(row)
        form.addRow("Into", w)
        self.format = QComboBox()
        for label, key in (("JPEG", "jpeg"), ("TIFF (lossless)", "tiff"), ("PNG (lossless)", "png")):
            self.format.addItem(label, key)
        self.format.setCurrentIndex(max(0, self.format.findData(last.get("format", "jpeg"))))
        self.format.currentIndexChanged.connect(self._format_changed)
        form.addRow("Format", self.format)
        self.size = QComboBox()
        for label, edge in (("Full size", None), ("4096 px long edge", 4096), ("2048 px long edge", 2048),
                            ("1600 px long edge", 1600), ("1080 px long edge", 1080)):
            self.size.addItem(label, edge)
        self.size.setCurrentIndex(max(0, self.size.findData(last.get("long_edge"))))
        form.addRow("Size", self.size)
        self.quality = QSpinBox(minimum=50, maximum=100, suffix=" %")
        self.quality.setValue(int(last.get("quality", 92)))
        form.addRow("JPEG quality", self.quality)
        self.metadata = QComboBox()
        for label, key in (("Keep camera, date and location", "all"), ("Keep all but the location (GPS)", "no_location"),
                           ("Remove all metadata", "none")):
            self.metadata.addItem(label, key)
        self.metadata.setCurrentIndex(max(0, self.metadata.findData(last.get("metadata", "all"))))
        form.addRow("Metadata", self.metadata)
        self.pattern = QLineEdit(last.get("pattern", "{name}"))
        self.pattern.setToolTip("{name} = the original's name, {date} = capture date, {n} = 001, 002…")
        form.addRow("File names", self.pattern)
        hint = QLabel("{name}  {date}  {n} - an existing file is never overwritten.", objectName="Help")
        form.addRow("", hint)
        v.addLayout(form)
        self.error = QLabel(objectName="Help")
        self.error.setObjectName("Error")                 # the theme's error colour
        v.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.go = buttons.addButton("Export", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)
        self._format_changed()
        self.options: ExportOptions | None = None

    def _pick(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Export into which folder?", self.folder.text())
        if d:
            self.folder.setText(d)

    def _format_changed(self) -> None:
        self.quality.setEnabled(self.format.currentData() == "jpeg")

    def _accept(self) -> None:
        opts = ExportOptions(self.folder.text().strip(), self.format.currentData(), self.size.currentData(),
                             self.quality.value(), self.metadata.currentData(), self.pattern.text().strip())
        try:
            if not opts.folder:
                raise ValueError("Choose a folder to export into.")
            opts.check()
        except ValueError as e:
            self.error.setText(str(e))
            return
        Settings(self.conn).set("export_last", asdict(opts))
        self.options = opts
        self.accept()


class ExportWorker(QObject):
    progress = Signal(int, int)
    done = Signal(int, int, list)          # exported, skipped videos, errors

    def __init__(self, file_ids: list[int], opts: ExportOptions) -> None:
        super().__init__()
        self.file_ids, self.opts = file_ids, opts
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        from lunelis import paths
        from lunelis.catalog.schema import open_catalog
        conn = open_catalog(paths.DEFAULT_CATALOG_PATH)
        made = videos = 0
        errors: list[str] = []
        try:
            for i, fid in enumerate(self.file_ids, 1):
                if self._cancel:
                    break
                fmt = conn.execute("SELECT format, filename FROM files WHERE id = ?", (fid,)).fetchone()
                if fmt and fmt[0] in ("mp4", "mov", "mpeg-ts"):
                    videos += 1
                else:
                    try:
                        export_one(conn, fid, self.opts, n=made + 1)
                        made += 1
                    except Exception as e:
                        errors.append(f"{fmt[1] if fmt else fid}: {type(e).__name__}: {e}")
                self.progress.emit(i, len(self.file_ids))
        finally:
            conn.close()
            self.done.emit(made, videos, errors)
