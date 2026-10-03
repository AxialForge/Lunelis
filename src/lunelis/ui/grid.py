"""
The library grid (Phase 1, Step 5).

A QAbstractScrollArea that paints only the rows in view. Item views
(QListView in icon mode) lay out every item up front, which at 160k photos
is seconds of work on each resize or sort; here layout is arithmetic:
row = i // cols, so any scroll position costs the same.

Tiles are square (cover-cropped), stretch to fill the width like the
mockup's CSS grid, and never block on the disk - see ui/thumbcache.py.
Selection is by file id, so it survives a re-sort.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QKeyEvent, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractScrollArea, QFrame, QLabel, QVBoxLayout

from lunelis.raw.thumbnails import cache_rel_path
from lunelis.ui.library import LibraryIndex
from lunelis.ui.thumbcache import ThumbCache
from lunelis.ui import theme as themes
from lunelis.ui.theme import label_color, qcolor

PAD_X, PAD_Y, GAP, RADIUS = 24, 20, 10, 8
MIN_TILE, MAX_TILE, DEFAULT_TILE = 100, 400, 180   # 180 = the mockup's 6 columns at 1440px
PX_STEP = 64                 # thumbnail decode size is quantised so a window resize doesn't reload
HOVER_DELAY_MS = 450         # still this long over a photo -> its info card
RESIZE_SETTLE_MS = 180       # while the size slider moves, tiles are scaled; decode once it stops


class HoverCard(QFrame):
    # The little info card over a photo the mouse rests on.
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("HoverCard")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(2)
        self.title = QLabel(objectName="HoverTitle")
        self.lines = QLabel(objectName="HoverLines")
        v.addWidget(self.title)
        v.addWidget(self.lines)
        self.hide()

    def set_info(self, info) -> None:
        self.title.setText(info.filename)
        rows = [info.when(), info.camera(), info.lens or "", info.exposure(),
                "  ·  ".join(x for x in (info.dimensions(), info.size_text(), info.length()) if x)]
        if info.stars:
            rows.append("★" * info.stars)
        if info.event:
            rows.append(f"Event: {info.event}")
        self.lines.setText("\n".join(r for r in rows if r))
        self.adjustSize()


class PhotoGrid(QAbstractScrollArea):
    selection_changed = Signal(int)       # number selected
    activated = Signal(int)               # file id (double-click / Enter)
    zoom = Signal(int)                    # Ctrl+wheel: +1 bigger / -1 smaller

    def __init__(self, thumbs: ThumbCache, parent=None) -> None:
        super().__init__(parent)
        self.index = LibraryIndex()
        self.thumbs = thumbs
        self.thumbs.ready.connect(self._on_thumb_ready)
        self.target_tile = DEFAULT_TILE
        self.cols = 1
        self.tile = DEFAULT_TILE
        self.selected: set[int] = set()   # file ids
        self.current = -1                 # position, for keyboard nav
        self.anchor = -1                  # position, for shift-range
        self._last_first_row = 0
        self.empty_text = ""
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFrameShape(QAbstractScrollArea.Shape.NoFrame)
        self.viewport().setAutoFillBackground(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._badge_font = QFont(self.font())
        self._badge_font.setPixelSize(9)
        self._badge_font.setBold(True)
        self._star_font = QFont(self.font())
        self._star_font.setPixelSize(10)
        self._msg_font = QFont(self.font())
        self._msg_font.setPixelSize(15)
        # Hover info (set by the window: file id -> photoinfo.PhotoInfo).
        self.info_provider = None
        self.hover_enabled = True
        self.card = HoverCard(self.viewport())
        self._hover_i = -1
        self._hover_timer = QTimer(self, singleShot=True, interval=HOVER_DELAY_MS, timeout=self._show_card)
        self.viewport().setMouseTracking(True)
        # Size changes: scale what's loaded now, decode at the new size when it settles.
        self._decode_timer = QTimer(self, singleShot=True, interval=RESIZE_SETTLE_MS,
                                    timeout=self._apply_decode_size)
        self._decode_px = 0
        # The timeline scrubber (date sorts), in the right margin.
        from lunelis.ui.timeline import WIDTH, TimelineScrubber
        self.scrubber = TimelineScrubber(self)
        self.scrubber.jump.connect(lambda i: self.scroll_to(i, top=True))
        self.scrubber.hovered.connect(self._scrub_bubble)
        self.scrubber.hide()
        self.bubble = QLabel(self, objectName="ScrubBubble")
        self.bubble.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.bubble.hide()
        self._scrubber_w = WIDTH

    # --- data ----------------------------------------------------------------

    def set_index(self, index: LibraryIndex) -> None:
        self.index = index
        if self.current >= len(index):
            self.current = self.anchor = -1
        self._hide_card()
        self._relayout()
        if self.scrubber.isVisible():
            self.scrubber.set_rows(index.rows)
            self._update_scrubber_view()

    def set_timeline(self, on: bool) -> None:
        # Date sorts get the scrubber instead of the scroll bar.
        self.scrubber.setVisible(on)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff if on
                                        else Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setViewportMargins(0, 0, self._scrubber_w if on else 0, 0)
        self._place_scrubber()
        if on:
            self.scrubber.set_rows(self.index.rows)
            self._update_scrubber_view()

    def _scrub_bubble(self, text: str, y: int) -> None:
        # The month under the mouse on the scrubber, floating over the photos.
        if not text:
            self.bubble.hide()
            return
        self.bubble.setText(text)
        self.bubble.adjustSize()
        self.bubble.move(self.scrubber.x() - self.bubble.width() - 6,
                         max(2, min(self.height() - self.bubble.height() - 2, y - self.bubble.height() // 2)))
        self.bubble.show()
        self.bubble.raise_()

    def _place_scrubber(self) -> None:
        self.scrubber.setGeometry(self.width() - self._scrubber_w, 0, self._scrubber_w, self.height())

    def _update_scrubber_view(self) -> None:
        n = max(1, len(self.index))
        first = self._first_visible()
        rows_on_screen = self.viewport().height() / max(1, self._row_h())
        self.scrubber.set_view(first / n, min(1.0, (first + rows_on_screen * self.cols) / n))

    def set_target_tile(self, px: int) -> None:
        self.target_tile = max(MIN_TILE, min(MAX_TILE, px))
        # Keep the top visible row roughly in place across the change.
        first = self._first_visible()
        self._relayout(decode_now=False)
        self.scroll_to(first, top=True)

    def zoom_by(self, steps: int) -> None:
        self.set_target_tile(self.target_tile + 20 * steps)

    def _apply_decode_size(self) -> None:
        self.thumbs.set_tile_size(self._decode_px)
        self.viewport().update()

    # --- layout --------------------------------------------------------------

    def _relayout(self, decode_now: bool = True) -> None:
        avail = max(1, self.viewport().width() - 2 * PAD_X)
        self.cols = max(1, (avail + GAP) // (self.target_tile + GAP))
        self.tile = max(MIN_TILE // 2, (avail - GAP * (self.cols - 1)) // self.cols)
        dpr = self.devicePixelRatioF()
        self._decode_px = int(-(-self.tile * dpr // PX_STEP) * PX_STEP)   # round up to the step
        if decode_now or not self.thumbs.px:
            self._decode_timer.stop()
            self.thumbs.set_tile_size(self._decode_px)
        elif self._decode_px != self.thumbs.px:
            self._decode_timer.start()                   # meanwhile tiles are drawn scaled
        rows = -(-len(self.index) // self.cols)
        content = PAD_Y * 2 + rows * self.tile + max(0, rows - 1) * GAP
        bar = self.verticalScrollBar()
        bar.setRange(0, max(0, content - self.viewport().height()))
        bar.setPageStep(self.viewport().height())
        bar.setSingleStep(max(20, (self.tile + GAP) // 3))
        self.viewport().update()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        first = self._first_visible()
        self._relayout(decode_now=False)
        self.scroll_to(first, top=True)
        self._place_scrubber()

    def _row_h(self) -> int:
        return self.tile + GAP

    def _tile_rect(self, i: int) -> QRect:
        r, c = divmod(i, self.cols)
        x = PAD_X + c * (self.tile + GAP)
        y = PAD_Y + r * self._row_h() - self.verticalScrollBar().value()
        return QRect(x, y, self.tile, self.tile)

    def _first_visible(self) -> int:
        row = max(0, (self.verticalScrollBar().value() - PAD_Y) // self._row_h())
        return row * self.cols

    def position_at(self, p: QPoint) -> int:
        y = p.y() + self.verticalScrollBar().value() - PAD_Y
        x = p.x() - PAD_X
        if x < 0 or y < 0:
            return -1
        row, ry = divmod(y, self._row_h())
        col, rx = divmod(x, self.tile + GAP)
        if ry >= self.tile or rx >= self.tile or col >= self.cols:
            return -1                     # in a gap
        i = row * self.cols + col
        return i if i < len(self.index) else -1

    def scroll_to(self, i: int, top: bool = False) -> None:
        if i < 0 or not len(self.index):
            return
        bar = self.verticalScrollBar()
        y = PAD_Y + (i // self.cols) * self._row_h()
        if top:
            bar.setValue(y - PAD_Y)
        elif y < bar.value():
            bar.setValue(y - GAP)
        elif y + self.tile > bar.value() + self.viewport().height():
            bar.setValue(y + self.tile - self.viewport().height() + GAP)

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        # A jump of more than a screen: whatever is still queued is for tiles
        # the user has already scrolled past.
        first_row = self._first_visible() // max(1, self.cols)
        rows_on_screen = self.viewport().height() // self._row_h() + 1
        if abs(first_row - self._last_first_row) > rows_on_screen:
            self.thumbs.forget_queue()
        self._last_first_row = first_row
        self._hide_card()
        if self.scrubber.isVisible():
            self._update_scrubber_view()
        self.viewport().update()

    # --- painting ------------------------------------------------------------

    def paintEvent(self, e) -> None:
        t = themes.current()
        p = QPainter(self.viewport())
        p.fillRect(self.viewport().rect(), qcolor(t.canvas))
        n = len(self.index)
        if not n:
            p.setPen(qcolor(t.text_faint))
            p.setFont(self._msg_font)
            p.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter, self.empty_text)
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        first = self._first_visible()
        rows_on_screen = self.viewport().height() // self._row_h() + 2
        last = min(n, first + rows_on_screen * self.cols)
        for i in range(first, last):
            self._paint_tile(p, i, self._tile_rect(i))
        p.end()

        # Prefetch a screen below and above so normal scrolling never shows placeholders.
        span = rows_on_screen * self.cols
        for i in list(range(last, min(n, last + span))) + list(range(max(0, first - span), first)):
            row = self.index.rows[i]
            if not row[2]:                                   # not marked unavailable
                self.thumbs.get(row[0], row[1] or cache_rel_path(row[0]))

    def _paint_tile(self, p: QPainter, i: int, r: QRect) -> None:
        t = themes.current()
        tile = self.index.tile(i)
        path = QPainterPath()
        path.addRoundedRect(QRectF(r), RADIUS, RADIUS)

        pix = None
        if not tile.unavailable:
            # The cache path is a pure function of the id, so a thumbnail a
            # background pass wrote after this index was loaded still shows.
            pix = self.thumbs.get(tile.file_id, tile.thumbnail_path or cache_rel_path(tile.file_id))
        if pix is not None:
            p.save()
            p.setClipPath(path)
            p.drawPixmap(r, pix)
            p.restore()
        else:
            broken = tile.unavailable or self.thumbs.failed(tile.file_id)
            p.fillPath(path, qcolor(t.tile_unavailable if broken else t.tile_placeholder))
            if broken and self.tile >= 120:
                p.setPen(qcolor(t.text_muted))
                p.setFont(self._star_font)
                label = "Video" if tile.is_video else (
                    "Preview unavailable" if tile.unavailable else "Preview pending")
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, label)

        if tile.flag == "reject":
            p.fillPath(path, qcolor(t.reject_veil))

        # Format badge, top-right; a collapsed stack's frame count beside it.
        if tile.badge:
            p.setFont(self._badge_font)
            fm = p.fontMetrics()
            w = fm.horizontalAdvance(tile.badge) + 10
            badge = QRect(r.right() - 6 - w, r.top() + 6, w, fm.height() + 4)
            self._pill(p, badge, tile.badge)
            left = badge.left()
            if tile.stack_size:
                text = f"❐ {tile.stack_size}"
                sw = fm.horizontalAdvance(text) + 12
                left -= 4 + sw
                self._pill(p, QRect(left, badge.top(), sw, badge.height()), text)
            if tile.edited:
                sw = fm.horizontalAdvance("EDITED") + 10
                self._pill(p, QRect(left - 4 - sw, badge.top(), sw, badge.height()), "EDITED")
        if tile.stack_size:
            # Two card edges peeking out in the gap below: "more under this one".
            p.setPen(Qt.PenStyle.NoPen)
            for k, colour in ((1, t.text_faint), (2, t.border)):
                p.setBrush(qcolor(colour))
                p.drawRoundedRect(QRectF(r.left() + 6 * k, r.bottom() + 1 + 3 * (k - 1), r.width() - 12 * k, 3), 1.5, 1.5)
        if tile.stack_open:
            # A frame of an expanded stack: an accent bar along the top.
            p.fillRect(QRect(r.left() + RADIUS, r.top(), r.width() - 2 * RADIUS, 3), qcolor(t.accent))

        # Rating, bottom-left (only when rated - an unrated photo shows nothing);
        # a video shows its length there instead.
        if tile.is_video and tile.duration:
            secs = int(round(tile.duration))
            text = f"▶ {secs // 3600}:{secs // 60 % 60:02d}:{secs % 60:02d}" if secs >= 3600 \
                else f"▶ {secs // 60}:{secs % 60:02d}"
            p.setFont(self._star_font)
            fm = p.fontMetrics()
            self._pill(p, QRect(r.left() + 6, r.bottom() - 6 - fm.height() - 4,
                                fm.horizontalAdvance(text) + 12, fm.height() + 4), text)
        elif tile.stars:
            p.setFont(self._star_font)
            text = "★" * tile.stars + "☆" * (5 - tile.stars)
            fm = p.fontMetrics()
            w = fm.horizontalAdvance(text) + 12
            self._pill(p, QRect(r.left() + 6, r.bottom() - 6 - fm.height() - 4, w, fm.height() + 4), text)

        # Flag, bottom-right; colour label as a dot beside it. The reject veil
        # above plus an explicit badge means rejects never rely on colour alone.
        x_right = r.right() - 6
        if tile.flag:
            p.setFont(self._badge_font)
            text = "REJECT" if tile.flag == "reject" else "PICK"
            fm = p.fontMetrics()
            w = fm.horizontalAdvance(text) + 10
            h = fm.height() + 4
            self._pill(p, QRect(x_right - w, r.bottom() - 6 - h, w, h), text)
            x_right -= w + 4
        colour = label_color(tile.label)
        if colour:
            d = 12
            p.setPen(QPen(qcolor(themes.current().badge_text), 1.5))
            p.setBrush(qcolor(colour))
            p.drawEllipse(QRect(x_right - d, r.bottom() - 6 - d - 2, d, d))

        if tile.file_id in self.selected:
            p.setPen(QPen(qcolor(t.selection), 3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(r).adjusted(1.5, 1.5, -1.5, -1.5), RADIUS, RADIUS)
            dot = QRect(r.left() + 6, r.top() + 6, 18, 18)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(qcolor(t.selection))
            p.drawEllipse(dot)
            p.setPen(QPen(qcolor(t.badge_text), 2))
            p.drawPolyline([QPoint(dot.left() + 5, dot.top() + 9), QPoint(dot.left() + 8, dot.top() + 12),
                            QPoint(dot.left() + 13, dot.top() + 6)])
        if i == self.current and self.hasFocus() and tile.file_id not in self.selected:
            p.setPen(QPen(qcolor(t.selection), 1, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(r).adjusted(0.5, 0.5, -0.5, -0.5), RADIUS, RADIUS)

    def _pill(self, p: QPainter, rect: QRect, text: str) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcolor(themes.current().badge_bg))
        p.drawRoundedRect(QRectF(rect), 4, 4)
        p.setPen(qcolor(themes.current().badge_text))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def _on_thumb_ready(self, file_id: int) -> None:
        self.viewport().update()          # cheap: paint only walks visible rows

    # --- selection & input ---------------------------------------------------

    def _set_current(self, i: int, mods: Qt.KeyboardModifier) -> None:
        if i < 0 or not len(self.index):
            return
        fid = self.index.file_id(i)
        if mods & Qt.KeyboardModifier.ShiftModifier and self.anchor >= 0:
            lo, hi = sorted((self.anchor, i))
            if not mods & Qt.KeyboardModifier.ControlModifier:
                self.selected.clear()
            self.selected.update(self.index.rows[j][0] for j in range(lo, hi + 1))
        elif mods & Qt.KeyboardModifier.ControlModifier:
            self.selected.symmetric_difference_update({fid})
            self.anchor = i
        else:
            self.selected = {fid}
            self.anchor = i
        self.current = i
        self.scroll_to(i)
        self.viewport().update()
        self.selection_changed.emit(len(self.selected))

    def clear_selection(self) -> None:
        self.selected.clear()
        self.viewport().update()
        self.selection_changed.emit(0)

    # --- hover card ------------------------------------------------------------

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        i = self.position_at(e.position().toPoint())
        if i != self._hover_i:
            self._hide_card()
            self._hover_i = i
            if i >= 0 and self.hover_enabled and self.info_provider is not None:
                self._hover_timer.start()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e) -> None:
        self._hover_i = -1
        self._hide_card()
        super().leaveEvent(e)

    def _hide_card(self) -> None:
        self._hover_timer.stop()
        self.card.hide()

    def _show_card(self) -> None:
        i = self._hover_i
        if i < 0 or i >= len(self.index) or self.info_provider is None:
            return
        info = self.info_provider(self.index.file_id(i))
        if info is None:
            return
        self.card.set_info(info)
        r = self._tile_rect(i)
        vw, vh = self.viewport().width(), self.viewport().height()
        x = r.right() + 8 if r.right() + 8 + self.card.width() < vw else r.left() - 8 - self.card.width()
        y = max(4, min(vh - self.card.height() - 4, r.top()))
        self.card.move(max(4, x), y)
        self.card.show()
        self.card.raise_()

    def wheelEvent(self, e) -> None:
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            dy = e.angleDelta().y()
            if dy:
                self.zoom.emit(1 if dy > 0 else -1)      # the window moves the size slider
            e.accept()
            return
        self._hide_card()
        super().wheelEvent(e)

    def mousePressEvent(self, e: QMouseEvent) -> None:
        self._hide_card()
        if e.button() == Qt.MouseButton.RightButton:
            # The Photo menu acts on the selection: a right-click on a photo that
            # isn't selected selects it first (as in Explorer and Lightroom).
            i = self.position_at(e.position().toPoint())
            if i >= 0 and self.index.file_id(i) not in self.selected:
                self._set_current(i, Qt.KeyboardModifier.NoModifier)
        if e.button() == Qt.MouseButton.LeftButton:
            i = self.position_at(e.position().toPoint())
            if i < 0:
                if not e.modifiers():
                    self.clear_selection()
            else:
                self._set_current(i, e.modifiers())
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:
        i = self.position_at(e.position().toPoint())
        if i >= 0:
            self.activated.emit(self.index.file_id(i))

    def keyPressEvent(self, e: QKeyEvent) -> None:
        n = len(self.index)
        if not n:
            return super().keyPressEvent(e)
        k, mods = e.key(), e.modifiers()
        cur = max(self.current, 0)
        rows_on_screen = max(1, self.viewport().height() // self._row_h())
        moves = {
            Qt.Key.Key_Left: cur - 1,
            Qt.Key.Key_Right: cur + 1,
            Qt.Key.Key_Up: cur - self.cols,
            Qt.Key.Key_Down: cur + self.cols,
            Qt.Key.Key_PageUp: cur - rows_on_screen * self.cols,
            Qt.Key.Key_PageDown: cur + rows_on_screen * self.cols,
            Qt.Key.Key_Home: 0,
            Qt.Key.Key_End: n - 1,
        }
        if k in moves:
            target = moves[k] if self.current >= 0 else 0
            self._set_current(max(0, min(n - 1, target)), mods)
        elif k == Qt.Key.Key_A and mods & Qt.KeyboardModifier.ControlModifier:
            self.selected = {r[0] for r in self.index.rows}
            self.viewport().update()
            self.selection_changed.emit(len(self.selected))
        elif k == Qt.Key.Key_Escape:
            self.clear_selection()
        elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.current >= 0:
            self.activated.emit(self.index.file_id(self.current))
        else:
            super().keyPressEvent(e)

    def focusInEvent(self, e) -> None:
        super().focusInEvent(e)
        self.viewport().update()

    def focusOutEvent(self, e) -> None:
        super().focusOutEvent(e)
        self.viewport().update()
