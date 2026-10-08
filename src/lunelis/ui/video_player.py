"""
Video playback in the photo view, with trimming to a new file.

    [ video                                                        ]
    [ ▶  0:12 ━━━━━━━●━━━━━━━━━━━━━━ 1:04   🔈   [ Start ] [ End ] [ Save trim… ] ]

- Qt Multimedia (FFmpeg backend) plays the file; nothing is decoded on the
  interface thread by us. Keys (handled by the photo view): K or the play
  button plays / pauses, J / L jump 5 s back / on, I / O mark the trim's
  start / end.
- **Save trim** copies the part between the marks to a new file in the
  Create folder (video/trim.py, on a worker). The original is only read.
- The player is made the first time a video is shown, so opening Lunelis
  doesn't load Qt Multimedia.
- **S-Log3 clips** (video/slog.py) play through the log preview: frames come
  from a QVideoSink, go through the LUT's fast table at about screen size and
  are painted by LutView. "Show log" turns the look off to compare.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QUrl, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget

from lunelis.ui.background import unless_closed

STEP_MS = 5000


def clock(ms: int) -> str:
    s = max(0, int(ms // 1000))
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


class _TrimSignals(QObject):
    done = Signal(object)               # TrimResult or an Exception


class _TrimJob(QRunnable):
    def __init__(self, signals, src: str, start: float, end: float, folder: str) -> None:
        super().__init__()
        self.signals, self.src, self.start, self.end, self.folder = signals, src, start, end, folder

    def run(self) -> None:
        from lunelis.video import trim
        try:
            self.signals.done.emit(trim.trim(self.src, self.start, self.end, self.folder))
        except Exception as e:          # shown in words on the player
            self.signals.done.emit(e)


class LutView(QWidget):
    """Paints the player's frames through a LUT, fitted to the widget."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        from PySide6.QtMultimedia import QVideoSink
        self.sink = QVideoSink(self)
        self.sink.videoFrameChanged.connect(self._frame)
        self.lut = None
        self.turn = 0                    # quarter turns clockwise (turns.py)
        self.image = None
        self.setMinimumSize(80, 60)

    def _frame(self, frame) -> None:
        import numpy as np
        from PySide6.QtGui import QImage
        img = frame.toImage()
        if img.isNull():
            return
        # Fit first: a 4K frame is scaled to the widget before the LUT, so the
        # lookup costs what the screen shows, not what the camera recorded.
        w, h = max(1, self.width()), max(1, self.height())
        if self.turn % 2:
            w, h = h, w                  # fitted the other way round, then turned
        img = img.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation)
        if self.turn:
            from PySide6.QtGui import QTransform
            img = img.transformed(QTransform().rotate(90 * self.turn))
        img = img.convertToFormat(QImage.Format.Format_RGB888)
        if self.lut is not None:
            bpl = img.bytesPerLine()
            a = np.frombuffer(img.constBits(), np.uint8, bpl * img.height()).reshape(img.height(), bpl)
            rgb = np.ascontiguousarray(a[:, :img.width() * 3].reshape(img.height(), img.width(), 3))
            out = self.lut.apply_fast(rgb)
            img = QImage(out.data, out.shape[1], out.shape[0], out.shape[1] * 3, QImage.Format.Format_RGB888).copy()
        self.image = img
        self.update()

    def paintEvent(self, e) -> None:
        from PySide6.QtGui import QPainter
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.GlobalColor.black)
        if self.image is not None:
            x = (self.width() - self.image.width()) // 2
            y = (self.height() - self.image.height()) // 2
            p.drawImage(x, y, self.image)
        p.end()


