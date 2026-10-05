"""
The main window: sidebar, toolbar, filter bar, library grid (Library mockup).

Scans, metadata and thumbnails run on one background QThread with its own
catalog connection (sqlite3 connections can't cross threads). The Library
menu is still the dev trigger for them until the Import screen (Step 9).
"""
from __future__ import annotations

import logging
import os

from PySide6.QtCore import QObject, QSize, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QSizePolicy,
    QApplication, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSlider, QStackedWidget, QToolButton,
    QVBoxLayout, QWidget,
)

from lunelis import paths
from lunelis.catalog import backup, ratings
from lunelis.catalog.schema import open_catalog
from lunelis.importers.metadata import ExtractResult, extract_pending
from lunelis.importers.scan import (
    RootOverlap, RootUnavailable, ScanResult, add_root, catalog_stats, scan_root,
)
from lunelis import stacks
from lunelis.dupes import similar
from lunelis.raw import thumbnails
from lunelis.jobs import engine
from lunelis.damage.check import check as check_damage
from lunelis.importers.relink import relink
from lunelis.importers.takeout import import_root as import_takeout, takeout_roots
from lunelis.importing import ingest
from lunelis.ui.damaged_view import DamagedView
from lunelis.ui.import_view import ImportView
from lunelis.ui.dupes_view import DuplicatesView
from lunelis.ui.grid import DEFAULT_TILE, MAX_TILE, MIN_TILE, PhotoGrid
from lunelis.ui.jobs import JobRunner, JobsDialog, ScopeDialog
from lunelis.ui.background import unless_closed
from lunelis.ui.library import SORTS, UNRATED, Filter, LibraryIndex
from lunelis.settings import Settings
from lunelis.ui.settings_view import SettingsView
from lunelis.ui.events_view import EventsView
from lunelis.ui.migrate_view import MigrateView
from lunelis.ui.backups_view import BackupsView
from lunelis.ui.detail_view import DetailView
from lunelis.ui.albums_view import AlbumsView
from lunelis.events import model as events
from lunelis.ui.theme import apply_palette, stylesheet
from lunelis.ui.thumbcache import ThumbCache
from lunelis.xmp import sync
from lunelis.xmp.sidecar import LABELS
from lunelis.ui.widgets import plain

# Lightroom/darktable number keys for colour labels (Purple has no key there either).
LABEL_KEYS = {"Red": "6", "Yellow": "7", "Green": "8", "Blue": "9", "Purple": None}
XMP_WRITE_DELAY_MS = 1200     # write sidecars shortly after the user stops rating
SIDEBAR_WIDE, SIDEBAR_NARROW = 240, 64     # px: with page names / icons only (Ctrl+B)
AUTO_SIDEBAR_WIDTH = 1100                  # px: below this the sidebar folds to icons by itself
CLOSE_SIDECAR_LIMIT = 200                  # sidecars written while closing; more wait for the next start
RATE_CONFIRM = 500                         # ask before rating / labelling / flagging more photos than this

# Sidebar sections (collapsible). Pages appear here once they're built -
# no greyed-out placeholders.
NAV = [
    ("Photos", ["Library", "Albums", "Tags", "Edit", "Map", "On this day", "Stats"]),
    ("Create", ["Create"]),
    ("Bring in & organize", ["Import", "Migrate", "Duplicates", "Damaged files"]),
    ("Keep safe", ["Library status", "Backups", "Quarantine"]),
]
BOTTOM_NAV = ["Settings"]


# The library worker's steps, in order: the status strip shows "Step n of 9".
SCAN_STEPS = ("Scanning folders", "Reading sidecars", "Reading metadata", "Google Takeout dates",
              "Finding burst shots", "Updating the search index", "Making thumbnails", "Comparing photos",
              "Checking for damaged files")


STICK_COUNT_SECONDS = 10         # how long a new USB drive is looked through for photos
EDITS_INLINE = 20                # Paste / Reset on more photos than this saves on a worker
SEARCH_NOW_LIMIT = 2000
REACH_CHECK_MS = 60_000          # how often sources are checked for being reachable          # search-index rows brought up to date before a query; the rest on a worker

def _thread_catalog():
    """A worker thread's own catalog connection. Not this module's `open_catalog`:
    tests swap that for their own connection, which another thread may not use
    (and the worker would close it)."""
    from lunelis.catalog import schema
    return schema.open_catalog(paths.DEFAULT_CATALOG_PATH)


class LibraryWorker(QObject):
    """scan -> metadata -> thumbnails, on a background thread."""

    progress = Signal(str)
    step = Signal(int, int, int)     # step (SCAN_STEPS index), done, total (0 = unknown)
    root_done = Signal(object)       # ScanResult
    root_failed = Signal(str)
    sidecars_done = Signal(object)   # SyncResult
    meta_done = Signal(object)       # ExtractResult
    thumb_done = Signal(object)      # ThumbResult
    relinked = Signal(int)           # entries re-linked to a moved file
    takeout_done = Signal(object)    # TakeoutResult
    damage_done = Signal(object)     # CheckResult
    similar_done = Signal(object)    # int near-duplicate groups (only when new photos were compared)
    noticed_done = Signal(int)       # new Lunelis noticed suggestions
    stacks_done = Signal(object)     # int burst stacks (None when stacking is off)
    finished = Signal()

    def __init__(self, root_ids: list[int]) -> None:
        super().__init__()
        self.root_ids = root_ids
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def _say(self, i: int, text: str, done: int = 0, total: int = 0) -> None:
        self.step.emit(i, done, total)       # the step first, so its text lands on the right row
        self.progress.emit(text)

    def run(self) -> None:
        conn = _thread_catalog()
        stop = lambda: self._cancel  # noqa: E731
        try:
            for root_id in self.root_ids:
                if self._cancel:
                    break
                try:
                    self._say(0, "Scanning…")
                    self.root_done.emit(scan_root(
                        conn, root_id, should_cancel=stop,
                        on_progress=lambda n, d: self._say(0, f"Scanning… {n:,} files  —  {d}"),
                    ))
                except RootUnavailable as e:
                    self.root_failed.emit(str(e))
            # Files moved/renamed outside Lunelis keep their catalog entry
            # (cheap tier now; the EXIF and hash tiers after metadata).
            linked = 0
            if not self._cancel:
                linked += relink(conn, use_hashes=False, sidecar_store=self._store(conn),
                                 thumbnail_cache=paths.THUMBNAIL_CACHE).linked
            # Sidecars changed outside Lunelis (darktable, a culling tool) -> catalog.
            if not self._cancel:
                self._say(1, "Reading sidecars…")
                self.sidecars_done.emit(sync.import_sidecars(
                    conn, should_cancel=stop,
                    on_progress=lambda d, t, f: self._say(1, f"Reading sidecars… {d:,} / {t:,}", d, t),
                ))
            if not self._cancel:
                self._say(2, "Reading metadata…")
                self.meta_done.emit(extract_pending(
                    conn, should_cancel=stop,
                    on_progress=lambda d, t, f: self._say(2, f"Reading metadata… {d:,} / {t:,}", d, t),
                ))
            # Google Takeout exports: dates/GPS from the JSON sidecars, for files
            # whose own metadata has none (needs the metadata pass first).
            if not self._cancel:
                for rid in takeout_roots(conn):
                    if rid in self.root_ids:
                        self._say(3, "Reading Google Takeout dates…")
                        self.takeout_done.emit(import_takeout(conn, rid))
            # Burst stacks from the capture times just read (~0.6 s on 159k).
            if not self._cancel:
                self._say(4, "Finding burst shots…")
                self.stacks_done.emit(stacks.rebuild_from_settings(conn))
                from lunelis import pairs
                pairs.rebuild_from_settings(conn)                    # RAW+JPEG shots as one photo
            # The search index, for whatever the scan and metadata changed (~1.5 s for all 159k).
            if not self._cancel:
                from lunelis import search
                if search.pending(conn):
                    self._say(5, "Updating the search index…")
                    search.refresh(conn)
            if not self._cancel:
                linked += relink(conn, sidecar_store=self._store(conn),
                                 thumbnail_cache=paths.THUMBNAIL_CACHE).linked
            if linked:
                self.relinked.emit(linked)
            # Thumbnails after metadata: RAW previews need the EXIF orientation.
            if not self._cancel:
                self._say(6, "Making thumbnails…")
                self.thumb_done.emit(thumbnails.generate_pending(
                    conn, paths.THUMBNAIL_CACHE, should_cancel=stop,
                    on_progress=lambda d, t, f: self._say(6, f"Making thumbnails… {d:,} / {t:,}", d, t),
                ))
            # Near-duplicate fingerprints come from the thumbnails just made
            # (local cache, no NAS reads); regroup only when there are new ones.
            if not self._cancel:
                groups = similar.refresh(
                    conn, paths.THUMBNAIL_CACHE, should_cancel=stop,
                    on_progress=lambda d, t: self._say(7, f"Comparing photos… {d:,} / {t:,}", d, t))
                if groups is not None:
                    self.similar_done.emit(groups)
            # Scene suggestions for photos the model hasn't seen (thumbnails only).
            if not self._cancel:
                self._scene_tags(conn, stop)
            if not self._cancel:
                self._noticed(conn, stop)
            if not self._cancel:
                self._say(8, "Checking for damaged files…")
                self.damage_done.emit(check_damage(conn))
        finally:
            conn.close()
            self.finished.emit()

    def _scene_tags(self, conn, stop) -> None:
        from lunelis.settings import Settings
        if not Settings(conn).get("scene_tags_auto"):
            return
        from lunelis.recognize import scenes
        rec = scenes.backend()
        if rec is None:
            return
        todo = [r[0] for r in conn.execute(
            "SELECT f.id FROM files f LEFT JOIN embeddings em ON em.file_id = f.id AND em.model = ?"
            " WHERE em.file_id IS NULL AND f.thumbnail_path IS NOT NULL AND f.missing_since IS NULL"
            " AND f.excluded = 0 AND f.quarantined_at IS NULL AND COALESCE(f.format, '') NOT IN ('mp4', 'mov', 'mpeg-ts')",
            (rec.model_id,))]
        for start in range(0, len(todo), 64):
            if stop():
                return
            self._say(7, f"Looking at scenes… {start:,} / {len(todo):,}", start, len(todo))
            scenes.tag_files(conn, todo[start:start + 64], rec, paths.DATA_DIR, stop)

    def _noticed(self, conn, stop) -> None:
        from lunelis.settings import Settings
        if not Settings(conn).get("noticed_auto"):
            return
        from lunelis import noticed
        self._say(7, "Looking for brackets, panoramas and timelapses…")
        n = noticed.find(conn, paths.THUMBNAIL_CACHE, stop)
        if n:
            self.noticed_done.emit(n)

    @staticmethod
    def _store(conn):
        from lunelis.settings import Settings
        return Settings(conn).get("sidecar_store_dir") or paths.SIDECAR_STORE


class CatalogBackup(QObject):
    """The daily catalog snapshot (or one asked for now), off the GUI thread
    (VACUUM INTO of a ~500 MB catalog takes seconds)."""

    done = Signal(object)            # Path | None

    def __init__(self, now: bool = False) -> None:
        super().__init__()
        self.now = now

    def run(self) -> None:
        conn = _thread_catalog()
        try:
            if self.now:
                from lunelis.settings import Settings
                folder = backup.backup_dir(Settings(conn), paths.DATA_DIR)
                self.done.emit(backup.snapshot(conn, folder, "manual"))
            else:
                self.done.emit(backup.snapshot_if_due(conn, paths.DATA_DIR))
        except Exception as e:       # a failed backup must never take the app down
            self.done.emit(e)
        finally:
            conn.close()


class AutopilotWorker(QObject):
    """Runs every unfinished autopilot (importing/autopilot.py) on its own connection."""

    progress = Signal(str)
    done = Signal(int)                 # how many runs now wait for review

    def __init__(self) -> None:
        super().__init__()
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        from lunelis.importing import autopilot
        conn = _thread_catalog()
        n = 0
        try:
            for rid in autopilot.unfinished(conn):
                if self._cancel:
                    break
                r = autopilot.run(conn, rid, paths.THUMBNAIL_CACHE, paths.DATA_DIR, self.progress.emit,
                                  lambda: self._cancel)
                n += r["state"] == "review"
        except Exception:
            import logging
            logging.getLogger("lunelis").exception("autopilot failed")
        finally:
            conn.close()
            self.done.emit(n)


class XmpWriter(QObject):
    """Writes pending ratings to sidecars (xmp.sync.export_pending) off the GUI thread."""

    done = Signal(object)            # SyncResult

    def run(self) -> None:
        conn = _thread_catalog()
        try:
            self.done.emit(sync.export_pending(conn))
        finally:
            conn.close()


def _divider() -> QFrame:
    f = QFrame()
    f.setObjectName("VDivider")
    return f


class StatusLabel(QLabel):
    # The status line; every message also goes to the log, so the log tells
    # the same story the user saw. A message can carry a link to a page
    # (<a href="page:Damaged files">), which opens that page when clicked.
    def setText(self, text: str) -> None:
        import re
        plain_text = re.sub("<[^>]+>", "", text or "")
        if text and text != self.text():
            import logging
            logging.getLogger("lunelis.status").info(plain_text)
        super().setText(text)
        self.setToolTip(plain_text)              # the whole message, when a narrow window cuts it off

    def set_link(self, before: str, link: str, page: str) -> None:
        from lunelis.ui import theme
        self.setText(f'{before}<a href="page:{page}" style="color:{theme.current().accent}">{link}</a>')

    def set_links(self, parts: list[tuple[str, str]]) -> None:
        """Several (text, page) links, separated by dots."""
        from lunelis.ui import theme
        c = theme.current().accent
        self.setText(" · ".join(f'<a href="page:{page}" style="color:{c}">{text}</a>' for text, page in parts))


class SpringLoad(QObject):
    """Photos dragged from the library and held over a sidebar entry open its
    page (Albums), so they can be dropped on an album there."""

    DELAY_MS = 600

    def __init__(self, button, opener) -> None:
        super().__init__(button)
        self.button, self.opener = button, opener
        button.setAcceptDrops(True)
        button.installEventFilter(self)
        self.timer = QTimer(self, singleShot=True, interval=self.DELAY_MS, timeout=opener)

    def eventFilter(self, obj, e) -> bool:
        from PySide6.QtCore import QEvent
        from lunelis.ui.grid import PhotoGrid
        t = e.type()
        if t in (QEvent.Type.DragEnter, QEvent.Type.DragMove) and e.mimeData().hasFormat(PhotoGrid.DRAG_MIME):
            e.acceptProposedAction()
            if t == QEvent.Type.DragEnter:
                self.timer.start()
            return True
        if t in (QEvent.Type.DragLeave, QEvent.Type.Drop):
            self.timer.stop()
            return t == QEvent.Type.Drop
        return False


