"""
The Damaged files page: every file that can't be what it claims, what's
wrong with it, and where an intact copy survives. Read-only - Lunelis never
repairs or replaces a file on its own; restoring a good copy is the user's
call (and, later, a safe-transfer job).
"""
from __future__ import annotations

import os
import subprocess

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QHeaderView, QLabel, QPushButton, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from lunelis import paths
from lunelis.catalog.schema import open_catalog
from lunelis.damage.check import NO_MOOV, PROBLEM_TEXT, check, survivors

# A video with no index can often be rebuilt from a good clip off the same
# camera. Lunelis doesn't repair files itself; this says how.
UNTRUNC_GUIDE = (
    "This video was cut off before the camera finished writing it (a flat battery, a full or"
    " pulled card), so it has no index and no player can open it. The picture data is usually"
    " still there. To try to rebuild it:\n"
    "1. Get untrunc (free, open source: github.com/anthwlock/untrunc - Windows builds are on its"
    " Releases page).\n"
    "2. Find a working clip from the same camera with the same settings (resolution, frame rate).\n"
    "3. Run:  untrunc  good-clip.mp4  broken-clip.mp4\n"
    "4. It writes broken-clip.mp4_fixed.mp4 beside the broken one - the original is left as it is."
    " Check the fixed copy plays, then keep whichever you want.")
from lunelis.ui.background import unless_closed


# Short words for the summary line ("80 zero-filled, 44 corrupt, 2 empty").
SHORT = {"zero_bytes": "empty", "zero_filled": "zero-filled", "unrecognised": "unrecognised",
         "truncated": "cut short", "corrupt": "corrupt", "changed_on_disk": "changed on disk"}


class CheckWorker(QObject):
    done = Signal(object)

    def run(self) -> None:
        conn = open_catalog(paths.DEFAULT_CATALOG_PATH)
        try:
            self.done.emit(check(conn))
        except Exception as e:
            self.done.emit(e)
        finally:
            conn.close()


def _explorer(path: str) -> None:
    subprocess.Popen(["explorer", "/select,", path])


