"""
The Duplicates view. Tab 1, Exact copies: every group of identical copies,
which copy to keep, and moving the extras to quarantine. Tab 2,
Near-duplicates: near_view.py.

"Likely" groups come from the sampled pass; "Verified" ones have had every
byte compared. Only verified groups can be quarantined (dupes/quarantine.py
enforces that too). Nothing here deletes anything.
"""
from __future__ import annotations

import os
import subprocess
from collections import defaultdict
from dataclasses import dataclass

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, Qt, QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton, QSplitter,
    QTableView, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from lunelis import paths
from lunelis.catalog.schema import open_catalog
from lunelis.dupes import detect
from lunelis.dupes.quarantine import QUARANTINE_DIR, QuarantineRefused, quarantine
from lunelis.raw.thumbnails import cache_rel_path
from lunelis.settings import Settings
from lunelis.ui.near_view import NearView


@dataclass
class Group:
    id: int
    verified: bool
    size: int
    members: list[tuple]          # (file id, root id, root path, rel path, thumbnail)
    keeper: int

    @property
    def extra_bytes(self) -> int:
        return self.size * (len(self.members) - 1)


def load_groups(conn, preferred_roots: list[int]) -> list[Group]:
    """Verified groups, plus likely groups not yet fully verified; biggest
    savings first. One query, grouped in Python (tens of thousands of rows)."""
    rows = conn.execute(
        "SELECT g.id, g.method, g.verified, f.id, f.root_id, r.path, f.rel_path, f.size_bytes,"
        "       f.thumbnail_path, f.content_hash IS NOT NULL"
        " FROM duplicate_groups g JOIN duplicate_group_files m ON m.group_id = g.id"
        " JOIN files f ON f.id = m.file_id JOIN roots r ON r.id = f.root_id"
        " WHERE g.method IN ('exact', 'sampled')"
        " AND f.missing_since IS NULL AND f.excluded = 0 AND f.quarantined_at IS NULL").fetchall()
    by: dict[int, list] = defaultdict(list)
    meta: dict[int, tuple] = {}
    for gid, method, verified, fid, rid, root, rel, size, thumb, hashed in rows:
        by[gid].append((fid, rid, root, rel, thumb, hashed))
        meta[gid] = (method, bool(verified), size)
    rank = detect.keeper_rank(preferred_roots)
    groups = []
    for gid, members in by.items():
        method, verified, size = meta[gid]
        if len(members) < 2:
            continue
        if method == "sampled" and all(m[5] for m in members):
            continue                      # already represented by its verified group
        members.sort(key=rank)
        groups.append(Group(gid, method == "exact", size, [m[:5] for m in members], members[0][0]))
    groups.sort(key=lambda g: (-g.extra_bytes, g.id))
    return groups


def _gb(n: int) -> str:
    return f"{n / 1e9:,.1f} GB" if n >= 1e8 else f"{n / 1e6:,.0f} MB"


class GroupModel(QAbstractTableModel):
    HEADERS = ["Status", "Copies", "Size", "Extra space", "Keep", "Other copies"]

    def __init__(self) -> None:
        super().__init__()
        self.groups: list[Group] = []

    def set_groups(self, groups: list[Group]) -> None:
        self.beginResetModel()
        self.groups = groups
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.groups)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        g = self.groups[index.row()]
        keep = next(m for m in g.members if m[0] == g.keeper)
        c = index.column()
        if c == 0:
            return "Verified" if g.verified else "Likely"
        if c == 1:
            return str(len(g.members))
        if c == 2:
            return _gb(g.size)
        if c == 3:
            return _gb(g.extra_bytes)
        if c == 4:
            return f"{os.path.basename(keep[2].rstrip(chr(92)))}/{keep[3]}"
        return "; ".join(f"{os.path.basename(m[2].rstrip(chr(92)))}/{m[3]}"
                         for m in g.members if m[0] != g.keeper)


