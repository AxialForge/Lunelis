"""
The Settings page (Phase 1, Step 12).

Every change is saved the moment it's made - there's no Save button to
forget. Text-like fields are checked first (lunelis.settings.validate) and
an error is shown next to the field instead of being stored. Anything that
needs files moved (the sidecar store, the backup folder) moves them on a
background thread before the setting switches over; anything that can't
happen while Lunelis is running (moving the data folder, restoring a catalog
backup) is recorded and done at the next start.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QRadioButton, QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from lunelis import paths
from lunelis.catalog import backup
from lunelis.importers.scan import exclude_folder, excluded_folders, include_folder
from lunelis.importing.templates import PRESETS, Context, TemplateError, render
from lunelis.settings import Settings
from lunelis.ui.widgets import plain

SIDECAR_CHOICES = (
    ("central", "In Lunelis's own sidecar folder",
     "Photo folders stay clean. darktable and Lightroom don't look there (a darktable plugin is planned)."),
    ("beside", "Next to each photo",
     "darktable, Lightroom and culling tools see your ratings. Adds an .xmp file beside every rated photo."),
    ("catalog", "Only in the Lunelis catalog",
     "No sidecar files at all. Ratings are safe in the catalog and its backups, but no other program sees them."),
)
WHEN_CHOICES = (("now", "Straight away"), ("idle", "Only while the PC is idle"),
                ("window", "Only during set hours"))
# Sample date for the template example: the user's own mockup date.
EXAMPLE = Context(datetime(2026, 6, 19, 14, 3), camera="ILCE-7RM5", import_name="Air Show",
                  import_date=date(2026, 6, 20))


def _inside(child: str, parent: str) -> bool:
    c = os.path.normcase(os.path.normpath(child))
    p = os.path.normcase(os.path.normpath(parent))
    return c == p or c.startswith(p.rstrip("\\") + "\\")


class FolderMove(QObject):
    """Moves a folder's contents to a new place (paths.move_tree) off the GUI thread."""

    done = Signal(object)            # None | Exception

    def __init__(self, src: Path, dst: Path) -> None:
        super().__init__()
        self.src, self.dst = src, dst

    def run(self) -> None:
        try:
            paths.move_tree(self.src, self.dst)
            self.done.emit(None)
        except Exception as e:       # reported, never fatal: the old folder is still whole
            self.done.emit(e)


class _UpdateTask(QObject):
    # Runs an updater call off the GUI thread. `fn` may take a progress callback.
    done = Signal(object)
    progress = Signal(int, int)

    def __init__(self, fn) -> None:
        super().__init__()
        self.fn = fn

    def run(self) -> None:
        import inspect
        try:
            takes_progress = bool(inspect.signature(self.fn).parameters)
            self.done.emit(self.fn(self.progress.emit) if takes_progress else self.fn())
        except Exception as e:           # shown in the Updates tab
            self.done.emit(e)


