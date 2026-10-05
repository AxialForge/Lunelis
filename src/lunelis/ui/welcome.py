"""
The Welcome window: the first-run questions, for a Lunelis that wasn't set up
by the installer (unzipped by hand, or the installer's pages were skipped).

The same answers as the installer's pages, in the same shape
(firstrun.read()'s dict), so MainWindow.apply_setup() handles both. Shown once:
at the first start with an empty library; Help > Welcome... opens it again.
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QStandardPaths, Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from lunelis import firstrun, paths

PHOTO_EXTS = {".jpg", ".jpeg", ".heic", ".png", ".tif", ".tiff", ".arw", ".cr2", ".cr3", ".nef", ".raf",
              ".dng", ".orf", ".rw2", ".mp4", ".mov"}


def _has_photos(folder: Path, budget: int = 400) -> bool:
    """A quick look (a few hundred entries at most) for anything that looks like a photo."""
    seen = 0
    try:
        for root, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if not d.startswith((".", "$", "_Lunelis"))]
            for f in files:
                if os.path.splitext(f)[1].lower() in PHOTO_EXTS:
                    return True
                seen += 1
            seen += len(dirs)
            if seen > budget:
                return False
    except OSError:
        pass
    return False


def suggested_folders() -> list[Path]:
    """Folders on this PC that probably hold photos: Pictures, OneDrive's
    Pictures, Google Takeout exports in Downloads. Only ones that exist and
    have photos in them."""
    home = Path.home()
    out: list[Path] = []
    pics = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)
    candidates = [Path(pics)] if pics else []
    for env in ("OneDrive", "OneDriveConsumer"):
        if os.environ.get(env):
            candidates.append(Path(os.environ[env]) / "Pictures")
    downloads = home / "Downloads"
    if downloads.is_dir():
        try:
            candidates += sorted(p for p in downloads.iterdir() if p.is_dir() and p.name.lower().startswith("takeout"))
        except OSError:
            pass
    for c in candidates:
        if c.is_dir() and not any(os.path.normcase(str(c)) == os.path.normcase(str(o)) for o in out) \
                and _has_photos(c):
            out.append(c)
    return out


def _mb(n: int) -> str:
    return f"{n / 1e6:,.0f} MB"


class WelcomeDialog(QDialog):
    """Five short pages: welcome, photos, start-up, optional downloads, done."""

    def __init__(self, parent=None, tray_on: bool = True, autostart_on: bool = False,
                 existing: list[str] | None = None) -> None:
        super().__init__(parent)
        self.existing = [os.path.normcase(os.path.normpath(e)) for e in existing or []]
        self.setWindowTitle("Welcome to Lunelis")
        self.setMinimumSize(620, 460)
        self.pages = QStackedWidget()
        self.pages.addWidget(self._intro())
        self.pages.addWidget(self._photos())
        self.pages.addWidget(self._startup(tray_on, autostart_on))
        self.pages.addWidget(self._downloads())
        self.pages.addWidget(self._done())

        self.back_b = QPushButton("Back", clicked=lambda: self._go(-1))
        self.next_b = QPushButton("Next", objectName="Primary", clicked=lambda: self._go(1))
        self.skip_b = QPushButton("Skip for now", clicked=self.reject)
        self.step = QLabel(objectName="Help")
        nav = QHBoxLayout()
        nav.addWidget(self.step)
        nav.addStretch(1)
        nav.addWidget(self.skip_b)
        nav.addWidget(self.back_b)
        nav.addWidget(self.next_b)
        lay = QVBoxLayout(self)
        lay.addWidget(self.pages, 1)
        lay.addLayout(nav)
        self._go(0)

    # --- pages -------------------------------------------------------------------

    @staticmethod
    def _page(title: str, text: str) -> tuple[QWidget, QVBoxLayout]:
        w = QWidget()
        v = QVBoxLayout(w)
        h = QLabel(title, objectName="PageTitle")
        v.addWidget(h)
        t = QLabel(text, wordWrap=True)
        t.setTextFormat(Qt.TextFormat.PlainText)
        v.addWidget(t)
        return w, v

    def _intro(self) -> QWidget:
        w, v = self._page("Welcome to Lunelis",
                          "Lunelis catalogues your photos and videos where they already are - on this PC, a USB "
                          "drive or a network drive - and helps you look after them.\n\n"
                          "Your originals are never changed, renamed or deleted. Edits are kept as notes and only "
                          "applied to copies you export, and anything set aside goes to a quarantine you can "
                          "restore from.\n\n"
                          "A few questions and you're set. Everything here can be changed later in Settings.")
        v.addStretch(1)
        return w

    def _photos(self) -> QWidget:
        w, v = self._page("Your photos",
                          "Which folders hold your photos? Lunelis only reads them. Add as many as you like: a "
                          "folder on this PC, a USB drive, or a network path such as \\\\nas\\photos.")
        self.folders = QListWidget()
        self.folders.setObjectName("WelcomeFolders")
        for p in suggested_folders():
            if not self._in_library(str(p)):
                self._add_item(str(p), checked=True, note="found on this PC")
        v.addWidget(self.folders, 1)
        row = QHBoxLayout()
        row.addWidget(QPushButton("Add a folder…", clicked=self._browse))
        row.addWidget(QPushButton("Remove", clicked=self._remove))
        row.addStretch(1)
        v.addLayout(row)
        self.none_note = QLabel("No folders yet - you can add them any time with Library > Add folder (Ctrl+O).",
                                objectName="Help", wordWrap=True)
        v.addWidget(self.none_note)
        self._update_note()
        return w

    def _in_library(self, path: str) -> bool:
        """Already a source, or inside / around one: nothing to offer."""
        key = os.path.normcase(os.path.normpath(path))
        for e in self.existing:
            try:
                if os.path.commonpath([key, e]) in (key, e):
                    return True
            except ValueError:                         # different drives
                continue
        return False

    def _add_item(self, path: str, checked: bool = True, note: str = "") -> None:
        for i in range(self.folders.count()):
            if os.path.normcase(self.folders.item(i).data(Qt.ItemDataRole.UserRole)) == os.path.normcase(path):
                return
        it = QListWidgetItem(f"{path}    ({note})" if note else path)
        it.setData(Qt.ItemDataRole.UserRole, path)
        it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        it.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self.folders.addItem(it)

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Add a photo folder")
        if folder:
            self._add_item(os.path.normpath(folder))
            self._update_note()

    def _remove(self) -> None:
        for it in self.folders.selectedItems():
            self.folders.takeItem(self.folders.row(it))
        self._update_note()

    def _update_note(self) -> None:
        self.none_note.setVisible(self.folders.count() == 0)

    def _startup(self, tray_on: bool, autostart_on: bool) -> QWidget:
        w, v = self._page("Start-up and the tray",
                          "With the tray on, closing the window keeps Lunelis running quietly, so it can offer "
                          "to import when you plug in a memory card, phone or USB stick.")
        self.tray_cb = QCheckBox("Keep Lunelis in the tray when the window is closed, and watch for memory cards")
        self.tray_cb.setChecked(tray_on)
        self.auto_cb = QCheckBox("Start Lunelis with Windows (it opens in the tray)")
        self.auto_cb.setChecked(autostart_on)
        self.tray_cb.toggled.connect(lambda on: (self.auto_cb.setEnabled(on), on or self.auto_cb.setChecked(False)))
        self.auto_cb.setEnabled(tray_on)
        v.addWidget(self.tray_cb)
        v.addWidget(self.auto_cb)
        v.addStretch(1)
        return w

    def _downloads(self) -> QWidget:
        w, v = self._page("Optional downloads",
                          "These small AI models run only on this PC; nothing about your photos is sent anywhere. "
                          "Each is checked against its known fingerprint before it's used. They download in the "
                          "background - you can start using Lunelis straight away.")
        try:
            sizes = firstrun.model_sizes()
        except Exception:                          # a broken model table must not stop the Welcome window
            sizes = {}
        self.model_cbs = {}
        for key, label in (("faces", "Faces - finds the people in your photos so you can name them once"),
                           ("scene", "Scene tags - suggests what's in each photo, and powers Find similar"),
                           ("subject", "Subject masks - select the person or thing in one click when editing"),
                           ("sky", "Sky masks - select the sky in one click when editing")):
            cb = QCheckBox(f"{label} ({_mb(sizes[key])})" if key in sizes else label)
            self.model_cbs[key] = cb
            v.addWidget(cb)
        v.addWidget(QLabel("Without them everything else works; you can download them later in Settings.",
                           objectName="Help", wordWrap=True))
        v.addStretch(1)
        return w

    def _done(self) -> QWidget:
        w, v = self._page("All set", "")
        self.summary = QLabel(wordWrap=True)
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        v.addWidget(self.summary)
        v.addStretch(1)
        return w

    # --- navigation and result ---------------------------------------------------------

    def _go(self, delta: int) -> None:
        i = self.pages.currentIndex() + delta
        if i >= self.pages.count():
            self.accept()
            return
        i = max(0, i)
        self.pages.setCurrentIndex(i)
        last = i == self.pages.count() - 1
        self.back_b.setEnabled(i > 0)
        self.next_b.setText("Start" if last else "Next")
        self.skip_b.setVisible(not last)
        self.step.setText(f"Step {i + 1} of {self.pages.count()}")
        if last:
            self.summary.setText(self._summary())

    def _summary(self) -> str:
        s = self.result_setup()
        lines = []
        n = len(s["folders"])
        lines.append(f"Lunelis will start reading {n} folder{'s' if n != 1 else ''}. You can browse while it works; "
                     "thumbnails fill in as they're made." if n else
                     "No folders yet: add them with Library > Add folder (Ctrl+O).")
        lines.append("The tray is on." if s["tray"] else "The tray is off: closing the window quits Lunelis.")
        if s["start_with_windows"]:
            lines.append("Lunelis starts with Windows, in the tray.")
        if s["models"]:
            lines.append("Downloading in the background: " + ", ".join(s["models"]) + ".")
        lines.append(f"Your catalog, thumbnails and backups live in {paths.DATA_DIR}. "
                     "To keep them on another drive, use Settings > Advanced > Data folder.")
        return "\n\n".join(lines)

    def result_setup(self) -> dict:
        folders = [self.folders.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.folders.count())
                   if self.folders.item(i).checkState() == Qt.CheckState.Checked]
        return {"version": 1, "folders": folders, "tray": self.tray_cb.isChecked(),
                "start_with_windows": self.tray_cb.isChecked() and self.auto_cb.isChecked(),
                "models": [k for k, cb in self.model_cbs.items() if cb.isChecked()]}
