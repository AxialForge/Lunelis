"""
Scene suggestions to review (Tags page > Suggestions): what the scene model
thinks is in your photos, a tag at a time. Tick and Accept (they become your
tags, and go to sidecars), Reject (gone, and never suggested again for those
photos), or accept everything above a confidence you trust.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QSlider, QSplitter,
    QStyle, QStyledItemDelegate, QVBoxLayout, QWidget,
)
from lunelis.ui.round_slider import RoundSlider  # noqa: E402  true circles (0.52)

from lunelis import paths
from lunelis.raw.thumbnails import cache_rel_path
from lunelis.recognize import scenes
from lunelis.ui.background import Background

THUMB = 120
TICK = 18                               # the tick box drawn inside a thumbnail's corner
PAD = 4                                 # room around a thumbnail in its cell


class TickThumb(QStyledItemDelegate):
    """A thumbnail with its tick box INSIDE the picture's corner and, with
    `caption`, the item's text directly under the picture. Qt's own icon-mode
    drawing hangs the box off the thumbnail's left edge, where the next
    thumbnail clips it, and floats the text at the bottom of the cell (0.55).

    `click_ticks`: a click on the box flips it here. Leave it off when the list
    already flips the item on any click (a second flip would undo the first)."""

    def __init__(self, parent, caption: bool = False, click_ticks: bool = True) -> None:
        super().__init__(parent)
        self.caption, self.click_ticks = caption, click_ticks

    def _caption_height(self, option) -> int:
        return option.fontMetrics.height() + 4 if self.caption else 0

    def picture_rect(self, option, index) -> QRect:
        """Where the thumbnail is drawn inside its cell."""
        area = option.rect.adjusted(PAD, PAD, -PAD, -PAD - self._caption_height(option))
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        size = icon.actualSize(area.size()) if isinstance(icon, QIcon) and not icon.isNull() else area.size()
        return QRect(area.x() + (area.width() - size.width()) // 2,
                     area.y() + (area.height() - size.height()) // 2, size.width(), size.height())

    def tick_rect(self, option, index) -> QRect:
        pic = self.picture_rect(option, index)
        return QRect(pic.x() + 4, pic.y() + 4, TICK, TICK)

    def paint(self, p: QPainter, option, index) -> None:
        from lunelis.ui import theme
        t = theme.current()
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pic = self.picture_rect(option, index)
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(icon, QIcon) and not icon.isNull():
            icon.paint(p, pic)
        else:
            p.fillRect(pic, theme.qcolor(t.tile_placeholder))        # its thumbnail hasn't been read yet
        if option.state & QStyle.StateFlag.State_Selected:
            p.setPen(QPen(theme.qcolor(t.accent), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(pic).adjusted(1, 1, -1, -1))
        state = index.data(Qt.ItemDataRole.CheckStateRole)
        if state is not None:
            on = Qt.CheckState(state) == Qt.CheckState.Checked
            box = QRectF(self.tick_rect(option, index)).adjusted(0.75, 0.75, -0.75, -0.75)
            p.setPen(QPen(theme.qcolor(t.chip_bg if on else t.text_muted), 1.5))
            p.setBrush(theme.qcolor(t.chip_bg if on else t.surface))
            p.drawRoundedRect(box, 4, 4)
            if on:
                path = QPainterPath(QPointF(box.x() + 4, box.y() + box.height() * 0.52))
                path.lineTo(box.x() + box.width() * 0.42, box.bottom() - 4.5)
                path.lineTo(box.right() - 3.5, box.y() + 4.5)
                p.setPen(QPen(theme.qcolor(t.chip_text), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                              Qt.PenJoinStyle.RoundJoin))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(path)
        if self.caption:
            p.setPen(theme.qcolor(t.text_muted))
            p.drawText(QRect(option.rect.x(), pic.bottom() + 3, option.rect.width(), option.fontMetrics.height()),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                       str(index.data(Qt.ItemDataRole.DisplayRole) or ""))
        p.restore()

    def editorEvent(self, event, model, option, index) -> bool:
        # The box is where WE drew it, not where the style would have put it.
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
                            QEvent.Type.MouseButtonDblClick):
            if not self.click_ticks or index.data(Qt.ItemDataRole.CheckStateRole) is None                     or not self.tick_rect(option, index).contains(event.position().toPoint()):
                return False
            if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                on = Qt.CheckState(index.data(Qt.ItemDataRole.CheckStateRole)) == Qt.CheckState.Checked
                model.setData(index, Qt.CheckState.Unchecked if on else Qt.CheckState.Checked,
                              Qt.ItemDataRole.CheckStateRole)
            return True
        return super().editorEvent(event, model, option, index)       # Space still ticks from the keyboard


class SceneReview(QWidget):
    changed = Signal()                  # tags were accepted (the tag list changed)

    def __init__(self, conn, parent=None) -> None:
        super().__init__(parent)
        self.conn = conn
        self.bg = Background(self, conn)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 8, 0, 0)
        self.summary = QLabel(objectName="Help")
        self.summary.setWordWrap(True)
        v.addWidget(self.summary)
        split = QSplitter()
        self.tags = QListWidget()
        self.tags.setMinimumWidth(220)
        self.tags.currentItemChanged.connect(lambda *_: self._load_photos())
        split.addWidget(self.tags)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(8, 0, 0, 0)
        self.photos = QListWidget()
        self.photos.setViewMode(QListWidget.ViewMode.IconMode)
        self.photos.setIconSize(QSize(THUMB, THUMB))
        self.photos.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.photos.setMovement(QListWidget.Movement.Static)
        self.photos.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.photos.itemClicked.connect(self._flip)
        # The tick inside each thumbnail, its percentage directly under the picture (0.55).
        self.photos.setItemDelegate(TickThumb(self.photos, caption=True, click_ticks=False))
        self.photos.setSpacing(4)
        rv.addWidget(self.photos, 1)
        row = QHBoxLayout()
        self.accept_b = QPushButton("Accept ticked", objectName="Primary", clicked=self.accept_ticked)
        self.reject_b = QPushButton("Reject ticked", clicked=self.reject_ticked)
        row.addWidget(self.accept_b)
        row.addWidget(self.reject_b)
        row.addWidget(QPushButton("Tick all", clicked=lambda: self._tick_all(True)))
        row.addWidget(QPushButton("Tick none", clicked=lambda: self._tick_all(False)))
        row.addStretch(1)
        row.addWidget(QLabel("Accept all at or above"))
        self.threshold = RoundSlider(Qt.Orientation.Horizontal, minimum=30, maximum=99, value=70)
        self.threshold.setMaximumWidth(160)
        self.threshold_label = QLabel("70 %")
        self.threshold.valueChanged.connect(lambda val: self.threshold_label.setText(f"{val} %"))
        row.addWidget(self.threshold)
        row.addWidget(self.threshold_label)
        self.bulk_b = QPushButton("Accept all above", clicked=self.accept_above)
        self.bulk_b.setToolTip("Accept every suggestion of this tag at or above the confidence set here")
        row.addWidget(self.bulk_b)
        rv.addLayout(row)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        v.addWidget(split, 1)
        self._thumbs: list[QListWidgetItem] = []
        self._thumb_timer = QTimer(self, interval=0, timeout=self._load_thumbs)

    # --- loading --------------------------------------------------------------------------

    def _note(self):
        if not hasattr(self, "_notice"):
            from lunelis.ui.notice import Notice
            self._notice = Notice()
        return self._notice

    def refresh(self) -> None:
        self.bg.run("queue", lambda c: scenes.queue(c), self._show_queue,
                    error=lambda e: self.summary.setText(f"Couldn't read the suggestions: {e}"))

    def _show_queue(self, rows) -> None:
        keep = self.current_tag()
        self.tags.blockSignals(True)
        self.tags.clear()
        for tag, n, avg in rows:
            it = QListWidgetItem(f"{tag.split('|')[-1]}   ({n:,} · {avg * 100:.0f} %)")
            it.setData(Qt.ItemDataRole.UserRole, tag)
            self.tags.addItem(it)
            if tag == keep:
                self.tags.setCurrentItem(it)
        self.tags.blockSignals(False)
        total = sum(n for _, n, _ in rows)
        from lunelis.recognize import clip
        if not clip.available():
            self.summary.setText("Scene tags are off. Settings > Recognition & autopilot > Scene tags downloads the model (about "
                                 "155 MB) - it runs only on this PC.")
        elif not rows:
            self.summary.setText(self._note().prefix() + "No suggestions waiting. Settings > Recognition & autopilot > Scene tags > Tag the library looks "
                                 "through your photos (their thumbnails) in the background.")
        else:
            self.summary.setText(self._note().prefix() + f"{total:,} suggestions for {len(rows):,} scene tags. Only accepted ones become "
                                 "tags and go to sidecars; rejected ones aren't suggested again.")
        if self.tags.currentItem() is None and self.tags.count():
            self.tags.setCurrentRow(0)
        self._load_photos()

    def current_tag(self) -> str | None:
        it = self.tags.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _load_photos(self) -> None:
        self.photos.clear()
        self._thumbs = []
        tag = self.current_tag()
        for b in (self.accept_b, self.reject_b, self.bulk_b):
            b.setEnabled(tag is not None)
        if tag is None:
            return
        for fid, conf in scenes.suggested_photos(self.conn, tag):
            it = QListWidgetItem(f"{conf * 100:.0f} %")
            it.setData(Qt.ItemDataRole.UserRole, fid)
            it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if conf * 100 >= self.threshold.value() else Qt.CheckState.Unchecked)
            # A cell the size of the picture plus its percentage; the delegate puts
            # the percentage straight under the picture, wide or upright (0.55).
            it.setSizeHint(QSize(THUMB + 2 * PAD, THUMB + 2 * PAD + self.photos.fontMetrics().height() + 4))
            self.photos.addItem(it)
            self._thumbs.append(it)
        self._thumb_timer.start()

    def _load_thumbs(self) -> None:
        for _ in range(24):
            if not self._thumbs:
                self._thumb_timer.stop()
                return
            it = self._thumbs.pop(0)
            try:
                fid = it.data(Qt.ItemDataRole.UserRole)
            except RuntimeError:
                continue
            row = self.conn.execute("SELECT thumbnail_path FROM files WHERE id = ?", (fid,)).fetchone()
            pm = QPixmap(str(paths.THUMBNAIL_CACHE / ((row[0] if row else None) or cache_rel_path(fid))))
            if not pm.isNull():
                it.setIcon(QIcon(pm))

    # --- actions ----------------------------------------------------------------------------

    def _flip(self, it: QListWidgetItem) -> None:
        it.setCheckState(Qt.CheckState.Unchecked if it.checkState() == Qt.CheckState.Checked else Qt.CheckState.Checked)

    def _tick_all(self, on: bool) -> None:
        for i in range(self.photos.count()):
            self.photos.item(i).setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    def ticked(self) -> list[int]:
        return [self.photos.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.photos.count())
                if self.photos.item(i).checkState() == Qt.CheckState.Checked]

    def accept_ticked(self) -> None:
        tag = self.current_tag()
        if tag and self.ticked():
            n = scenes.accept(self.conn, tag, self.ticked())
            self.summary.setText(self._note().say(f"Accepted {n:,}: they're tagged {tag.replace('|', ' > ')} now."))
            self.changed.emit()
            self.refresh()

    def reject_ticked(self) -> None:
        tag = self.current_tag()
        if tag and self.ticked():
            n = scenes.reject(self.conn, tag, self.ticked())
            self.summary.setText(f"Rejected {n:,} - they won't be suggested as {tag.split('|')[-1]} again.")
            self.refresh()

    def accept_above(self) -> None:
        tag = self.current_tag()
        if tag:
            pct = self.threshold.value()
            count = self.conn.execute(
                "SELECT COUNT(*) FROM file_tags ft JOIN tags t ON t.id = ft.tag_id JOIN files f ON f.id = ft.file_id"
                " WHERE t.name = ? AND ft.confidence IS NOT NULL AND ft.confidence >= ?"
                " AND f.missing_since IS NULL AND f.excluded = 0 AND f.quarantined_at IS NULL",
                (tag, pct / 100)).fetchone()[0]
            if not count:
                self.summary.setText(f"No {tag.split('|')[-1]} suggestions at or above {pct} %.")
                return
            from PySide6.QtWidgets import QMessageBox
            if QMessageBox.question(
                    self, "Accept suggestions",
                    f"Tag {count:,} photo{'s' if count != 1 else ''} {tag.replace('|', ' > ')}? (Every suggestion "
                    f"at or above {pct} %. You can remove a tag later on the Tags page.)") \
                    != QMessageBox.StandardButton.Yes:
                return
            n = scenes.accept_above(self.conn, tag, pct / 100)
            self.summary.setText(self._note().say(f"Accepted {n:,} at or above {self.threshold.value()} %."))
            self.changed.emit()
            self.refresh()
