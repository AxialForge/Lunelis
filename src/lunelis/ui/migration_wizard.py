"""
The migration wizard (0.43): a window that takes every choice of a migration
in order. Nothing on disk changes before step 8.

 1. Sources      the folders to bring together; a Google Takeout one is sent
                 to the Google Takeout page to tick what comes
 2. Target       the new dataset; Library\\ and Lunelis\\ go inside it, and the
                 Lunelis folder is set to <target>\\Lunelis
 3. Layout       Photos / Videos / Timelapse per day, Undated, and a preview
                 of where some of your real photos would go
 4. Duplicates   one copy of each verified identical group, which copy is
                 kept, and how many are still unverified
 5. Leftovers    the files in the sources that aren't photos or videos, by
                 type - they stay where they are
 6. Safety       keep the originals until you release them; how long the
                 Trash keeps things
 7. Dry run      the plan: counts, space, time, problems - nothing moves
 8. Run          the copy, verified file by file, as a background job that
                 pauses, resumes and waits for a sleeping NAS
 9. Release      the accounted-for report; only when it's clean are the
                 originals moved aside
"""
from __future__ import annotations

import os
from collections import Counter

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QRadioButton, QStackedWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from lunelis import paths
from lunelis.catalog.schema import open_catalog
from lunelis.importers.formats import CATALOGED_EXTS
from lunelis.migrate import layout
from lunelis.settings import Settings

STEPS = ("Sources", "Target", "Layout", "Duplicates", "Leftovers", "Safety", "Dry run", "Run", "Release")
COPY_MB_PER_S = 45          # a gigabit network through the PC: ~6-8 h per TB


def _gb(n: int) -> str:
    return f"{n / 1e12:,.2f} TB" if n >= 1e12 else f"{n / 1e9:,.1f} GB" if n >= 1e8 else f"{n / 1e6:,.0f} MB"


def leftovers(roots: list[str]) -> list[tuple[str, int, int]]:
    """(type, files, bytes) of what in these folders isn't a photo or video, most first."""
    from lunelis.migrate.logs import inventory
    n, b = Counter(), Counter()
    for root in roots:
        for rel, size, _m in inventory(root):
            name = rel.rsplit("/", 1)[-1]
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else "(no extension)"
            if ext in CATALOGED_EXTS:
                continue
            kind = "Takeout JSON" if name.lower().endswith(".json") and ".supplemental" in name.lower() else \
                "Sidecar (XMP)" if ext == "xmp" else ext.upper() if ext != "(no extension)" else ext
            n[kind] += 1
            b[kind] += size
    return sorted(((k, n[k], b[k]) for k in n), key=lambda t: -t[1])


