"""
Culling: going through a shoot full screen, from the keyboard.

    ← →        previous / next photo (in compare: the group moves along)
    P X U      pick, reject, unflag            0-5   stars      6-9  colour label
    A          auto-advance on / off (on: a pick, reject or rating moves on)
    C          compare: this photo and the next (C again: 3, then 4, then back to 1)
    Tab        in compare: the next photo of the group is the one the keys act on
    Z / wheel  zoom; drag to pan - every photo in the compare moves together
    Esc        back to the library

The photos are the library's selection when more than one is selected,
otherwise everything the library shows. Ratings go through the window's own
rating (`rate_ids`): sidecars, undo and RAW+JPEG pairs behave as they do
everywhere else.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QKeyEvent, QPixmap
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from lunelis.ui.background import unless_closed
from lunelis import paths
from lunelis.raw.thumbnails import cache_rel_path
from lunelis.ui import photoinfo
from lunelis.ui.detail_view import PhotoCanvas, PreviewCache

LABEL_KEYS = {Qt.Key.Key_6: "Red", Qt.Key.Key_7: "Yellow", Qt.Key.Key_8: "Green", Qt.Key.Key_9: "Blue"}
STAR_KEYS = {getattr(Qt.Key, f"Key_{n}"): n for n in range(6)}


class SyncCanvas(PhotoCanvas):
    """A PhotoCanvas that says when you zoom or pan it, so the others can follow."""

    moved = Signal(object)            # self
    clicked = Signal(object)
    marked = False                    # the photo the keys act on, in a compare

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        if self.marked:
            from PySide6.QtGui import QPainter, QPen
            from lunelis.ui import theme
            p = QPainter(self)
            p.setPen(QPen(theme.qcolor(theme.current().accent), 4))
            p.drawRect(self.rect().adjusted(2, 2, -2, -2))
            p.end()

    def wheelEvent(self, e) -> None:
        super().wheelEvent(e)
        self.moved.emit(self)

    def mouseMoveEvent(self, e) -> None:
        super().mouseMoveEvent(e)
        if self._pan is not None:
            self.moved.emit(self)

    def mouseDoubleClickEvent(self, e) -> None:
        super().mouseDoubleClickEvent(e)
        self.moved.emit(self)

    def mousePressEvent(self, e) -> None:
        super().mousePressEvent(e)
        self.clicked.emit(self)

    def follow(self, other: "SyncCanvas") -> None:
        """Take `other`'s zoom (relative to fit) and position."""
        if (self.pix is None and self.full_size is None) or (other.pix is None and other.full_size is None):
            return                                   # nothing on screen yet to measure against
        if other.scale is None:
            self.scale = None
        else:
            self.scale = self.fit_scale() * (other.scale / max(other.fit_scale(), 1e-9))
        self.center = QPointF(other.center)
        self._clamp()
        self.update()


