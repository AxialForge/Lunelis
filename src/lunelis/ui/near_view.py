"""
The Near-duplicates tab of the Duplicates page: the same photo saved as a
different file (resized, re-compressed, a Takeout re-encode, an export),
found by dupes/similar.py.

Pick a group, see every copy side by side with the one that's kept, tick or
untick the copies to set aside. Nothing is set aside without a confirmation,
edited exports are never suggested, and "set aside" is the quarantine
folder - never a delete.
"""
from __future__ import annotations

import os

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton, QSplitter,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from lunelis import paths
from lunelis.catalog.schema import open_catalog
from lunelis.dupes import similar
from lunelis.dupes.quarantine import QUARANTINE_DIR, QuarantineRefused
from lunelis.raw.thumbnails import cache_rel_path
from lunelis.settings import Settings
from lunelis.ui.background import unless_closed
from lunelis.ui.widgets import sharp


def _size(n: int) -> str:
    return f"{n / 1e9:,.1f} GB" if n >= 1e8 else f"{n / 1e6:,.1f} MB"


class SimilarWorker(QObject):
    """'find': fingerprint new photos and regroup. 'aside': set aside the
    extras of the given groups."""

    progress = Signal(str)
    done = Signal(object)                  # int groups | (moved, freed, errors)

    def __init__(self, action: str, groups: list | None = None) -> None:
        super().__init__()
        self.action = action
        self.groups = groups or []
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        conn = open_catalog(paths.DEFAULT_CATALOG_PATH)
        result: object = None
        try:
            if self.action == "find":
                similar.compute_missing(conn, paths.THUMBNAIL_CACHE, should_cancel=lambda: self._cancel,
                                        on_progress=lambda d, t: self.progress.emit(
                                            f"Fingerprinting photos… {d:,} / {t:,}"))
                self.progress.emit("Comparing fingerprints…")
                result = similar.rebuild(conn, similar.find_groups(conn))
            else:
                moved = freed = 0
                errors: list[str] = []
                backup_dir = paths.BACKUP_DIR          # one catalog snapshot, before the first move
                for i, g in enumerate(self.groups, 1):
                    if self._cancel:
                        break
                    try:
                        similar.quarantine_similar(conn, g.id, g.extras, backup_dir=backup_dir)
                        backup_dir = None
                        moved += len(g.extras)
                        freed += g.extra_bytes
                    except (QuarantineRefused, OSError) as e:
                        errors.append(f"group {g.id}: {e}")
                    self.progress.emit(f"Setting copies aside… {i:,} / {len(self.groups):,} groups")
                result = (moved, freed, errors)
        finally:
            conn.close()
            self.done.emit(result)


