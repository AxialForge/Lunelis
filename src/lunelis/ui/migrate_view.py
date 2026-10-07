"""
The Migrate page: choose sources, a target and a folder template, preview
exactly what would happen (a dry run - nothing moves), then start it as a
background job. Review mode keeps every original until "Release originals".
"""
from __future__ import annotations

import os
from datetime import datetime

from lunelis.ui.widgets import plain, row_toggles
from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QRadioButton, QScrollArea,
    QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from lunelis import paths
from lunelis.catalog import backup
from lunelis.catalog.schema import open_catalog
from lunelis.importing.templates import PRESETS, Context, TemplateError, render
from lunelis.migrate import execute
from lunelis.migrate.plan import Options, PlanError, discard, plan, summary
from lunelis.settings import Settings
from lunelis.ui.background import Background, unless_closed

STATE_TEXT = {"planned": "Waiting", "copied": "Copied, original pending", "done": "Done",
              "kept": "Done - original kept for review", "released": "Done - original released",
              "skipped": "Skipped", "failed": "Failed"}


def _gb(n: int) -> str:
    return f"{n / 1e12:,.2f} TB" if n >= 1e12 else f"{n / 1e9:,.1f} GB" if n >= 1e8 else f"{n / 1e6:,.0f} MB"


class Worker(QObject):
    """Runs one call with its own catalog connection (plan / start / release)."""

    done = Signal(object)

    def __init__(self, fn) -> None:
        super().__init__()
        self.fn = fn

    def run(self) -> None:
        conn = open_catalog(paths.DEFAULT_CATALOG_PATH)
        try:
            self.done.emit(self.fn(conn))
        except Exception as e:           # shown to the user, never fatal
            self.done.emit(e)
        finally:
            conn.close()


def _load_page(conn):
    """Sources with their counts, duplicate counts, and the migration to show (on a worker)."""
    sources = [tuple(r) for r in conn.execute(
        "SELECT r.id, r.path, COUNT(f.id), COALESCE(SUM(f.size_bytes), 0) FROM roots r"
        " LEFT JOIN files f ON f.root_id = r.id AND f.missing_since IS NULL AND f.excluded = 0"
        " AND f.quarantined_at IS NULL WHERE r.enabled = 1 GROUP BY r.id ORDER BY r.id")]
    dups = tuple(conn.execute(
        "SELECT COALESCE(SUM(method = 'exact' AND verified = 1), 0), COALESCE(SUM(method = 'sampled'), 0)"
        " FROM duplicate_groups").fetchone())
    # The latest migration that isn't finished with (running, done-with-kept-originals, or a plan).
    row = conn.execute(
        "SELECT id FROM migrations WHERE state IN ('planned', 'running', 'done') ORDER BY id DESC LIMIT 1"
    ).fetchone()
    mid = row[0] if row else None
    return sources, dups, mid, (_load_plan(conn, mid) if mid is not None else None)


def _load_plan(conn, mid: int):
    """A plan's summary (incl. the target's free space) and its notes (on a worker)."""
    s = summary(conn, mid)
    rows = [tuple(r) for r in conn.execute(
        "SELECT r.path, m.src_rel, m.action, m.note, m.state, m.error FROM migration_items m"
        " JOIN roots r ON r.id = m.src_root WHERE m.migration_id = ?"
        " AND (m.note IS NOT NULL OR m.state = 'failed') ORDER BY m.state = 'failed' DESC, m.id LIMIT 2000",
        (mid,))]
    return s, rows


def _item(text: str, right: bool = False) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
    if right:
        it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return it