class QuarantineWorker(QObject):
    """Moves the extra copies of every verified group to quarantine."""

    progress = Signal(int, int)
    done = Signal(int, int, list)          # files moved, bytes freed, errors

    def __init__(self, preferred_roots: list[int]) -> None:
        super().__init__()
        self.preferred = preferred_roots

    def run(self) -> None:
        conn = open_catalog(paths.DEFAULT_CATALOG_PATH)
        moved = freed = 0
        errors: list[str] = []
        try:
            groups = [g for g in load_groups(conn, self.preferred) if g.verified]
            backup_dir = paths.BACKUP_DIR           # one snapshot, before the first move
            for i, g in enumerate(groups, 1):
                others = [m[0] for m in g.members if m[0] != g.keeper]
                try:
                    quarantine(conn, g.id, others, backup_dir=backup_dir)
                    backup_dir = None
                    moved += len(others)
                    freed += g.extra_bytes
                except (QuarantineRefused, OSError) as e:
                    errors.append(f"group {g.id}: {e}")
                self.progress.emit(i, len(groups))
        finally:
            conn.close()
            self.done.emit(moved, freed, errors)


class DuplicatesView(QWidget):
    start_verify = Signal()                # ask the window to queue a verify job

    def __init__(self, conn, parent=None) -> None:
        super().__init__(parent)
        self.conn = conn
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 16, 24, 16)
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs)
        exact = QWidget()
        self.tabs.addTab(exact, "Exact copies")
        self.near = NearView(conn)
        self.tabs.addTab(self.near, "Near-duplicates")
        self.tabs.currentChanged.connect(self._tab_changed)
        v = QVBoxLayout(exact)
        v.setContentsMargins(0, 12, 0, 0)
        top = QHBoxLayout()
        self.summary = QLabel()
        self.summary.setObjectName("Count")
        top.addWidget(self.summary, 1)
        top.addWidget(QLabel("Keep copies in:"))
        self.prefer = QComboBox()
        self.prefer.currentIndexChanged.connect(self._prefer_changed)
        top.addWidget(self.prefer)
        self.verify_b = QPushButton("Verify likely groups…", clicked=lambda: self.start_verify.emit())
        self.quarantine_b = QPushButton("Move extra copies to quarantine…", clicked=self._quarantine_all)
        top.addWidget(self.verify_b)
        top.addWidget(self.quarantine_b)
        v.addLayout(top)

        split = QSplitter(Qt.Orientation.Vertical)
        self.model = GroupModel()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(4, 360)
        self.table.selectionModel().currentRowChanged.connect(lambda cur, _: self._show(cur.row()))
        split.addWidget(self.table)
        self.detail = QTableWidget(0, 4)
        self.detail.setHorizontalHeaderLabels(["", "Location", "", ""])
        self.detail.verticalHeader().hide()
        self.detail.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.detail.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.detail.setWordWrap(False)                  # UNC paths break at every backslash otherwise
        self.detail.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        split.addWidget(self.detail)
        split.setSizes([500, 260])
        v.addWidget(split, 1)
        self._thread = None

    # --- data ----------------------------------------------------------------

    def refresh(self) -> None:
        s = Settings(self.conn)
        preferred = s.get("preferred_roots")
        self.prefer.blockSignals(True)
        self.prefer.clear()
        self.prefer.addItem("Wherever (shallowest folder, not Takeout)", None)
        for rid, path in self.conn.execute("SELECT id, path FROM roots WHERE enabled = 1 ORDER BY id"):
            self.prefer.addItem(path, rid)
            if preferred and preferred[0] == rid:
                self.prefer.setCurrentIndex(self.prefer.count() - 1)
        self.prefer.blockSignals(False)
        groups = load_groups(self.conn, preferred)
        self.model.set_groups(groups)
        if self.tabs.currentIndex() == 1:
            self.near.refresh()
        verified = [g for g in groups if g.verified]
        likely = len(groups) - len(verified)
        self.summary.setText(
            f"{len(groups):,} groups · {_gb(sum(g.extra_bytes for g in groups))} in extra copies · "
            f"{len(verified):,} verified ({_gb(sum(g.extra_bytes for g in verified))}), {likely:,} likely")
        self.verify_b.setEnabled(likely > 0)
        self.quarantine_b.setEnabled(bool(verified) and self._thread is None)
        if groups:
            self.table.selectRow(0)
        else:
            self.detail.setRowCount(0)

    def _tab_changed(self, index: int) -> None:
        if index == 1:
            self.near.refresh()

    def _prefer_changed(self) -> None:
        rid = self.prefer.currentData()
        Settings(self.conn).set("preferred_roots", [rid] if rid else [])
        self.refresh()

    def _show(self, row: int) -> None:
        if row < 0 or row >= len(self.model.groups):
            return
        g = self.model.groups[row]
        # clearContents() also deletes cell widgets; shrinking the row count
        # alone can leave the previous group's buttons floating over the table.
        self.detail.clearContents()
        self.detail.setRowCount(0)
        self.detail.setRowCount(len(g.members))
        for i, (fid, rid, root, rel, thumb) in enumerate(g.members):
            pic = QLabel()
            pm = QPixmap(str(paths.THUMBNAIL_CACHE / (thumb or cache_rel_path(fid))))
            if not pm.isNull():
                pic.setPixmap(pm.scaled(72, 72, Qt.AspectRatioMode.KeepAspectRatio,
                                        Qt.TransformationMode.SmoothTransformation))
            self.detail.setCellWidget(i, 0, pic)
            full = os.path.join(root, *rel.split("/"))
            self.detail.setItem(i, 1, QTableWidgetItem(full))
            self.detail.setItem(i, 2, QTableWidgetItem("Keep" if fid == g.keeper else "Extra copy"))
            show = QPushButton("Show in Explorer",
                               clicked=lambda _=False, p=full: subprocess.Popen(["explorer", "/select,", p]))
            self.detail.setCellWidget(i, 3, show)
            self.detail.setRowHeight(i, 78)
        self.detail.setColumnWidth(0, 80)
        self.detail.setColumnWidth(2, 90)
        self.detail.setColumnWidth(3, 150)

    # --- quarantine ------------------------------------------------------------

    def _quarantine_all(self) -> None:
        verified = [g for g in self.model.groups if g.verified]
        n = sum(len(g.members) - 1 for g in verified)
        answer = QMessageBox.question(
            self, "Move extra copies to quarantine",
            f"Move {n:,} extra copies ({_gb(sum(g.extra_bytes for g in verified))}) from "
            f"{len(verified):,} verified groups into a '{QUARANTINE_DIR}' folder on the same drive?\n\n"
            "Nothing is deleted: each copy is only renamed into that folder, one copy of every "
            "photo stays where it is, and the catalog is backed up first. The Quarantine page "
            "(sidebar > Keep safe) can put copies back, or empty it once you're happy.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._thread = QThread(self)
        self._worker = QuarantineWorker(Settings(self.conn).get("preferred_roots"))
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        # A bound method, not a lambda: a lambda would run on the worker's thread (CLAUDE.md).
        self._worker.progress.connect(self._quarantine_progress)
        self._worker.done.connect(self._quarantine_done)
        self.quarantine_b.setEnabled(False)
        self._thread.start()

    def _quarantine_progress(self, i: int, t: int) -> None:
        self.summary.setText(f"Moving to quarantine… {i:,} / {t:,} groups")

    def _quarantine_done(self, moved: int, freed: int, errors: list) -> None:
        self._thread.quit()
        self._thread.wait()
        self._thread = None
        msg = f"Moved {moved:,} extra copies ({_gb(freed)}) to quarantine."
        if errors:
            msg += f"\n\n{len(errors):,} groups were skipped:\n" + "\n".join(errors[:8])
        QMessageBox.information(self, "Quarantine", msg)
        self.refresh()
