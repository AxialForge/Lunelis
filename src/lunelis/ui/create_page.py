"""
The Create page (sidebar > Create): new files made from your photos.

A home with a card per tool and the output folder, then the tools:

- **Animation** - a burst or selection as MP4 / WebP / GIF, previewed live
  at the chosen speed (create/animation.py).
- **Collage** - a layout of photos on one canvas; drag a photo onto another
  cell to swap them, drag inside a cell to move the photo, the wheel zooms
  it (create/collage.py).
- **Batch copies** - resize, convert, watermark, rename, strip metadata
  (create/batch.py).

Every tool starts from the same **photo picker**: the library's selection,
what the library shows, an album or event, a folder, Picks, 4-5 stars or
recent imports. The photos are a strip: drag to reorder, untick to leave
one out. Making runs on a worker with progress and Cancel; outputs are NEW
files in one folder (Settings key create_output_dir, default
Pictures\\Lunelis creations) - nothing is ever overwritten.
"""
from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QObject, QPointF, QRectF, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QColorDialog, QComboBox, QFileDialog, QFormLayout, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QProgressDialog, QPushButton,
    QScrollArea, QSlider, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from lunelis import paths
from lunelis.create import animation, batch, collage, engine
from lunelis.raw.thumbnails import cache_rel_path
from lunelis.ui.background import db_file, unless_closed
from lunelis.ui.library import Filter, LibraryIndex
from lunelis.ui.widgets import plain

STRIP_MAX = 400                      # photos shown in the strip (a batch can take more)
THUMB = 96

SOURCES = (
    ("selection", "Selected in the library"),
    ("view", "What the library shows now"),
    ("album", "An album or event…"),
    ("folder", "A folder…"),
    ("picks", "Picks"),
    ("favorites", "4 and 5 stars"),
    ("recent", "Recently imported"),
)


def thumb_image(conn, file_id: int, edge: int = 512) -> Image.Image:
    """The cached thumbnail (fast) - previews; a grey frame when there's none yet."""
    try:
        return engine.thumb(conn, file_id, edge)
    except OSError:
        return Image.new("RGB", (edge, round(edge * 2 / 3)), (90, 90, 90))


def to_pixmap(img: Image.Image) -> QPixmap:
    img = img.convert("RGB")
    q = QImage(img.tobytes(), img.width, img.height, 3 * img.width, QImage.Format.Format_RGB888).copy()
    return QPixmap.fromImage(q)


# --- the shared photo picker ---------------------------------------------------------------

