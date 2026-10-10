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

from PySide6.QtCore import QObject, QPoint, QPointF, QRect, QRectF, QRunnable, QSize, Qt, QThreadPool, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap
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

SCREEN = "off"               # Settings monitor_profile: the photo view's colours go through it (edit/icc.py)
PREVIEW_EDGE = 2560          # plenty for a 1440p/4K window without decoding 60 MP
PREVIEW_CACHE = 8


# --- background decode --------------------------------------------------------------

class _PreviewSignals(QObject):
    loaded = Signal(int, QImage)       # file id, image (null = couldn't)
    why = Signal(int, str)             # file id, why it couldn't (before a null `loaded`)


def _damage_reason(path: str, error: Exception) -> str:
    """Why a photo can't be opened, in plain words."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(4096)
        if head and not head.strip(b"\0"):
            return "the file is damaged - it is all zeros (see Damaged files for an intact copy)"
        if not head:
            return "the file is empty (0 bytes) - see Damaged files"
    except FileNotFoundError:
        return "the file isn't there any more"
    except OSError:
        return "its folder can't be reached right now"
    from lunelis.ui.photoinfo import friendly
    return friendly(f"{type(error).__name__}: {error}")


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
            if SCREEN != "off":
                from lunelis.edit import icc
                img = icc.to_screen(img, SCREEN)
            data = img.tobytes()
            q = QImage(data, img.width, img.height, 3 * img.width, QImage.Format.Format_RGB888).copy()
        except Exception as e:
            self.signals.why.emit(self.file_id, _damage_reason(self.path, e))
            q = QImage()
        self.signals.loaded.emit(self.file_id, q)


class _ThumbNow(QRunnable):
    """A photo opened before the library pass made its thumbnail: make it now
    (its own connection), so the grid and the filmstrip show it at once."""

    def __init__(self, signals, file_id: int) -> None:
        super().__init__()
        self.signals, self.file_id = signals, file_id

    def run(self) -> None:
        try:
            from lunelis.catalog.schema import open_catalog
            from lunelis.raw.thumbnails import generate_pending
            conn = open_catalog(paths.DEFAULT_CATALOG_PATH)
            try:
                made = generate_pending(conn, paths.THUMBNAIL_CACHE, only=[self.file_id]).made
            finally:
                conn.close()
        except Exception:
            made = 0
        if made:
            self.signals.made.emit(self.file_id)


class _ThumbNowSignals(QObject):
    made = Signal(int)


class _FullSignals(QObject):
    loaded = Signal(int, QImage)


class _AFSignals(QObject):
    found = Signal(int, object)          # file id, (x, y) fractions or None


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
        self.reasons: dict[int, str] = {}
        self._signals = _PreviewSignals()
        self._signals.loaded.connect(self._loaded)
        self._signals.why.connect(self.reasons.__setitem__)
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
    face_menu = Signal(int, QPoint)    # a face box was clicked (face id, global position)
    face_drawn = Signal(list)          # Ctrl+drag drew a new face box ([x, y, w, h] fractions)
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
        # Face overlay (F): [(face id, [x, y, w, h], label, state)] - state named | suggested | unknown.
        self.faces: list[tuple[int, list[float], str, str]] = []
        self.show_faces = False
        self.turn_id: int | None = None   # the file shown, for its turn (turns.py); None in the editor
        # View tools (0.52, view_tools.py): drawn over the picture, never into it.
        self.view_tool: str | None = None     # peaking | clipping | false_colour | zones
        self.show_histogram = False
        self.guide: str | None = None         # thirds | golden | centre | level
        self.af: tuple | None = None          # the camera's focus area while peaking: (x, y[, w, h]) fractions
        self._tool_cache: tuple | None = None # (pix key, tool) -> (overlay QImage, histogram)
        self._draw: tuple[QPointF, QPointF] | None = None
        self.draw_mode = None             # set: the next drag draws a face box without Ctrl (0.53)
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
        if pix is not None and self.turn_id is not None:
            from lunelis import turns
            pix = turns.apply(pix, self.turn_id)      # Rotate left / right, without an edit
        if not keep_zoom and (not sharp or pix is None):
            self.scale = None
        self.pix, self.sharp, self.message = pix, sharp, message
        self.update()

    def _turned(self) -> bool:
        """Face boxes are in the file's own pixels; on a turned photo they'd be
        in the wrong place, so they aren't drawn."""
        from lunelis import turns
        return self.turn_id is not None and turns.get(self.turn_id) != 0

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
        if self.view_tool or self.show_histogram or self.guide:
            self._paint_tools(p)
        if self.scale is not None:
            dpr = max(self.devicePixelRatioF(), 1.0)
            text = f"{self.scale * dpr * 100:.0f} %"
            p.setPen(qcolor(t.text_faint))
            p.drawText(self.rect().adjusted(12, 0, 0, -8), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, text)
        if not self.sharp or self.message:
            p.setPen(qcolor(t.text_faint))
            p.drawText(self.rect().adjusted(0, 0, -12, -8), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
                       self.message or "Loading full size…")
        if self.show_faces and not self._turned():
            self._paint_faces(p)

    def _tool_images(self):
        """(overlay QImage or None, histogram or None) for the picture shown -
        worked out once per picture and tool."""
        key = (self.pix.cacheKey(), self.view_tool, self.show_histogram)
        if self._tool_cache and self._tool_cache[0] == key:
            return self._tool_cache[1]
        import numpy as np
        from PySide6.QtGui import QImage
        from lunelis import view_tools as vt
        img = self.pix.toImage().convertToFormat(QImage.Format.Format_RGB888)
        bpl = img.bytesPerLine()
        a = np.frombuffer(img.constBits(), np.uint8, bpl * img.height()).reshape(img.height(), bpl)
        rgb = vt.shrink(np.ascontiguousarray(a[:, :img.width() * 3].reshape(img.height(), img.width(), 3)))
        over = None
        if self.view_tool in vt.OVERLAYS:
            o = np.ascontiguousarray(vt.OVERLAYS[self.view_tool](rgb))
            over = QImage(o.data, o.shape[1], o.shape[0], o.shape[1] * 4, QImage.Format.Format_RGBA8888).copy()
        hist = vt.histogram(rgb) if self.show_histogram else None
        self._tool_cache = (key, (over, hist))
        return over, hist

    def _paint_tools(self, p: QPainter) -> None:
        r = self._fit_rect()
        over, hist = self._tool_images()
        if over is not None:
            p.drawImage(r, over, QRectF(over.rect()))
        if self.view_tool == "peaking" and self.af is not None:
            c = QPointF(r.x() + self.af[0] * r.width(), r.y() + self.af[1] * r.height())
            # The camera's own focus frame at its real size when the file has it
            # (0.53: a Sony a7R V); else a marker where it focused.
            fw = fh = None
            if len(self.af) >= 4 and self.af[2] and self.af[3]:
                fw, fh = self.af[2] * r.width(), self.af[3] * r.height()
            box = (QRectF(c.x() - fw / 2, c.y() - fh / 2, fw, fh) if fw
                   else QRectF(c.x() - 18, c.y() - 18, 36, 36))
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setBrush(Qt.BrushStyle.NoBrush)
            for colour, w in ((QColor(0, 0, 0, 160), 4), (QColor(255, 220, 0), 2)):
                pen = QPen(colour, w)
                if not fw:
                    pen.setStyle(Qt.PenStyle.DashLine)        # a point only: the size isn't the camera's
                p.setPen(pen)
                p.drawRect(box)
        if self.guide:
            self._paint_guide(p, r)
        if hist is not None:
            self._paint_histogram(p, hist)

    def _paint_guide(self, p: QPainter, r: QRectF) -> None:
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        lines = []
        if self.guide in ("thirds", "golden"):
            k = 1 / 3 if self.guide == "thirds" else 0.382
            for f in (k, 1 - k):
                lines.append(((r.left() + f * r.width(), r.top()), (r.left() + f * r.width(), r.bottom())))
                lines.append(((r.left(), r.top() + f * r.height()), (r.right(), r.top() + f * r.height())))
        elif self.guide == "centre":
            cx, cy = r.center().x(), r.center().y()
            lines += [((cx - 24, cy), (cx + 24, cy)), ((cx, cy - 24), (cx, cy + 24))]
            lines += [((r.left(), r.top()), (r.right(), r.bottom())), ((r.right(), r.top()), (r.left(), r.bottom()))]
        elif self.guide == "level":
            for f in (0.25, 0.5, 0.75):
                lines.append(((r.left(), r.top() + f * r.height()), (r.right(), r.top() + f * r.height())))
            for f in (0.25, 0.5, 0.75):
                lines.append(((r.left() + f * r.width(), r.top()), (r.left() + f * r.width(), r.bottom())))
        for colour, w in ((QColor(0, 0, 0, 110), 3), (QColor(255, 255, 255, 190), 1)):
            p.setPen(QPen(colour, w))
            for a, b in lines:
                p.drawLine(QPointF(*a), QPointF(*b))

    def _paint_histogram(self, p: QPainter, hist) -> None:
        import numpy as np
        from PySide6.QtGui import QPainterPath
        w, h = 256, 110
        box = QRectF(self.width() - w - 16, self.height() - h - 34, w, h)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 170))
        p.drawRoundedRect(box.adjusted(-6, -6, 6, 6), 6, 6)
        top = float(np.percentile(hist[:3, 2:-2], 99.5)) or 1.0      # one spike mustn't flatten the rest
        for row, colour in ((3, QColor(220, 220, 220, 90)), (0, QColor(255, 70, 70, 120)),
                            (1, QColor(70, 230, 90, 120)), (2, QColor(80, 130, 255, 120))):
            path = QPainterPath(QPointF(box.left(), box.bottom()))
            for i, v in enumerate(hist[row]):
                path.lineTo(box.left() + i * w / 255, box.bottom() - min(1.0, v / top) * h)
            path.lineTo(box.right(), box.bottom())
            p.setBrush(colour)
            p.drawPath(path)

    def face_rect(self, box: list[float]) -> QRectF:
        r = self._fit_rect()
        return QRectF(r.x() + box[0] * r.width(), r.y() + box[1] * r.height(),
                      box[2] * r.width(), box[3] * r.height())

    def _paint_faces(self, p: QPainter) -> None:
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        accent = qcolor(themes.current().accent)
        colours = {"named": accent, "suggested": QColor(240, 190, 60), "unknown": QColor(255, 255, 255),
                   "stranger": QColor(160, 160, 170), "animal": QColor(120, 200, 140),
                   "pet": QColor(120, 200, 140)}
        f = p.font()
        f.setPointSizeF(max(8.0, f.pointSizeF()))
        p.setFont(f)
        fm = p.fontMetrics()
        for _fid, box, label, state in self.faces:
            r = self.face_rect(box)
            c = colours.get(state, colours["unknown"])
            pen = QPen(c, 2)
            # A name draws a solid box with its label; no name changes the box:
            # dashed = suggested, dotted = unnamed pet / stranger, dash-dot = unnamed face.
            if state in ("suggested", "stranger", "animal", "unknown"):
                pen.setStyle({"suggested": Qt.PenStyle.DashLine, "unknown": Qt.PenStyle.DashDotLine}
                             .get(state, Qt.PenStyle.DotLine))
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, 4, 4)
            if label:
                w = fm.horizontalAdvance(label) + 10
                tag = QRectF(r.center().x() - w / 2, r.bottom() + 3, w, fm.height() + 4)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(0, 0, 0, 170))
                p.drawRoundedRect(tag, 4, 4)
                p.setPen(c)
                p.drawText(tag, Qt.AlignmentFlag.AlignCenter, label)
        if self._draw is not None:
            p.setPen(QPen(QColor(255, 255, 255), 1.5, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(self._draw[0], self._draw[1]).normalized())

    def face_at(self, pos: QPointF) -> int | None:
        for fid, box, _label, _state in reversed(self.faces):
            if self.face_rect(box).adjusted(-4, -4, 4, 4).contains(pos):
                return fid
        return None

    def _face_press(self, e) -> bool:
        """With the overlay on: a click on a box opens its menu; Ctrl+drag draws a new box."""
        if not self.show_faces or self._turned() or self.pix is None or e.button() != Qt.MouseButton.LeftButton:
            return False
        if self.draw_mode is not None or e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._draw = (e.position(), e.position())
            return True
        fid = self.face_at(e.position())
        if fid is not None:
            self.face_menu.emit(fid, e.globalPosition().toPoint())
            return True
        return False

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
        if self._face_press(e):
            return
        if self.scale is not None and e.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            self._pan = e.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, e) -> None:
        if self._draw is not None:
            self._draw = (self._draw[0], e.position())
            self.update()
            return
        if self._pan is not None and self.pix is not None and self.scale is not None:
            d = e.position() - self._pan
            self._pan = e.position()
            r = self._fit_rect()
            self.center = QPointF(self.center.x() - d.x() / r.width(), self.center.y() - d.y() / r.height())
            self._clamp()
            self.update()

    def mouseReleaseEvent(self, e) -> None:
        if self._draw is not None:
            a, b = self._draw
            self._draw = None
            self.update()
            r, box = self._fit_rect(), QRectF(a, b).normalized()
            if box.width() > 8 and box.height() > 8 and r.width() and r.height():
                x0 = max(0.0, (box.left() - r.x()) / r.width())
                y0 = max(0.0, (box.top() - r.y()) / r.height())
                x1 = min(1.0, (box.right() - r.x()) / r.width())
                y1 = min(1.0, (box.bottom() - r.y()) / r.height())
                if x1 > x0 and y1 > y0:
                    self.face_drawn.emit([x0, y0, x1 - x0, y1 - y0])
            return
        if self._pan is not None:
            self._pan = None
            self.setCursor(Qt.CursorShape.OpenHandCursor if self.scale is not None else Qt.CursorShape.ArrowCursor)


