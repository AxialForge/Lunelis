"""
Photo > Export... (Ctrl+Shift+E): the selected photos (or the one on screen)
as new files, with their edits, in a folder you pick. Settings are
remembered (Settings `export_last`), and any set can be saved as a named
preset (`export_presets`). Output sharpening (for screen, matte or glossy
prints) and an output colour profile (.icc) are applied to the exported
file only - never to the edit. Runs on a worker thread with a progress
window you can cancel; videos are skipped.
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


# Ready-made sets (the folder stays whatever you chose). Yours are added with Save as preset.
BUILT_IN = {
    "Web - 2048 px, sharpened for screens": {"format": "jpeg", "long_edge": 2048, "quality": 88,
                                              "metadata": "no_location", "sharpen": "screen"},
    "Email - 1600 px, small files": {"format": "jpeg", "long_edge": 1600, "quality": 80,
                                     "metadata": "no_location", "sharpen": "screen"},
    "Social - 1080 px, no metadata": {"format": "jpeg", "long_edge": 1080, "quality": 90,
                                      "metadata": "none", "sharpen": "screen"},
    "Print, matte - full size": {"format": "jpeg", "long_edge": None, "quality": 95,
                                 "metadata": "all", "sharpen": "matte"},
    "Print, glossy - full size": {"format": "jpeg", "long_edge": None, "quality": 95,
                                  "metadata": "all", "sharpen": "glossy"},
    "Archive - full size TIFF": {"format": "tiff", "long_edge": None, "metadata": "all", "sharpen": "none"},
}


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
        prow = QHBoxLayout()
        self.preset = QComboBox()
        self.preset.addItem("Last used", None)
        for name in BUILT_IN:
            self.preset.addItem(name, "builtin:" + name)
        for name in sorted(Settings(conn).get("export_presets") or {}):
            self.preset.addItem(name, name)
        self.preset.currentIndexChanged.connect(self._preset_chosen)
        prow.addWidget(self.preset, 1)
        prow.addWidget(QPushButton("Save as preset…", clicked=self._save_preset))
        pw = QWidget()
        pw.setLayout(prow)
        form.addRow("Preset", pw)
        row = QHBoxLayout()
        from lunelis import lunelis_folder
        yearly = lunelis_folder.exports(Settings(conn))         # Lunelis folder: Exports\<this year>
        self.folder = QLineEdit(str(yearly) if yearly else
                                last.get("folder") or os.path.join(os.path.expanduser("~"), "Pictures", "Lunelis exports"))
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
        self.sharpen = QComboBox()
        for label, key in (("None", "none"), ("For screen", "screen"), ("For matte paper", "matte"),
                           ("For glossy paper", "glossy")):
            self.sharpen.addItem(label, key)
        self.amount = QComboBox()
        for label, key in (("Low", "low"), ("Standard", "standard"), ("High", "high")):
            self.amount.addItem(label, key)
        srow = QHBoxLayout()
        srow.addWidget(self.sharpen, 1)
        srow.addWidget(self.amount)
        sw = QWidget()
        sw.setLayout(srow)
        form.addRow("Output sharpening", sw)
        self.profile = QComboBox()
        self.profile.addItem("sRGB (screens, the web, most labs)", None)
        self.profile.addItem("Display P3 (wide-colour screens, phones)", "builtin:display-p3")
        self.profile.addItem("Adobe RGB (1998) compatible (print workflows)", "builtin:adobe-rgb")
        self.profile.addItem("A profile of my own (.icc)…", "pick")
        self.profile.activated.connect(self._profile_chosen)
        self.intent = QComboBox()
        for label, key in (("Perceptual", "perceptual"), ("Relative colorimetric", "relative")):
            self.intent.addItem(label, key)
        crow = QHBoxLayout()
        crow.addWidget(self.profile, 1)
        crow.addWidget(self.intent)
        cw = QWidget()
        cw.setLayout(crow)
        form.addRow("Colour profile", cw)
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
        self._show_extras(last)

    def _show_extras(self, d: dict) -> None:
        self.sharpen.setCurrentIndex(max(0, self.sharpen.findData(d.get("sharpen", "none"))))
        self.amount.setCurrentIndex(max(0, self.amount.findData(d.get("sharpen_amount", "standard"))))
        self.intent.setCurrentIndex(max(0, self.intent.findData(d.get("intent", "perceptual"))))
        self._set_profile(d.get("profile"))

    def _set_profile(self, path: str | None) -> None:
        for i in range(self.profile.count() - 1, 3, -1):           # drop an earlier own profile
            self.profile.removeItem(i)
        if path and path.startswith("builtin:"):
            self.profile.setCurrentIndex(max(0, self.profile.findData(path)))
        elif path:
            self.profile.addItem(os.path.basename(path), path)
            self.profile.setCurrentIndex(self.profile.count() - 1)
        else:
            self.profile.setCurrentIndex(0)
        self.intent.setEnabled(bool(path))

    def _profile_chosen(self, _i: int) -> None:
        if self.profile.currentData() != "pick":
            self.intent.setEnabled(bool(self.profile.currentData()))
            return
        path, _ = QFileDialog.getOpenFileName(self, "Choose a colour profile", "", "ICC profiles (*.icc *.icm)")
        self._set_profile(path or None)

    def _current(self) -> ExportOptions:
        prof = self.profile.currentData()
        return ExportOptions(self.folder.text().strip(), self.format.currentData(), self.size.currentData(),
                             self.quality.value(), self.metadata.currentData(), self.pattern.text().strip(),
                             self.sharpen.currentData(), self.amount.currentData(),
                             prof if prof not in (None, "pick") else None, self.intent.currentData())

    def _apply(self, d: dict) -> None:
        self.folder.setText(d.get("folder") or self.folder.text())
        self.format.setCurrentIndex(max(0, self.format.findData(d.get("format", "jpeg"))))
        self.size.setCurrentIndex(max(0, self.size.findData(d.get("long_edge"))))
        self.quality.setValue(int(d.get("quality", 92)))
        self.metadata.setCurrentIndex(max(0, self.metadata.findData(d.get("metadata", "all"))))
        self.pattern.setText(d.get("pattern", "{name}"))
        self._show_extras(d)

    def _preset_chosen(self, _i: int) -> None:
        name = self.preset.currentData()
        s = Settings(self.conn)
        if name and name.startswith("builtin:"):
            self._apply({"folder": self.folder.text(), "pattern": self.pattern.text(), **BUILT_IN[name[8:]]})
            return
        self._apply((s.get("export_presets") or {}).get(name) if name else (s.get("export_last") or {}))

    def _save_preset(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Save as preset", "Name this export preset:")
        name = name.strip()
        if not ok or not name:
            return
        s = Settings(self.conn)
        presets = dict(s.get("export_presets") or {})
        presets[name] = asdict(self._current())
        s.set("export_presets", presets)
        if self.preset.findData(name) < 0:
            self.preset.addItem(name, name)
        self.preset.blockSignals(True)
        self.preset.setCurrentIndex(self.preset.findData(name))
        self.preset.blockSignals(False)

    def _pick(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Export into which folder?", self.folder.text())
        if d:
            self.folder.setText(d)

    def _format_changed(self) -> None:
        self.quality.setEnabled(self.format.currentData() == "jpeg")

    def _accept(self) -> None:
        opts = self._current()
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
                        from lunelis.reach import explain, gone_offline
                        if gone_offline(e, self.opts.folder):
                            errors.append(f"{fmt[1] if fmt else fid}: {explain(e, 'exporting', self.opts.folder)}")
                        else:
                            errors.append(f"{fmt[1] if fmt else fid}: {type(e).__name__}: {e}")
                self.progress.emit(i, len(self.file_ids))
        finally:
            conn.close()
            self.done.emit(made, videos, errors)