class PhotoPicker(QWidget):
    """Which photos, in which order. `ids()` = the ticked ones, in strip order."""

    changed = Signal()

    def __init__(self, conn, parent=None, minimum: int = 1) -> None:
        super().__init__(parent)
        self.conn = conn
        self.minimum = minimum
        self._selection: list[int] = []
        self._view_filter = Filter()
        self._sort: str | None = None
        self._album: tuple[str, str] | None = None       # (kind, key) of the chosen album / event
        self._folder: tuple[int, str] | None = None
        self._extra: list[int] = []                       # beyond STRIP_MAX: not shown, still used
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(QLabel("Photos:", objectName="ToolLabel"))
        self.source = QComboBox()
        for key, label in SOURCES:
            self.source.addItem(label, key)
        self.source.activated.connect(self._source_chosen)
        row.addWidget(self.source)
        self.scope = QLabel(objectName="Help")
        row.addWidget(self.scope, 1)
        self.count = QLabel(objectName="Count")
        row.addWidget(self.count)
        v.addLayout(row)
        self.strip = QListWidget()
        self.strip.setViewMode(QListWidget.ViewMode.IconMode)
        self.strip.setFlow(QListWidget.Flow.LeftToRight)
        self.strip.setWrapping(False)
        self.strip.setIconSize(QSize(THUMB, THUMB))
        self.strip.setFixedHeight(THUMB + 28)
        self.strip.setMovement(QListWidget.Movement.Snap)
        self.strip.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.strip.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.strip.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.strip.setToolTip("Drag to change the order. Untick a photo to leave it out.")
        self.strip.itemChanged.connect(lambda _i: self._counted())
        self.strip.model().rowsMoved.connect(lambda *_a: self.changed.emit())
        v.addWidget(self.strip)
        self._pending: list[QListWidgetItem] = []
        self._thumb_timer = QTimer(self, interval=0, timeout=self._load_some)

    def set_library_context(self, selection: list[int], view_filter: Filter, sort: str | None) -> None:
        self._selection, self._view_filter, self._sort = list(selection), view_filter, sort

    def choose_default(self) -> None:
        self.source.setCurrentIndex(self.source.findData("selection" if self._selection else "view"))
        self.load()

    def _source_chosen(self, _i: int) -> None:
        key = self.source.currentData()
        if key == "album" and not self._pick_album():
            return
        if key == "folder" and not self._pick_folder():
            return
        self.load()

    def _pick_album(self) -> bool:
        from PySide6.QtWidgets import QInputDialog
        from lunelis.albums import model as albums
        found = [(a.kind, a.key, a.name, a.count) for a in albums.your_albums(self.conn) + albums.event_albums(self.conn)]
        if not found:
            QMessageBox.information(self, "Albums", "There are no albums or events yet.")
            return False
        labels = [f"{'Event' if k == 'event' else 'Album'}: {n}  ({c:,})" for k, _key, n, c in found]
        label, ok = QInputDialog.getItem(self, "Which album or event?", "Photos from:", labels, 0, False)
        if not ok:
            return False
        kind, key, name, _c = found[labels.index(label)]
        self._album = (kind, key)
        self.scope.setText(name)
        return True

    def _pick_folder(self) -> bool:
        picked = QFileDialog.getExistingDirectory(self, "Photos from which folder (and the folders in it)?")
        if not picked:
            return False
        norm = os.path.normcase(os.path.normpath(picked))
        for rid, path in self.conn.execute("SELECT id, path FROM roots"):
            base = os.path.normcase(os.path.normpath(path))
            if norm == base or norm.startswith(base.rstrip("\\") + "\\"):
                rel = os.path.relpath(os.path.normpath(picked), path).replace("\\", "/")
                self._folder = (rid, "" if rel == "." else rel)
                self.scope.setText(picked)
                return True
        QMessageBox.information(self, "Not in the library", "That folder isn't inside any of your sources.")
        return False

    def _filter(self) -> Filter:
        key = self.source.currentData()
        if key == "selection":
            return Filter(ids=tuple(self._selection))
        if key == "view":
            return self._view_filter
        if key == "album" and self._album:
            kind, k = self._album
            return Filter(event_id=int(k)) if kind == "event" else Filter(album_id=int(k))
        if key == "folder" and self._folder:
            return Filter(folder=self._folder)
        return {"picks": Filter(flag="pick"), "favorites": Filter(min_stars=4),
                "recent": Filter(auto="recent")}.get(key, Filter(ids=()))

    def load(self) -> None:
        if self.source.currentData() not in ("album", "folder"):
            self.scope.setText("")
        idx = LibraryIndex()
        idx.collapse = False
        idx.load(self.conn, self._sort or "date_asc", replace(self._filter(), hide_videos=True))
        tiles = [idx.tile(i) for i in range(len(idx))]
        self.strip.blockSignals(True)
        self.strip.clear()
        self._pending = []
        for t in tiles[:STRIP_MAX]:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, t.file_id)
            item.setData(Qt.ItemDataRole.UserRole + 1, t.thumbnail_path)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDropEnabled)
            item.setCheckState(Qt.CheckState.Checked)
            item.setSizeHint(QSize(THUMB + 12, THUMB + 30))
            self.strip.addItem(item)
            self._pending.append(item)
        self.strip.blockSignals(False)
        self._extra = [t.file_id for t in tiles[STRIP_MAX:]]
        self._thumb_timer.start()
        self._counted()

    def _load_some(self) -> None:
        for _ in range(24):
            if not self._pending:
                self._thumb_timer.stop()
                return
            item = self._pending.pop(0)
            try:
                rel = item.data(Qt.ItemDataRole.UserRole + 1) or cache_rel_path(item.data(Qt.ItemDataRole.UserRole))
            except RuntimeError:                       # the strip was cleared meanwhile
                continue
            pm = QPixmap(str(paths.THUMBNAIL_CACHE / rel))
            if not pm.isNull():
                item.setIcon(QIcon(pm.scaled(THUMB, THUMB, Qt.AspectRatioMode.KeepAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation)))

    def _counted(self) -> None:
        n = len(self.ids())
        more = f" (+{len(self._extra):,} not shown)" if self._extra else ""
        self.count.setText(f"{n:,} photo{'s' if n != 1 else ''}{more}")
        self.changed.emit()

    def ids(self) -> list[int]:
        out = [self.strip.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.strip.count())
               if self.strip.item(i).checkState() == Qt.CheckState.Checked]
        return out + self._extra


# --- making things on a worker -----------------------------------------------------------

class MakeWorker(QObject):
    progress = Signal(int, int)
    done = Signal(object)

    def __init__(self, db: str, fn) -> None:
        super().__init__()
        self.db, self.fn = db, fn
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        from lunelis.catalog.schema import open_catalog
        try:
            conn = open_catalog(self.db)
            try:
                result = self.fn(conn, lambda d, t: self.progress.emit(d, t), lambda: self._cancel)
            finally:
                conn.close()
        except Exception as e:
            self.done.emit(e)
            return
        self.done.emit(result)


class Tool(QWidget):
    """A Create tool: back button, title, picker, options, Make."""

    back = Signal()
    title_text = ""
    blurb = ""
    minimum = 1

    def __init__(self, conn, parent=None) -> None:
        super().__init__(parent)
        self.conn = conn
        self._thread: QThread | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 12, 24, 16)
        head = QHBoxLayout()
        b = QPushButton("‹ Create", clicked=self.back.emit)
        b.setFlat(True)
        head.addWidget(b)
        t = QLabel(self.title_text, objectName="PageTitle")
        head.addWidget(t)
        head.addStretch(1)
        outer.addLayout(head)
        help_ = QLabel(self.blurb, objectName="Help")
        help_.setWordWrap(True)
        outer.addWidget(help_)
        self.picker = PhotoPicker(conn, self, self.minimum)
        outer.addWidget(self.picker)
        self.body = QHBoxLayout()
        outer.addLayout(self.body, 1)
        foot = QHBoxLayout()
        self.result = QLabel(objectName="Help")
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.result.linkActivated.connect(lambda href: QDesktopServices.openUrl(QUrl.fromLocalFile(href)))
        foot.addWidget(self.result, 1)
        self.make_b = QPushButton("Make", objectName="Primary", clicked=self.make)
        foot.addWidget(self.make_b)
        outer.addLayout(foot)
        self.picker.changed.connect(self._enable)

    def _enable(self) -> None:
        self.make_b.setEnabled(len(self.picker.ids()) >= self.minimum and self._thread is None)

    def out_dir(self) -> Path:
        return engine.output_dir(self.conn)

    def make(self) -> None:
        raise NotImplementedError

    def run(self, label: str, fn, total: int) -> None:
        """Run fn(conn, progress, cancelled) on a worker with a progress dialog."""
        if self._thread is not None:
            return
        self._progress = QProgressDialog(label, "Cancel", 0, max(1, total), self)
        self._progress.setWindowTitle(self.title_text)
        self._progress.setMinimumDuration(300)
        self._thread = QThread(self)
        self._worker = MakeWorker(db_file(self.conn), fn)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._step)
        self._worker.done.connect(self._done)
        self._progress.canceled.connect(lambda: self._worker.cancel())     # direct: see CLAUDE.md
        self._enable()
        self._thread.start()

    def _step(self, done: int, total: int) -> None:
        if getattr(self, "_progress", None) is not None:
            self._progress.setMaximum(max(1, total))
            self._progress.setValue(done)

    @unless_closed
    def _done(self, result) -> None:
        self._thread.quit()
        self._thread.wait()
        self._thread = None
        if self._progress is not None:
            self._progress.close()
            self._progress = None
        self._enable()
        if isinstance(result, (animation.Cancelled, batch.Cancelled)):
            self.result.setText("Stopped - nothing half-made was kept.")
            return
        if isinstance(result, batch.BatchError):
            self.finished(result.made)
            QMessageBox.warning(self, self.title_text, str(result))
            return
        if isinstance(result, Exception):
            self.result.setText("")
            QMessageBox.warning(self, self.title_text, plain(result))
            return
        self.finished(result if isinstance(result, list) else [result])

    def finished(self, made: list[str]) -> None:
        if not made:
            return
        folder = os.path.dirname(made[0])
        what = (f'<a href="{made[0]}">{os.path.basename(made[0])}</a>' if len(made) == 1
                else f"{len(made):,} files")
        self.result.setText(f"Made {what} in <a href=\"{folder}\">{folder}</a>")