class TrimBar(QWidget):
    """The play bar: the playhead, and the trim flags drawn on it. Click or drag
    the bar to move through the clip; drag a flag to move it; double-click a
    flag to take it off."""

    seek = Signal(int)                   # ms
    marks_moved = Signal(object, object)  # in, out (ms or None)
    FLAG = 7                             # half-width of a flag's handle, px

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.duration = 0
        self.position = 0
        self.mark_in: int | None = None
        self.mark_out: int | None = None
        self._drag: str | None = None    # 'in' | 'out' | 'head'
        self.setMinimumHeight(30)
        self.setMouseTracking(True)
        self.setToolTip("Click or drag to move through the clip · drag a flag to move it, double-click to remove it")

    def _x(self, ms: int) -> float:
        w = max(1, self.width() - 2 * self.FLAG)
        return self.FLAG + (ms / self.duration * w if self.duration else 0)

    def _ms(self, x: float) -> int:
        w = max(1, self.width() - 2 * self.FLAG)
        return int(max(0, min(self.duration, (x - self.FLAG) / w * self.duration))) if self.duration else 0

    def _hit(self, x: float) -> str | None:
        for name, ms in (("in", self.mark_in), ("out", self.mark_out)):
            if ms is not None and abs(self._x(ms) - x) <= self.FLAG + 2:
                return name
        return None

    def paintEvent(self, e) -> None:
        from PySide6.QtGui import QColor, QPainter, QPolygonF
        from PySide6.QtCore import QPointF, QRectF
        from lunelis.ui import theme
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        mid = self.height() / 2 + 4
        track = QRectF(self.FLAG, mid - 3, self.width() - 2 * self.FLAG, 6)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t.border))
        p.drawRoundedRect(track, 3, 3)
        if self.mark_in is not None or self.mark_out is not None:
            a = self._x(self.mark_in or 0)
            b = self._x(self.mark_out if self.mark_out is not None else self.duration)
            span = QColor(t.accent)
            span.setAlpha(110)
            p.setBrush(span)
            p.drawRect(QRectF(a, mid - 3, max(1.0, b - a), 6))
        p.setBrush(QColor(t.text_muted))
        p.drawRoundedRect(QRectF(self.FLAG, mid - 3, self._x(self.position) - self.FLAG, 6), 3, 3)
        for ms, colour in ((self.mark_in, "#2e8b57"), (self.mark_out, "#d0453b")):
            if ms is None:
                continue
            x = self._x(ms)
            p.setBrush(QColor(colour))
            p.drawRect(QRectF(x - 1, 2, 2, mid + 4))
            p.drawPolygon(QPolygonF([QPointF(x - self.FLAG, 2), QPointF(x + self.FLAG, 2), QPointF(x, 2 + self.FLAG + 3)]))
        p.setBrush(QColor(t.text))
        p.drawEllipse(QPointF(self._x(self.position), mid), 6, 6)
        p.end()

    def mousePressEvent(self, e) -> None:
        x = e.position().x()
        self._drag = self._hit(x) or "head"
        if self._drag == "head":
            self.seek.emit(self._ms(x))

    def mouseMoveEvent(self, e) -> None:
        x = e.position().x()
        if self._drag is None:
            self.setCursor(Qt.CursorShape.SizeHorCursor if self._hit(x) else Qt.CursorShape.PointingHandCursor)
            return
        ms = self._ms(x)
        if self._drag == "head":
            self.seek.emit(ms)
        elif self._drag == "in":
            self.mark_in = min(ms, (self.mark_out if self.mark_out is not None else self.duration) - 100)
            self.marks_moved.emit(self.mark_in, self.mark_out)
        else:
            self.mark_out = max(ms, (self.mark_in or 0) + 100)
            self.marks_moved.emit(self.mark_in, self.mark_out)
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        self._drag = None

    def mouseDoubleClickEvent(self, e) -> None:
        hit = self._hit(e.position().x())
        if hit == "in":
            self.mark_in = None
        elif hit == "out":
            self.mark_out = None
        else:
            return
        self.marks_moved.emit(self.mark_in, self.mark_out)
        self.update()


