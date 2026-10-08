"""
The Duplicates page. Tab 1, Exact copies: every group of identical copies of
one file, which copy stays, and setting the extras aside. Tab 2,
Near-duplicates: near_view.py.

Laid out for people, not file systems:

- **How it works**, in three numbered steps at the top: found (the
  duplicates job), verified (every byte compared), set aside (moved to
  quarantine on the same drive - restorable).
- **The groups** on the left: a thumbnail, the file's name, how many copies
  and how much space the extras take, and whether it's Verified or Likely.
- **The chosen group** on the right: the photo, the copy that stays and
  where it is, the extra copies (any of them can be the one to keep
  instead), and the one action that applies: Verify, or Set aside.

"Likely" groups come from the sampled pass; "Verified" ones have had every
byte compared. Only verified groups can be set aside (dupes/quarantine.py
enforces that too). Nothing here deletes anything.
"""
from __future__ import annotations

import os
import subprocess
from collections import defaultdict
from dataclasses import dataclass

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QSize, Qt, QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton, QScrollArea, QSplitter,
    QTableView, QTabWidget, QVBoxLayout, QWidget,
)

from lunelis import paths
from lunelis.catalog.schema import open_catalog
from lunelis.dupes import detect
from lunelis.dupes.quarantine import QUARANTINE_DIR, QuarantineRefused, quarantine
from lunelis.raw.thumbnails import cache_rel_path
from lunelis.settings import Settings
from lunelis.ui.background import Background
from lunelis.ui.near_view import NearView
from lunelis.ui.widgets import sharp

THUMB = 44
BIG = 220


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

    @property
    def name(self) -> str:
        keep = next(m for m in self.members if m[0] == self.keeper)
        return keep[3].rsplit("/", 1)[-1]


def load_groups(conn, preferred_roots: list[int]) -> list[Group]:
    """Verified groups, plus likely groups not yet fully verified; biggest
    savings first. One query, grouped in Python (tens of thousands of rows).
    A copy you chose to keep (is_keeper) wins over the keeper rules."""
    rows = conn.execute(
        "SELECT g.id, g.method, g.verified, f.id, f.root_id, r.path, f.rel_path, f.size_bytes,"
        "       f.thumbnail_path, f.content_hash IS NOT NULL, m.is_keeper"
        " FROM duplicate_groups g JOIN duplicate_group_files m ON m.group_id = g.id"
        " JOIN files f ON f.id = m.file_id JOIN roots r ON r.id = f.root_id"
        " WHERE g.method IN ('exact', 'sampled')"
        " AND f.missing_since IS NULL AND f.excluded = 0 AND f.quarantined_at IS NULL").fetchall()
    by: dict[int, list] = defaultdict(list)
    meta: dict[int, tuple] = {}
    chosen: dict[int, int] = {}
    for gid, method, verified, fid, rid, root, rel, size, thumb, hashed, is_keeper in rows:
        by[gid].append((fid, rid, root, rel, thumb, hashed))
        meta[gid] = (method, bool(verified), size)
        if is_keeper:
            chosen[gid] = fid
    rank = detect.keeper_rank(preferred_roots)
    groups = []
    for gid, members in by.items():
        method, verified, size = meta[gid]
        if len(members) < 2:
            continue
        if method == "sampled" and all(m[5] for m in members):
            continue                      # already represented by its verified group
        members.sort(key=rank)
        keeper = chosen.get(gid, members[0][0])
        groups.append(Group(gid, method == "exact", size, [m[:5] for m in members], keeper))
    groups.sort(key=lambda g: (-g.extra_bytes, g.id))
    return groups


def choose_keeper(conn, group_id: int, file_id: int) -> None:
    """Keep this copy of a group instead of the one the rules picked."""
    conn.execute("UPDATE duplicate_group_files SET is_keeper = (file_id = ?) WHERE group_id = ?",
                 (file_id, group_id))
    conn.commit()


def _gb(n: int) -> str:
    return f"{n / 1e9:,.1f} GB" if n >= 1e8 else f"{n / 1e6:,.0f} MB"


def _source(root: str) -> str:
    """A source's short name: the last part of its path (the share or folder name)."""
    return os.path.basename(root.rstrip("\\/")) or root


def _folder(rel: str) -> str:
    return rel.rsplit("/", 1)[0].replace("/", " › ") if "/" in rel else "(top folder)"