# --- Animation ---------------------------------------------------------------------------------

class AnimationTool(Tool):
    title_text = "Animation"
    blurb = ("A burst or a few photos as a short animation. MP4 is the best quality and smallest, WebP plays in "
             "browsers, GIF plays everywhere (256 colours a frame). Drag the photos to change the order; untick "
             "one to leave it out.")
    minimum = 2

    def __init__(self, conn, parent=None) -> None:
        super().__init__(conn, parent)
        form = QFormLayout()
        self.kind = QComboBox()
        for k, label in (("mp4", "MP4 video"), ("webp", "WebP animation"), ("gif", "GIF")):
            self.kind.addItem(label, k)
        form.addRow("Make", self.kind)
        self.speed = QSpinBox(minimum=20, maximum=5000, singleStep=10, value=150, suffix=" ms a photo")
        form.addRow("Speed", self.speed)
        self.loops = QSpinBox(minimum=0, maximum=100, value=0, specialValueText="Forever")
        form.addRow("Loop", self.loops)
        self.bounce = QCheckBox("Play forward, then back")
        form.addRow("", self.bounce)
        self.size = QComboBox()
        for edge in (720, 1080, 1440, 2160):
            self.size.addItem(f"{edge} px", edge)
        self.size.setCurrentIndex(1)
        form.addRow("Size", self.size)
        self.quality = QSpinBox(minimum=10, maximum=100, value=80, suffix=" %")
        form.addRow("Quality", self.quality)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        box = QWidget()
        box.setLayout(form)
        box.setMaximumWidth(380)
        self.body.addWidget(box)
        self.preview = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(240, 160)
        self.preview.setObjectName("CreatePreview")
        self.body.addWidget(self.preview, 1)
        self._frames: list[QPixmap] = []
        self._i = 0
        self._play = QTimer(self, timeout=self._next_frame)
        self.picker.changed.connect(self._restart_preview)
        self.speed.valueChanged.connect(self._restart_preview)
        self.bounce.toggled.connect(self._restart_preview)
        self._kind_changed()

    def _kind_changed(self) -> None:
        mp4 = self.kind.currentData() == "mp4"
        self.loops.setSpecialValueText("Once" if mp4 else "Forever")
        self.loops.setToolTip("How many times the photos are played in the video" if mp4
                              else "0 = loops forever")

    def _restart_preview(self) -> None:
        ids = self.picker.ids()[:animation.MAX_FRAMES]
        self._frames = [to_pixmap(thumb_image(self.conn, f, 512)) for f in ids[:60]]
        self._frames = animation.sequence(self._frames, self.bounce.isChecked())
        self._i = 0
        if len(self._frames) >= 2:
            self._play.start(self.speed.value())
        else:
            self._play.stop()
            self.preview.setText("Pick two or more photos")
        self._next_frame()

    def _next_frame(self) -> None:
        if not self._frames:
            return
        pm = self._frames[self._i % len(self._frames)]
        self.preview.setPixmap(pm.scaled(self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation))
        self._i += 1

    def hideEvent(self, e) -> None:
        self._play.stop()
        super().hideEvent(e)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if len(self._frames) >= 2:
            self._play.start(self.speed.value())

    def options(self) -> animation.AnimOptions:
        return animation.AnimOptions(self.kind.currentData(), self.speed.value(), self.loops.value(),
                                     self.bounce.isChecked(), self.size.currentData(), self.quality.value())

    def make(self) -> None:
        ids = self.picker.ids()
        opts = self.options()
        try:
            opts.check(len(ids))
        except ValueError as e:
            QMessageBox.information(self, "Animation", str(e))
            return
        folder, name = self.out_dir(), engine.stamp("Animation")
        self.result.setText("")
        self.run(f"Making the {self.kind.currentText()}…",
                 lambda conn, prog, stop: animation.make(conn, ids, opts, folder, name, prog, stop), len(ids))


# --- Collage -----------------------------------------------------------------------------------

