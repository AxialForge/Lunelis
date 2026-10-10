"""
The Quarantine page (sidebar > Keep safe > Quarantine): everything Lunelis
has set aside, why, and whether the copy that stays is still there.
Restore puts files back; Empty removes them for good (dupes/manage.py -
local drives to the Recycle Bin, network shares deleted, never a file
whose kept copy is missing).
"""
from __future__ import annotations

import os
import subprocess

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QProgressDialog, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from lunelis import paths
from lunelis.dupes import manage
from lunelis.dupes.quarantine import QuarantineRefused
from lunelis.ui.background import unless_closed
from lunelis.ui.widgets import plain, sharp


def _local_date(iso: str | None) -> str:
    # Stored in UTC; shown as the local date (an evening set-aside isn't "tomorrow").
    if not iso:
        return ""
    from datetime import datetime, timezone
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso[:10]
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%Y-%m-%d")


def _size(n) -> str:
    from lunelis.ui.sizes import human
    return human(n)


def db_file(conn) -> str:
    """The catalog file this connection is on (workers open their own)."""
    return conn.execute("PRAGMA database_list").fetchone()[2]


class LoadWorker(QObject):
    """The entries, with their disk checks done here rather than on the GUI thread."""
    done = Signal(object)

    def __init__(self, db: str) -> None:
        super().__init__()
        self.db = db

    def run(self) -> None:
        from lunelis.catalog.schema import open_catalog
        try:
            conn = open_catalog(self.db)
            try:
                items = [e.probe() for e in manage.entries(conn)]
            finally:
                conn.close()
            self.done.emit(items)
        except Exception as e:                     # shown on the page, never lost
            self.done.emit(e)


class RestoreWorker(QObject):
    progress = Signal(int, int)
    done = Signal(object)

    def __init__(self, db: str, keys: set[str]) -> None:
        super().__init__()
        self.db = db
        self.keys = keys
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        from lunelis.catalog.schema import open_catalog
        back, problems, originals = 0, [], False
        try:
            conn = open_catalog(self.db)
            try:
                items = [e for e in manage.entries(conn) if e.key in self.keys]
                for i, e in enumerate(items, 1):
                    if self._cancel:
                        break
                    originals |= e.reason == "original"
                    try:
                        manage.restore_entry(conn, e)
                        back += 1
                    except (QuarantineRefused, OSError) as err:
                        problems.append(f"{os.path.basename(e.original)}: {err}")
                    self.progress.emit(i, len(items))
            finally:
                conn.close()
            self.done.emit((back, problems, originals))
        except Exception as e:
            self.done.emit(e)


class EmptyWorker(QObject):
    progress = Signal(int, int)
    done = Signal(object)

    def __init__(self, db: str, keys: set[str]) -> None:
        super().__init__()
        self.db = db
        self.keys = keys
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        from lunelis.catalog.schema import open_catalog
        conn = open_catalog(self.db)
        try:
            items = [e for e in manage.entries(conn) if e.key in self.keys]
            # The snapshot goes where Settings keeps catalog backups (0.54: always the default folder).
            from lunelis.catalog import backup
            from lunelis.settings import Settings
            res = manage.empty(conn, items, backup_dir=backup.backup_dir(Settings(conn), paths.DATA_DIR),
                               on_progress=self.progress.emit, should_cancel=lambda: self._cancel)
        except Exception as e:                     # reported, so the dialog never hangs
            res = e
        finally:
            conn.close()
        self.done.emit(res)


