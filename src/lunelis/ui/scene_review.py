"""
Scene suggestions to review (Tags page > Suggestions): what the scene model
thinks is in your photos, a tag at a time. Tick and Accept (they become your
tags, and go to sidecars), Reject (gone, and never suggested again for those
photos), or accept everything above a confidence you trust.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QSlider, QSplitter,
    QVBoxLayout, QWidget,
)
from lunelis.ui.round_slider import RoundSlider  # noqa: E402  true circles (0.52)

from lunelis import paths
from lunelis.raw.thumbnails import cache_rel_path
from lunelis.recognize import scenes
from lunelis.ui.background import Background

THUMB = 120


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
            self.summary.setText("Scene tags are off. Settings > Library > Scene tags downloads the model (about "
                                 "155 MB) - it runs only on this PC.")
        elif not rows:
            self.summary.setText(self._note().prefix() + "No suggestions waiting. Settings > Library > Scene tags > Tag the library looks "
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
            it.setSizeHint(QSize(THUMB + 16, THUMB + 34))
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
