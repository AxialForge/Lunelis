"""
The photo detail view (double-click a photo, or Enter) - the Detail mockup:
a big photo on a dark background, a filmstrip underneath, and an Info panel
on the right. Left/Right (or the mouse wheel) step through the same list the
library shows, in the same order and with the same filters.

The big image is the photo's own embedded preview (for a RAW) or the image
itself, decoded at about screen size on a background thread. Until it
arrives, the grid's 512 px thumbnail is shown scaled, so stepping through is
instant and sharpens a moment later; the next and previous photos are
decoded ahead of time.

Ratings, labels and flags use the app's usual keys (0-5, 6-9, P/X/U) and
the buttons in the Info panel; the photo shown is the one they apply to.

E (or Edit) switches the right panel to Develop (develop.py); edit mode
stays on while you step through photos. An edited photo shows its cached
proxy (edit/render.py), made again from the stack if the cache is gone.
"""
from __future__ import annotations

import os
import time
from collections import OrderedDict

from PySide6.QtCore import QObject, QPoint, QPointF, QRect, QRectF, QRunnable, Qt, QThreadPool, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from lunelis import paths
from lunelis.raw.thumbnails import cache_rel_path
from lunelis.ui import photoinfo
from lunelis.ui import theme as themes
from lunelis.ui.library import LibraryIndex
from lunelis.ui.theme import label_color, qcolor
from lunelis.ui.thumbcache import ThumbCache
from lunelis.xmp.sidecar import LABELS
from lunelis.ui.background import unless_closed

PREVIEW_EDGE = 2560          # plenty for a 1440p/4K window without decoding 60 MP
PREVIEW_CACHE = 8


# --- background decode --------------------------------------------------------------

class _PreviewSignals(QObject):
    loaded = Signal(int, QImage)       # file id, image (null = couldn't)


class _PreviewLoad(QRunnable):
    def __init__(self, signals, file_id: int, path: str, orientation: int | None, edit=None) -> None:
        super().__init__()
        self.signals, self.file_id, self.path, self.orientation = signals, file_id, path, orientation
        self.edit = edit                   # (is_raw, stack, filter params) of an edited photo
        # Kept by PreviewCache (not auto-deleted) so a queued load can be taken back.
        self.setAutoDelete(False)

    def run(self) -> None:
        try:
            from lunelis.raw.thumbnails import render
            if self.edit is not None:
                from PIL import Image
                from lunelis.edit import render as edit_render
                proxy = edit_render.proxy_path(paths.EDIT_CACHE, self.file_id)
                if proxy.exists():
                    with Image.open(proxy) as im:
                        img = im.convert("RGB")
                else:
                    # Rendered in memory only: the edit controller is the one
                    # writer of proxies/thumbnails (a write from here could land
                    # after a Reset and leave a stale edited thumbnail).
                    from lunelis.edit import pipeline
                    is_raw, stack, fparams = self.edit
                    from lunelis.edit import ai
                    src = edit_render.load_source(self.path, is_raw, PREVIEW_EDGE)
                    from lunelis.edit import lens
                    # Cached AI masks only: running the model here would race the
                    # edit controller (the one that computes and stores them).
                    img = pipeline.to_image(pipeline.apply(src, stack, fparams,
                                                           ai.maps_for(self.file_id, stack, None),
                                                           lens.info_for_id(self.file_id) if stack.lens else None))
            else:
                img = render(self.path, self.orientation, edge=PREVIEW_EDGE).convert("RGB")
            data = img.tobytes()
            q = QImage(data, img.width, img.height, 3 * img.width, QImage.Format.Format_RGB888).copy()
        except Exception:
            q = QImage()
        self.signals.loaded.emit(self.file_id, q)


class _FullSignals(QObject):
    loaded = Signal(int, QImage)


class _FullLoad(QRunnable):
    """The photo at its own full resolution, for zooming past the preview
    (a RAW is decoded - ~2 s for 60 MP; a JPEG/HEIC read whole)."""

    def __init__(self, signals, file_id: int, path: str, is_raw: bool) -> None:
        super().__init__()
        self.signals, self.file_id, self.path, self.is_raw = signals, file_id, path, is_raw

    def run(self) -> None:
        try:
            if self.is_raw:
                import rawpy
                with rawpy.imread(self.path) as raw:
                    rgb = raw.postprocess(use_camera_wb=True, output_bps=8)
                h, w = rgb.shape[:2]
                q = QImage(rgb.tobytes(), w, h, 3 * w, QImage.Format.Format_RGB888).copy()
            else:
                from PIL import Image, ImageOps
                from lunelis.raw.thumbnails import _to_srgb
                with Image.open(self.path) as im:
                    im.load()
                    img = _to_srgb(ImageOps.exif_transpose(im)).convert("RGB")
                q = QImage(img.tobytes(), img.width, img.height, 3 * img.width, QImage.Format.Format_RGB888).copy()
        except Exception:
            q = QImage()
        self.signals.loaded.emit(self.file_id, q)