class ConfirmEmpty(QDialog):
    def __init__(self, items: list, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Empty quarantine")
        v = QVBoxLayout(self)
        net = [e for e in items if manage._is_network(e.now)]
        local = [e for e in items if e not in net]
        bad = [e for e in items if not e.kept_ok()]
        lines = [f"Remove {len(items):,} file{'s' if len(items) != 1 else ''} "
                 f"({_size(sum(e.size for e in items))}) from quarantine for good?", ""]
        if local:
            lines.append(f"• {len(local):,} on this PC go to the Recycle Bin (you can still get them back there).")
        if net:
            lines.append(f"• {len(net):,} on network, USB or memory-card drives are DELETED - Windows has no "
                         "Recycle Bin there, so they can't be recovered.")
        if bad:
            lines.append(f"• {len(bad):,} stay in quarantine: the copy that was kept can't be found.")
        lines += ["", "The catalog is backed up first. Every file removed is written to a log."]
        text = QLabel("\n".join(lines))
        text.setWordWrap(True)
        v.addWidget(text)
        self.agree = QCheckBox("I understand those files can't be recovered")
        self.agree.setVisible(bool(net))
        v.addWidget(self.agree)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.go = buttons.addButton("Empty", QDialogButtonBox.ButtonRole.DestructiveRole)
        self.go.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.go.setEnabled(not net)
        self.agree.toggled.connect(self.go.setEnabled)
        v.addWidget(buttons)


class QuarantineView(QWidget):
    changed = Signal()                     # files came back (the library should reload)

    def __init__(self, conn, parent=None) -> None:
        super().__init__(parent)
        self.conn = conn
        self.items: list[manage.Entry] = []
        self._thread = None
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 16, 24, 16)
        top = QHBoxLayout()
        top.addWidget(QLabel("Quarantine", objectName="SectionTitle"))
        self.summary = QLabel(objectName="Count")
        self.summary.setWordWrap(True)
        top.addWidget(self.summary, 1)
        top.addWidget(QLabel("Show:"))
        self.reason = QComboBox()
        self.reason.addItem("Everything", None)
        for key, label in manage.REASONS.items():
            self.reason.addItem(label, key)
        self.reason.currentIndexChanged.connect(lambda _: self._fill())
        top.addWidget(self.reason)
        v.addLayout(top)
        hint = QLabel("Files Lunelis set aside instead of deleting: duplicate copies, near-duplicates, and "
                      "originals of migrated photos. Each sits in a “_Lunelis Quarantine” folder on its own "
                      "drive. Restore puts one back; Empty removes it for good - only when the copy that was "
                      "kept is still there.", objectName="Hint")
        hint.setWordWrap(True)
        v.addWidget(hint)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["", "File", "Why", "Set aside", "Size", "Kept copy"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 60)
        self.table.setColumnWidth(2, 130)
        self.table.setColumnWidth(3, 110)
        self.table.setColumnWidth(4, 90)
        self.table.itemSelectionChanged.connect(self._buttons)
        from lunelis.ui.empty_state import EmptyStack
        self.table_box = EmptyStack(self.table)
        v.addWidget(self.table_box, 1)
        row = QHBoxLayout()
        self.restore_b = QPushButton("Restore", clicked=self.restore_selected)
        self.restore_b.setToolTip("Put the selected files back where they were")
        self.show_b = QPushButton("Show in Explorer", clicked=self._show)
        self.empty_b = QPushButton("Empty selected…", clicked=lambda: self._empty(self._selected()))
        self.empty_all_b = QPushButton("Empty all…", clicked=lambda: self._empty(self._visible()))
        # Kept for a while, then offered for removal - never removed without asking.
        self.keep = QComboBox()
        for label, days in (("Keep set-aside files forever", 0), ("Offer to remove after 30 days", 30),
                            ("Offer to remove after 90 days", 90), ("Offer to remove after a year", 365)):
            self.keep.addItem(label, days)
        from lunelis.settings import Settings
        self.keep.setCurrentIndex(max(0, self.keep.findData(Settings(self.conn).get("trash_keep_days"))))
        self.keep.currentIndexChanged.connect(self._keep_changed)
        self.due_b = QPushButton("Remove what's past its time…", clicked=lambda: self._empty(self._due()))
        for b in (self.restore_b, self.show_b):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.keep)
        row.addWidget(self.due_b)
        for b in (self.empty_b, self.empty_all_b):
            row.addWidget(b)
        v.addLayout(row)

    # --- data ---

    def refresh(self) -> None:
        if self._thread is not None:
            return
        self.summary.setText("Checking the quarantine folders…")
        self._run(LoadWorker(db_file(self.conn)), self._loaded)

    def _run(self, worker: QObject, then) -> None:
        self._thread = QThread(self)
        self._worker = worker
        worker.moveToThread(self._thread)
        self._thread.started.connect(worker.run)
        worker.done.connect(then)
        self._buttons()
        self._thread.start()

    def _end(self) -> None:
        self._thread.quit()
        self._thread.wait()
        self._thread = None

    @unless_closed
    def _loaded(self, result) -> None:
        self._end()
        if isinstance(result, Exception):
            self.summary.setText(f"Couldn't read the quarantine: {result}")
            self._buttons()
            return
        self.items = result
        per_drive = manage.summary(self.items)
        if not self.items:
            self.summary.setText("Nothing in quarantine.")
        else:
            total = sum(e.size for e in self.items)
            drives = ", ".join(f"{d} {n:,} file{'s' if n != 1 else ''} ({_size(b)})"
                               for d, (n, b) in per_drive.items())
            n = len(self.items)
            self.summary.setText(f"{n:,} file{'s' if n != 1 else ''} · {_size(total)} · on {drives}")
        self._fill()

    def _due(self) -> list[manage.Entry]:
        from lunelis.settings import Settings
        return manage.due(self.items, Settings(self.conn).get("trash_keep_days"))

    def _keep_changed(self, _i: int) -> None:
        from lunelis.settings import Settings
        Settings(self.conn).set("trash_keep_days", self.keep.currentData())
        self._buttons()

    def _visible(self) -> list[manage.Entry]:
        r = self.reason.currentData()
        return [e for e in self.items if r is None or e.reason == r]

    def _fill(self) -> None:
        shown = self._visible()
        self.table_box.empty(None if shown else
                             "Nothing set aside.\n\nCopies you set aside on the Duplicates page, and originals a "
                             "migration moved, wait here until you put them back or empty them." if not self.items
                             else "Nothing of this kind - choose Everything above.")
        self.table.setRowCount(0)
        self.table.setRowCount(len(shown))
        for i, e in enumerate(shown):
            pic = QLabel()
            if e.thumbnail:
                pm = QPixmap(str(paths.THUMBNAIL_CACHE / e.thumbnail))
                if not pm.isNull():
                    pic.setPixmap(sharp(pm, 52, pic))
            self.table.setCellWidget(i, 0, pic)
            name = QTableWidgetItem(e.original)
            name.setData(Qt.ItemDataRole.UserRole, e.key)
            name.setToolTip(f"Was: {e.original}\nNow: {e.now}")
            self.table.setItem(i, 1, name)
            self.table.setItem(i, 2, QTableWidgetItem(manage.REASONS.get(e.reason, e.reason)))
            self.table.setItem(i, 3, QTableWidgetItem(_local_date(e.when)))
            size = QTableWidgetItem(_size(e.size))
            size.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(i, 4, size)
            if not e.present:
                kept = QTableWidgetItem("⚠ No longer in the quarantine folder")
            elif e.kept_ok():
                kept = QTableWidgetItem("✓ " + e.kept)
            else:
                kept = QTableWidgetItem("⚠ Missing - this file won't be emptied" + (f": {e.kept}" if e.kept else ""))
            kept.setToolTip(e.kept or "")
            self.table.setItem(i, 5, kept)
            self.table.setRowHeight(i, 58)
        self._buttons()

    def _selected(self) -> list[manage.Entry]:
        keys = {self.table.item(r.row(), 1).data(Qt.ItemDataRole.UserRole)
                for r in self.table.selectionModel().selectedRows()}
        return [e for e in self.items if e.key in keys]

    def _buttons(self) -> None:
        sel = bool(self.table.selectionModel().selectedRows())
        idle = self._thread is None
        self.restore_b.setEnabled(sel and idle)
        self.show_b.setEnabled(sel)
        self.empty_b.setEnabled(sel and idle)
        self.empty_all_b.setEnabled(bool(self._visible()) and idle)
        n = len(self._due())
        self.due_b.setText(f"Remove {n:,} past their time…" if n else "Nothing past its time")
        self.due_b.setEnabled(bool(n) and idle)

    # --- actions ---

    def _show(self) -> None:
        sel = self._selected()
        if sel:
            path = sel[0].now if sel[0].present else os.path.dirname(sel[0].now)
            subprocess.Popen(["explorer", "/select,", path])

    def restore_selected(self) -> None:
        sel = self._selected()
        if not sel or self._thread is not None:
            return
        self._progress = QProgressDialog("Restoring…", "Stop", 0, len(sel), self)
        self._progress.setWindowTitle("Restore")
        self._progress.setMinimumDuration(400)
        worker = RestoreWorker(db_file(self.conn), {e.key for e in sel})
        worker.progress.connect(self._step)
        self._progress.canceled.connect(lambda: worker.cancel())   # direct: the worker's thread is busy
        self._run(worker, self._restored)

    @unless_closed
    def _restored(self, result) -> None:
        self._end()
        self._progress.close()
        if isinstance(result, Exception):
            QMessageBox.warning(self, "Restore", f"Restoring stopped: {plain(result)}")
            self.refresh()
            return
        back, problems, originals = result
        msg = f"Restored {back:,} file{'s' if back != 1 else ''} to where {'they' if back != 1 else 'it'} came from."
        if originals:
            msg += ("\n\nA migrated original that comes back is a second copy of the photo in its new place - "
                    "the next scan lists it again.")
        if problems:
            msg += "\n\nNot restored:\n" + "\n".join(problems[:8])
        QMessageBox.information(self, "Restore", msg)
        self.refresh()
        self.changed.emit()

    def _empty(self, items: list[manage.Entry]) -> None:
        items = [e for e in items if e.present]
        if not items:
            return
        dlg = ConfirmEmpty(items, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._progress = QProgressDialog("Emptying…", "Stop", 0, len(items), self)
        self._progress.setWindowTitle("Empty quarantine")
        self._progress.setMinimumDuration(0)
        self._thread = QThread(self)
        self._worker = EmptyWorker(db_file(self.conn), {e.key for e in items})
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._step)
        self._worker.done.connect(self._emptied)
        self._progress.canceled.connect(lambda: self._worker.cancel())
        self._buttons()
        self._thread.start()

    def _step(self, i: int, n: int) -> None:
        self._progress.setValue(i)

    @unless_closed
    def _emptied(self, res) -> None:
        self._thread.quit()
        self._thread.wait()
        self._thread = None
        self._progress.close()
        if isinstance(res, Exception):
            QMessageBox.warning(self, "Empty quarantine", f"Emptying stopped: {plain(res)}\n\nNothing was lost: "
                                "what wasn't removed yet is still in quarantine.")
            self.refresh()
            return
        parts = []
        if res.recycled:
            parts.append(f"{res.recycled:,} sent to the Recycle Bin")
        if res.deleted:
            parts.append(f"{res.deleted:,} deleted from network drives")
        msg = (", ".join(parts) or "Nothing removed") + f" - {_size(res.bytes)} freed."
        if res.skipped:
            msg += f"\n\n{len(res.skipped):,} stayed in quarantine:\n" + "\n".join(
                f"{os.path.basename(e.original)}: {why}" for e, why in res.skipped[:8])
        QMessageBox.information(self, "Empty quarantine", msg)
        self.refresh()