class CollageCanvas(QWidget):
    """The live collage: drag a photo onto another cell to swap them, drag
    inside its cell to move it, the wheel zooms the photo under the pointer."""

    changed = Signal()

    def __init__(self, tool: "CollageTool") -> None:
        super().__init__()
        self.tool = tool
        self.setMinimumSize(260, 260)
        self.setMouseTracking(True)
        self._pix: QPixmap | None = None
        self._boxes: list[QRectF] = []
        self._drag: tuple[int, QPointF, tuple[float, float]] | None = None
        self._hover = -1
        self._redraw = QTimer(self, singleShot=True, interval=30, timeout=self.rebuild)

    def _area(self) -> tuple[QRectF, tuple[int, int]]:
        opts = self.tool.opts
        a, b = collage.ASPECTS[opts.aspect]
        w, h = self.width() - 16, self.height() - 16
        scale = min(w / a, h / b)
        size = (max(1, int(a * scale)), max(1, int(b * scale)))
        return QRectF((self.width() - size[0]) / 2, (self.height() - size[1]) / 2, *size), size

    def rebuild(self) -> None:
        opts = self.tool.opts
        area, size = self._area()
        cache = self.tool.thumbs
        img = collage.render(opts, lambda fid, edge: cache(fid), long_edge=max(size))
        self._pix = to_pixmap(img)
        boxes = collage.cell_boxes(replace(opts, cells=collage.fit_cells(opts)), img.size)
        k = area.width() / img.width
        self._boxes = [QRectF(area.x() + l * k, area.y() + t * k, (r - l) * k, (b - t) * k) for l, t, r, b in boxes]
        self.update()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._redraw.start()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        if self._pix is not None:
            area, _size = self._area()
            p.drawPixmap(area.toRect(), self._pix)
        if self._drag is not None and 0 <= self._hover != self._drag[0]:
            pen = QPen(QColor("#4c8dff"), 3)
            p.setPen(pen)
            p.drawRect(self._boxes[self._hover].adjusted(1, 1, -1, -1))
        p.end()

    def _cell_at(self, pos: QPointF) -> int:
        return next((i for i, r in enumerate(self._boxes) if r.contains(pos)), -1)

    def mousePressEvent(self, e) -> None:
        i = self._cell_at(e.position())
        cells = collage.fit_cells(self.tool.opts)
        if i >= 0 and cells[i].file_id:
            self._drag = (i, e.position(), cells[i].pan)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, e) -> None:
        if self._drag is None:
            self.setCursor(Qt.CursorShape.OpenHandCursor if self._cell_at(e.position()) >= 0
                           else Qt.CursorShape.ArrowCursor)
            return
        i, start, pan = self._drag
        self._hover = self._cell_at(e.position())
        if self._hover == i:
            # Moving inside the cell: the photo follows the pointer.
            box = self._boxes[i]
            cell = collage.fit_cells(self.tool.opts)[i]
            d = e.position() - start
            span = max(0.05, 1.0 / max(1.0, cell.zoom))
            self.tool.set_cell(i, pan=(min(1.0, max(0.0, pan[0] - d.x() / box.width() * span)),
                                       min(1.0, max(0.0, pan[1] - d.y() / box.height() * span))))
        else:
            self.update()

    def mouseReleaseEvent(self, e) -> None:
        if self._drag is None:
            return
        i = self._drag[0]
        j = self._cell_at(e.position())
        self._drag, self._hover = None, -1
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        if j >= 0 and j != i:
            self.tool.swap(i, j)
        self.update()

    def wheelEvent(self, e) -> None:
        i = self._cell_at(e.position())
        if i < 0:
            return
        cell = collage.fit_cells(self.tool.opts)[i]
        step = 1.1 if e.angleDelta().y() > 0 else 1 / 1.1
        self.tool.set_cell(i, zoom=min(4.0, max(1.0, cell.zoom * step)))


