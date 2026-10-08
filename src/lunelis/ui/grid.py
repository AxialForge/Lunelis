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

from PySide6.QtCore import QMimeData, QPoint, QRect, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDrag, QFont, QKeyEvent, QMouseEvent, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QAbstractScrollArea, QFrame, QLabel, QScrollBar, QVBoxLayout

from lunelis.raw.thumbnails import cache_rel_path
from lunelis.ui.library import ROOT, LibraryIndex
from lunelis.ui.thumbcache import ThumbCache
from lunelis.ui import theme as themes
from lunelis.ui.theme import label_color, qcolor

PAD_X, PAD_Y, GAP, RADIUS = 24, 20, 10, 8
MIN_TILE, MAX_TILE, DEFAULT_TILE = 100, 400, 180   # 180 = the mockup's 6 columns at 1440px
PX_STEP = 64                 # thumbnail decode size is quantised so a window resize doesn't reload
HOVER_DELAY_MS = 450         # still this long over a photo -> its info card
GIF_HOVER_MS = 250           # still this long over an animated GIF -> it plays in its tile
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


class WideOnUseBar(QScrollBar):
    """The Library's scrollbar: thin at rest, 20 px wide while the mouse is on
    it or dragging it, so it's easy to grab on a long library (0.45)."""

    def __init__(self, parent=None) -> None:
        super().__init__(Qt.Orientation.Vertical, parent)
        self.setObjectName("GridScroll")
        self._over = self._held = False

    def _restyle(self) -> None:
        on = self._over or self._held
        if self.property("active") != on:
            self.setProperty("active", on)
            self.style().unpolish(self)
            self.style().polish(self)
            self.updateGeometry()

    def enterEvent(self, e) -> None:
        self._over = True
        self._restyle()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        self._over = False
        self._restyle()
        super().leaveEvent(e)

    def mousePressEvent(self, e) -> None:
        self._held = True
        self._restyle()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        self._held = False
        self._restyle()
        super().mouseReleaseEvent(e)


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
        self.setVerticalScrollBar(WideOnUseBar(self))
        self.viewport().setAutoFillBackground(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # In points, grown with Windows' text size (theme.font_pt); the badges
        # were 9 px - too small to read at 100 %.
        from lunelis.ui.theme import font_pt
        self._badge_font = QFont(self.font())
        self._badge_font.setPointSizeF(font_pt(10.5))
        self._badge_font.setBold(True)
        self._star_font = QFont(self.font())
        self._star_font.setPointSizeF(font_pt(11))
        self._msg_font = QFont(self.font())
        self._msg_font.setPointSizeF(font_pt(15))
        # Hover info (set by the window: file id -> photoinfo.PhotoInfo).
        self.info_provider = None
        self.hover_enabled = True
        self.card = HoverCard(self.viewport())
        self._hover_i = -1
        self._hover_timer = QTimer(self, singleShot=True, interval=HOVER_DELAY_MS, timeout=self._show_card)
        # Sources that aren't answering right now (set by the window): their tiles say so.
        self.offline_roots: set[int] = set()
        # An animated GIF under the pointer plays in its tile (one at a time).
        self._anim_i = -1
        self._anim = None
        self._anim_timer = QTimer(self, singleShot=True, interval=GIF_HOVER_MS, timeout=self._start_anim)
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
        if i == self._anim_i and self._anim is not None and not self._anim.currentPixmap().isNull():
            pix = None
            frame = self._anim.currentPixmap()
            # Cover the tile like the thumbnail does: the middle square of the frame.
            side = min(frame.width(), frame.height())
            src = QRect((frame.width() - side) // 2, (frame.height() - side) // 2, side, side)
            p.save()
            p.setClipPath(path)
            p.drawPixmap(r, frame, src)
            p.restore()
        elif pix is not None:
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
        if self.offline_roots and self.index.rows[i][ROOT] in self.offline_roots:
            # Top-left: the photo's drive or NAS isn't answering - the thumbnail
            # still shows, and nothing is marked missing.
            p.setFont(self._badge_font)
            fm = p.fontMetrics()
            self._pill(p, QRect(r.left() + 6, r.top() + 6, fm.horizontalAdvance("OFFLINE") + 10, fm.height() + 4),
                       "OFFLINE")

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
        if getattr(self, "_band", None) is not None and self._band.isVisible()                 and e.buttons() & Qt.MouseButton.LeftButton:
            self._band_move(e.position().toPoint())
            return
        if self._maybe_drag(e):
            return
        i = self.position_at(e.position().toPoint())
        if i != self._hover_i:
            self._hide_card()
            self._stop_anim()
            self._hover_i = i
            if i >= 0 and self.hover_enabled and self.info_provider is not None:
                self._hover_timer.start()
            if i >= 0 and self.info_provider is not None and self.index.tile(i).badge == "GIF":
                self._anim_timer.start()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e) -> None:
        self._hover_i = -1
        self._hide_card()
        self._stop_anim()
        super().leaveEvent(e)

    # --- animated GIFs play on hover --------------------------------------------

    def _start_anim(self) -> None:
        i = self._hover_i
        if i < 0 or i >= len(self.index) or self.info_provider is None:
            return
        info = self.info_provider(self.index.file_id(i))
        if info is None or not info.path:
            return
        from PySide6.QtGui import QMovie
        movie = QMovie(info.path)
        if not movie.isValid() or movie.frameCount() == 1:
            return
        movie.setCacheMode(QMovie.CacheMode.CacheAll)
        movie.frameChanged.connect(lambda _n, i=i: self.viewport().update(self._tile_rect(i)))
        self._anim_i, self._anim = i, movie
        movie.start()

    def _stop_anim(self) -> None:
        self._anim_timer.stop()
        if self._anim is not None:
            i = self._anim_i
            self._anim.stop()
            self._anim.deleteLater()
            self._anim, self._anim_i = None, -1
            if 0 <= i < len(self.index):
                self.viewport().update(self._tile_rect(i))

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
            self._press = (e.position().toPoint(), i)
            self._pending_select = None
            if i < 0 or e.modifiers() & Qt.KeyboardModifier.AltModifier:
                # On empty space (or with Alt anywhere): drag a box to select (0.45).
                if not e.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    self.clear_selection()
                self._band_start(e.position().toPoint())
                self._press = None
                return
            elif not e.modifiers() and self.index.file_id(i) in self.selected and len(self.selected) > 1:
                # Pressing on a selection may start a drag of all of it: only a
                # click that doesn't become a drag narrows it to this photo.
                self._pending_select = i
                self.current = i
                self.viewport().update()
            else:
                self._set_current(i, e.modifiers())
        super().mousePressEvent(e)

    # --- selecting many ------------------------------------------------------

    def _band_start(self, p) -> None:
        from PySide6.QtWidgets import QRubberBand
        if getattr(self, "_band", None) is None:
            self._band = QRubberBand(QRubberBand.Shape.Rectangle, self.viewport())
        self._band_origin = p
        self._band_y0 = p.y() + self.verticalScrollBar().value()      # in content coordinates
        self._band_base = set(self.selected)
        self._band.setGeometry(QRect(p, p))
        self._band.show()

    def _band_move(self, p) -> None:
        y0 = self._band_y0 - self.verticalScrollBar().value()
        rect = QRect(QPoint(self._band_origin.x(), y0), p).normalized()
        self._band.setGeometry(rect.intersected(self.viewport().rect()))
        top = min(self._band_y0, p.y() + self.verticalScrollBar().value())
        bottom = max(self._band_y0, p.y() + self.verticalScrollBar().value())
        left, right = rect.left(), rect.right()
        hit = set()
        for i in range(len(self.index)):
            r = self._tile_rect(i)
            ry = r.top() + self.verticalScrollBar().value()
            if ry > bottom:
                break
            if ry + r.height() >= top and r.right() >= left and r.left() <= right:
                hit.add(self.index.file_id(i))
        self.selected = self._band_base | hit
        self.viewport().update()
        self.selection_changed.emit(len(self.selected))

    def select_where(self, keep) -> int:
        """Select every photo in view whose row passes keep(row); returns how many."""
        self.selected = {r[0] for r in self.index.rows if keep(r)}
        self.viewport().update()
        self.selection_changed.emit(len(self.selected))
        return len(self.selected)

    def invert_selection(self) -> int:
        return self.select_where(lambda r, s=set(self.selected): r[0] not in s)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if getattr(self, "_band", None) is not None and self._band.isVisible():
            self._band.hide()
        if e.button() == Qt.MouseButton.LeftButton and getattr(self, "_pending_select", None) is not None:
            self._set_current(self._pending_select, Qt.KeyboardModifier.NoModifier)
        self._pending_select = None
        self._press = None
        super().mouseReleaseEvent(e)

    # --- drag out ------------------------------------------------------------

    DRAG_MIME = "application/x-lunelis-files"

    def _maybe_drag(self, e: QMouseEvent) -> bool:
        press = getattr(self, "_press", None)
        if not press or press[1] < 0 or not (e.buttons() & Qt.MouseButton.LeftButton):
            return False
        from PySide6.QtWidgets import QApplication
        if (e.position().toPoint() - press[0]).manhattanLength() < QApplication.startDragDistance():
            return False
        self._press = None
        self._pending_select = None
        self.start_drag(press[1])
        return True

    def drag_ids(self, i: int) -> list[int]:
        """What dragging tile i takes: the selection if it's in it, else just it."""
        fid = self.index.file_id(i)
        if fid in self.selected:
            return [self.index.file_id(j) for j in range(len(self.index)) if self.index.file_id(j) in self.selected]
        return [fid]

    def drag_mime(self, ids: list[int]) -> QMimeData:
        """Photos as files (Explorer copies them) and as Lunelis ids (albums)."""
        import json
        mime = QMimeData()
        paths = self.paths_provider(ids) if getattr(self, "paths_provider", None) else []
        mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
        mime.setData(self.DRAG_MIME, json.dumps(ids).encode("ascii"))
        return mime

    def start_drag(self, i: int) -> None:
        ids = self.drag_ids(i)
        drag = QDrag(self)
        drag.setMimeData(self.drag_mime(ids))
        rect = self._tile_rect(i)
        pix = QPixmap(rect.size())
        pix.fill(Qt.GlobalColor.transparent)
        self.viewport().render(pix, QPoint(), rect)
        pix = pix.scaled(96, 96, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        if len(ids) > 1:
            p = QPainter(pix)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setBrush(Qt.GlobalColor.darkBlue)
            p.setPen(Qt.GlobalColor.white)
            p.drawRoundedRect(QRect(pix.width() - 40, 4, 36, 20), 9, 9)
            p.drawText(QRect(pix.width() - 40, 4, 36, 20), Qt.AlignmentFlag.AlignCenter, f"{len(ids)}")
            p.end()
        drag.setPixmap(pix)
        drag.setHotSpot(QPoint(pix.width() // 2, pix.height() // 2))
        drag.exec(Qt.DropAction.CopyAction)

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
        elif k == Qt.Key.Key_Space and self.current >= 0:
            # Space: this photo in or out of the selection, the rest kept (as Ctrl+click).
            fid = self.index.file_id(self.current)
            self.selected ^= {fid}
            self.viewport().update()
            self.selection_changed.emit(len(self.selected))
        elif (k == Qt.Key.Key_Menu or (k == Qt.Key.Key_F10 and mods & Qt.KeyboardModifier.ShiftModifier)) \
                and self.current >= 0:
            # The keyboard's menu key: the Photo menu at the photo, not wherever the pointer is.
            fid = self.index.file_id(self.current)
            if fid not in self.selected:
                self._set_current(self.current, Qt.KeyboardModifier.NoModifier)
            self.customContextMenuRequested.emit(self._tile_rect(self.current).center())
        else:
            super().keyPressEvent(e)

    def focusInEvent(self, e) -> None:
        super().focusInEvent(e)
        self.viewport().update()

    def focusOutEvent(self, e) -> None:
        super().focusOutEvent(e)
        self.viewport().update()
