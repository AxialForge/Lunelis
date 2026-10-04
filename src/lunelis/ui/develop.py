"""
Editing in the photo view (E, or the Edit button): the Develop panel
replaces the Info panel, and the big picture shows the edit live.

- EditSession decodes the photo once (a RAW through rawpy, 2560 px) and
  renders previews on a background thread. Requests coalesce: while one
  render runs, only the newest waiting request survives, so dragging a
  slider never builds a queue. While a slider is held, previews render at
  half size (4x fewer pixels); letting go renders at full display size.
- EditCanvas is the photo canvas plus a crop rectangle (drag the corners,
  edges, or the middle; an aspect ratio can be locked).
- DevelopPanel has Auto / Reset / Before-after, the filter strip (each
  filter previewed on this photo; one Amount slider; save your own; "Adjust
  sliders" unpacks a filter into the sliders), crop and rotate, and one
  slider per adjustment (double-click a slider's name to reset it).
- BatchOutputs renders proxies + thumbnails for many photos at once (paste
  edit settings, reset edits, the new-import filter).
- EditMode ties them to the photo view: every change is saved to the
  catalog shortly after you stop (no Save button), Ctrl+Z / Ctrl+Shift+Z
  step through this photo's history, and leaving the photo renders the
  cached proxy + grid thumbnail in the background.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QMenu, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QSlider, QToolButton, QVBoxLayout, QWidget,
)

from lunelis.edit import pipeline, render, store
from lunelis.edit.stack import GROUPS, PARAMS, Geometry, Stack, effective
from lunelis.ui.detail_view import PhotoCanvas

FULL_CROP = (0.0, 0.0, 1.0, 1.0)


def to_qimage(a: np.ndarray) -> QImage:
    u8 = np.ascontiguousarray((np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8))
    h, w = u8.shape[:2]
    return QImage(u8.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


def _shrink(a: np.ndarray, edge: int) -> np.ndarray:
    return render._resize(a, edge)


# --- decoding and rendering off the GUI thread ---------------------------------------------------

class _Signals(QObject):
    decoded = Signal(int, object)          # token, (base, display, fast) or an error message
    rendered = Signal(int, int, QImage, bool)   # token, request id, image, fast


class _Decode(QRunnable):
    def __init__(self, signals, token: int, path: str, is_raw: bool, display_edge: int) -> None:
        super().__init__()
        self.s, self.token, self.path, self.is_raw, self.edge = signals, token, path, is_raw, display_edge

    def run(self) -> None:
        try:
            base = render.load_source(self.path, self.is_raw, render.PROXY_EDGE)
            disp = _shrink(base, self.edge)
            fast = _shrink(disp, max(200, max(disp.shape[:2]) // 2))
            self.s.decoded.emit(self.token, (base, disp, fast))
        except Exception as e:
            self.s.decoded.emit(self.token, f"{type(e).__name__}: {e}")


class _Render(QRunnable):
    def __init__(self, signals, token: int, rid: int, src: np.ndarray, stack: Stack, fparams, fast: bool,
                 ai_maps=None) -> None:
        super().__init__()
        self.s, self.token, self.rid = signals, token, rid
        self.src, self.stack, self.fparams, self.fast, self.ai_maps = src, stack, fparams, fast, ai_maps
        self.lens_info, self.lens_cache = None, None

    def run(self) -> None:
        try:
            src, stack = self.src, self.stack
            _p, p_nr = pipeline.split_noise(effective(stack, self.fparams))
            prepared = False
            if (stack.lens or p_nr) and self.lens_cache is not None:
                # Lens corrections and noise reduction cost up to a second or two:
                # done once per setting, then reused while other sliders move.
                key = (id(self.src), tuple(sorted(stack.lens.items())), tuple(sorted(p_nr.items())))
                if self.lens_cache.get("key") != key:
                    self.lens_cache.clear()
                    self.lens_cache.update(key=key, img=pipeline.prepare_source(self.src, stack, p_nr,
                                                                                 self.lens_info))
                src, prepared = self.lens_cache["img"], True
            img = to_qimage(pipeline.apply(src, stack, self.fparams, self.ai_maps, prepared=prepared))
        except Exception:
            img = QImage()
        try:
            self.s.rendered.emit(self.token, self.rid, img, self.fast)
        except RuntimeError:                 # the Edit panel closed while this rendered
            pass


class _Outputs(QRunnable):
    """Render (or, for a reset, clear) one photo's proxy + thumbnail. All of
    them run on one single-thread pool, in order - so a quick re-edit or
    Reset can never be overtaken by an older render."""

    def __init__(self, done, file_id: int, args=None, clear=None) -> None:
        super().__init__()
        self.done, self.file_id, self.args, self.clear = done, file_id, args, clear

    def run(self) -> None:
        try:
            if self.clear is not None:
                render.clear_outputs(*self.clear)
            else:
                render.render_outputs(*self.args)
            self.done.emit(self.file_id, True)
        except Exception:
            self.done.emit(self.file_id, False)


class EditSession(QObject):
    ready = Signal()                       # the photo is decoded
    failed = Signal(str)
    rendered = Signal(QImage, bool)        # preview, fast (while dragging)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._s = _Signals()
        self._s.decoded.connect(self._decoded)
        self._s.rendered.connect(self._rendered)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)     # one render at a time; newer requests wait and coalesce
        self.token = 0
        self.base = self.disp = self.fast = None
        self._rid = 0
        self._busy = False
        self._want: tuple | None = None

    def open(self, path: str, is_raw: bool, display_edge: int, lens_info=None) -> None:
        self._lens_caches = {}
        self.lens_info = lens_info
        self.token += 1
        self.base = self.disp = self.fast = None
        self._want = None
        QThreadPool.globalInstance().start(_Decode(self._s, self.token, path, is_raw, display_edge))

    def close(self) -> None:
        self.token += 1
        self.base = self.disp = self.fast = None
        self._want = None

    def request(self, stack: Stack, fparams: dict | None, fast: bool = False, ai_maps=None) -> None:
        self._rid += 1
        self._want = (self._rid, stack, fparams, fast, ai_maps)
        self._pump()

    lens_info = None
    _lens_caches: dict = {}

    def _pump(self) -> None:
        if self._busy or self._want is None or self.disp is None:
            return
        rid, stack, fparams, fast, ai_maps = self._want
        self._want = None
        self._busy = True
        job = _Render(self._s, self.token, rid, self.fast if fast else self.disp, stack, fparams, fast, ai_maps)
        job.lens_info = self.lens_info
        job.lens_cache = self._lens_caches.setdefault((self.token, fast), {})
        self.pool.start(job)

    def _decoded(self, token: int, result) -> None:
        if token != self.token:
            return
        if isinstance(result, str):
            self.failed.emit(result)
            return
        self.base, self.disp, self.fast = result
        self.ready.emit()
        self._pump()

    def _rendered(self, token: int, rid: int, img: QImage, fast: bool) -> None:
        self._busy = False
        if token == self.token and not img.isNull():
            self.rendered.emit(img, fast)
        self._pump()


# --- the canvas, with a crop rectangle ---------------------------------------------------------

class EditCanvas(PhotoCanvas):
    crop_changed = Signal(tuple, bool)     # (x0, y0, x1, y1) of the shown image, final (mouse released)
    HANDLE = 10

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.crop: tuple | None = None     # set = crop mode
        self.aspect: float | None = None   # locked width/height, in image pixels
        self._grab: str | None = None
        self._start: tuple | None = None
        self._at = QPointF()
        self.setMouseTracking(True)
        from lunelis.ui.mask_tool import MaskTool
        self.mask = MaskTool(self)               # active while a mask is selected (kind set)

    def set_crop(self, crop: tuple | None, aspect: float | None = None) -> None:
        self.crop, self.aspect = crop, aspect
        if crop is not None:
            self.zoomed = False
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def _crop_rect(self) -> QRectF:
        r = self._fit_rect()
        x0, y0, x1, y1 = self.crop
        return QRectF(r.x() + x0 * r.width(), r.y() + y0 * r.height(), (x1 - x0) * r.width(), (y1 - y0) * r.height())

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        if self.crop is None and self.mask.kind is not None and self.pix is not None:
            self.mask.paint(QPainter(self))
            return
        if self.crop is None or self.pix is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        img, c = self._fit_rect(), self._crop_rect()
        veil = QColor(0, 0, 0, 150)
        p.fillRect(QRectF(img.left(), img.top(), img.width(), c.top() - img.top()), veil)
        p.fillRect(QRectF(img.left(), c.bottom(), img.width(), img.bottom() - c.bottom()), veil)
        p.fillRect(QRectF(img.left(), c.top(), c.left() - img.left(), c.height()), veil)
        p.fillRect(QRectF(c.right(), c.top(), img.right() - c.right(), c.height()), veil)
        p.setPen(QPen(QColor(255, 255, 255, 110), 1))
        for k in (1, 2):                   # rule of thirds
            p.drawLine(QPointF(c.left() + c.width() * k / 3, c.top()), QPointF(c.left() + c.width() * k / 3, c.bottom()))
            p.drawLine(QPointF(c.left(), c.top() + c.height() * k / 3), QPointF(c.right(), c.top() + c.height() * k / 3))
        p.setPen(QPen(QColor(255, 255, 255), 1.5))
        p.drawRect(c)
        p.setPen(QPen(QColor(255, 255, 255), 4))
        L = 16
        for x, y, dx, dy in ((c.left(), c.top(), 1, 1), (c.right(), c.top(), -1, 1),
                             (c.left(), c.bottom(), 1, -1), (c.right(), c.bottom(), -1, -1)):
            p.drawLine(QPointF(x, y), QPointF(x + dx * L, y))
            p.drawLine(QPointF(x, y), QPointF(x, y + dy * L))

    def _hit(self, pos: QPointF) -> str | None:
        c = self._crop_rect()
        h = self.HANDLE
        near_l, near_r = abs(pos.x() - c.left()) < h, abs(pos.x() - c.right()) < h
        near_t, near_b = abs(pos.y() - c.top()) < h, abs(pos.y() - c.bottom()) < h
        inside_x = c.left() - h < pos.x() < c.right() + h
        inside_y = c.top() - h < pos.y() < c.bottom() + h
        edge = ("t" if near_t else "b" if near_b else "") + ("l" if near_l else "r" if near_r else "")
        if edge and inside_x and inside_y:
            return edge
        if c.contains(pos):
            return "move"
        return None

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.MiddleButton:
            return super().mousePressEvent(e)          # the middle button pans, in every mode
        if self.crop is None and self.mask.press(e):
            return
        if self.crop is None:
            return super().mousePressEvent(e)
        if e.button() == Qt.MouseButton.LeftButton:
            self._grab = self._hit(e.position())
            self._start, self._at = self.crop, e.position()

    def mouseMoveEvent(self, e) -> None:
        if self._pan is not None:
            return super().mouseMoveEvent(e)
        if self.crop is None and self.mask.move(e):
            return
        if self.crop is None:
            return super().mouseMoveEvent(e)
        if self._grab is None:
            hit = self._hit(e.position())
            cursors = {"move": Qt.CursorShape.SizeAllCursor, "l": Qt.CursorShape.SizeHorCursor,
                       "r": Qt.CursorShape.SizeHorCursor, "t": Qt.CursorShape.SizeVerCursor,
                       "b": Qt.CursorShape.SizeVerCursor, "tl": Qt.CursorShape.SizeFDiagCursor,
                       "br": Qt.CursorShape.SizeFDiagCursor, "tr": Qt.CursorShape.SizeBDiagCursor,
                       "bl": Qt.CursorShape.SizeBDiagCursor}
            self.setCursor(cursors.get(hit, Qt.CursorShape.ArrowCursor))
            return
        r = self._fit_rect()
        dx = (e.position().x() - self._at.x()) / r.width()
        dy = (e.position().y() - self._at.y()) / r.height()
        self.crop = self._dragged(self._start, self._grab, dx, dy)
        self.crop_changed.emit(self.crop, False)
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        if self._pan is not None:
            return super().mouseReleaseEvent(e)
        if self.crop is None and self.mask.release(e):
            return
        if self.crop is None:
            return super().mouseReleaseEvent(e)
        if self._grab is not None:
            self._grab = None
            self.crop_changed.emit(self.crop, True)

    def mouseDoubleClickEvent(self, e) -> None:
        if self.crop is None and self.mask.kind is None:
            super().mouseDoubleClickEvent(e)

    def wheelEvent(self, e) -> None:
        super().wheelEvent(e)                          # zoom works while cropping and masking too

    def leaveEvent(self, e) -> None:
        self.mask.leave()
        super().leaveEvent(e)

    def _dragged(self, c: tuple, grab: str, dx: float, dy: float) -> tuple:
        x0, y0, x1, y1 = c
        MIN = 0.05
        if grab == "move":
            dx = max(-x0, min(1 - x1, dx))
            dy = max(-y0, min(1 - y1, dy))
            return (x0 + dx, y0 + dy, x1 + dx, y1 + dy)
        if "l" in grab:
            x0 = max(0.0, min(x1 - MIN, x0 + dx))
        if "r" in grab:
            x1 = min(1.0, max(x0 + MIN, x1 + dx))
        if "t" in grab:
            y0 = max(0.0, min(y1 - MIN, y0 + dy))
        if "b" in grab:
            y1 = min(1.0, max(y0 + MIN, y1 + dy))
        if self.aspect and self.pix is not None:
            return fit_aspect((x0, y0, x1, y1), self.aspect, self.pix.width() / self.pix.height(), grab)
        return (x0, y0, x1, y1)


def fit_aspect(c: tuple, aspect: float, image_aspect: float, anchor: str = "") -> tuple:
    """Make crop `c` (fractions) have width/height `aspect` in pixels,
    keeping the side being dragged and staying inside the image."""
    x0, y0, x1, y1 = c
    w, h = x1 - x0, y1 - y0
    want_h = w * image_aspect / aspect             # height (fraction) for this width
    if anchor in ("t", "b"):
        w = h * aspect / image_aspect
    else:
        h = want_h
    if h > 1:
        h, w = 1.0, aspect / image_aspect
    if w > 1:
        w, h = 1.0, image_aspect / aspect
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    if "l" in anchor:
        x0 = x1 - w
    elif "r" in anchor:
        x1 = x0 + w
    else:
        x0, x1 = cx - w / 2, cx + w / 2
    if "t" in anchor:
        y0 = y1 - h
    elif "b" in anchor:
        y1 = y0 + h
    else:
        y0, y1 = cy - h / 2, cy + h / 2
    # Slide back inside the image.
    sx = -x0 if x0 < 0 else (1 - x1 if x1 > 1 else 0)
    sy = -y0 if y0 < 0 else (1 - y1 if y1 > 1 else 0)
    return (x0 + sx, y0 + sy, x1 + sx, y1 + sy)


def rotate_crop(c: tuple, quarter_turns_cw: int) -> tuple:
    for _ in range(quarter_turns_cw % 4):
        x0, y0, x1, y1 = c
        c = (1 - y1, x0, 1 - y0, x1)
    return c


# --- the panel -----------------------------------------------------------------------------

class _ResetSlider(QSlider):
    """A slider that goes back to 0 on a double-click."""

    reset = Signal()

    def mouseDoubleClickEvent(self, e) -> None:
        self.reset.emit()


class ParamSlider(QWidget):
    """Name, a number you can type into, and the slider. Double-click the
    name or the slider to put it back to 0."""

    moved = Signal(str, float, bool)       # key, value, final (released / typed / keyboard)

    def __init__(self, key: str, label: str, lo: float, hi: float, step: float, parent=None) -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import QAbstractSpinBox, QDoubleSpinBox
        self.key, self.scale = key, 1 / step
        g = QGridLayout(self)
        g.setContentsMargins(0, 1, 0, 1)
        g.setVerticalSpacing(0)
        self.name = QLabel(label)
        self.name.setToolTip("Double-click to reset")
        self.name.mouseDoubleClickEvent = lambda _e: self.set_value(0, emit=True)
        self.number = QDoubleSpinBox(objectName="SliderNumber")
        self.number.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.number.setDecimals(2 if step < 1 else 0)
        self.number.setRange(lo, hi)
        self.number.setSingleStep(step)
        self.number.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.number.setFixedWidth(64)
        self.number.setKeyboardTracking(False)          # a typed value applies on Enter / leaving the box
        self.number.setToolTip("Type a value, then Enter")
        self.number.valueChanged.connect(self._typed)
        self.slider = _ResetSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(round(lo * self.scale), round(hi * self.scale))
        self.slider.setToolTip("Double-click to reset")
        self.slider.valueChanged.connect(self._changed)
        self.slider.sliderReleased.connect(lambda: self.moved.emit(self.key, self.current(), True))
        self.slider.reset.connect(lambda: self.set_value(0, emit=True))
        g.addWidget(self.name, 0, 0)
        g.addWidget(self.number, 0, 1)
        g.addWidget(self.slider, 1, 0, 1, 2)
        self._show(0)

    @property
    def value(self):                                    # the old read-only label's name
        return self.number

    def current(self) -> float:
        return self.slider.value() / self.scale

    def _show(self, v: float) -> None:
        self.number.blockSignals(True)
        self.number.setValue(v)
        self.number.blockSignals(False)

    def _changed(self, raw: int) -> None:
        v = raw / self.scale
        self._show(v)
        self.moved.emit(self.key, v, not self.slider.isSliderDown())

    def _typed(self, v: float) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(round(v * self.scale))
        self.slider.blockSignals(False)
        self.moved.emit(self.key, self.current(), True)

    def set_value(self, v: float, emit: bool = False) -> None:
        self.slider.blockSignals(not emit)
        self.slider.setValue(round(v * self.scale))
        self.slider.blockSignals(False)
        self._show(v)


class Section(QWidget):
    """A foldable part of the Edit panel: a header you click, and its body."""

    toggled = Signal(str, bool)            # title, open

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        self.title = title
        self.header = QPushButton(objectName="SectionHeader", checkable=True, checked=True)
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header.toggled.connect(self._toggle)
        v.addWidget(self.header)
        self.body = QWidget()
        self.lay = QVBoxLayout(self.body)
        self.lay.setContentsMargins(0, 2, 0, 6)
        self.lay.setSpacing(6)
        v.addWidget(self.body)
        self._label(True)

    def _label(self, open_: bool) -> None:
        self.header.setText(("▾  " if open_ else "▸  ") + self.title.upper().replace("&", "&&"))

    def _toggle(self, open_: bool) -> None:
        self.body.setVisible(open_)
        self._label(open_)
        self.toggled.emit(self.title, open_)

    def set_open(self, open_: bool) -> None:
        self.header.blockSignals(True)
        self.header.setChecked(open_)
        self.header.blockSignals(False)
        self.body.setVisible(open_)
        self._label(open_)


ASPECTS = [("Free", None), ("Original", "orig"), ("1 : 1", 1.0), ("4 : 5", 0.8), ("3 : 2", 1.5),
           ("2 : 3", 2 / 3), ("4 : 3", 4 / 3), ("16 : 9", 16 / 9)]


class DevelopPanel(QScrollArea):
    adjust = Signal(str, float, bool)      # key, value, final
    geometry_action = Signal(str)          # rotate_left | rotate_right | flip_h | flip_v
    angle = Signal(float, bool)
    crop_mode = Signal(bool)
    aspect_changed = Signal(object)
    auto = Signal()
    reset = Signal()
    before = Signal(bool)
    done = Signal()
    filter_chosen = Signal(object)         # name | None
    amount = Signal(float, bool)
    save_filter = Signal()
    unpack_filter = Signal()
    delete_filter = Signal(str)
    curve = Signal(str, tuple, bool)       # channel, points, final
    mask_add = Signal(str)
    mask_select = Signal(int)
    mask_delete = Signal()
    mask_invert = Signal(bool)
    mask_overlay = Signal(bool)
    mask_adjust = Signal(str, float, bool)
    mask_feather = Signal(float, bool)
    brush_changed = Signal()
    lens_changed = Signal(str, object, bool)   # key, value, final

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsScroll")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFixedWidth(340)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page = QWidget(objectName="DetailPanel")
        v = QVBoxLayout(page)
        v.setContentsMargins(20, 16, 20, 20)
        v.setSpacing(6)
        self.setWidget(page)
        head = QHBoxLayout()
        t = QLabel("Edit", objectName="SectionTitle")
        head.addWidget(t, 1)
        self.done_b = QPushButton("Done", clicked=lambda: self.done.emit())
        self.done_b.setToolTip("Back to the photo's info (E)")
        head.addWidget(self.done_b)
        v.addLayout(head)
        self.status = QLabel(objectName="Help")
        self.status.setWordWrap(True)
        v.addWidget(self.status)
        row = QHBoxLayout()
        self.auto_b = QPushButton("Auto", clicked=lambda: self.auto.emit())
        self.auto_b.setToolTip("A starting point from the photo's histogram")
        self.reset_b = QPushButton("Reset", clicked=lambda: self.reset.emit())
        self.reset_b.setToolTip("Back to the original (Ctrl+Z undoes it)")
        self.before_b = QPushButton("Before", checkable=True)
        self.before_b.setToolTip("Show the original (\\ key)")
        self.before_b.toggled.connect(self.before.emit)
        for b in (self.auto_b, self.reset_b, self.before_b):
            row.addWidget(b)
        v.addLayout(row)

        v.addWidget(self._heading("Filters"))
        self.filter_box = QWidget()
        self.filter_grid = QGridLayout(self.filter_box)
        self.filter_grid.setContentsMargins(0, 0, 0, 0)
        self.filter_grid.setSpacing(6)
        self.filter_group = QButtonGroup(self)
        self.filter_group.setExclusive(True)
        self.filter_buttons: dict[object, QToolButton] = {}
        v.addWidget(self.filter_box)
        self.amount_s = ParamSlider("amount", "Amount", 0, 100, 1)
        self.amount_s.moved.connect(lambda _k, val, final: self.amount.emit(val, final))
        v.addWidget(self.amount_s)
        row = QHBoxLayout()
        self.save_filter_b = QPushButton("Save as filter…", clicked=lambda: self.save_filter.emit())
        self.save_filter_b.setToolTip("Keep this look as your own filter, for any photo")
        self.unpack_b = QPushButton("Adjust sliders", clicked=lambda: self.unpack_filter.emit())
        self.unpack_b.setToolTip("Move the filter's values into the sliders below to fine-tune each one")
        row.addWidget(self.save_filter_b)
        row.addWidget(self.unpack_b)
        v.addLayout(row)

        v.addWidget(self._heading("Crop & rotate"))
        row = QHBoxLayout()
        for text, act, tip in (("⟲", "rotate_left", "Rotate left"), ("⟳", "rotate_right", "Rotate right"),
                               ("⇋", "flip_h", "Flip horizontally"), ("⇵", "flip_v", "Flip vertically")):
            b = QPushButton(text, clicked=lambda _=False, a=act: self.geometry_action.emit(a))
            b.setToolTip(tip)
            b.setFixedWidth(40)
            row.addWidget(b)
        self.crop_b = QPushButton("Crop", checkable=True)
        self.crop_b.setToolTip("Drag the corners or edges; Enter when done (R)")
        self.crop_b.toggled.connect(self.crop_mode.emit)
        row.addWidget(self.crop_b, 1)
        v.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("Aspect"))
        self.aspect_box = QComboBox()
        for label, val in ASPECTS:
            self.aspect_box.addItem(label, val)
        self.aspect_box.currentIndexChanged.connect(lambda _: self.aspect_changed.emit(self.aspect_box.currentData()))
        row.addWidget(self.aspect_box, 1)
        v.addLayout(row)
        self.straighten = ParamSlider("angle", "Straighten", -45, 45, 0.1)
        self.straighten.moved.connect(lambda _k, val, final: self.angle.emit(val, final))
        v.addWidget(self.straighten)

        self._build_masks(v)
        self._build_lens(v)

        self.sliders: dict[str, ParamSlider] = {}
        from lunelis.ui.curve_editor import CurveEditor
        for group in GROUPS:
            v.addWidget(self._heading(group))
            for p in PARAMS:
                if p.group == group:
                    s = ParamSlider(p.key, p.label, p.lo, p.hi, p.step)
                    s.moved.connect(self.adjust.emit)
                    v.addWidget(s)
                    self.sliders[p.key] = s
            if group == "Light":
                v.addWidget(self._heading("Tone curve"))
                self.curves = CurveEditor()
                self.curves.changed.connect(self.curve.emit)
                v.addWidget(self.curves)
        v.addStretch(1)
        self._fold_sections(v)

    def _build_masks(self, v) -> None:
        from PySide6.QtWidgets import QCheckBox, QListWidget
        from lunelis.edit.masks import LOCAL_KEYS
        from lunelis.edit.stack import BY_KEY
        v.addWidget(self._heading("Masks"))
        rows = (QHBoxLayout(), QHBoxLayout())       # two rows: five buttons don't fit in 300 px
        for r in rows:
            r.setSpacing(4)
        self.mask_buttons = {}
        for i, (kind, label, tip) in enumerate( (("linear", "Gradient", "Full effect at one line, fading to none at the other"),
                                 ("radial", "Radial", "An ellipse: inside (or outside, inverted)"),
                                 ("brush", "Brush", "Paint where it applies; Alt erases"),
                                 ("subject", "Subject", "The main subject, found by a local AI model"),
                                 ("sky", "Sky", "The sky, found by a local AI model"))):
            b = QPushButton(label, clicked=lambda _=False, k=kind: self.mask_add.emit(k))
            b.setToolTip(f"Add a mask: {tip}")
            b.setMinimumWidth(0)
            b.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            rows[0 if i < 3 else 1].addWidget(b)
            self.mask_buttons[kind] = b
        for r in rows:
            v.addLayout(r)
        self.mask_list = QListWidget()
        self.mask_list.setFixedHeight(92)
        self.mask_list.currentRowChanged.connect(self.mask_select.emit)
        v.addWidget(self.mask_list)
        self.mask_box = QWidget()
        mv = QVBoxLayout(self.mask_box)
        mv.setContentsMargins(0, 0, 0, 0)
        mv.setSpacing(4)
        row = QHBoxLayout()
        self.mask_inv = QCheckBox("Invert")
        self.mask_inv.toggled.connect(self.mask_invert.emit)
        self.mask_show = QCheckBox("Show mask (O)")
        self.mask_show.setChecked(True)
        self.mask_show.toggled.connect(self.mask_overlay.emit)
        self.mask_del = QPushButton("Delete", clicked=lambda: self.mask_delete.emit())
        row.addWidget(self.mask_inv)
        row.addWidget(self.mask_show)
        row.addStretch(1)
        row.addWidget(self.mask_del)
        mv.addLayout(row)
        self.mask_feather_s = ParamSlider("feather", "Feather", 0, 100, 1)
        self.mask_feather_s.moved.connect(lambda _k, val, final: self.mask_feather.emit(val, final))
        mv.addWidget(self.mask_feather_s)
        self.brush_box = QWidget()
        bv = QVBoxLayout(self.brush_box)
        bv.setContentsMargins(0, 0, 0, 0)
        self.brush_size = ParamSlider("size", "Brush size", 1, 200, 1)
        self.brush_soft = ParamSlider("soft", "Brush feather", 0, 100, 1)
        self.brush_flow = ParamSlider("flow", "Flow", 5, 100, 1)
        self.brush_erase = QCheckBox("Erase (or hold Alt)")
        for w in (self.brush_size, self.brush_soft, self.brush_flow):
            w.moved.connect(lambda *_: self.brush_changed.emit())
            bv.addWidget(w)
        self.brush_erase.toggled.connect(lambda *_: self.brush_changed.emit())
        bv.addWidget(self.brush_erase)
        self.brush_size.set_value(30)
        self.brush_soft.set_value(50)
        self.brush_flow.set_value(100)
        mv.addWidget(self.brush_box)
        self.mask_sliders: dict[str, ParamSlider] = {}
        for key in LOCAL_KEYS:
            p = BY_KEY[key]
            s = ParamSlider(p.key, p.label, p.lo, p.hi, p.step)
            s.moved.connect(self.mask_adjust.emit)
            mv.addWidget(s)
            self.mask_sliders[key] = s
        v.addWidget(self.mask_box)
        self.mask_box.hide()

    def _build_lens(self, v) -> None:
        from PySide6.QtWidgets import QCheckBox
        v.addWidget(self._heading("Lens corrections"))
        self.lens_profile = QCheckBox("Use the lens profile")
        self.lens_profile.setToolTip("Distortion, colour fringing and vignetting from the lens's profile "
                                     "(lensfun). Camera JPEGs are usually corrected in the camera already.")
        self.lens_profile.toggled.connect(lambda on: self.lens_changed.emit("profile", on, True))
        v.addWidget(self.lens_profile)
        self.lens_name = QLabel(objectName="Help")
        self.lens_name.setWordWrap(True)
        v.addWidget(self.lens_name)
        self.lens_sliders = {}
        for key, label in (("distortion", "Distortion"), ("vignette", "Vignetting"),
                           ("ca_red", "Fringing red / cyan"), ("ca_blue", "Fringing blue / yellow")):
            s = ParamSlider(key, label, -100, 100, 1)
            s.moved.connect(self.lens_changed.emit)
            v.addWidget(s)
            self.lens_sliders[key] = s

    def show_lens(self, settings: dict, profile: str | None, lens_name: str | None) -> None:
        self.lens_profile.blockSignals(True)
        self.lens_profile.setChecked(bool(settings.get("profile")))
        self.lens_profile.blockSignals(False)
        self.lens_profile.setEnabled(profile is not None or bool(settings.get("profile")))
        self.lens_name.setText(f"Profile: {profile}" if profile else
                               f"No profile for {lens_name}." if lens_name else "The lens isn't recorded.")
        for key, s in self.lens_sliders.items():
            s.set_value(settings.get(key, 0))

    def show_masks(self, masks: tuple, current: int) -> None:
        from lunelis.edit.masks import KINDS
        names = {"linear": "Gradient", "radial": "Radial", "brush": "Brush", "subject": "Subject", "sky": "Sky"}
        self.mask_list.blockSignals(True)
        self.mask_list.clear()
        counts: dict[str, int] = {}
        for m in masks:
            counts[m.kind] = counts.get(m.kind, 0) + 1
            n = names.get(m.kind, m.kind) + (f" {counts[m.kind]}" if counts[m.kind] > 1 else "")
            self.mask_list.addItem(n + (" (inverted)" if m.invert else ""))
        self.mask_list.setCurrentRow(current)
        self.mask_list.blockSignals(False)
        self.mask_box.setVisible(0 <= current < len(masks))
        if 0 <= current < len(masks):
            m = masks[current]
            self.mask_inv.blockSignals(True)
            self.mask_inv.setChecked(m.invert)
            self.mask_inv.blockSignals(False)
            self.mask_feather_s.setVisible(m.kind == "radial")
            if m.kind == "radial":
                self.mask_feather_s.set_value(round(m.shape[4] * 100))
            self.brush_box.setVisible(m.kind == "brush")
            for key, s in self.mask_sliders.items():
                s.set_value(m.adjust.get(key, 0))

    def brush_settings(self) -> tuple[tuple[float, float, float], bool]:
        return ((self.brush_size.current() / 1000, self.brush_soft.current() / 100, self.brush_flow.current() / 100),
                self.brush_erase.isChecked())

    @staticmethod
    def _heading(text: str) -> QLabel:
        h = QLabel(text.upper(), objectName="FilterLabel")
        h.setProperty("section", text)
        h.setContentsMargins(0, 10, 0, 0)
        return h

    def _fold_sections(self, v: QVBoxLayout) -> None:
        """Turn every heading and what follows it into a foldable Section
        (built flat first, then grouped - so the builders stay simple)."""
        from lunelis.settings import Settings
        self.sections: dict[str, Section] = {}
        items = []
        while v.count():
            items.append(v.takeAt(0))
        current = None
        for it in items:
            w = it.widget()
            title = w.property("section") if w is not None else None
            if title:
                current = Section(title)
                current.toggled.connect(self._section_toggled)
                self.sections[title] = current
                v.addWidget(current)
                w.hide()                               # gone now, not at the next event loop
                w.setParent(None)
                w.deleteLater()
            elif current is None or it.spacerItem() is not None:
                v.addItem(it)
            elif it.layout() is not None:
                # A row that's a layout: its widgets only move with it inside a
                # container widget (addItem() alone left them drawn at the top).
                holder = QWidget()
                lay = it.layout()
                lay.setParent(None)
                holder.setLayout(lay)
                lay.setContentsMargins(0, 0, 0, 0)
                current.lay.addWidget(holder)
            else:
                current.lay.addWidget(w)
        self._conn_for_sections = None

    def restore_sections(self, conn) -> None:
        from lunelis.settings import Settings
        self._conn_for_sections = conn
        closed = set(Settings(conn).get("edit_sections_closed") or [])
        for title, sec in self.sections.items():
            sec.set_open(title not in closed)

    def _section_toggled(self, title: str, open_: bool) -> None:
        if self._conn_for_sections is None:
            return
        from lunelis.settings import Settings
        s = Settings(self._conn_for_sections)
        closed = set(s.get("edit_sections_closed") or [])
        (closed.discard if open_ else closed.add)(title)
        s.set("edit_sections_closed", sorted(closed))

    def show_stack(self, adjust: dict, geometry: Geometry, filter_name=None, amount: int = 100,
                   curves: dict | None = None) -> None:
        """Sliders show the manual adjustments (a filter's own values stay
        in the filter)."""
        self.curves.set_curves(curves or {})
        for key, s in self.sliders.items():
            s.set_value(adjust.get(key, 0))
        self.straighten.set_value(geometry.angle)
        b = self.filter_buttons.get(filter_name) or self.filter_buttons.get(None)
        if b is not None:
            b.blockSignals(True)
            b.setChecked(True)
            b.blockSignals(False)
        self.amount_s.set_value(amount)
        self.amount_s.setVisible(filter_name is not None)
        self.unpack_b.setEnabled(filter_name is not None)

    def set_filters(self, tiles: list[tuple], current) -> None:
        """tiles: (name or None, QPixmap or None, yours)."""
        for b in self.filter_buttons.values():
            self.filter_group.removeButton(b)
            b.deleteLater()
        self.filter_buttons = {}
        for i, (name, pix, yours) in enumerate(tiles):
            b = QToolButton(checkable=True)
            b.setText((name or "None").replace("&", "&&"))       # "&" is a Qt shortcut marker
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            b.setIconSize(QSize(88, 66))
            b.setFixedSize(96, 92)
            if pix is not None:
                b.setIcon(QIcon(pix))
            b.setChecked(name == current)
            b.clicked.connect(lambda _=False, n=name: self.filter_chosen.emit(n))
            if yours:
                b.setToolTip(f"{name} - your filter (right-click to delete)")
                b.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                b.customContextMenuRequested.connect(lambda _p, n=name, btn=b: self._filter_menu(n, btn))
            self.filter_group.addButton(b)
            self.filter_grid.addWidget(b, i // 3, i % 3)
            self.filter_buttons[name] = b

    def _filter_menu(self, name: str, button) -> None:
        m = QMenu(self)
        m.addAction(f"Delete filter '{name}'…", lambda: self.delete_filter.emit(name))
        m.exec(button.mapToGlobal(button.rect().bottomLeft()))


# --- the controller ------------------------------------------------------------------------------

class EditMode(QObject):
    """Editing one photo at a time inside the photo view."""

    saved = Signal(int)                    # file id whose proxy + thumbnail were just re-rendered
    active_changed = Signal(bool)

    def __init__(self, conn, canvas: EditCanvas, panel: DevelopPanel, thumb_cache, edit_cache, parent=None) -> None:
        super().__init__(parent)
        self.conn, self.canvas, self.panel = conn, canvas, panel
        self.thumb_cache, self.edit_cache = thumb_cache, edit_cache
        self.session = EditSession(self)
        self.session.ready.connect(self._ready)
        self.session.failed.connect(self._failed)
        self.session.rendered.connect(self._rendered)
        self.info = None
        self.has_render = False
        self._live = "fast"
        self.stack = Stack()
        self.history: list[Stack] = []
        self.pos = 0
        self.saved_stack = Stack()
        self.cropping = False
        self.showing_before = False
        self._aspect = None
        self.out_pool = QThreadPool(self)
        self.out_pool.setMaxThreadCount(1)
        self._out_signals = _OutSignals()
        self._out_signals.done.connect(self._outputs_done)
        self._save_timer = QTimer(self, singleShot=True, interval=700, timeout=self.save)
        self._overlay_timer = QTimer(self, singleShot=True, interval=60, timeout=lambda: self._update_overlay())
        self._before_pix = None                         # (decoded image, its QPixmap): Before shows it at once
        self._auto_signals = _AutoSignals()
        self._auto_signals.done.connect(self._auto_done)
        panel.adjust.connect(self._adjust)
        panel.angle.connect(self._angle)
        panel.geometry_action.connect(self._geometry_action)
        panel.crop_mode.connect(self.set_crop_mode)
        panel.aspect_changed.connect(self._aspect_changed)
        panel.auto.connect(self.auto)
        panel.reset.connect(lambda: self._set(Stack()))
        panel.before.connect(self.set_before)
        panel.filter_chosen.connect(self._filter)
        panel.amount.connect(self._amount)
        panel.save_filter.connect(self._save_filter)
        panel.unpack_filter.connect(self._unpack)
        panel.delete_filter.connect(self._delete_filter)
        panel.curve.connect(self._curve)
        panel.mask_add.connect(self.add_mask)
        panel.mask_select.connect(self.select_mask)
        panel.mask_delete.connect(self.delete_mask)
        panel.mask_invert.connect(self._mask_invert)
        panel.mask_overlay.connect(self.set_overlay)
        panel.mask_adjust.connect(self._mask_adjust)
        panel.mask_feather.connect(self._mask_feather)
        panel.brush_changed.connect(self._brush_changed)
        panel.lens_changed.connect(self._lens_changed)
        canvas.mask.shape_changed.connect(self._mask_shape)
        canvas.mask.stroke.connect(self._mask_stroke)
        self.mask_index = -1
        canvas.crop_changed.connect(self._crop_moved)
        self.ai_maps: dict = {}
        self._ai_busy: set[str] = set()
        self._ai_signals = _AiSignals()
        self._ai_signals.downloaded.connect(self._ai_downloaded)
        self._ai_signals.computed.connect(self._ai_computed)
        self._ai_signals.progress.connect(self._ai_progress)

    def _ai_progress(self, done: int, total: int) -> None:
        if getattr(self, "_dl_progress", None) is not None:
            self._dl_progress.setValue(int(done * 100 / max(1, total)))

    @property
    def active(self) -> bool:
        return self.info is not None

    # --- opening / leaving a photo ---

    def start(self, info) -> bool:
        if info is None or info.is_video:
            return False
        self.finish()
        self.info = info
        self.has_render = False
        self.mask_index = -1
        self.ai_maps, self._ai_busy = {}, set()
        self.stack = self.saved_stack = store.get(self.conn, info.file_id)
        self.history, self.pos = [self.stack], 0
        from lunelis.settings import Settings
        self._live = Settings(self.conn).get("edit_live_quality")
        self.panel.restore_sections(self.conn)
        self.panel.set_filters([(None, None, False)] + [(n, None, not b) for n, _, b in store.filters(self.conn)],
                               self.stack.filter)
        self._show_panel()
        self.panel.status.setText("Opening the photo for editing…")
        self.panel.setEnabled(True)
        edge = int(max(self.canvas.width(), self.canvas.height()) * max(self.canvas.devicePixelRatioF(), 1.0))
        from lunelis.edit.lens import LensInfo
        self.session.open(info.path, info.is_raw, max(800, min(render.PROXY_EDGE, edge)),
                          LensInfo(info.make, info.model, info.lens, info.focal, info.aperture))
        self._show_lens()
        self.active_changed.emit(True)
        return True

    def finish(self) -> None:
        """Save, and render the proxy + thumbnail in the background."""
        if self.info is None:
            return
        self.set_crop_mode(False)
        self.set_before(False)
        changed = self.save()
        fid = self.info.file_id
        if changed or (not self.stack.is_identity() and not render.proxy_path(self.edit_cache, fid).exists()):
            if self.stack.is_identity():
                job = _Outputs(self._out_signals.done, fid, clear=(
                    self.info.path, fid, self.info.orientation, self.thumb_cache, self.edit_cache))
            else:
                job = _Outputs(self._out_signals.done, fid, args=(
                    self.info.path, self.info.is_raw, fid, self.stack, self._fparams(),
                    self.thumb_cache, self.edit_cache, self.session.base))
            self.out_pool.start(job)
        self.session.close()
        self.info = None
        self.active_changed.emit(False)

    def save(self) -> bool:
        self._save_timer.stop()
        if self.info is None or self.stack == self.saved_stack:
            return False
        store.save(self.conn, self.info.file_id, self.stack)
        self.saved_stack = self.stack
        return True

    def _fparams(self):
        return store.filter_params(self.conn, self.stack.filter)

    # --- rendering ---

    def _render(self, fast: bool = False) -> None:
        if self.showing_before:
            return
        stack = self.stack
        if self.cropping:
            stack = replace(stack, geometry=replace(stack.geometry, crop=FULL_CROP))
        if fast and self._live == "sharp":
            fast = False
        self.session.request(stack, self._fparams(), fast, self.ai_maps)

    def _ready(self) -> None:
        if getattr(self, "closed", False):
            return
        self.panel.status.setText("")
        self._ensure_ai()
        self._render()
        self._filter_previews()
        Y = self.session.fast @ pipeline.LUMA
        self.panel.curves.set_histogram(np.histogram(Y, bins=64, range=(0, 1))[0].astype(np.float32))

    def _show_panel(self) -> None:
        s = self.stack
        self.panel.show_stack(s.adjust, s.geometry, s.filter, s.amount, s.curves)
        self._show_lens()
        if self.mask_index >= len(s.masks):
            self.mask_index = len(s.masks) - 1
        self.panel.show_masks(s.masks, self.mask_index)
        self._sync_mask_tool()

    def _filter_previews(self) -> None:
        """Each filter, previewed on this photo (tiny, so it's instant)."""
        if self.session.fast is None:
            return
        tiny = _shrink(pipeline.apply_geometry(self.session.fast, self.stack.geometry), 120)
        tiles = [(None, QPixmap.fromImage(to_qimage(tiny)), False)]
        for name, params, builtin in store.filters(self.conn):
            look = pipeline.apply_adjustments(tiny, effective(Stack(name), params))
            tiles.append((name, QPixmap.fromImage(to_qimage(look)), not builtin))
        self.panel.set_filters(tiles, self.stack.filter)

    def _failed(self, why: str) -> None:
        self.panel.status.setText(f"This photo can't be edited: {why}")

    def _rendered(self, img: QImage, fast: bool) -> None:
        if getattr(self, "closed", False):
            return
        if self.info is None or self.showing_before:
            return
        self.has_render = True
        size_changed = self.canvas.pix is None or self.canvas.pix.size() != img.size()
        self.canvas.show_pixmap(QPixmap.fromImage(img), sharp=True)
        if size_changed and self._mask() is not None:
            self._update_overlay()

    def _outputs_done(self, file_id: int, ok: bool) -> None:
        if getattr(self, "closed", False):
            return
        self.saved.emit(file_id)

    # --- changes ---

    def _set(self, stack: Stack, *, record: bool = True, fast: bool = False) -> None:
        changed = stack != self.stack
        self.stack = stack
        if record and stack != self.history[self.pos]:
            # A slider drag renders live without recording; letting go records once.
            del self.history[self.pos + 1:]
            self.history.append(stack)
            self.pos = len(self.history) - 1
            self._save_timer.start()
            self._show_panel()
        if changed or not fast:
            self._render(fast)

    def _adjust(self, key: str, value: float, final: bool) -> None:
        self._set(self.stack.with_adjust(key, value), record=final, fast=not final)

    def _angle(self, value: float, final: bool) -> None:
        self._set(replace(self.stack, geometry=replace(self.stack.geometry, angle=value)), record=final, fast=not final)

    def _geometry_action(self, action: str) -> None:
        g = self.stack.geometry
        if action in ("rotate_left", "rotate_right"):
            turns = 1 if action == "rotate_right" else 3
            g = replace(g, rotate=(g.rotate + 90 * turns) % 360, crop=rotate_crop(g.crop, turns))
        elif action == "flip_h":
            x0, y0, x1, y1 = g.crop
            g = replace(g, flip_h=not g.flip_h, angle=-g.angle, crop=(1 - x1, y0, 1 - x0, y1))
        elif action == "flip_v":
            x0, y0, x1, y1 = g.crop
            g = replace(g, flip_v=not g.flip_v, angle=-g.angle, crop=(x0, 1 - y1, x1, 1 - y0))
        self._set(replace(self.stack, geometry=g))
        if self.cropping:
            self.canvas.set_crop(g.crop, self._aspect_value())

    # --- lens ---

    def _show_lens(self) -> None:
        if self.info is None:
            return
        from lunelis.edit import lens
        info = self.session.lens_info
        self.panel.show_lens(self.stack.lens, lens.profile_name(info), self.info.lens)

    def _lens_changed(self, key: str, value, final: bool) -> None:
        self._set(self.stack.with_lens(key, value), record=final, fast=not final)

    # --- masks ---

    def _mask(self):
        return self.stack.masks[self.mask_index] if 0 <= self.mask_index < len(self.stack.masks) else None

    def _replace_mask(self, m, *, record: bool = True, fast: bool = False) -> None:
        old = self._mask()
        masks = list(self.stack.masks)
        masks[self.mask_index] = m
        self._set(replace(self.stack, masks=tuple(masks)), record=record, fast=fast)
        # The overlay shows WHERE the mask is: its adjustments don't move it.
        if old is not None and (old.kind, old.shape, old.strokes, old.invert) == (m.kind, m.shape, m.strokes, m.invert):
            return
        if fast:
            self._overlay_timer.start()                 # dragging: a 640 px rebuild per move was too much
        else:
            self._overlay_timer.stop()
            self._update_overlay()

    def add_mask(self, kind: str) -> None:
        from lunelis.edit import masks as M
        if kind in ("subject", "sky") and not self._ai_ready(kind):
            return
        self.set_crop_mode(False)
        self.mask_index = len(self.stack.masks)
        self._set(replace(self.stack, masks=self.stack.masks + (M.default(kind),)))
        self.set_overlay(True)
        self._ensure_ai()

    def _ai_ready(self, kind: str) -> bool:
        """True when the model is installed; otherwise offer to download it
        (the mask is added once that's done)."""
        from lunelis.edit import ai
        if ai.available(kind):
            return True
        m = ai.MODELS[kind]
        answer = QMessageBox.question(
            self.panel, "Download a model",
            f"{kind.title()} masks need a free AI model, downloaded once "
            f"({m.size / 1e6:,.0f} MB from {m.source}). It runs only on this PC - "
            "your photos never leave it.\n\nDownload it now?")
        if answer != QMessageBox.StandardButton.Yes:
            return False
        from PySide6.QtWidgets import QProgressDialog
        self._dl_progress = QProgressDialog(f"Downloading the {kind} model…", "Cancel", 0, 100, self.panel)
        self._dl_progress.setWindowTitle("Download a model")
        self._dl_progress.setMinimumDuration(0)
        job = _AiJob(self._ai_signals, "download", kind)
        self._dl_progress.canceled.connect(job.cancel)
        QThreadPool.globalInstance().start(job)
        return False

    def _ai_downloaded(self, kind: str, ok: bool, message: str) -> None:
        if getattr(self, "_dl_progress", None) is not None:
            self._dl_progress.close()
            self._dl_progress = None
        if not ok:
            if message != "cancelled":
                QMessageBox.warning(self.panel, "Download a model", f"The download didn't work: {message}")
            return
        if self.info is not None:
            self.add_mask(kind)

    def _ensure_ai(self) -> None:
        """Make sure every AI mask in the stack has its map (cached, or
        computed now from the decoded photo, in the background)."""
        from lunelis.edit import ai
        if self.info is None:
            return
        for kind in ai.kinds_in(self.stack):
            if kind in self.ai_maps or kind in self._ai_busy:
                continue
            m = ai.cached(self.info.file_id, kind)
            if m is not None:
                self.ai_maps = {**self.ai_maps, kind: m}
                continue
            if self.session.base is None or not ai.available(kind):
                continue
            self._ai_busy.add(kind)
            self.panel.status.setText(f"Finding the {kind}…")
            QThreadPool.globalInstance().start(_AiJob(self._ai_signals, "compute", kind,
                                                      self.info.file_id, self.session.base))

    def _ai_computed(self, kind: str, file_id: int, mask) -> None:
        self._ai_busy.discard(kind)
        if self.info is None or file_id != self.info.file_id:
            return
        self.panel.status.setText("" if mask is not None else f"Couldn't find the {kind}.")
        if mask is not None:
            self.ai_maps = {**self.ai_maps, kind: mask}
            self._render()
            self._update_overlay()

    def select_mask(self, index: int) -> None:
        self.mask_index = index if 0 <= index < len(self.stack.masks) else -1
        if self.mask_index >= 0:
            self.set_crop_mode(False)
        self._show_panel()

    def delete_mask(self) -> None:
        if self._mask() is None:
            return
        masks = list(self.stack.masks)
        del masks[self.mask_index]
        self.mask_index = -1
        self._set(replace(self.stack, masks=tuple(masks)))

    def _mask_invert(self, on: bool) -> None:
        m = self._mask()
        if m is not None:
            self._replace_mask(replace(m, invert=on))

    def _mask_adjust(self, key: str, value: float, final: bool) -> None:
        m = self._mask()
        if m is not None:
            self._replace_mask(m.with_adjust(key, value), record=final, fast=not final)

    def _mask_feather(self, value: float, final: bool) -> None:
        m = self._mask()
        if m is not None and m.kind == "radial":
            self._replace_mask(replace(m, shape=m.shape[:4] + (value / 100,)), record=final, fast=not final)

    def _mask_shape(self, shape: tuple, final: bool) -> None:
        m = self._mask()
        if m is not None:
            self._replace_mask(replace(m, shape=tuple(round(v, 4) for v in shape)), record=final, fast=not final)

    def _mask_stroke(self, stroke: tuple) -> None:
        m = self._mask()
        if m is not None and m.kind == "brush":
            self._replace_mask(replace(m, strokes=m.strokes + (stroke,)))

    def _brush_changed(self) -> None:
        self.canvas.mask.brush, self.canvas.mask.erase = self.panel.brush_settings()
        self.canvas.update()

    def set_overlay(self, on: bool) -> None:
        self.canvas.mask.show_overlay = on
        if self.panel.mask_show.isChecked() != on:
            self.panel.mask_show.blockSignals(True)
            self.panel.mask_show.setChecked(on)
            self.panel.mask_show.blockSignals(False)
        self._update_overlay()

    def _sync_mask_tool(self) -> None:
        m = self._mask()
        tool = self.canvas.mask
        if m is None or self.cropping:
            tool.set(None)
            return
        tool.set(m.kind if m.kind in ("linear", "radial", "brush") else "ai", m.shape, self.stack.geometry.crop)
        self._brush_changed()
        self._update_overlay()

    def _update_overlay(self) -> None:
        tool = self.canvas.mask
        m = self._mask()
        if m is None or not tool.show_overlay or self.canvas.pix is None:
            tool.overlay = None
            self.canvas.update()
            return
        from lunelis.edit import masks as M
        pw, ph = self.canvas.pix.width(), self.canvas.pix.height()
        k = min(1.0, 640 / max(pw, ph))                # the overlay is soft: a small one is plenty
        w, h = max(1, round(pw * k)), max(1, round(ph * k))
        a = M.overlay(m, self.stack.geometry.crop, h, w, getattr(self, "ai_maps", None))
        rgba = np.zeros((h, w, 4), dtype=np.uint8)
        rgba[..., 0] = 230
        rgba[..., 1] = 40
        rgba[..., 2] = 40
        rgba[..., 3] = (np.clip(a, 0, 1) * 110).astype(np.uint8)
        tool.overlay = QImage(rgba.data, w, h, 4 * w, QImage.Format.Format_RGBA8888).copy()
        tool.crop = self.stack.geometry.crop
        self.canvas.update()

    def _curve(self, channel: str, points: tuple, final: bool) -> None:
        self._set(self.stack.with_curve(channel, points), record=final, fast=not final)

    def _filter(self, name) -> None:
        self._set(replace(self.stack, filter=name, amount=100))

    def _amount(self, value: float, final: bool) -> None:
        self._set(replace(self.stack, amount=int(value)), record=final, fast=not final)

    def _save_filter(self) -> None:
        look = store.flatten(self.conn, self.stack)
        if not look:
            QMessageBox.information(self.panel, "Save as filter", "Adjust a slider or pick a filter first.")
            return
        name, ok = QInputDialog.getText(self.panel, "Save as filter", "Name for this look:")
        if not ok or not name.strip():
            return
        try:
            store.save_filter(self.conn, name, look)
        except ValueError as e:
            QMessageBox.warning(self.panel, "Save as filter", str(e))
            return
        # Same look, now as the named filter (so its Amount slider works).
        self._set(Stack(name.strip(), 100, {}, self.stack.geometry))
        self._filter_previews()

    def _unpack(self) -> None:
        if self.stack.filter:
            self._set(Stack(None, 100, store.flatten(self.conn, self.stack), self.stack.geometry))

    def _delete_filter(self, name: str) -> None:
        users = store.users_of(self.conn, name)
        extra = (f"\n\n{len(users):,} photo{'s use' if len(users) != 1 else ' uses'} it - "
                 "they keep their look (its values move into their own sliders).") if users else ""
        if QMessageBox.question(self.panel, "Delete filter", f"Delete your filter '{name}'?{extra}") \
                != QMessageBox.StandardButton.Yes:
            return
        self.save()
        store.delete_filter(self.conn, name)
        self.stack = self.saved_stack = store.get(self.conn, self.info.file_id)
        self.history, self.pos = [self.stack], 0
        self._filter_previews()
        self._show_panel()

    def auto(self) -> None:
        if self.session.disp is None or self.info is None:
            return
        self.panel.status.setText("Working out Auto…")
        self.panel.auto_b.setEnabled(False)
        QThreadPool.globalInstance().start(
            _AutoJob(self._auto_signals, self.info.file_id, self.session.disp, self.stack.geometry))

    def _auto_done(self, file_id: int, adjust) -> None:
        self.panel.auto_b.setEnabled(True)
        if getattr(self, "closed", False) or self.info is None or file_id != self.info.file_id:
            return                                      # moved on to another photo meanwhile
        self.panel.status.setText("" if adjust is not None else "Auto couldn't work this photo out")
        if adjust is not None:
            self._set(replace(self.stack, adjust=adjust))

    def undo(self) -> None:
        if self.pos > 0:
            self.pos -= 1
            self._set(self.history[self.pos], record=False)
            self._show_panel()
            self._sync_crop_frame()
            self._save_timer.start()

    def redo(self) -> None:
        if self.pos < len(self.history) - 1:
            self.pos += 1
            self._set(self.history[self.pos], record=False)
            self._show_panel()
            self._sync_crop_frame()
            self._save_timer.start()

    def set_before(self, on: bool) -> None:
        if on == self.showing_before:
            return
        self.showing_before = on
        if self.panel.before_b.isChecked() != on:
            self.panel.before_b.blockSignals(True)
            self.panel.before_b.setChecked(on)
            self.panel.before_b.blockSignals(False)
        if on and self.session.disp is not None:
            disp = self.session.disp
            if self._before_pix is None or self._before_pix[0] is not disp:
                self._before_pix = (disp, QPixmap.fromImage(to_qimage(disp)))
            self.canvas.show_pixmap(self._before_pix[1], sharp=True)
        elif not on:
            self._render()

    # --- crop ---

    def _aspect_value(self) -> float | None:
        a = self._aspect
        if a == "orig" and self.session.disp is not None:
            src = pipeline.apply_geometry(self.session.fast, replace(self.stack.geometry, crop=FULL_CROP))
            return src.shape[1] / src.shape[0]
        return a if isinstance(a, float) else None

    def _sync_crop_frame(self) -> None:
        """Undo / redo while cropping: the frame follows the restored crop."""
        if self.cropping:
            self.canvas.set_crop(self.stack.geometry.crop, self._aspect_value())

    def set_crop_mode(self, on: bool) -> None:
        if on == self.cropping:
            return
        self.cropping = on
        if on:
            self.mask_index = -1
            self.canvas.mask.set(None)
            self.panel.show_masks(self.stack.masks, -1)
        if self.panel.crop_b.isChecked() != on:
            self.panel.crop_b.blockSignals(True)
            self.panel.crop_b.setChecked(on)
            self.panel.crop_b.blockSignals(False)
        self.canvas.set_crop(self.stack.geometry.crop if on else None, self._aspect_value() if on else None)
        self._render()

    def _aspect_changed(self, value) -> None:
        self._aspect = value
        if not self.cropping:
            self.set_crop_mode(True)
        aspect = self._aspect_value()
        self.canvas.aspect = aspect
        if aspect and self.canvas.pix is not None:
            crop = fit_aspect(self.stack.geometry.crop, aspect, self.canvas.pix.width() / self.canvas.pix.height())
            self.canvas.set_crop(crop, aspect)
            self._crop_moved(crop, True)

    def _crop_moved(self, crop: tuple, final: bool) -> None:
        if final:
            self._set(replace(self.stack, geometry=replace(self.stack.geometry, crop=tuple(round(c, 5) for c in crop))))


class _AutoSignals(QObject):
    done = Signal(int, object)             # file id, adjust dict | None


class _AutoJob(QRunnable):
    def __init__(self, signals: _AutoSignals, file_id: int, disp, geometry) -> None:
        super().__init__()
        self.s, self.file_id, self.disp, self.geometry = signals, file_id, disp, geometry

    def run(self) -> None:
        try:
            adjust = pipeline.auto(pipeline.apply_geometry(self.disp, self.geometry))
        except Exception:
            adjust = None
        self.s.done.emit(self.file_id, adjust)


class _OutSignals(QObject):
    done = Signal(int, bool)


class _AiSignals(QObject):
    downloaded = Signal(str, bool, str)    # kind, ok, message
    computed = Signal(str, int, object)    # kind, file id, mask | None
    progress = Signal(int, int)


class _AiJob(QRunnable):
    def __init__(self, signals: _AiSignals, action: str, kind: str, file_id: int = 0, source=None) -> None:
        super().__init__()
        self.s, self.action, self.kind, self.file_id, self.source = signals, action, kind, file_id, source
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        from lunelis.edit import ai
        if self.action == "download":
            try:
                ai.download(self.kind, lambda d, t: self.s.progress.emit(d, t), lambda: self._cancel)
                self.s.downloaded.emit(self.kind, True, "")
            except Exception as e:
                self.s.downloaded.emit(self.kind, False, str(e))
            return
        try:
            mask = ai.compute(self.kind, self.source)
            ai.store(self.file_id, self.kind, mask)
        except Exception:
            mask = None
        self.s.computed.emit(self.kind, self.file_id, mask)


class BatchOutputs(QObject):
    """Render proxies + thumbnails for many photos (pasted edits, reset,
    the new-import filter), one at a time on a worker thread."""

    progress = Signal(int, int)
    one = Signal(int)                      # file id whose thumbnail changed
    finished = Signal()

    def __init__(self, file_ids: list[int]) -> None:
        super().__init__()
        self.file_ids = file_ids
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        import os
        from lunelis import paths
        from lunelis.catalog.schema import open_catalog
        conn = open_catalog(paths.DEFAULT_CATALOG_PATH)
        try:
            for i, fid in enumerate(self.file_ids, 1):
                if self._cancel:
                    break
                row = conn.execute("SELECT r.path, f.rel_path, f.is_raw, e.orientation FROM files f"
                                   " JOIN roots r ON r.id = f.root_id LEFT JOIN exif e ON e.file_id = f.id"
                                   " WHERE f.id = ?", (fid,)).fetchone()
                if row:
                    path = os.path.join(row[0], *row[1].split("/"))
                    stack = store.get(conn, fid)
                    try:
                        if stack.is_identity():
                            render.clear_outputs(path, fid, row[3], paths.THUMBNAIL_CACHE, paths.EDIT_CACHE)
                        else:
                            render.render_outputs(path, bool(row[2]), fid, stack,
                                                  store.filter_params(conn, stack.filter),
                                                  paths.THUMBNAIL_CACHE, paths.EDIT_CACHE)
                        self.one.emit(fid)
                    except Exception:
                        pass                   # unreadable file: its thumbnail stays as it was
                self.progress.emit(i, len(self.file_ids))
        finally:
            conn.close()
            self.finished.emit()