class CollageTool(Tool):
    title_text = "Collage"
    blurb = ("Photos side by side on one picture. Choose a layout and shape; drag a photo onto another cell to "
             "swap them, drag inside a cell to move the photo, and use the mouse wheel to zoom it.")

    def __init__(self, conn, parent=None) -> None:
        super().__init__(conn, parent)
        self.opts = collage.CollageOptions()
        self._thumbs: dict[int, Image.Image] = {}
        form = QFormLayout()
        self.layout_box = QComboBox()
        for name in collage.TEMPLATES:
            self.layout_box.addItem(f"{name}  ({collage.cell_count(name)} photos)", name)
        self.layout_box.setCurrentIndex(self.layout_box.findData("2 x 2"))
        form.addRow("Layout", self.layout_box)
        self.aspect = QComboBox()
        for a, label in (("1:1", "Square 1:1"), ("4:5", "Portrait 4:5"), ("9:16", "Story 9:16"),
                         ("16:9", "Wide 16:9"), ("3:2", "Landscape 3:2"), ("2:3", "Tall 2:3")):
            self.aspect.addItem(label, a)
        form.addRow("Shape", self.aspect)
        self.edge = QComboBox()
        for e in (1080, 2048, 3840, 6000):
            self.edge.addItem(f"{e} px on the long side", e)
        self.edge.setCurrentIndex(1)
        form.addRow("Size", self.edge)
        self.spacing = self._slider(0, 100, 15)
        form.addRow("Spacing", self.spacing)
        self.border = self._slider(0, 100, 30)
        form.addRow("Border", self.border)
        self.radius = self._slider(0, 100, 0)
        form.addRow("Corners", self.radius)
        self.bg_b = QPushButton("Background colour…", clicked=self._pick_color)
        form.addRow("", self.bg_b)
        self.format = QComboBox()
        for f, label in (("jpeg", "JPEG"), ("png", "PNG"), ("webp", "WebP")):
            self.format.addItem(label, f)
        form.addRow("Save as", self.format)
        box = QWidget()
        box.setLayout(form)
        box.setMaximumWidth(380)
        self.body.addWidget(box)
        self.canvas = CollageCanvas(self)
        self.body.addWidget(self.canvas, 1)
        self.layout_box.currentIndexChanged.connect(self._options_changed)
        self.aspect.currentIndexChanged.connect(self._options_changed)
        for s in (self.spacing, self.border, self.radius):
            s.valueChanged.connect(self._options_changed)
        self.picker.changed.connect(self._photos_changed)

    def _slider(self, lo: int, hi: int, v: int) -> QSlider:
        s = QSlider(Qt.Orientation.Horizontal, minimum=lo, maximum=hi, value=v)
        s.setToolTip("% of the picture's short side (tenths)")
        return s

    def thumbs(self, fid: int) -> Image.Image:
        if fid not in self._thumbs:
            self._thumbs[fid] = thumb_image(self.conn, fid, 512)
        return self._thumbs[fid]

    def _photos_changed(self) -> None:
        old = {c.file_id: c for c in self.opts.cells if c.file_id}
        cells = [old.get(f, collage.Cell(f)) for f in self.picker.ids()[:9]]
        self.opts = replace(self.opts, cells=cells)
        self._apply()

    def _options_changed(self) -> None:
        self.opts = replace(self.opts, template=self.layout_box.currentData(), aspect=self.aspect.currentData(),
                            spacing=self.spacing.value() / 10, border=self.border.value() / 10,
                            radius=self.radius.value() / 10)
        self._apply()

    def _apply(self) -> None:
        self.canvas._redraw.start()

    def _pick_color(self) -> None:
        c = QColorDialog.getColor(QColor(self.opts.background), self, "Background colour")
        if c.isValid():
            self.opts = replace(self.opts, background=c.name())
            self._apply()

    def set_cell(self, i: int, **changes) -> None:
        cells = collage.fit_cells(self.opts)
        cells[i] = replace(cells[i], **changes)
        self.opts = replace(self.opts, cells=cells)
        self.canvas.rebuild()

    def swap(self, a: int, b: int) -> None:
        self.opts = collage.swap(self.opts, a, b)
        self.canvas.rebuild()

    def make(self) -> None:
        opts = replace(self.opts, cells=collage.fit_cells(self.opts), long_edge=self.edge.currentData())
        try:
            opts.check()
        except ValueError as e:
            QMessageBox.information(self, "Collage", str(e))
            return
        preset = engine.Preset("Collage", format=self.format.currentData(), quality=92)
        folder, name = self.out_dir(), engine.stamp("Collage")
        n = sum(1 for c in opts.cells if c.file_id)

        def work(conn, prog, stop):
            img = collage.render(opts, lambda fid, edge: engine.photo(conn, fid, edge), progress=prog)
            if stop():
                raise animation.Cancelled()
            return engine.save(img, preset, folder, name)
        self.result.setText("")
        self.run("Making the collage…", work, n)


# --- Batch copies ---------------------------------------------------------------------------------

class BatchTool(Tool):
    title_text = "Batch copies"
    blurb = ("Copies of many photos at once, with their edits: resized, in another format, with a watermark, "
             "renamed, with or without their metadata. Always new files in a new folder - the originals are "
             "only read.")

    def __init__(self, conn, parent=None) -> None:
        super().__init__(conn, parent)
        form = QFormLayout()
        self.preset = QComboBox()
        form.addRow("Size and format", self.preset)
        edit_b = QPushButton("Edit the presets…", clicked=self._edit_presets)
        edit_b.setFlat(True)
        form.addRow("", edit_b)
        self.pattern = QLineEdit("{name}")
        self.pattern.setToolTip("{name} the original's name, {n} 001 002…, {date} the capture date")
        form.addRow("Names", self.pattern)
        self.meta = QComboBox()
        for k, label in (("all", "Keep all metadata"), ("no_location", "Keep it, but not the location"),
                         ("none", "Strip all metadata")):
            self.meta.addItem(label, k)
        form.addRow("Metadata", self.meta)
        self.wm_text = QLineEdit(placeholderText="No watermark")
        form.addRow("Watermark", self.wm_text)
        self.wm_pos = QComboBox()
        for p in batch.POSITIONS:
            self.wm_pos.addItem(p.capitalize(), p)
        form.addRow("Where", self.wm_pos)
        self.wm_size = QSpinBox(minimum=1, maximum=30, value=4, suffix=" % of the short side")
        form.addRow("Size", self.wm_size)
        self.wm_opacity = QSpinBox(minimum=5, maximum=100, value=70, suffix=" %")
        form.addRow("Opacity", self.wm_opacity)
        self.wm_color = QComboBox()
        self.wm_color.addItem("White", "white")
        self.wm_color.addItem("Black", "black")
        form.addRow("Colour", self.wm_color)
        box = QWidget()
        box.setLayout(form)
        box.setMaximumWidth(460)
        self.body.addWidget(box)
        self.body.addStretch(1)
        self.load_presets()

    def load_presets(self) -> None:
        self.preset.clear()
        try:
            found = engine.presets(paths.DATA_DIR)
        except engine.PresetError as e:
            QMessageBox.warning(self, "Presets", f"{e}\n\nThe built-in presets are used meanwhile.")
            found = engine.presets()
        for p in found:
            self.preset.addItem(p.name, p)

    def _edit_presets(self) -> None:
        path = engine.write_user_presets(paths.DATA_DIR)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        QMessageBox.information(self, "Presets", f"The presets are in\n{path}\n\nSave the file, then come back: "
                                "they're read again each time you open this tool.")

    def showEvent(self, e) -> None:
        super().showEvent(e)
        keep = self.preset.currentText()
        self.load_presets()
        i = self.preset.findText(keep)
        if i >= 0:
            self.preset.setCurrentIndex(i)

    def options(self) -> batch.BatchOptions:
        return batch.BatchOptions(self.preset.currentData(), self.pattern.text(), self.meta.currentData(),
                                  batch.Watermark(self.wm_text.text(), self.wm_pos.currentData(),
                                                  float(self.wm_size.value()), self.wm_opacity.value(),
                                                  self.wm_color.currentData()))

    def make(self) -> None:
        ids = self.picker.ids()
        opts = self.options()
        try:
            opts.check()
        except ValueError as e:
            QMessageBox.information(self, "Batch copies", str(e))
            return
        folder = self.out_dir() / engine.stamp("Batch")
        self.result.setText("")
        self.run(f"Making {len(ids):,} copies…",
                 lambda conn, prog, stop: batch.run(conn, ids, opts, folder, prog, stop), len(ids))