class PreviewCache(QObject):
    """file id -> full-size QPixmap, a few at a time, loaded off the GUI thread."""

    ready = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pix: OrderedDict[int, QPixmap] = OrderedDict()
        self._pending: dict[int, _PreviewLoad] = {}
        self.failed: set[int] = set()
        self._signals = _PreviewSignals()
        self._signals.loaded.connect(self._loaded)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)

    def get(self, info: photoinfo.PhotoInfo, edit=None) -> QPixmap | None:
        pix = self._pix.get(info.file_id)
        if pix is not None:
            self._pix.move_to_end(info.file_id)
            return pix
        if info.file_id not in self._pending and info.file_id not in self.failed:
            job = _PreviewLoad(self._signals, info.file_id, info.path, info.orientation, edit)
            self._pending[info.file_id] = job
            self.pool.start(job)
        return None

    def keep_only(self, file_ids) -> None:
        """Take back queued loads for photos no longer wanted (holding an arrow
        key queues a pair of neighbours per photo passed; without this the
        photo you stop on waits behind all of them)."""
        wanted = set(file_ids)
        for fid, job in list(self._pending.items()):
            if fid not in wanted and self.pool.tryTake(job):     # False once it has started
                del self._pending[fid]

    def forget(self, file_id: int) -> None:
        self._pix.pop(file_id, None)
        self.failed.discard(file_id)

    def _loaded(self, file_id: int, img: QImage) -> None:
        self._pending.pop(file_id, None)
        if img.isNull():
            self.failed.add(file_id)
        else:
            self._pix[file_id] = QPixmap.fromImage(img)
            while len(self._pix) > PREVIEW_CACHE:
                self._pix.popitem(last=False)
        self.ready.emit(file_id)


# --- the photo ----------------------------------------------------------------------------

