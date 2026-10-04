"""
The Backups page: backup sets (a USB drive, stick or network folder each),
how up to date they are, and backing up / verifying / restoring.

Backups and verify passes are jobs (pausable, resumable, wait for the drive);
restores run on a worker thread here.
"""
from __future__ import annotations

import os
import shutil
from datetime import datetime

from lunelis.ui.widgets import plain, row_toggles
from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QRadioButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from lunelis import paths
from lunelis.backups import core
from lunelis.catalog.schema import open_catalog
from lunelis.ui.background import Background, unless_closed


def _gb(n: int) -> str:
    return f"{n / 1e12:,.2f} TB" if n >= 1e12 else f"{n / 1e9:,.1f} GB" if n >= 1e8 else f"{n / 1e6:,.0f} MB"


def _when(iso: str | None) -> str:
    if not iso:
        return "Never"
    try:
        return f"{datetime.fromisoformat(iso).astimezone():%b %d, %Y %H:%M}"
    except ValueError:
        return iso


def _item(text: str, right: bool = False) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
    if right:
        it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return it


class NewBackupDialog(QDialog):
    def __init__(self, conn, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New backup")
        self.setMinimumWidth(560)
        self.conn = conn
        v = QVBoxLayout(self)
        v.addWidget(QLabel("Back up to (a USB drive, stick or network folder outside your library):"))
        row = QHBoxLayout()
        self.dest = QLineEdit(readOnly=True)
        self.dest.setObjectName("PathField")
        row.addWidget(self.dest, 1)
        row.addWidget(QPushButton("Choose…", clicked=self._choose))
        v.addLayout(row)
        v.addWidget(QLabel("Name:"))
        self.name = QLineEdit(placeholderText="e.g. Blue USB drive")
        v.addWidget(self.name)
        v.addWidget(QLabel("What to back up:"))
        self.sources = QListWidget()
        row_toggles(self.sources)
        for rid, path, n, size in conn.execute(
                "SELECT r.id, r.path, COUNT(f.id), COALESCE(SUM(f.size_bytes), 0) FROM roots r"
                " LEFT JOIN files f ON f.root_id = r.id AND f.missing_since IS NULL AND f.excluded = 0"
                " AND f.quarantined_at IS NULL WHERE r.enabled = 1 GROUP BY r.id ORDER BY r.id"):
            it = QListWidgetItem(f"{path}   ({n:,} files, {_gb(size)})")
            it.setData(Qt.ItemDataRole.UserRole, (rid, size))
            it.setCheckState(Qt.CheckState.Unchecked)
            self.sources.addItem(it)
        self.sources.itemChanged.connect(lambda _: self._update())
        v.addWidget(self.sources)
        self.auto = QCheckBox("Back up automatically whenever this drive is connected")
        self.auto.setChecked(True)
        v.addWidget(self.auto)
        self.space = QLabel(objectName="Help")
        v.addWidget(self.space)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)
        self.set_id = None

    def _choose(self) -> None:
        picked = QFileDialog.getExistingDirectory(self, "Back up to which folder?")
        if picked:
            self.dest.setText(os.path.normpath(picked))
            if not self.name.text():
                serial_label = core.volume_info(picked)[1] if core._is_local_drive(picked) else None
                self.name.setText(serial_label or os.path.basename(os.path.normpath(picked)) or picked)
            self._update()

    def _picked(self) -> list[tuple[int, int]]:
        return [self.sources.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.sources.count())
                if self.sources.item(i).checkState() == Qt.CheckState.Checked]

    def _update(self) -> None:
        need = sum(size for _, size in self._picked())
        free = None
        if self.dest.text():
            try:
                free = shutil.disk_usage(self.dest.text()).free
            except OSError:
                pass
        text = f"To back up: {_gb(need)}"
        if free is not None:
            text += f"  ·  free on the drive: {_gb(free)}" + ("  - not enough for everything; it fills up and "
                                                              "then waits" if free < need else "")
        self.space.setText(text)

    def _accept(self) -> None:
        try:
            self.set_id = core.create_set(self.conn, self.name.text(), self.dest.text(),
                                          [rid for rid, _ in self._picked()],
                                          {"auto_on_connect": self.auto.isChecked()})
        except core.BackupError as e:
            QMessageBox.warning(self, "New backup", plain(e))
            return
        self.accept()