# --- Contact sheet -------------------------------------------------------------------------------

class ContactSheetTool(Tool):
    title_text = "Contact sheet"
    blurb = ("The photos in a grid on Letter or A4 pages, with their names, dates or stars underneath - to print, "
             "or to send someone to choose from. A PDF with every page, or a PNG per page.")

    def __init__(self, conn, parent=None) -> None:
        super().__init__(conn, parent)
        from lunelis.create import contact_sheet as cs
        self.cs = cs
        form = QFormLayout()
        self.title = QLineEdit(placeholderText="Contact sheet")
        form.addRow("Title", self.title)
        self.page = QComboBox()
        for key, label in (("letter", "Letter (8.5 x 11 in)"), ("a4", "A4")):
            self.page.addItem(label, key)
        form.addRow("Paper", self.page)
        self.landscape = QCheckBox("Landscape")
        form.addRow("", self.landscape)
        self.columns = QSpinBox(minimum=2, maximum=10, value=5, suffix=" across")
        form.addRow("Photos", self.columns)
        self.names = QCheckBox("File names", checked=True)
        self.dates = QCheckBox("Dates", checked=True)
        self.stars = QCheckBox("Stars")
        for w in (self.names, self.dates, self.stars):
            form.addRow("" if w is not self.names else "Captions", w)
        self.format = QComboBox()
        self.format.addItem("PDF (all pages)", "pdf")
        self.format.addItem("PNG (a picture per page)", "png")
        form.addRow("Save as", self.format)
        self.pages_note = QLabel(objectName="Help")
        form.addRow("", self.pages_note)
        box = QWidget()
        box.setLayout(form)
        box.setMaximumWidth(460)
        self.body.addWidget(box)
        self.body.addStretch(1)
        for w in (self.columns,):
            w.valueChanged.connect(self._note)
        for w in (self.page, self.format):
            w.currentIndexChanged.connect(self._note)
        for w in (self.landscape, self.names, self.dates, self.stars):
            w.toggled.connect(self._note)
        self.picker.changed.connect(self._note)

    def options(self):
        return self.cs.SheetOptions(self.page.currentData(), self.landscape.isChecked(), self.columns.value(),
                                    self.title.text().strip(), self.names.isChecked(), self.dates.isChecked(),
                                    self.stars.isChecked(), self.format.currentData())

    def _note(self, *_a) -> None:
        n = len(self.picker.ids())
        self.pages_note.setText(f"{self.cs.pages_needed(self.options(), n):,} page(s)" if n else "")

    def make(self) -> None:
        ids = self.picker.ids()
        opts = self.options()
        try:
            opts.check(len(ids))
        except ValueError as e:
            QMessageBox.information(self, self.title_text, str(e))
            return
        folder, name = self.out_dir(), engine.stamp(opts.title or "Contact sheet")
        self.result.setText("")
        self.run("Making the contact sheet…",
                 lambda conn, prog, stop: self.cs.make(conn, ids, opts, folder, name, prog, stop), len(ids))


# --- Timelapse -----------------------------------------------------------------------------------

class TimelapseTool(Tool):
    title_text = "Timelapse"
    blurb = ("A sequence of photos (an interval shoot) as a smooth video. Deflicker evens out exposure jumps "
             "between frames; stabilise takes out drift and knocks. The photos go in the order of the strip.")
    minimum = 2

    def __init__(self, conn, parent=None) -> None:
        super().__init__(conn, parent)
        from lunelis.create import timelapse
        self.tl = timelapse
        form = QFormLayout()
        self.fps = QSpinBox(minimum=1, maximum=60, value=24, suffix=" frames a second")
        form.addRow("Speed", self.fps)
        self.size = QComboBox()
        for key, label in (("720p", "720p (1280 x 720)"), ("1080p", "1080p (1920 x 1080)"), ("4k", "4K (3840 x 2160)")):
            self.size.addItem(label, key)
        self.size.setCurrentIndex(1)
        form.addRow("Size", self.size)
        self.deflicker = QCheckBox("Deflicker", checked=True)
        form.addRow("", self.deflicker)
        self.window = QSpinBox(minimum=3, maximum=99, value=15, suffix=" frames")
        form.addRow("Even out over", self.window)
        self.stabilize = QCheckBox("Stabilise")
        form.addRow("", self.stabilize)
        self.length = QLabel(objectName="Help")
        form.addRow("", self.length)
        box = QWidget()
        box.setLayout(form)
        box.setMaximumWidth(460)
        self.body.addWidget(box)
        self.body.addStretch(1)
        self.fps.valueChanged.connect(self._length)
        self.picker.changed.connect(self._length)
        self.deflicker.toggled.connect(self.window.setEnabled)

    def _length(self, *_a) -> None:
        n = len(self.picker.ids())
        self.length.setText(f"{n:,} frames = {n / max(1, self.fps.value()):.1f} s of video" if n else "")

    def make(self) -> None:
        ids = self.picker.ids()
        opts = self.tl.TimelapseOptions(self.fps.value(), self.size.currentData(), self.deflicker.isChecked(),
                                        self.window.value(), self.stabilize.isChecked())
        try:
            opts.check(len(ids))
        except ValueError as e:
            QMessageBox.information(self, self.title_text, str(e))
            return
        folder, name = self.out_dir(), engine.stamp("Timelapse")
        self.result.setText("")
        self.run("Making the timelapse…",
                 lambda conn, prog, stop: self.tl.make(conn, ids, opts, folder, name, prog, stop), 2 * len(ids))


