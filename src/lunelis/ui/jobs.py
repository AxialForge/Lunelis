"""
Jobs in the UI: the background runner, the Jobs panel, and the scope dialog.

JobRunner lives on its own QThread with its own catalog connection and works
through queued jobs one at a time (lunelis.jobs.engine). The GUI thread only
ever writes job *state* (pause/resume/cancel) with its own connection and
pokes the runner; progress is read back from the catalog on a timer, so the
panel shows the same truth after a restart as during a run.
"""
from __future__ import annotations

import os
import threading
import time

from lunelis.ui.widgets import row_toggles
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
    QListWidget, QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QRadioButton,
    QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from lunelis import paths
from lunelis.catalog.schema import open_catalog
from lunelis.jobs import engine
from lunelis.log import LOG

WAKE_WAITING_S = engine.OFFLINE_RETRY_S


class JobRunner(QObject):
    changed = Signal()                    # a job started, finished or changed state

    def __init__(self) -> None:
        super().__init__()
        self._quit = False
        self._stop_current = False
        self._cancel_ids: set[int] = set()
        self._poke = threading.Event()
        self.current: int | None = None

    # called from the GUI thread
    def poke(self) -> None:
        self._poke.set()

    def stop_current(self, cancel: bool = False) -> None:
        if cancel and self.current is not None:
            self._cancel_ids.add(self.current)
        self._stop_current = True
        self._poke.set()

    def quit(self) -> None:
        self._quit = True
        self._poke.set()

    # runs on the worker thread
    def run(self) -> None:
        conn = open_catalog(paths.DEFAULT_CATALOG_PATH)
        try:
            if engine.recover_interrupted(conn):
                self.changed.emit()
            last_wake = time.monotonic()
            while not self._quit:
                if time.monotonic() - last_wake > WAKE_WAITING_S:
                    engine.wake_waiting(conn)             # offline share / schedule: try again
                    last_wake = time.monotonic()
                job = engine.next_runnable(conn)
                if job is None:
                    self._poke.wait(5)
                    self._poke.clear()
                    continue
                self.current, self._stop_current = job, False
                self.changed.emit()
                try:
                    engine.run_job(conn, job, should_stop=lambda: self._stop_current or self._quit)
                except Exception as e:
                    # One job's bug must not end every background job for the
                    # session: log it, mark that job failed, carry on with the rest.
                    LOG.exception("Background job %s failed", job)
                    try:
                        if conn.in_transaction:
                            conn.rollback()
                        engine.set_state(conn, job, "failed", f"Stopped by an error: {type(e).__name__}: {e}"[:300])
                    except Exception:
                        LOG.exception("Couldn't mark job %s failed", job)
                if job in self._cancel_ids:
                    engine.cancel(conn, job)
                    self._cancel_ids.discard(job)
                self.current = None
                self.changed.emit()
        finally:
            conn.close()


STATE_TEXT = {"queued": "Queued", "running": "Running", "waiting": "Waiting", "paused": "Paused",
              "done": "Done", "cancelled": "Cancelled", "failed": "Failed"}