class DamagedView(QWidget):
    def __init__(self, conn, parent=None) -> None:
        super().__init__(parent)
        self.conn = conn
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 16, 24, 16)
        top = QHBoxLayout()
        self.summary = QLabel()
        self.summary.setObjectName("Count")
        top.addWidget(self.summary, 1)
        self.show_dismissed = QCheckBox("Show dismissed")
        self.show_dismissed.toggled.connect(self.refresh)
        top.addWidget(self.show_dismissed)
        self.check_b = QPushButton("Check now", clicked=self.run_check)
        top.addWidget(self.check_b)
        v.addLayout(top)

        split = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Problem", "File", "Best surviving copy", ""])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 300)
        # Long UNC paths: keep the share at the start and the file name at the
        # end. With word wrap on, Qt broke them at backslashes and showed "\...".
        for t in (self.table,):
            t.setWordWrap(False)
            t.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.table.setColumnWidth(3, 110)
        self.table.currentCellChanged.connect(lambda r, *_: self._show(r))
        from lunelis.ui.empty_state import EmptyStack
        self.table_box = EmptyStack(self.table)
        split.addWidget(self.table_box)
        self.detail = QTableWidget(0, 3)
        self.detail.setHorizontalHeaderLabels(["Intact copy", "Why it's a good copy", ""])
        self.detail.verticalHeader().hide()
        self.detail.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.detail.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.detail.setColumnWidth(1, 260)
        self.detail.setColumnWidth(2, 150)
        self.detail.setWordWrap(False)
        self.detail.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.guide = QLabel(UNTRUNC_GUIDE, wordWrap=True, objectName="Hint")
        self.guide.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.guide.hide()
        v.addWidget(self.guide)
        split.addWidget(self.detail)
        split.setSizes([500, 220])
        v.addWidget(split, 1)
        self._rows: list[tuple] = []
        self._thread = None

    def refresh(self) -> None:
        where = "" if self.show_dismissed.isChecked() else " AND d.dismissed = 0"
        rows = self.conn.execute(
            "SELECT d.file_id, d.problem, d.dismissed, r.path, f.rel_path, d.detail FROM damaged d"
            " JOIN files f ON f.id = d.file_id JOIN roots r ON r.id = f.root_id"
            f" WHERE f.missing_since IS NULL AND f.excluded = 0 AND f.quarantined_at IS NULL{where}"
            " ORDER BY d.problem, r.path, f.rel_path").fetchall()
        # Files with no intact copy anywhere first - they're the ones at risk.
        self._copies = {r[0]: survivors(self.conn, r[0]) for r in rows}
        self._rows = sorted(rows, key=lambda r: bool(self._copies[r[0]]))
        counts: dict[str, int] = {}
        for _, p, *_ in self._rows:
            counts[p] = counts.get(p, 0) + 1
        without = 0
        self.table.clearContents()
        self.table.setRowCount(len(self._rows))
        for i, (fid, problem, dismissed, root, rel, _detail) in enumerate(self._rows):
            full = os.path.join(root, *rel.split("/"))
            best = self._copies[fid]
            without += not best
            self.table.setItem(i, 0, QTableWidgetItem(PROBLEM_TEXT.get(problem, problem)
                                                      + (" (dismissed)" if dismissed else "")))
            self.table.setItem(i, 1, QTableWidgetItem(full))
            self.table.setItem(i, 2, QTableWidgetItem(
                f"{best[0][2]}: {best[0][1]}" if best else "No intact copy found in the library"))
            b = QPushButton("Dismiss" if not dismissed else "Restore",
                            clicked=lambda _=False, f=fid, d=dismissed: self._dismiss(f, not d))
            self.table.setCellWidget(i, 3, b)
        parts = [f"{n:,} {SHORT.get(p, p)}" for p, n in sorted(counts.items(), key=lambda kv: -kv[1])]
        self.summary.setText(
            (f"{len(self._rows):,} damaged files: " + ", ".join(parts) +
             f" · {without:,} with no intact copy anywhere") if self._rows else "No damaged files found.")
        self.table_box.empty(None if self._rows else
                             "No damaged files found.\n\nThe check runs after each scan, reading only files whose "
                             "start isn't a picture Lunelis recognises - press Check now to run it again.")
        if self._rows:
            self.table.selectRow(0)
        else:
            self.detail.clearContents()
            self.detail.setRowCount(0)

    def _show(self, row: int) -> None:
        self.detail.clearContents()
        self.detail.setRowCount(0)
        self.guide.hide()
        if row < 0 or row >= len(self._rows):
            return
        self.guide.setVisible(self._rows[row][5] == NO_MOOV)
        copies = self._copies.get(self._rows[row][0], [])
        self.detail.setRowCount(len(copies))
        for i, (_, full, why) in enumerate(copies):
            self.detail.setItem(i, 0, QTableWidgetItem(full))
            self.detail.setItem(i, 1, QTableWidgetItem(why))
            self.detail.setCellWidget(i, 2, QPushButton("Show in Explorer",
                                                        clicked=lambda _=False, p=full: _explorer(p)))

    def _dismiss(self, file_id: int, dismissed: bool) -> None:
        self.conn.execute("UPDATE damaged SET dismissed = ? WHERE file_id = ?", (int(dismissed), file_id))
        self.conn.commit()
        self.refresh()

    def run_check(self) -> None:
        if self._thread is not None:
            return
        self._thread = QThread(self)
        self._worker = CheckWorker()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.done.connect(self._check_done)
        self.check_b.setEnabled(False)
        self.summary.setText("Checking…")
        self._thread.start()

    @unless_closed
    def _check_done(self, result) -> None:
        self._thread.quit()
        self._thread.wait()
        self._thread = None
        self.check_b.setEnabled(True)
        self.refresh()
        if isinstance(result, Exception):
            self.summary.setText(f"The check stopped: {result} (details in the log)")
