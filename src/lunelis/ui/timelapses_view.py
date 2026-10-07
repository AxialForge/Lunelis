"""
The Timelapses page (sidebar > Bring in & organize > Timelapses): every
interval shoot the timelapse engine found (timelapses.py), for review.

Each timelapse shows its first frame, when and with what it was shot, how
many frames at what interval, and how long it plays. Per timelapse:
Show photos, Build timelapse..., Confirm / Dismiss, Stack (one tile in the
library) / Unstack, and - for 50 frames or fewer - It's a burst. Nothing is
built automatically. At the top: stack every timelapse of 500+ frames by
itself, show dismissed ones, and Look again now.
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from lunelis import timelapses
from lunelis.settings import Settings
from lunelis.ui.background import Background

THUMB = 120
FPS = 24


def _when_and_camera(conn, fid: int) -> tuple[str, str]:
    row = conn.execute("SELECT e.captured_at, COALESCE(e.camera_model, ''), COALESCE(e.lens, '')"
                       " FROM exif e WHERE e.file_id = ?", (fid,)).fetchone()
    if not row or not row[0]:
        return "", ""
    from lunelis.ui.photoinfo import format_date
    try:
        when = format_date(datetime.fromisoformat(row[0][:19]))
    except ValueError:
        when = row[0][:16]
    return when, " · ".join(x for x in row[1:] if x)


def _thumb(conn, fid: int):
    from lunelis import paths
    from lunelis.ui.thumbcache import load_image
    row = conn.execute("SELECT thumbnail_path FROM files WHERE id = ?", (fid,)).fetchone()
    return load_image(paths.THUMBNAIL_CACHE / row[0], THUMB) if row and row[0] else None


def _length(frames: int) -> str:
    s = frames / FPS
    return f"{s:.0f} s" if s < 60 else f"{int(s // 60)} min {int(s % 60)} s"


def describe(q: timelapses.Sequence) -> str:
    parts = [f"{len(q.file_ids):,} frames"]
    if q.interval:
        parts[0] += f" every {q.interval:g} s"
    if q.pauses:
        parts.append(f"{q.pauses} pause{'s' if q.pauses != 1 else ''}")
    parts.append(f"{_length(len(q.file_ids))} at {FPS} fps")
    return " · ".join(parts)


STATUS = {"found": "Found", "confirmed": "Confirmed", "dismissed": "Dismissed"}


class TimelapsesView(QWidget):
    show_ids = Signal(list, str)          # file ids, a name for the filter chip
    build = Signal(list)                  # frames to hand to Create > Timelapse
    changed = Signal()                    # stacks changed: the library reloads

    def __init__(self, conn, parent=None) -> None:
        super().__init__(parent)
        self.conn = conn
        self.bg = Background(self, conn)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        head = QWidget(objectName="Toolbar")
        head.setFixedHeight(64)
        h = QHBoxLayout(head)
        h.setContentsMargins(24, 0, 24, 0)
        h.addWidget(QLabel("Timelapses", objectName="PageTitle"))
        h.addSpacing(12)
        self.summary = QLabel(objectName="Count")
        h.addWidget(self.summary)
        h.addStretch(1)
        self.auto_stack = QCheckBox()
        self.auto_stack.setToolTip("New timelapses this long show as one tile in the library as soon as they're found")
        self.auto_stack.toggled.connect(lambda on: Settings(self.conn).set("timelapse_auto_stack", on))
        h.addWidget(self.auto_stack)
        self.show_dismissed = QCheckBox("Show dismissed")
        self.show_dismissed.toggled.connect(self.refresh)
        h.addWidget(self.show_dismissed)
        self.look_b = QPushButton("Look again now", clicked=self.look_again)
        self.look_b.setToolTip("Look through the whole library for interval shoots (about a second)")
        h.addWidget(self.look_b)
        outer.addWidget(head)
        self.note = QLabel(objectName="Help", wordWrap=True)
        self.note.setContentsMargins(24, 8, 24, 0)
        outer.addWidget(self.note)
        scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.Shape.NoFrame)
        body = QWidget()
        self.rows = QVBoxLayout(body)
        self.rows.setContentsMargins(24, 12, 24, 24)
        self.rows.setSpacing(12)
        self.rows.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        self.cards: dict[int, QFrame] = {}

    def refresh(self) -> None:
        s = Settings(self.conn)
        self.auto_stack.blockSignals(True)
        self.auto_stack.setChecked(bool(s.get("timelapse_auto_stack")))
        self.auto_stack.setText(f"Stack {s.get('timelapse_auto_stack_frames'):,}+ frames by themselves")
        self.auto_stack.blockSignals(False)
        self.note.setText(
            f"Interval shoots of {s.get('timelapse_min_frames'):,} frames or more at a steady interval, from one "
            "camera and lens - a RAW+JPEG pair is one frame. Shorter sets: select them in the library, then "
            "Photo > Make a timelapse from the selection. Nothing is built until you press Build."
            + ("" if s.get("timelapse_detect") else "  Looking for them after each scan is off (Settings > Library)."))
        while self.rows.count() > 1:
            w = self.rows.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self.cards = {}
        qs = [q for q in timelapses.all_sequences(self.conn, self.show_dismissed.isChecked()) if q.kind == "timelapse"]
        frames = sum(len(q.file_ids) for q in qs)
        self.summary.setText(f"{len(qs):,} timelapse{'s' if len(qs) != 1 else ''} · {frames:,} frames" if qs else "")
        if not qs:
            self.rows.insertWidget(0, QLabel("No timelapses found yet.", objectName="Help"))
            return
        for n, q in enumerate(qs):
            card = self._card(q)
            self.cards[q.id] = card
            self.rows.insertWidget(n, card)

    def _card(self, q: timelapses.Sequence) -> QFrame:
        card = QFrame(objectName="Card")
        h = QHBoxLayout(card)
        h.setContentsMargins(12, 10, 12, 10)
        h.setSpacing(14)
        pic = QLabel()
        pic.setFixedSize(THUMB * 3 // 2, THUMB)
        pic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        img = _thumb(self.conn, q.file_ids[0]) if q.file_ids else None
        if img is not None and not img.isNull():
            pic.setPixmap(QPixmap.fromImage(img).scaled(pic.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                                       Qt.TransformationMode.SmoothTransformation))
        h.addWidget(pic)
        text = QVBoxLayout()
        when, camera = _when_and_camera(self.conn, q.file_ids[0]) if q.file_ids else ("", "")
        title = QLabel(when or "Undated", objectName="SectionTitle")
        text.addWidget(title)
        text.addWidget(QLabel(camera, objectName="Help"))
        text.addWidget(QLabel(describe(q)))
        state = STATUS.get(q.status, q.status) + (" · stacked" if q.stacked else "") \
            + (" · made by hand" if q.origin == "manual" else "")
        text.addWidget(QLabel(state, objectName="Count"))
        text.addStretch(1)
        h.addLayout(text, 1)
        buttons = QVBoxLayout()
        row1, row2 = QHBoxLayout(), QHBoxLayout()
        b = QPushButton("Build timelapse…", objectName="Primary",
                        clicked=lambda _=False, ids=q.file_ids: self.build.emit(list(ids)))
        row1.addWidget(b)
        row1.addWidget(QPushButton("Show photos", clicked=lambda _=False, q=q: self.show_ids.emit(
            list(q.file_ids), f"Timelapse, {len(q.file_ids):,} frames")))
        if q.status == "dismissed":
            row2.addWidget(QPushButton("Restore", clicked=lambda _=False, sid=q.id: self._status(sid, "found")))
        else:
            if q.status != "confirmed":
                row2.addWidget(QPushButton("Confirm", clicked=lambda _=False, sid=q.id: self._status(sid, "confirmed")))
            row2.addWidget(QPushButton("Dismiss", clicked=lambda _=False, sid=q.id: self._status(sid, "dismissed")))
            row2.addWidget(QPushButton("Unstack" if q.stacked else "Stack",
                                       clicked=lambda _=False, sid=q.id, on=q.stacked: self._stack(sid, not on)))
            if len(q.file_ids) <= timelapses.BURST_MAX:
                burst = QPushButton("It's a burst", clicked=lambda _=False, sid=q.id: self._burst(sid))
                burst.setToolTip("Stack these frames as a burst instead")
                row2.addWidget(burst)
        buttons.addLayout(row1)
        buttons.addLayout(row2)
        buttons.addStretch(1)
        h.addLayout(buttons)
        return card

    def _status(self, sid: int, status: str) -> None:
        timelapses.set_status(self.conn, sid, status)
        self.changed.emit()
        self.refresh()

    def _stack(self, sid: int, on: bool) -> None:
        if on:
            timelapses.stack(self.conn, sid)
        else:
            timelapses.unstack_sequence(self.conn, sid)
        self.changed.emit()
        self.refresh()

    def _burst(self, sid: int) -> None:
        timelapses.to_burst(self.conn, sid)
        self.changed.emit()
        self.refresh()

    def look_again(self) -> None:
        self.look_b.setEnabled(False)
        self.summary.setText("Looking…")
        self.bg.run("look", timelapses.refresh, self._looked)

    def _looked(self, added) -> None:
        self.look_b.setEnabled(True)
        self.changed.emit()
        self.refresh()
        if isinstance(added, int) and added:
            self.summary.setText(self.summary.text() + f" · {added:,} new")