class RestoreWorker(QObject):
    done = Signal(object)

    def __init__(self, fn) -> None:
        super().__init__()
        self.fn = fn

    def run(self) -> None:
        conn = open_catalog(paths.DEFAULT_CATALOG_PATH)
        try:
            self.done.emit(self.fn(conn))
        except Exception as e:
            self.done.emit(e)
        finally:
            conn.close()


class BackupsView(QWidget):
    job_started = Signal(int)
    restart = Signal()
    library_changed = Signal()

    def __init__(self, conn, parent=None) -> None:
        super().__init__(parent)
        self.conn = conn
        self.sets: list[core.BackupSet] = []
        self._thread = None
        self.bg = Background(self, conn)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = QWidget(objectName="Toolbar")
        head.setFixedHeight(64)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(24, 0, 24, 0)
        title = QLabel("Backups")
        title.setObjectName("PageTitle")
        hl.addWidget(title)
        hl.addStretch(1)
        new = QPushButton("New backup…", clicked=self._new)
        new.setObjectName("Primary")
        hl.addWidget(new)
        outer.addWidget(head)

        body = QVBoxLayout()
        body.setContentsMargins(24, 16, 24, 16)
        intro = QLabel("Each backup mirrors the sources you choose onto a USB drive, stick or network folder. "
                       "Only new and changed files are copied, every copy is checked, and nothing is ever deleted "
                       "from a backup - a changed file's old copy is kept too. The catalog and your ratings go "
                       "with it, so a new PC can be restored from the backup alone.", objectName="Help")
        intro.setWordWrap(True)
        body.addWidget(intro)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Backup", "Where", "Status", "Backed up", "To do", "Last backup",
                                              "Last verified"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        hh = self.table.horizontalHeader()
        for c in range(7):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(1, 380)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._buttons)
        body.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.backup_b = QPushButton("Back up now", clicked=lambda: self._job(False))
        self.verify_b = QPushButton("Verify", clicked=lambda: self._job(True))
        self.verify_b.setToolTip("Re-read every copy in the backup and compare it with the hash taken when it was made")
        self.restore_b = QPushButton("Restore…", clicked=self._restore)
        self.forget_b = QPushButton("Forget this backup", clicked=self._forget)
        self.forget_b.setToolTip("Lunelis stops using it - the files on the drive are left exactly as they are")
        for b in (self.backup_b, self.verify_b, self.restore_b):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.forget_b)
        body.addLayout(row)
        self.message = QLabel(objectName="Help")
        self.message.setWordWrap(True)
        body.addWidget(self.message)
        wrap = QWidget()
        wrap.setLayout(body)
        outer.addWidget(wrap, 1)
        self.refresh()

    def refresh(self) -> None:
        # Each set's status checks its drive (a sleeping NAS can take seconds)
        # and counts its files: on a worker, never the GUI thread.
        if not self.sets and not self.table.rowCount():
            self.message.setText("Checking backups…")
        self.bg.run("sets", lambda conn: [(s, core.status(conn, s.id)) for s in core.all_sets(conn)],
                    self._fill, error=lambda e: self.message.setText(f"Couldn't read the backups: {e}"))

    def _fill(self, rows) -> None:
        keep = self._current()
        self.sets = [s for s, _st in rows]
        self.table.setRowCount(len(self.sets))
        for i, (s, st) in enumerate(rows):
            where = st.dest or s.dest_path
            if s.volume_label and s.volume_serial:
                where = f"{where}  ({s.volume_label})"
            status = s.status or "Not backed up yet"
            if not st.connected:
                status = "Drive not connected" + (" - backs up when it is" if s.options.get("auto_on_connect") else "")
            elif st.problems:
                status = f"{st.problems:,} copies need attention (run Verify / Back up now)"
            self.table.setItem(i, 0, _item(s.name))
            w = _item(where)
            w.setToolTip(where)
            self.table.setItem(i, 1, w)
            self.table.setItem(i, 2, _item(status))
            self.table.setItem(i, 3, _item(f"{st.backed_up:,} of {st.files:,}", True))
            self.table.setItem(i, 4, _item(f"{st.pending_files:,} ({_gb(st.pending_bytes)})" if st.pending_files
                                           else "Nothing", True))
            self.table.setItem(i, 5, _item(_when(s.last_run_at)))
            self.table.setItem(i, 6, _item(_when(s.last_verified_at)))
            if keep is not None and s.id == keep.id:
                self.table.selectRow(i)
        if self.sets and self._current() is None:
            self.table.selectRow(0)
        if not self.sets:
            self.message.setText("No backups yet - New backup… to make one.")
        elif self.message.text().startswith(("No backups yet", "Checking backups")):
            self.message.setText("")
        self._buttons()

    def _current(self) -> core.BackupSet | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows or rows[0].row() >= len(self.sets):
            return None
        return self.sets[rows[0].row()]

    def _buttons(self) -> None:
        on = self._current() is not None and self._thread is None
        for b in (self.backup_b, self.verify_b, self.restore_b, self.forget_b):
            b.setEnabled(on)

    def _new(self) -> None:
        dlg = NewBackupDialog(self.conn, self)
        if dlg.exec() and dlg.set_id:
            self.refresh()
            if QMessageBox.question(self, "Back up now?", "Start the first backup now?") == \
                    QMessageBox.StandardButton.Yes:
                self.job_started.emit(core.start(self.conn, dlg.set_id))
                self.message.setText("Backing up - progress in Jobs (Ctrl+J). It can be paused, and carries on "
                                     "where it stopped.")

    def _job(self, verify: bool) -> None:
        s = self._current()
        if s:
            self.job_started.emit(core.start(self.conn, s.id, verify=verify))
            self.message.setText(("Verifying" if verify else "Backing up") + f" {s.name} - progress in Jobs (Ctrl+J).")

    def _forget(self) -> None:
        s = self._current()
        if s and QMessageBox.question(
                self, "Forget this backup?",
                f"Stop using \"{s.name}\"?\n\nThe backup's files on the drive are NOT deleted - you can still "
                "copy them back by hand.") == QMessageBox.StandardButton.Yes:
            core.forget_set(self.conn, s.id)
            self.refresh()

    def _restore(self) -> None:
        s = self._current()
        if not s:
            return
        if core.resolve_dest(self.conn, s.id) is None:
            QMessageBox.information(self, "Restore", "Connect the backup drive first.")
            return
        n = len(core.restorable(self.conn, s.id))
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Restore from {s.name}")
        v = QVBoxLayout(dlg)
        group = QButtonGroup(dlg)
        opts = [QRadioButton(f"Bring back the {n:,} missing or damaged library files, to where they were"),
                QRadioButton("Copy everything in this backup into a new folder…"),
                QRadioButton("Restore the catalog (ratings, events…) from this backup - Lunelis restarts")]
        for i, b in enumerate(opts):
            group.addButton(b, i)
            v.addWidget(b)
        opts[0].setChecked(bool(n))
        opts[0].setEnabled(bool(n))
        if not n:
            opts[1].setChecked(True)
        note = QLabel("Every file is checked against the hash taken when it was backed up. A damaged file in the "
                      "library is moved to quarantine first - nothing is overwritten.", objectName="Help")
        note.setWordWrap(True)
        v.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        v.addWidget(buttons)
        if not dlg.exec():
            return
        choice = group.checkedId()
        if choice == 2:
            if QMessageBox.question(
                    self, "Restore the catalog?",
                    f"Put the catalog from {s.name} in place of the one you're using, and restart Lunelis?\n\n"
                    "Ratings, albums, events and edits go back to how they were when that backup was made. "
                    "The current catalog is kept alongside, so this can be undone by hand.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            try:
                snap = core.restore_catalog(self.conn, s.id, paths.DATA_DIR)
            except core.BackupError as e:
                QMessageBox.warning(self, "Restore", plain(e))
                return
            QMessageBox.information(self, "Restore", f"Lunelis will restart and put {snap.name} in place. The "
                                    "current catalog is kept alongside.")
            self.restart.emit()
            return
        folder = None
        if choice == 1:
            folder = QFileDialog.getExistingDirectory(self, "Restore everything into which folder?")
            if not folder:
                return
        sid = s.id
        self.message.setText("Restoring…")
        self._thread = QThread(self)
        self._worker = RestoreWorker(lambda c: core.restore_files(c, sid, to_folder=folder))
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.done.connect(self._restored)       # a bound method: handled on the GUI thread
        self._thread.start()
        self._buttons()

    @unless_closed
    def _restored(self, res) -> None:
        self._thread.quit()
        self._thread.wait()
        self._thread = None
        if isinstance(res, Exception):
            self.message.setText(f"Restore failed: {res}")
        else:
            text = f"Restored {res.restored:,} files, every one checked."
            if res.skipped:
                text += f" {res.skipped:,} skipped (already there and healthy)."
            if res.failed:
                text += f" {res.failed:,} failed: " + "; ".join(res.errors[:3])
            self.message.setText(text)
            self.library_changed.emit()
        self.refresh()
