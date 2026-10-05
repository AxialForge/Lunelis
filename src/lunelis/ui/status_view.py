"""
Library status page (sidebar > Keep safe): what Lunelis is doing to the
library and how the library is.

- **Updating the library:** the library worker's steps (main_window.SCAN_STEPS)
  with a progress bar each, live while a scan runs, and Rescan / Stop.
- **At a glance:** photos and videos, archived, missing, damaged, waiting for
  thumbnails or metadata, duplicates to review, quarantine, backups - each a
  link to the page that deals with it.
- **Lunelis noticed:** shot sequences that could be built into an HDR,
  panorama, timelapse... (noticed.py), each with Build it / Show photos /
  Dismiss. Nothing is built without that click.
- **Sources:** every folder Lunelis catalogs, whether it's reachable right now
  (checked off the GUI thread: an asleep NAS can take seconds to answer),
  its size, missing files and last scan, with a Rescan button each.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QProgressBar, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

LIVE = "f.missing_since IS NULL AND f.excluded = 0 AND f.quarantined_at IS NULL"


def _when(stamp: str | None) -> str:
    if not stamp:
        return "Never"
    try:
        t = datetime.fromisoformat(stamp)
    except ValueError:
        return stamp
    t = t.astimezone() if t.tzinfo else t
    return t.strftime("%b %d, %Y %I:%M %p").replace(" 0", " ")


def _figures(conn) -> tuple[list, list]:
    """The page's figures and its sources' rows (on a worker: see refresh)."""
    q = lambda sql, *a: conn.execute(sql, a).fetchone()[0]  # noqa: E731
    base = "FROM files f JOIN roots r ON r.id = f.root_id WHERE r.enabled = 1"
    total = q(f"SELECT COUNT(*) {base} AND {LIVE}")
    archived = q(f"SELECT COUNT(*) {base} AND {LIVE} AND f.archived_at IS NOT NULL")
    missing = q(f"SELECT COUNT(*) {base} AND f.missing_since IS NOT NULL AND f.quarantined_at IS NULL"
                " AND f.excluded = 0")
    damaged = q("SELECT COUNT(*) FROM damaged d JOIN files f ON f.id = d.file_id"
                f" WHERE d.dismissed = 0 AND {LIVE}")
    thumbs = q(f"SELECT COUNT(*) {base} AND {LIVE} AND f.thumbnail_path IS NULL AND f.thumb_error IS NULL")
    meta = q(f"SELECT COUNT(*) FROM files f JOIN roots r ON r.id = f.root_id LEFT JOIN exif e ON e.file_id = f.id"
             f" WHERE r.enabled = 1 AND {LIVE} AND e.file_id IS NULL")
    dupes = q("SELECT COUNT(*) FROM duplicate_groups WHERE resolved = 0")
    quarantined = q("SELECT COUNT(*) FROM files WHERE quarantined_at IS NOT NULL")
    backup_sets = q("SELECT COUNT(*) FROM backup_sets")
    from lunelis.backups.protection import unprotected_count
    unprotected = unprotected_count(conn)
    last_photo_backup = q("SELECT MAX(last_run_at) FROM backup_sets")
    from lunelis import paths
    from lunelis.catalog import backup
    from lunelis.settings import Settings
    snap = backup.last_snapshot_time(backup.backup_dir(Settings(conn), paths.DATA_DIR))
    cells = [
        ("Photos & videos", f"{total:,}", None),
        ("Archived", f"{archived:,}", "Albums" if archived else None),
        ("Missing (not found at the last scan)", f"{missing:,}", None),
        ("Damaged", f"{damaged:,}", "Damaged files" if damaged else None),
        ("Waiting for thumbnails", f"{thumbs:,}", None),
        ("Waiting for metadata", f"{meta:,}", None),
        ("Duplicate groups to review", f"{dupes:,}", "Duplicates" if dupes else None),
        ("In quarantine", f"{quarantined:,}", "Quarantine" if quarantined else None),
        ("Last catalog backup", snap.strftime("%b %d, %I:%M %p").replace(" 0", " ") if snap else "Never", None),
        ("Photo backups", (f"{backup_sets} · last run {_when(last_photo_backup) if last_photo_backup else 'never'}"
                           if backup_sets else "None set up"), "Backups"),
        ("Not backed up", f"{unprotected:,}", "Backups" if unprotected else None),
    ]
    rows = conn.execute(
        "SELECT r.id, r.path, r.enabled, r.last_scanned_at,"
        " SUM(CASE WHEN f.missing_since IS NULL AND f.excluded = 0 AND f.quarantined_at IS NULL THEN 1 ELSE 0 END),"
        " SUM(CASE WHEN f.missing_since IS NOT NULL AND f.quarantined_at IS NULL THEN 1 ELSE 0 END)"
        " FROM roots r LEFT JOIN files f ON f.root_id = r.id GROUP BY r.id ORDER BY r.path").fetchall()
    return cells, [tuple(r) for r in rows]


class _ReachSignals(QObject):
    done = Signal(int, bool)            # root id, reachable


class _Reach(QRunnable):
    """Is this source there right now? (os.path.isdir can wait on a sleeping NAS.)"""

    def __init__(self, root_id: int, path: str, signals: _ReachSignals) -> None:
        super().__init__()
        self.root_id, self.path, self.signals = root_id, path, signals

    def run(self) -> None:
        try:
            ok = os.path.isdir(self.path)
        except OSError:
            ok = False
        try:
            self.signals.done.emit(self.root_id, ok)
        except RuntimeError:                 # the page closed meanwhile
            pass


NOTICED_SHOWN = 12                       # suggestions listed; the rest are counted
NOTICED_THUMBS = 6
BUILDABLE = {"hdr": "Build the HDR…", "panorama": "Build the panorama…", "timelapse": "Make the timelapse…",
             "focus": "Make the focus stack…", "startrails": "Make the star trails…"}


def _noticed(conn) -> tuple[list, int]:
    """(rows, total) - rows: (id, suggestion, [QImage thumbnails]); on a worker."""
    from PySide6.QtGui import QImage
    from lunelis import noticed, paths
    found = noticed.open_suggestions(conn)
    rows = []
    for sid, sug in found[:NOTICED_SHOWN]:
        ids = sug.file_ids
        pick = [ids[round(i * (len(ids) - 1) / max(1, NOTICED_THUMBS - 1))] for i in range(min(NOTICED_THUMBS, len(ids)))]
        thumbs = []
        for fid in dict.fromkeys(pick):
            row = conn.execute("SELECT thumbnail_path FROM files WHERE id = ?", (fid,)).fetchone()
            img = QImage(str(paths.THUMBNAIL_CACHE / row[0])) if row and row[0] else QImage()
            thumbs.append(img.scaledToHeight(56) if not img.isNull() else img)
        rows.append((sid, sug, thumbs))
    return rows, len(found)


class StatusView(QWidget):
    open_page = Signal(str)              # a link to another page
    show_ids = Signal(list)              # Lunelis noticed: show these photos in the library
    build = Signal(int, str, list)       # (suggestion id, kind, file ids): hand off to the builder
    rescan = Signal(object)              # list of root ids, or None for everything
    stop = Signal()

    def __init__(self, conn: sqlite3.Connection, steps: tuple[str, ...], parent=None) -> None:
        super().__init__(parent)
        self.conn = conn
        self.steps = steps
        self._running = False
        self._reach = _ReachSignals()
        self._reach.done.connect(self._reached)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        head = QWidget(objectName="Toolbar")
        head.setFixedHeight(64)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(24, 0, 24, 0)
        title = QLabel("Library status", objectName="PageTitle")
        hl.addWidget(title)
        hl.addSpacing(12)
        self.state = QLabel(objectName="Count")
        self.state.setMinimumWidth(self.state.fontMetrics().horizontalAdvance(
            max((f"Step 9 of 9 · {s}" for s in steps), key=len)) + 24)
        hl.addWidget(self.state)
        hl.addStretch(1)
        self.rescan_b = QPushButton("Rescan everything (F5)", objectName="Primary",
                                    clicked=lambda: self.rescan.emit(None))
        self.stop_b = QPushButton("Stop", clicked=lambda: self.stop.emit())
        hl.addWidget(self.stop_b)
        hl.addWidget(self.rescan_b)
        outer.addWidget(head)

        body = QWidget()
        v = QVBoxLayout(body)
        v.setContentsMargins(24, 20, 24, 24)
        v.setSpacing(16)
        outer.addWidget(body, 1)

        # Updating the library: one row per step.
        card, cv = self._card("Updating the library",
                              "Every scan works through these steps in order. Files are only read: "
                              "nothing in your photo folders changes.")
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        self.step_rows: list[tuple[QLabel, QLabel, QProgressBar, QLabel]] = []
        for i, name in enumerate(steps):
            mark = QLabel("·", objectName="StepMark")
            mark.setFixedWidth(16)
            label = QLabel(f"{i + 1}. {name}")
            bar = QProgressBar(objectName="ScanProgress", textVisible=False)
            bar.setFixedSize(160, 6)
            bar.hide()
            detail = QLabel(objectName="Help")
            for col, w in enumerate((mark, label, bar, detail)):
                grid.addWidget(w, i, col)
            self.step_rows.append((mark, label, bar, detail))
        grid.setColumnStretch(3, 1)
        cv.addLayout(grid)
        v.addWidget(card)

        # At a glance.
        card, cv = self._card("At a glance")
        self.glance = QGridLayout()
        self.glance.setHorizontalSpacing(24)
        self.glance.setVerticalSpacing(10)
        cv.addLayout(self.glance)
        self._glance_cells: dict[str, QLabel] = {}
        v.addWidget(card)

        # Lunelis noticed.
        card, cv = self._card("Lunelis noticed",
                              "Shots that look like they were taken to be combined - brackets, panoramas, "
                              "focus stacks, timelapses, star trails. Nothing is built unless you say so; "
                              "dismiss one and it isn't offered again.")
        self.noticed_box = QVBoxLayout()
        self.noticed_box.setSpacing(10)
        cv.addLayout(self.noticed_box)
        self.noticed_card = card
        v.addWidget(card)

        # Sources.
        card, cv = self._card("Sources", "The folders Lunelis catalogs. A source that's offline (a sleeping NAS, "
                                         "an unplugged drive) is left alone: nothing in it is marked missing.")
        self.sources = QTableWidget(0, 6)
        self.sources.setHorizontalHeaderLabels(["Folder", "Status", "Photos & videos", "Missing", "Last scanned", ""])
        self.sources.verticalHeader().hide()
        self.sources.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.sources.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        hh = self.sources.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in range(1, 6):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        cv.addWidget(self.sources)
        v.addWidget(card)
        v.addStretch(1)
        self._row_of_root: dict[int, int] = {}
        self.set_running(False)

    @staticmethod
    def _card(title: str, blurb: str | None = None) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame(objectName="Card")
        v = QVBoxLayout(card)
        v.setContentsMargins(20, 16, 20, 18)
        v.setSpacing(10)
        v.addWidget(QLabel(title, objectName="SectionTitle"))
        if blurb:
            b = QLabel(blurb, objectName="Help")
            b.setWordWrap(True)
            v.addWidget(b)
        return card, v

    # --- live progress (from MainWindow's library worker) ------------------------------------

    def set_running(self, running: bool) -> None:
        self._running = running
        self.stop_b.setEnabled(running)
        self.rescan_b.setEnabled(not running)
        if running:
            self.state.setText("Updating the library…")
            for mark, label, bar, detail in self.step_rows:
                mark.setText("·")
                label.setEnabled(False)
                bar.hide()
                detail.setText("")
        else:
            for i, (mark, label, bar, detail) in enumerate(self.step_rows):
                bar.hide()
                if mark.text() == "▸":            # the step that was running when it stopped
                    mark.setText("·")

    def step(self, i: int, done: int, total: int) -> None:
        if not self._running:
            self.set_running(True)
        for j, (mark, label, bar, detail) in enumerate(self.step_rows):
            if j < i and mark.text() != "✓":
                mark.setText("✓")
                label.setEnabled(True)
                bar.hide()
        mark, label, bar, detail = self.step_rows[i]
        mark.setText("▸")
        label.setEnabled(True)
        bar.setRange(0, total)
        if total:
            bar.setValue(done)
        bar.show()
        self.state.setText(f"Step {i + 1} of {len(self.steps)} · {self.steps[i]}")

    def step_text(self, text: str) -> None:
        """The worker's own words for the running step ('Reading metadata… 1,200 / 5,000')."""
        for mark, label, bar, detail in self.step_rows:
            if mark.text() == "▸":
                detail.setText(text)

    def finished(self, cancelled: bool) -> None:
        if not cancelled:
            for mark, label, bar, detail in self.step_rows:
                mark.setText("✓")
                label.setEnabled(True)
        self.set_running(False)
        self.state.setText("Stopped - press Rescan to finish" if cancelled
                           else f"Up to date ({datetime.now().strftime('%I:%M %p').lstrip('0')})")
        self.refresh()

    # --- figures -----------------------------------------------------------------------------

    def refresh(self) -> None:
        # Counting 159k rows several ways takes a while: on a worker.
        if not hasattr(self, "bg"):
            from lunelis.ui.background import Background
            self.bg = Background(self, self.conn)
        self.bg.run("figures", _figures, self._show)
        self.bg.run("noticed", _noticed, self._show_noticed)

    def _show_noticed(self, result) -> None:
        from PySide6.QtGui import QPixmap
        from lunelis import noticed
        rows, total = result
        while self.noticed_box.count():
            item = self.noticed_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self.noticed_rows: list[tuple[int, QPushButton | None, QPushButton, QPushButton]] = []
        if not rows:
            self.noticed_box.addWidget(QLabel("Nothing right now. Lunelis looks after every scan.", objectName="Help"))
            return
        for sid, sug, thumbs in rows:
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(6)
            for img in thumbs:
                lab = QLabel()
                lab.setFixedHeight(56)
                if not img.isNull():
                    lab.setPixmap(QPixmap.fromImage(img))
                h.addWidget(lab)
            text = QLabel(sug.text() + (" Build it?" if sug.kind in BUILDABLE else ""))
            text.setWordWrap(True)
            h.addWidget(text, 1)
            build_b = None
            if sug.kind in BUILDABLE:
                build_b = QPushButton(BUILDABLE[sug.kind], objectName="Primary")
                build_b.clicked.connect(lambda _=False, sid=sid, sug=sug: self.build.emit(sid, sug.kind, sug.file_ids))
                h.addWidget(build_b)
            else:
                text.setToolTip("A builder for this comes in a later version - Show photos to see them together.")
            show_b = QPushButton("Show photos")
            show_b.clicked.connect(lambda _=False, ids=sug.file_ids: self.show_ids.emit(list(ids)))
            dismiss_b = QPushButton("Dismiss")
            dismiss_b.setToolTip("Don't offer these frames again")

            def gone(_=False, sid=sid):
                noticed.dismiss(self.conn, sid)
                self.refresh()
            dismiss_b.clicked.connect(gone)
            h.addWidget(show_b)
            h.addWidget(dismiss_b)
            self.noticed_box.addWidget(row)
            self.noticed_rows.append((sid, build_b, show_b, dismiss_b))
        if total > len(rows):
            self.noticed_box.addWidget(QLabel(f"…and {total - len(rows):,} more", objectName="Help"))

    def _show(self, figures) -> None:
        cells, sources = figures
        while self.glance.count():
            w = self.glance.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        from lunelis.ui import theme
        accent = theme.current().accent
        for n, (name, value, page) in enumerate(cells):
            row, col = divmod(n, 2)
            key = QLabel(name, objectName="Help")
            val = QLabel()
            val.setObjectName("GlanceValue")
            if page:
                val.setText(f'<a href="page:{page}" style="color:{accent}">{value}</a>')
                val.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
                val.linkActivated.connect(lambda href: self.open_page.emit(href.split(":", 1)[1]))
            else:
                val.setText(value)
            self.glance.addWidget(key, row, col * 2)
            self.glance.addWidget(val, row, col * 2 + 1)
        self.glance.setColumnStretch(1, 1)
        self.glance.setColumnStretch(3, 1)
        if not self._running and not self.state.text():
            self.state.setText("Idle")
        self._load_sources(sources)

    def _load_sources(self, rows) -> None:
        self.sources.setRowCount(len(rows))
        self.sources.verticalHeader().setDefaultSectionSize(38)
        self._row_of_root.clear()
        for i, (rid, path, enabled, scanned, n, missing) in enumerate(rows):
            self._row_of_root[rid] = i
            self.sources.setItem(i, 0, QTableWidgetItem(path))
            self.sources.setItem(i, 1, QTableWidgetItem("Off" if not enabled else "Checking…"))
            for c, val in ((2, n or 0), (3, missing or 0)):
                it = QTableWidgetItem(f"{val:,}")
                it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.sources.setItem(i, c, it)
            self.sources.setItem(i, 4, QTableWidgetItem(_when(scanned)))
            b = QPushButton("Rescan", clicked=lambda _=False, rid=rid: self.rescan.emit([rid]))
            b.setEnabled(bool(enabled) and not self._running)
            self.sources.setCellWidget(i, 5, b)
            if enabled:
                QThreadPool.globalInstance().start(_Reach(rid, path, self._reach))
        self.sources.setFixedHeight(self.sources.horizontalHeader().height() + 4
                                    + sum(self.sources.rowHeight(i) for i in range(len(rows))))

    def _reached(self, root_id: int, ok: bool) -> None:
        row = self._row_of_root.get(root_id)
        if row is not None and self.sources.item(row, 1) is not None:
            self.sources.item(row, 1).setText("Online" if ok else "Offline - not reachable now")