# --- Slideshow video -------------------------------------------------------------------------------

class SlideshowTool(Tool):
    title_text = "Slideshow video"
    blurb = ("Photos one after another as a video you can share or play on a TV, with transitions, a slow zoom "
             "and your own music. The photos play in the order of the strip.")

    def __init__(self, conn, parent=None) -> None:
        super().__init__(conn, parent)
        from lunelis.create import slideshow
        self.ss = slideshow
        self.music: str | None = None
        form = QFormLayout()
        self.seconds = QSpinBox(minimum=1, maximum=30, value=3, suffix=" s a photo")
        form.addRow("Timing", self.seconds)
        self.transition = QComboBox()
        for key, label in (("crossfade", "Crossfade"), ("black", "Fade through black"), ("cut", "Cut")):
            self.transition.addItem(label, key)
        form.addRow("Transition", self.transition)
        self.zoom = QCheckBox("Slow zoom (Ken Burns)", checked=True)
        form.addRow("", self.zoom)
        self.fill = QCheckBox("Fill the frame (crop) instead of showing the whole photo")
        form.addRow("", self.fill)
        self.size = QComboBox()
        for key, label in (("1080p", "1080p widescreen"), ("720p", "720p widescreen"), ("square", "Square 1080"),
                           ("vertical", "Vertical 1080 x 1920 (phones)")):
            self.size.addItem(label, key)
        form.addRow("Size", self.size)
        music_row = QHBoxLayout()
        self.music_label = QLabel("No music", objectName="Help")
        music_row.addWidget(self.music_label, 1)
        music_row.addWidget(QPushButton("Choose…", clicked=self._choose_music))
        music_row.addWidget(QPushButton("None", clicked=lambda: self._set_music(None)))
        form.addRow("Music", music_row)
        self.length = QLabel(objectName="Help")
        form.addRow("", self.length)
        box = QWidget()
        box.setLayout(form)
        box.setMaximumWidth(520)
        self.body.addWidget(box)
        self.body.addStretch(1)
        self.seconds.valueChanged.connect(self._length)
        self.picker.changed.connect(self._length)

    def _choose_music(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Music for the slideshow", "",
                                              "Audio (*.mp3 *.m4a *.aac *.wav *.flac *.ogg *.opus);;All files (*)")
        if path:
            self._set_music(path)

    def _set_music(self, path: str | None) -> None:
        self.music = path
        self.music_label.setText(os.path.basename(path) if path else "No music")

    def _length(self, *_a) -> None:
        n = len(self.picker.ids())
        secs = n * self.seconds.value()
        self.length.setText(f"{secs // 60}:{secs % 60:02d} long" if n else "")

    def make(self) -> None:
        ids = self.picker.ids()
        opts = self.ss.SlideshowOptions(float(self.seconds.value()), 0.8, self.transition.currentData(),
                                        self.zoom.isChecked(), self.fill.isChecked(), self.size.currentData(),
                                        self.music)
        try:
            opts.check(len(ids))
        except ValueError as e:
            QMessageBox.information(self, self.title_text, str(e))
            return
        folder, name = self.out_dir(), engine.stamp("Slideshow")
        self.result.setText("")
        self.run("Making the slideshow…",
                 lambda conn, prog, stop: self.ss.make(conn, ids, opts, folder, name, prog, stop), len(ids))


# --- Before and after ------------------------------------------------------------------------------

class BeforeAfterTool(Tool):
    title_text = "Before and after"
    blurb = ("Each photo as it was shot next to how you edited it - side by side, one above the other, or a "
             "slider that sweeps across (a video or GIF). One file per photo.")

    def __init__(self, conn, parent=None) -> None:
        super().__init__(conn, parent)
        from lunelis.create import before_after
        self.ba = before_after
        form = QFormLayout()
        self.layout_box = QComboBox()
        for key, label in (("side", "Side by side"), ("stacked", "One above the other"), ("slider", "Slider")):
            self.layout_box.addItem(label, key)
        form.addRow("Layout", self.layout_box)
        self.kind = QComboBox()
        self.kind.addItem("MP4 video", "mp4")
        self.kind.addItem("GIF", "gif")
        form.addRow("Slider as", self.kind)
        self.labels = QCheckBox("Label Before and After", checked=True)
        form.addRow("", self.labels)
        self.edge = QComboBox()
        for e in (1080, 2048, 3840):
            self.edge.addItem(f"{e} px", e)
        self.edge.setCurrentIndex(1)
        form.addRow("Size", self.edge)
        box = QWidget()
        box.setLayout(form)
        box.setMaximumWidth(460)
        self.body.addWidget(box)
        self.body.addStretch(1)
        self.layout_box.currentIndexChanged.connect(lambda _i: self.kind.setEnabled(self.layout_box.currentData() == "slider"))
        self.kind.setEnabled(False)

    def make(self) -> None:
        ids = self.picker.ids()
        opts = self.ba.BeforeAfterOptions(self.layout_box.currentData(), self.edge.currentData(),
                                          self.labels.isChecked(), self.kind.currentData())
        folder, name = self.out_dir(), engine.stamp("Before and after")
        self.result.setText("")
        self.run("Making before and after…",
                 lambda conn, prog, stop: self.ba.make(conn, ids, opts, folder, name, prog, stop), len(ids))