class JobsDialog(QDialog):
    """The Jobs panel: every job, its progress and pause / resume / cancel."""

    def __init__(self, conn, runner: JobRunner, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Jobs")
        self.resize(980, 380)
        self.conn, self.runner = conn, runner
        v = QVBoxLayout(self)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Job", "State", "Progress", "Read", "Now"])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(2, 180)
        v.addWidget(self.table)
        row = QHBoxLayout()
        self.pause_b = QPushButton("Pause", clicked=self._pause)
        self.resume_b = QPushButton("Resume", clicked=self._resume)
        self.cancel_b = QPushButton("Cancel", clicked=self._cancel)
        self.clear_b = QPushButton("Clear finished", clicked=self._clear)
        for b in (self.pause_b, self.resume_b, self.cancel_b):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(QLabel("Jobs keep their progress if you close Lunelis or turn the PC off."))
        row.addWidget(self.clear_b)
        v.addLayout(row)
        self._ids: list[int] = []
        self.refresh()
        self._timer = QTimer(self, interval=1000, timeout=self.refresh)
        self._timer.start()
        self.table.itemSelectionChanged.connect(self._update_buttons)

    def _selected(self) -> tuple[int, str] | None:
        r = self.table.currentRow()
        if r < 0 or r >= len(self._ids):
            return None
        state = self.conn.execute("SELECT state FROM jobs WHERE id = ?", (self._ids[r],)).fetchone()
        return (self._ids[r], state[0]) if state else None

    def refresh(self) -> None:
        keep = self._selected()
        rows = self.conn.execute(
            "SELECT id, title, kind, state, status, files_done, files_total, bytes_read FROM jobs"
            " ORDER BY CASE state WHEN 'running' THEN 0 WHEN 'waiting' THEN 1 WHEN 'queued' THEN 2"
            " WHEN 'paused' THEN 3 ELSE 4 END, id DESC").fetchall()
        self._ids = [r[0] for r in rows]
        if len(rows) < self.table.rowCount():
            self.table.clearContents()                 # cell-widget bars would float (CLAUDE.md)
        self.table.setRowCount(len(rows))
        for i, (jid, title, kind, state, status, done, total, read) in enumerate(rows):
            kind_text = {"duplicates": "Find duplicates", "verify": "Verify duplicates",
                         "full_hash": "Hash everything", "integrity": "Check integrity",
                         "migrate": "Migration", "backup": "Backup", "backup_verify": "Verify backup",
                         "scene_tags": "Scene tags", "faces": "Find faces"}.get(kind, kind)
            self.table.setItem(i, 0, QTableWidgetItem(f"{kind_text}: {title}"))
            self.table.setItem(i, 1, QTableWidgetItem(STATE_TEXT.get(state, state)))
            bar = self.table.cellWidget(i, 2) or QProgressBar()
            bar.setRange(0, max(1, total))
            bar.setValue(done)
            bar.setFormat(f"{done:,} / {total:,} files")
            self.table.setCellWidget(i, 2, bar)
            self.table.setItem(i, 3, QTableWidgetItem(f"{read / 1e9:,.1f} GB" if read >= 1e8 else
                                                      f"{read / 1e6:,.0f} MB"))
            self.table.setItem(i, 4, QTableWidgetItem(status or ""))
        if keep and keep[0] in self._ids:
            self.table.selectRow(self._ids.index(keep[0]))
        self._update_buttons()

    def _update_buttons(self) -> None:
        sel = self._selected()
        state = sel[1] if sel else None
        self.pause_b.setEnabled(state in ("running", "queued", "waiting"))
        self.resume_b.setEnabled(state in ("paused", "waiting"))
        self.cancel_b.setEnabled(state in ("running", "queued", "waiting", "paused"))

    def _pause(self) -> None:
        sel = self._selected()
        if not sel:
            return
        if sel[0] == self.runner.current:
            self.runner.stop_current()
        else:
            engine.pause(self.conn, sel[0])
        self.refresh()

    def _resume(self) -> None:
        sel = self._selected()
        if sel:
            engine.resume(self.conn, sel[0])
            self.runner.poke()
            self.refresh()

    def _cancel(self) -> None:
        sel = self._selected()
        if not sel:
            return
        if sel[0] == self.runner.current:
            self.runner.stop_current(cancel=True)
        else:
            engine.cancel(self.conn, sel[0])
        self.refresh()

    def _clear(self) -> None:
        self.conn.execute("DELETE FROM jobs WHERE state IN ('done', 'cancelled', 'failed')")
        self.conn.commit()
        self.refresh()


class ScopeDialog(QDialog):
    """Choose where and when a job runs. result: (title, scope, options)."""

    def __init__(self, conn, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.conn = conn
        self.folder: tuple[int, str] | None = None
        v = QVBoxLayout(self)
        v.addWidget(QLabel("Where to look:"))
        self.roots = QListWidget()
        row_toggles(self.roots)
        for rid, path, n in conn.execute(
                "SELECT r.id, r.path, (SELECT COUNT(*) FROM files f WHERE f.root_id = r.id"
                " AND f.missing_since IS NULL AND f.excluded = 0) FROM roots r WHERE r.enabled = 1 ORDER BY r.id"):
            item = QListWidgetItem(f"{path}   ({n:,} files)")
            item.setData(Qt.ItemDataRole.UserRole, (rid, path))
            item.setCheckState(Qt.CheckState.Unchecked)
            self.roots.addItem(item)
        v.addWidget(self.roots)
        row = QHBoxLayout()
        self.folder_label = QLabel("…or just one folder:")
        row.addWidget(self.folder_label, 1)
        row.addWidget(QPushButton("Choose folder…", clicked=self._choose_folder))
        v.addLayout(row)

        v.addWidget(QLabel("When to run:"))
        # Defaults come from Settings > Duplicates and background jobs.
        from lunelis.settings import Settings
        s = Settings(conn)
        self._idle = s.get("job_idle_minutes")
        self._hours = (s.get("job_window_start_hour"), s.get("job_window_end_hour"))
        self.when = QButtonGroup(self)
        default = ("now", "idle", "window").index(s.get("job_default_when"))
        for i, text in enumerate(("Now", f"Only while the PC is idle ({self._idle} min)",
                                  f"Only between {self._hours[0]:02d}:00 and {self._hours[1]:02d}:00")):
            b = QRadioButton(text)
            b.setChecked(i == default)
            self.when.addButton(b, i)
            v.addWidget(b)
        speed = QHBoxLayout()
        speed.addWidget(QLabel("Speed limit (MB/s, 0 = none):"))
        self.mbps = QSpinBox(minimum=0, maximum=100_000, value=s.get("job_mb_per_s"))
        speed.addWidget(self.mbps)
        speed.addStretch(1)
        v.addLayout(speed)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)
        self.result_value = None

    def _choose_folder(self) -> None:
        picked = QFileDialog.getExistingDirectory(self, "Choose a folder inside one of your sources")
        if not picked:
            return
        norm = os.path.normcase(os.path.normpath(picked))
        for i in range(self.roots.count()):
            rid, path = self.roots.item(i).data(Qt.ItemDataRole.UserRole)
            base = os.path.normcase(os.path.normpath(path))
            if norm == base or norm.startswith(base.rstrip("\\") + "\\"):
                rel = os.path.relpath(os.path.normpath(picked), path).replace("\\", "/")
                self.folder = (rid, "" if rel == "." else rel)
                self.folder_label.setText(f"Just this folder: {picked}")
                return
        QMessageBox.warning(self, "Not in the library", "That folder isn't inside any of your sources.")

    def _accept(self) -> None:
        scope: list[tuple[int, str | None]] = []
        names = []
        if self.folder:
            rid, rel = self.folder
            scope.append((rid, rel or None))
            if rel:
                names.append(rel.rsplit("/", 1)[-1])
            else:                                       # the source itself: its own name, not the first one's
                path = next(self.roots.item(i).data(Qt.ItemDataRole.UserRole)[1] for i in range(self.roots.count())
                            if self.roots.item(i).data(Qt.ItemDataRole.UserRole)[0] == rid)
                names.append(os.path.basename(path.rstrip("\\")) or path)
        for i in range(self.roots.count()):
            item = self.roots.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                rid, path = item.data(Qt.ItemDataRole.UserRole)
                scope.append((rid, None))
                names.append(os.path.basename(path.rstrip("\\")) or path)
        if not scope:
            QMessageBox.information(self, "Nothing chosen", "Tick a source or choose a folder.")
            return
        schedule = [{"mode": "now"}, {"mode": "idle", "idle_minutes": self._idle},
                    {"mode": "window", "start_hour": self._hours[0], "end_hour": self._hours[1]}
                    ][self.when.checkedId()]
        options = {"schedule": schedule, "mb_per_s": self.mbps.value() or None}
        self.result_value = (", ".join(names), scope, options)
        self.accept()