class GroupModel(QAbstractTableModel):
    HEADERS = ["Photo", "Copies", "Space the extras take", "Status"]

    def __init__(self) -> None:
        super().__init__()
        self.groups: list[Group] = []
        self._thumbs: dict[int, QPixmap] = {}

    def set_groups(self, groups: list[Group]) -> None:
        self.beginResetModel()
        self.groups = groups
        self._thumbs = {}
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.groups)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return None

    def _thumb(self, g: Group) -> QPixmap:
        pm = self._thumbs.get(g.id)
        if pm is None:
            keep = next(m for m in g.members if m[0] == g.keeper)
            pm = QPixmap(str(paths.THUMBNAIL_CACHE / (keep[4] or cache_rel_path(keep[0]))))
            if not pm.isNull():
                pm = pm.scaled(THUMB, THUMB, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                               Qt.TransformationMode.SmoothTransformation)
            if len(self._thumbs) > 400:
                self._thumbs.clear()
            self._thumbs[g.id] = pm
        return pm

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        g = self.groups[index.row()]
        c = index.column()
        if role == Qt.ItemDataRole.DecorationRole and c == 0:
            return self._thumb(g)
        if role == Qt.ItemDataRole.ToolTipRole and c == 3:
            return ("Every byte of every copy compared: identical" if g.verified else
                    "Same size and matching spot checks - verify to compare every byte")
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if c == 0:
            return g.name
        if c == 1:
            return f"{len(g.members)} copies"
        if c == 2:
            return _gb(g.extra_bytes)
        return "✓ Verified" if g.verified else "Likely"


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


def _card() -> tuple[QFrame, QVBoxLayout]:
    card = QFrame(objectName="Card")
    v = QVBoxLayout(card)
    v.setContentsMargins(14, 12, 14, 12)
    v.setSpacing(6)
    return card, v