class MigrateView(QWidget):
    job_started = Signal(int)            # the runner should pick it up
    library_changed = Signal()

    def __init__(self, conn, parent=None) -> None:
        super().__init__(parent)
        self.conn = conn
        self.migration_id: int | None = None
        self._thread: QThread | None = None
        self._summary = None                       # the plan on screen (Start / Release use it)
        self.bg = Background(self, conn)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = QWidget(objectName="Toolbar")
        head.setFixedHeight(64)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(24, 0, 24, 0)
        title = QLabel("Migrate & consolidate")
        title.setObjectName("PageTitle")
        hl.addWidget(title)
        hl.addStretch(1)
        self.headline = QLabel(objectName="Count")
        hl.addWidget(self.headline)
        outer.addWidget(head)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.addWidget(self._form())
        split.addWidget(self._results())
        split.setSizes([560, 820])
        outer.addWidget(split, 1)
        self.refresh()

    # --- the form -------------------------------------------------------------------------

    def _form(self) -> QWidget:
        scroll = QScrollArea(objectName="SettingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page = QWidget(objectName="SettingsPage")
        v = QVBoxLayout(page)
        v.setContentsMargins(24, 20, 16, 20)
        v.setSpacing(10)

        v.addWidget(QLabel("1. What to move", objectName="SectionTitle"))
        self.sources = QListWidget()
        row_toggles(self.sources)
        self.sources.setMaximumHeight(190)
        self.sources.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.sources.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        v.addWidget(self.sources)

        v.addWidget(QLabel("2. Where to", objectName="SectionTitle"))
        row = QHBoxLayout()
        self.target = QLineEdit(readOnly=True, placeholderText="Choose an empty drive or folder")
        self.target.setObjectName("PathField")
        row.addWidget(self.target, 1)
        row.addWidget(QPushButton("Choose…", clicked=self._choose_target))
        v.addLayout(row)
        h = QLabel("Outside your current sources - an empty drive or network share is ideal. It becomes a "
                   "source itself when the migration starts.", objectName="Help")
        h.setWordWrap(True)
        v.addWidget(h)

        v.addWidget(QLabel("3. Folders", objectName="SectionTitle"))
        self.template = QComboBox()
        self.template.setEditable(True)
        for label, t in PRESETS.items():
            if "import" in t and "event" not in t and "{YYYY}" not in t:
                continue                      # import-date presets make no sense for a migration
            self.template.addItem(t)
            self.template.setItemData(self.template.count() - 1, label, Qt.ItemDataRole.ToolTipRole)
        self.template.setCurrentText(Settings(self.conn).get("import_template"))
        self.template.currentTextChanged.connect(self._example)
        v.addWidget(self.template)
        self.example = QLabel(objectName="Example")
        self.example.setWordWrap(True)
        v.addWidget(self.example)
        h = QLabel("File names are never changed. Photos in an event are filed by the event's start date, "
                   "and {import_name} is the event's name here. A different photo whose name is taken goes to "
                   "a sibling folder, e.g. \"6-19-2026 (2)\".",
                   objectName="Help")
        h.setWordWrap(True)
        v.addWidget(h)

        self.library_layout = QCheckBox("New library layout: Library\\Photos and Videos\\Year\\Day\\"
                                        "Photos | Videos | Timelapse, and Library\\Undated")
        self.library_layout.setToolTip("Each day gets Photos, Videos and (for a timelapse) Timelapse folders; "
                                       "a day with several events takes its first event's name.")
        self.library_layout.setChecked(True)
        self.library_layout.toggled.connect(self._layout_toggled)
        v.addWidget(self.library_layout)
        self.layout_box = QWidget()
        lb = QVBoxLayout(self.layout_box)
        lb.setContentsMargins(24, 0, 0, 0)
        self.undated_mtime = QCheckBox("File a photo with no date by its modified date, when that date is believable")
        self.undated_mtime.setToolTip("Not in the future, not before 1995, and not the day a whole folder was "
                                      "copied. Otherwise it goes to Library\\Undated.")
        lb.addWidget(self.undated_mtime)
        sub = QHBoxLayout()
        sub.addWidget(QLabel("Inside Photos:"))
        self.photo_sub = QComboBox()
        for label, key in (("One folder for the day", "none"), ("A folder per camera", "camera"),
                           ("The folder each photo came from", "original")):
            self.photo_sub.addItem(label, key)
        sub.addWidget(self.photo_sub)
        sub.addStretch(1)
        lb.addLayout(sub)
        v.addWidget(self.layout_box)

        v.addWidget(QLabel("4. How", objectName="SectionTitle"))
        self.one_copy = QCheckBox("Move one copy of each verified duplicate")
        self.one_copy.setChecked(True)
        v.addWidget(self.one_copy)
        self.dup_help = QLabel(objectName="Help")
        self.dup_help.setWordWrap(True)
        self.dup_help.setContentsMargins(24, 0, 0, 0)
        v.addWidget(self.dup_help)
        self.skip_damaged = QCheckBox("Leave damaged files behind when an intact copy exists")
        self.skip_damaged.setChecked(True)
        v.addWidget(self.skip_damaged)
        self.videos = QCheckBox("Include videos")
        self.videos.setChecked(True)
        v.addWidget(self.videos)
        self.archived_only = QCheckBox("Only photos in the Archive - move the Archive out to this drive")
        self.archived_only.setToolTip("Archived photos move to the target; they stay archived and in "
                                      "Albums > Archive, and everything else stays where it is.")
        v.addWidget(self.archived_only)
        self.mode = QButtonGroup(self)
        keep = QRadioButton("Keep the originals until I've reviewed the new library")
        keep.setChecked(True)
        move = QRadioButton("Move each original to quarantine as soon as its copy is verified")
        self.mode.addButton(keep, 0)
        self.mode.addButton(move, 1)
        v.addWidget(keep)
        v.addWidget(move)
        h = QLabel("Either way nothing is deleted: originals go to a \"_Lunelis Quarantine\" folder on their own "
                   "drive, and the catalog is backed up before anything moves.", objectName="Help")
        h.setWordWrap(True)
        v.addWidget(h)
        self.preview_b = QPushButton("Preview - nothing is moved", clicked=self._preview)
        self.preview_b.setObjectName("Primary")
        v.addWidget(self.preview_b)
        v.addStretch(1)
        scroll.setWidget(page)
        self._layout_toggled(self.library_layout.isChecked())
        return scroll

    # --- the results --------------------------------------------------------------------------

    def _results(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(16, 20, 24, 20)
        self.message = QLabel("Choose what to move and where, then Preview. The preview shows exactly where every "
                              "photo would go - nothing is copied or moved until you start.")
        self.message.setWordWrap(True)
        self.message.setStyleSheet("font-size: 14px;")
        v.addWidget(self.message)
        v.addWidget(QLabel("NEW FOLDERS", objectName="FilterLabel"))
        self.folders = QTableWidget(0, 3)
        self.folders.setHorizontalHeaderLabels(["Top folder", "Files", "Size"])
        self._setup(self.folders)
        v.addWidget(self.folders, 1)
        v.addWidget(QLabel("WORTH A LOOK", objectName="FilterLabel"))
        self.notes = QTableWidget(0, 3)
        self.notes.setHorizontalHeaderLabels(["File", "What happens", "Status"])
        self._setup(self.notes)
        self.notes.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.notes.setColumnWidth(0, 420)
        self.notes.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        v.addWidget(self.notes, 2)
        row = QHBoxLayout()
        self.discard_b = QPushButton("Discard this plan", clicked=self._discard)
        row.addWidget(self.discard_b)
        row.addStretch(1)
        self.report_b = QPushButton("Accounted-for report", clicked=self._report)
        self.report_b.setToolTip("Every source file and what became of it - must be clean before release")
        row.addWidget(self.report_b)
        self.logs_b = QPushButton("Open the logs", clicked=self._open_logs)
        self.logs_b.setToolTip("Before / after inventories, the per-file manifest and the report (CSV)")
        row.addWidget(self.logs_b)
        self.release_b = QPushButton("Release originals…", clicked=self._release)
        self.release_b.setToolTip("Move the originals kept for review aside (the Lunelis folder's Trash, "
                                  "else quarantine) - only once every source file is accounted for")
        row.addWidget(self.release_b)
        self.start_b = QPushButton("Start migration…", clicked=self._start)
        self.start_b.setObjectName("Primary")
        row.addWidget(self.start_b)
        v.addLayout(row)
        return w

    @staticmethod
    def _setup(t: QTableWidget) -> None:
        t.verticalHeader().hide()
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setWordWrap(False)
        t.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        hh = t.horizontalHeader()
        for c in range(t.columnCount()):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)

    # --- loading --------------------------------------------------------------------------------

    def prefill_archive(self) -> None:
        """Library > Move the Archive to a drive: every source, archived photos only."""
        self._tick_all = True                         # also sources the refresh below adds
        self.refresh()
        for i in range(self.sources.count()):
            self.sources.item(i).setCheckState(Qt.CheckState.Checked)
        self.archived_only.setChecked(True)
        self.message.setText("Choose the archive drive or folder as the target, then Preview. Archived photos "
                             "are copied, checked, and only then set aside from their old place.")

    def refresh(self) -> None:
        # Counting every source's files and the duplicate groups takes seconds on
        # a big library, and the plan's summary checks the target's free space
        # (a sleeping NAS): all on a worker.
        self.bg.run("page", _load_page, self._fill_page,
                    error=lambda e: self.message.setText(f"Couldn't read the migration: {e}"))
        self._buttons()

    def _fill_page(self, data) -> None:
        sources, (exact, likely), mid, plan_data = data
        tick_all, self._tick_all = getattr(self, "_tick_all", False), False
        checked = {self.sources.item(i).data(Qt.ItemDataRole.UserRole)
                   for i in range(self.sources.count())
                   if self.sources.item(i).checkState() == Qt.CheckState.Checked}
        self.sources.clear()
        for rid, path, n, size in sources:
            it = QListWidgetItem(f"{path}   ({n:,} files, {_gb(size)})")
            it.setData(Qt.ItemDataRole.UserRole, rid)
            it.setCheckState(Qt.CheckState.Checked if rid in checked or tick_all
                             else Qt.CheckState.Unchecked)
            self.sources.addItem(it)
        self.dup_help.setText(
            f"{exact:,} verified groups. {likely:,} likely groups aren't verified yet - verify them in "
            "Duplicates first, or their copies move too (an identical file that lands in the same place is "
            "still recognised and not copied twice).")
        self.migration_id = mid
        self._render(plan_data)

    def _layout_toggled(self, on: bool) -> None:
        self.layout_box.setEnabled(on)
        self.template.setEnabled(not on)               # the layout names the day folders itself
        self._example(self.template.currentText())

    def _example(self, text: str) -> None:
        box = getattr(self, "library_layout", None)
        if box is not None and box.isChecked():
            from lunelis.migrate import layout
            o = layout.LayoutOptions()
            t = datetime(2026, 6, 19, 14, 3)
            self.example.setObjectName("Example")
            self.example.setText(
                "e.g. " + layout.place(o, taken=t, kind="Photos", event="Air Show") + "\\DSC01234.ARW   ·   "
                + layout.place(o, taken=t, kind="Videos") + "\\C0001.MP4   ·   "
                + layout.place(o, taken=t, kind="Photos", timelapse=(t, 786)) + "\\DSC05000.ARW")
            self.example.style().unpolish(self.example)
            self.example.style().polish(self.example)
            return
        try:
            folder = render(text, Context(datetime(2026, 6, 19, 14, 3), camera="ILCE-7RM5", import_name="Air Show",
                                          event="Air Show", event_start=datetime(2026, 6, 19, 9)))
            plain = render(text, Context(datetime(2026, 6, 19, 14, 3), camera="ILCE-7RM5"))
            self.example.setObjectName("Example")
            self.example.setText(f"e.g. {plain}\\DSC01234.ARW   ·   in an event: {folder}\\DSC01234.ARW")
        except TemplateError as e:
            self.example.setObjectName("Error")
            self.example.setText(f"Can't use this template: {e}")
        self.example.style().unpolish(self.example)
        self.example.style().polish(self.example)

    def _show(self) -> None:
        """Redraw the plan: the buttons now, the figures once the worker has them."""
        mid = self.migration_id
        if mid is None:
            self._render(None)
            return
        self.bg.run("plan", lambda c: _load_plan(c, mid), self._render,
                    error=lambda e: self.message.setText(f"Couldn't read the plan: {e}"))
        self._buttons()

    def _buttons(self) -> None:
        busy = self._thread is not None
        loading = self.bg.busy()
        s = self._summary
        self.preview_b.setEnabled(not busy)
        planned = s is not None and s.state == "planned"
        self.start_b.setVisible(planned)
        # Start and Release act on the figures on screen: not while newer ones load.
        self.start_b.setEnabled(planned and s.enough_space and s.move_files > 0 and not busy and not loading)
        self.discard_b.setVisible(planned)
        self.release_b.setVisible(s is not None and s.state == "done" and bool(s.states.get("kept")))
        ran = s is not None and s.state in ("running", "done", "released")
        self.report_b.setVisible(ran)
        self.logs_b.setVisible(ran)
        self.report_b.setEnabled(not busy)
        # Nothing that changes the plan while a worker uses it.
        self.discard_b.setEnabled(not busy)
        self.release_b.setEnabled(not busy and not loading)

    def _render(self, data) -> None:
        self.folders.setRowCount(0)
        self.notes.setRowCount(0)
        if data is None or data[0].migration_id != self.migration_id:
            self._summary = None
            self.headline.setText("")
            self._buttons()
            return
        s, rows = data
        self._summary = s
        opts = s.options
        keep = opts.get("keep_sources", True)
        lines = [f"<b>{s.move_files:,} files ({_gb(s.move_bytes)})</b> into <b>{s.target}</b>"]
        if s.dup_files:
            lines.append(f"{s.dup_files:,} duplicate copies ({_gb(s.dup_bytes)}) not moved - one copy of each goes")
        if s.damaged_skipped:
            lines.append(f"{s.damaged_skipped:,} damaged files left behind - an intact copy goes instead")
        if s.takeout_skipped:
            lines.append(f"{s.takeout_skipped:,} Google Takeout items left in place - unticked on the Google "
                         "Takeout page (or already in your library)")
        if s.damaged_only_copy:
            lines.append(f"{s.damaged_only_copy:,} damaged files with no intact copy - moved as they are")
        if s.probable_copies:
            lines.append(f"{s.probable_copies:,} files ({_gb(s.probable_bytes)}) look like copies of others - "
                         "compared when copying and not copied twice")
        if s.sibling_folders:
            lines.append(f"{s.sibling_folders:,} files go to a sibling folder \"(2)\" - their name was taken")
        if s.undated:
            lines.append(f"{s.undated:,} files have no date and go to Undated")
        free = f"{_gb(s.free_bytes)} free on the target" if s.free_bytes is not None else "free space unknown"
        lines.append(("✓ " if s.enough_space else "✗ Not enough space: ") + free)
        lines.append("Originals: " + ("kept until you release them" if keep else "moved to quarantine as each "
                                      "copy is verified"))
        if s.state == "planned":
            head = "Preview - nothing has moved yet."
        elif s.state == "running":
            done = sum(n for st, n in s.states.items() if st in ("done", "kept", "released", "skipped"))
            head = f"Migrating… {done:,} of {sum(s.states.values()):,} handled (progress in Jobs, Ctrl+J)."
        else:
            head = "Finished." + (" The originals are still in place - release them when you're happy with "
                                  "the new library." if keep and s.states.get("kept") else "")
        fails = s.states.get("failed", 0)
        if fails:
            head += f" {fails:,} failed - see below."
        self.message.setText(f"{head}<br><br>" + "<br>".join(lines))
        self.headline.setText({"planned": "Plan ready", "running": "Running", "done": "Finished"}.get(s.state, ""))
        self.folders.setRowCount(len(s.folders))
        for i, (top, n, size) in enumerate(s.folders):
            self.folders.setItem(i, 0, _item(top or "(top level)"))
            self.folders.setItem(i, 1, _item(f"{n:,}", True))
            self.folders.setItem(i, 2, _item(_gb(size), True))
        self.notes.setRowCount(len(rows))
        for i, (root, rel, act, note, st, err) in enumerate(rows):
            self.notes.setItem(i, 0, _item(os.path.join(root, *rel.split("/"))))
            self.notes.setItem(i, 1, _item(err or note or ""))
            self.notes.setItem(i, 2, _item(STATE_TEXT.get(st, st)))
        self._buttons()

    # --- actions -------------------------------------------------------------------------------------

    def _choose_target(self) -> None:
        picked = QFileDialog.getExistingDirectory(self, "Migrate into which folder or drive?")
        if picked:
            self.target.setText(os.path.normpath(picked))

    def _run(self, fn, then) -> None:
        if self._thread is not None:
            return                                   # one at a time: a second would orphan the first
        self._thread = QThread(self)
        self._worker = Worker(fn)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        # A bound method (not a lambda) so the result is handled on the GUI thread.
        self._then = then
        self._worker.done.connect(self._finished)
        self._thread.start()
        self._show()

    @unless_closed
    def _finished(self, result) -> None:
        then = self._then
        self._thread.quit()
        self._thread.wait()
        self._thread = None
        if isinstance(result, Exception):
            QMessageBox.warning(self, "Migration", plain(result))
            self._show()
            return
        then(result)

    def _preview(self) -> None:
        sources = [self.sources.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.sources.count())
                   if self.sources.item(i).checkState() == Qt.CheckState.Checked]
        target, template = self.target.text(), self.template.currentText()
        if not sources or not target:
            QMessageBox.information(self, "Migration", "Tick at least one source and choose a target first.")
            return
        opts = Options(sources, keep_sources=self.mode.checkedId() == 0, one_copy=self.one_copy.isChecked(),
                       skip_damaged_copies=self.skip_damaged.isChecked(), include_videos=self.videos.isChecked(),
                       only_archived=self.archived_only.isChecked(),
                       library_layout=self.library_layout.isChecked(),
                       undated_by_mtime=self.undated_mtime.isChecked(),
                       photo_subfolders=self.photo_sub.currentData())
        preferred = Settings(self.conn).get("preferred_roots")
        if self.migration_id is not None and self._summary is not None and self._summary.state == "planned":
            discard(self.conn, self.migration_id)          # a new preview replaces the old one
        self.migration_id = None
        self.message.setText("Working out where everything goes…")
        self._run(lambda c: plan(c, target, template, opts, preferred), self._planned)

    def _planned(self, mid: int) -> None:
        self.migration_id = mid
        self._show()

    def _discard(self) -> None:
        if self.migration_id is not None:
            if QMessageBox.question(self, "Discard this plan?",
                                    "Throw this plan away? Nothing on disk has moved; you can make a new "
                                    "preview at any time.") != QMessageBox.StandardButton.Yes:
                return
            discard(self.conn, self.migration_id)
        self.refresh()

    def _start(self) -> None:
        s = self._summary                              # what's on screen (Start waits for it)
        if s is None or s.migration_id != self.migration_id:
            return
        keep = s.options.get("keep_sources", True)
        if QMessageBox.question(
                self, "Start the migration?",
                f"Copy {s.move_files:,} files ({_gb(s.move_bytes)}) into {s.target}?\n\n"
                "Each copy is verified against its original before anything else happens. "
                + ("The originals stay where they are until you release them."
                   if keep else "Each original then goes to quarantine on its own drive.")
                + "\n\nThe catalog is backed up first. It runs in the background and can be paused.",
                ) != QMessageBox.StandardButton.Yes:
            return
        mid = self.migration_id
        folder = backup.backup_dir(Settings(self.conn), paths.DATA_DIR)
        s_ = Settings(self.conn)
        schedule = {"mode": s_.get("job_default_when"), "idle_minutes": s_.get("job_idle_minutes"),
                    "start_hour": s_.get("job_window_start_hour"), "end_hour": s_.get("job_window_end_hour")}
        mb_per_s = s_.get("job_mb_per_s") or None        # read here: the worker has its own connection
        self.message.setText("Backing up the catalog and starting…")
        self._run(lambda c: execute.start(c, mid, backup_dir=folder, schedule=schedule,
                                          mb_per_s=mb_per_s), self._started)

    def _started(self, job_id: int) -> None:
        self.job_started.emit(job_id)
        self.library_changed.emit()
        self.refresh()

    def _report(self) -> None:
        mid = self.migration_id
        if mid is None:
            return
        from lunelis.migrate import logs
        self._run(lambda c: logs.accounted(c, mid), self._show_report)

    def _show_report(self, rep) -> None:
        from lunelis.migrate import logs
        text = rep.text()
        box = QMessageBox(QMessageBox.Icon.Information if rep.clean else QMessageBox.Icon.Warning,
                          "Accounted-for report", text.split("\n")[0], parent=self)
        box.setDetailedText(text)
        where = logs.logs_dir(self.conn, rep.migration_id) / "report.csv"
        box.setInformativeText("Every source file is accounted for - the originals can be released."
                               if rep.clean else
                               f"{len(rep.unaccounted):,} file(s) aren't accounted for: release stays locked. "
                               f"The full list: {where}")
        box.exec()

    def _open_logs(self) -> None:
        if self.migration_id is None:
            return
        from lunelis.migrate import logs
        d = logs.logs_dir(self.conn, self.migration_id)
        d.mkdir(parents=True, exist_ok=True)
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(d)))

    def _release(self) -> None:
        s = self._summary
        if s is None or s.migration_id != self.migration_id:
            return
        n = s.states.get("kept", 0)
        if QMessageBox.question(
                self, "Release the originals?",
                f"Move the {n:,} originals kept for review into quarantine (\"_Lunelis Quarantine\" on their own "
                "drive, or the Lunelis folder's Trash when one is set)?\n\nThe accounted-for report checks every source file first; if any isn't accounted for, nothing is released. Nothing is deleted. The Quarantine page (sidebar > Keep safe) can put files back, or empty it once you're happy.",
                ) != QMessageBox.StandardButton.Yes:
            return
        mid = self.migration_id
        folder = backup.backup_dir(Settings(self.conn), paths.DATA_DIR)
        self._run(lambda c: execute.release(c, mid, backup_dir=folder), lambda n: self.refresh())