# --- the filmstrip ------------------------------------------------------------------------------

class Filmstrip(QWidget):
    """The library's photos around the current one; click to jump.

    0.53: each thumbnail fills its tile cropped (it was squeezed into the
    square - squashed, hard to match with the photo above); the wheel scrolls
    the strip without changing the photo (it used to step the photo, loading
    every one on the way); the strip comes back to the current photo when that
    changes; the current one has an accent frame."""

    picked = Signal(int)               # position in the index
    GAP = 8

    def __init__(self, thumbs: ThumbCache, parent=None) -> None:
        super().__init__(parent)
        self.thumbs = thumbs
        self.thumbs.ready.connect(lambda _: self.update())
        self.index = LibraryIndex()
        self.pos = 0
        self.offset = 0                       # tiles the wheel moved the view from the current photo
        self.setMouseTracking(True)
        self._hover = -1
        self.setMinimumHeight(56)             # drag the divider above it: the thumbnails follow
        self.setMaximumHeight(260)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    @property
    def THUMB(self) -> int:
        return max(32, self.height() - 24)

    def set_position(self, index: LibraryIndex, pos: int) -> None:
        self.index, self.pos = index, pos
        self.offset = 0                       # a new photo: back to it
        self.update()

    def _slots(self) -> tuple[int, int]:
        per = max(1, (self.width() - 16) // (self.THUMB + self.GAP))
        first = max(0, min(self.pos + self.offset - per // 2, len(self.index) - per))
        return first, per

    def _x0(self, per: int) -> int:
        total_w = min(per, len(self.index)) * (self.THUMB + self.GAP) - self.GAP
        return max(16, (self.width() - total_w) // 2)

    def slot_at(self, x: int) -> int:
        """The index position of the tile at x, or -1."""
        first, per = self._slots()
        x0 = self._x0(per)
        k, rest = divmod(x - x0, self.THUMB + self.GAP)
        if x < x0 or rest >= self.THUMB or not (0 <= k < per) or first + k >= len(self.index):
            return -1
        return first + k

    def paintEvent(self, e) -> None:
        t = themes.current()
        p = QPainter(self)
        p.fillRect(self.rect(), qcolor(t.viewer_strip))
        n = len(self.index)
        if not n:
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        first, per = self._slots()
        x0 = self._x0(per)
        y = (self.height() - self.THUMB) // 2
        for k, i in enumerate(range(first, min(n, first + per))):
            r = QRect(x0 + k * (self.THUMB + self.GAP), y, self.THUMB, self.THUMB)
            path = QPainterPath()
            path.addRoundedRect(QRectF(r), 6, 6)
            row = self.index.rows[i]
            pix = None if row[2] else self.thumbs.get(row[0], row[1] or cache_rel_path(row[0]))
            if pix is not None and not pix.isNull():
                # Cropped to fill the tile, never squeezed.
                s = min(pix.width(), pix.height())
                src = QRectF((pix.width() - s) / 2, (pix.height() - s) / 2, s, s)
                p.save()
                p.setClipPath(path)
                p.drawPixmap(QRectF(r), pix, src)
                if i != self.pos:
                    p.fillRect(r, QColor(0, 0, 0, 40 if i == self._hover else 90))   # the current one stands out
                p.restore()
            else:
                p.fillPath(path, qcolor(t.tile_unavailable))
            self._marks(p, r, self.index.tile(i))
            if i == self.pos:
                p.setPen(QPen(qcolor(t.accent), 3))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(QRectF(r).adjusted(1.5, 1.5, -1.5, -1.5), 6, 6)
        if self.offset:
            p.setPen(qcolor(t.text_faint))
            p.drawText(self.rect().adjusted(0, 0, -10, -4), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
                       "click a photo to open it")

    def _marks(self, p: QPainter, r: QRect, tile) -> None:
        """What kind of media a tile is (0.53), as the grid shows it, scaled
        down: a play mark and the length for a video, the format when it isn't
        a plain JPEG (RAW, ARW+JPG, GIF, HEIC...), a stack's frame count, and
        a dot for an edited photo. Small tiles keep only the play mark and the
        stack count."""
        f = QFont(self.font())
        f.setPixelSize(max(8, min(11, self.THUMB // 6)))
        f.setBold(True)
        p.setFont(f)
        fm = p.fontMetrics()
        h = fm.height() + 2
        roomy = self.THUMB >= 56

        def pill(x: int, y: int, text: str, right: bool = False) -> None:
            w = fm.horizontalAdvance(text) + 8
            box = QRect(x - w if right else x, y, w, h)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 170))
            p.drawRoundedRect(QRectF(box), 4, 4)
            p.setPen(QColor(255, 255, 255))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

        if tile.is_video:
            secs = int(round(tile.duration or 0))
            text = "▶" + (f" {secs // 60}:{secs % 60:02d}" if roomy and secs else "")
            pill(r.left() + 3, r.bottom() - 3 - h, text)
        if tile.stack_size:
            pill(r.left() + 3, r.top() + 3, f"❐{tile.stack_size}")
        if roomy and tile.badge and tile.badge not in ("JPG", "JPEG"):
            pill(r.right() - 3, r.top() + 3, tile.badge, right=True)
        if tile.edited and not tile.is_video:
            p.setPen(QPen(QColor(0, 0, 0, 170), 1))
            p.setBrush(qcolor(themes.current().accent))
            p.drawEllipse(QPointF(r.right() - 7, r.bottom() - 7), 3.5, 3.5)

    def mousePressEvent(self, e) -> None:
        i = self.slot_at(int(e.position().x()))
        if i >= 0:
            self.picked.emit(i)

    def mouseMoveEvent(self, e) -> None:
        i = self.slot_at(int(e.position().x()))
        if i != self._hover:
            self._hover = i
            self.update()

    def leaveEvent(self, e) -> None:
        self._hover = -1
        self.update()

    def wheelEvent(self, e) -> None:
        """Scroll along the strip (a few tiles a notch); the photo stays."""
        dy = e.angleDelta().y() or e.angleDelta().x()
        n = len(self.index)
        if not dy or not n:
            return
        _first, per = self._slots()
        step = max(1, per // 3) * (-1 if dy > 0 else 1)
        self.offset = max(-self.pos, min(n - 1 - self.pos, self.offset + step))
        self._hover = self.slot_at(int(e.position().x()))
        self.update()


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
    show_on_map = Signal(float, float)
    tags_added = Signal(list)
    tag_removed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsScroll")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumWidth(300)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page = QWidget(objectName="DetailPanel")
        self.v = QVBoxLayout(page)
        self.v.setContentsMargins(20, 16, 20, 20)
        self.v.setSpacing(4)
        self.setWidget(page)
        self.info: photoinfo.PhotoInfo | None = None

        self.title = QLabel(objectName="SectionTitle", textFormat=Qt.TextFormat.PlainText)
        self.title.setWordWrap(True)
        self.v.addWidget(self.title)
        self.kind = QLabel(objectName="Help", textFormat=Qt.TextFormat.PlainText)
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
                             ("sidecar", "Sidecar"), ("problems", "Problems")):
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
        # A frame of a timelapse (timelapses.py): build it from here. Never automatic.
        self.timelapse_b = QPushButton("Build timelapse…", objectName="Primary")
        self.timelapse_b.hide()
        self.v.addWidget(self.timelapse_b)
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
            from html import unescape
            self.show_event.emit(int(eid), unescape(name))
        elif href.startswith("https://www.openstreetmap.org/") and self.info and self.info.lat is not None:
            self.show_on_map.emit(self.info.lat, self.info.lon)   # the main window picks Map or browser

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

        from html import escape

        def put(key: str, text: str, rich: bool = False) -> None:
            # Text from the file itself (camera, lens, names...) is shown as text,
            # never as markup: a crafted photo can't put a link or picture here.
            lab = self.fields[key]
            lab.setText(text if rich else escape(text))
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
        put("event", f'<a href="event:{info.event_id}:{escape(info.event)}">{escape(info.event)}</a>'
            if info.event else "", rich=True)
        url = info.map_url()
        place = ""
        if url:
            from lunelis.geo import places
            place = escape(places.lookup(info.lat, info.lon).label) + (" (pinned on the map)" if info.pinned else "")                 + "<br>"
        put("location", f'{place}{info.lat:.5f}, {info.lon:.5f}  ·  <a href="{escape(url)}">Open map</a>' if url else "",
            rich=True)
        from lunelis.damage.check import PROBLEM_TEXT
        put("status", PROBLEM_TEXT.get(info.damaged, info.damaged) if info.damaged else "")
        put("backup", info.protection)
        put("path", photoinfo.breakable(info.path))
        put("sidecar", info.sidecar or "")
        put("problems", "<br>".join(escape(p) for p in info.problems), rich=True)


# --- the page ------------------------------------------------------------------------------------

TOOL_BUTTON = 36        # the photo view's icon buttons, square
TOOL_ICON = 20


class DetailView(QWidget):
    back = Signal()
    rate = Signal(dict)
    current_changed = Signal(int)      # file id now shown (the app's rating target)
    show_event = Signal(int, str)
    show_on_map = Signal(float, float)
    rotate_requested = Signal(int)     # -1 left, 1 right (turns.py)
    build_timelapse = Signal(list)     # Info > Build timelapse...: its frames
    edited = Signal(int)               # a photo's edit was saved and its thumbnail re-rendered
    faces_changed = Signal()           # a face was named or corrected here (People tags changed)
    show_person = Signal(int)          # "All photos of Ann" from a face's menu
    thumb_made = Signal(int)           # a thumbnail was made on opening the photo

    def retheme(self) -> None:
        """The toolbar icons in the theme's text colour (again after a theme switch)."""
        from lunelis.ui import icons, theme
        t = theme.current()
        for b, name in ((self.rotate_l, "rotate_left"), (self.rotate_r, "rotate_right"),
                        (self.prev_b, "chevron_left"), (self.next_b, "chevron_right")):
            b.setIcon(icons.icon(name, t.text, t.text, TOOL_ICON))
        for name, b in getattr(self, "tool_b", {}).items():
            b.setIcon(icons.icon(name, t.text_muted, t.accent_text, TOOL_ICON))
        if hasattr(self, "faces_menu_b"):
            self.faces_menu_b.setIcon(icons.icon("chevron_down", t.text, size=14))

    # --- view tools (0.52) ---------------------------------------------------------------------

    TOOLS = (("peaking", "Focus peaking - the sharpest edges glow, and the camera's AF point when the file "
                         "records it"),
             ("clipping", "Exposure warnings - blown highlights red, crushed shadows blue"),
             ("false_colour", "False colour - the picture painted by brightness: blue shadows, green middle grey, "
                              "pink skin (a stop over), yellow to red at the top"),
             ("zones", "Zones - Ansel Adams' eleven zones, black (0) to white (X)"),
             ("histogram", "Histogram - red, green, blue and brightness"),
             ("guides", "Guides - click again for the next: thirds, golden ratio, centre, level"))
    GUIDES = ("thirds", "golden", "centre", "level")

    def _tool_rail(self) -> QWidget:
        from PySide6.QtWidgets import QFrame, QScrollArea, QToolButton
        # In a scroll area: on the shortest window (350 px) the six buttons
        # mustn't make the photo view taller than the window.
        rail = QScrollArea(objectName="ToolRail", widgetResizable=True, frameShape=QFrame.Shape.NoFrame)
        rail.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rail.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rail.setFixedWidth(TOOL_BUTTON + 12)
        inner = QWidget(objectName="ToolRailInner")
        rail.setWidget(inner)
        v = QVBoxLayout(inner)
        v.setContentsMargins(6, 10, 6, 10)
        v.setSpacing(6)
        self.tool_b: dict[str, QToolButton] = {}
        for name, tip in self.TOOLS:
            b = QToolButton(objectName="RailButton", checkable=True)
            b.setFixedSize(TOOL_BUTTON, TOOL_BUTTON)
            b.setIconSize(QSize(TOOL_ICON, TOOL_ICON))
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, n=name: self.toggle_tool(n))
            self.tool_b[name] = b
            v.addWidget(b)
            if name == "zones":
                v.addSpacing(8)
        v.addStretch(1)
        from lunelis.ui import icons, theme
        t = theme.current()
        for name, b in self.tool_b.items():
            b.setIcon(icons.icon(name, t.text_muted, t.accent_text, TOOL_ICON))
        return rail

    def toggle_tool(self, name: str) -> None:
        c = self.canvas
        if name == "histogram":
            c.show_histogram = not c.show_histogram
        elif name == "guides":
            i = self.GUIDES.index(c.guide) + 1 if c.guide in self.GUIDES else 0
            c.guide = self.GUIDES[i] if i < len(self.GUIDES) else None
            self.tool_b["guides"].setToolTip(f"Guides: {c.guide or 'off'} - click again for the next")
        else:
            c.view_tool = None if c.view_tool == name else name
            if c.view_tool == "peaking":
                self._load_af()
        for n, b in self.tool_b.items():
            b.setChecked(c.view_tool == n if n in ("peaking", "clipping", "false_colour", "zones")
                         else c.show_histogram if n == "histogram" else c.guide is not None)
        c.update()

    def _load_af(self) -> None:
        """The camera's AF point, read from the file on a worker (it isn't in the catalog)."""
        c = self.canvas
        c.af = None
        i = self.info
        self._af_for = i.file_id if i is not None else None
        if i is None or i.is_video:
            return
        fid, path, orient = i.file_id, i.path, getattr(i, "orientation", None)

        class Job(QRunnable):
            def run(_):
                from lunelis import view_tools
                pt = view_tools.af_area(path, orient)
                self._af_signals.found.emit(fid, pt)
        QThreadPool.globalInstance().start(Job())

    def _af_found(self, fid: int, pt) -> None:
        if self.info is not None and self.info.file_id == fid and self.canvas.view_tool == "peaking":
            self.canvas.af = pt
            self.canvas.update()

    def __init__(self, conn, parent=None, workspace: bool = False) -> None:
        super().__init__(parent)
        self.conn = conn
        global SCREEN
        from lunelis.settings import Settings as _Settings
        SCREEN = _Settings(conn).get("monitor_profile")
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
        self.name = QLabel(textFormat=Qt.TextFormat.PlainText)
        self.name.setStyleSheet("font-weight: 600;")
        h.addWidget(self.name)
        self.summary = QLabel(objectName="Count", textFormat=Qt.TextFormat.PlainText)
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
        self.faces_b = QPushButton("Faces", checkable=True)
        self.faces_b.setToolTip("Show the faces in this photo and who they are (F). Click a face to name or "
                                "correct it; Ctrl+drag draws one Lunelis missed.")
        self.faces_b.toggled.connect(self.set_faces_overlay)
        h.addWidget(self.faces_b)
        from PySide6.QtWidgets import QMenu, QToolButton
        self.faces_menu_b = QToolButton(popupMode=QToolButton.ToolButtonPopupMode.InstantPopup)
        self.faces_menu_b.setObjectName("MenuArrow")
        self.faces_menu_b.setFixedSize(28, TOOL_BUTTON)
        self.faces_menu_b.setIconSize(QSize(14, 14))
        self.faces_menu_b.setToolTip("Add, change or remove the faces, pets and animals in this photo")
        self.faces_menu = QMenu(self.faces_menu_b)
        self.faces_menu.aboutToShow.connect(self._fill_faces_menu)
        self.faces_menu_b.setMenu(self.faces_menu)
        h.addWidget(self.faces_menu_b)
        self.rotate_l = QPushButton(clicked=lambda: self.rotate_requested.emit(-1))
        self.rotate_l.setToolTip("Rotate left (Ctrl+[) - how it's shown; the file isn't changed")
        self.rotate_r = QPushButton(clicked=lambda: self.rotate_requested.emit(1))
        self.rotate_r.setToolTip("Rotate right (Ctrl+]) - how it's shown; the file isn't changed")
        self.prev_b = QPushButton(clicked=lambda: self.go(self.pos - 1))
        self.prev_b.setToolTip("Previous (Left)")
        self.counter = QLabel(objectName="ToolLabel")
        self.next_b = QPushButton(clicked=lambda: self.go(self.pos + 1))
        self.next_b.setToolTip("Next (Right)")
        # Drawn icons on buttons as tall as Edit / Faces (0.52: the text arrows
        # were a few pixels tall and hard to hit).
        for b in (self.rotate_l, self.rotate_r, self.prev_b, self.next_b):
            b.setFixedSize(TOOL_BUTTON, TOOL_BUTTON)
            b.setIconSize(QSize(TOOL_ICON, TOOL_ICON))
        self.retheme()
        for w in (self.rotate_l, self.rotate_r, self.prev_b, self.counter, self.next_b):
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
        self.canvas.face_menu.connect(self._face_menu)
        self.canvas.face_drawn.connect(self._face_drawn)
        from lunelis.settings import Settings as _S
        self.canvas.wheel_mode = _S(conn).get("wheel_action")
        self.faces_b.blockSignals(True)
        self.faces_b.setChecked(bool(_S(conn).get("faces_overlay")))    # remembered between sessions
        self.faces_b.blockSignals(False)
        self.canvas.show_faces = self.faces_b.isChecked()
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
        row = QHBoxLayout()
        row.setSpacing(0)
        row.addWidget(self.split, 1)
        row.addWidget(self._tool_rail())
        left.addLayout(row, 1)
        left.setContentsMargins(0, 0, 0, 0)
        left_w = QWidget()
        left_w.setLayout(left)
        # The photo | the side panel (Info or Edit): drag the divider to widen the panel; remembered.
        self.body_split = QSplitter(Qt.Orientation.Horizontal)
        self.body_split.setChildrenCollapsible(False)
        self.body_split.setHandleWidth(6)
        self.body_split.addWidget(left_w)
        body.addWidget(self.body_split, 1)
        self._full_signals = _FullSignals()
        self._full_signals.loaded.connect(self._full_ready)
        self._af_signals = _AFSignals()
        self._af_signals.found.connect(self._af_found)
        self._full_for: int | None = None
        self._full_pix: tuple[int, QPixmap] | None = None
        self.panel = InfoPanel()
        from lunelis.ui.tag_editor import TagBox
        self.panel.attach_tags(TagBox(conn))
        self.panel.tags_added.connect(self._tags_added)
        self.panel.tag_removed.connect(self._tag_removed)
        self.panel.rate.connect(self.rate.emit)
        self.panel.show_event.connect(self.show_event.emit)
        self.panel.show_on_map.connect(self.show_on_map.emit)
        self.develop = DevelopPanel()
        self.develop.done.connect(lambda: self.set_editing(False))
        if self.workspace:
            for w in (self.back_b, self.edit_b, self.develop.done_b):
                w.hide()
        self.side = QStackedWidget()
        self.side.setMinimumWidth(300)
        self.side.setMaximumWidth(760)
        self.side.addWidget(self.panel)
        self.side.addWidget(self.develop)
        self.body_split.addWidget(self.side)
        self.body_split.setStretchFactor(0, 1)
        self.body_split.setStretchFactor(1, 0)
        side_w = int(Settings(conn).get("side_panel_width"))
        self.body_split.setSizes([1200, side_w])
        self.body_split.splitterMoved.connect(
            lambda *_: Settings(self.conn).set("side_panel_width", max(300, min(760, self.side.width()))))
        self.editing = False
        self.edit = EditMode(conn, self.canvas, self.develop, paths.THUMBNAIL_CACHE, paths.EDIT_CACHE, self)
        self.edit.saved.connect(self._edit_saved)
        wrap = QWidget()
        wrap.setLayout(body)
        outer.addWidget(wrap, 1)

    # --- showing ---------------------------------------------------------------------------

    def open(self, index: LibraryIndex, pos: int) -> None:
        self.index = index.snapshot()          # its own copy: library reloads can't move the photo under it
        self.go(pos)
        self.setFocus()

    def follow(self, index: LibraryIndex) -> None:
        """The library was reloaded (new photos, a re-sort): take the new list,
        staying on the photo being shown. A photo that left the list (filtered
        out, set aside) keeps the old list until you move on."""
        if self.info is None:
            return
        pos = index.position(self.info.file_id)
        if pos < 0:
            return
        self.index = index.snapshot()
        self.pos = pos
        n = len(self.index)
        self.strip.set_position(self.index, pos)
        self.counter.setText(f"{pos + 1:,} of {n:,}")
        self.prev_b.setEnabled(pos > 0)
        self.next_b.setEnabled(pos < n - 1)

    def go(self, pos: int) -> None:
        n = len(self.index)
        if not n:
            return
        pos = max(0, min(n - 1, pos))
        self.edit.finish()                 # saves the photo being left
        self._stop_motion()
        self.pos = pos
        fid = self.index.file_id(pos)
        info = photoinfo.load(self.conn, fid)
        if info is None:
            # Gone from the catalog since the list was made: say so, never keep showing the last photo.
            self.info = None
            self.strip.set_position(self.index, pos)
            self.counter.setText(f"{pos + 1:,} of {n:,}")
            self.canvas.set_photo(None)
            self.canvas.show_pixmap(None, sharp=False, message="This photo isn't in the library any more")
            return
        self.info = info
        self.refresh_info()
        self.canvas.set_photo((self.info.width, self.info.height))
        self._full_for = None
        self._full_pix = None                  # one full-size picture in memory at a time
        self.strip.set_position(self.index, pos)
        self.load_faces()
        self.counter.setText(f"{pos + 1:,} of {n:,}")
        self.prev_b.setEnabled(pos > 0)
        self.next_b.setEnabled(pos < n - 1)
        self._show_image()
        if self.editing:
            # A video or animated GIF can't be edited: its Info shows meanwhile, and
            # editing picks up again on the next photo.
            can_edit = not self.info.is_video and self._movie is None
            self.side.setCurrentWidget(self.develop if can_edit else self.panel)
            if can_edit:
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
        self.canvas.show_faces = self.faces_b.isChecked() and not on     # editing: the edit tools own the canvas
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
        self._offer_timelapse(i.file_id)
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
        if self.canvas.view_tool == "peaking" and getattr(self, "_af_for", None) != i.file_id:
            self._load_af()                        # the next photo's AF point
        self.canvas.turn_id = i.file_id
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
        why = self.previews.reasons.get(i.file_id)
        self.canvas.show_pixmap(thumb, sharp=False,
                                message=(f"Can't open this photo: {why}" if why else
                                         "Couldn't open this file at full size") if failed else "")
        if thumb is None and not failed:
            self._make_thumb_now(i.file_id)

    def _make_thumb_now(self, file_id: int) -> None:
        row = self.conn.execute("SELECT thumbnail_path, thumb_error FROM files WHERE id = ?", (file_id,)).fetchone()
        if not row or row[0] or row[1] or file_id in getattr(self, "_thumb_asked", set()):
            return
        self._thumb_asked = getattr(self, "_thumb_asked", set()) | {file_id}
        if not hasattr(self, "_thumb_now"):
            self._thumb_now = _ThumbNowSignals(self)
            self._thumb_now.made.connect(self._thumb_made)
        QThreadPool.globalInstance().start(_ThumbNow(self._thumb_now, file_id))

    @unless_closed
    def _thumb_made(self, file_id: int) -> None:
        self.big_thumbs.reload(file_id)
        self.thumb_made.emit(file_id)
        if self.info and self.info.file_id == file_id and not self.canvas.sharp:
            self._show_image()

    def _offer_timelapse(self, file_id: int) -> None:
        from lunelis import timelapses
        sid = timelapses.sequence_of(self.conn, file_id)
        q = timelapses.get(self.conn, sid) if sid else None
        b = self.panel.timelapse_b
        b.setVisible(q is not None)
        self._timelapse_frames = list(q.file_ids) if q is not None else []
        if q is not None:
            b.setText(f"Build timelapse… ({len(q.file_ids):,} frames)")
            b.setToolTip("This photo is a frame of a timelapse: make the video in Create > Timelapse")
            if not getattr(self, "_timelapse_hooked", False):
                b.clicked.connect(lambda: self.build_timelapse.emit(list(self._timelapse_frames)))
                self._timelapse_hooked = True

    def reshow(self) -> None:
        """The photo again from the start, e.g. after Rotate left / right."""
        self._full_pix = None
        if self.info and self.info.is_video:
            self._play_video(self.info)
        else:
            self._show_image()

    # --- videos and animated GIFs ---

    def _play_video(self, i: photoinfo.PhotoInfo) -> None:
        if self.player is None:
            from lunelis.ui.video_player import VideoPlayer
            self.player = VideoPlayer(self.conn)
            self.player.setFocusPolicy(Qt.FocusPolicy.NoFocus)   # keys stay with the photo view
            self.view_stack.addWidget(self.player)
        self.view_stack.setCurrentWidget(self.player)
        from lunelis import turns
        self.player.load(i.path, turn=turns.get(i.file_id))

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

    # --- faces (recognize/faces.py) ---------------------------------------------------------

    def set_faces_overlay(self, on: bool) -> None:
        if self.faces_b.isChecked() != on:
            self.faces_b.setChecked(on)               # comes back here through toggled
            return
        try:
            from lunelis.settings import Settings
            Settings(self.conn).set("faces_overlay", bool(on))
        except Exception:
            pass
        self.canvas.show_faces = on and not self.editing
        self.load_faces()

    def load_faces(self) -> None:
        self.canvas.faces = []
        if self.info is not None and self.faces_b.isChecked() and not self.info.is_video:
            from lunelis.recognize import faces
            for f in faces.faces_of(self.conn, self.info.file_id, with_strangers=True):
                if f.stranger:
                    self.canvas.faces.append((f.id, f.box, "Stranger", "stranger"))
                elif f.animal:
                    self.canvas.faces.append((f.id, f.box, f.name or "Animal", "pet" if f.name else "animal"))
                elif f.name:
                    self.canvas.faces.append((f.id, f.box, f.name, "named"))
                elif f.suggested:
                    self.canvas.faces.append((f.id, f.box, f"{f.suggested}?", "suggested"))
                else:
                    self.canvas.faces.append((f.id, f.box, "", "unknown"))
        self.canvas.update()

    def _face_changed(self) -> None:
        self.load_faces()
        self.refresh_info()                            # the People tag shows in the Info panel
        self.faces_changed.emit()

    def _ask_name(self, title: str) -> str | None:
        from PySide6.QtWidgets import QInputDialog
        from lunelis.recognize import faces
        names = [p.name for p in faces.people(self.conn)]
        name, ok = QInputDialog.getItem(self, title, "Who is this? Pick someone or type a new name:",
                                        names or [""], 0, True)
        name = (name or "").strip()
        if ok and name.casefold() == "unknown":
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Strangers", "\"Unknown\" is kept for strangers: choose Stranger "
                                    "instead, and the photo is tagged People > Unknown.")
            return None
        return name if ok else None                # "" = OK with no name: a box without a name

    def _face_menu(self, face_id: int, at) -> None:
        from PySide6.QtWidgets import QMenu
        from lunelis.recognize import faces
        f = next((x for x in faces.faces_of(self.conn, self.info.file_id, with_strangers=True)
                  if x.id == face_id), None) if self.info else None
        if f is None:
            return
        m = QMenu(self)
        self._face_actions(m, f)
        m.exec(at)

    def _fill_faces_menu(self) -> None:
        """The menu beside the Faces button (0.53): add, change or remove faces by hand."""
        from lunelis.recognize import faces
        m = self.faces_menu
        m.clear()
        show = m.addAction("Show faces (F)", lambda: self.set_faces_overlay(not self.faces_b.isChecked()))
        show.setCheckable(True)
        show.setChecked(self.faces_b.isChecked())
        ok = self.info is not None and not self.info.is_video and not self.editing
        m.addSeparator()
        for text, mode in (("Add a person… (drag a box around the face)", "person"),
                           ("Add a pet or animal… (drag a box around it)", "pet")):
            m.addAction(text, lambda md=mode: self.start_face_draw(md)).setEnabled(ok)
        again = m.addAction("Look for faces in this photo again", self._faces_look_again)
        again.setEnabled(ok and faces.available())
        if ok:
            here = faces.faces_of(self.conn, self.info.file_id, with_strangers=True)
            if here:
                m.addSeparator()
            n = 0
            for f in here:
                if f.name:
                    label = f.name + (" (pet)" if f.pet else "")
                elif f.animal:
                    label = "Animal"
                elif f.stranger:
                    label = "Stranger"
                else:
                    n += 1
                    label = f"Unnamed face {n}" + (f" - {f.suggested}?" if f.suggested else "")
                self._face_actions(m.addMenu(label), f)
        m.addSeparator()
        m.addAction("Open the People page", lambda: self.show_person.emit(-1))

    def _face_actions(self, m, f) -> None:
        """What can be done to one face: in its right-click menu and the Faces menu."""
        from lunelis.recognize import faces
        m.addAction("Redraw this box… (drag a new one)", lambda: self.start_face_draw(("redraw", f.id)))
        m.addAction("Remove this box", lambda: (faces.remove_face(self.conn, f.id), self._face_changed()))
        m.addSeparator()
        if f.animal:                               # 0.52: pets
            m.addAction("Rename this pet…" if f.name else "Name this pet…", lambda: self._name_pet(f.id))
            m.addAction("Not an animal", lambda: (faces.ignore(self.conn, [f.id], ignored=False),
                                                  self._face_changed()))
            m.addAction("A person, not an animal…", lambda: (faces.ignore(self.conn, [f.id], ignored=False),
                                                           self._name_face(f.id)))
            if f.name:
                m.addSeparator()
                m.addAction(f"All photos of {f.name}…", lambda: self.show_person.emit(f.person_id))
            return
        if f.suggested and not f.name:
            m.addAction(f"Yes, this is {f.suggested}", lambda: (faces.confirm(self.conn, [f.id], f.suggested_id),
                                                                self._face_changed()))
        m.addAction("Rename…" if f.name else "Name…", lambda: self._name_face(f.id))
        if f.name or f.suggested:
            m.addAction(f"Not {f.name or f.suggested}", lambda: (faces.reject(self.conn, [f.id]), self._face_changed()))
        if f.stranger:
            m.addAction("Not a stranger", lambda: (faces.ignore(self.conn, [f.id], ignored=False),
                                                   self._face_changed()))
        else:
            m.addAction("Stranger (tag the photo People > Unknown)",
                        lambda: (faces.mark_strangers(self.conn, [f.id]), self._face_changed()))
        m.addAction("An animal…", lambda: self._name_pet(f.id, ask_first=True))
        if faces.unnamed_in(self.conn, [f.file_id]):
            m.addSeparator()
            m.addAction("Everyone not named here is a stranger",
                        lambda: (faces.rest_are_strangers(self.conn, [f.file_id]), self._face_changed()))
        if f.name:
            m.addSeparator()
            m.addAction(f"All photos of {f.name}…", lambda: self.show_person.emit(f.person_id))

    def start_face_draw(self, mode) -> None:
        """The next drag on the photo draws a box: a new person, a new pet, or
        an existing face's box again. Esc cancels."""
        if self.info is None or self.info.is_video:
            return
        if not self.faces_b.isChecked():
            self.set_faces_overlay(True)
        self.canvas.draw_mode = mode
        self.canvas.setCursor(Qt.CursorShape.CrossCursor)
        self.canvas.message = "Drag a box around it - Esc to cancel"
        self.canvas.update()

    def _end_face_draw(self) -> None:
        self.canvas.draw_mode = None
        self.canvas.message = ""
        self.canvas.setCursor(Qt.CursorShape.ArrowCursor)
        self.canvas.update()

    def _faces_look_again(self) -> None:
        from lunelis.recognize import faces
        if self.info is None:
            return
        try:
            n = faces.look_again(self.conn, self.info.file_id)
        except RuntimeError as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Faces", str(e).capitalize() + ".")
            return
        if not self.faces_b.isChecked():
            self.set_faces_overlay(True)
        self._face_changed()
        self.canvas.message = f"{n} face{'s' if n != 1 else ''} found" if n else "No faces found - add one by hand"
        self.canvas.update()

    def _name_face(self, face_id: int) -> None:
        from lunelis.recognize import faces
        name = self._ask_name("Name this face")
        if name:
            try:
                faces.name_faces(self.conn, [face_id], name)
            except ValueError as e:                # a pet's name (0.52)
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.information(self, "Name this face", str(e))
                return
            self._face_changed()

    def _name_pet(self, face_id: int, ask_first: bool = False) -> None:
        """Mark a face as an animal and, when a name is given, name the pet."""
        from PySide6.QtWidgets import QInputDialog
        from lunelis.recognize import faces
        pets = [p.name for p in faces.people(self.conn, faces.PET)]
        name, ok = QInputDialog.getItem(self, "An animal", "Which pet is this? Pick one, type a new name, or "
                                        "leave it empty for an animal you don't name:", pets or [""], 0, True)
        if not ok:
            return
        name = (name or "").strip()
        try:
            if name:
                faces.name_pet(self.conn, [face_id], name)
            else:
                faces.mark_animal(self.conn, [face_id])
        except ValueError as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "An animal", str(e))
            return
        self._face_changed()

    def _face_drawn(self, box: list) -> None:
        if self.info is None:
            return
        from lunelis.recognize import faces
        mode = self.canvas.draw_mode
        self._end_face_draw()
        if isinstance(mode, tuple) and mode[0] == "redraw":
            faces.move_face(self.conn, mode[1], box)
            self._face_changed()
            return
        if mode == "pet":
            fid = faces.add_face(self.conn, self.info.file_id, box, None)
            faces.mark_animal(self.conn, [fid])
            self._face_changed()
            self._name_pet(fid)                      # a name is optional: it stays an animal without one
            return
        name = self._ask_name("A face Lunelis missed")
        if name is None:
            self.canvas.update()                     # cancelled: no box is added
            return
        try:
            faces.add_face(self.conn, self.info.file_id, box, name or None)
        except ValueError as e:                      # a pet's name
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Name this face", str(e))
        self._face_changed()

    def keyPressEvent(self, e) -> None:
        k, mods = e.key(), e.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        if k == Qt.Key.Key_Escape and self.canvas.draw_mode is not None:
            self._end_face_draw()                      # not adding a face after all
            return
        if k == Qt.Key.Key_F and not ctrl and not self.editing and not (mods & Qt.KeyboardModifier.ShiftModifier):
            self.set_faces_overlay(not self.faces_b.isChecked())
            return
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
                if self.workspace:
                    self.back.emit()               # the Edit page: Esc leaves it, as Back does elsewhere
                else:
                    self.set_editing(False)
                return
        if self.info is not None and self.info.is_video and self.player is not None and not ctrl:
            act = {Qt.Key.Key_K: self.player.toggle, Qt.Key.Key_Space: self.player.toggle,
                   Qt.Key.Key_J: lambda: self.player.jump(-5000),
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