class DuplicatesView(QWidget):
    start_verify = Signal()                # ask the window to queue a verify job

    def __init__(self, conn, parent=None) -> None:
        super().__init__(parent)
        self.conn = conn
        top = QVBoxLayout(self)
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(0)
        from lunelis.ui.page_header import page_header
        bar, _row = page_header("Duplicates")
        top.addWidget(bar)
        body = QWidget()
        top.addWidget(body, 1)
        outer = QVBoxLayout(body)
        outer.setContentsMargins(24, 12, 24, 16)
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs)
        exact = QWidget()
        self.tabs.addTab(exact, "Exact copies")
        self.near = NearView(conn)
        self.tabs.addTab(self.near, "Near-duplicates")
        self.tabs.currentChanged.connect(self._tab_changed)
        v = QVBoxLayout(exact)
        v.setContentsMargins(0, 12, 0, 0)
        v.setSpacing(10)

        # How it works, in three steps.
        steps = QHBoxLayout()
        steps.setSpacing(10)
        for n, (title, text) in enumerate((
                ("Found", "The same file in more than one place - same size, matching samples."),
                ("Verified", "Every byte of every copy compared. Only verified copies can be set aside."),
                ("Set aside", f"Extra copies move to a '{QUARANTINE_DIR}' folder on the same drive. "
                              "Nothing is deleted; the Quarantine page puts them back.")), 1):
            card, cv = _card()
            cv.addWidget(QLabel(f"{n}  {title}", objectName="SectionTitle"))
            t = QLabel(text, objectName="Help")
            t.setWordWrap(True)
            cv.addWidget(t)
            steps.addWidget(card, 1)
        v.addLayout(steps)

        top = QHBoxLayout()
        self.summary = QLabel(objectName="SectionTitle")
        self.summary.setWordWrap(True)
        top.addWidget(self.summary, 1)
        self.show_box = QComboBox()
        self.show_box.addItem("All groups", "all")
        self.show_box.addItem("Verified only", "verified")
        self.show_box.addItem("Likely only", "likely")
        self.show_box.currentIndexChanged.connect(self._apply_show)
        top.addWidget(QLabel("Show"))
        top.addWidget(self.show_box)
        self.verify_b = QPushButton("Verify all likely groups…", clicked=lambda: self.start_verify.emit())
        self.verify_b.setToolTip("A background job compares every byte (Jobs, Ctrl+J) - it can pause and run at night")
        self.quarantine_b = QPushButton("Set aside all verified extras…", objectName="Primary",
                                        clicked=self._quarantine_all)
        top.addWidget(self.verify_b)
        top.addWidget(self.quarantine_b)
        v.addLayout(top)
        prefer_row = QHBoxLayout()
        prefer_row.addWidget(QLabel("When copies are identical, keep the one in"))
        self.prefer = QComboBox()
        self.prefer.currentIndexChanged.connect(self._prefer_changed)
        prefer_row.addWidget(self.prefer, 1)
        v.addLayout(prefer_row)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.model = GroupModel()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.setIconSize(QSize(THUMB, THUMB))
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(THUMB + 10)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2, 3):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.table.selectionModel().currentRowChanged.connect(lambda cur, _: self._show(cur.row()))
        split.addWidget(self.table)

        self.detail_scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.Shape.NoFrame)
        self.detail = QWidget()
        self.detail_v = QVBoxLayout(self.detail)
        self.detail_v.setContentsMargins(12, 0, 4, 0)
        self.detail_v.setSpacing(10)
        self.detail_scroll.setWidget(self.detail)
        self.detail_scroll.setMinimumWidth(340)
        split.addWidget(self.detail_scroll)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        v.addWidget(split, 1)
        self._thread = None
        self._all: list[Group] = []
        self.current: Group | None = None

    # --- data ----------------------------------------------------------------

    def refresh(self) -> None:
        s = Settings(self.conn)
        preferred = s.get("preferred_roots")
        self.prefer.blockSignals(True)
        self.prefer.clear()
        self.prefer.addItem("Any source - the copy in the shallowest folder, not a Google Takeout export", None)
        for rid, path in self.conn.execute("SELECT id, path FROM roots WHERE enabled = 1 ORDER BY id"):
            self.prefer.addItem(f"{_source(path)}   ({path})", rid)
            if preferred and preferred[0] == rid:
                self.prefer.setCurrentIndex(self.prefer.count() - 1)
        self.prefer.blockSignals(False)
        if self.tabs.currentIndex() == 1:
            self.near.refresh()
        # Thousands of groups with their copies: loaded on a worker.
        if not hasattr(self, "bg"):
            self.bg = Background(self, self.conn)
        if not self._all:
            self.summary.setText("Loading duplicate groups…")
        self.bg.run("groups", lambda c: load_groups(c, preferred), self._show_groups,
                    error=lambda e: self.summary.setText(f"Couldn't load the duplicates: {e}"))

    def _show_groups(self, groups) -> None:
        self._all = groups
        verified = [g for g in groups if g.verified]
        likely = len(groups) - len(verified)
        if not groups:
            self.summary.setText("No duplicates found. Library > Find duplicates… looks for copies of the same "
                                 "file across your sources.")
        else:
            self.summary.setText(
                f"{len(groups):,} files have copies · {_gb(sum(g.extra_bytes for g in groups))} could be freed  "
                f"({len(verified):,} verified, {_gb(sum(g.extra_bytes for g in verified))} ready to set aside · "
                f"{likely:,} likely, still to verify)")
        self.verify_b.setEnabled(likely > 0)
        self.quarantine_b.setEnabled(bool(verified) and self._thread is None)
        self._apply_show()

    def _apply_show(self) -> None:
        mode = self.show_box.currentData()
        shown = [g for g in self._all if mode == "all" or (g.verified == (mode == "verified"))]
        keep = self.current.id if self.current else None
        self.model.set_groups(shown)
        row = next((i for i, g in enumerate(shown) if g.id == keep), 0 if shown else -1)
        if row >= 0:
            self.table.selectRow(row)
        else:
            self._show(-1)

    def _tab_changed(self, index: int) -> None:
        if index == 1:
            self.near.refresh()

    def _prefer_changed(self) -> None:
        rid = self.prefer.currentData()
        Settings(self.conn).set("preferred_roots", [rid] if rid else [])
        self.refresh()

    # --- the chosen group ---------------------------------------------------------------

    def _clear_detail(self) -> None:
        def clear(layout) -> None:
            while layout.count():
                item = layout.takeAt(0)
                if item.widget() is not None:
                    item.widget().hide()               # gone at once, not at the next event loop turn
                    item.widget().deleteLater()
                elif item.layout() is not None:        # a row of buttons: its buttons too
                    clear(item.layout())
                    item.layout().deleteLater()
        clear(self.detail_v)

    def _show(self, row: int) -> None:
        self._clear_detail()
        if row < 0 or row >= len(self.model.groups):
            self.current = None
            self.detail_v.addWidget(QLabel("Choose a group on the left.", objectName="Help"))
            self.detail_v.addStretch(1)
            return
        g = self.current = self.model.groups[row]
        keep = next(m for m in g.members if m[0] == g.keeper)
        pic = QLabel()
        pm = QPixmap(str(paths.THUMBNAIL_CACHE / (keep[4] or cache_rel_path(keep[0]))))
        if not pm.isNull():
            pic.setPixmap(sharp(pm, BIG, pic))
        self.detail_v.addWidget(pic)
        name = QLabel(g.name, objectName="SectionTitle")
        name.setWordWrap(True)
        self.detail_v.addWidget(name)
        status = QLabel(
            f"✓ Verified - every byte of all {len(g.members)} copies is the same. The extra copies take "
            f"{_gb(g.extra_bytes)}." if g.verified else
            f"Likely the same - {len(g.members)} copies of the same size whose spot checks match. Verify to "
            "compare every byte before anything is set aside.", objectName="Help")
        status.setWordWrap(True)
        self.detail_v.addWidget(status)

        card, cv = _card()
        cv.addWidget(QLabel("Stays where it is", objectName="SubTitle"))
        cv.addWidget(self._copy_row(g, keep, kept=True))
        self.detail_v.addWidget(card)

        card, cv = _card()
        cv.addWidget(QLabel("Extra copies" + (" - set aside together" if g.verified else ""), objectName="SubTitle"))
        for m in g.members:
            if m[0] != g.keeper:
                cv.addWidget(self._copy_row(g, m, kept=False))
        self.detail_v.addWidget(card)

        act = QHBoxLayout()
        if g.verified:
            b = QPushButton(f"Set aside {len(g.members) - 1} extra cop{'y' if len(g.members) == 2 else 'ies'}",
                            objectName="Primary", clicked=lambda: self._quarantine_one(g))
            b.setToolTip(f"Moved into '{QUARANTINE_DIR}' on the same drive - the Quarantine page puts them back")
        else:
            b = QPushButton("Verify this group", objectName="Primary", clicked=lambda: self._verify_one(g))
            b.setToolTip("Compares every byte of these copies now")
        act.addWidget(b)
        act.addStretch(1)
        self.detail_v.addLayout(act)
        self.detail_v.addStretch(1)

    def _copy_row(self, g: Group, m: tuple, kept: bool) -> QWidget:
        fid, rid, root, rel, _thumb = m
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 2, 0, 2)
        text = QLabel(f"<b>{_esc(_source(root))}</b><br>{_esc(_folder(rel))}")
        text.setTextFormat(Qt.TextFormat.RichText)
        text.setWordWrap(True)
        text.setToolTip(os.path.join(root, *rel.split("/")))
        h.addWidget(text, 1)
        full = os.path.join(root, *rel.split("/"))
        h.addWidget(QPushButton("Show", clicked=lambda _=False, p=full: subprocess.Popen(["explorer", "/select,", p])))
        if not kept:
            h.addWidget(QPushButton("Keep this one", clicked=lambda _=False: self._keep(g, fid)))
        return w

    def _keep(self, g: Group, fid: int) -> None:
        choose_keeper(self.conn, g.id, fid)
        g.keeper = fid
        self._show(self.model.groups.index(g) if g in self.model.groups else -1)

    def _verify_one(self, g: Group) -> None:
        self.summary.setText(f"Verifying {g.name}…")
        self.bg.run("verify-one", lambda c: detect.verify_group(c, g.id), lambda _ids: self.refresh(),
                    error=lambda e: QMessageBox.warning(self, "Verify", f"Couldn't verify these copies: {e}"))

    def _quarantine_one(self, g: Group) -> None:
        others = [m[0] for m in g.members if m[0] != g.keeper]
        if QMessageBox.question(
                self, "Set aside", f"Set aside {len(others)} extra cop{'y' if len(others) == 1 else 'ies'} of "
                f"{g.name} ({_gb(g.extra_bytes)})? They move into '{QUARANTINE_DIR}' on the same drive; "
                "the Quarantine page can put them back.") != QMessageBox.StandardButton.Yes:
            return
        try:
            quarantine(self.conn, g.id, others, backup_dir=paths.BACKUP_DIR)
        except (QuarantineRefused, OSError) as e:
            QMessageBox.warning(self, "Set aside", f"Nothing was moved: {e}")
            return
        self.current = None
        self.refresh()

    # --- set aside everything verified ---------------------------------------------------

    def _quarantine_all(self) -> None:
        verified = [g for g in self._all if g.verified]
        n = sum(len(g.members) - 1 for g in verified)
        answer = QMessageBox.question(
            self, "Set aside all verified extras",
            f"Set aside {n:,} extra copies ({_gb(sum(g.extra_bytes for g in verified))}) from "
            f"{len(verified):,} verified groups? Each moves into a '{QUARANTINE_DIR}' folder on the same drive.\n\n"
            "Nothing is deleted: one copy of every file stays where it is, and the catalog is backed up "
            "first. The Quarantine page (sidebar > Keep safe) puts copies back, or empties it once you're happy.")
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
        self.summary.setText(f"Setting aside… {i:,} / {t:,} groups")

    def _quarantine_done(self, moved: int, freed: int, errors: list) -> None:
        self._thread.quit()
        self._thread.wait()
        self._thread = None
        msg = f"Set aside {moved:,} extra copies ({_gb(freed)})."
        if errors:
            msg += f"\n\n{len(errors):,} groups were skipped:\n" + "\n".join(errors[:8])
        QMessageBox.information(self, "Set aside", msg)
        self.refresh()


def _esc(s: str) -> str:
    from html import escape
    return escape(s)
