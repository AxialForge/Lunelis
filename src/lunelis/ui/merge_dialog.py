"""
Photo > Merge > HDR... / Panorama...: the options, then where to save
(asked every time - the user's choice, 2026-09-27; the dialog starts in
the last folder used). Runs on a worker thread with a progress window.
"""
from __future__ import annotations

import os

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QLabel, QVBoxLayout,
)

from lunelis.edit.merge import HDR_MAX, PANO_MAX, MergeFailed, MergeOptions, run
from lunelis.settings import Settings

TEXT = {
    "hdr": ("Merge to HDR",
            "Blends bracketed exposures of one scene into a single photo with detail in both the "
            "highlights and the shadows. Hand-held brackets are lined up first."),
    "panorama": ("Merge to panorama",
                 "Stitches overlapping photos into one wide picture. Overlap each frame by about a "
                 "third, and keep the exposure the same."),
}


class MergeDialog(QDialog):
    def __init__(self, conn, kind: str, count: int, first_name: str, parent=None) -> None:
        super().__init__(parent)
        self.conn, self.kind, self.first_name = conn, kind, first_name
        title, blurb = TEXT[kind]
        self.setWindowTitle(title)
        self.setMinimumWidth(480)
        v = QVBoxLayout(self)
        head = QLabel(f"{blurb}\n\n{count} photos selected.")
        head.setWordWrap(True)
        v.addWidget(head)
        form = QFormLayout()
        self.format = QComboBox()
        self.format.addItem("TIFF, 16-bit (best for editing)", "tiff")
        self.format.addItem("JPEG", "jpeg")
        form.addRow("Save as", self.format)
        self.align = QCheckBox("Line the photos up first (hand-held brackets)")
        self.align.setChecked(True)
        self.half = QCheckBox("Work at half size (much faster; still ~3000 px per frame)")
        self.half.setChecked(True)
        self.crop = QCheckBox("Crop away the empty edges")
        self.crop.setChecked(True)
        if kind == "hdr":
            form.addRow("", self.align)
        else:
            form.addRow("", self.half)
            form.addRow("", self.crop)
        v.addLayout(form)
        limit = HDR_MAX if kind == "hdr" else PANO_MAX
        self.error = QLabel(objectName="Help")
        self.error.setObjectName("Error")                 # the theme's error colour
        if not 2 <= count <= limit:
            self.error.setText(f"Select 2 to {limit} photos.")
        v.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.go = buttons.addButton("Choose where to save…", QDialogButtonBox.ButtonRole.AcceptRole)
        self.go.setEnabled(2 <= count <= limit)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)
        self.options: MergeOptions | None = None

    def suggested_path(self) -> str:
        s = Settings(self.conn)
        folder = s.get("merge_last_dir") or os.path.join(os.path.expanduser("~"), "Pictures")
        stem = os.path.splitext(self.first_name)[0]
        ext = ".tif" if self.format.currentData() == "tiff" else ".jpg"
        return os.path.join(folder, f"{stem}-{'HDR' if self.kind == 'hdr' else 'Pano'}{ext}")

    def _accept(self) -> None:
        fmt = self.format.currentData()
        filt = "TIFF (*.tif *.tiff)" if fmt == "tiff" else "JPEG (*.jpg *.jpeg)"
        path, _ = QFileDialog.getSaveFileName(self, "Save the merged photo", self.suggested_path(), filt)
        if not path:
            return
        want = (".tif", ".tiff") if fmt == "tiff" else (".jpg", ".jpeg")
        if not path.lower().endswith(want):
            base, ext = os.path.splitext(path)
            # "x.jpg" saved as TIFF becomes "x.tif", not "x.jpg.tif"
            path = (base if ext.lower() in (".jpg", ".jpeg", ".tif", ".tiff", ".png") else path) + want[0]
        Settings(self.conn).set("merge_last_dir", os.path.dirname(path))
        self.options = MergeOptions(self.kind, path, fmt, self.align.isChecked(), self.half.isChecked(),
                                    self.crop.isChecked())
        self.accept()


class MergeWorker(QObject):
    progress = Signal(str)
    done = Signal(object, str, str)        # new file id | None, path, error ("" = ok)

    def __init__(self, file_ids: list[int], opts: MergeOptions) -> None:
        super().__init__()
        self.file_ids, self.opts = file_ids, opts
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        from lunelis import paths
        from lunelis.catalog.schema import open_catalog
        conn = open_catalog(paths.DEFAULT_CATALOG_PATH)
        try:
            fid = run(conn, self.file_ids, self.opts, self.progress.emit, lambda: self._cancel)
            self.done.emit(fid, self.opts.path, "")
        except MergeFailed as e:
            self.done.emit(None, self.opts.path, str(e))
        except MemoryError:
            self.done.emit(None, self.opts.path, "Not enough memory - try fewer photos, or half size.")
        except Exception as e:
            from lunelis.reach import explain, gone_offline
            self.done.emit(None, self.opts.path, explain(e, "merging the photos", self.opts.path)
                           if gone_offline(e, self.opts.path) else f"{type(e).__name__}: {e}")
        finally:
            conn.close()