class SettingsView(QWidget):
    library_changed = Signal()       # a source turned on/off, a folder skipped/unskipped
    rescan = Signal(list)            # root ids to scan now
    add_source = Signal()
    remove_source = Signal(int, str)  # root id, path: the window checks, backs up, removes
    rewrite_sidecars = Signal()      # every rating was marked pending: write them out
    tray_changed = Signal(bool)
    autostart_changed = Signal(bool)
    backup_now = Signal()
    scene_job = Signal()             # Tag the library (a scene_tags job)
    open_suggestions = Signal()      # the Tags page's Scene suggestions
    faces_job = Signal()             # Find faces in the library (a faces job)
    open_people = Signal()           # the People page
    restart = Signal()
    theme_changed = Signal(str)      # Appearance: apply it now
    view_changed = Signal()          # Appearance: library defaults changed
    open_page = Signal(str)          # a button that leads to another page (e.g. Backups)
    report_problem = Signal()
    install_update = Signal(object)  # a staged update folder: swap it in and restart

    TABS = ("General", "Appearance", "Library", "Import", "Edit", "Ratings & sidecars", "Duplicates & jobs",
            "Backups", "darktable", "Updates", "Advanced")
    THUMB_PRESETS = (("Small", 120), ("Medium", 180), ("Large", 260), ("Extra large", 360))

    def __init__(self, conn, parent=None) -> None:
        super().__init__(parent)
        self.conn = conn
        self._thread: QThread | None = None
        self._loading = False
        self._spins: dict[str, QSpinBox] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = QWidget(objectName="Toolbar")
        head.setFixedHeight(64)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(24, 0, 24, 0)
        title = QLabel("Settings")
        title.setObjectName("PageTitle")
        hl.addWidget(title)
        hl.addStretch(1)
        self.saved = QLabel(objectName="Count")
        hl.addWidget(self.saved)
        outer.addWidget(head)

        # Tabs across the top, one scrolling page of cards each.
        from PySide6.QtWidgets import QTabWidget
        self.tabs = QTabWidget()
        # Not document mode: Qt ignores tab-bar centring in it. The pane's frame
        # is styled away instead (theme.py, #SettingsTabs).
        self.tabs.setDocumentMode(False)
        self.tabs.tabBar().setExpanding(False)
        self.tabs.setObjectName("SettingsTabs")      # the app stylesheet centres its tab bar
        self.tabs.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)   # so its band colour applies
        cards = {
            "General": [self._startup, self._tray],
            "Edit": [self._editing, self._ai_models, self._edit_caches],
            "Appearance": [self._appearance, self._library_view],
            "Library": [self._sources, self._thumbnails, self._scene_tags, self._faces, self._places,
                        self._helpers],
            "Import": [self._import],
            "Ratings & sidecars": [self._sidecars],
            "Duplicates & jobs": [self._duplicates_and_jobs],
            "Backups": [self._backups],
            "darktable": [self._darktable],
            "Updates": [self._updates],
            "Advanced": [self._data_folder, self._logs],
        }
        for name in self.TABS:
            # '&' marks a keyboard shortcut in Qt labels; '&&' is a literal ampersand.
            self.tabs.addTab(self._tab_page([build() for build in cards[name]]), name.replace('&', '&&'))
        outer.addWidget(self.tabs, 1)
        self.refresh()

    def _tab_page(self, widgets: list[QWidget]) -> QScrollArea:
        scroll = QScrollArea(objectName="SettingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        page = QWidget(objectName="SettingsPage")
        col = QVBoxLayout(page)
        col.setContentsMargins(32, 24, 32, 32)
        col.setSpacing(20)
        for w in widgets:
            col.addWidget(w)
        col.addStretch(1)
        page.setMaximumWidth(920)
        wrap = QHBoxLayout()
        wrap.addStretch(1)
        wrap.addWidget(page, 100)
        wrap.addStretch(1)
        holder = QWidget(objectName="SettingsPage")
        holder.setLayout(wrap)
        scroll.setWidget(holder)
        return scroll

    def show_tab(self, name: str) -> None:
        if name in self.TABS:
            self.tabs.setCurrentIndex(self.TABS.index(name))

    # --- new cards (v0.3.0) ------------------------------------------------------------

    def _appearance(self) -> QFrame:
        from lunelis.ui import theme
        card, v = self._card("Theme", "Graphite is the light look from the mockups; Midnight is dark; High contrast "
                                      "is black and white with yellow highlights. \"Follow Windows\" switches "
                                      "between Graphite and Midnight with Windows' own light/dark setting.")
        self.theme_group = QButtonGroup(self)
        for i, (key, title) in enumerate(theme.CHOICES):
            row = QHBoxLayout()
            b = QRadioButton(title)
            b.setProperty("theme", key)
            self.theme_group.addButton(b, i)
            row.addWidget(b)
            if key in theme.THEMES:
                t = theme.THEMES[key]
                for colour in (t.sidebar_bg, t.canvas, t.surface, t.chip_bg, t.accent):
                    sw = QLabel(objectName="ThemeSwatch")
                    sw.setFixedSize(22, 16)
                    sw.setStyleSheet(f"background: {colour};")
                    row.addWidget(sw)
            row.addStretch(1)
            v.addLayout(row)
        self.theme_group.idClicked.connect(self._theme_clicked)
        return card

    def _theme_clicked(self, button_id: int) -> None:
        from lunelis.ui import theme
        key = theme.CHOICES[button_id][0]
        if self._set("theme", key):
            self.theme_changed.emit(key)

    def _library_view(self) -> QFrame:
        from lunelis.ui.library import SORTS
        card, v = self._card("Library view", "How the library opens. Changing the sort or grid size in the "
                                             "library also updates these.")
        self.default_sort = QComboBox()
        for key, (label, _) in SORTS.items():
            self.default_sort.addItem(label, key)
        self.default_sort.currentIndexChanged.connect(
            lambda _: self._set("grid_default_sort", self.default_sort.currentData()) and self.view_changed.emit())
        self._row(v, "Sort by", self.default_sort)
        self.thumb_size = QComboBox()
        for label, px in self.THUMB_PRESETS:
            self.thumb_size.addItem(f"{label} ({px} px)", px)
        self.thumb_size.addItem("Custom…", None)
        self.thumb_size.currentIndexChanged.connect(self._thumb_preset)
        size = self._spin("grid_default_size", 100, 400, " px")
        size.valueChanged.connect(lambda _: None if self._loading else self.view_changed.emit())
        self._row(v, "Thumbnail size", self.thumb_size, size,
                  help="Medium fits about six columns on a 1440-pixel-wide window. Ctrl + mouse wheel in the "
                       "library changes it too.")
        self.show_videos = QCheckBox("Show videos in the library")
        self.show_videos.toggled.connect(lambda on: self._set("show_videos", on) and self.view_changed.emit())
        v.addWidget(self.show_videos)
        self.hover_info = QCheckBox("Show photo info when the mouse rests on a photo")
        self.hover_info.toggled.connect(lambda on: self._set("hover_info", on) and self.view_changed.emit())
        v.addWidget(self.hover_info)
        self.sidebar_auto = QCheckBox("Fold the sidebar to icons when the window is narrow")
        self.sidebar_auto.toggled.connect(lambda on: self._set("sidebar_auto", on) and self.view_changed.emit())
        v.addWidget(self.sidebar_auto)
        self.pair_raw = QCheckBox("Show a RAW+JPEG shot as one photo (stars, labels and albums go to both)")
        self.pair_raw.toggled.connect(lambda on: self._set("pair_raw_jpeg", on) and self._repair())
        v.addWidget(self.pair_raw)
        self.stack_bursts = QCheckBox("Stack burst shots into one photo with a frame count")
        self.stack_bursts.toggled.connect(lambda on: self._set("stack_bursts", on) and self._restack())
        v.addWidget(self.stack_bursts)
        gap = QDoubleSpinBox(minimum=0.1, maximum=5.0, singleStep=0.1, decimals=1, suffix=" s")
        gap.setMinimumWidth(110)
        gap.valueChanged.connect(lambda val: self._set("burst_gap_seconds", round(val, 1)) and self._restack())
        self._spins["burst_gap_seconds"] = gap
        self._row(v, "Frames at most", gap, help="apart are one burst (measured to the sub-second when the "
                                                  "camera records it; otherwise they must share a second).")
        frames = self._spin("burst_min_frames", 2, 50, " shots")
        frames.valueChanged.connect(lambda _: None if self._loading else self._restack())
        self._row(v, "A burst needs", frames, help="A RAW+JPEG pair counts as one shot.")
        return card

    def _repair(self) -> bool:
        from lunelis import pairs
        pairs.rebuild_from_settings(self.conn)
        self.view_changed.emit()
        return True

    def _restack(self) -> bool:
        from lunelis import stacks
        stacks.rebuild_from_settings(self.conn)
        self.view_changed.emit()
        return True

    def _thumb_preset(self) -> None:
        px = self.thumb_size.currentData()
        spin = self._spins["grid_default_size"]
        spin.setVisible(px is None)
        if px is not None and not self._loading:
            spin.setValue(px)                        # saves, and the library follows

    def _load_thumb_preset(self, s) -> None:
        px = s.get("grid_default_size")
        i = self.thumb_size.findData(px)
        self.thumb_size.setCurrentIndex(i if i >= 0 else self.thumb_size.count() - 1)
        self._spins["grid_default_size"].setVisible(i < 0)

    # --- General ----------------------------------------------------------------------------

    def _startup(self) -> QFrame:
        from lunelis.ui.photoinfo import DATE_FORMATS
        card, v = self._card("Start-up and behaviour")
        self.start_page = QComboBox()
        for label, key in (("The library", "Library"), ("Albums", "Albums"), ("Where I left off", "last")):
            self.start_page.addItem(label, key)
        self.start_page.currentIndexChanged.connect(lambda _: self._set("start_page", self.start_page.currentData()))
        self._row(v, "Open Lunelis on", self.start_page)
        self.wheel = QComboBox()
        self.wheel.addItem("Zooms in and out", "zoom")
        self.wheel.addItem("Goes to the next / previous photo", "step")
        self.wheel.currentIndexChanged.connect(
            lambda _: self._set("wheel_action", self.wheel.currentData()) and self.view_changed.emit())
        self._row(v, "In the photo view, the mouse wheel", self.wheel,
                  help="Whichever it isn't: the arrow keys, the filmstrip and tilting the wheel left / right "
                       "always change photo; Z and double-click always zoom.")
        self.date_fmt = QComboBox()
        for key, example in DATE_FORMATS.items():
            self.date_fmt.addItem(example, key)
        self.date_fmt.currentIndexChanged.connect(self._date_changed)
        self._row(v, "Dates look like", self.date_fmt)
        self.clock_24h = QCheckBox("24-hour clock (14:03 instead of 2:03 PM)")
        self.clock_24h.toggled.connect(self._clock_changed)
        v.addWidget(self.clock_24h)
        self.confirm_quit = QCheckBox("Ask before quitting while an export, merge, import or job is still running")
        self.confirm_quit.toggled.connect(lambda on: self._set("confirm_quit", on))
        v.addWidget(self.confirm_quit)
        return card

    def _clock_changed(self, on: bool) -> None:
        from lunelis.ui import photoinfo
        if self._set("clock_24h", on):
            photoinfo.set_clock_24h(on)
            self.view_changed.emit()

    def _date_changed(self) -> None:
        from lunelis.ui import photoinfo
        key = self.date_fmt.currentData()
        if self._set("date_format", key):
            photoinfo.set_date_format(key)
            self.view_changed.emit()

    # --- Edit -------------------------------------------------------------------------------

    def _editing(self) -> QFrame:
        card, v = self._card("Editing", "Edits never change your files: they're kept in the catalog and the "
                                        "photo's sidecar, and shown from a rendered copy in the cache.")
        self.edit_filter = QComboBox()
        self.edit_filter.currentIndexChanged.connect(lambda _: self._import_filter(self.edit_filter))
        self._row(v, "Start new photos with", self.edit_filter,
                  help="A filter every imported photo gets as its starting edit (same as on the Import tab).")
        self.live = QComboBox()
        self.live.addItem("Fast - half size while a slider moves", "fast")
        self.live.addItem("Sharp - full preview size all the time", "sharp")
        self.live.currentIndexChanged.connect(lambda _: self._set("edit_live_quality", self.live.currentData()))
        self._row(v, "While dragging a slider", self.live)
        unfold = QPushButton("Unfold every section", clicked=lambda: self._set("edit_sections_closed", []))
        self._row(v, "Edit panel", unfold, help="Sections you fold stay folded; this opens them all again.")
        v.addWidget(QLabel("Colour", objectName="SubTitle"))
        self.monitor = QComboBox()
        self.monitor.addItem("Off - show sRGB as it is", "off")
        self.monitor.addItem("Use Windows' display profile", "system")
        self.monitor.currentIndexChanged.connect(lambda _: self._set("monitor_profile", self.monitor.currentData()))
        self._row(v, "Monitor profile", self.monitor,
                  help="With a calibrated monitor, the photo view and Edit show colours through its profile "
                       "(Windows > Colour Management). Edits and exports are unaffected.")
        self.proof_label = QLabel(objectName="Count")
        self._row(v, "Soft-proof profile", self.proof_label,
                  QPushButton("Choose…", clicked=self._choose_proof),
                  QPushButton("Clear", clicked=lambda: (self._set("proof_profile", None), self._load_colour())),
                  help="A printer / paper profile (.icc) from your lab or printer maker. Edit > Proof then "
                       "shows how a photo will print, with colours it can't reproduce in magenta.")
        return card

    def _load_colour(self) -> None:
        s = Settings(self.conn)
        self.monitor.setCurrentIndex(max(0, self.monitor.findData(s.get("monitor_profile"))))
        proof = s.get("proof_profile")
        self.proof_label.setText(os.path.basename(proof) if proof else "None chosen")

    def _choose_proof(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, "Choose a printer or paper profile", "",
                                              "ICC profiles (*.icc *.icm)")
        if not path:
            return
        try:
            from PIL import ImageCms
            ImageCms.getOpenProfile(path)
        except Exception as e:
            self.saved.setText(f"Not saved: that isn't a colour profile ({e})")
            return
        self._set("proof_profile", path)
        self._load_colour()

    def _helpers(self) -> QFrame:
        card, v = self._card("Shoots, videos and the autopilot",
                             "What Lunelis does for you after a scan or an import - all of it only suggests, "
                             "or can be undone.")
        self.noticed_cb = QCheckBox("After each scan, look for brackets, panoramas, focus stacks and timelapses")
        self.noticed_cb.setToolTip("Suggestions wait on the Library status page; nothing is built by itself")
        self.noticed_cb.toggled.connect(lambda on: self._set("noticed_auto", on))
        v.addWidget(self.noticed_cb)
        self.log_look = QComboBox()
        self.log_look.addItem("Built-in look (to Rec.709)", "builtin")
        self.log_look.addItem("My own LUT…", "cube")
        self.log_look.addItem("Off - show as recorded", "off")
        self.log_look.currentIndexChanged.connect(self._log_look_changed)
        self.log_lut_label = QLabel(objectName="Count")
        self._row(v, "S-Log3 videos", self.log_look, self.log_lut_label,
                  help="Sony S-Log3 clips look flat and grey until they're graded. Lunelis shows them through a "
                       "look in the player and the thumbnails - the files are never changed. A .cube LUT of your "
                       "own should take S-Log3 as its input.")
        v.addWidget(QLabel("Autopilot after an import (tick Autopilot on the Import page)", objectName="SubTitle"))
        from lunelis.importing.autopilot import STAGES, TITLES
        self.autopilot_stages: dict[str, QCheckBox] = {}
        for stage in STAGES:
            cb = QCheckBox(TITLES[stage])
            cb.toggled.connect(lambda _on: self._set("autopilot_skip",
                                                      [k for k, b in self.autopilot_stages.items() if not b.isChecked()]))
            self.autopilot_stages[stage] = cb
            v.addWidget(cb)
        return card

    def _load_helpers(self) -> None:
        skip = set(Settings(self.conn).get("autopilot_skip"))
        for stage, cb in self.autopilot_stages.items():
            cb.setChecked(stage not in skip)

    def _scene_tags(self) -> QFrame:
        card, v = self._card("Scene tags", "A model that runs only on this PC looks at your photos' thumbnails "
                                           "and suggests what's in them - Scene > Beach, Scene > Food... You "
                                           "review the suggestions; only the ones you accept become tags.")
        from lunelis.recognize import clip
        self.scene_status = QLabel(objectName="Help")
        self.scene_status.setWordWrap(True)
        self.scene_b = QPushButton(clicked=self._scene_model_action)
        self._row(v, f"Scene model ({clip.TOTAL / 1e6:,.0f} MB, CLIP from Hugging Face)", self.scene_status,
                  self.scene_b)
        self.scene_auto = QCheckBox("Look at new photos after each scan")
        self.scene_auto.toggled.connect(lambda on: self._set("scene_tags_auto", on))
        v.addWidget(self.scene_auto)
        self.scene_accept, self.scene_threshold = self._auto_row(
            v, "Accept a suggestion by itself when the model is at least", "scene_auto_accept",
            "scene_auto_threshold", "Off: every suggestion waits for you on the Tags page.")
        row = QHBoxLayout()
        self.scene_job_b = QPushButton("Tag the library…", clicked=lambda: self.scene_job.emit())
        self.scene_job_b.setToolTip("A background job: pausable, and runs only when you choose (Jobs, Ctrl+J)")
        row.addWidget(self.scene_job_b)
        row.addWidget(QPushButton("Review suggestions", clicked=lambda: self.open_suggestions.emit()))
        row.addWidget(QPushButton("Edit the labels…", clicked=self._edit_scene_labels))
        row.addStretch(1)
        v.addLayout(row)
        return card

    def _auto_row(self, v: QVBoxLayout, text: str, key: str, threshold_key: str, help: str):
        """A "do it by itself above N %" checkbox with its percentage."""
        cb = QCheckBox(text)
        pct = QSpinBox()
        pct.setRange(30, 99)
        pct.setSuffix(" % sure")
        cb.toggled.connect(lambda on: (self._set(key, on), pct.setEnabled(on)))
        pct.valueChanged.connect(lambda n: self._set(threshold_key, n / 100))
        row = QHBoxLayout()
        row.addWidget(cb)
        row.addWidget(pct)
        row.addStretch(1)
        v.addLayout(row)
        hl = QLabel(help, objectName="Help")
        hl.setWordWrap(True)
        v.addWidget(hl)
        return cb, pct

    def _faces(self) -> QFrame:
        card, v = self._card("Faces", "Two small models that run only on this PC find the faces in your photos and "
                                      "group the ones that look alike. Name a face once and Lunelis suggests that "
                                      "person in other photos; each photo with a named face gets a People tag. "
                                      "Nothing is sent anywhere.")
        from lunelis.recognize import faces
        self.faces_status = QLabel(objectName="Help")
        self.faces_status.setWordWrap(True)
        self.faces_b = QPushButton(clicked=self._faces_model_action)
        self._row(v, f"Face models ({faces.TOTAL / 1e6:,.0f} MB, YuNet and SFace from OpenCV)", self.faces_status,
                  self.faces_b)
        self.faces_auto = QCheckBox("Look for faces in new photos after each scan")
        self.faces_auto.toggled.connect(lambda on: self._set("faces_auto", on))
        v.addWidget(self.faces_auto)
        self.faces_confirm, self.faces_threshold = self._auto_row(
            v, "Name a face by itself when it's at least", "faces_auto_confirm", "faces_auto_threshold",
            "Off: a likely match shows as \"Ann?\" and waits on the People page until you say yes. "
            "On: sure matches are named (and tagged) straight away; fix any mistake on the People page.")
        row = QHBoxLayout()
        self.faces_job_b = QPushButton("Find faces in the library…", clicked=lambda: self.faces_job.emit())
        self.faces_job_b.setToolTip("A background job: pausable, and runs only when you choose (Jobs, Ctrl+J)")
        row.addWidget(self.faces_job_b)
        row.addWidget(QPushButton("Open the People page", clicked=lambda: self.open_people.emit()))
        row.addStretch(1)
        v.addLayout(row)
        return card

    def _places(self) -> QFrame:
        card, v = self._card("Places", "Photos with a location (from the camera, or a pin you dropped on the Map) "
                                       "get a place tag - Places > Italy > Lazio > Rome - from a list of 32,000 "
                                       "towns built into Lunelis. Nothing is looked up online. Far from any town "
                                       "it's Places > Italy > Unknown, at sea Places > Unknown.")
        self.places_auto = QCheckBox("Give photos a place tag after each scan")
        self.places_auto.toggled.connect(lambda on: self._set("places_auto", on))
        v.addWidget(self.places_auto)
        self.places_none = QCheckBox("Tag photos without any location Places > No location")
        self.places_none.setToolTip("Handy for finding the photos that still need a pin. Off by default: on a "
                                    "library from cameras without GPS it tags most photos.")
        self.places_none.toggled.connect(self._places_none_changed)
        v.addWidget(self.places_none)
        self.map_online = QCheckBox("Show map pictures on the Map (from OpenStreetMap, over the internet)")
        self.map_online.setToolTip("Only the map tile numbers are asked for - nothing about your photos is sent. "
                                   "Off: the Map shows the dots on a plain grid.")
        self.map_online.toggled.connect(lambda on: self._set("map_online", on))
        v.addWidget(self.map_online)
        self.location_opens = QComboBox()
        self.location_opens.addItem("Lunelis's Map", "map")
        self.location_opens.addItem("OpenStreetMap in the browser", "browser")
        self.location_opens.currentIndexChanged.connect(
            lambda _: self._set("location_opens", self.location_opens.currentData()))
        self._row(v, "A photo's location in Info opens", self.location_opens)
        row = QHBoxLayout()
        row.addWidget(QPushButton("Name places now", clicked=self._name_places))
        self.places_status = QLabel(objectName="Help")
        row.addWidget(self.places_status, 1)
        v.addLayout(row)
        hl = QLabel("Place names: GeoNames (geonames.org), CC BY 4.0.", objectName="Help")
        v.addWidget(hl)
        return card

    def _load_places(self) -> None:
        s = Settings(self.conn)
        self.places_auto.setChecked(bool(s.get("places_auto")))
        self.places_none.setChecked(bool(s.get("places_tag_no_location")))
        self.map_online.setChecked(bool(s.get("map_online")))
        self.location_opens.setCurrentIndex(max(0, self.location_opens.findData(s.get("location_opens"))))

    def _places_none_changed(self, on: bool) -> None:
        if self._set("places_tag_no_location", on):
            self._name_places()

    def _name_places(self) -> None:
        from lunelis.geo import places
        n = places.tag_pending(self.conn)
        self.places_status.setText(f"{n:,} photo{'s' if n != 1 else ''} tagged or updated" if n else "All up to date")

    def _load_faces(self) -> None:
        from lunelis.recognize import faces
        s = Settings(self.conn)
        have = faces.available()
        self.faces_b.setText("Remove" if have else "Download and turn on")
        for w in (self.faces_job_b, self.faces_auto, self.faces_confirm):
            w.setEnabled(have)
        self.faces_auto.setChecked(bool(s.get("faces_auto")))
        self.faces_confirm.setChecked(bool(s.get("faces_auto_confirm")))
        self.faces_threshold.setValue(round(float(s.get("faces_auto_threshold")) * 100))
        self.faces_threshold.setEnabled(have and bool(s.get("faces_auto_confirm")))
        if not have:
            self.faces_status.setText("Off")
            return
        c = faces.counts(self.conn)
        self.faces_status.setText(f"On - {c['scanned']:,} photos looked at, {c['faces']:,} faces, "
                                  f"{c['people']:,} people, {c['waiting']:,} waiting for a yes")

    def _faces_model_action(self) -> None:
        from lunelis.recognize import faces
        if faces.available():
            if QMessageBox.question(self, "Remove the face models", "Remove the face models? Names you've given "
                                    "and People tags stay; new photos aren't looked at until you download them "
                                    "again.") == QMessageBox.StandardButton.Yes:
                faces.remove()
                self._set("faces_auto", False)
            self._load_faces()
            return
        if QMessageBox.question(
                self, "Turn on faces",
                f"Download the face models ({faces.TOTAL / 1e6:,.0f} MB, from OpenCV's model collection on "
                "GitHub)?\n\nThey run only on this PC; your photos and faces never leave it. Each file is checked "
                "against its known fingerprint before it's used.") != QMessageBox.StandardButton.Yes:
            return
        from PySide6.QtCore import QRunnable, QThreadPool
        from PySide6.QtWidgets import QProgressDialog
        dlg = QProgressDialog("Downloading the face models…", "Cancel", 0, 100, self)
        dlg.setWindowTitle("Faces")
        dlg.setMinimumDuration(0)

        class Job(QRunnable):
            def __init__(self, sig):
                super().__init__()
                self.sig, self.stop = sig, False

            def cancel(self):
                self.stop = True

            def run(self):
                try:
                    faces.download(lambda d, t: self.sig.progress.emit(d, t), lambda: self.stop)
                    self.sig.downloaded.emit("faces", True, "")
                except Exception as e:
                    self.sig.downloaded.emit("faces", False, str(e))
        from lunelis.ui.develop import _AiSignals
        self._faces_sig = sig = _AiSignals()
        job = Job(sig)
        queued = Qt.ConnectionType.QueuedConnection
        sig.progress.connect(lambda d, t: dlg.setValue(int(d * 100 / max(1, t))), queued)

        def done(_k, ok, message):
            dlg.close()
            self._faces_sig = None
            if not ok and message != "cancelled":
                QMessageBox.warning(self, "Faces", f"The download didn't work: {message}")
            elif ok:
                self._set("faces_auto", True)
            self._load_faces()
        sig.downloaded.connect(done, queued)
        dlg.canceled.connect(job.cancel)
        QThreadPool.globalInstance().start(job)

    def _load_scene_tags(self) -> None:
        from lunelis.recognize import clip
        have = clip.available()
        self.scene_b.setText("Remove" if have else "Download and turn on")
        self.scene_job_b.setEnabled(have)
        self.scene_auto.setEnabled(have)
        self.scene_auto.setChecked(bool(Settings(self.conn).get("scene_tags_auto")))
        self.scene_accept.setEnabled(have)
        self.scene_accept.setChecked(bool(Settings(self.conn).get("scene_auto_accept")))
        self.scene_threshold.setValue(round(float(Settings(self.conn).get("scene_auto_threshold")) * 100))
        self.scene_threshold.setEnabled(have and bool(Settings(self.conn).get("scene_auto_accept")))
        if not have:
            self.scene_status.setText("Off")
            return
        n, s = self.conn.execute(
            "SELECT (SELECT COUNT(*) FROM embeddings), (SELECT COUNT(*) FROM file_tags WHERE confidence IS NOT NULL)"
        ).fetchone()
        self.scene_status.setText(f"On - {n:,} photos looked at, {s:,} suggestions waiting")

    def _scene_model_action(self) -> None:
        from lunelis.recognize import clip
        if clip.available():
            if QMessageBox.question(self, "Remove the scene model", "Remove the scene model? Suggestions already "
                                    "made stay; accepted tags are yours either way.") == QMessageBox.StandardButton.Yes:
                clip.remove()
            self._load_scene_tags()
            return
        if QMessageBox.question(
                self, "Turn on scene tags",
                f"Download the scene model ({clip.TOTAL / 1e6:,.0f} MB, OpenAI's CLIP, from Hugging Face)?\n\n"
                "It runs only on this PC and reads your photos' thumbnails - nothing is sent anywhere. Each file is "
                "checked against its known fingerprint before it's used.") != QMessageBox.StandardButton.Yes:
            return
        from PySide6.QtCore import QThreadPool, QRunnable
        from PySide6.QtWidgets import QProgressDialog
        dlg = QProgressDialog("Downloading the scene model…", "Cancel", 0, 100, self)
        dlg.setWindowTitle("Scene tags")
        dlg.setMinimumDuration(0)

        class Job(QRunnable):
            def __init__(self, sig):
                super().__init__()
                self.sig, self.stop = sig, False

            def cancel(self):
                self.stop = True

            def run(self):
                try:
                    clip.download(lambda d, t: self.sig.progress.emit(d, t), lambda: self.stop)
                    self.sig.downloaded.emit("scene", True, "")
                except Exception as e:
                    self.sig.downloaded.emit("scene", False, str(e))
        from lunelis.ui.develop import _AiSignals
        self._scene_sig = sig = _AiSignals()
        job = Job(sig)
        queued = Qt.ConnectionType.QueuedConnection
        sig.progress.connect(lambda d, t: dlg.setValue(int(d * 100 / max(1, t))), queued)

        def done(_k, ok, message):
            dlg.close()
            self._scene_sig = None
            if not ok and message != "cancelled":
                QMessageBox.warning(self, "Scene tags", f"The download didn't work: {message}")
            elif ok:
                self._set("scene_tags_auto", True)
            self._load_scene_tags()
        sig.downloaded.connect(done, queued)
        dlg.canceled.connect(job.cancel)
        QThreadPool.globalInstance().start(job)

    def _edit_scene_labels(self) -> None:
        import shutil
        from lunelis.recognize import scenes
        path = paths.DATA_DIR / scenes.USER_FILE
        if not path.exists():
            shutil.copyfile(scenes.BUILTIN, path)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _ai_models(self) -> QFrame:
        card, v = self._card("AI models", "Subject and Sky masks use small free models that run only on this "
                                          "PC. Each is downloaded the first time you add that kind of mask.")
        self.ai_rows: dict[str, tuple[QLabel, QPushButton]] = {}
        from lunelis.edit import ai
        for kind, m in ai.MODELS.items():
            status = QLabel(objectName="Help")
            b = QPushButton(clicked=lambda _=False, k=kind: self._ai_action(k))
            self._row(v, f"{kind.title()} ({m.size / 1e6:,.0f} MB, {m.source})", status, b)
            self.ai_rows[kind] = (status, b)
        return card

    def _load_ai(self) -> None:
        from lunelis.edit import ai
        for kind, (status, b) in self.ai_rows.items():
            have = ai.available(kind)
            status.setText("Installed" if have else "Not downloaded")
            b.setText("Remove" if have else "Download")

    def _ai_action(self, kind: str) -> None:
        from lunelis.edit import ai
        if ai.available(kind):
            if QMessageBox.question(self, "Remove model", f"Remove the {kind} model? It's downloaded again "
                                    "the next time you add that kind of mask.") == QMessageBox.StandardButton.Yes:
                ai.model_path(kind).unlink(missing_ok=True)
                ai._SESSIONS.pop(kind, None)
        else:
            from PySide6.QtWidgets import QProgressDialog
            m = ai.MODELS[kind]
            if QMessageBox.question(self, "Download model", f"Download the {kind} model ({m.size / 1e6:,.0f} MB "
                                    f"from {m.source})?") != QMessageBox.StandardButton.Yes:
                return
            # Downloaded on a pool thread (the same job the photo view uses); the
            # dialog's Cancel reaches it directly (see CLAUDE.md, Cancel gotcha).
            from PySide6.QtCore import QThreadPool
            from lunelis.ui.develop import _AiJob, _AiSignals
            dlg = QProgressDialog(f"Downloading the {kind} model…", "Cancel", 0, 100, self)
            dlg.setWindowTitle("Download model")
            dlg.setMinimumDuration(0)
            self._ai_dl = sig = _AiSignals()             # kept alive until the job reports back
            job = _AiJob(sig, "download", kind)
            queued = Qt.ConnectionType.QueuedConnection       # emitted on the pool thread
            sig.progress.connect(lambda d, t: dlg.setValue(int(d * 100 / max(1, t))), queued)

            def done(_kind: str, ok: bool, message: str) -> None:
                dlg.close()
                self._ai_dl = None
                if not ok and message != "cancelled":
                    QMessageBox.warning(self, "Download model", f"The download didn't work: {message}")
                self._load_ai()
            sig.downloaded.connect(done, queued)
            dlg.canceled.connect(job.cancel)
            QThreadPool.globalInstance().start(job)
            return
        self._load_ai()

    def _edit_caches(self) -> QFrame:
        card, v = self._card("Caches", "Made again from your edits whenever they're needed - deleting them "
                                       "loses nothing.")
        self.cache_info: dict[str, QLabel] = {}
        for key, label in (("edits", "Rendered edits"), ("masks", "Subject and sky masks")):
            info = QLabel(objectName="Help")
            b = QPushButton("Clear", clicked=lambda _=False, k=key: self._clear_cache(k))
            self._row(v, label, info, b)
            self.cache_info[key] = info
        return card

    def _cache_dir(self, key: str):
        return paths.DATA_DIR / "cache" / key

    def _load_caches(self, stale_after: float = 0) -> None:
        import time
        if stale_after and time.monotonic() - getattr(self, "_caches_at", -1e9) < stale_after:
            return                                       # a toggle's refresh: the sizes haven't changed
        self._caches_at = time.monotonic()
        # The thumbnail cache alone is ~160k files: walked on a worker, once per
        # refresh burst (Background coalesces), never on the GUI thread.
        dirs = {key: self._cache_dir(key) for key in self.cache_info}

        def walk():
            out = {}
            for key, d in dirs.items():
                n = size = 0
                for root, _dirs, files in os.walk(d):
                    for f in files:
                        try:
                            size += os.stat(os.path.join(root, f)).st_size
                            n += 1
                        except OSError:
                            pass
                out[key] = (n, size)
            return out

        def show(sizes):
            for key, (n, size) in sizes.items():
                self.cache_info[key].setText(f"{n:,} file{'s' if n != 1 else ''} · {size / 1e6:,.1f} MB")

        for info in self.cache_info.values():
            if not info.text():
                info.setText("Counting…")
        self._bg().run("caches", walk, show, db=False)

    def _bg(self):
        if not hasattr(self, "bg"):
            from lunelis.ui.background import Background
            self.bg = Background(self, self.conn)
        return self.bg

    def _clear_cache(self, key: str) -> None:
        import shutil
        d = self._cache_dir(key)
        if d.exists() and QMessageBox.question(self, "Clear cache", f"Delete {d}? It's only Lunelis's own cache "
                                               "- it's made again when needed.") == QMessageBox.StandardButton.Yes:
            shutil.rmtree(d, ignore_errors=True)
        self._load_caches()

    def _thumbnails(self) -> QFrame:
        card, v = self._card("Thumbnails", "512-pixel previews Lunelis makes from each photo's own embedded "
                                           "preview. They're a cache: deleting them loses nothing, they're just "
                                           "made again.")
        self.thumb_info = QLabel(objectName="Help")
        v.addWidget(self.thumb_info)
        row = QHBoxLayout()
        row.addSpacing(0)
        row.addWidget(QPushButton("Rebuild all thumbnails…", clicked=self._rebuild_thumbnails))
        row.addWidget(QPushButton("Open the folder", clicked=lambda: self._open(paths.THUMBNAIL_CACHE)))
        row.addStretch(1)
        v.addLayout(row)
        return card

    def _load_log_look(self) -> None:
        choice = Settings(self.conn).get("log_preview")
        key = "cube" if choice.startswith("cube:") else choice
        self.log_look.setCurrentIndex(max(0, self.log_look.findData(key)))
        self.log_lut_label.setText(os.path.basename(choice[5:]) if key == "cube" else "")

    def _log_look_changed(self, _i: int) -> None:
        if self._loading:
            return
        key = self.log_look.currentData()
        old = Settings(self.conn).get("log_preview")
        if key == "cube":
            from PySide6.QtWidgets import QFileDialog
            path, _ = QFileDialog.getOpenFileName(self, "Choose a .cube LUT", "", "3D LUT (*.cube)")
            if not path:
                self._loading = True
                self._load_log_look()               # cancelled: back to what it was
                self._loading = False
                return
            from lunelis.video.lut import LutError, load_cube
            try:
                load_cube(path)
            except LutError as e:
                self.saved.setText(f"Not saved: {e}")
                self._loading = True
                self._load_log_look()
                self._loading = False
                return
            choice = "cube:" + path
        else:
            choice = key
        if choice == old or not self._set("log_preview", choice):
            return
        self._load_log_look()
        self.saved.setText("Remaking the thumbnails of S-Log3 clips…")

        def clear(conn):
            from lunelis.video import slog
            return slog.refresh_thumbnails(conn)

        def done(n):
            self.saved.setText(f"{n:,} S-Log3 clip thumbnail{'s' if n != 1 else ''} will be made again"
                               if n else "Saved - no S-Log3 clips in the library yet")
            if n:
                self.library_changed.emit()
                self.rescan.emit([r[0] for r in self.conn.execute("SELECT id FROM roots WHERE enabled = 1")])
        self._bg().run("log_look", clear, done)

    def _load_thumbnails(self) -> None:
        n, missing = self.conn.execute(
            "SELECT COALESCE(SUM(thumbnail_path IS NOT NULL), 0),"
            " COALESCE(SUM(thumbnail_path IS NULL AND thumb_error IS NULL), 0) FROM files"
            " WHERE missing_since IS NULL").fetchone()
        self.thumb_info.setText(f"{n:,} thumbnails in {paths.THUMBNAIL_CACHE}"
                                + (f" · {missing:,} still to make" if missing else ""))

    def _rebuild_thumbnails(self) -> None:
        if QMessageBox.question(
                self, "Rebuild thumbnails?",
                "Delete every thumbnail and make them again from your photos?\n\nOnly Lunelis's own cache is "
                "deleted - never a photo. With a large NAS library this takes a while.") \
                != QMessageBox.StandardButton.Yes:
            return
        # Renaming is instant; the old folder (~5 GB for a big library) is deleted
        # in the background instead of freezing the window.
        import shutil
        import threading
        import time
        old = paths.THUMBNAIL_CACHE.with_name(f"thumbnails.old-{int(time.time())}")
        try:
            paths.THUMBNAIL_CACHE.rename(old)
            threading.Thread(target=shutil.rmtree, args=(old,), kwargs={"ignore_errors": True},
                             daemon=True).start()
        except OSError:
            shutil.rmtree(paths.THUMBNAIL_CACHE, ignore_errors=True)
        paths.THUMBNAIL_CACHE.mkdir(parents=True, exist_ok=True)
        self.conn.execute("UPDATE files SET thumbnail_path = NULL, thumb_error = NULL")
        self.conn.commit()
        self.saved.setText("Thumbnails cleared - making them again")
        self.library_changed.emit()
        self.rescan.emit([r[0] for r in self.conn.execute("SELECT id FROM roots WHERE enabled = 1")])
        self.refresh()

    def _updates(self) -> QFrame:
        from PySide6.QtWidgets import QProgressBar, QTextBrowser
        from lunelis import updater
        card, v = self._card("Updates", "New versions come from the public Lunelis releases page. Updating "
                                        "replaces only the program - your library, settings and thumbnails live "
                                        "in the data folder and are never touched. Each download is checked "
                                        "against its published checksum before it's installed.")
        self.version_label = QLabel()
        self.version_label.setObjectName("SectionTitle")
        v.addWidget(self.version_label)
        self.update_status = QLabel(objectName="Help")
        self.update_status.setWordWrap(True)
        v.addWidget(self.update_status)
        self.update_notes = QTextBrowser()
        self.update_notes.setOpenExternalLinks(False)
        self.update_notes.setOpenLinks(False)
        # Release notes come from GitHub: only web links open (never a file or a network share).
        self.update_notes.anchorClicked.connect(
            lambda url: QDesktopServices.openUrl(url) if url.scheme() == "https" else None)
        self.update_notes.setMaximumHeight(220)
        self.update_notes.hide()
        v.addWidget(self.update_notes)
        self.update_bar = QProgressBar()
        self.update_bar.hide()
        v.addWidget(self.update_bar)
        row = QHBoxLayout()
        self.check_b = QPushButton("Check for updates", clicked=self.check_updates)
        self.install_b = QPushButton(clicked=self._download_update)
        self.install_b.setObjectName("Primary")
        self.install_b.hide()
        self.skip_b = QPushButton("Skip this version", clicked=self._skip_update)
        self.skip_b.hide()
        page_b = QPushButton("Releases page", clicked=lambda: __import__("webbrowser").open(updater.PAGE))
        for b in (self.check_b, self.install_b, self.skip_b, page_b):
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        self.auto_update = QCheckBox("Check for updates when Lunelis starts (once a day)")
        self.auto_update.toggled.connect(lambda on: self._set("update_check", on))
        v.addWidget(self.auto_update)
        self._release = None
        return card

    # --- updates ------------------------------------------------------------------------------

    def check_updates(self) -> None:
        from lunelis import updater
        self.check_b.setEnabled(False)
        self.update_status.setText("Checking…")
        self._run_update_task(lambda: updater.check(), self._checked)

    def _run_update_task(self, fn, done) -> None:
        self._utask = _UpdateTask(fn)
        self._uthread = QThread(self)
        self._utask.moveToThread(self._uthread)
        self._uthread.started.connect(self._utask.run)
        self._utask.progress.connect(self._update_progress)
        self._update_done = done
        self._utask.done.connect(self._update_task_done)     # bound method: handled on the GUI thread
        self._uthread.start()

    def _update_task_done(self, result) -> None:
        self._uthread.quit()
        self._uthread.wait()
        self._uthread = None
        self._update_done(result)

    def _update_progress(self, done: int, total: int) -> None:
        self.update_bar.show()
        self.update_bar.setMaximum(max(1, total))
        self.update_bar.setValue(done)
        self.update_bar.setFormat(f"{done / 1e6:,.0f} of {total / 1e6:,.0f} MB")

    def _checked(self, result) -> None:
        from datetime import datetime, timezone
        from lunelis import updater
        self.check_b.setEnabled(True)
        if isinstance(result, Exception):
            self.update_status.setText(str(result))
            return
        Settings(self.conn).set("update_last_check", datetime.now(tz=timezone.utc).isoformat(timespec="seconds"))
        self.show_release(result)

    def show_release(self, rel) -> None:
        from lunelis import paths, updater
        self._release = rel
        if not updater.is_newer(rel.version, paths.version()):
            self.update_status.setText(f"You're up to date - {rel.version} is the newest version.")
            self.install_b.hide()
            self.skip_b.hide()
            self.update_notes.hide()
            return
        self.update_status.setText(f"Lunelis {rel.version} is available ({rel.size / 1e6:,.0f} MB).")
        from lunelis.updater import safe_markdown
        self.update_notes.setMarkdown(safe_markdown(rel.notes) or "(no release notes)")
        self.update_notes.show()
        if paths.FROZEN:
            self.install_b.setText(f"Download and install {rel.version}")
            self.install_b.show()
        else:
            self.update_status.setText(self.update_status.text() + " You're running from source - update with "
                                       "git pull.")
        self.skip_b.show()

    def _skip_update(self) -> None:
        if self._release:
            self._set("update_skip_version", self._release.version)
            self.update_status.setText(f"Skipping {self._release.version} - you'll hear about the next one.")
            self.install_b.hide()
            self.skip_b.hide()

    def _download_update(self) -> None:
        from lunelis import updater
        rel = self._release
        if rel is None:
            return
        self.install_b.setEnabled(False)
        self.check_b.setEnabled(False)
        self.update_status.setText(f"Downloading {rel.version}…")

        def work(progress):
            z = updater.download(rel, on_progress=progress)
            return updater.stage(z)
        self._run_update_task(work, self._downloaded)

    def _downloaded(self, result) -> None:
        self.check_b.setEnabled(True)
        self.install_b.setEnabled(True)
        self.update_bar.hide()
        if isinstance(result, Exception):
            self.update_status.setText(f"The update didn't download: {result}")
            return
        if QMessageBox.question(
                self, "Install the update?",
                f"Lunelis {self._release.version} is downloaded and checked. Restart now to install it?\n\n"
                "Lunelis closes, swaps in the new version and opens again. Anything running in the background "
                "(a scan, a job) carries on after the restart.") == QMessageBox.StandardButton.Yes:
            self.install_update.emit(result)
        else:
            self.update_status.setText("Downloaded - press Download and install again when you're ready.")

    def _logs(self) -> QFrame:
        from lunelis import log
        card, v = self._card("Log and problem reports",
                             "Lunelis writes what it does - and every error - to a log in the data folder, so a "
                             "problem can be looked at afterwards.")
        self._row(v, "Log", QLabel(str(log.log_file())))
        row = QHBoxLayout()
        row.addSpacing(208)
        row.addWidget(QPushButton("Open the log folder", clicked=lambda: self._open(log.log_dir())))
        row.addWidget(QPushButton("Report a problem…", clicked=lambda: self.report_problem.emit()))
        row.addStretch(1)
        v.addLayout(row)
        return card

    # --- building blocks -----------------------------------------------------------

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

    @staticmethod
    def _row(v: QVBoxLayout, label: str, *widgets, help: str | None = None) -> QLabel | None:
        h = QHBoxLayout()
        h.setSpacing(8)
        lab = QLabel(label)
        lab.setFixedWidth(200)
        lab.setWordWrap(True)
        h.addWidget(lab, 0, Qt.AlignmentFlag.AlignTop)
        for w in widgets:
            h.addWidget(w, 1 if isinstance(w, (QLineEdit, QComboBox)) else 0)
        if not any(isinstance(w, (QLineEdit, QComboBox)) for w in widgets):
            h.addStretch(1)
        v.addLayout(h)
        if help:
            hl = QLabel(help, objectName="Help")
            hl.setWordWrap(True)
            hl.setContentsMargins(208, 0, 0, 0)
            v.addWidget(hl)
            return hl
        return None

    @staticmethod
    def _path_field(placeholder: str = "") -> QLineEdit:
        e = QLineEdit(readOnly=True, placeholderText=placeholder)
        e.setObjectName("PathField")
        return e

    def _spin(self, key: str, lo: int, hi: int, suffix: str = "", special: str | None = None) -> QSpinBox:
        s = QSpinBox(minimum=lo, maximum=hi)
        s.setSuffix(suffix)
        if special:
            s.setSpecialValueText(special)
        s.setMinimumWidth(110)
        s.valueChanged.connect(lambda v, k=key: self._set(k, v))
        self._spins[key] = s
        return s

    def _import_filter(self, combo) -> None:
        """"Start new photos with" is on both the Edit and Import tabs: one setting."""
        if not self._set("import_filter", combo.currentData()):
            return
        for other in (self.edit_filter, self.import_filter):
            if other is not combo:
                other.blockSignals(True)
                other.setCurrentIndex(max(0, other.findData(combo.currentData())))
                other.blockSignals(False)

    def _set(self, key: str, value) -> bool:
        if self._loading:
            return False
        try:
            Settings(self.conn).set(key, value)
        except (ValueError, TemplateError) as e:
            self.saved.setText(f"Not saved: {e}")
            return False
        self.saved.setText("Saved")
        return True

    # --- sections ----------------------------------------------------------------------

    def _sources(self) -> QFrame:
        card, v = self._card(
            "Sources",
            "The folders Lunelis catalogs. Turning a source off hides its photos and stops it being "
            "scanned; nothing on disk or in the catalog is deleted, and turning it back on brings "
            "everything back, ratings included.")
        self.roots = QTableWidget(0, 4)
        self.roots.setHorizontalHeaderLabels(["On", "Folder", "Photos & videos", "Last scanned"])
        self.roots.verticalHeader().hide()
        self.roots.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.roots.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.roots.setWordWrap(False)
        self.roots.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        hh = self.roots.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for c in (0, 2, 3):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.roots.itemChanged.connect(self._root_toggled)
        v.addWidget(self.roots)
        buttons = QHBoxLayout()
        buttons.addWidget(QPushButton("Add a folder…", clicked=lambda: self.add_source.emit()))
        self.remove_source_b = QPushButton("Remove a source…", clicked=self._remove_source)
        self.remove_source_b.setToolTip("Take the selected source out of Lunelis - the folder and its photos "
                                        "on disk are not touched")
        buttons.addWidget(self.remove_source_b)
        buttons.addWidget(QPushButton("Skip a folder inside a source…", clicked=self._skip_folder))
        buttons.addStretch(1)
        v.addLayout(buttons)

        v.addWidget(QLabel("Skipped folders", objectName="SubTitle"))
        help = QLabel("Folders Lunelis leaves alone - export folders, editing caches, anything you don't "
                      "want in the library. Files already cataloged there are hidden, with their ratings "
                      "kept in case you stop skipping the folder.", objectName="Help")
        help.setWordWrap(True)
        v.addWidget(help)
        self.skipped = QListWidget()
        self.skipped.setMaximumHeight(120)
        v.addWidget(self.skipped)
        row = QHBoxLayout()
        self.unskip_b = QPushButton("Stop skipping", clicked=self._unskip_folder)
        row.addWidget(self.unskip_b)
        row.addStretch(1)
        v.addLayout(row)
        return card

    def _import(self) -> QFrame:
        card, v = self._card(
            "Importing from memory cards",
            "File names are never changed - the folder template only chooses which folder each photo "
            "goes into. A different photo with a name that's already taken goes into a sibling folder, "
            "such as \"6-19-2026 (2)\".")
        self.dest = self._path_field()
        self._row(v, "Import into", self.dest,
                  QPushButton("Change…", clicked=lambda: self._pick_folder(
                      "import_destination", "Import into which library folder?")))
        self.template = QComboBox()
        self.template.setEditable(True)
        for label, t in PRESETS.items():
            self.template.addItem(t)
            self.template.setItemData(self.template.count() - 1, label, Qt.ItemDataRole.ToolTipRole)
        self.template.currentTextChanged.connect(self._template_changed)
        self.template_help = self._row(
            v, "Folders", self.template,
            help="Tokens: {YYYY} {YY} {M} {MM} {D} {DD} {month_name} {date} {camera} {import_name} "
                 "{import_date} {original_folder}. [square brackets] are left out when the import has no name.")
        self.template_example = QLabel(objectName="Example")
        self.template_example.setContentsMargins(208, 0, 0, 0)
        v.addWidget(self.template_example)

        self.staging_local = self._path_field()
        self._row(v, "Staging folder on this PC", self.staging_local,
                  QPushButton("Change…", clicked=lambda: self._pick_folder(
                      "import_staging_local", "Stage card copies in which local folder?")),
                  QPushButton("Default", clicked=lambda: self._reset("import_staging_local")),
                  help="Cards are copied here first (fast, so the card is free quickly), checked, "
                       "then filed into the library.")
        self._row(v, "Keep this much free", self._spin("import_local_reserve_gb", 0, 10_000, " GB"),
                  help="When staging would take the local drive below this, the rest of the card goes "
                       "to the network staging folder instead.")
        self.staging_net = self._path_field("None - pause the import when the local drive is full")
        self._row(v, "Network staging folder", self.staging_net,
                  QPushButton("Change…", clicked=self._pick_network_staging),
                  QPushButton("None", clicked=lambda: (self._set("import_staging_network", None),
                                                       self.refresh())),
                  help="Must be outside every source, so half-imported files are never cataloged.")
        self.import_filter = QComboBox()
        self.import_filter.currentIndexChanged.connect(lambda _: self._import_filter(self.import_filter))
        self._row(v, "Start new photos with", self.import_filter,
                  help="A filter every imported photo gets, as a starting point. It's an edit like any "
                       "other: change or reset it per photo; the files themselves are never touched.")
        return card

    def _load_startup(self, s) -> None:
        self.start_page.setCurrentIndex(max(0, self.start_page.findData(s.get("start_page"))))
        self.wheel.setCurrentIndex(max(0, self.wheel.findData(s.get("wheel_action"))))
        self.date_fmt.setCurrentIndex(max(0, self.date_fmt.findData(s.get("date_format"))))
        self.clock_24h.setChecked(bool(s.get("clock_24h")))
        self.confirm_quit.setChecked(bool(s.get("confirm_quit")))
        self.live.setCurrentIndex(max(0, self.live.findData(s.get("edit_live_quality"))))
        self._load_colour()
        self.edit_filter.clear()
        for i in range(self.import_filter.count()):
            self.edit_filter.addItem(self.import_filter.itemText(i), self.import_filter.itemData(i))
        self.edit_filter.setCurrentIndex(max(0, self.edit_filter.findData(s.get("import_filter"))))

    def _load_import_filter(self, s) -> None:
        from lunelis.edit import store
        self.import_filter.clear()
        self.import_filter.addItem("No filter", None)
        for name, _params, builtin in store.filters(self.conn):
            self.import_filter.addItem(name if builtin else f"{name} (yours)", name)
        self.import_filter.setCurrentIndex(max(0, self.import_filter.findData(s.get("import_filter"))))

    def _sidecars(self) -> QFrame:
        card, v = self._card(
            "Ratings and sidecars",
            "Stars, colour labels and rejects are always kept in the catalog. This chooses where "
            "they're also written as XMP sidecar files. Picks live only in the catalog.")
        self.sidecar_mode = QButtonGroup(self)
        for i, (key, title, help) in enumerate(SIDECAR_CHOICES):
            b = QRadioButton(title)
            b.setProperty("mode", key)
            self.sidecar_mode.addButton(b, i)
            v.addWidget(b)
            h = QLabel(help, objectName="Help")
            h.setWordWrap(True)
            h.setContentsMargins(24, 0, 0, 4)
            v.addWidget(h)
        self.sidecar_mode.idClicked.connect(self._sidecar_mode_changed)
        self.update_existing = QCheckBox(
            "Also keep sidecars that already exist next to photos up to date (darktable's, Lightroom's) "
            "- without ever creating new ones there")
        self.update_existing.toggled.connect(lambda on: self._set("update_existing_sidecars", on))
        v.addWidget(self.update_existing)
        self.store = self._path_field()
        self._row(v, "Sidecar folder", self.store,
                  QPushButton("Change…", clicked=lambda: self._move_folder(
                      "sidecar_store_dir", paths.SIDECAR_STORE, "the sidecar folder")),
                  QPushButton("Default", clicked=lambda: self._move_folder(
                      "sidecar_store_dir", paths.SIDECAR_STORE, "the sidecar folder", reset=True)),
                  help="Used by \"In Lunelis's own sidecar folder\". Existing sidecars move with it.")
        return card

    def _duplicates_and_jobs(self) -> QFrame:
        card, v = self._card(
            "Duplicates and background jobs",
            "Duplicate detection finds byte-identical copies. Long jobs (duplicates, hashing, integrity "
            "checks) can be paused, survive a restart, and wait for the NAS if it's asleep.")
        self.prefer = QComboBox()
        self.prefer.currentIndexChanged.connect(self._prefer_changed)
        self._row(v, "Keep the copy in", self.prefer,
                  help="When copies are identical, the one in this source is the keeper. Otherwise the "
                       "copy in the shallowest folder, and never a Google Takeout copy if there's another.")
        v.addWidget(QLabel("New jobs start with", objectName="SubTitle"))
        self.when = QComboBox()
        for key, label in WHEN_CHOICES:
            self.when.addItem(label, key)
        self.when.currentIndexChanged.connect(lambda _: self._set("job_default_when", self.when.currentData()))
        self._row(v, "When to run", self.when)
        self._row(v, "Idle means no input for", self._spin("job_idle_minutes", 1, 24 * 60, " min"))
        start = self._spin("job_window_start_hour", 0, 23, ":00")
        end = self._spin("job_window_end_hour", 0, 23, ":00")
        self._row(v, "Set hours", QLabel("from"), start, QLabel("to"), end,
                  help="Hours can run past midnight (22:00 to 06:00 is overnight).")
        self._row(v, "Speed limit", self._spin("job_mb_per_s", 0, 100_000, " MB/s", special="No limit"),
                  help="Caps how fast jobs read, so the NAS stays usable for everything else.")
        v.addWidget(QLabel("Regular file checks", objectName="SubTitle"))
        self.integrity_every = QComboBox()
        for key, label in (("week", "Every week"), ("month", "Every month"), ("off", "Off")):
            self.integrity_every.addItem(label, key)
        self.integrity_every.currentIndexChanged.connect(
            lambda _: self._set("integrity_every", self.integrity_every.currentData()))
        self._row(v, "Re-read files", self.integrity_every,
                  self._spin("integrity_gb", 1, 100_000, " GB each time"),
                  help="A little of the library is read again in idle time - the files checked longest ago "
                       "first - so damage is caught while a backup still has a good copy.")
        self.integrity_state = QLabel(objectName="Help")
        v.addWidget(self.integrity_state)
        return card

    def _load_integrity(self) -> None:
        from lunelis.jobs import rolling
        s = Settings(self.conn)
        self.integrity_every.setCurrentIndex(max(0, self.integrity_every.findData(s.get("integrity_every"))))
        done, total, oldest = rolling.summary(self.conn)
        text = f"{done:,} of {total:,} files checked so far"
        if oldest and done >= total and total:
            text += f"; the oldest check was {oldest[:10]}"
        self.integrity_state.setText(text)

    def _backups(self) -> QFrame:
        card, v = self._card(
            "Catalog backups",
            "The catalog holds things XMP can't - picks, jobs, events and albums - so it's "
            "backed up automatically, and before anything moves files.")
        self.backup_folder = self._path_field()
        self._row(v, "Backup folder", self.backup_folder,
                  QPushButton("Change…", clicked=lambda: self._move_folder(
                      "catalog_backup_dir", paths.BACKUP_DIR, "the backup folder")),
                  QPushButton("Default", clicked=lambda: self._move_folder(
                      "catalog_backup_dir", paths.BACKUP_DIR, "the backup folder", reset=True)),
                  help="A different drive (or a network folder) protects against this PC's drive failing.")
        self._row(v, "Back up every", self._spin("catalog_backup_every_hours", 1, 24 * 365, " hours"))
        self._row(v, "Keep the newest", self._spin("catalog_backups_keep", 1, 1000, " backups"))
        self.backup_status = QLabel(objectName="Help")
        self.backup_status.setContentsMargins(208, 0, 0, 0)
        v.addWidget(self.backup_status)
        row = QHBoxLayout()
        row.addSpacing(208)
        row.addWidget(QPushButton("Back up now", clicked=lambda: self.backup_now.emit()))
        row.addWidget(QPushButton("Restore a backup…", clicked=self._restore))
        row.addWidget(QPushButton("Open folder", clicked=lambda: self._open(self._backup_dir())))
        row.addWidget(QPushButton("Photo backups (USB, network)…", clicked=lambda: self.open_page.emit("Backups")))
        row.addStretch(1)
        v.addLayout(row)
        return card

    def _data_folder(self) -> QFrame:
        card, v = self._card(
            "Data folder",
            "Where Lunelis keeps the catalog, thumbnails, backups and sidecars - never inside your "
            "photo folders. It has to be on a drive in this PC: the catalog can't live on a network share.")
        self.data_dir = self._path_field()
        self._row(v, "Location", self.data_dir,
                  QPushButton("Move…", clicked=self._move_data_dir),
                  QPushButton("Open", clicked=lambda: self._open(paths.DATA_DIR)))
        self.data_status = QLabel(objectName="Help")
        self.data_status.setWordWrap(True)
        self.data_status.setContentsMargins(208, 0, 0, 0)
        v.addWidget(self.data_status)
        row = QHBoxLayout()
        row.addSpacing(208)
        self.cancel_move_b = QPushButton("Cancel the move", clicked=self._cancel_move)
        row.addWidget(self.cancel_move_b)
        row.addStretch(1)
        v.addLayout(row)
        return card

    def _darktable(self) -> QFrame:
        card, v = self._card(
            "darktable",
            "A small script in darktable swaps stars, colour labels and rejects with Lunelis, both ways - "
            "so darktable sees your ratings even though Lunelis keeps its sidecars out of your photo folders. "
            "The newest change wins; picks stay in Lunelis. darktable loads it the next time it starts.")
        self.dt_status = QLabel()
        self.dt_status.setWordWrap(True)
        v.addWidget(self.dt_status)
        self.dt_exchange = self._path_field()
        self._row(v, "Exchange folder", self.dt_exchange,
                  QPushButton("Change…", clicked=self._dt_pick_exchange),
                  help="Where the two swap ratings. If darktable runs on another PC, choose a network folder "
                       "both can reach, and install the script there from that PC's Lunelis.")
        row = QHBoxLayout()
        row.addSpacing(208)
        self.dt_install_b = QPushButton(clicked=self._dt_install)
        self.dt_sync_b = QPushButton("Sync now", clicked=self._dt_sync)
        self.dt_remove_b = QPushButton("Remove from darktable", clicked=self._dt_remove)
        for b in (self.dt_install_b, self.dt_sync_b, self.dt_remove_b):
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        return card

    def _load_darktable(self) -> None:
        from lunelis.darktable import bridge
        cfg = bridge.darktable_config_dir()
        on = Settings(self.conn).get("darktable_sync")
        installed = bridge.installed()
        if not cfg.is_dir():
            text = (f"darktable's settings folder ({cfg}) isn't on this PC - install darktable and start it "
                    "once, then come back here.")
        elif installed:
            text = f"Installed in darktable ({cfg}\\lua\\lunelis.lua)." + ("" if on else " Syncing is off.")
        else:
            text = f"Not installed. darktable's settings: {cfg}"
        self.dt_status.setText(text)
        self.dt_exchange.setText(str(bridge.exchange_dir(self.conn)))
        self.dt_install_b.setText("Reinstall" if installed else "Install in darktable")
        self.dt_install_b.setEnabled(cfg.is_dir())
        self.dt_sync_b.setEnabled(bool(on))
        self.dt_remove_b.setEnabled(installed or bool(on))

    def _dt_pick_exchange(self) -> None:
        from lunelis.darktable import bridge
        picked = QFileDialog.getExistingDirectory(self, "Swap ratings with darktable through which folder?",
                                                  str(bridge.exchange_dir(self.conn)))
        if picked:
            self._set("darktable_exchange_dir", os.path.normpath(picked))
            if bridge.installed():
                bridge.install(self.conn)           # the script carries the folder: refresh it
            self.refresh()

    def _dt_install(self) -> None:
        from lunelis.darktable import bridge
        try:
            path = bridge.install(self.conn)
        except OSError as e:
            QMessageBox.warning(self, "darktable", str(e))
            return
        QMessageBox.information(self, "darktable", f"Installed: {path}\n\nRestart darktable to load it. In the "
                                "lighttable's right panel you'll find a Lunelis box with \"Sync with Lunelis\"; it "
                                "also syncs by itself when darktable starts, when you switch views and when it closes.")
        self.refresh()

    def _dt_sync(self) -> None:
        from lunelis.darktable import bridge
        r = bridge.sync(self.conn)
        self.saved.setText(f"darktable: {r.applied:,} ratings from darktable" +
                           (f", {r.older:,} older than Lunelis's (kept Lunelis's)" if r.older else ""))
        self.library_changed.emit()

    def _dt_remove(self) -> None:
        from lunelis.darktable import bridge
        bridge.uninstall(self.conn)
        self.saved.setText("Removed from darktable - restart darktable to unload it")
        self.refresh()

    def _tray(self) -> QFrame:
        card, v = self._card("Tray and start-up")
        self.tray_cb = QCheckBox("Keep Lunelis in the tray when the window is closed, and watch for memory cards")
        self.tray_cb.toggled.connect(self._tray_toggled)
        v.addWidget(self.tray_cb)
        self.autostart_cb = QCheckBox("Start Lunelis with Windows (it opens in the tray)")
        self.autostart_cb.setEnabled(sys.platform == "win32")
        self.autostart_cb.toggled.connect(self._autostart_toggled)
        v.addWidget(self.autostart_cb)
        return card

    # --- loading ---------------------------------------------------------------------------

    def refresh(self) -> None:
        self._loading = True
        try:
            s = Settings(self.conn)
            self._load_sources(s)
            self.dest.setText(s.get("import_destination") or "")
            self.template.setCurrentText(s.get("import_template"))
            self._load_import_filter(s)
            self._load_startup(s)
            self._load_thumb_preset(s)
            self._load_ai()
            self._load_caches(stale_after=60)
            self._show_example(s.get("import_template"))
            self.staging_local.setText(s.get("import_staging_local") or "")
            self.staging_local.setPlaceholderText(f"Default: {paths.DATA_DIR / 'staging'}")
            self.staging_net.setText(s.get("import_staging_network") or "")
            mode = s.get("sidecar_mode")
            for b in self.sidecar_mode.buttons():
                b.setChecked(b.property("mode") == mode)
            self.update_existing.setChecked(s.get("update_existing_sidecars"))
            self.store.setText(s.get("sidecar_store_dir") or "")
            self.store.setPlaceholderText(f"Default: {paths.SIDECAR_STORE}")
            self.when.setCurrentIndex(max(0, self.when.findData(s.get("job_default_when"))))
            for key, spin in self._spins.items():
                spin.setValue(s.get(key) if isinstance(spin, QDoubleSpinBox) else int(s.get(key)))
            self.backup_folder.setText(s.get("catalog_backup_dir") or "")
            self.backup_folder.setPlaceholderText(f"Default: {paths.BACKUP_DIR}")
            self._load_backup_status()
            self.data_dir.setText(str(paths.DATA_DIR))
            self._load_data_status()
            self.tray_cb.setChecked(s.get("tray_enabled"))
            from lunelis.ui.tray import autostart_enabled
            self.autostart_cb.setChecked(autostart_enabled())
            self._load_darktable()
            key = s.get("theme")
            for b in self.theme_group.buttons():
                b.setChecked(b.property("theme") == key)
            self.default_sort.setCurrentIndex(max(0, self.default_sort.findData(s.get("grid_default_sort"))))
            self.show_videos.setChecked(s.get("show_videos"))
            self._load_log_look()
            self.noticed_cb.setChecked(s.get("noticed_auto"))
            self._load_helpers()
            self._load_integrity()
            self.hover_info.setChecked(s.get("hover_info"))
            self.sidebar_auto.setChecked(s.get("sidebar_auto"))
            self.stack_bursts.setChecked(s.get("stack_bursts"))
            self.pair_raw.setChecked(s.get("pair_raw_jpeg"))
            self._load_thumbnails()
            self._load_scene_tags()
            self._load_faces()
            self._load_places()
            self.version_label.setText(f"Lunelis {paths.version()}" + (" (from source)" if not paths.FROZEN else ""))
            self.auto_update.setChecked(s.get("update_check"))
        finally:
            self._loading = False

    def _load_sources(self, s: Settings) -> None:
        counts = getattr(self, "_root_counts", {})         # filled in by _count_sources (a worker)
        rows = self.conn.execute("SELECT id, path, enabled, last_scanned_at FROM roots ORDER BY id").fetchall()
        self.roots.blockSignals(True)
        self.roots.clearContents()
        self.roots.setRowCount(len(rows))
        for i, (rid, path, enabled, scanned) in enumerate(rows):
            on = QTableWidgetItem()
            on.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            on.setCheckState(Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked)
            on.setData(Qt.ItemDataRole.UserRole, rid)
            self.roots.setItem(i, 0, on)
            self.roots.setItem(i, 1, QTableWidgetItem(path))
            n = QTableWidgetItem(f"{counts[rid]:,}" if rid in counts else "…")
            n.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.roots.setItem(i, 2, n)
            self.roots.setItem(i, 3, QTableWidgetItem(_when(scanned)))
        self.roots.setFixedHeight(min(360, self.roots.horizontalHeader().sizeHint().height() + 4
                                      + sum(self.roots.rowHeight(r) for r in range(len(rows)))
                                      + (0 if rows else 30)))
        self.roots.blockSignals(False)
        self._count_sources()

        names = {rid: path for rid, path, *_ in rows}
        self.skipped.clear()
        for rid, rel in excluded_folders(self.conn):
            item = QListWidgetItem(os.path.join(names.get(rid, "?"), *rel.split("/")))
            item.setData(Qt.ItemDataRole.UserRole, (rid, rel))
            self.skipped.addItem(item)
        if not self.skipped.count():
            self.skipped.addItem("No folders skipped")
        self.unskip_b.setEnabled(bool(excluded_folders(self.conn)))

        preferred = s.get("preferred_roots")
        self.prefer.blockSignals(True)
        self.prefer.clear()
        self.prefer.addItem("Any source (shallowest folder, not Takeout)", None)
        for rid, path, enabled, _ in rows:
            if enabled:
                self.prefer.addItem(path, rid)
                if preferred and preferred[0] == rid:
                    self.prefer.setCurrentIndex(self.prefer.count() - 1)
        self.prefer.blockSignals(False)

    def _count_sources(self) -> None:
        def show(counts):
            self._root_counts = counts
            self.roots.blockSignals(True)
            for i in range(self.roots.rowCount()):
                rid = self.roots.item(i, 0).data(Qt.ItemDataRole.UserRole) if self.roots.item(i, 0) else None
                if rid is not None and self.roots.item(i, 2) is not None:
                    self.roots.item(i, 2).setText(f"{counts.get(rid, 0):,}")
            self.roots.blockSignals(False)
        self._bg().run("counts", lambda c: dict(c.execute(
            "SELECT root_id, COUNT(*) FROM files WHERE missing_since IS NULL AND excluded = 0"
            " GROUP BY root_id").fetchall()), show)

    def _backup_dir(self) -> Path:
        return backup.backup_dir(Settings(self.conn), paths.DATA_DIR)

    def _load_backup_status(self) -> None:
        # The backup folder can be on a network drive: listed on a worker.
        folder = self._backup_dir()

        def look():
            snaps = backup.list_snapshots(folder)
            if not snaps:
                return "No backups yet."
            last = backup.last_snapshot_time(folder)
            total = sum(p.stat().st_size for p in snaps)
            return f"Last backup {last:%b %d, %Y %H:%M} · {len(snaps)} kept · {total / 1e6:,.0f} MB"

        self._bg().run("backups", look, self.backup_status.setText,
                       error=lambda e: self.backup_status.setText(f"Can't read the backup folder: {e}"), db=False)

    def _load_data_status(self) -> None:
        cat = paths.DEFAULT_CATALOG_PATH
        size = cat.stat().st_size if cat.exists() else 0
        pending = paths.pending_move()
        text = f"Catalog {size / 1e6:,.0f} MB."
        if pending:
            text += f" Moving to {pending} the next time Lunelis starts."
        self.data_status.setText(text)
        self.cancel_move_b.setVisible(pending is not None)

    # --- sources -------------------------------------------------------------------------------

    def _remove_source(self) -> None:
        row = self.roots.currentRow()
        item = self.roots.item(row, 0) if row >= 0 else None
        if item is None:
            QMessageBox.information(self, "Remove a source", "Select a source in the table first.")
            return
        self.remove_source.emit(item.data(Qt.ItemDataRole.UserRole), self.roots.item(row, 1).text())

    def _root_toggled(self, item: QTableWidgetItem) -> None:
        if item.column() != 0 or self._loading:
            return
        rid = item.data(Qt.ItemDataRole.UserRole)
        on = item.checkState() == Qt.CheckState.Checked
        self.conn.execute("UPDATE roots SET enabled = ? WHERE id = ?", (int(on), rid))
        self.conn.commit()
        self.saved.setText("Source turned on - scanning it" if on else "Source turned off")
        self.library_changed.emit()
        if on:
            self.rescan.emit([rid])
        self.refresh()

    def _root_for(self, folder: str) -> tuple[int, str] | None:
        for rid, path in self.conn.execute("SELECT id, path FROM roots"):
            if _inside(folder, path):
                return rid, path
        return None

    def _skip_folder(self) -> None:
        picked = QFileDialog.getExistingDirectory(self, "Skip which folder? (it must be inside a source)")
        if not picked:
            return
        picked = os.path.normpath(picked)
        hit = self._root_for(picked)
        if hit is None:
            QMessageBox.information(self, "Not in a source", "That folder isn't inside any of your sources.")
            return
        rid, root = hit
        rel = os.path.relpath(picked, root).replace("\\", "/")
        if rel == ".":
            QMessageBox.information(self, "That's a whole source",
                                    "To leave out a whole source, untick it in the list instead.")
            return
        before = self.conn.execute("SELECT COUNT(*) FROM files WHERE root_id = ? AND excluded = 1",
                                   (rid,)).fetchone()[0]
        after = exclude_folder(self.conn, rid, rel)
        self.saved.setText(f"Skipping {picked} - {after - before:,} files hidden")
        self.library_changed.emit()
        self.refresh()

    def _unskip_folder(self) -> None:
        item = self.skipped.currentItem()
        data = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not data:
            QMessageBox.information(self, "Stop skipping", "Choose a folder in the list first.")
            return
        rid, rel = data
        include_folder(self.conn, rid, rel)
        self.saved.setText("No longer skipped - scanning it for new files")
        self.library_changed.emit()
        self.rescan.emit([rid])
        self.refresh()

    def _prefer_changed(self) -> None:
        rid = self.prefer.currentData()
        self._set("preferred_roots", [rid] if rid else [])

    # --- import ------------------------------------------------------------------------------

    def _show_example(self, text: str) -> bool:
        try:
            folder = render(text, EXAMPLE)
        except TemplateError as e:
            self.template_example.setObjectName("Error")
            self.template_example.setText(f"Can't use this template: {e}")
        else:
            self.template_example.setObjectName("Example")
            self.template_example.setText(f"e.g. {folder}\\DSC01234.ARW   (an import named \"Air Show\")")
        self.template_example.style().unpolish(self.template_example)
        self.template_example.style().polish(self.template_example)
        return self.template_example.objectName() == "Example"

    def _template_changed(self, text: str) -> None:
        if self._show_example(text):
            self._set("import_template", text)

    def _pick_folder(self, key: str, caption: str) -> None:
        current = Settings(self.conn).get(key) or ""
        picked = QFileDialog.getExistingDirectory(self, caption, current)
        if picked:
            self._set(key, os.path.normpath(picked))
            self.refresh()

    def _pick_network_staging(self) -> None:
        current = Settings(self.conn).get("import_staging_network") or ""
        picked = QFileDialog.getExistingDirectory(self, "Spill-over staging folder", current)
        if not picked:
            return
        picked = os.path.normpath(picked)
        hit = self._root_for(picked)
        if hit is not None:
            QMessageBox.warning(self, "Inside a source",
                                f"{picked} is inside the source {hit[1]}. Half-copied files there would be "
                                "cataloged - choose a folder outside every source.")
            return
        self._set("import_staging_network", picked)
        self.refresh()

    def _reset(self, key: str) -> None:
        Settings(self.conn).reset(key)
        self.saved.setText("Saved")
        self.refresh()

    # --- sidecars ------------------------------------------------------------------------------

    def _sidecar_mode_changed(self, button_id: int) -> None:
        mode = SIDECAR_CHOICES[button_id][0]
        if mode == Settings(self.conn).get("sidecar_mode") or not self._set("sidecar_mode", mode):
            return
        if mode == "catalog":
            return
        n = self.conn.execute(
            "SELECT COUNT(*) FROM ratings WHERE stars > 0 OR color_label IS NOT NULL OR flag = 'reject'"
        ).fetchone()[0]
        if not n:
            return
        where = "next to each photo" if mode == "beside" else "into the sidecar folder"
        if QMessageBox.question(
                self, "Write existing ratings?",
                f"Write your {n:,} existing ratings {where} now?\n\nOtherwise only ratings you change from "
                "now on are written there.") == QMessageBox.StandardButton.Yes:
            self.conn.execute("UPDATE ratings SET xmp_pending = 1"
                              " WHERE stars > 0 OR color_label IS NOT NULL OR flag = 'reject'")
            self.conn.commit()
            self.rewrite_sidecars.emit()
            self.saved.setText(f"Writing {n:,} ratings {where}…")

    # --- moving folders ----------------------------------------------------------------------

    def _move_folder(self, key: str, default_dir: Path, what: str, reset: bool = False) -> None:
        """Point `key` at a new folder, moving what's in the old one there first."""
        if self._thread is not None:
            QMessageBox.information(self, "Busy", "Still moving files - try again in a moment.")
            return
        old = Path(Settings(self.conn).get(key) or default_dir)
        if reset:
            new = Path(default_dir)
        else:
            picked = QFileDialog.getExistingDirectory(self, f"Move {what} to which folder?", str(old))
            if not picked:
                return
            new = Path(os.path.normpath(picked))
        if os.path.normcase(str(new)) == os.path.normcase(str(old)):
            if reset:
                self._reset(key)
            return
        if _inside(str(new), str(old)) or _inside(str(old), str(new)):
            QMessageBox.warning(self, "Can't use that folder",
                                f"Choose a folder that's neither inside {what} nor contains it.")
            return
        hit = self._root_for(str(new))
        if hit is not None:
            QMessageBox.warning(self, "Inside a source",
                                f"{new} is inside the source {hit[1]}. Lunelis keeps its own files out of "
                                "your photo folders - choose somewhere else.")
            return
        has_files = old.is_dir() and any(p.is_file() for p in old.rglob("*"))
        if has_files and QMessageBox.question(
                self, f"Move {what}?",
                f"Move everything in\n{old}\nto\n{new}?") != QMessageBox.StandardButton.Yes:
            return

        def finished(err) -> None:
            self._thread.quit()
            self._thread.wait()
            self._thread = None
            if err is not None:
                QMessageBox.warning(self, "Move failed",
                                    f"Couldn't move {what}: {err}\n\nNothing changed - it's still at {old}.")
            elif reset:
                self._reset(key)
            else:
                self._set(key, str(new))
            self.refresh()

        if not has_files:
            if reset:
                self._reset(key)
            else:
                self._set(key, str(new))
            self.refresh()
            return
        self.saved.setText(f"Moving {what}…")
        self._thread = QThread(self)
        self._mover = FolderMove(old, new)
        self._mover.moveToThread(self._thread)
        self._thread.started.connect(self._mover.run)
        # Through a bound method: a plain function would run on the mover's thread.
        self._move_finished = finished
        self._mover.done.connect(self._on_moved)
        self._thread.start()

    def _on_moved(self, err) -> None:
        self._move_finished(err)

    def _open(self, folder: Path) -> None:
        Path(folder).mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    # --- backups & data folder -------------------------------------------------------------------

    def _restore(self) -> None:
        picked, _ = QFileDialog.getOpenFileName(self, "Restore which catalog backup?", str(self._backup_dir()),
                                                "Catalog backups (catalog-*.zip)")
        if not picked:
            return
        name = os.path.basename(picked)
        if QMessageBox.question(
                self, "Restore this backup?",
                f"Replace the catalog with {name}?\n\nRatings, picks and jobs go back to how they were "
                "then. Lunelis restarts to do it, and keeps the current catalog alongside "
                "(catalog.db.before-restore-…) in case you change your mind.") != QMessageBox.StandardButton.Yes:
            return
        try:
            backup.request_restore(paths.DATA_DIR, Path(picked))
        except Exception as e:        # a bad zip, unreadable file...
            QMessageBox.warning(self, "Can't restore", plain(e))
            return
        self.restart.emit()

    def _move_data_dir(self) -> None:
        from lunelis.importing.ingest import unfinished
        if unfinished(self.conn):
            # Staged card copies are recorded by their full path in the data folder.
            QMessageBox.information(self, "An import isn't finished",
                                    "Finish (or cancel) the memory-card import first - its staged copies "
                                    "live in the data folder.")
            return
        picked = QFileDialog.getExistingDirectory(self, "Move Lunelis's data to which folder?")
        if not picked:
            return
        new = Path(os.path.normpath(picked))
        if paths.is_network_path(new):
            QMessageBox.warning(self, "Data folder", "The data folder has to be on a drive in this PC - the "
                                "catalog can't live on a network share.")
            return
        if new.name.lower() != "lunelis" and any(new.iterdir()):
            new = new / "Lunelis"            # a non-empty pick gets its own subfolder
        try:
            paths.check_new_data_dir(new)
        except ValueError as e:
            QMessageBox.warning(self, "Can't use that folder", plain(e))
            return
        if QMessageBox.question(
                self, "Move the data folder?",
                f"Move the catalog, thumbnails, backups and sidecars to\n{new}?\n\nLunelis restarts to do "
                "it. On another drive this copies everything first and only then removes the old "
                "folder, so an interruption loses nothing.") != QMessageBox.StandardButton.Yes:
            return
        paths.request_move(new)
        self.restart.emit()

    def _cancel_move(self) -> None:
        paths.cancel_move()
        self.refresh()

    # --- tray ------------------------------------------------------------------------------------

    def _tray_toggled(self, on: bool) -> None:
        if self._set("tray_enabled", on):
            self.tray_changed.emit(on)

    def _autostart_toggled(self, on: bool) -> None:
        if self._loading:
            return
        from lunelis.ui.tray import set_autostart
        try:
            set_autostart(on)
        except OSError as e:
            self.saved.setText(f"Couldn't change Start with Windows: {e}")
            self.autostart_cb.blockSignals(True)
            self.autostart_cb.setChecked(not on)          # show what Windows actually has
            self.autostart_cb.blockSignals(False)
            return
        self._set("start_with_windows", on)
        self.autostart_changed.emit(on)


def _when(iso: str | None) -> str:
    if not iso:
        return "Never"
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if t.tzinfo is not None:
            t = t.astimezone().replace(tzinfo=None)
        return f"{t:%b %d, %Y %H:%M}"
    except ValueError:
        return iso