class PhotoCanvas(QWidget):
    """The big picture. The mouse wheel zooms, around the pointer, from
    "fit" up to 400 %; double-click (or Z) jumps between fit and 100 %;
    drag to pan (left button, or the middle button in any mode). Tilting
    the wheel left / right (on mice that have it) steps to the previous /
    next photo, once per TILT_REPEAT however long it's held. The zoom
    is measured against the ORIGINAL photo's pixels (`full_size`), and when
    it asks for more pixels than the shown image has, `wants_detail` is
    emitted so the view can load the full-resolution picture."""

    step = Signal(int)                 # previous / next photo (wheel tilt, or the "step" wheel mode)
    wants_detail = Signal()
    MAX_ZOOM = 4.0
    TILT_REPEAT = 0.3                  # s: a held tilt sends a stream of events; one photo per this

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.pix: QPixmap | None = None
        self.sharp = False             # the full preview (not the scaled-up thumbnail)
        self.message = ""
        self.scale: float | None = None   # screen px per original px; None = fit
        self.center = QPointF(0.5, 0.5)   # zoomed: the image point at the view's centre (0-1)
        self.full_size: tuple[int, int] | None = None
        self._pan: QPointF | None = None
        self._asked = False
        self._last_tilt = 0.0
        self.setMinimumSize(200, 100)                 # short windows (the 900 x 350 minimum) still fit
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    @property
    def zoomed(self) -> bool:
        return self.scale is not None

    @zoomed.setter
    def zoomed(self, on: bool) -> None:
        if not on:
            self.scale = None
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def show_pixmap(self, pix: QPixmap | None, sharp: bool, message: str = "", keep_zoom: bool = False) -> None:
        if not keep_zoom and (not sharp or pix is None):
            self.scale = None
        self.pix, self.sharp, self.message = pix, sharp, message
        self.update()

    def set_photo(self, full_size: tuple[int, int] | None) -> None:
        """A new photo: back to fit."""
        self.full_size = full_size if full_size and full_size[0] and full_size[1] else None
        self.scale = None
        self.center = QPointF(0.5, 0.5)
        self._asked = False
        self.setCursor(Qt.CursorShape.ArrowCursor)

    # --- geometry ---

    def _dims(self) -> tuple[float, float]:
        """The photo's size in original pixels (or the pixmap's)."""
        if self.full_size:
            fw, fh = self.full_size
            # The pixmap shows the photo upright; full_size may be the sensor's way round.
            if (self.pix.width() > self.pix.height()) != (fw > fh):
                fw, fh = fh, fw
            return float(fw), float(fh)
        return float(self.pix.width()), float(self.pix.height())

    def fit_scale(self) -> float:
        fw, fh = self._dims()
        return min(self.width() / fw, self.height() / fh)

    def _fit_rect(self) -> QRectF:
        """Where the picture is drawn now (fit or zoomed) - the crop and mask
        tools map through this, so they follow the zoom."""
        if self.pix is None:
            return QRectF(self.rect())
        fw, fh = self._dims()
        s = self.scale if self.scale is not None else self.fit_scale()
        w, h = fw * s, fh * s
        if self.scale is None:
            return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)
        return QRectF(self.width() / 2 - self.center.x() * w, self.height() / 2 - self.center.y() * h, w, h)

    def zoom_to(self, scale: float | None, at: QPointF | None = None) -> None:
        if self.pix is None:
            return
        fit = self.fit_scale()
        if scale is not None and scale <= fit * 1.001:
            scale = None
        if scale is not None:
            scale = min(self.MAX_ZOOM, scale)
        if at is not None:
            # Keep the image point under the pointer where it is.
            r = self._fit_rect()
            u = min(1.0, max(0.0, (at.x() - r.x()) / r.width()))
            v = min(1.0, max(0.0, (at.y() - r.y()) / r.height()))
            self.scale = scale
            if scale is not None:
                fw, fh = self._dims()
                self.center = QPointF(u - (at.x() - self.width() / 2) / (fw * scale),
                                      v - (at.y() - self.height() / 2) / (fh * scale))
        else:
            self.scale = scale
        self._clamp()
        self.setCursor(Qt.CursorShape.OpenHandCursor if self.scale is not None else Qt.CursorShape.ArrowCursor)
        self._maybe_ask()
        self.update()

    def _clamp(self) -> None:
        if self.scale is None:
            self.center = QPointF(0.5, 0.5)
            return
        fw, fh = self._dims()
        hw = min(0.5, self.width() / (2 * fw * self.scale))
        hh = min(0.5, self.height() / (2 * fh * self.scale))
        self.center = QPointF(min(1 - hw, max(hw, self.center.x())), min(1 - hh, max(hh, self.center.y())))

    def _maybe_ask(self) -> None:
        if self.scale is None or self._asked or self.pix is None:
            return
        fw, _fh = self._dims()
        dpr = max(self.devicePixelRatioF(), 1.0)
        if fw * self.scale * dpr > self.pix.width() * 1.05:
            self._asked = True
            self.wants_detail.emit()

    def toggle_zoom(self, at: QPointF | None = None) -> None:
        if self.pix is None:
            return
        if self.scale is None:
            self.zoom_to(1.0 / max(self.devicePixelRatioF(), 1.0), at or QPointF(self.width() / 2, self.height() / 2))
        else:
            self.zoom_to(None)

    # --- painting ---

    def paintEvent(self, e) -> None:
        t = themes.current()
        p = QPainter(self)
        p.fillRect(self.rect(), qcolor(t.viewer_bg))
        if self.pix is None:
            p.setPen(qcolor(t.text_faint))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.message or "Loading…")
            return
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawPixmap(self._fit_rect(), self.pix, QRectF(self.pix.rect()))
        if self.scale is not None:
            dpr = max(self.devicePixelRatioF(), 1.0)
            text = f"{self.scale * dpr * 100:.0f} %"
            p.setPen(qcolor(t.text_faint))
            p.drawText(self.rect().adjusted(12, 0, 0, -8), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, text)
        if not self.sharp or self.message:
            p.setPen(qcolor(t.text_faint))
            p.drawText(self.rect().adjusted(0, 0, -12, -8), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
                       self.message or "Loading full size…")

    def resizeEvent(self, e) -> None:
        self._clamp()
        super().resizeEvent(e)

    # --- mouse ---

    wheel_mode = "zoom"                # Settings > General: zoom | step

    def wheelEvent(self, e) -> None:
        dx, dy = e.angleDelta().x(), e.angleDelta().y()
        if abs(dx) > abs(dy) and not (e.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            # Tilting the wheel: left = previous photo, right = next.
            now = time.monotonic()
            if now - self._last_tilt >= self.TILT_REPEAT:
                self._last_tilt = now
                self.step.emit(-1 if dx > 0 else 1)
            e.accept()
            return
        if not dy or self.pix is None:
            return
        if self.wheel_mode == "step" and not (e.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.step.emit(-1 if dy > 0 else 1)      # Ctrl + wheel still zooms
            return
        cur = self.scale if self.scale is not None else self.fit_scale()
        self.zoom_to(cur * (1.2 ** (dy / 120)), e.position())
        e.accept()

    def mouseDoubleClickEvent(self, e) -> None:
        self.toggle_zoom(e.position())

    def mousePressEvent(self, e) -> None:
        if self.scale is not None and e.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            self._pan = e.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, e) -> None:
        if self._pan is not None and self.pix is not None and self.scale is not None:
            d = e.position() - self._pan
            self._pan = e.position()
            r = self._fit_rect()
            self.center = QPointF(self.center.x() - d.x() / r.width(), self.center.y() - d.y() / r.height())
            self._clamp()
            self.update()

    def mouseReleaseEvent(self, e) -> None:
        if self._pan is not None:
            self._pan = None
            self.setCursor(Qt.CursorShape.OpenHandCursor if self.scale is not None else Qt.CursorShape.ArrowCursor)


# --- the filmstrip ------------------------------------------------------------------------------

class Filmstrip(QWidget):
    """The library's photos around the current one; click to jump."""

    picked = Signal(int)               # position in the index
    GAP = 8

    def __init__(self, thumbs: ThumbCache, parent=None) -> None:
        super().__init__(parent)
        self.thumbs = thumbs
        self.thumbs.ready.connect(lambda _: self.update())
        self.index = LibraryIndex()
        self.pos = 0
        self.setMinimumHeight(56)             # drag the divider above it: the thumbnails follow
        self.setMaximumHeight(260)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    @property
    def THUMB(self) -> int:
        return max(32, self.height() - 24)

    def set_position(self, index: LibraryIndex, pos: int) -> None:
        self.index, self.pos = index, pos
        self.update()

    def _slots(self) -> tuple[int, int]:
        per = max(1, (self.width() - 16) // (self.THUMB + self.GAP))
        first = max(0, min(self.pos - per // 2, len(self.index) - per))
        return first, per

    def paintEvent(self, e) -> None:
        t = themes.current()
        p = QPainter(self)
        p.fillRect(self.rect(), qcolor(t.viewer_strip))
        n = len(self.index)
        if not n:
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        first, per = self._slots()
        total_w = min(per, n) * (self.THUMB + self.GAP) - self.GAP
        x0 = max(16, (self.width() - total_w) // 2)
        y = (self.height() - self.THUMB) // 2
        for k, i in enumerate(range(first, min(n, first + per))):
            r = QRect(x0 + k * (self.THUMB + self.GAP), y, self.THUMB, self.THUMB)
            path = QPainterPath()
            path.addRoundedRect(QRectF(r), 6, 6)
            row = self.index.rows[i]
            pix = None if row[2] else self.thumbs.get(row[0], row[1] or cache_rel_path(row[0]))
            if pix is not None:
                p.save()
                p.setClipPath(path)
                p.drawPixmap(r, pix)
                p.restore()
            else:
                p.fillPath(path, qcolor(t.tile_unavailable))
            if i == self.pos:
                p.setPen(QPen(qcolor(t.badge_text), 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(QRectF(r).adjusted(1, 1, -1, -1), 6, 6)

    def mousePressEvent(self, e) -> None:
        first, per = self._slots()
        n = len(self.index)
        total_w = min(per, n) * (self.THUMB + self.GAP) - self.GAP
        x0 = max(16, (self.width() - total_w) // 2)
        k = (int(e.position().x()) - x0) // (self.THUMB + self.GAP)
        if 0 <= k < per and first + k < n:
            self.picked.emit(first + k)

    def wheelEvent(self, e) -> None:
        dy = e.angleDelta().y() or e.angleDelta().x()
        if dy and len(self.index):
            self.picked.emit(max(0, min(len(self.index) - 1, self.pos + (-1 if dy > 0 else 1))))


# --- the Info panel -----------------------------------------------------------------------------

DETAIL_FIELDS = (
    ("Mode", ("EXIF ExposureProgram", "MakerNote ExposureMode")),
    ("Metering", ("EXIF MeteringMode",)),
    ("Focus", ("MakerNote FocusMode", "MakerNote AFMode")),
    ("Drive", ("MakerNote ReleaseMode", "MakerNote DriveMode")),
    ("Flash", ("EXIF Flash",)),
    ("White balance", ("MakerNote WhiteBalance", "EXIF WhiteBalance")),
    ("Style", ("MakerNote CreativeStyle", "MakerNote PictureControlName", "MakerNote FilmMode")),
    ("Stabilization", ("MakerNote ImageStabilization", "MakerNote ImageStabilization2")),
    ("DRO / HDR", ("MakerNote DynamicRangeOptimizer", "MakerNote HDR")),
    ("35mm focal length", ("EXIF FocalLengthIn35mmFilm",)),
    ("Lens range", ("EXIF LensSpecification",)),
    ("Time zone", ("EXIF OffsetTimeOriginal", "EXIF OffsetTime")),
    ("Altitude", ("GPS GPSAltitude",)),
    ("Camera serial", ("EXIF BodySerialNumber", "MakerNote SerialNumber")),
    ("Firmware", ("Image Software",)),
    ("Artist", ("Image Artist",)),
    ("Copyright", ("Image Copyright",)),
)


class InfoPanel(QScrollArea):
    rate = Signal(dict)                # {"stars": n} / {"label": name|None} / {"flag": ...}
    show_event = Signal(int, str)
    tags_added = Signal(list)
    tag_removed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsScroll")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFixedWidth(340)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page = QWidget(objectName="DetailPanel")
        self.v = QVBoxLayout(page)
        self.v.setContentsMargins(20, 16, 20, 20)
        self.v.setSpacing(4)
        self.setWidget(page)
        self.info: photoinfo.PhotoInfo | None = None

        self.title = QLabel(objectName="SectionTitle")
        self.title.setWordWrap(True)
        self.v.addWidget(self.title)
        self.kind = QLabel(objectName="Help")
        self.v.addWidget(self.kind)
        self.v.addSpacing(10)

        # Rating, label, flag - clickable.
        self._heading("Rating")
        row = QHBoxLayout()
        row.setSpacing(2)
        self.star_buttons = []
        for n in range(1, 6):
            b = QPushButton("☆", objectName="StarButton")
            b.setToolTip(f"{n} star{'s' if n > 1 else ''} (key {n}) - click again to clear")
            b.clicked.connect(lambda _=False, n=n: self._star(n))
            row.addWidget(b)
            self.star_buttons.append(b)
        row.addStretch(1)
        self.v.addLayout(row)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.label_buttons = {}
        for name in LABELS:
            b = QPushButton(objectName="LabelButton")
            b.setFixedSize(22, 22)
            b.setToolTip(f"{name} label - click again to remove")
            b.clicked.connect(lambda _=False, name=name: self._label(name))
            row.addWidget(b)
            self.label_buttons[name] = b
        row.addSpacing(10)
        self.pick_b = QPushButton("Pick", objectName="FlagButton", checkable=True)
        self.pick_b.setToolTip("Pick (P)")
        self.pick_b.clicked.connect(lambda: self.rate.emit({"flag": "pick" if self.pick_b.isChecked() else None}))
        self.reject_b = QPushButton("Reject", objectName="FlagButton", checkable=True)
        self.reject_b.setToolTip("Reject (X)")
        self.reject_b.clicked.connect(lambda: self.rate.emit({"flag": "reject" if self.reject_b.isChecked() else None}))
        row.addWidget(self.pick_b)
        row.addWidget(self.reject_b)
        row.addStretch(1)
        self.v.addLayout(row)
        self.v.addSpacing(10)

        self._heading("Tags")
        self.tag_box = None                # made by the view (it needs the catalog)

        self.fields: dict[str, QLabel] = {}
        for key, heading in (("when", "Taken"), ("camera", "Camera"), ("lens", "Lens"), ("exposure", "Exposure"),
                             ("dimensions", "Size"), ("event", "Event"), ("location", "Location"),
                             ("albums", "Albums"), ("status", "Condition"), ("backup", "Backup"), ("path", "File"),
                             ("sidecar", "Sidecar")):
            h = self._heading(heading)
            lab = QLabel()
            lab.setWordWrap(True)
            lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse
                                        | Qt.TextInteractionFlag.LinksAccessibleByMouse)
            lab.linkActivated.connect(self._link)
            self.v.addWidget(lab)
            self.fields[key] = lab
            lab.setProperty("heading", h)
        self._heading("Shooting details")
        self.details = QLabel(objectName="Help")
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.v.addWidget(self.details)
        from PySide6.QtWidgets import QToolButton
        self.all_b = QToolButton(text="▸ All metadata", checkable=True)
        self.all_b.setAutoRaise(True)
        self.all_b.toggled.connect(self._toggle_all)
        self.v.addWidget(self.all_b)
        self.all_meta = QLabel(objectName="Help")
        self.all_meta.setWordWrap(True)
        self.all_meta.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.all_meta.hide()
        self.v.addWidget(self.all_meta)
        self.v.addSpacing(8)
        self.folder_b = QPushButton("Show in folder", clicked=self._show_in_folder)
        self.open_b = QPushButton("Open with default app", clicked=self._open)
        for b in (self.folder_b, self.open_b):
            self.v.addWidget(b)
        self.v.addStretch(1)

    def _heading(self, text: str) -> QLabel:
        h = QLabel(text.upper(), objectName="FilterLabel")
        h.setContentsMargins(0, 8, 0, 0)
        self.v.addWidget(h)
        return h

    def _toggle_all(self, on: bool) -> None:
        self.all_b.setText(("▾ " if on else "▸ ") + "All metadata")
        self.all_meta.setVisible(on)

    def show_metadata(self, raw: dict) -> None:
        """The curated shooting details, and every tag the file carries."""
        from html import escape
        rows = [(label, raw[k]) for label, keys in DETAIL_FIELDS for k in keys if raw.get(k) not in (None, "", "Unknown")
                for _ in [0]]
        seen, shown = set(), []
        for label, value in rows:
            if label not in seen:
                seen.add(label)
                shown.append(f"<b>{escape(label)}</b>&nbsp; {escape(str(value))}")
        self.details.setText("<br>".join(shown) or "No shooting details recorded.")
        groups: dict[str, list[str]] = {}
        for key, value in sorted(raw.items()):
            group, _, name = key.partition(" ")
            if not name or any(skip in name for skip in ("Offset", "JPEGInterchange", "Thumbnail", "SubIFDs")):
                continue
            groups.setdefault(group, []).append(f"{escape(name)}: {escape(str(value)[:120])}")
        self.all_meta.setText("<br>".join(f"<br><b>{escape(g)}</b><br>" + "<br>".join(lines)
                                          for g, lines in groups.items()) or "Nothing more recorded.")
        self.all_b.setVisible(bool(raw))

    def attach_tags(self, box) -> None:
        # Right under the TAGS heading.
        i = next(i for i in range(self.v.count())
                 if isinstance(self.v.itemAt(i).widget(), QLabel) and self.v.itemAt(i).widget().text() == "TAGS")
        self.v.insertWidget(i + 1, box)
        self.tag_box = box
        box.added.connect(self.tags_added.emit)
        box.removed.connect(self.tag_removed.emit)

    def _star(self, n: int) -> None:
        cur = self.info.stars if self.info else 0
        self.rate.emit({"stars": 0 if cur == n else n})

    def _label(self, name: str) -> None:
        cur = self.info.label if self.info else None
        self.rate.emit({"label": None if cur == name else name})

    def _link(self, href: str) -> None:
        if href.startswith("event:"):
            _, eid, name = href.split(":", 2)
            self.show_event.emit(int(eid), name)
        else:
            QDesktopServices.openUrl(QUrl(href))

    def _show_in_folder(self) -> None:
        if self.info:
            import subprocess
            subprocess.Popen(["explorer", "/select,", os.path.normpath(self.info.path)])

    def _open(self) -> None:
        if self.info:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.info.path))

    def show_info(self, info: photoinfo.PhotoInfo) -> None:
        self.info = info
        t = themes.current()
        self.title.setText(info.filename)
        self.kind.setText("  ·  ".join(x for x in (info.kind(), info.size_text(), info.length()) if x))
        for n, b in enumerate(self.star_buttons, 1):
            b.setText("★" if n <= info.stars else "☆")
            b.setStyleSheet(f"color: {t.rating if n <= info.stars else t.text_faint};")
        for name, b in self.label_buttons.items():
            on = info.label == name
            b.setStyleSheet(f"background: {label_color(name)}; border-radius: 11px;"
                            f" border: {3 if on else 1}px solid {t.text if on else t.border};")
        self.pick_b.setChecked(info.flag == "pick")
        self.reject_b.setChecked(info.flag == "reject")

        def put(key: str, text: str) -> None:
            lab = self.fields[key]
            lab.setText(text)
            lab.setVisible(bool(text))
            lab.property("heading").setVisible(bool(text))

        when = info.when()
        if info.date_source == "takeout":
            when += "  (from Google Takeout)"
        put("when", when)
        put("camera", info.camera())
        put("lens", info.lens or "")
        put("exposure", info.exposure())
        put("dimensions", info.dimensions() + (" · Motion photo (a short video is inside)" if info.motion_video else ""))
        put("event", f'<a href="event:{info.event_id}:{info.event}">{info.event}</a>' if info.event else "")
        url = info.map_url()
        put("location", f'{info.lat:.5f}, {info.lon:.5f}  ·  <a href="{url}">Open map</a>' if url else "")
        from lunelis.damage.check import PROBLEM_TEXT
        put("status", PROBLEM_TEXT.get(info.damaged, info.damaged) if info.damaged else "")
        put("backup", info.protection)
        put("path", photoinfo.breakable(info.path))
        put("sidecar", info.sidecar or "")


# --- the page ------------------------------------------------------------------------------------

class DetailView(QWidget):
    back = Signal()
    rate = Signal(dict)
    current_changed = Signal(int)      # file id now shown (the app's rating target)
    show_event = Signal(int, str)
    edited = Signal(int)               # a photo's edit was saved and its thumbnail re-rendered

    def __init__(self, conn, parent=None, workspace: bool = False) -> None:
        super().__init__(parent)
        self.conn = conn
        # workspace = the Edit page's view: always editing, no way "back".
        self.workspace = workspace
        self.index = LibraryIndex()
        self.pos = -1
        self.info: photoinfo.PhotoInfo | None = None
        self.previews = PreviewCache(self)
        self.previews.ready.connect(self._preview_ready)
        # The grid's thumbnails at 512 px: instant placeholders; filmstrip at 128.
        self.big_thumbs = ThumbCache(paths.THUMBNAIL_CACHE, self)
        self.big_thumbs.set_tile_size(512)
        self.big_thumbs.ready.connect(self._thumb_ready)
        self.strip_thumbs = ThumbCache(paths.THUMBNAIL_CACHE, self)
        self.strip_thumbs.set_tile_size(128)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        bar = QWidget(objectName="Toolbar")
        bar.setFixedHeight(56)
        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 0, 16, 0)
        h.setSpacing(14)
        back = self.back_b = QPushButton("‹  Back to Library", clicked=lambda: self.back.emit())
        back.setFlat(True)
        back.setToolTip("Esc")
        h.addWidget(back)
        self.name = QLabel()
        self.name.setStyleSheet("font-weight: 600;")
        h.addWidget(self.name)
        self.summary = QLabel(objectName="Count")
        # The details line gives way on narrow windows (it's all in the Info panel
        # and the tooltip too) instead of forcing the window wider.
        self.summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.summary.setMinimumWidth(0)
        h.addWidget(self.summary, 1)
        self.stars = QLabel()
        h.addWidget(self.stars)
        self.edit_b = QPushButton("Edit", checkable=True)
        self.edit_b.setToolTip("Edit this photo (E) - the original is never changed")
        self.edit_b.toggled.connect(self.set_editing)
        h.addWidget(self.edit_b)
        self.prev_b = QPushButton("‹", clicked=lambda: self.go(self.pos - 1))
        self.prev_b.setToolTip("Previous (Left)")
        self.counter = QLabel(objectName="ToolLabel")
        self.next_b = QPushButton("›", clicked=lambda: self.go(self.pos + 1))
        self.next_b.setToolTip("Next (Right)")
        for w in (self.prev_b, self.counter, self.next_b):
            h.addWidget(w)
        outer.addWidget(bar)

        body = QHBoxLayout()
        body.setSpacing(0)
        left = QVBoxLayout()
        left.setSpacing(0)
        from lunelis.ui.develop import DevelopPanel, EditCanvas, EditMode
        self.canvas = EditCanvas()
        self.canvas.wants_detail.connect(self._load_full)
        self.canvas.step.connect(lambda d: self.go(self.pos + d))
        from lunelis.settings import Settings as _S
        self.canvas.wheel_mode = _S(conn).get("wheel_action")
        from PySide6.QtWidgets import QSplitter
        self.split = QSplitter(Qt.Orientation.Vertical)
        self.split.setChildrenCollapsible(False)
        self.split.setHandleWidth(6)
        # The photo, or - for a video - the player (made the first time it's needed).
        self.view_stack = QStackedWidget()
        self.view_stack.addWidget(self.canvas)
        self.player = None
        self._movie = None                 # an animated GIF playing on the canvas
        self.offline_paths: set[str] = set()   # source folders not answering (set by the window)
        self.split.addWidget(self.view_stack)
        self.strip = Filmstrip(self.strip_thumbs)
        self.strip.picked.connect(self.go)
        self.split.addWidget(self.strip)
        self.split.setStretchFactor(0, 1)
        self.split.setStretchFactor(1, 0)
        from lunelis.settings import Settings
        h = Settings(conn).get("detail_strip_height")
        self.split.setSizes([800, h])
        self.split.splitterMoved.connect(lambda *_: Settings(self.conn).set("detail_strip_height",
                                                                           max(56, min(260, self.strip.height()))))
        left.addWidget(self.split, 1)
        body.addLayout(left, 1)
        self._full_signals = _FullSignals()
        self._full_signals.loaded.connect(self._full_ready)
        self._full_for: int | None = None
        self._full_pix: tuple[int, QPixmap] | None = None
        self.panel = InfoPanel()
        from lunelis.ui.tag_editor import TagBox
        self.panel.attach_tags(TagBox(conn))
        self.panel.tags_added.connect(self._tags_added)
        self.panel.tag_removed.connect(self._tag_removed)
        self.panel.rate.connect(self.rate.emit)
        self.panel.show_event.connect(self.show_event.emit)
        self.develop = DevelopPanel()
        self.develop.done.connect(lambda: self.set_editing(False))
        if self.workspace:
            for w in (self.back_b, self.edit_b, self.develop.done_b):
                w.hide()
        self.side = QStackedWidget()
        self.side.setFixedWidth(340)
        self.side.addWidget(self.panel)
        self.side.addWidget(self.develop)
        body.addWidget(self.side)
        self.editing = False
        self.edit = EditMode(conn, self.canvas, self.develop, paths.THUMBNAIL_CACHE, paths.EDIT_CACHE, self)
        self.edit.saved.connect(self._edit_saved)
        wrap = QWidget()
        wrap.setLayout(body)
        outer.addWidget(wrap, 1)

    # --- showing ---------------------------------------------------------------------------

    def open(self, index: LibraryIndex, pos: int) -> None:
        self.index = index
        self.go(pos)
        self.setFocus()

    def go(self, pos: int) -> None:
        n = len(self.index)
        if not n:
            return
        pos = max(0, min(n - 1, pos))
        self.edit.finish()                 # saves the photo being left
        self._stop_motion()
        self.pos = pos
        fid = self.index.file_id(pos)
        self.info = photoinfo.load(self.conn, fid)
        if self.info is None:
            return
        self.refresh_info()
        self.canvas.set_photo((self.info.width, self.info.height))
        self._full_for = None
        self._full_pix = None                  # one full-size picture in memory at a time
        self.strip.set_position(self.index, pos)
        self.counter.setText(f"{pos + 1:,} of {n:,}")
        self.prev_b.setEnabled(pos > 0)
        self.next_b.setEnabled(pos < n - 1)
        self._show_image()
        if self.editing:
            self.edit.start(self.info)
        self.edit_b.setEnabled(not self.info.is_video and self._movie is None)
        # Decode the neighbours ahead of time - and drop what's still queued
        # for photos already passed.
        near = [self.index.file_id(p) for p in (pos, pos + 1, pos - 1) if 0 <= p < n]
        self.previews.keep_only(near)
        for nfid in near[1:]:
            nb = photoinfo.load(self.conn, nfid)
            if nb and not nb.is_video:
                self.previews.get(nb, self._edit_of(nb))
        self.current_changed.emit(fid)

    # --- tags ---------------------------------------------------------------------------------

    tags_changed = Signal()

    def _tags_added(self, names: list) -> None:
        from lunelis.tags import model as tags
        if self.info is None:
            return
        tags.add(self.conn, [self.info.file_id], names)
        tags.remember_recent(self.conn, names)
        self.refresh_info()
        self.tags_changed.emit()

    def _tag_removed(self, name: str) -> None:
        from lunelis.tags import model as tags
        if self.info is None:
            return
        tags.remove(self.conn, [self.info.file_id], name)
        self.refresh_info()
        self.tags_changed.emit()

    # --- editing ------------------------------------------------------------------------------

    def _edit_of(self, info: photoinfo.PhotoInfo):
        from lunelis.edit import store
        stack = store.get(self.conn, info.file_id)
        if stack.is_identity():
            return None
        return (info.is_raw, stack, store.filter_params(self.conn, stack.filter))

    def set_editing(self, on: bool) -> None:
        if self.workspace and not on:
            return                         # the Edit page never leaves editing
        if on and (self.info is None or self.info.is_video):
            on = False
        if self.edit_b.isChecked() != on:
            self.edit_b.blockSignals(True)
            self.edit_b.setChecked(on)
            self.edit_b.blockSignals(False)
        if on == self.editing:
            return
        self.editing = on
        self.side.setCurrentWidget(self.develop if on else self.panel)
        if on:
            self.edit.start(self.info)
        else:
            self.edit.finish()
            self._show_image()
        self.setFocus()

    def _edit_saved(self, file_id: int) -> None:
        self.previews.forget(file_id)
        self.big_thumbs.reload(file_id)
        self.strip_thumbs.reload(file_id)
        self.strip.update()
        if self.info and file_id == self.info.file_id and not self.edit.active:
            self._show_image()
        self.edited.emit(file_id)

    def refresh_info(self) -> None:
        """After a rating change: re-read and redraw the panel and header."""
        if self.pos < 0:
            return
        self.info = photoinfo.load(self.conn, self.index.file_id(self.pos))
        if self.info is None:
            return
        i = self.info
        self.name.setText(i.filename)
        self.summary.setText("  ·  ".join(x for x in (i.when(), i.camera(), i.lens or "", i.exposure()) if x))
        self.summary.setToolTip(self.summary.text())
        self.stars.setText("★" * i.stars + "☆" * (5 - i.stars) if i.stars else "")
        self.stars.setStyleSheet(f"color: {themes.current().rating}; font-size: 15px;")
        self.panel.show_info(i)
        from lunelis.tags import model as tags
        self.panel.tag_box.set_tags(tags.tags_of(self.conn, i.file_id))
        from lunelis.catalog.exifblob import unpack_dict
        row = self.conn.execute("SELECT raw_exif FROM exif WHERE file_id = ?", (i.file_id,)).fetchone()
        self.panel.show_metadata(unpack_dict(row[0]) if row and row[0] else {})
        from lunelis.albums.model import albums_of
        names = ", ".join(n for _, n in albums_of(self.conn, i.file_id))
        lab = self.panel.fields["albums"]
        lab.setText(names)
        lab.setVisible(bool(names))
        lab.property("heading").setVisible(bool(names))

    def shut(self) -> None:
        """The window is closing: save the edit, let its renders finish, and ignore
        anything that arrives late (previews, thumbnails) - the catalog is about
        to close."""
        self.edit.finish()
        self.edit.out_pool.waitForDone(15_000)
        self.edit.closed = True
        self._stop_motion()
        self._shut = True

    def _show_image(self) -> None:
        if getattr(self, "_shut", False):
            return
        i = self.info
        if i is None:
            return
        if i.root in self.offline_paths:
            # Its drive or NAS isn't answering: the thumbnail, and why.
            self._stop_motion()
            thumb = self.big_thumbs.get(i.file_id, cache_rel_path(i.file_id))
            self.canvas.show_pixmap(thumb, sharp=False,
                                    message=f"{i.root} isn't answering - this is the thumbnail. The photo is "
                                            "safe there and nothing is marked missing.")
            return
        if i.is_video:
            self._play_video(i)
            return
        if self._movie is None and i.filename.lower().endswith(".gif") and not self.edit.active:
            self._play_gif(i)
        if self._movie is not None:
            return                         # its frames draw themselves
        if self.edit.active and self.edit.has_render:
            return                         # the live edit is on screen
        if self._full_pix is not None and self._full_pix[0] == i.file_id:
            self.canvas.show_pixmap(self._full_pix[1], sharp=True, keep_zoom=True)
            return
        full = self.previews.get(i, self._edit_of(i))
        if full is not None:
            self.canvas.show_pixmap(full, sharp=True)
            return
        thumb = self.big_thumbs.get(i.file_id, cache_rel_path(i.file_id))
        failed = i.file_id in self.previews.failed
        self.canvas.show_pixmap(thumb, sharp=False,
                                message="Couldn't open this file at full size" if failed else "")

    # --- videos and animated GIFs ---

    def _play_video(self, i: photoinfo.PhotoInfo) -> None:
        if self.player is None:
            from lunelis.ui.video_player import VideoPlayer
            self.player = VideoPlayer(self.conn)
            self.player.setFocusPolicy(Qt.FocusPolicy.NoFocus)   # keys stay with the photo view
            self.view_stack.addWidget(self.player)
        self.view_stack.setCurrentWidget(self.player)
        self.player.load(i.path)

    def _play_gif(self, i: photoinfo.PhotoInfo) -> None:
        from PySide6.QtGui import QMovie
        movie = QMovie(i.path)
        if not movie.isValid() or movie.frameCount() == 1:
            return                         # a still GIF shows like any photo
        movie.setCacheMode(QMovie.CacheMode.CacheAll)
        movie.frameChanged.connect(self._gif_frame)
        self._movie = movie
        movie.start()

    @unless_closed
    def _gif_frame(self, _n: int) -> None:
        if self._movie is not None and not getattr(self, "_shut", False):
            self.canvas.show_pixmap(self._movie.currentPixmap(), sharp=True, keep_zoom=True)

    def _stop_motion(self) -> None:
        if self._movie is not None:
            self._movie.stop()
            self._movie.frameChanged.disconnect(self._gif_frame)
            self._movie.deleteLater()
            self._movie = None
        if self.player is not None and self.player.path:
            self.player.stop()
        self.view_stack.setCurrentWidget(self.canvas)

    def hideEvent(self, e) -> None:
        # Back to the library: nothing keeps playing behind it.
        if self.player is not None:
            self.player.player.pause()
        if self._movie is not None:
            self._movie.setPaused(True)
        super().hideEvent(e)

    def showEvent(self, e) -> None:
        if self._movie is not None:
            self._movie.setPaused(False)
        super().showEvent(e)

    # --- full resolution (zooming past the preview) ---

    def _load_full(self) -> None:
        i = self.info
        if i is None or i.is_video or self.edit.active or self._edit_of(i) is not None:
            return                             # edited photos zoom their rendering
        if self._full_for == i.file_id:
            return
        self._full_for = i.file_id
        self.canvas.message = "Loading full resolution…"
        self.canvas.update()
        QThreadPool.globalInstance().start(_FullLoad(self._full_signals, i.file_id, i.path, i.is_raw))

    @unless_closed
    def _full_ready(self, file_id: int, img: QImage) -> None:
        if getattr(self, "_shut", False):
            return
        if self.info is None or file_id != self.info.file_id or self.edit.active:
            return
        self.canvas.message = ""
        if img.isNull():
            self.canvas.update()
            return
        self._full_pix = (file_id, QPixmap.fromImage(img))
        self.canvas.show_pixmap(self._full_pix[1], sharp=True, keep_zoom=True)

    @unless_closed
    def _preview_ready(self, file_id: int) -> None:
        if getattr(self, "_shut", False):
            return
        if self.info and file_id == self.info.file_id:
            self._show_image()

    @unless_closed
    def _thumb_ready(self, file_id: int) -> None:
        if getattr(self, "_shut", False):
            return
        if self.info and file_id == self.info.file_id and not self.canvas.sharp:
            self._show_image()

    # --- keys ------------------------------------------------------------------------------

    def keyPressEvent(self, e) -> None:
        k, mods = e.key(), e.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        if k == Qt.Key.Key_E and not ctrl:
            self.set_editing(not self.editing)
            return
        if self.edit.active:
            if k == Qt.Key.Key_Backslash:
                self.edit.set_before(not self.edit.showing_before)
                return
            if ctrl and k == Qt.Key.Key_Z:
                self.edit.redo() if mods & Qt.KeyboardModifier.ShiftModifier else self.edit.undo()
                return
            if ctrl and k == Qt.Key.Key_Y:
                self.edit.redo()
                return
            if k == Qt.Key.Key_R and not ctrl:
                self.edit.set_crop_mode(not self.edit.cropping)
                return
            if self.edit.cropping and k in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Escape):
                self.edit.set_crop_mode(False)
                return
            if k == Qt.Key.Key_O and not ctrl and self.edit.mask_index >= 0:
                self.edit.set_overlay(not self.canvas.mask.show_overlay)
                return
            if k == Qt.Key.Key_Escape and self.edit.mask_index >= 0:
                self.edit.select_mask(-1)
                return
            if k == Qt.Key.Key_Escape:
                self.set_editing(False)
                return
        if self.info is not None and self.info.is_video and self.player is not None and not ctrl:
            act = {Qt.Key.Key_K: self.player.toggle, Qt.Key.Key_J: lambda: self.player.jump(-5000),
                   Qt.Key.Key_L: lambda: self.player.jump(5000), Qt.Key.Key_I: self.player.set_in,
                   Qt.Key.Key_O: self.player.set_out}.get(k)
            if act is not None:
                act()
                return
        if k in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            self.go(self.pos - 1)
        elif k in (Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_Space):
            self.go(self.pos + 1)
        elif k == Qt.Key.Key_Home:
            self.go(0)
        elif k == Qt.Key.Key_End:
            self.go(len(self.index) - 1)
        elif k in (Qt.Key.Key_Escape, Qt.Key.Key_Backspace):
            self.back.emit()
        elif k == Qt.Key.Key_Z:
            self.canvas.toggle_zoom()
        else:
            super().keyPressEvent(e)