class VideoPlayer(QWidget):
    trimmed = Signal(str)               # the new file's path

    def __init__(self, conn, parent=None) -> None:
        super().__init__(parent)
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget
        self.conn = conn
        self.path: str | None = None
        self.mark_in: int | None = None     # ms
        self.mark_out: int | None = None
        self.busy = False
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.video = QVideoWidget()
        self.video.setStyleSheet("background: black;")
        self.player.setVideoOutput(self.video)

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        from PySide6.QtWidgets import QStackedWidget
        self.screen = QStackedWidget()
        self.screen.addWidget(self.video)
        self.lut_view = LutView()
        self.screen.addWidget(self.lut_view)
        v.addWidget(self.screen, 1)
        self.log_kind: str | None = None
        self.lut = None
        bar = QWidget(objectName="Toolbar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 6, 12, 6)
        h.setSpacing(10)
        self.play_b = QPushButton("▶", clicked=self.toggle)
        self.play_b.setToolTip("Play / pause (K)")
        self.play_b.setFixedWidth(40)
        h.addWidget(self.play_b)
        self.pos_l = QLabel("0:00", objectName="ToolLabel")
        h.addWidget(self.pos_l)
        self.slider = TrimBar()
        self.slider.seek.connect(self.player.setPosition)
        self.slider.marks_moved.connect(self._marks_dragged)
        h.addWidget(self.slider, 1)
        self.len_l = QLabel("0:00", objectName="ToolLabel")
        h.addWidget(self.len_l)
        self.mute_b = QPushButton("🔊", checkable=True)
        self.mute_b.setToolTip("Mute / unmute")
        self.mute_b.setMinimumWidth(40)                # sized to fit its icon (0.48: it was clipped)
        self.mute_b.toggled.connect(self._mute)
        h.addWidget(self.mute_b)
        self.volume = QSlider(Qt.Orientation.Horizontal, minimum=0, maximum=100, value=80)
        self.volume.setFixedWidth(90)
        self.volume.setToolTip("Volume")
        self.volume.valueChanged.connect(lambda v: self.audio.setVolume(v / 100))
        self.audio.setVolume(0.8)
        h.addWidget(self.volume)
        v.addWidget(bar)
        # Trimming on its own row, so the play bar keeps its width on a small window.
        trim = QWidget(objectName="Toolbar")
        th = QHBoxLayout(trim)
        th.setContentsMargins(12, 0, 12, 6)
        th.setSpacing(8)
        self.in_b = QPushButton("Start here", clicked=self.set_in)
        self.in_b.setToolTip("Put the green start flag at the playhead (I)")
        self.clear_in_b = QPushButton("×", clicked=lambda: self._marks_dragged(None, self.mark_out))
        self.clear_in_b.setToolTip("Remove the start flag")
        self.out_b = QPushButton("End here", clicked=self.set_out)
        self.out_b.setToolTip("Put the red end flag at the playhead (O)")
        self.clear_out_b = QPushButton("×", clicked=lambda: self._marks_dragged(self.mark_in, None))
        self.clear_out_b.setToolTip("Remove the end flag")
        for b in (self.clear_in_b, self.clear_out_b):
            b.setMinimumWidth(30)
        self.save_b = QPushButton("Save trimmed copy", clicked=self.save_trim)
        self.save_b.setToolTip("A new file between the flags, in the Create folder - the original is untouched")
        self.log_b = QPushButton("Show log", checkable=True)
        self.log_b.setToolTip("Show the clip as recorded (flat S-Log), without the preview look")
        self.log_b.toggled.connect(self._show_log)
        self.log_b.hide()
        for b in (self.in_b, self.clear_in_b, self.out_b, self.clear_out_b):
            th.addWidget(b)
        th.addStretch(1)
        th.addWidget(self.log_b)
        th.addWidget(self.save_b)
        v.addWidget(trim)
        # A click on the picture plays or pauses, as in every video player.
        for w in (self.video, self.lut_view):
            w.installEventFilter(self)
        self.note = QLabel(objectName="Count")
        self.note.setTextFormat(Qt.TextFormat.RichText)
        self.note.setOpenExternalLinks(False)
        self.note.linkActivated.connect(self._open_link)
        self.note.setContentsMargins(12, 0, 12, 6)
        v.addWidget(self.note)

        self.player.durationChanged.connect(self._duration)
        self.player.positionChanged.connect(self._position)
        self.player.playbackStateChanged.connect(self._state)
        self.player.errorOccurred.connect(self._error)
        self._trim_signals = _TrimSignals()
        self._trim_signals.done.connect(self._trim_done)
        self._update_marks()

    # --- loading -----------------------------------------------------------------------

    def load(self, path: str, turn: int = 0) -> None:
        if path == self.path and turn == self.lut_view.turn:
            return
        self.lut_view.turn = turn
        self.player.stop()
        self.path = path
        self.mark_in = self.mark_out = None
        self.note.setText("")
        self._choose_look(path)
        self.player.setSource(QUrl.fromLocalFile(path))
        self._update_marks()

    def _choose_look(self, path: str) -> None:
        from lunelis.settings import Settings
        from lunelis.video import slog
        self.log_kind = slog.detect(path)
        self.lut = slog.preview_lut(path, Settings(self.conn).get("log_preview")) if self.log_kind else None
        self.log_b.setVisible(self.lut is not None)
        self.log_b.blockSignals(True)
        self.log_b.setChecked(False)
        self.log_b.blockSignals(False)
        self._apply_look()
        self.note.setText(self.look_text())

    def _apply_look(self) -> None:
        # A turned clip is drawn by LutView too: QVideoWidget can't rotate.
        through_lut = self.lut is not None or self.lut_view.turn != 0
        self.lut_view.lut = None if self.log_b.isChecked() else self.lut
        self.lut_view.image = None
        if through_lut:
            self.player.setVideoOutput(self.lut_view.sink)
            self.screen.setCurrentWidget(self.lut_view)
        else:
            self.player.setVideoOutput(self.video)
            self.screen.setCurrentWidget(self.video)

    def _show_log(self, on: bool) -> None:
        self.lut_view.lut = None if on else self.lut
        self.log_b.setText("Show preview" if on else "Show log")
        self.player.setPosition(self.player.position())       # redraw the current frame now

    def look_text(self) -> str:
        from lunelis.video import slog
        if not self.log_kind:
            return ""
        if self.lut is None:
            return f"{slog.describe(self.log_kind)} - shown as recorded (log preview is off in Settings)"
        return f"{slog.describe(self.log_kind)} - shown through {self.lut.title}"

    def stop(self) -> None:
        """Leaving the clip: stop and let go of the file."""
        from lunelis import pace
        pace.set_playing(False)
        self.player.stop()
        self.player.setSource(QUrl())
        self.path = None

    # --- playing -----------------------------------------------------------------------

    def toggle(self) -> None:
        from PySide6.QtMultimedia import QMediaPlayer
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def jump(self, ms: int) -> None:
        self.player.setPosition(max(0, min(self.player.duration(), self.player.position() + ms)))

    def _mute(self, on: bool) -> None:
        self.audio.setMuted(on)
        self.mute_b.setText("🔇" if on else "🔊")
        self.volume.setEnabled(not on)

    def _duration(self, ms: int) -> None:
        self.slider.duration = ms
        self.slider.update()
        self.len_l.setText(clock(ms))
        self._update_marks()

    def _position(self, ms: int) -> None:
        self.slider.position = ms
        self.slider.update()
        self.pos_l.setText(clock(ms))

    def _state(self, state) -> None:
        from PySide6.QtMultimedia import QMediaPlayer
        from lunelis import pace
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_b.setText("❚❚" if playing else "▶")
        pace.set_playing(playing)                  # background work waits while it plays

    def hideEvent(self, e) -> None:
        from lunelis import pace
        pace.set_playing(False)                    # off screen: nothing to give way to
        super().hideEvent(e)

    def _error(self, _err, text: str) -> None:
        self.note.setText(f"Couldn't play this file: {text or 'unknown format'}. "
                          "Open it with the default app from the Info panel.")

    # --- trimming ----------------------------------------------------------------------

    def set_in(self) -> None:
        self.mark_in = self.player.position()
        if self.mark_out is not None and self.mark_out <= self.mark_in:
            self.mark_out = None
        self._update_marks()

    def set_out(self) -> None:
        self.mark_out = self.player.position()
        if self.mark_in is not None and self.mark_in >= self.mark_out:
            self.mark_in = None
        self._update_marks()

    def _span(self) -> tuple[int, int]:
        return (self.mark_in or 0, self.mark_out if self.mark_out is not None else self.player.duration())

    def eventFilter(self, obj, e) -> bool:
        from PySide6.QtCore import QEvent
        if obj in (self.video, self.lut_view) and e.type() == QEvent.Type.MouseButtonRelease \
                and e.button() == Qt.MouseButton.LeftButton:
            self.toggle()
            return True
        return super().eventFilter(obj, e)

    def _marks_dragged(self, a, b) -> None:
        self.mark_in, self.mark_out = a, b
        self._update_marks()

    def _update_marks(self) -> None:
        self.slider.mark_in, self.slider.mark_out = self.mark_in, self.mark_out
        self.slider.update()
        self.clear_in_b.setEnabled(self.mark_in is not None)
        self.clear_out_b.setEnabled(self.mark_out is not None)
        a, b = self._span()
        marked = self.mark_in is not None or self.mark_out is not None
        self.save_b.setEnabled(bool(self.path) and marked and b - a >= 100 and not self.busy)
        if marked and not self.busy:
            self.note.setText(f"Trim: {clock(a)} to {clock(b)} ({clock(b - a)}). "
                              "The copy starts at the keyframe just before the start mark.")

    def save_trim(self) -> None:
        if not self.path or self.busy:
            return
        from lunelis.create import engine
        a, b = self._span()
        self.busy = True
        self._update_marks()
        self.note.setText("Saving the trimmed copy…")
        QThreadPool.globalInstance().start(
            _TrimJob(self._trim_signals, self.path, a / 1000, b / 1000, str(engine.output_dir(self.conn))))

    @unless_closed
    def _trim_done(self, r) -> None:
        self.busy = False
        self._update_marks()
        if isinstance(r, Exception):
            from lunelis.reach import explain, gone_offline
            self.note.setText(explain(r, "trimming the clip", self.path) if gone_offline(r)
                              else f"Trim failed: {r}")
            return
        from pathlib import Path
        p = Path(r.path)
        self.note.setText(f'Saved <a href="file:{p.as_posix()}">{p.name}</a> · '
                          f'<a href="folder:{p.parent.as_posix()}">Open the folder</a>')
        self.trimmed.emit(r.path)

    def _open_link(self, href: str) -> None:
        from PySide6.QtGui import QDesktopServices
        kind, _, target = href.partition(":")
        QDesktopServices.openUrl(QUrl.fromLocalFile(target))