# --- the page ---------------------------------------------------------------------------------

CARD_ICONS = {"animation": "status", "collage": "create", "batch": "duplicates", "contact": "albums",
              "timelapse": "status", "slideshow": "library", "before_after": "edit", "print": "backups"}


class _Card(QFrame):
    """A tool on the home page: icon, name and a line about it; the whole card clicks."""

    clicked = Signal()

    def __init__(self, name: str, blurb: str, icon_name: str) -> None:
        super().__init__(objectName="CreateCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(110)
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 16)
        from lunelis.ui import icons, theme
        head = QHBoxLayout()
        pic = QLabel()
        pic.setPixmap(icons.icon(icon_name, theme.current().accent).pixmap(28, 28))
        head.addWidget(pic)
        head.addWidget(QLabel(name, objectName="SectionTitle"), 1)
        v.addLayout(head)
        text = QLabel(blurb, objectName="Help")
        text.setWordWrap(True)
        v.addWidget(text)
        v.addStretch(1)
        self.setToolTip(f"Open {name}")

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
        else:
            super().keyPressEvent(e)



TOOLS = (
    ("animation", "Animation", "GIF, WebP or MP4 from a burst or a few photos.", AnimationTool),
    ("collage", "Collage", "Photos side by side on one picture, in a layout you choose.", CollageTool),
    ("batch", "Batch copies", "Resize, convert, watermark, rename or strip metadata - as new files.", BatchTool),
    ("contact", "Contact sheet", "A grid of photos with captions on printable pages - PDF or PNG.", ContactSheetTool),
    ("timelapse", "Timelapse", "An interval shoot as a smooth video, deflickered and stabilised.", TimelapseTool),
    ("slideshow", "Slideshow video", "Photos as a video with transitions, a slow zoom and music.", SlideshowTool),
    ("before_after", "Before and after", "The original next to your edit - side by side or a sweeping slider.",
     BeforeAfterTool),
)


class CreatePage(QWidget):
    def __init__(self, conn, parent=None) -> None:
        super().__init__(parent)
        self.conn = conn
        self._selection: list[int] = []
        self._view_filter = Filter()
        self._sort: str | None = None
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        v.addWidget(self.stack)
        home = QWidget()
        h = QVBoxLayout(home)
        h.setContentsMargins(24, 16, 24, 16)
        title = QLabel("Create", objectName="PageTitle")
        h.addWidget(title)
        intro = QLabel("New files made from your photos, with their edits. Your photos are only read - everything "
                       "made here is a new file in one folder.", objectName="Help")
        intro.setWordWrap(True)
        h.addWidget(intro)
        where = QHBoxLayout()
        self.where = QLabel(objectName="Help")
        self.where.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        where.addWidget(self.where, 1)
        where.addWidget(QPushButton("Open the folder", clicked=self.open_folder))
        where.addWidget(QPushButton("Change…", clicked=self.change_folder))
        h.addLayout(where)
        grid = QGridLayout()
        grid.setSpacing(16)
        self.tools: dict[str, Tool] = {}
        self.cards: dict[str, _Card] = {}
        for n, (key, name, blurb, cls) in enumerate(TOOLS):
            card = _Card(name, blurb, CARD_ICONS.get(key, "create"))
            card.clicked.connect(lambda k=key: self.open_tool(k))
            grid.addWidget(card, n // 3, n % 3)
            grid.setColumnStretch(n % 3, 1)
            self.cards[key] = card
            tool = cls(conn)
            tool.back.connect(lambda: self.stack.setCurrentIndex(0))
            self.tools[key] = tool
        h.addLayout(grid)
        h.addStretch(1)
        home_scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.Shape.NoFrame)
        home_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        home_scroll.setWidget(home)                       # more tools than the window is tall: it scrolls
        self.stack.addWidget(home_scroll)
        # Each tool scrolls up and down on a short window; its width follows
        # the window, so its text wraps instead of pushing things off-screen.
        self._scrolls: dict[str, QScrollArea] = {}
        for key, tool in self.tools.items():
            sc = QScrollArea(widgetResizable=True, frameShape=QFrame.Shape.NoFrame)
            sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            tool.setMinimumHeight(560)
            sc.setWidget(tool)
            self._scrolls[key] = sc
            self.stack.addWidget(sc)

    def set_library_context(self, selection: list[int], view_filter: Filter, sort: str | None) -> None:
        self._selection, self._view_filter, self._sort = list(selection), view_filter, sort

    def refresh(self) -> None:
        self.where.setText(f"Saved to: {engine.output_dir(self.conn)}")

    def open_tool(self, key: str) -> None:
        tool = self.tools[key]
        tool.picker.set_library_context(self._selection, self._view_filter, self._sort)
        tool.picker.choose_default()
        self.stack.setCurrentWidget(self._scrolls[key])

    def open_folder(self) -> None:
        folder = engine.output_dir(self.conn)
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def change_folder(self) -> None:
        from lunelis.settings import Settings
        picked = QFileDialog.getExistingDirectory(self, "Save what Create makes in which folder?",
                                                  str(engine.output_dir(self.conn)))
        if picked:
            Settings(self.conn).set("create_output_dir", os.path.normpath(picked))
            self.refresh()