def layout_preview(conn, sources: list[int], opts: layout.LayoutOptions, limit: int = 12) -> list[tuple[str, str]]:
    """(where it is, where it would go) for a spread of real photos and videos."""
    if not sources:
        return []
    q = ",".join("?" * len(sources))
    total = conn.execute(f"SELECT COUNT(*) FROM files WHERE root_id IN ({q})", sources).fetchone()[0] or 1
    step = max(1, total // limit)
    rows = conn.execute(
        f"SELECT f.rel_path, f.format, e.captured_at, e.camera_model FROM files f LEFT JOIN exif e ON e.file_id = f.id"
        f" WHERE f.root_id IN ({q}) AND f.missing_since IS NULL AND f.excluded = 0 ORDER BY f.id", sources).fetchall()
    out = []
    from datetime import datetime
    for rel, fmt, taken, cam in rows[::step][:limit]:
        try:
            t = datetime.fromisoformat(taken[:19]) if taken else None
        except ValueError:
            t = None
        kind = layout.media_kind(fmt, {"mp4", "mov", "mpeg-ts"})
        out.append((rel, layout.place(opts, taken=t, kind=kind, camera=cam) + "\\" + rel.rsplit("/", 1)[-1]))
    return out


class _Worker(QObject):
    done = Signal(object)

    def __init__(self, fn, db: str) -> None:
        super().__init__()
        self.fn, self.db = fn, db

    def run(self) -> None:
        conn = open_catalog(self.db)                 # the wizard's own catalog, on this thread
        try:
            self.done.emit(self.fn(conn))
        except Exception as e:                       # shown, never fatal
            self.done.emit(e)
        finally:
            conn.close()


class MigrationWizard(QDialog):
    job_started = Signal(int)
    library_changed = Signal()
    open_takeout = Signal()

    def __init__(self, conn, parent=None) -> None:
        super().__init__(parent)
        self.conn = conn
        self.setWindowTitle("Migration wizard")
        self.resize(820, 620)
        self.migration_id: int | None = None
        self.summary = None
        self._thread = None
        v = QVBoxLayout(self)
        self.crumbs = QLabel(objectName="Help")
        v.addWidget(self.crumbs)
        self.pages = QStackedWidget()
        v.addWidget(self.pages, 1)
        for build in (self._sources, self._target, self._layout, self._dupes, self._leftovers, self._safety,
                      self._dry_run, self._run_page, self._release):
            self.pages.addWidget(build())
        foot = QHBoxLayout()
        self.status = QLabel(objectName="Help", wordWrap=True)
        foot.addWidget(self.status, 1)
        self.back_b = QPushButton("Back", clicked=lambda: self.go(self.pages.currentIndex() - 1))
        self.next_b = QPushButton("Next", objectName="Primary", clicked=self._next)
        foot.addWidget(self.back_b)
        foot.addWidget(self.next_b)
        v.addLayout(foot)
        self._resume()

    # --- steps -----------------------------------------------------------------------------

    def _page(self, title: str, text: str) -> tuple[QWidget, QVBoxLayout]:
        w = QWidget()
        lv = QVBoxLayout(w)
        lv.addWidget(QLabel(title, objectName="PageTitle"))
        t = QLabel(text, objectName="Help", wordWrap=True)
        lv.addWidget(t)
        return w, lv

    def _sources(self) -> QWidget:
        w, lv = self._page("1. Sources", "Tick every folder whose photos and videos go into the new Library. "
                           "A Google Takeout folder: tick it here, then choose what comes on the Google Takeout page.")
        self.src_list = QListWidget()
        from lunelis.importers.takeout import takeout_roots
        takeout = set(takeout_roots(self.conn))
        for rid, path, n in self.conn.execute(
                "SELECT r.id, r.path, COUNT(f.id) FROM roots r LEFT JOIN files f ON f.root_id = r.id"
                " AND f.missing_since IS NULL AND f.excluded = 0 WHERE r.enabled = 1 GROUP BY r.id ORDER BY r.path"):
            it = QListWidgetItem(f"{path}   ({n:,} photos and videos)" + ("   - Google Takeout" if rid in takeout else ""))
            it.setData(Qt.ItemDataRole.UserRole, rid)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Unchecked)
            self.src_list.addItem(it)
        lv.addWidget(self.src_list, 1)
        b = QPushButton("Open the Google Takeout page…", clicked=self.open_takeout.emit)
        b.setVisible(bool(takeout))
        lv.addWidget(b, 0, Qt.AlignmentFlag.AlignLeft)
        return w

    def _target(self) -> QWidget:
        w, lv = self._page("2. Target", "The empty dataset or drive for the new Library. Lunelis makes two folders "
                           "in it: Library (only photos and videos) and Lunelis (exports, Create folders, backups, "
                           "Duplicates, Trash and the migration logs).")
        row = QHBoxLayout()
        self.target = QLabel(objectName="PathField")
        self.target.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(self.target, 1)
        row.addWidget(QPushButton("Choose…", clicked=self._choose_target))
        lv.addLayout(row)
        self.target_note = QLabel(objectName="Help", wordWrap=True)
        lv.addWidget(self.target_note)
        lv.addStretch(1)
        return w

    def _layout(self) -> QWidget:
        w, lv = self._page("3. Layout", "Library\\Photos and Videos\\Year\\Day[ Event]\\ with Photos, Videos and "
                           "Timelapse (one folder per timelapse) inside. A day with several events takes the first "
                           "one's name. Photos with no date go to Library\\Undated.")
        self.undated_mtime = QCheckBox("File a photo with no date by its modified date, when that date is believable")
        self.undated_mtime.toggled.connect(self._preview_layout)
        lv.addWidget(self.undated_mtime)
        row = QHBoxLayout()
        row.addWidget(QLabel("Inside Photos:"))
        self.photo_sub = QComboBox()
        for label, key in (("One folder for the day", "none"), ("A folder per camera", "camera"),
                           ("The folder each photo came from", "original")):
            self.photo_sub.addItem(label, key)
        self.photo_sub.currentIndexChanged.connect(self._preview_layout)
        row.addWidget(self.photo_sub)
        row.addStretch(1)
        lv.addLayout(row)
        lv.addWidget(QLabel("Where some of your photos would go:", objectName="SectionTitle"))
        self.layout_view = QTextBrowser()
        lv.addWidget(self.layout_view, 1)
        return w

    def _dupes(self) -> QWidget:
        w, lv = self._page("4. Duplicates", "One copy of each identical group moves - only groups proven identical "
                           "byte for byte (verified). The copy kept is the one with your work on it (ratings, edits, "
                           "tags, a sidecar), else the older file; not a Google Takeout copy. The others go to "
                           "Lunelis\\Duplicates with their original paths, after their ratings and tags move to "
                           "the kept copy.")
        self.one_copy = QCheckBox("Move one copy of each verified identical group", checked=True)
        lv.addWidget(self.one_copy)
        self.dupe_note = QLabel(objectName="Help", wordWrap=True)
        lv.addWidget(self.dupe_note)
        lv.addStretch(1)
        return w

    def _leftovers(self) -> QWidget:
        w, lv = self._page("5. Leftovers", "Files in the sources that aren't photos or videos - documents, camera "
                           "XML, Thumbs.db, Takeout JSON. They stay where they are and are listed in the migration "
                           "logs; sidecars travel with their photos.")
        self.left_view = QTextBrowser()
        lv.addWidget(self.left_view, 1)
        return w

    def _safety(self) -> QWidget:
        w, lv = self._page("6. Safety", "Every copy is read back and compared before anything else happens to its "
                           "original. File names never change. The catalog is backed up before it starts.")
        self.mode = QButtonGroup(self)
        keep = QRadioButton("Keep the originals until I've reviewed the new library (recommended)", checked=True)
        move = QRadioButton("Move each original aside as soon as its copy is verified")
        self.mode.addButton(keep, 0)
        self.mode.addButton(move, 1)
        lv.addWidget(keep)
        lv.addWidget(move)
        row = QHBoxLayout()
        row.addWidget(QLabel("The Trash keeps set-aside files:"))
        self.keep_days = QComboBox()
        for label, days in (("Forever", 0), ("30 days", 30), ("90 days", 90), ("A year", 365)):
            self.keep_days.addItem(label, days)
        self.keep_days.setCurrentIndex(max(0, self.keep_days.findData(Settings(self.conn).get("trash_keep_days"))))
        row.addWidget(self.keep_days)
        row.addStretch(1)
        lv.addLayout(row)
        lv.addWidget(QLabel("Then they're offered for removal on the Quarantine page - nothing goes without you "
                            "saying so.", objectName="Help", wordWrap=True))
        lv.addStretch(1)
        return w

    def _dry_run(self) -> QWidget:
        w, lv = self._page("7. Dry run", "The plan, worked out from your real files. Nothing has moved.")
        self.plan_view = QTextBrowser()
        lv.addWidget(self.plan_view, 1)
        return w

    def _run_page(self) -> QWidget:
        w, lv = self._page("8. Run", "The copy runs as a background job: pause it, close Lunelis, let the NAS "
                           "sleep - it carries on where it stopped. Its progress is on the Jobs button.")
        self.run_view = QLabel(objectName="Help", wordWrap=True)
        lv.addWidget(self.run_view)
        lv.addStretch(1)
        return w

    def _release(self) -> QWidget:
        w, lv = self._page("9. Release", "When the copy is done, check the new Library for a while. Then the "
                           "accounted-for report checks every source file; only when every one is accounted for "
                           "can the originals be moved aside (to Lunelis\\Trash).")
        self.report_view = QTextBrowser()
        lv.addWidget(self.report_view, 1)
        row = QHBoxLayout()
        self.check_b = QPushButton("Check now", clicked=self._check)
        self.release_b = QPushButton("Release the originals…", clicked=self._do_release)
        self.release_b.setEnabled(False)
        row.addWidget(self.check_b)
        row.addStretch(1)
        row.addWidget(self.release_b)
        lv.addLayout(row)
        return w

    # --- moving between steps -----------------------------------------------------------------

    def go(self, i: int) -> None:
        i = max(0, min(len(STEPS) - 1, i))
        self.pages.setCurrentIndex(i)
        self.crumbs.setText("  ›  ".join(f"<b>{n}</b>" if k == i else n for k, n in enumerate(STEPS)))
        self.back_b.setEnabled(i > 0 and i < 7)
        self.next_b.setText({6: "Start the copy…", 7: "Next", 8: "Close"}.get(i, "Next"))
        self.status.setText("")
        {2: self._preview_layout, 3: self._dupe_counts, 4: self._scan_leftovers, 6: self._make_plan,
         7: self._show_run, 8: self._check}.get(i, lambda: None)()

    def _next(self) -> None:
        i = self.pages.currentIndex()
        if i == 0 and not self._chosen():
            self.status.setText("Tick at least one source.")
            return
        if i == 1 and not self.target.text():
            self.status.setText("Choose the target first.")
            return
        if i == 6:
            self._start()
            return
        if i == 8:
            self.accept()
            return
        self.go(i + 1)

    def _resume(self) -> None:
        """A migration already under way: straight to its step."""
        row = self.conn.execute("SELECT id, state, target FROM migrations WHERE state IN ('running', 'done')"
                                " ORDER BY id DESC LIMIT 1").fetchone()
        if row:
            self.migration_id = row[0]
            self.target.setText(row[2])
            self.go(7 if row[1] == "running" else 8)
        else:
            self.go(0)

    def _chosen(self) -> list[int]:
        return [self.src_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.src_list.count())
                if self.src_list.item(i).checkState() == Qt.CheckState.Checked]

    def _choose_target(self) -> None:
        picked = QFileDialog.getExistingDirectory(self, "The new dataset or drive for the Library")
        if not picked:
            return
        from lunelis.migrate.plan import PlanError, check_target
        try:
            check_target(self.conn, picked)
        except PlanError as e:
            self.target_note.setText(str(e))
            return
        self.target.setText(os.path.normpath(picked))
        self.target_note.setText(f"Library: {os.path.join(picked, 'Library')}\nLunelis folder: "
                                 f"{os.path.join(picked, 'Lunelis')} (set as the Lunelis folder when the copy starts)")

    def _layout_opts(self) -> layout.LayoutOptions:
        return layout.LayoutOptions(self.undated_mtime.isChecked(), self.photo_sub.currentData())

    def _preview_layout(self, *_a) -> None:
        rows = layout_preview(self.conn, self._chosen(), self._layout_opts())
        self.layout_view.setHtml("".join(f"<p><span style='color:gray'>{a}</span><br>&rarr; {b}</p>"
                                         for a, b in rows) or "<p>No photos in the chosen sources yet.</p>")

    def _dupe_counts(self) -> None:
        src = self._chosen()
        if not src:
            return
        q = ",".join("?" * len(src))
        verified, unverified = self.conn.execute(
            "SELECT COALESCE(SUM(g.method = 'exact' AND g.verified = 1), 0), COALESCE(SUM(g.method = 'sampled'), 0)"
            f" FROM duplicate_groups g WHERE EXISTS (SELECT 1 FROM duplicate_group_files m JOIN files f"
            f" ON f.id = m.file_id WHERE m.group_id = g.id AND f.root_id IN ({q}))", src).fetchone()
        self.dupe_note.setText(
            f"{verified:,} verified identical groups. "
            + (f"{unverified:,} more groups only look identical (sampled) - they're compared byte for byte when "
               "copied, and copied once if they match. Duplicates > Verify first makes the plan exact."
               if unverified else "Nothing left to verify."))

    def _scan_leftovers(self) -> None:
        roots = [p for rid, p in self.conn.execute("SELECT id, path FROM roots") if rid in set(self._chosen())]
        self.left_view.setHtml("<p>Looking through the sources…</p>")
        self._run(lambda c: leftovers(roots), self._show_leftovers)

    def _show_leftovers(self, rows) -> None:
        if isinstance(rows, Exception):
            self.left_view.setHtml(f"<p>Couldn't look: {rows}</p>")
            return
        self.left_view.setHtml("<table>" + "".join(f"<tr><td>{k}</td><td align=right>{n:,} files</td>"
                                                   f"<td align=right>&nbsp; {_gb(b)}</td></tr>" for k, n, b in rows)
                               + "</table>" if rows else "<p>Nothing but photos and videos.</p>")

    def _options(self):
        from lunelis.migrate.plan import Options
        return Options(self._chosen(), keep_sources=self.mode.checkedId() == 0, one_copy=self.one_copy.isChecked(),
                       library_layout=True, undated_by_mtime=self.undated_mtime.isChecked(),
                       photo_subfolders=self.photo_sub.currentData())

    def _make_plan(self) -> None:
        from lunelis.migrate.plan import discard, plan, summary
        if self.migration_id is not None:
            discard(self.conn, self.migration_id)
            self.migration_id = None
        target, opts = self.target.text(), self._options()
        preferred = Settings(self.conn).get("preferred_roots")
        self.plan_view.setHtml("<p>Working out where everything goes…</p>")
        self.next_b.setEnabled(False)
        self._run(lambda c: (lambda mid: (mid, summary(c, mid)))(
            plan(c, target, layout.DAY_TEMPLATE, opts, preferred)), self._planned)

    def _planned(self, result) -> None:
        if isinstance(result, Exception):
            self.plan_view.setHtml(f"<p>The plan couldn't be made: {result}</p>")
            return
        self.migration_id, s = result
        self.summary = s
        hours = s.move_bytes / (COPY_MB_PER_S * 1e6) / 3600
        lines = [f"<b>{s.move_files:,} files ({_gb(s.move_bytes)})</b> go to {s.target}\\Library - about "
                 f"{hours:,.1f} hours of copying.",
                 f"{s.dup_files:,} identical copies set aside ({_gb(s.dup_bytes)}).",
                 f"{s.undated:,} with no date go to Library\\Undated." if s.undated else "",
                 f"{s.damaged_skipped:,} damaged files left behind - an intact copy goes instead."
                 if s.damaged_skipped else "",
                 f"{s.takeout_skipped:,} Google Takeout items left in place (unticked)." if s.takeout_skipped else "",
                 f"{s.sibling_folders:,} files whose name is taken go to a sibling folder." if s.sibling_folders else "",
                 ("Free space: " + (_gb(s.free_bytes) if s.free_bytes is not None else "unknown")
                  + ("" if s.enough_space else " - <b>not enough</b>"))]
        self.plan_view.setHtml("".join(f"<p>{x}</p>" for x in lines if x))
        self.next_b.setEnabled(s.enough_space and s.move_files > 0)

    def _start(self) -> None:
        s = self.summary
        if s is None:
            return
        if QMessageBox.question(self, "Start the copy?", f"Copy {s.move_files:,} files ({_gb(s.move_bytes)}) into "
                                f"{s.target}\\Library?\n\nEach copy is verified before anything else happens to its "
                                "original. The catalog is backed up first.") != QMessageBox.StandardButton.Yes:
            return
        st = Settings(self.conn)
        st.set("lunelis_folder", os.path.join(s.target, "Lunelis"))
        st.set("trash_keep_days", self.keep_days.currentData())
        from lunelis import lunelis_folder
        try:
            lunelis_folder.make_folders(st)
        except OSError as e:
            self.status.setText(f"The Lunelis folder couldn't be made: {e}")
            return
        from lunelis.catalog import backup
        from lunelis.migrate import execute
        folder = backup.backup_dir(st, paths.DATA_DIR)
        mid = self.migration_id
        self._run(lambda c: execute.start(c, mid, backup_dir=folder), self._started)

    def _started(self, job_id) -> None:
        if isinstance(job_id, Exception):
            self.status.setText(f"It couldn't start: {job_id}")
            return
        self.job_started.emit(job_id)
        self.library_changed.emit()
        self.go(7)

    def _show_run(self) -> None:
        if self.migration_id is None:
            return
        states = dict(self.conn.execute("SELECT state, COUNT(*) FROM migration_items WHERE migration_id = ?"
                                        " GROUP BY state", (self.migration_id,)).fetchall())
        done = sum(n for st, n in states.items() if st != "planned")
        total = sum(states.values())
        self.run_view.setText(f"{done:,} of {total:,} files handled"
                              + (f" · {states['failed']:,} failed - see the Migrate page" if states.get("failed") else "")
                              + ". Come back to this window any time (Migrate > Migration wizard).")

    def _check(self) -> None:
        if self.migration_id is None:
            return
        from lunelis.migrate import logs
        mid = self.migration_id
        self.report_view.setHtml("<p>Checking every source file…</p>")
        self._run(lambda c: logs.accounted(c, mid), self._checked)

    def _checked(self, rep) -> None:
        if isinstance(rep, Exception):
            self.report_view.setHtml(f"<p>Couldn't check: {rep}</p>")
            return
        self.report_view.setPlainText(rep.text())
        state = self.conn.execute("SELECT state FROM migrations WHERE id = ?", (rep.migration_id,)).fetchone()
        kept = self.conn.execute("SELECT COUNT(*) FROM migration_items WHERE migration_id = ? AND state = 'kept'",
                                 (rep.migration_id,)).fetchone()[0]
        self.release_b.setEnabled(rep.clean and bool(state) and state[0] == "done" and kept > 0)

    def _do_release(self) -> None:
        if QMessageBox.question(self, "Release the originals?", "Move the originals kept for review to "
                                "Lunelis\\Trash? Nothing is deleted - the Quarantine page can put them back.") \
                != QMessageBox.StandardButton.Yes:
            return
        from lunelis.catalog import backup
        from lunelis.migrate import execute
        folder = backup.backup_dir(Settings(self.conn), paths.DATA_DIR)
        mid = self.migration_id
        self._run(lambda c: execute.release(c, mid, backup_dir=folder), self._released)

    def _released(self, n) -> None:
        if isinstance(n, Exception):
            self.status.setText(str(n))
            return
        self.status.setText(f"{n:,} originals moved to the Trash.")
        self.library_changed.emit()
        self._check()

    # --- workers ------------------------------------------------------------------------------------

    def _run(self, fn, then) -> None:
        if self._thread is not None:
            return
        self._thread = QThread(self)
        from lunelis.ui.quarantine_view import db_file
        self._worker = _Worker(fn, db_file(self.conn))
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._then = then
        self._worker.done.connect(self._finished)
        self._thread.start()

    def _finished(self, result) -> None:
        self._thread.quit()
        self._thread.wait()
        self._thread = None
        self._then(result)

    def wait(self) -> None:
        """Tests: let a running step finish."""
        while self._thread is not None:
            from PySide6.QtWidgets import QApplication
            QApplication.processEvents()