class PageStack(QStackedWidget):
    """The pages, sized for small and scaled screens: a page that can't
    shrink sits in a scroll area, and only the page on show counts toward
    the window's minimum size (a QStackedWidget counts every page, so one
    wide page used to stop the whole window going below 1145 x 763).
    Callers still see the pages themselves - currentWidget(), widget(),
    indexOf() and setCurrentWidget() unwrap and wrap."""

    def __init__(self) -> None:
        super().__init__()
        self._area: dict[QWidget, QScrollArea] = {}
        self._page: dict[QScrollArea, QWidget] = {}
        self.currentChanged.connect(lambda _i: self.updateGeometry())

    def addWidget(self, page: QWidget, scroll: bool = True) -> int:
        if not scroll:
            return super().addWidget(page)
        area = QScrollArea(objectName="PageScroll", widgetResizable=True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setWidget(page)
        self._area[page], self._page[area] = area, page
        return super().addWidget(area)

    def setCurrentWidget(self, page: QWidget) -> None:
        super().setCurrentWidget(self._area.get(page, page))

    def currentWidget(self) -> QWidget | None:
        w = super().currentWidget()
        return self._page.get(w, w)

    def widget(self, i: int) -> QWidget | None:
        w = super().widget(i)
        return self._page.get(w, w)

    def indexOf(self, page: QWidget) -> int:
        return super().indexOf(self._area.get(page, page))

    def minimumSizeHint(self) -> QSize:
        w = super().currentWidget()
        if isinstance(w, QScrollArea):
            return QSize(200, 160)
        return w.minimumSizeHint() if w is not None else QSize(0, 0)

    def sizeHint(self) -> QSize:
        w = super().currentWidget()
        return w.sizeHint() if w is not None else QSize(0, 0)


class MainWindow(QMainWindow):
    def __init__(self, tray: bool = False) -> None:
        super().__init__()
        self._quitting = False
        self._tray_allowed = tray      # the real app; tests and scripts never get a tray
        self.setWindowTitle(f"Lunelis {paths.version()}")
        self.resize(1440, 900)
        icon_path = paths.ASSETS / "icons" / "app_icon.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.conn = open_catalog(paths.DEFAULT_CATALOG_PATH)
        self.apply_theme()
        from lunelis.ui import photoinfo
        photoinfo.set_date_format(Settings(self.conn).get("date_format"))
        self.index = LibraryIndex()
        self._thread: QThread | None = None
        self._worker: LibraryWorker | None = None
        self.filter = Filter()
        self._xmp_thread: QThread | None = None
        self._xmp_again = False
        self._xmp_timer = QTimer(self, singleShot=True, interval=XMP_WRITE_DELAY_MS,
                                 timeout=self._write_sidecars)

        self._build_menu()
        root = QWidget(objectName="Main")
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_sidebar())
        main = QWidget()
        col = QVBoxLayout(main)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        self.toolbar = self._build_toolbar()
        col.addWidget(self.toolbar)
        self.filter_bar = self._build_filter_bar()
        col.addWidget(self.filter_bar)
        from lunelis.ui.ask_bar import AskBar
        self.ask_bar = AskBar(self.conn)
        self.ask_bar.asked.connect(self._ask)
        self.ask_bar.closed.connect(lambda: self.filter.ranked and self.set_filter(Filter()))
        self.ask_bar.hide()
        col.addWidget(self.ask_bar)
        self.thumbs = ThumbCache(paths.THUMBNAIL_CACHE, self)
        self.grid = PhotoGrid(self.thumbs)
        self.grid.selection_changed.connect(self._update_count)
        self.grid.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.grid.paths_provider = self._paths_of
        self.grid.customContextMenuRequested.connect(
            lambda pos: self.photo_menu.exec(self.grid.mapToGlobal(pos)))
        self.pages = PageStack()
        self.pages.addWidget(self.grid, scroll=False)               # scrolls itself
        self.dupes = DuplicatesView(self.conn)
        self.dupes.start_verify.connect(self.verify_duplicates)
        self.pages.addWidget(self.dupes)
        self.damaged = DamagedView(self.conn)
        self.pages.addWidget(self.damaged)
        self.importer = ImportView(self.conn)
        self.importer.imported.connect(self._after_import)
        self.importer.autopilot.connect(self._autopilot_import)
        self._autopilot_thread: QThread | None = None
        self.pages.addWidget(self.importer)
        self.events_page = EventsView(self.conn)
        self.events_page.show_event.connect(self.show_event)
        self.events_page.back.connect(lambda: self.open_page("Albums"))
        self.pages.addWidget(self.events_page)
        self.albums_page = AlbumsView(self.conn)
        self.albums_page.open_album.connect(self.show_album)
        self.albums_page.add_to_album.connect(self._dropped_on_album)
        self.albums_page.open_suggestions.connect(lambda: self.show_page("Events"))
        self.pages.addWidget(self.albums_page, scroll=False)        # scrolls itself
        from lunelis.ui.quarantine_view import QuarantineView
        self.quarantine_page = QuarantineView(self.conn)
        self.quarantine_page.changed.connect(self.reload)
        self.pages.addWidget(self.quarantine_page)
        from lunelis.ui.tags_view import TagsView
        self.tags_page = TagsView(self.conn)
        self.tags_page.open_tag.connect(self.show_tag)
        self.pages.addWidget(self.tags_page)
        self.detail = DetailView(self.conn)
        self.detail.back.connect(self.close_detail)
        self.detail.rate.connect(lambda change: self.rate(**change))
        self.detail.current_changed.connect(self._detail_moved)
        self.detail.show_event.connect(self.show_event)
        self.detail.edited.connect(self._photo_edited)
        self.detail.tags_changed.connect(lambda: self.filter.tag and self.reload())
        self.pages.addWidget(self.detail, scroll=False)             # fills the window
        self.grid.activated.connect(self.open_detail)
        from lunelis.ui import photoinfo
        self.grid.info_provider = lambda fid: photoinfo.load(self.conn, fid)
        self.grid.hover_enabled = Settings(self.conn).get("hover_info")
        self.grid.zoom.connect(lambda step: self.size_slider.setValue(self.size_slider.value() + 20 * step))
        self.migrate_page = MigrateView(self.conn)
        self.migrate_page.job_started.connect(lambda _: self.runner.poke())
        self.migrate_page.library_changed.connect(self.reload)
        self.pages.addWidget(self.migrate_page)
        self.backups_page = BackupsView(self.conn)
        self.backups_page.job_started.connect(lambda _: self._job_queued())
        self.backups_page.restart.connect(self.restart)
        self.backups_page.library_changed.connect(self.reload)
        self.pages.addWidget(self.backups_page)
        from lunelis.ui.edit_page import EditPage
        self.edit_page = EditPage(self.conn)
        v = self.edit_page.view
        v.rate.connect(lambda change: self.rate(**change))
        v.show_event.connect(self.show_event)
        v.edited.connect(self._photo_edited)
        self.edit_page.copy_edit.connect(self._copy_edit_of)
        self.edit_page.paste_all.connect(self._paste_edit_to)
        self.edit_page.reset_all.connect(self._reset_edits_of)
        self.edit_page.export_all.connect(self.export_photos)
        self.pages.addWidget(self.edit_page, scroll=False)          # fills the window
        from lunelis.ui.stats_view import StatsView
        self.stats_page = StatsView(self.conn)
        self.pages.addWidget(self.stats_page, scroll=False)          # scrolls itself
        from lunelis.ui.map_view import MapView
        self.map_page = MapView(self.conn)
        self.map_page.show_ids.connect(lambda ids: self.show_photos(ids, "On the map"))
        self.pages.addWidget(self.map_page, scroll=False)
        from lunelis.ui.autopilot_view import AutopilotView
        self.review_page = AutopilotView(self.conn)
        self.review_page.show_ids.connect(self.show_photos)
        self.review_page.reviewed.connect(self._shoot_reviewed)
        self.pages.addWidget(self.review_page, scroll=False)
        from lunelis.ui.calendar_view import CalendarView
        self.calendar_page = CalendarView(self.conn)
        self.calendar_page.show_ids.connect(self.show_photos)
        self.pages.addWidget(self.calendar_page, scroll=False)
        from lunelis.ui.create_page import CreatePage
        self.create_page = CreatePage(self.conn)
        self.pages.addWidget(self.create_page, scroll=False)        # fills the window; wraps its text
        from lunelis.ui.status_view import StatusView
        self.status_page = StatusView(self.conn, SCAN_STEPS)
        self.status_page.open_page.connect(self.open_page)
        self.status_page.rescan.connect(lambda ids: self.start(ids) if ids else self.rescan_all())
        self.status_page.stop.connect(self.cancel_scan)
        self.status_page.show_ids.connect(self.show_photos)
        self.status_page.build.connect(self.build_noticed)
        self._noticed_pending: int | None = None      # a suggestion handed to the merge dialog
        self.pages.addWidget(self.status_page)
        self.settings_page = SettingsView(self.conn)
        self.settings_page.library_changed.connect(self.reload)
        self.settings_page.rescan.connect(self._rescan_roots)
        self.settings_page.add_source.connect(self.add_folder)
        self.settings_page.scene_job.connect(self._tag_the_library)
        self.settings_page.open_suggestions.connect(self._open_suggestions)
        self.settings_page.rewrite_sidecars.connect(self._xmp_timer.start)
        self.settings_page.tray_changed.connect(self.set_tray_enabled)
        self.settings_page.autostart_changed.connect(self._autostart_changed)
        self.settings_page.backup_now.connect(lambda: self._daily_backup(now=True))
        self.settings_page.restart.connect(self.restart)
        self.settings_page.theme_changed.connect(self.apply_theme)
        self.settings_page.view_changed.connect(self._view_defaults_changed)
        self.settings_page.view_changed.connect(self._auto_sidebar)
        self.settings_page.open_page.connect(self.open_page)
        self.settings_page.report_problem.connect(self.report_problem)
        self.settings_page.install_update.connect(self._install_update)
        self.pages.addWidget(self.settings_page, scroll=False)      # each tab scrolls
        col.addWidget(self.pages, 1)
        layout.addWidget(main, 1)
        self.setCentralWidget(root)

        self.status = StatusLabel()
        from lunelis import log
        log.set_crash_hook(self._crashed)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.status.linkActivated.connect(lambda href: self.open_page(href.split(":", 1)[1]))
        # While the library worker runs: "Step 3 of 9 · Reading metadata" and a bar.
        self.scan_step = StatusLabel(objectName="ScanStep")
        self.scan_step.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.scan_step.linkActivated.connect(lambda href: self.open_page(href.split(":", 1)[1]))
        self.scan_step.setMinimumWidth(self.scan_step.fontMetrics().horizontalAdvance(max(
            (f"Step 9 of 9 · {s}" for s in SCAN_STEPS), key=len)) + 24)
        self.scan_bar = QProgressBar(objectName="ScanProgress", textVisible=False)
        self.scan_bar.setFixedSize(180, 6)
        for w in (self.scan_step, self.scan_bar):
            w.hide()
            self.statusBar().addWidget(w)
        # The passing message takes what's left and is cut short rather than
        # pushing the rest off a narrow window (its tooltip has all of it).
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.status.setMinimumWidth(60)
        self._scan_step_min = self.scan_step.minimumWidth()
        self.statusBar().addWidget(self.status, 1)
        # The library's state stays put on the right ("Up to date · 9:41 PM"),
        # while passing messages come and go on the left.
        self.library_state = StatusLabel(objectName="LibraryState")
        self.library_state.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.library_state.linkActivated.connect(lambda href: self.open_page(href.split(":", 1)[1]))
        self.statusBar().addPermanentWidget(self.library_state)
        self._show_library_state()
        self.jobs_button = QPushButton("Jobs", clicked=self.show_jobs)
        self.jobs_button.setFlat(True)
        self.statusBar().addPermanentWidget(self.jobs_button)
        self.reload()
        saved = Settings(self.conn).get("window_geometry")      # size, place, monitor, maximized
        if saved:
            from PySide6.QtCore import QByteArray
            self.restoreGeometry(QByteArray.fromHex(saved.encode("ascii")))
        start = Settings(self.conn).get("start_page")
        start = Settings(self.conn).get("last_page") if start == "last" else start
        if start and start != "Library" and start in self._nav:
            self._later(0, lambda: self.open_page(start))

        # The jobs engine: one background thread working through queued jobs.
        self.runner = JobRunner()
        self._runner_thread = QThread(self)
        self.runner.moveToThread(self._runner_thread)
        self._runner_thread.started.connect(self.runner.run)
        self.runner.changed.connect(self._jobs_changed)
        self._runner_thread.start()
        self._jobs_dialog = None
        self._jobs_timer = QTimer(self, interval=2000, timeout=self._update_jobs_button)
        self._jobs_timer.start()

        # While a long pass runs, fold its new thumbnails into the grid now
        # and then rather than only at the end.
        self._refresh_timer = QTimer(self, interval=15000, timeout=self.reload_later)
        if ratings.pending_count(self.conn):
            self._xmp_timer.start()   # changes from a previous session not yet written
        self._later(3000, self._daily_backup)
        self._later(4000, self._resume_imports)
        # A backup drive being plugged in starts its backup (sets with auto_on_connect).
        from lunelis.backups.core import connected_drives
        self._drives = set(connected_drives())
        self._drive_timer = QTimer(self, interval=5000, timeout=self._check_backup_drives)
        self._drive_timer.start()
        # Packaged builds: look for a new version once a day, quietly.
        self._later(8000, self._startup_update_check)
        # darktable plugin: pick up its rating changes, hand it ours (cheap when idle).
        self._dt_state: dict = {}
        self._dt_timer = QTimer(self, interval=20_000, timeout=self._darktable_tick)
        self._dt_timer.start()
        # Which sources answer right now (a sleeping NAS, an unplugged drive): their
        # photos stay browsable from the thumbnails, marked OFFLINE.
        self.offline_roots: dict[int, str] = {}
        self._reach_timer = QTimer(self, interval=REACH_CHECK_MS, timeout=self.check_sources)
        self._reach_timer.start()
        self._later(3000, self.check_sources)
        # Regular file checks: a little of the library re-read each week, in idle time.
        self._integrity_timer = QTimer(self, interval=3_600_000, timeout=self._integrity_tick)
        self._integrity_timer.start()
        self._later(60_000, self._integrity_tick)

        # Tray + memory-card watching (the real app only - not tests/scripts).
        self.tray = None
        self.cards = None
        self._auto_action = None
        if tray:
            self.set_tray_enabled(Settings(self.conn).get("tray_enabled"))

    # --- building ------------------------------------------------------------

    def _build_menu(self) -> None:
        menu = self.menuBar().addMenu("&Library")
        a = QAction("&Ask your library…", self, shortcut="Ctrl+Shift+F", triggered=self.open_ask)
        menu.addAction(a)
        self.addAction(a)
        self.add_action = QAction("&Add folder…", self, shortcut="Ctrl+O", triggered=self.add_folder)
        self.rescan_action = QAction("&Rescan all folders", self, shortcut="F5", triggered=self.rescan_all)
        self.cancel_action = QAction("&Stop", self, shortcut="Ctrl+.", enabled=False,
                                     triggered=self.cancel_scan)
        menu.addActions([self.add_action, self.rescan_action, self.cancel_action])
        menu.addAction(QAction("&Import from a card or folder…", self, shortcut="Ctrl+I",
                               triggered=lambda: self.open_page("Import")))
        menu.addSeparator()
        menu.addAction(QAction("Find &duplicates…", self, triggered=lambda: self.new_job("duplicates")))
        menu.addAction(QAction("&Hash everything (integrity baseline)…", self,
                               triggered=lambda: self.new_job("full_hash")))
        menu.addAction(QAction("Check &integrity against the baseline…", self,
                               triggered=lambda: self.new_job("integrity")))
        menu.addAction(QAction("&Jobs…", self, shortcut="Ctrl+J", triggered=self.show_jobs))
        menu.addSeparator()
        menu.addAction(QAction("&Settings…", self, shortcut="Ctrl+,",
                               triggered=lambda: self.open_page("Settings")))
        menu.addAction(QAction("&Move the Archive to a drive…", self, triggered=self.move_archive))
        self._sidebar_action = QAction("Sidebar: &icons only", self, shortcut="Ctrl+B", checkable=True,
                                       checked=bool(Settings(self.conn).get("sidebar_compact")),
                                       triggered=lambda on: self.toggle_sidebar(on))
        menu.addAction(self._sidebar_action)

        self.photo_menu = self.menuBar().addMenu("&Photo")
        stars = self.photo_menu.addMenu("&Rating")
        for n in range(6):
            a = QAction("No stars" if n == 0 else "★" * n, self, shortcut=str(n),
                        triggered=lambda _=False, n=n: self.rate(stars=n))
            stars.addAction(a)
            self.addAction(a)                        # shortcut works without opening the menu
        labels = self.photo_menu.addMenu("&Label")
        for name in LABELS:
            a = QAction(name, self, triggered=lambda _=False, name=name: self.rate(label=name))
            if LABEL_KEYS[name]:
                a.setShortcut(LABEL_KEYS[name])
            labels.addAction(a)
            self.addAction(a)
        labels.addSeparator()
        labels.addAction(QAction("No label", self, triggered=lambda: self.rate(label=None)))
        ev = self.photo_menu.addMenu("&Event")
        a = QAction("&New event from selection…", self, shortcut="Ctrl+E", triggered=self.new_event)
        ev.addAction(a)
        self.addAction(a)
        ev.addAction(QAction("&Add to an event…", self, triggered=self.add_to_event))
        ev.addAction(QAction("&Remove from its event", self, triggered=self.remove_from_event))
        al = self.photo_menu.addMenu("&Album")
        a = QAction("&Add to album…", self, shortcut="Ctrl+Shift+A", triggered=self.add_to_album)
        al.addAction(a)
        self.addAction(a)
        al.addAction(QAction("&New album from selection…", self, triggered=self.new_album))
        self.remove_album_action = QAction("&Remove from this album", self, triggered=self.remove_from_album)
        al.addAction(self.remove_album_action)
        al.aboutToShow.connect(lambda: self.remove_album_action.setEnabled(self.filter.album_id is not None))
        a = self.undo_action = QAction("&Undo", self, shortcut="Ctrl+Z", triggered=self.undo)
        self.photo_menu.insertAction(self.photo_menu.actions()[0], a)
        self.addAction(a)
        b = self.redo_action = QAction("&Redo", self, triggered=self.redo)
        b.setShortcuts(["Ctrl+Shift+Z", "Ctrl+Y"])
        self.photo_menu.insertAction(self.photo_menu.actions()[1], b)
        self.photo_menu.insertSeparator(self.photo_menu.actions()[2])
        self.addAction(b)
        self.photo_menu.aboutToShow.connect(self._update_undo_actions)
        a = QAction("C&ull full screen…", self, shortcut="Ctrl+K", triggered=self.cull)
        self.photo_menu.addAction(a)
        self.addAction(a)
        self.similar_action = QAction("Find &similar photos", self, shortcut="Ctrl+Alt+F", triggered=self.find_similar)
        self.photo_menu.addAction(self.similar_action)
        self.addAction(self.similar_action)
        self.photo_menu.aboutToShow.connect(lambda: self.similar_action.setText(
            "More &like these" if len(self.grid.selected) > 1 else "Find &similar photos"))
        self.archive_action = QAction("Arc&hive", self, shortcut="Ctrl+Shift+H", triggered=self.toggle_archive)
        self.photo_menu.addAction(self.archive_action)
        self.addAction(self.archive_action)
        self.photo_menu.aboutToShow.connect(self._update_archive_action)
        help_menu = self.menuBar().addMenu("&Help")
        a = QAction("&Keyboard shortcuts", self, shortcut="?", triggered=self.show_shortcuts)
        help_menu.addAction(a)
        self.addAction(a)
        help_menu.addAction(QAction("&About Lunelis", self, triggered=self.about))
        help_menu.addAction(QAction("&Open the data folder", self,
                                    triggered=lambda: os.startfile(str(paths.DATA_DIR))))
        help_menu.addAction(QAction("Open the &log folder", self, triggered=self._open_logs))
        help_menu.addSeparator()
        help_menu.addAction(QAction("&Report a problem…", self, triggered=self.report_problem))
        st = self.photo_menu.addMenu("&Stack")
        a = QAction("&Open / close stack", self, shortcut="S", triggered=self.toggle_stack)
        st.addAction(a)
        self.addAction(a)
        st.addAction(QAction("Make this the stack &cover", self, triggered=self.set_stack_cover))
        st.addAction(QAction("&Unstack (show every frame, for good)", self, triggered=self.unstack))
        a = QAction("E&xport…", self, shortcut="Ctrl+Shift+E", triggered=self.export_photos)
        self.photo_menu.addAction(a)
        self.addAction(a)
        a = QAction("&Find…", self, shortcut="Ctrl+F", triggered=self.focus_search)
        self.addAction(a)
        tg = self.photo_menu.addMenu("&Tags")
        a = QAction("&Tag photos…", self, shortcut="Ctrl+T", triggered=self.tag_photos)
        tg.addAction(a)
        self.addAction(a)
        tg.addAction(QAction("&Manage tags", self, triggered=lambda: self.open_page("Tags")))
        mg = self.photo_menu.addMenu("&Merge")
        mg.addAction(QAction("&HDR…", self, triggered=lambda: self.merge_photos("hdr")))
        mg.addAction(QAction("&Panorama…", self, triggered=lambda: self.merge_photos("panorama")))
        ed = self.photo_menu.addMenu("E&dit")
        for text, key, slot in (("&Copy edit settings", "Ctrl+Shift+C", self.copy_edit),
                                ("&Paste edit settings", "Ctrl+Shift+V", self.paste_edit),
                                ("&Reset edits…", None, self.reset_edits)):
            a = QAction(text, self, triggered=slot)
            if key:
                a.setShortcut(key)
            ed.addAction(a)
            self.addAction(a)
        ed.addSeparator()
        ed.addAction(QAction("&Open in the Edit page", self, triggered=lambda: self.open_page("Edit")))
        flags = self.photo_menu.addMenu("&Flag")
        for text, key, flag in (("Pick", "P", "pick"), ("Reject", "X", "reject"), ("Unflag", "U", None)):
            a = QAction(text, self, shortcut=key, triggered=lambda _=False, f=flag: self.rate(flag=f))
            flags.addAction(a)
            self.addAction(a)

    def _build_sidebar(self) -> QWidget:
        from PySide6.QtGui import QPixmap
        from lunelis.settings import Settings
        side = QWidget(objectName="Sidebar")
        side.setFixedWidth(SIDEBAR_WIDE)
        self.sidebar = side
        self._sidebar_compact = bool(Settings(self.conn).get("sidebar_compact"))
        self._nav_heads: list[QPushButton] = []
        self._nav_rules: list[QFrame] = []
        v = QVBoxLayout(side)
        self._side_layout = v
        v.setContentsMargins(16, 20, 16, 16)
        v.setSpacing(2)
        brand = QHBoxLayout()
        brand.setSpacing(10)
        mark = QLabel()
        pix = QPixmap(str(paths.ASSETS / "icons" / "app_icon.png"))
        if not pix.isNull():
            ratio = self.devicePixelRatioF() or 1.0
            pix = pix.scaled(int(36 * ratio), int(36 * ratio), Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
            pix.setDevicePixelRatio(ratio)
            mark.setPixmap(pix)
        brand.addWidget(mark)
        self._brand_name = QLabel("Lunelis", objectName="AppName")
        brand.addWidget(self._brand_name)
        brand.addStretch(1)
        v.addLayout(brand)
        v.addSpacing(8)
        # The pages scroll on short screens (1366 x 768 at 125 % is 614 px tall).
        nav_box = QWidget(objectName="SidebarNav")
        nv = QVBoxLayout(nav_box)
        nv.setContentsMargins(0, 0, 0, 0)
        nv.setSpacing(2)
        nav_scroll = QScrollArea(objectName="SidebarScroll", widgetResizable=True)
        nav_scroll.setFrameShape(QFrame.Shape.NoFrame)
        nav_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        nav_scroll.setWidget(nav_box)
        v.addWidget(nav_scroll, 1)
        group = QButtonGroup(side)
        self._nav: dict[str, QPushButton] = {}
        for section, pages in NAV:
            rule = QFrame(objectName="NavRule")             # stands in for the heading when compact
            rule.setFixedHeight(1)
            nv.addSpacing(4)
            nv.addWidget(rule)
            self._nav_rules.append(rule)
            head = QPushButton(objectName="NavSection")
            head.setCursor(Qt.CursorShape.PointingHandCursor)
            nv.addWidget(head)
            self._nav_heads.append(head)
            items = []
            for label in pages:
                b = QPushButton(label, objectName="NavItem", checkable=True)
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.setIconSize(QSize(20, 20))
                b.setChecked(label == "Library")
                b.clicked.connect(lambda _=False, name=label: self.show_page(name))
                if label == "Albums":
                    self._spring = SpringLoad(b, lambda: self.open_page("Albums"))
                group.addButton(b)
                nv.addWidget(b)
                items.append(b)
                self._nav[label] = b

            def toggle(_=False, section=section, head=head, items=items, flip=True):
                s = Settings(self.conn)
                shut = set(s.get("sidebar_collapsed"))
                if flip:
                    shut ^= {section}
                    s.set("sidebar_collapsed", sorted(shut))
                closed = section in shut
                head.setText(("▸  " if closed else "▾  ") + section.upper().replace("&", "&&"))
                for it in items:
                    # A collapsed section still shows the page you're on; the
                    # icon-only sidebar always shows every page.
                    it.setVisible(self._sidebar_compact or not closed or it.isChecked())
            head.clicked.connect(toggle)
            toggle(flip=False)
            self._section_toggles = getattr(self, "_section_toggles", []) + [toggle]
        nv.addStretch(1)
        self.collapse_b = QPushButton(objectName="NavItem")
        self.collapse_b.setCursor(Qt.CursorShape.PointingHandCursor)
        self.collapse_b.setIconSize(QSize(20, 20))
        self.collapse_b.clicked.connect(lambda: self.toggle_sidebar(not self._sidebar_compact))
        v.addWidget(self.collapse_b)
        v.addSpacing(6)
        for label in BOTTOM_NAV:
            b = QPushButton(label, objectName="NavBottom", checkable=True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setIconSize(QSize(20, 20))
            b.clicked.connect(lambda _=False, name=label: self.show_page(name))
            group.addButton(b)
            v.addWidget(b)
            self._nav[label] = b
        self.footer = QLabel(objectName="SidebarFooter")
        self.footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.footer)
        self.set_sidebar_compact(self._sidebar_compact, animate=False, save=False)
        return side

    # --- the sidebar: full, or icons only ---------------------------------------------------

    def toggle_sidebar(self, compact: bool) -> None:
        """You chose (Collapse, Ctrl+B). Opening it on a narrow window holds
        until the window is wide again - the automatic fold won't fight you."""
        if not compact and self.width() < AUTO_SIDEBAR_WIDTH:
            self._auto_held = True
        self.set_sidebar_compact(compact)

    def _auto_sidebar(self) -> None:
        if not hasattr(self, "sidebar") or getattr(self, "_closed", False):
            return
        s = Settings(self.conn)
        narrow = self.width() < AUTO_SIDEBAR_WIDTH
        # The status strip: on a narrow window the scan bar is shorter and the
        # step text doesn't hold room for the longest step, so the message fits.
        self.scan_bar.setFixedWidth(90 if narrow else 180)
        self.scan_step.setMinimumWidth(0 if narrow else getattr(self, "_scan_step_min", 0))
        if not narrow:
            self._auto_held = False
        want = bool(s.get("sidebar_compact")) or (
            bool(s.get("sidebar_auto")) and narrow and not getattr(self, "_auto_held", False))
        if want != self._sidebar_compact:
            self.set_sidebar_compact(want, animate=self.isVisible(), save=False)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if not hasattr(self, "_auto_timer"):
            self._auto_timer = QTimer(self, singleShot=True, interval=120, timeout=self._auto_sidebar)
        self._auto_timer.start()

    def show_shortcuts(self) -> None:
        from lunelis.ui.shortcuts import ShortcutSheet
        page = self.pages.currentWidget()
        if page is self.edit_page or (page is self.detail and self.detail.editing):
            screen = "Edit panel"
        elif page is self.detail:
            screen = "Photo view"
        else:
            screen = "Library"
        ShortcutSheet(self, screen).exec()

    def set_sidebar_compact(self, compact: bool, animate: bool = True, save: bool = True) -> None:
        """Icons only (SIDEBAR_NARROW wide, names in tooltips) or full width with names."""
        self._sidebar_compact = compact
        if save:
            Settings(self.conn).set("sidebar_compact", compact)
        side = self.sidebar
        side.setProperty("compact", compact)
        for w in [side, *side.findChildren(QWidget)]:
            w.style().unpolish(w)
            w.style().polish(w)
        self._side_layout.setContentsMargins(*((10, 20, 10, 16) if compact else (16, 20, 16, 16)))
        self._brand_name.setVisible(not compact)
        self.footer.setVisible(not compact)
        for head in self._nav_heads:
            head.setVisible(not compact)
        for rule in self._nav_rules:
            rule.setVisible(compact)
        for name, b in self._nav.items():
            b.setText("" if compact else "  " + name)       # a little air after the icon
            b.setToolTip(name if compact else "")
        self.collapse_b.setText("" if compact else "  Collapse")
        self.collapse_b.setToolTip("Show page names (Ctrl+B)" if compact else "Icons only (Ctrl+B)")
        for refold in getattr(self, "_section_toggles", []):
            refold(flip=False)
        if hasattr(self, "_sidebar_action"):
            self._sidebar_action.setChecked(compact)
        self._nav_icons()
        target = SIDEBAR_NARROW if compact else SIDEBAR_WIDE
        if not animate or not self.isVisible():
            side.setFixedWidth(target)
            return
        from PySide6.QtCore import QEasingCurve, QVariantAnimation
        anim = QVariantAnimation(self, startValue=side.width(), endValue=target, duration=160)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.valueChanged.connect(lambda w: side.setFixedWidth(int(w)))
        anim.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)
        self._sidebar_anim = anim

    def _nav_icons(self) -> None:
        """Page icons in the theme's sidebar colours (again after a theme switch)."""
        from lunelis.ui import icons, theme
        t = theme.current()
        for name, b in getattr(self, "_nav", {}).items():
            b.setIcon(icons.icon(icons.NAV_ICONS.get(name, "library"), t.sidebar_muted, t.sidebar_text))
        if hasattr(self, "collapse_b"):
            self.collapse_b.setIcon(icons.icon("expand" if self._sidebar_compact else "collapse",
                                               t.sidebar_footer, t.sidebar_text))

    def apply_theme(self, choice: str | None = None) -> None:
        # Resolve the Appearance setting and restyle everything, live.
        from lunelis.settings import Settings
        from lunelis.ui import theme
        t = theme.resolve(choice or Settings(self.conn).get("theme"))
        theme.set_current(t)
        app = QApplication.instance()
        if app is not None:
            apply_palette(app, t)
            app.setStyleSheet(stylesheet(t))
            if not getattr(self, "_follows_windows", False):
                self._follows_windows = True
                app.styleHints().colorSchemeChanged.connect(self._windows_scheme_changed)
        if hasattr(self, "grid"):
            self.grid.viewport().update()
        self._nav_icons()

    def _windows_scheme_changed(self, *_):
        from lunelis.settings import Settings
        if Settings(self.conn).get("theme") == "system":
            self.apply_theme()

    def _build_toolbar(self) -> QWidget:
        bar = QWidget(objectName="Toolbar")
        bar.setFixedHeight(64)
        h = QHBoxLayout(bar)
        h.setContentsMargins(24, 0, 24, 0)
        h.setSpacing(16)
        self.search = QLineEdit(objectName="Search", placeholderText="Search: names, tags, places, cameras, 2024…")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(160)                 # shrinks on small or scaled screens
        self.search.setToolTip(
            "Every word has to match: file and folder names, tags, camera, lens, events, albums.\n"
            "Also: 2024, june 2024, raw, video, edited, picks, 4 stars, untagged,\n"
            "tag:beach  camera:a7rv  lens:24-105  folder:vegas  -word to leave out.  (Ctrl+F)")
        self._search_timer = QTimer(self, singleShot=True, interval=250, timeout=self._search_changed)
        self.search.textChanged.connect(lambda _t: self._search_timer.start())
        from PySide6.QtGui import QKeySequence, QShortcut
        QShortcut(QKeySequence("Escape"), self.search, activated=self.search.clear,
                  context=Qt.ShortcutContext.WidgetShortcut)
        self._search_completer_ready = False
        h.addWidget(self.search)
        h.addStretch(1)
        h.addWidget(QLabel("Sort:", objectName="ToolLabel"))
        from lunelis.settings import Settings
        s = Settings(self.conn)
        self.sort = QComboBox(objectName="Sort")
        for key, (label, _) in SORTS.items():
            self.sort.addItem(label, key)
        self.sort.setCurrentIndex(max(0, self.sort.findData(s.get("grid_default_sort"))))
        self.sort.currentIndexChanged.connect(self._sort_changed)
        # Sized for a typical choice, not the longest one, so the Top bar can shrink.
        self.sort.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.sort.setMinimumContentsLength(14)
        h.addWidget(self.sort)
        h.addWidget(_divider())
        h.addWidget(QLabel("Grid size", objectName="ToolLabel"))
        self.size_slider = QSlider(Qt.Orientation.Horizontal, minimum=MIN_TILE, maximum=MAX_TILE,
                                   value=max(MIN_TILE, min(MAX_TILE, s.get("grid_default_size") or DEFAULT_TILE)))
        self.size_slider.setFixedWidth(120)
        # Remembered however it changes (dragging, Ctrl+wheel, keys) - once it settles.
        self._size_save = QTimer(self, singleShot=True, interval=800, timeout=lambda: (
            not getattr(self, "_closed", False)
            and Settings(self.conn).set("grid_default_size", self.size_slider.value())))
        self.size_slider.valueChanged.connect(lambda _v: self._size_save.start())
        self.size_slider.valueChanged.connect(lambda v: self.grid.set_target_tile(v))
        h.addWidget(self.size_slider)
        return bar

    def _build_filter_bar(self) -> QWidget:
        bar = QWidget(objectName="FilterBar")
        bar.setFixedHeight(44)
        h = QHBoxLayout(bar)
        h.setContentsMargins(24, 0, 24, 0)
        h.setSpacing(8)
        h.addWidget(QLabel("Filters:", objectName="FilterLabel"))
        h.addWidget(self._filter_button("Rating", [
            ("Any rating", 0), *((("★" * n) + ("+" if n < 5 else ""), n) for n in range(1, 6)),
            ("Unrated", UNRATED)], "min_stars"))
        h.addWidget(self._filter_button("Label", [("Any label", None), *((n, n) for n in LABELS)], "label"))
        h.addWidget(self._filter_button("Flag", [("Any flag", None), ("Picks", "pick"),
                                                 ("Rejects", "reject")], "flag"))
        h.addWidget(self._filter_button("Backup", [("Any", None), ("Backed up", "ok"),
                                                   ("Not backed up", "none")], "backup"))
        from lunelis.ui.tag_editor import TagFilter
        self.tag_filter = TagFilter(self.conn)
        self.tag_filter.chosen.connect(self.show_tag)
        h.addWidget(self.tag_filter)
        # Active filters as chips, in a strip that gives way on narrow windows
        # (and scrolls with the wheel) instead of widening the window.
        chips_box = QWidget(objectName="ChipBox")
        self.chips = QHBoxLayout(chips_box)
        self.chips.setContentsMargins(0, 0, 0, 0)
        self.chips.setSpacing(6)
        self.chips.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        chip_strip = QScrollArea(objectName="ChipStrip", widgetResizable=True)
        chip_strip.setFrameShape(QFrame.Shape.NoFrame)
        chip_strip.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        chip_strip.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        chip_strip.setFixedHeight(34)
        chip_strip.setMinimumWidth(0)
        chip_strip.setWidget(chips_box)
        h.addWidget(chip_strip, 1)
        self.clear_all = QPushButton("Clear all", objectName="ClearAll",
                                     clicked=lambda: self.set_filter(Filter()))
        self.clear_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_all.hide()
        h.addWidget(self.clear_all)
        self.stack_cb = QCheckBox("Stack bursts")
        self.stack_cb.setToolTip("Show each burst of shots as one photo with a frame count (S opens a stack)")
        self.stack_cb.setChecked(Settings(self.conn).get("stack_bursts"))
        self.stack_cb.toggled.connect(self._stack_toggled)
        h.addWidget(self.stack_cb)
        h.addSpacing(12)
        self.count = QLabel(objectName="Count")
        h.addWidget(self.count)
        return bar

    def _stack_toggled(self, on: bool) -> None:
        s = Settings(self.conn)
        if on == s.get("stack_bursts"):
            return
        s.set("stack_bursts", on)
        if on and not stacks.stats(self.conn)[0]:
            stacks.rebuild_from_settings(self.conn)
        self.reload()

    # --- data ----------------------------------------------------------------

    def _view_defaults_changed(self) -> None:
        from lunelis.settings import Settings
        s = Settings(self.conn)
        self.detail.canvas.wheel_mode = s.get("wheel_action")
        self.sort.blockSignals(True)
        self.sort.setCurrentIndex(max(0, self.sort.findData(s.get("grid_default_sort"))))
        self.sort.blockSignals(False)
        self.size_slider.setValue(s.get("grid_default_size"))
        self.grid.hover_enabled = s.get("hover_info")
        self.reload()

    def _sort_changed(self, _=None) -> None:
        from lunelis.settings import Settings
        Settings(self.conn).set("grid_default_sort", self.sort.currentData())
        self.reload()

    def _view_settings(self):
        from dataclasses import replace as _replace
        from lunelis.settings import Settings
        hide = not Settings(self.conn).get("show_videos")
        pairs_on = bool(Settings(self.conn).get("pair_raw_jpeg"))
        self.index.collapse = Settings(self.conn).get("stack_bursts")
        if hasattr(self, "stack_cb") and self.stack_cb.isChecked() != self.index.collapse:
            self.stack_cb.blockSignals(True)
            self.stack_cb.setChecked(self.index.collapse)
            self.stack_cb.blockSignals(False)
        return self.sort.currentData(), _replace(self.filter, hide_videos=hide, hide_pairs=pairs_on)

    def _catalog_mark(self) -> tuple[int, int]:
        """Changes since the last load: another connection's commits (the scan,
        jobs) move data_version, this connection's own writes total_changes."""
        return self.conn.execute("PRAGMA data_version").fetchone()[0], self.conn.total_changes

    def reload(self) -> None:
        """Load the grid now (a filter, sort or edit the user is waiting on)."""
        self._index_gen = getattr(self, "_index_gen", 0) + 1     # an older background load is stale
        self._loaded_mark = self._catalog_mark()
        sort_key, filt = self._view_settings()
        self.index.load(self.conn, sort_key, filt)
        self._show_index(catalog_stats(self.conn))

    def reload_later(self, only_if_changed: bool = False) -> None:
        """Load the grid on a worker (the scan's periodic refresh, coming back
        to the Library page): ~0.3 s of query on a 159k library that the window
        shouldn't freeze for. The grid keeps showing the old rows until then."""
        if only_if_changed and getattr(self, "_loaded_mark", None) == self._catalog_mark():
            return
        mark = self._catalog_mark()
        gen = getattr(self, "_index_gen", 0)
        sort_key, filt = self._view_settings()

        def load(conn):
            import time
            t = time.perf_counter()
            rows = LibraryIndex.query(conn, sort_key, filt)
            return rows, time.perf_counter() - t, catalog_stats(conn)

        def show(result) -> None:
            if gen != getattr(self, "_index_gen", 0) or (sort_key, filt) != self._view_settings():
                return                                   # the user changed the view meanwhile
            rows, seconds, stats = result
            self.index.sort_key, self.index.filter = sort_key, filt
            self.index.apply(rows, seconds)
            self._loaded_mark = mark
            self._show_index(stats)
        self._bg().run("index", load, show)

    def _show_index(self, s) -> None:
        self.grid.set_timeline(self.sort.currentData() in ("date_desc", "date_asc"))
        self.thumbs.reset_failed()
        if len(self.index):
            self.grid.empty_text = ""
        elif self.filter.query:
            self.grid.empty_text = f"No photos match “{self.filter.query}”"
        elif self.filter.active():
            self.grid.empty_text = "No photos match these filters"
        else:
            self.grid.empty_text = "No folders yet — Library ▸ Add folder… (Ctrl+O)"
        self.grid.set_index(self.index)
        self.footer.setText(f"{s['bytes'] / 1e12:,.1f} TB indexed\n{s['files'] - s['missing'] - s['excluded']:,} photos & videos")
        self._update_count(len(self.grid.selected))

    # --- import, cards & tray ----------------------------------------------------

    def open_page(self, name: str) -> None:
        b = self._nav.get(name)
        if b is not None:
            b.setChecked(True)
            b.setVisible(True)
        self.show_page(name)
        # Bring the window up (from the tray, or minimized) WITHOUT changing a
        # maximized/full-screen window back to normal size - showNormal() did.
        if not self.isVisible():
            self.show()
        if self.isMinimized():
            self.setWindowState((self.windowState() & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive)
        self.raise_()
        self.activateWindow()

    def _setup_tray(self) -> None:
        from PySide6.QtWidgets import QSystemTrayIcon
        from lunelis.ui.tray import CardWatcher, autostart_enabled, set_autostart
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip("Lunelis")
        m = QMenu()
        m.addAction("Open Lunelis", lambda: self.open_page("Library"))
        m.addAction("Import…", lambda: self.open_page("Import"))
        auto = QAction("Start with Windows", self, checkable=True, checked=autostart_enabled())
        auto.toggled.connect(set_autostart)
        auto.toggled.connect(lambda _: self.settings_page.refresh())
        self._auto_action = auto
        m.addAction(auto)
        m.addAction("Settings…", lambda: self.open_page("Settings"))
        m.addSeparator()
        m.addAction("Quit Lunelis", self.quit_app)
        self.tray.setContextMenu(m)
        self.tray.activated.connect(lambda reason: self.open_page("Library")
                                    if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self._tray_card = None
        self.tray.messageClicked.connect(self._tray_message_clicked)
        self.tray.show()
        self.cards = CardWatcher(self)
        self.cards.inserted.connect(self._card_inserted)
        self.cards.stick_inserted.connect(self._stick_inserted)
        self.cards.removed.connect(lambda d: self.importer.update_cards())

    def _open_logs(self) -> None:
        from lunelis import log
        log.log_dir().mkdir(parents=True, exist_ok=True)
        os.startfile(str(log.log_dir()))

    def report_problem(self) -> None:
        # Everything needed to look into a problem, ready to copy.
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QPlainTextEdit
        from lunelis import log
        dlg = QDialog(self)
        dlg.setWindowTitle("Report a problem")
        dlg.resize(760, 560)
        v = QVBoxLayout(dlg)
        intro = QLabel("Copy this and send it along with what you were doing when it happened (a GitHub issue "
                       "or a message). It contains versions, your folders and the recent log - no photos.")
        intro.setWordWrap(True)
        v.addWidget(intro)
        text = QPlainTextEdit(log.diagnostics(self.conn))
        text.setReadOnly(True)
        v.addWidget(text, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        copy = buttons.addButton("Copy", QDialogButtonBox.ButtonRole.ActionRole)
        copy.clicked.connect(lambda: (QApplication.clipboard().setText(text.toPlainText()),
                                      copy.setText("Copied")))
        folder = buttons.addButton("Open the log folder", QDialogButtonBox.ButtonRole.ActionRole)
        folder.clicked.connect(self._open_logs)
        buttons.rejected.connect(dlg.reject)
        v.addWidget(buttons)
        dlg.exec()

    def _crashed(self, exc_type, exc) -> None:
        # An unexpected error on the GUI thread: logged already; say so once
        # per session instead of disappearing silently.
        if getattr(self, "_crash_shown", False):
            self.status.setText(f"Something went wrong ({exc_type.__name__}) - details are in the log")
            return
        self._crash_shown = True
        box = QMessageBox(QMessageBox.Icon.Warning, "Something went wrong",
                          f"Lunelis hit an unexpected error: {exc_type.__name__}: {exc}\n\nIt's been written to "
                          "the log. You can keep working; if something looks wrong, Help > Report a problem "
                          "collects what's needed to fix it.", parent=self)
        report = box.addButton("Report a problem…", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if box.clickedButton() is report:
            self.report_problem()

    def about(self) -> None:
        QMessageBox.about(
            self, "About Lunelis",
            f"<b>Lunelis {paths.version()}</b><br>A local, Windows-first photo library.<br><br>"
            f"Data folder: {paths.DATA_DIR}<br>"
            "Your photos are never changed; everything Lunelis keeps is in the data folder.")

    def set_tray_enabled(self, enabled: bool) -> None:
        """Tray on: closing the window keeps Lunelis running and watching for
        cards. Tray off: closing the window quits."""
        if not self._tray_allowed:
            return
        if enabled and self.tray is None:
            self._setup_tray()
        elif not enabled and self.tray is not None:
            self.tray.hide()
            self.tray.deleteLater()
            self.tray = None
            if self.cards is not None:
                self.cards._timer.stop()
                self.cards.deleteLater()
                self.cards = None
            self._auto_action = None
        app = QApplication.instance()
        if app is not None:
            app.setQuitOnLastWindowClosed(self.tray is None)

    def _autostart_changed(self, on: bool) -> None:
        if self._auto_action is not None:
            self._auto_action.blockSignals(True)
            self._auto_action.setChecked(on)
            self._auto_action.blockSignals(False)

    def restart(self) -> None:
        """Start a fresh Lunelis that waits for this one to exit (so the
        catalog is closed before a data move or restore touches it), then quit."""
        from PySide6.QtCore import QProcess
        program, args = paths.launch_command("--after", str(os.getpid()))
        if not QProcess.startDetached(program, args):
            QMessageBox.information(self, "Restart Lunelis",
                                    "Close and reopen Lunelis to finish - the change happens on the next start.")
            return
        self.quit_app()

    def _rescan_roots(self, root_ids: list) -> None:
        if self._thread is None:
            self.start(list(root_ids))
        else:
            self.status.setText("A scan is already running - press F5 afterwards to pick up the change.")

    def _startup_update_check(self) -> None:
        from datetime import datetime, timedelta, timezone
        from lunelis.settings import Settings
        s = Settings(self.conn)
        if not paths.FROZEN or not s.get("update_check"):
            return
        last = s.get("update_last_check")
        if last and datetime.now(tz=timezone.utc) - datetime.fromisoformat(last) < timedelta(hours=20):
            return
        page = self.settings_page
        page.check_updates()
        page._update_done = self._startup_checked

    def _startup_checked(self, result) -> None:
        from lunelis import updater
        from lunelis.settings import Settings
        self.settings_page._checked(result)
        if isinstance(result, Exception) or not updater.is_newer(result.version, paths.version()):
            return
        if Settings(self.conn).get("update_skip_version") == result.version:
            return
        self._update_found(result)

    def _update_found(self, rel) -> None:
        if getattr(self, "_update_button", None) is None:
            self._update_button = QPushButton(clicked=lambda: (self.open_page("Settings"),
                                                               self.settings_page.show_tab("Updates")))
            self._update_button.setObjectName("Primary")
            self.statusBar().addPermanentWidget(self._update_button)
        self._update_button.setText(f"Update to {rel.version}")
        self.status.setText(f"Lunelis {rel.version} is available - Settings > Updates")

    def _install_update(self, staged) -> None:
        from lunelis import updater
        try:
            updater.apply(staged)
        except Exception as e:
            QMessageBox.warning(self, "Update", f"Couldn't start the update: {e}")
            return
        self.quit_app()

    def _darktable_tick(self) -> None:
        from lunelis.darktable import bridge
        try:
            r = bridge.tick(self.conn, self._dt_state)
        except OSError:
            return                                    # exchange folder offline: try next time
        if r is not None and r.applied:
            self.status.setText(f"{r.applied:,} rating change(s) from darktable")
            self.grid.viewport().update()
            if self.pages.currentWidget() is self.grid:
                self.reload()
            self._xmp_timer.start()                   # into sidecars too

    def _job_queued(self) -> None:
        self.runner.poke()
        self._update_jobs_button()

    def _check_backup_drives(self) -> None:
        from lunelis.backups import core as backups
        now = backups.connected_drives()
        arrived = set(now) - self._drives
        self._drives = set(now)
        for serial in arrived:
            for sid in backups.sets_on_drive(self.conn, now[serial]):
                s = backups.get_set(self.conn, sid)
                if not s.options.get("auto_on_connect"):
                    continue
                backups.start(self.conn, sid)
                self._job_queued()
                text = f"Backup drive connected - backing up to {s.name}"
                self.status.setText(text)
                if self.tray is not None:
                    self.tray.showMessage("Lunelis", text)

    def _card_inserted(self, drive: str) -> None:
        self.importer.update_cards()
        serial, label = ingest.volume_info(drive)
        # An import that was waiting for THIS card carries on by itself.
        for imp, state, want in ingest.unfinished(self.conn):
            if want and want == serial and state == "waiting" and not self.importer.busy():
                self.conn.execute("UPDATE imports SET source = ? WHERE id = ?", (drive, imp))
                self.conn.commit()
                self.importer.resume(imp)
                return
        self._tray_card = drive
        if self.tray is None:
            return

        # Counting walks the whole card: on a worker. A card pulled meanwhile
        # just gets no message.
        def say(n: int) -> None:
            if drive in ingest.removable_drives_with_media():
                self.tray.showMessage("Memory card inserted",
                                      f"{label or 'Card'} ({drive.rstrip(chr(92))}): {n:,} photos and videos. "
                                      "Click to import.")
        self._bg().run(f"card {drive}", lambda: len(ingest.discover(drive)), say, db=False,
                       error=lambda e: logging.getLogger(__name__).info("card %s couldn't be read: %s", drive, e))

    def _stick_inserted(self, drive: str) -> None:
        """A USB stick (no camera folders): offered for import when it holds
        photos - counted on a worker, and only up to a point (a big drive)."""
        self.importer.update_cards()
        if self.tray is None:
            return
        _serial, label = ingest.volume_info(drive)

        def count() -> int:
            import time
            from lunelis.importers.formats import is_cataloged
            n, start = 0, time.monotonic()
            for _dirpath, dirs, files in os.walk(drive):
                dirs[:] = [d for d in dirs if not d.startswith((".", "$")) and d != "System Volume Information"]
                n += sum(1 for f in files if is_cataloged(f))
                if time.monotonic() - start > STICK_COUNT_SECONDS:
                    break
            return n

        def say(n: int) -> None:
            if n and drive in ingest.removable_drives():
                self._tray_card = drive
                self.tray.showMessage("USB drive inserted",
                                      f"{label or 'USB drive'} ({drive.rstrip(chr(92))}): {n:,} photos and videos. "
                                      "Click to import.")
        self._bg().run(f"stick {drive}", count, say, db=False,
                       error=lambda e: logging.getLogger(__name__).info("drive %s couldn't be read: %s", drive, e))

    def _bg(self):
        if not hasattr(self, "bg"):
            from lunelis.ui.background import Background
            self.bg = Background(self, self.conn)
        return self.bg

    def _tray_message_clicked(self) -> None:
        if self._tray_card:
            self.open_page("Import")
            self.importer.choose(self._tray_card)

    def _resume_imports(self) -> None:
        """At start-up: filing into the library needs no card, so an import that
        was interrupted after staging carries on; one still copying waits for
        its card."""
        # Whether a folder import's source is there can take seconds on a
        # sleeping NAS: decided on a worker, resumed here.
        def pick(conn):
            for imp, state, _ in ingest.unfinished(conn):
                pending = conn.execute("SELECT 1 FROM import_items WHERE import_id = ? AND state = 'pending'"
                                       " LIMIT 1", (imp,)).fetchone()
                source = conn.execute("SELECT source FROM imports WHERE id = ?", (imp,)).fetchone()[0]
                if not pending or os.path.isdir(source):
                    return imp
            return None

        def resume(imp) -> None:
            if imp is not None and not self.importer.busy():
                self.importer.resume(imp)
        self._bg().run("resume imports", pick, resume)

    def _after_import(self, destination: str) -> None:
        """Make sure the photos just filed show up: rescan the source that holds
        the destination (adding it as a source if it isn't inside one)."""
        norm = os.path.normcase(os.path.normpath(destination))
        root_id = None
        for rid, path in self.conn.execute("SELECT id, path FROM roots"):
            base = os.path.normcase(os.path.normpath(path))
            if norm == base or norm.startswith(base.rstrip("\\") + "\\"):
                root_id = rid
                break
        if root_id is None:
            try:
                root_id = add_root(self.conn, destination)
            except (RootOverlap, RootUnavailable) as e:
                self.status.setText(f"Imported, but couldn't add {destination} as a source: {e}")
                return
        self.start([root_id])

    # --- autopilot import --------------------------------------------------------------------

    def _autopilot_import(self, import_id: int) -> None:
        """Recorded now (so a restart carries on); it runs once the scan has catalogued the photos."""
        from lunelis.importing import autopilot
        autopilot.create_run(self.conn, import_id)

    def _run_autopilot(self) -> None:
        from lunelis.importing import autopilot
        if self._autopilot_thread is not None or getattr(self, "_quitting", False)                 or not autopilot.unfinished(self.conn):
            return
        self._autopilot_thread = QThread(self)
        self._autopilot = AutopilotWorker()
        self._autopilot.moveToThread(self._autopilot_thread)
        self._autopilot_thread.started.connect(self._autopilot.run)
        self._autopilot.progress.connect(lambda t: self.status.setText(f"Autopilot: {t}…"))
        self._autopilot.done.connect(self._autopilot_done)
        self._autopilot_thread.start()

    @unless_closed
    def _autopilot_done(self, reviewable: int) -> None:
        self._autopilot_thread.quit()
        self._autopilot_thread.wait()
        self._autopilot_thread.deleteLater()
        self._autopilot.deleteLater()
        self._autopilot_thread = None
        if reviewable:
            self.status.set_link("Autopilot: your shoot is ready - ", "review it", "Review your shoot")
            if self.tray is not None:
                self.tray.showMessage("Your shoot is ready", "Autopilot has sorted it out - review it in Lunelis.")
        self._start_thumbnails_if_needed()
        self.reload()

    def _shoot_reviewed(self) -> None:
        self._start_thumbnails_if_needed()
        self.reload()

    def _start_thumbnails_if_needed(self) -> None:
        """Applied edits cleared some thumbnails: a quick pass remakes just those."""
        ids = [r[0] for r in self.conn.execute(
            "SELECT id FROM files WHERE thumbnail_path IS NULL AND thumb_error IS NULL AND missing_since IS NULL")]
        if ids:
            from lunelis.raw.thumbnails import generate_pending
            generate_pending(self.conn, paths.THUMBNAIL_CACHE, only=ids[:500])

    def quit_app(self) -> None:
        self._quitting = True
        self.close()
        QApplication.instance().quit()

    # --- pages & jobs ----------------------------------------------------------

    # --- the photo detail view -------------------------------------------------

    def open_detail(self, file_id: int) -> None:
        pos = self.index.position(file_id)
        if pos >= 0 and self.index.tile(pos).stack_size:
            # A collapsed burst: open it, so the filmstrip steps through its frames.
            self.index.toggle_stack(self.index.stack_id(pos))
            self.grid.set_index(self.index)
            pos = self.index.position(file_id)
        if pos < 0:
            return
        self.toolbar.hide()
        self.filter_bar.hide()
        self.pages.setCurrentWidget(self.detail)
        self.detail.open(self.index, pos)

    # --- edits across a selection ---------------------------------------------------------

    def _edit_targets(self) -> list[int]:
        if self.pages.currentWidget() is self.detail and self.detail.info:
            return [self.detail.info.file_id]
        if self.pages.currentWidget() is self.edit_page and self.edit_page.current() is not None:
            return [self.edit_page.current()]
        return self._targets()

    def _editing_view(self):
        """The photo view whose edit is open, if any (the photo view, or the Edit page's)."""
        for page, view in ((self.detail, self.detail), (self.edit_page, self.edit_page.view)):
            if self.pages.currentWidget() is page and view.edit.active:
                return view
        return None

    def _copy_edit_of(self, fid: int) -> None:
        from lunelis.edit import store
        stack = store.get(self.conn, fid)
        if stack.is_identity():
            self.status.setText("That photo has no edits to copy")
            return
        self._edit_clipboard = stack
        self.status.setText("Copied the edit settings (crop and rotation stay with each photo)")

    def _paste_edit_to(self, ids: list[int]) -> None:
        from dataclasses import replace
        from lunelis.edit import store
        clip = getattr(self, "_edit_clipboard", None)
        if clip is None or not ids:
            self.status.setText("Copy a photo's edit settings first (Copy this edit, or Ctrl+Shift+C)")
            return
        if len(ids) > 1 and QMessageBox.question(
                self, "Paste to all", f"Paste the copied edit onto all {len(ids):,} photos? Each keeps its own "
                "crop and rotation; Reset all takes it off again.") != QMessageBox.StandardButton.Yes:
            return
        self._apply_edits(ids, lambda c, fid: replace(clip, geometry=store.get(c, fid).geometry),
                          f"Pasted the edit settings onto {len(ids):,} photo{'s' if len(ids) != 1 else ''}")

    def _reset_edits_of(self, ids: list[int]) -> None:
        from lunelis.edit import store
        from lunelis.edit.stack import Stack
        ids = [f for f in ids if f in store.edited_ids(self.conn, ids)]
        if not ids:
            self.status.setText("None of these photos are edited")
            return
        if QMessageBox.question(self, "Reset edits",
                                f"Take every edit off {len(ids):,} photo{'s' if len(ids) != 1 else ''} "
                                "(crop and rotation too)? The files themselves were never changed.") \
                != QMessageBox.StandardButton.Yes:
            return
        self._apply_edits(ids, lambda _c, _fid: Stack(), f"Reset {len(ids):,} photos to the original")

    def copy_edit(self) -> None:
        from lunelis.edit import store
        ids = self._edit_targets()
        if self.pages.currentWidget() is self.detail:
            self.detail.edit.save()
        if not ids:
            return
        fid = ids[0] if len(ids) == 1 or self.grid.current < 0 else self.index.file_id(self.grid.current)
        stack = store.get(self.conn, fid)
        if stack.is_identity():
            self.status.setText("That photo has no edits to copy")
            return
        self._edit_clipboard = stack
        self.status.setText("Copied the edit settings (crop and rotation stay with each photo)")

    def paste_edit(self) -> None:
        from dataclasses import replace
        from lunelis.edit import store
        clip = getattr(self, "_edit_clipboard", None)
        ids = self._edit_targets()
        if clip is None or not ids:
            self.status.setText("Copy a photo's edit settings first (Ctrl+Shift+C)")
            return
        self._apply_edits(ids, lambda c, fid: replace(clip, geometry=store.get(c, fid).geometry),
                          f"Pasted the edit settings onto {len(ids):,} photo{'s' if len(ids) != 1 else ''}")

    def reset_edits(self) -> None:
        from lunelis.edit import store
        from lunelis.edit.stack import Stack
        ids = [f for f in self._edit_targets() if f in store.edited_ids(self.conn, self._edit_targets())]
        if not ids:
            self.status.setText("None of the selected photos are edited")
            return
        if QMessageBox.question(self, "Reset edits",
                                f"Take every edit off {len(ids):,} photo{'s' if len(ids) != 1 else ''} "
                                "(crop and rotation too)? The files themselves were never changed.") \
                != QMessageBox.StandardButton.Yes:
            return
        self._apply_edits(ids, lambda _c, _fid: Stack(), f"Reset {len(ids):,} photos to the original")

    def _apply_edits(self, ids: list[int], stack_for, message: str) -> None:
        """Save `stack_for(conn, fid)` for each photo. Each save writes a sidecar
        (on the NAS, often), so more than a few photos are saved on a worker."""
        from lunelis.edit import store
        if self._bg().busy("edits"):
            self.status.setText("Still saving the last batch of edits - try again in a moment")
            return
        self._history().before(self.conn, message[0].lower() + message[1:], "edits", ids)
        editing = self._editing_view()
        if editing:
            editing.edit.finish()

        def save(conn) -> list[int]:
            changed = [fid for fid in ids if store.save(conn, fid, stack_for(conn, fid), commit=False)]
            conn.commit()
            return changed

        def saved(changed: list[int]) -> None:
            if editing and editing.info is not None:
                editing.edit.start(editing.info)
            self.status.setText(message)
            self.render_edits(changed)

        def failed(e) -> None:
            if editing and editing.info is not None:
                editing.edit.start(editing.info)
            self.status.setText(f"Saving the edits stopped: {e}")

        if len(ids) <= EDITS_INLINE:
            saved(save(self.conn))
            return
        self.status.setText(f"Saving edits for {len(ids):,} photos…")
        self._bg().run("edits", save, saved, error=failed)

    def export_photos(self, ids: list[int] | None = None) -> None:
        from lunelis.ui.export_dialog import ExportDialog, ExportWorker
        if getattr(self, "_export_thread", None) is not None:
            self.status.setText("An export is already running")
            return
        if self.pages.currentWidget() is self.detail:
            self.detail.edit.save()
        if self.pages.currentWidget() is self.edit_page:
            self.edit_page.view.edit.save()
        ids = list(ids) if ids else self._edit_targets()
        if not ids:
            QMessageBox.information(self, "Export", "Select some photos in the library first.")
            return
        ids = sorted(ids, key=lambda f: self.index.position(f))       # library order: {n} follows it
        dlg = ExportDialog(self.conn, len(ids), self)
        if dlg.exec() != ExportDialog.DialogCode.Accepted or dlg.options is None:
            return
        from PySide6.QtWidgets import QProgressDialog
        self._export_opts = dlg.options
        self._export_progress = QProgressDialog("Exporting…", "Cancel", 0, len(ids), self)
        self._export_progress.setWindowTitle("Export")
        self._export_progress.setMinimumDuration(0)
        self._export_thread = QThread(self)
        self._export = ExportWorker(ids, dlg.options)
        self._export.moveToThread(self._export_thread)
        self._export_thread.started.connect(self._export.run)
        self._export.progress.connect(self._export_step)
        self._export.done.connect(self._export_done)
        self._export_progress.canceled.connect(lambda: self._export.cancel())   # direct, see CLAUDE.md
        self._export_thread.start()

    def _export_step(self, i: int, n: int) -> None:
        self._export_progress.setValue(i)
        self._export_progress.setLabelText(f"Exporting {i:,} of {n:,}…")

    @unless_closed
    def _export_done(self, made: int, videos: int, errors: list) -> None:
        self._export_thread.quit()
        self._export_thread.wait()
        self._export_thread = None
        self._export_progress.close()
        folder = self._export_opts.folder
        msg = f"Exported {made:,} photo{'s' if made != 1 else ''} to {folder}."
        if videos:
            msg += f"\n{videos:,} video{'s were' if videos != 1 else ' was'} skipped."
        if errors:
            msg += f"\n\n{len(errors):,} couldn't be exported:\n" + "\n".join(errors[:8])
        box = QMessageBox(QMessageBox.Icon.Information, "Export", msg, parent=self)
        show = box.addButton("Show folder", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if box.clickedButton() is show:
            os.startfile(folder)

    # --- tags ----------------------------------------------------------------------------------

    # --- search ---------------------------------------------------------------------------------

    def focus_search(self) -> None:
        if self.pages.currentWidget() is not self.grid:
            self.open_page("Library")
        if not self._search_completer_ready:
            from PySide6.QtWidgets import QCompleter
            from lunelis import search
            c = QCompleter(search.suggestions(self.conn), self.search)
            c.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            c.setFilterMode(Qt.MatchFlag.MatchContains)
            self.search.setCompleter(c)
            self._search_completer_ready = True
        self.search.setFocus()
        self.search.selectAll()

    def _search_changed(self) -> None:
        from dataclasses import replace as _replace
        from lunelis import search
        text = self.search.text().strip() or None
        if text == self.filter.query:
            return
        if text:
            # Anything changed since the last scan: usually a handful of photos. A
            # big backlog (a scan that was stopped) is finished on a worker, and
            # the results are shown again when it's done.
            search.refresh(self.conn, limit=SEARCH_NOW_LIMIT)
            left = search.pending(self.conn)
            if left:
                self.status.setText(f"Search is catching up on {left:,} photos - the results update when it's done")
                self._bg().run("search index", lambda c: search.refresh(c),
                               lambda _n: self.filter.query and self.reload())
            if self.pages.currentWidget() is not self.grid:
                self.show_page("Library")
                b = self._nav.get("Library")
                if b is not None:
                    b.setChecked(True)
        self.set_filter(_replace(self.filter, query=text))

    def show_tag(self, name: str) -> None:
        from dataclasses import replace as _replace
        self.show_page("Library")
        b = self._nav.get("Library")
        if b is not None:
            b.setChecked(True)
        self.set_filter(_replace(self.filter, tag=name))

    def tag_photos(self) -> None:
        from lunelis.ui.tag_editor import TagDialog
        if self.pages.currentWidget() is self.detail and self.detail.info:
            ids = [self.detail.info.file_id]
        else:
            ids = self._with_pairs(self._targets())
        if not ids:
            QMessageBox.information(self, "Tags", "Select some photos in the library first.")
            return
        self._history().before(self.conn, f"tags on {len(ids):,} photo{'s' if len(ids) != 1 else ''}", "tags", ids)
        dlg = TagDialog(self.conn, ids, self)
        dlg.exec()
        if dlg.changed:
            self._tags_changed()
        else:
            self._history().forget_last()

    def _tags_changed(self) -> None:
        if self.pages.currentWidget() is self.detail:
            self.detail.refresh_info()
        if self.filter.tag:
            self.reload()

    def merge_photos(self, kind: str) -> None:
        from lunelis.ui.merge_dialog import MergeDialog, MergeWorker
        if getattr(self, "_merge_thread", None) is not None:
            self.status.setText("A merge is already running")
            return
        ids = sorted(self._targets(), key=lambda f: self.index.position(f))
        if not ids:
            QMessageBox.information(self, "Merge", "Select the photos to merge in the library first.")
            return
        first = self.conn.execute("SELECT filename FROM files WHERE id = ?", (ids[0],)).fetchone()[0]
        dlg = MergeDialog(self.conn, kind, len(ids), first, self)
        if dlg.exec() != MergeDialog.DialogCode.Accepted or dlg.options is None:
            return
        from PySide6.QtWidgets import QProgressDialog
        self._merge_progress = QProgressDialog("Starting…", "Cancel", 0, 0, self)
        self._merge_progress.setWindowTitle("HDR" if kind == "hdr" else "Panorama")
        self._merge_progress.setMinimumDuration(0)
        self._merge_thread = QThread(self)
        self._merge = MergeWorker(ids, dlg.options)
        self._merge.moveToThread(self._merge_thread)
        self._merge_thread.started.connect(self._merge.run)
        self._merge.progress.connect(self._merge_progress.setLabelText)
        self._merge.done.connect(self._merge_done)
        self._merge_progress.canceled.connect(lambda: self._merge.cancel())     # direct, see CLAUDE.md
        self._merge_thread.start()

    @unless_closed
    def _merge_done(self, file_id, path: str, error: str) -> None:
        self._merge_thread.quit()
        self._merge_thread.wait()
        self._merge_thread = None
        self._merge_progress.close()
        sid, self._noticed_pending = self._noticed_pending, None
        if error:
            if error != "cancelled":
                QMessageBox.warning(self, "Merge", error)
            return
        if sid is not None:
            from lunelis import noticed
            noticed.mark_built(self.conn, sid)
        if file_id is not None:
            from lunelis.raw.thumbnails import generate_pending
            generate_pending(self.conn, paths.THUMBNAIL_CACHE, only=[file_id])   # just the new file
            self.reload()
            pos = self.index.position(file_id)
            if pos >= 0:
                self.grid.current = self.grid.anchor = pos
                self.grid.selected = {file_id}
                self.grid.scroll_to(pos)
                self.grid.selection_changed.emit(1)
            self.status.setText(f"Saved {os.path.basename(path)} and added it to the library")
            return
        box = QMessageBox(QMessageBox.Icon.Information, "Merge",
                          f"Saved {path}.\n\nThat folder isn't one of your library's sources, so it isn't "
                          "in the library - add the folder (Library > Add folder) to see it here.", parent=self)
        show = box.addButton("Show folder", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if box.clickedButton() is show:
            os.startfile(os.path.dirname(path))

    def render_edits(self, ids: list[int]) -> None:
        """Proxies + thumbnails for many photos, in the background."""
        if not ids:
            return
        from lunelis.ui.develop import BatchOutputs
        if getattr(self, "_batch_thread", None) is not None:
            self._batch_queue = getattr(self, "_batch_queue", []) + list(ids)
            return
        self._batch_thread = QThread(self)
        self._batch = BatchOutputs(list(ids))
        self._batch.moveToThread(self._batch_thread)
        self._batch_thread.started.connect(self._batch.run)
        self._batch.one.connect(self._photo_edited)
        self._batch.progress.connect(self._batch_progress)
        self._batch.finished.connect(self._batch_finished)
        self._batch_thread.start()

    def _batch_progress(self, i: int, n: int) -> None:
        if n > 1:
            self.status.setText(f"Rendering edits… {i:,} / {n:,}")

    @unless_closed
    def _batch_finished(self) -> None:
        self._batch_thread.quit()
        self._batch_thread.wait()
        self._batch_thread = None
        queued, self._batch_queue = getattr(self, "_batch_queue", []), []
        if queued:
            self.render_edits(queued)

    def _finish_threads(self) -> None:
        """Never close the catalog under a running thread: destroying a QThread
        that's still running aborts the whole process (mid-copy, mid-move).
        Every timed wait above may have run out; wait here for what's left,
        saying so, for as long as it takes."""
        from PySide6.QtWidgets import QProgressDialog
        running = [t for t in self.findChildren(QThread) if t.isRunning()]
        if not running:
            return
        for t in running:
            t.quit()
        note = QProgressDialog("Finishing what's still running so nothing is left half done…", None, 0, 0, self)
        note.setWindowTitle("Closing Lunelis")
        note.setMinimumDuration(0)
        note.show()
        while any(t.isRunning() for t in running):
            QApplication.processEvents()
            for t in running:
                t.wait(100)
        note.close()

    def _photo_edited(self, file_id: int) -> None:
        if getattr(self, "_closed", False):
            return
        self.thumbs.reload(file_id)
        self.index.refresh_edits(self.conn, [file_id])
        self.grid.viewport().update()

    def close_detail(self) -> None:
        self.detail.set_editing(False)
        fid = self.detail.info.file_id if self.detail.info else None
        self.show_page("Library")
        if fid is not None:
            pos = self.index.position(fid)
            if pos >= 0:
                self.grid.current = self.grid.anchor = pos
                self.grid.selected = {fid}
                self.grid.scroll_to(pos)
                self.grid.selection_changed.emit(1)
        self.grid.setFocus()

    def _detail_moved(self, file_id: int) -> None:
        # The photo on screen is what the rating keys apply to.
        self.grid.selected = {file_id}
        self.grid.current = self.detail.pos

    def show_page(self, name: str) -> None:
        if name != "Edit" and self.pages.currentWidget() is getattr(self, "edit_page", None):
            self.edit_page.leave()                     # saves the photo being edited
        if name in getattr(self, "_nav", {}) and name != "Settings":
            try:
                Settings(self.conn).set("last_page", name)
            except Exception:
                pass
        for refold in getattr(self, "_section_toggles", []):
            refold(flip=False)                     # a folded section hides the page you just left
        library = name == "Library"
        self.toolbar.setVisible(library)
        self.filter_bar.setVisible(library)
        if library:
            self.pages.setCurrentWidget(self.grid)
            self.reload_later(only_if_changed=True)      # back from another page: only if something changed
        elif name == "Import":
            self.pages.setCurrentWidget(self.importer)
            self.importer.refresh()
        elif name == "Backups":
            self.pages.setCurrentWidget(self.backups_page)
            self.backups_page.refresh()
        elif name == "Edit":
            self.edit_page.set_library_context(list(self.grid.selected), self.filter, self.sort.currentData())
            if self.pages.currentWidget() is not self.edit_page:
                self.edit_page.choose_default()
            self.pages.setCurrentWidget(self.edit_page)
            self.edit_page.load()
        elif name == "Stats":
            self.pages.setCurrentWidget(self.stats_page)
            self.stats_page.refresh()
        elif name == "Review your shoot":
            self.pages.setCurrentWidget(self.review_page)
            self.review_page.load()
        elif name == "Map":
            self.pages.setCurrentWidget(self.map_page)
            self.map_page.refresh()
        elif name == "On this day":
            self.pages.setCurrentWidget(self.calendar_page)
            self.calendar_page.refresh()
        elif name == "Create":
            self.create_page.set_library_context(list(self.grid.selected), self.filter, self.sort.currentData())
            self.pages.setCurrentWidget(self.create_page)
            self.create_page.refresh()
        elif name == "Library status":
            self.pages.setCurrentWidget(self.status_page)
            self.status_page.refresh()
        elif name == "Migrate":
            self.pages.setCurrentWidget(self.migrate_page)
            self.migrate_page.refresh()
        elif name == "Albums":
            self.pages.setCurrentWidget(self.albums_page)
            self.albums_page.refresh()
        elif name == "Quarantine":
            self.pages.setCurrentWidget(self.quarantine_page)
            self.quarantine_page.refresh()
        elif name == "Tags":
            self.pages.setCurrentWidget(self.tags_page)
            self.tags_page.refresh()
        elif name == "Events":
            self.pages.setCurrentWidget(self.events_page)
            self.events_page.refresh()
        elif name == "Settings":
            self.pages.setCurrentWidget(self.settings_page)
            self.settings_page.refresh()
        elif name == "Duplicates":
            self.pages.setCurrentWidget(self.dupes)
            self.dupes.refresh()
        else:
            self.pages.setCurrentWidget(self.damaged)
            self.damaged.refresh()

    # --- events ------------------------------------------------------------------

    # --- albums --------------------------------------------------------------------

    def show_album(self, album) -> None:
        # A tile on the Albums page -> the library, showing just those photos.
        if album.kind == "event":
            self.show_event(int(album.key), album.name)
            return
        self.open_page("Library")
        if album.kind == "smart":
            import json
            from lunelis.albums import smart
            self.set_filter(Filter(smart=json.dumps(smart.get(self.conn, int(album.key))), scope_name=album.name))
        elif album.kind == "album":
            self.set_filter(Filter(album_id=int(album.key), scope_name=album.name))
        else:
            self.set_filter(Filter(auto=album.key, scope_name=album.name))

    def _pick_album(self, title: str):
        from PySide6.QtWidgets import QInputDialog
        from lunelis.albums import model as albums
        mine = albums.your_albums(self.conn)
        labels = ["New album…"] + [f"{a.name}  ({a.count:,})" for a in mine]
        choice, ok = QInputDialog.getItem(self, title, "Album:", labels, 1 if mine else 0, False)
        if not ok:
            return None
        if choice == labels[0]:
            name, ok = QInputDialog.getText(self, "New album", "Name:")
            if not ok or not name.strip():
                return None
            return albums.create(self.conn, name), name.strip()
        a = mine[labels.index(choice) - 1]
        return int(a.key), a.name

    def add_to_album(self) -> None:
        from lunelis.albums import model as albums
        ids = self._selected_or_warn()
        if not ids:
            return
        picked = self._pick_album(f"Add {len(ids):,} photo{'s' if len(ids) != 1 else ''} to an album")
        if picked is None:
            return
        aid, name = picked
        both = self._with_pairs(ids)
        self._history().before(self.conn, f"add to \"{name}\"", "album", both, aid)
        added = albums.add_files(self.conn, aid, both)
        self.status.setText(f"Added {added:,} photo{'s' if added != 1 else ''} to \"{name}\""
                            + (f" ({len(ids) - added:,} already there)" if added < len(ids) else ""))

    def _tag_the_library(self) -> None:
        """Scene tags for every photo the model hasn't seen: a background job."""
        from lunelis.jobs import engine
        roots = [(rid, None) for rid, in self.conn.execute("SELECT id FROM roots WHERE enabled = 1")]
        if not roots:
            return
        s = Settings(self.conn)
        schedule = {"mode": s.get("job_default_when"), "idle_minutes": s.get("job_idle_minutes"),
                    "start_hour": s.get("job_window_start_hour"), "end_hour": s.get("job_window_end_hour")}
        engine.create_job(self.conn, "scene_tags", "Scene tags", roots, {"schedule": schedule})
        self._job_queued()
        self.status.setText("Scene tags: looking through the library in the background (Jobs, Ctrl+J)")

    def _open_suggestions(self) -> None:
        self.open_page("Tags")
        self.tags_page.show_suggestions()

    def open_ask(self) -> None:
        self.open_page("Library")
        self.ask_bar.open()

    def _ask(self, asked) -> None:
        """Show the answer to a sentence (ask.py) in the library, best first."""
        from lunelis import ask
        from lunelis.recognize.scenes import backend
        rules = ask.filter_json(asked)
        rec = backend()
        if asked.looks and rec is None:
            self.ask_bar.say("Scene tags are off, so this looks for the words in names, folders and tags")
        else:
            self.ask_bar.say("Working it out…" if asked.looks else "")

        def show(result) -> None:
            ranked, words = result
            name = f"Asked: {asked.sentence}"
            if ranked is not None:
                self.ask_bar.say(f"{len(ranked):,} best matches" if ranked else "Nothing looks like that")
                self.set_filter(Filter(smart=rules, ids=tuple(ranked), ranked=True, scope_name=name))
            else:
                self.ask_bar.say("")
                self.set_filter(Filter(smart=rules, query=words, scope_name=name))
        self._bg().run("ask", lambda c: ask.answer(c, asked, rec), show,
                       error=lambda e: self.ask_bar.say(f"Couldn't answer that: {e}"))

    def find_similar(self) -> None:
        """Find similar (one photo) / More like these (a selection), best first."""
        from lunelis import ask
        from lunelis.recognize.scenes import backend
        ids = self._targets()
        if not ids:
            return
        rec = backend()
        if rec is None:
            self.status.setText("Find similar needs scene tags turned on (Settings > Library > Scene tags)")
            return
        name = "Like this photo" if len(ids) == 1 else f"Like these {len(ids):,} photos"

        def show(ranked) -> None:
            if not ranked:
                self.status.setText("Nothing to compare yet - the scene model hasn't looked at this photo")
                return
            self.open_page("Library")
            self.set_filter(Filter(ids=tuple(ranked), ranked=True, scope_name=name))
        self._bg().run("similar", lambda c: ask.similar_to(c, ids, rec), show,
                       error=lambda e: self.status.setText(f"Couldn't find similar photos: {e}"))

    def cull(self) -> None:
        """Full-screen, keyboard culling of the selection (if several are
        selected) or of everything the library shows."""
        from lunelis.ui.cull_view import CullView
        if len(self.grid.selected) > 1:
            ids = [self.index.file_id(i) for i in range(len(self.index)) if self.index.file_id(i) in self.grid.selected]
        else:
            ids = [self.index.file_id(i) for i in range(len(self.index))]
        if not ids:
            self.status.setText("Nothing to cull - the library shows no photos")
            return
        start = self.index.file_id(self.grid.current) if 0 <= self.grid.current < len(self.index) else ids[0]
        self.cull_view = CullView(self.conn, ids, self.rate_ids)
        self.cull_view.pos = ids.index(start) if start in ids else 0
        self.cull_view._show()
        self.cull_view.closed.connect(lambda: (self.grid.viewport().update(), self.activateWindow()))
        self.cull_view.showFullScreen()

    def _dropped_on_album(self, album, ids: list[int]) -> None:
        from lunelis.albums import model as albums
        both = self._with_pairs(ids)
        self._history().before(self.conn, f"add to \"{album.name}\"", "album", both, int(album.key))
        added = albums.add_files(self.conn, int(album.key), both)
        self.status.setText(f"Added {added:,} photo{'s' if added != 1 else ''} to \"{album.name}\""
                            + (f" ({len(both) - added:,} already there)" if added < len(both) else ""))
        self.albums_page.refresh()

    def _paths_of(self, ids: list[int]) -> list[str]:
        out = {}
        for start in range(0, len(ids), 900):
            chunk = ids[start:start + 900]
            for fid, root, rel in self.conn.execute(
                    f"SELECT f.id, r.path, f.rel_path FROM files f JOIN roots r ON r.id = f.root_id"
                    f" WHERE f.id IN ({','.join('?' * len(chunk))})", chunk):
                out[fid] = os.path.join(root, *rel.split("/"))
        return [out[i] for i in ids if i in out]

    def new_album(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        from lunelis.albums import model as albums
        ids = self._selected_or_warn()
        if not ids:
            return
        name, ok = QInputDialog.getText(self, "New album", f"Name for an album of {len(ids):,} photos:")
        if ok and name.strip():
            both = self._with_pairs(ids)
            aid = albums.create(self.conn, name, both)
            self._history().record(f"new album \"{name.strip()}\"", "album", both,
                                   {"exists": False, "name": None, "inside": set()}, aid)
            self.status.setText(f"Album \"{name.strip()}\" made from {len(ids):,} photos")

    def _update_archive_action(self) -> None:
        from lunelis.albums import archive
        ids = self._targets()
        done, _ = archive.split(self.conn, ids) if ids else ([], [])
        self.archive_action.setText("Bring back from the arc&hive" if ids and len(done) == len(ids) else "Arc&hive")

    def move_archive(self) -> None:
        self.open_page("Migrate")
        self.migrate_page.prefill_archive()

    def toggle_archive(self) -> None:
        """Archive the selection; if it's all archived already, bring it back."""
        from lunelis.albums import archive
        ids = self._with_pairs(self._targets())
        if not ids:
            return
        archived, live = archive.split(self.conn, ids)
        self._history().before(self.conn, "archive" if live else "bring back from the archive", "archive", ids)
        if live:
            n = archive.archive(self.conn, live)
            self.status.set_link(f"Archived {n:,} photo{'s' if n != 1 else ''} - out of the library, kept in ",
                                 "Albums > Archive", "Albums")
        else:
            n = archive.unarchive(self.conn, archived)
            self.status.setText(f"Brought {n:,} photo{'s' if n != 1 else ''} back into the library")
        self.grid.clear_selection()
        self.reload()

    def remove_from_album(self) -> None:
        from lunelis.albums import model as albums
        ids = self._selected_or_warn()
        if not ids or self.filter.album_id is None:
            return
        self._history().before(self.conn, f"take out of \"{self.filter.scope_name}\"", "album", ids,
                               self.filter.album_id)
        n = albums.remove_files(self.conn, self.filter.album_id, ids)
        self.status.setText(f"Took {n:,} photo{'s' if n != 1 else ''} out of \"{self.filter.scope_name}\" "
                            "- the photos themselves are untouched")
        self.grid.clear_selection()
        self.reload()

    def show_event(self, event_id: int, name: str) -> None:
        self.open_page("Library")
        self.set_filter(Filter(event_id=event_id, event_name=name))

    # --- burst stacks ----------------------------------------------------------------

    def _current_stack(self) -> tuple[int, int] | None:
        """(file id, stack id) of the photo the stack actions apply to."""
        i = self.grid.current
        if not 0 <= i < len(self.index):
            return None
        sid = self.index.stack_id(i)
        return (self.index.file_id(i), sid) if sid is not None else None

    def toggle_stack(self) -> None:
        cur = self._current_stack()
        if not cur:
            self.status.setText("That photo isn't part of a burst stack")
            return
        fid, sid = cur
        opened = self.index.toggle_stack(sid)
        self.grid.set_index(self.index)
        if not opened:
            # Closed: land on the tile that stands for the stack now.
            cover = next((i for i, r in enumerate(self.index.rows) if r[11] == sid), -1)
            fid = self.index.file_id(cover) if cover >= 0 else fid
        pos = self.index.position(fid)
        if pos >= 0:
            self.grid.current = self.grid.anchor = pos
            self.grid.selected = {fid}
            self.grid.scroll_to(pos)
            self.grid.selection_changed.emit(1)
        self.grid.viewport().update()

    def set_stack_cover(self) -> None:
        cur = self._current_stack()
        if cur and stacks.set_cover(self.conn, cur[0]):
            self.status.setText("Stack cover changed")
            self.reload()

    def unstack(self) -> None:
        cur = self._current_stack()
        if not cur:
            return
        self._history().before(self.conn, "unstack", "stack", stacks.members(self.conn, cur[1]), cur[1])
        n = stacks.unstack(self.conn, cur[1])
        self.index.expanded.discard(cur[1])
        self.status.setText(f"Unstacked {n} frames - they'll stay separate")
        self.reload()

    def _selected_or_warn(self) -> list[int]:
        ids = list(self.grid.selected)
        if not ids:
            QMessageBox.information(self, "Nothing selected", "Select some photos in the library first.")
        return ids

    def _confirm_move(self, ids: list[int], to: str) -> bool:
        taken = events.events_of(self.conn, ids)
        if not taken:
            return True
        n = len(taken)
        return QMessageBox.question(
            self, "Move photos between events?",
            f"{n:,} of these photos are already in another event. A photo is in one event at a time - "
            f"move them to {to}?") == QMessageBox.StandardButton.Yes

    def new_event(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        ids = self._selected_or_warn()
        if not ids:
            return
        name, ok = QInputDialog.getText(self, "New event", f"Name for an event of {len(ids):,} photos:")
        if not ok or not name.strip() or not self._confirm_move(ids, f"\"{name.strip()}\""):
            return
        events.create(self.conn, name, ids)
        self.status.setText(f"Event \"{name.strip()}\" made from {len(ids):,} photos")
        self.events_page.refresh()

    def add_to_event(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        ids = self._selected_or_warn()
        if not ids:
            return
        all_ev = events.all_events(self.conn)
        if not all_ev:
            self.new_event()
            return
        labels = [f"{e.name}  ({e.dates()})" for e in all_ev]
        choice, ok = QInputDialog.getItem(self, "Add to an event", "Event:", labels, 0, False)
        if not ok:
            return
        e = all_ev[labels.index(choice)]
        if not self._confirm_move([i for i in ids if events.events_of(self.conn, [i]).get(i) != e.id],
                                  f"\"{e.name}\""):
            return
        both = self._with_pairs(ids)
        self._history().before(self.conn, f"add to the event \"{e.name}\"", "events", both)
        events.add_files(self.conn, e.id, both)
        self.status.setText(f"Added {len(ids):,} photos to \"{e.name}\"")
        self.events_page.refresh()
        if self.filter.event_id is not None:
            self.reload()

    def remove_from_event(self) -> None:
        ids = self._selected_or_warn()
        if not ids:
            return
        n = len(events.events_of(self.conn, ids))
        self._history().before(self.conn, "take out of their event", "events", ids)
        events.remove_files(self.conn, ids)
        self.status.setText(f"Took {n:,} photos out of their event" if n else "None of these were in an event")
        self.events_page.refresh()
        if self.filter.event_id is not None:
            self.reload()

    def new_job(self, kind: str) -> None:
        title = {"duplicates": "Find duplicates", "full_hash": "Hash everything",
                 "integrity": "Check integrity"}[kind]
        dlg = ScopeDialog(self.conn, title, self)
        if not dlg.exec() or not dlg.result_value:
            return
        name, scope, options = dlg.result_value
        engine.create_job(self.conn, kind, name, scope, options)
        self.runner.poke()
        self.show_jobs()

    def verify_duplicates(self) -> None:
        roots = [(rid, None) for (rid,) in self.conn.execute(
            "SELECT DISTINCT f.root_id FROM duplicate_groups g JOIN duplicate_group_files m"
            " ON m.group_id = g.id JOIN files f ON f.id = m.file_id WHERE g.method = 'sampled'"
            " AND f.content_hash IS NULL")]
        if not roots:
            return
        engine.create_job(self.conn, "verify", "Likely duplicate groups", roots)
        self.runner.poke()
        self.show_jobs()

    def show_jobs(self) -> None:
        if self._jobs_dialog is None:
            self._jobs_dialog = JobsDialog(self.conn, self.runner, self)
        self._jobs_dialog.show()
        self._jobs_dialog.raise_()

    def _jobs_changed(self) -> None:
        self._update_jobs_button()
        if self.pages.currentWidget() is self.dupes and self.runner.current is None:
            self.dupes.refresh()                  # a job just finished: show its groups
        if self.pages.currentWidget() is self.migrate_page and self.migrate_page._thread is None:
            self.migrate_page.refresh()           # migration progress
        if self.pages.currentWidget() is self.backups_page and self.runner.current is None:
            self.backups_page.refresh()

    def _update_jobs_button(self) -> None:
        counts = dict(self.conn.execute(
            "SELECT state, COUNT(*) FROM jobs WHERE state IN ('running','queued','waiting','paused')"
            " GROUP BY state").fetchall())
        active = counts.get("running", 0) + counts.get("queued", 0)
        parts = []
        if active:
            parts.append(f"{active} running")
        if counts.get("waiting"):
            parts.append(f"{counts['waiting']} waiting")
        if counts.get("paused"):
            parts.append(f"{counts['paused']} paused")
        self.jobs_button.setText("Jobs" + (f" — {', '.join(parts)}" if parts else ""))

    # --- filtering -------------------------------------------------------------

    def _filter_button(self, text: str, options: list, attr: str) -> QToolButton:
        b = QToolButton(objectName="FilterMenu", text=f"{text} ▾")
        b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(b)
        for label, value in options:
            menu.addAction(label, lambda v=value: self.set_filter(
                Filter(**{**self.filter.__dict__, attr: v})))
        b.setMenu(menu)
        return b

    def set_filter(self, f: Filter) -> None:
        self.filter = f
        if hasattr(self, "search") and (self.search.text().strip() or None) != f.query:
            self.search.blockSignals(True)
            self.search.setText(f.query or "")
            self.search.blockSignals(False)
        self.reload()
        self.grid.verticalScrollBar().setValue(0)
        while self.chips.count():
            self.chips.takeAt(0).widget().deleteLater()
        parts = []
        if f.min_stars == UNRATED:
            parts.append(("Unrated", "min_stars", 0))
        elif f.min_stars:
            parts.append(("★ " + str(f.min_stars) + ("+" if f.min_stars < 5 else ""), "min_stars", 0))
        if f.label:
            parts.append((f.label, "label", None))
        if f.flag:
            parts.append(("Picks" if f.flag == "pick" else "Rejects", "flag", None))
        if f.backup:
            parts.append(("Backed up" if f.backup == "ok" else "Not backed up", "backup", None))
        if f.event_id is not None:
            parts.append((f"Event: {f.event_name or f.event_id}", "event_id", None))
        if f.album_id is not None:
            parts.append((f"Album: {f.scope_name or f.album_id}", "album_id", None))
        if f.auto:
            parts.append((f.scope_name or f.auto, "auto", None))
        if f.tag:
            parts.append(("Tag: " + f.tag.replace("|", " › "), "tag", None))
        if f.ranked:
            parts.append((f.scope_name or "Best matches", "ranked", None))
        elif f.smart:
            parts.append((f"Smart album: {f.scope_name or ''}".rstrip(": "), "smart", None))
        if f.folder is not None:
            parts.append((f"Folder: {f.scope_name or ''}".rstrip(": "), "folder", None))
        # Some chips stand for several fields: an answer is its ids, order and rules.
        groups = {"ranked": {"ids": None, "ranked": False, "smart": None, "scope_name": None},
                  "smart": {"smart": None, "scope_name": None}, "folder": {"folder": None, "scope_name": None}}
        for text, attr, cleared in parts:
            chip = QPushButton(f"{text}  ✕", objectName="Chip")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setToolTip("Remove this filter")
            clear = groups.get(attr, {attr: cleared})
            chip.clicked.connect(lambda _=False, c=clear: self.set_filter(Filter(**{**self.filter.__dict__, **c})))
            self.chips.addWidget(chip)
        self.clear_all.setVisible(f.active())

    # --- rating --------------------------------------------------------------

    def _targets(self) -> list[int]:
        if self.pages.currentWidget() is getattr(self, "edit_page", None) and self.edit_page.current() is not None:
            return [self.edit_page.current()]          # the Edit page: the photo being edited
        if self.grid.selected:
            return list(self.grid.selected)
        if 0 <= self.grid.current < len(self.index):
            return [self.index.file_id(self.grid.current)]
        return []

    def _with_pairs(self, ids: list[int]) -> list[int]:
        """A RAW+JPEG pair is one photo: an action on one is on both."""
        from lunelis import pairs
        return pairs.with_partners(self.conn, ids) if ids else ids

    def rate(self, **change) -> None:
        self.rate_ids(self._targets(), **change)

    def rate_ids(self, ids: list[int], **change) -> None:
        """Stars / label / flag on these photos (and their RAW+JPEG partners),
        undoable, written to sidecars shortly after."""
        ids = self._with_pairs(list(ids))
        if not ids:
            return
        if change.get("label"):
            # Pressing a label key again takes it off (Lightroom behaviour).
            have = 0
            for start in range(0, len(ids), 900):
                chunk = ids[start:start + 900]
                have += self.conn.execute(
                    f"SELECT COUNT(*) FROM ratings WHERE color_label = ? AND file_id IN "
                    f"({','.join('?' * len(chunk))})", (change["label"], *chunk)).fetchone()[0]
            if have == len(ids):
                change["label"] = None
        if len(ids) > RATE_CONFIRM and QMessageBox.question(
                self, "Change many photos?",
                f"Change {self._describe_rating(change)} on {len(ids):,} photos at once? (Ctrl+Z takes it back.)") \
                != QMessageBox.StandardButton.Yes:
            return
        self._history().before(self.conn, f"{self._describe_rating(change)} on {len(ids):,} "
                                          f"photo{'s' if len(ids) != 1 else ''}", "ratings", ids)
        ratings.set_ratings(self.conn, ids, **change)
        self.index.refresh_ratings(self.conn, ids)
        self.grid.viewport().update()
        what = self._describe_rating(change)
        n = len(ids)
        self.status.setText(f"{n:,} photo{'s' if n != 1 else ''}: {what} — saving to sidecars…")
        self._xmp_timer.start()                       # restart the debounce
        if self.pages.currentWidget() is self.detail:
            self.detail.refresh_info()
        elif self.pages.currentWidget() is self.edit_page:
            self.edit_page.view.refresh_info()

    @staticmethod
    def _describe_rating(change: dict) -> str:
        if "stars" in change:
            return "★" * change["stars"] or "no stars"
        if "label" in change:
            return change["label"] or "no label"
        return change.get("flag") or "unflagged"

    def undo(self) -> None:
        """Ctrl+Z: the photo being edited undoes its edit; otherwise the last
        rating, label or flag change is taken back."""
        for page, view in ((self.detail, self.detail), (self.edit_page, self.edit_page.view)):
            if self.pages.currentWidget() is page and view.edit.active:
                view.edit.undo()
                return
        self._step(undo=True)

    def redo(self) -> None:
        """Ctrl+Shift+Z / Ctrl+Y: the photo being edited redoes its edit; otherwise
        the last thing undone comes back."""
        for page, view in ((self.detail, self.detail), (self.edit_page, self.edit_page.view)):
            if self.pages.currentWidget() is page and view.edit.active:
                view.edit.redo()
                return
        self._step(undo=False)

    def _history(self):
        if not hasattr(self, "history"):
            from lunelis.history import History
            self.history = History()
        return self.history

    def _update_undo_actions(self) -> None:
        h = self._history()
        self.undo_action.setText(f"&Undo {h.next_undo()}" if h.next_undo() else "&Undo")
        self.redo_action.setText(f"&Redo {h.next_redo()}" if h.next_redo() else "&Redo")
        self.redo_action.setEnabled(h.next_redo() is not None)

    def _step(self, undo: bool) -> None:
        if self._bg().busy("edits"):
            self.status.setText("Still saving edits - try again in a moment")
            return
        h = self._history()
        step = h.undo(self.conn) if undo else h.redo(self.conn)
        if step is None:
            self.status.setText("Nothing to undo" if undo else "Nothing to redo")
            return
        if step.kind == "ratings":
            self.index.refresh_ratings(self.conn, step.ids)
            self.grid.viewport().update()
            self._xmp_timer.start()
        elif step.kind == "edits":
            self.render_edits(step.ids)
        else:
            self.reload()
        if step.kind == "tags":
            self._tags_changed()
        if step.kind in ("events",):
            self.events_page.refresh()
        self.status.setText(f"{'Undone' if undo else 'Redone'}: {step.label}")
        for page, view in ((self.detail, self.detail), (self.edit_page, self.edit_page.view)):
            if self.pages.currentWidget() is page:
                view.refresh_info()

    def _daily_backup(self, now: bool = False) -> None:
        if getattr(self, "_backup_thread", None) is not None:
            return                                    # one already running
        if now:
            self.status.setText("Backing up the catalog…")
        self._backup_thread = QThread(self)
        self._backup_worker = CatalogBackup(now)
        self._backup_worker.moveToThread(self._backup_thread)
        self._backup_thread.started.connect(self._backup_worker.run)
        self._backup_worker.done.connect(self._on_backup_done)
        self._backup_thread.start()

    @unless_closed
    def _on_backup_done(self, result) -> None:
        self._backup_thread.quit()
        self._backup_thread.wait()
        self._backup_thread.deleteLater()
        self._backup_worker.deleteLater()
        self._backup_thread = None
        if isinstance(result, Exception):
            self.status.setText(f"Catalog backup failed: {result}")
        elif result is not None:
            self.status.setText(f"Catalog backed up ({result.stat().st_size / 1e6:.0f} MB)")
        if self.pages.currentWidget() is self.settings_page:
            self.settings_page.refresh()

    def _write_sidecars(self) -> None:
        if self._xmp_thread is not None:
            self._xmp_again = True                    # changes arrived mid-write: go again after
            return
        self._xmp_thread = QThread(self)
        self._xmp_worker = XmpWriter()
        self._xmp_worker.moveToThread(self._xmp_thread)
        self._xmp_thread.started.connect(self._xmp_worker.run)
        self._xmp_worker.done.connect(self._on_sidecars_written)
        self._xmp_thread.start()

    @unless_closed
    def _on_sidecars_written(self, r) -> None:
        self._xmp_thread.quit()
        self._xmp_thread.wait()
        self._xmp_thread.deleteLater()
        self._xmp_worker.deleteLater()
        self._xmp_thread = None
        if r.failed:
            self.status.setText(f"Couldn't write {r.failed:,} sidecar(s) — kept in the catalog, "
                                "will retry (folder offline or read-only?)")
            self._later(60_000, self._write_sidecars)
        elif r.done:
            self.status.setText(f"Saved {r.done:,} rating change(s) to sidecars")
        if self._xmp_again:
            self._xmp_again = False
            self._xmp_timer.start()

    def _update_count(self, selected: int) -> None:
        n = len(self.index)
        text = f"{n:,} photo{'s' if n != 1 else ''}"
        if selected:
            text = f"{selected:,} of {text} selected"
        self.count.setText(text)

    # --- background work -----------------------------------------------------

    def add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Add a photo folder")
        if not folder:
            return
        try:
            root_id = add_root(self.conn, folder)
        except (RootOverlap, RootUnavailable) as e:
            QMessageBox.warning(self, "Can't add that folder", plain(e))
            return
        self.start([root_id])

    def rescan_all(self) -> None:
        ids = [r["id"] for r in self.conn.execute("SELECT id FROM roots WHERE enabled = 1")]
        if ids:
            self.start(ids)

    def start(self, root_ids: list[int]) -> None:
        if self._thread is not None:
            return
        self._thread = QThread(self)
        self._worker = LibraryWorker(root_ids)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self.status.setText)
        self._worker.step.connect(self._scan_step)
        self._worker.step.connect(self.status_page.step)
        self._worker.progress.connect(self.status_page.step_text)
        self.status_page.set_running(True)
        self._worker.root_done.connect(self._on_root_done)
        self._worker.root_failed.connect(lambda m: QMessageBox.warning(self, "Folder unavailable", m))
        self._worker.meta_done.connect(self._on_meta_done)
        self._worker.thumb_done.connect(self._on_thumb_done)
        self._worker.relinked.connect(
            lambda n: self.status.setText(f"Recognised {n:,} moved file{'s' if n != 1 else ''} - ratings kept"))
        self._worker.damage_done.connect(self._on_damage_done)
        self._worker.similar_done.connect(self._on_similar_done)
        self._worker.noticed_done.connect(self._on_noticed)
        self._worker.takeout_done.connect(lambda r: self.status.setText(
            f"Google Takeout: dates for {r.dated:,} files, locations for {r.located:,}"))
        self._worker.finished.connect(self._on_finished)
        self._set_busy(True)
        self._thread.start()
        self._refresh_timer.start()

    def cancel_scan(self) -> None:
        if self._worker:
            self._worker.cancel()
            self.status.setText("Stopping…")

    @unless_closed
    def _on_root_done(self, r: ScanResult) -> None:
        self.status.setText(f"Scanned in {r.seconds:.1f}s: {r.added:,} new, {r.updated:,} changed, "
                            f"{r.missing:,} missing")
        self.reload()

    @unless_closed
    def _on_meta_done(self, r: ExtractResult) -> None:
        if r.read or r.failed:
            self.status.setText(f"Metadata read for {r.read:,} files in {r.seconds:.1f}s")
            self.reload()

    @unless_closed
    def _on_thumb_done(self, r) -> None:
        if r.made or r.failed:
            self.status.setText(f"Made {r.made:,} thumbnails in {r.seconds:.1f}s"
                                + (f" ({r.failed:,} preview unavailable)" if r.failed else ""))

    @unless_closed
    def _integrity_tick(self) -> None:
        from lunelis.jobs import rolling
        if rolling.due(self.conn) and rolling.start(self.conn):
            self.runner.poke()

    def check_sources(self) -> None:
        """Ask every enabled source whether it answers, off the GUI thread."""
        roots = self.conn.execute("SELECT id, path FROM roots WHERE enabled = 1").fetchall()

        def ask():
            from lunelis.reach import reachable
            return {rid: path for rid, path in roots if not reachable(path)}
        if not hasattr(self, "_reach_bg"):
            from lunelis.ui.background import Background
            self._reach_bg = Background(self, self.conn)
        self._reach_bg.run("reach", ask, self._sources_checked, db=False)

    def _sources_checked(self, offline: dict) -> None:
        if offline == self.offline_roots:
            return
        newly = [p for r, p in offline.items() if r not in self.offline_roots]
        back = [p for r, p in self.offline_roots.items() if r not in offline]
        self.offline_roots = offline
        self.grid.offline_roots = set(offline)
        self.grid.viewport().update()
        self.detail.offline_paths = set(offline.values())
        if newly:
            self.status.setText(f"{', '.join(newly)} isn't answering - its photos stay browsable, marked OFFLINE")
        elif back:
            self.status.setText(f"{', '.join(back)} is back")

    def _on_noticed(self, n: int) -> None:
        self.status.setText(f'Lunelis noticed {n} set{"s" if n != 1 else ""} of shots worth building - '
                            '<a href="page:Library status">see Library status</a>')
        if self.pages.currentWidget() is self.status_page:
            self.status_page.refresh()

    def show_photos(self, ids: list, name: str = "Lunelis noticed") -> None:
        """The library showing just these photos, all selected."""
        self.open_page("Library")
        self.set_filter(Filter(ids=tuple(ids), scope_name=name))
        self.grid.selected = {f for f in ids if self.index.position(f) >= 0}
        if self.grid.selected:
            first = min(self.index.position(f) for f in self.grid.selected)
            self.grid.current = self.grid.anchor = first
            self.grid.scroll_to(first)
        self.grid.viewport().update()
        self.grid.selection_changed.emit(len(self.grid.selected))

    def build_noticed(self, sid: int, kind: str, ids: list) -> None:
        """Build it: hand the frames to the tool that makes the thing."""
        from lunelis import noticed
        self.show_photos(ids)
        if kind in ("hdr", "panorama"):
            self._noticed_pending = sid
            self.merge_photos(kind)
            if getattr(self, "_merge_thread", None) is None:
                self._noticed_pending = None          # the dialog was cancelled: still on offer
        elif kind == "timelapse":
            self.open_page("Create")
            self.create_page.open_tool("timelapse")
            noticed.mark_built(self.conn, sid)

    def _on_similar_done(self, groups: int) -> None:
        if self.pages.currentWidget() is self.dupes:
            self.dupes.refresh()

    def _show_library_state(self) -> None:
        """The state as the catalog has it, before any scan this session."""
        from datetime import datetime
        last = self.conn.execute("SELECT MAX(last_scanned_at) FROM roots").fetchone()[0]
        if not last:
            self.library_state.setText("")
            return
        try:
            t = datetime.fromisoformat(last)
            t = t.astimezone() if t.tzinfo else t          # stored in UTC
            when = t.strftime("%b %d, %I:%M %p").replace(" 0", " ")
        except ValueError:
            when = last
        n = self.conn.execute("SELECT COUNT(*) FROM damaged d JOIN files f ON f.id = d.file_id"
                              " WHERE d.dismissed = 0 AND f.missing_since IS NULL").fetchone()[0]
        self.library_state.set_links([(f"Last scanned {when}", "Library status")]
                                     + ([(f"{n:,} damaged file{'s' if n != 1 else ''}", "Damaged files")] if n else []))

    def _scan_step(self, i: int, done: int, total: int) -> None:
        self.scan_step.set_link("", f"Step {i + 1} of {len(SCAN_STEPS)} · {SCAN_STEPS[i]}", "Library status")
        self.scan_bar.setRange(0, total)                 # 0..0 = busy (no total yet)
        if total:
            self.scan_bar.setValue(done)
        self.scan_step.show()
        self.scan_bar.show()
        self.library_state.setText("Updating the library…")

    @unless_closed
    def _on_damage_done(self, r) -> None:
        from datetime import datetime
        n = sum(r.found.values())
        when = datetime.now().strftime("%I:%M %p").lstrip("0")
        if n:
            self.status.set_link(f"Up to date - {n:,} damaged file{'s' if n != 1 else ''}: ",
                                 "see Damaged files", "Damaged files")
        else:
            self.status.setText("Up to date")
        self.library_state.set_links([(f"Library up to date ({when})", "Library status")]
                                     + ([(f"{n:,} damaged file{'s' if n != 1 else ''}", "Damaged files")] if n else []))

    @unless_closed
    def _on_finished(self) -> None:
        self._refresh_timer.stop()
        self.scan_step.hide()
        self.scan_bar.hide()
        self.status_page.finished(cancelled=bool(self._worker and self._worker._cancel))
        self._thread.quit()
        self._thread.wait()
        self._thread.deleteLater()
        self._worker.deleteLater()
        self._thread = self._worker = None
        self._set_busy(False)
        # Newly imported photos get the Settings > Import filter.
        from lunelis.edit import store as edit_store
        self.render_edits(edit_store.apply_import_filter(self.conn))
        # Photos from a named import are cataloged now: join them to their event.
        linked = events.link_imports(self.conn)
        if linked:
            self.status.setText(f"Added {linked:,} imported photos to their event")
        self.reload()
        self._run_autopilot()

    def _set_busy(self, busy: bool) -> None:
        self.add_action.setEnabled(not busy)
        self.rescan_action.setEnabled(not busy)
        self.cancel_action.setEnabled(busy)

    def _later(self, ms: int, fn) -> None:
        """Run fn once after ms - unless the window has closed by then. A plain
        QTimer.singleShot can't be stopped and fired into a closed catalog."""
        t = QTimer(self, singleShot=True, interval=ms)
        t.timeout.connect(lambda: None if getattr(self, "_closed", False) else fn())
        t.start()

    def _busy_work(self) -> list[str]:
        busy = []
        if getattr(self, "_export_thread", None) is not None:
            busy.append("an export")
        if getattr(self, "_merge_thread", None) is not None:
            busy.append("a merge")
        if getattr(self, "_batch_thread", None) is not None:
            busy.append("rendering edits")
        if self.importer.busy():
            busy.append("an import")
        if getattr(self.runner, "current", None) is not None:
            busy.append("a background job")
        return busy

    def closeEvent(self, event) -> None:
        if self.tray is not None and not self._quitting:
            # Closing the window keeps Lunelis in the tray, watching for cards.
            event.ignore()
            self.hide()
            if not getattr(self, "_told_tray", False):
                self._told_tray = True
                self.tray.showMessage("Lunelis is still running",
                                      "It's in the tray, watching for memory cards. Right-click it to quit.")
            return
        try:
            Settings(self.conn).set("window_geometry", bytes(self.saveGeometry().toHex()).decode("ascii"))
        except Exception:
            pass                                       # never let this stop a close
        busy = self._busy_work()
        if busy and Settings(self.conn).get("confirm_quit") and not getattr(self, "_quit_confirmed", False):
            answer = QMessageBox.question(
                self, "Quit Lunelis?",
                "Still running: " + ", ".join(busy) + ".\n\nQuit anyway? It stops safely and picks up where it "
                "left off next time where it can.")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                self._quitting = False
                return
            self._quit_confirmed = True
        if self.importer.busy():
            self.importer._stop()
            self.importer._thread.wait(15_000)
        # An edit in progress is saved; its proxy + thumbnail finish rendering.
        self.detail.set_editing(False)
        self.edit_page.view.shut()
        if getattr(self, "_merge_thread", None) is not None:
            self._merge.cancel()
            self._merge_thread.quit()
            self._merge_thread.wait(60_000)
        if getattr(self, "_export_thread", None) is not None:
            self._export.cancel()
            self._export_thread.quit()
            self._export_thread.wait(30_000)
        if getattr(self, "_batch_thread", None) is not None:
            self._batch.cancel()
            self._batch_thread.quit()
            self._batch_thread.wait(15_000)
        from PySide6.QtCore import QThreadPool
        QThreadPool.globalInstance().waitForDone(15_000)
        self.detail.edit.out_pool.waitForDone(15_000)
        # Pages' own worker threads (album counts, suggestions, previews, restores,
        # update checks) must finish before the catalog closes - a QThread
        # destroyed while running aborts the whole process.
        for page in (self.albums_page, self.events_page, self.migrate_page, self.backups_page,
                     self.settings_page, self.importer, self.dupes, self.dupes.near, self.quarantine_page,
                     self.damaged):
            worker = getattr(page, "_worker", None)
            if hasattr(worker, "cancel"):
                worker.cancel()
            for name in ("_thread", "_uthread"):
                th = getattr(page, name, None)
                if isinstance(th, QThread) and th.isRunning():
                    th.quit()
                    th.wait(10_000)
        # Timers that read the catalog must stop before it's closed below.
        self._jobs_timer.stop()
        self._drive_timer.stop()
        self._dt_timer.stop()
        self._reach_timer.stop()
        self._integrity_timer.stop()
        if self._autopilot_thread is not None:
            self._autopilot.cancel()
            self._autopilot_thread.quit()
            self._autopilot_thread.wait(30_000)
        if self._jobs_dialog is not None:
            self._jobs_dialog._timer.stop()
            self._jobs_dialog.close()
        # Jobs stop between files; their progress is already in the catalog and
        # they resume on the next start.
        self.runner.quit()
        self._runner_thread.quit()
        self._runner_thread.wait(10_000)
        if getattr(self, "_backup_thread", None) is not None:
            self._backup_thread.quit()
            self._backup_thread.wait()
        # Don't leave rating changes only in the catalog: write them now.
        self._xmp_timer.stop()
        if self._xmp_thread is not None:
            self._xmp_thread.quit()
            self._xmp_thread.wait()
        # A few are written now; a big batch (or one waiting on a sleeping NAS)
        # stays pending in the catalog and is written at the next start.
        pending = ratings.pending_count(self.conn)
        if pending and pending <= CLOSE_SIDECAR_LIMIT:
            sync.export_pending(self.conn)
        elif pending:
            import logging
            logging.getLogger("lunelis").info("%d sidecar writes left for the next start", pending)
        if self._worker:
            self._worker.cancel()
        self.thumbs.pool.clear()
        self.thumbs.pool.waitForDone(2000)
        self._finish_threads()
        # Every timer (in the window and its pages) reads the catalog: stop them
        # all before it closes, or one fires on a closed database.
        for timer in self.findChildren(QTimer):
            timer.stop()
        self._closed = True
        self.conn.close()
        super().closeEvent(event)