class CullView(QWidget):
    closed = Signal()

    def __init__(self, conn, ids: list[int], rate_ids, parent=None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.conn, self.ids, self.rate_ids = conn, list(ids), rate_ids
        self.setWindowTitle("Lunelis - culling")
        self.setObjectName("Cull")
        self.pos = 0
        self.group = 1                     # photos side by side (1-4)
        self.active = 0                    # which of the group the keys act on
        self.auto_advance = True
        self.previews = PreviewCache(self)
        self.previews.ready.connect(self._preview_ready)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.area = QWidget()
        self.grid = QGridLayout(self.area)
        self.grid.setContentsMargins(4, 4, 4, 4)
        self.grid.setSpacing(4)
        v.addWidget(self.area, 1)
        bar = QWidget(objectName="CullBar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 8, 16, 8)
        self.info = QLabel(objectName="CullInfo")
        h.addWidget(self.info, 1)
        self.hint = QLabel("← → move · P pick · X reject · U unflag · 0-5 stars · C compare · A auto-advance · "
                           "Ctrl+Z undo · ? keys · Esc done",
                           objectName="Help")
        h.addWidget(self.hint)
        v.addWidget(bar)
        self.canvases: list[SyncCanvas] = []
        self._layout()
        self._show()

    # --- layout -------------------------------------------------------------------------

    def _layout(self) -> None:
        for c in self.canvases:
            self.grid.removeWidget(c)
            c.deleteLater()
        self.canvases = []
        cols = 1 if self.group == 1 else 2
        for i in range(self.group):
            c = SyncCanvas()
            c.moved.connect(self._sync)
            c.clicked.connect(lambda cv: self._activate(self.canvases.index(cv)))
            self.grid.addWidget(c, i // cols, i % cols)
            self.canvases.append(c)
        self.active = min(self.active, self.group - 1)

    def _sync(self, source: SyncCanvas) -> None:
        for c in self.canvases:
            if c is not source:
                c.follow(source)

    def _activate(self, i: int) -> None:
        self.active = i
        self._show()

    # --- photos ---------------------------------------------------------------------------

    def shown(self) -> list[int]:
        return self.ids[self.pos:self.pos + self.group]

    def current(self) -> int | None:
        s = self.shown()
        return s[min(self.active, len(s) - 1)] if s else None

    def _show(self) -> None:
        shown = self.shown()
        # Previews still queued for photos skipped past are taken back, as the
        # photo view does (0.54: after holding an arrow key, the photo you
        # stopped on waited behind every one before it).
        self.previews.keep_only(shown)
        for n, c in enumerate(self.canvases):
            if n >= len(shown):
                c.show_pixmap(None, False)
                continue
            fid = shown[n]
            info = photoinfo.load(self.conn, fid)
            if info is None:
                continue
            if getattr(c, "_fid", None) != fid:
                c._fid = fid
                c.set_photo((info.width, info.height) if info.width and info.height else None)
            pix = self.previews.get(info, self._edit_of(info)) if not info.is_video else None
            if pix is not None:
                c.show_pixmap(pix, True, keep_zoom=True)
            else:
                thumb = QPixmap(str(paths.THUMBNAIL_CACHE / cache_rel_path(fid)))
                c.show_pixmap(None if thumb.isNull() else thumb, False, keep_zoom=True)
            c.marked = n == self.active and self.group > 1
            c.update()
        cur = self.current()
        info = photoinfo.load(self.conn, cur) if cur is not None else None
        if info is None:
            self.info.setText("No photos")
            return
        flag = {"pick": "Picked", "reject": "Rejected"}.get(info.flag or "", "")
        bits = [f"{self.pos + min(self.active, len(shown) - 1) + 1:,} of {len(self.ids):,}", info.filename,
                "★" * info.stars if info.stars else "no stars", flag, info.label or "",
                "auto-advance on" if self.auto_advance else "auto-advance off"]
        self.info.setText("   ·   ".join(b for b in bits if b))

    def _edit_of(self, info):
        """An edited photo is shown as edited (the same as the photo view)."""
        from lunelis.edit import store
        stack = store.get(self.conn, info.file_id)
        if stack.is_identity():
            return None
        return info.is_raw, stack, store.filter_params(self.conn, stack.filter)

    def step(self, d: int) -> None:
        n = len(self.ids)
        if not n:
            return
        self.pos = max(0, min(n - 1, self.pos + d))
        self.active = 0 if d > 0 else min(self.active, self.group - 1)
        self._show()

    def _rated(self) -> None:
        if self.auto_advance:
            if self.group > 1 and self.active < min(self.group, len(self.shown())) - 1:
                self.active += 1                 # the next of the group first
                self._show()
            else:
                self.step(self.group)
        else:
            self._show()

    def rate(self, **change) -> None:
        fid = self.current()
        if fid is None:
            return
        self.rate_ids([fid], **change)
        self._rated()

    # --- keys ---------------------------------------------------------------------------------

    @unless_closed
    def _preview_ready(self, _fid: int) -> None:
        self._show()                              # not after the window (or the catalog) has closed

    def keyPressEvent(self, e: QKeyEvent) -> None:
        k = e.key()
        ctrl = bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier)
        window = getattr(self.rate_ids, "__self__", None)          # the main window: its undo history
        if ctrl and k in (Qt.Key.Key_Z, Qt.Key.Key_Y) and window is not None:
            redo = k == Qt.Key.Key_Y or bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            window.redo() if redo else window.undo()
            self._show()
            return
        if k == Qt.Key.Key_Question and window is not None:
            from lunelis.ui.shortcuts import ShortcutSheet
            ShortcutSheet(window, "Culling").exec()
            return
        if k == Qt.Key.Key_Escape:
            self.close()
        elif k in (Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_PageDown):
            self.step(self.group)
        elif k in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_PageUp):
            self.step(-self.group)
        elif k == Qt.Key.Key_P:
            self.rate(flag="pick")
        elif k == Qt.Key.Key_X:
            self.rate(flag="reject")
        elif k == Qt.Key.Key_U:
            self.rate(flag=None)
        elif k in STAR_KEYS:
            self.rate(stars=STAR_KEYS[k])
        elif k in LABEL_KEYS:
            self.rate(label=LABEL_KEYS[k])
        elif k == Qt.Key.Key_A:
            self.auto_advance = not self.auto_advance
            self._show()
        elif k == Qt.Key.Key_C:
            self.group = {1: 2, 2: 3, 3: 4, 4: 1}[self.group]
            self._layout()
            self._show()
        elif k == Qt.Key.Key_Tab and self.group > 1:
            self.active = (self.active + 1) % min(self.group, max(1, len(self.shown())))
            self._show()
        elif k == Qt.Key.Key_Z:
            c = self.canvases[min(self.active, len(self.canvases) - 1)]
            c.toggle_zoom()
            self._sync(c)
        else:
            super().keyPressEvent(e)

    def focusNextPrevChild(self, nxt: bool) -> bool:
        return False                             # Tab is ours

    def closeEvent(self, e) -> None:
        self.closed.emit()
        super().closeEvent(e)