class NearView(QWidget):
    def __init__(self, conn, parent=None) -> None:
        super().__init__(parent)
        self.conn = conn
        self.groups: list[similar.SimilarGroup] = []
        self._thread = None
        self._checks: list[tuple[int, QCheckBox]] = []
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 12, 0, 0)
        top = QHBoxLayout()
        self.summary = QLabel()
        self.summary.setObjectName("Count")
        self.summary.setWordWrap(True)
        top.addWidget(self.summary, 1)
        self.find_b = QPushButton("Find again", clicked=lambda: self._run("find"))
        self.group_b = QPushButton("Set aside ticked copies", clicked=self._aside_group)
        self.all_b = QPushButton("Set aside all suggested…", clicked=self._aside_all)
        self.stop_b = QPushButton("Stop", clicked=lambda: self._worker.cancel())   # direct: sets a flag
        self.stop_b.hide()
        for b in (self.find_b, self.group_b, self.all_b, self.stop_b):
            top.addWidget(b)
        v.addLayout(top)
        note = QLabel("The same photo saved as another file - resized, re-compressed, re-encoded by "
                      "Google Takeout or exported. The best copy (not damaged, most pixels, not a "
                      "Takeout re-encode) is kept, and edited exports are never suggested. Copies you "
                      "set aside go to the quarantine folder, not the bin.")
        note.setObjectName("Hint")
        note.setWordWrap(True)
        v.addWidget(note)
        split = QSplitter(Qt.Orientation.Vertical)
        self.list = QTableWidget(0, 3)
        self.list.setHorizontalHeaderLabels(["Copies", "Suggested", "Kept copy"])
        self.list.verticalHeader().hide()
        self.list.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.list.setWordWrap(False)
        self.list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.list.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.list.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.list.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.list.currentCellChanged.connect(self._current_changed)
        split.addWidget(self.list)
        self.detail = QTableWidget(0, 4)
        self.detail.setHorizontalHeaderLabels(["", "Location", "Size", ""])
        self.detail.verticalHeader().hide()
        self.detail.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.detail.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.detail.setWordWrap(False)                  # UNC paths wrap at backslashes (CLAUDE.md)
        self.detail.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        split.addWidget(self.detail)
        split.setSizes([300, 420])
        v.addWidget(split, 1)

    # --- data ----------------------------------------------------------------

    def refresh(self) -> None:
        if self._thread is not None:
            return
        self.groups = similar.load(self.conn, Settings(self.conn).get("preferred_roots"))
        total = self.conn.execute(
            f"SELECT COUNT(*) FROM files f WHERE {similar.LIVE} AND f.thumbnail_path IS NOT NULL").fetchone()[0]
        done = min(total, Settings(self.conn).get("similar_grouped_count"))   # compared, not just fingerprinted
        n = sum(len(g.extras) for g in self.groups)
        if self.groups:
            self.summary.setText(
                f"{len(self.groups):,} groups · {n:,} copies suggested to set aside "
                f"({_size(sum(g.extra_bytes for g in self.groups))}) · {done:,} of {total:,} photos compared")
        elif done:
            self.summary.setText(f"No near-duplicates among {done:,} photos.")
        else:
            self.summary.setText("Not searched yet. Find again compares every photo (about a minute).")
        self.list.setRowCount(0)
        self.list.setRowCount(len(self.groups))
        for i, g in enumerate(self.groups):
            keep = next(m for m in g.members if m[0] == g.keeper)
            self.list.setItem(i, 0, QTableWidgetItem(str(len(g.members))))
            self.list.setItem(i, 1, QTableWidgetItem(str(len(g.extras))))
            self.list.setItem(i, 2, QTableWidgetItem(os.path.join(keep[1], *keep[2].split("/"))))
        self.list.setColumnWidth(0, 70)
        self.list.setColumnWidth(1, 90)
        if self.groups:
            self.list.selectRow(0)
        else:
            self._show(-1)
        self._buttons()

    def _buttons(self) -> None:
        idle = self._thread is None
        self.stop_b.setVisible(not idle)
        self.find_b.setEnabled(idle)
        self.all_b.setEnabled(idle and any(g.extras for g in self.groups))
        self.group_b.setEnabled(idle and any(c.isChecked() for _, c in self._checks))

    def _current_changed(self, row: int, *_) -> None:
        self._show(row)

    def _show(self, row: int) -> None:
        # clearContents() also deletes the cell widgets (see dupes_view).
        self.detail.clearContents()
        self.detail.setRowCount(0)
        self._checks = []
        if 0 <= row < len(self.groups):
            g = self.groups[row]
            self.detail.setRowCount(len(g.members))
            for i, (fid, root, rel, thumb, size, w, h) in enumerate(g.members):
                pic = QLabel()
                pm = QPixmap(str(paths.THUMBNAIL_CACHE / (thumb or cache_rel_path(fid))))
                if not pm.isNull():
                    pic.setPixmap(sharp(pm, 120, pic))
                self.detail.setCellWidget(i, 0, pic)
                where = QTableWidgetItem(os.path.join(root, *rel.split("/")))
                where.setToolTip(where.text())
                self.detail.setItem(i, 1, where)
                dims = f"{w:,} × {h:,}\n" if w and h else ""
                self.detail.setItem(i, 2, QTableWidgetItem(dims + _size(size or 0)))
                if fid == g.keeper:
                    self.detail.setItem(i, 3, QTableWidgetItem("Kept"))
                elif similar.is_edit(rel):
                    self.detail.setItem(i, 3, QTableWidgetItem("Edit - kept"))
                else:
                    box = QCheckBox("Set aside")
                    box.setChecked(fid in g.extras)
                    box.toggled.connect(self._toggled)
                    self.detail.setCellWidget(i, 3, box)
                    self._checks.append((fid, box))
                self.detail.setRowHeight(i, 126)
            self.detail.setColumnWidth(0, 130)
            self.detail.setColumnWidth(2, 130)
            self.detail.setColumnWidth(3, 110)
        self._buttons()

    def _toggled(self, _checked: bool) -> None:
        self._buttons()

    # --- setting aside -----------------------------------------------------------

    def _aside_group(self) -> None:
        row = self.list.currentRow()
        picked = [fid for fid, c in self._checks if c.isChecked()]
        if row < 0 or not picked:
            return
        g = self.groups[row]
        answer = QMessageBox.question(
            self, "Set copies aside",
            f"Move {len(picked)} cop{'y' if len(picked) == 1 else 'ies'} of this photo into the "
            f"'{QUARANTINE_DIR}' folder on the same drive?\n\nThe kept copy stays where it is and "
            "gets their ratings, labels and albums. Nothing is deleted.")
        if answer == QMessageBox.StandardButton.Yes:
            self._run("aside", [similar.SimilarGroup(g.id, g.members, g.keeper, picked)])

    def _aside_all(self) -> None:
        todo = [g for g in self.groups if g.extras]
        n = sum(len(g.extras) for g in todo)
        answer = QMessageBox.question(
            self, "Set aside all suggested copies",
            f"Move {n:,} suggested copies ({_size(sum(g.extra_bytes for g in todo))}) from "
            f"{len(todo):,} groups into a '{QUARANTINE_DIR}' folder on the same drive?\n\n"
            "Nothing is deleted. The best copy of every photo stays where it is and gets the "
            "others' ratings, labels and albums; edited exports are never moved; the catalog is "
            "backed up first. Look through a few groups before doing this.")
        if answer == QMessageBox.StandardButton.Yes:
            self._run("aside", todo)

    def _run(self, action: str, groups: list | None = None) -> None:
        if self._thread is not None:
            return
        self._thread = QThread(self)
        self._worker = SimilarWorker(action, groups)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self.summary.setText)
        self._worker.done.connect(self._done)            # bound method: runs on the GUI thread
        self._buttons()
        self._thread.start()

    @unless_closed
    def _done(self, result) -> None:
        self._thread.quit()
        self._thread.wait()
        self._thread = None
        if isinstance(result, tuple):
            moved, freed, errors = result
            msg = f"Set aside {moved:,} copies ({_size(freed)}) in the quarantine folder."
            if errors:
                msg += f"\n\n{len(errors):,} groups were skipped:\n" + "\n".join(errors[:8])
            QMessageBox.information(self, "Near-duplicates", msg)
        self.refresh()
