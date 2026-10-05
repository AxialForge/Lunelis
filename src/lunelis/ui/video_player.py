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
        v.addWidget(self.video, 1)
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
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderMoved.connect(self.player.setPosition)
        self.slider.setToolTip("Drag to move through the clip; J / L jump 5 seconds")
        h.addWidget(self.slider, 1)
        self.len_l = QLabel("0:00", objectName="ToolLabel")
        h.addWidget(self.len_l)
        self.mute_b = QPushButton("Sound on", checkable=True)
        self.mute_b.setToolTip("Mute")
        self.mute_b.toggled.connect(self._mute)
        h.addWidget(self.mute_b)
        self.in_b = QPushButton("Start here", clicked=self.set_in)
        self.in_b.setToolTip("Mark where the trimmed copy starts (I)")
        self.out_b = QPushButton("End here", clicked=self.set_out)
        self.out_b.setToolTip("Mark where the trimmed copy ends (O)")
        self.save_b = QPushButton("Save trimmed copy", clicked=self.save_trim)
        self.save_b.setToolTip("A new file between the marks, in the Create folder - the original is untouched")
        for b in (self.in_b, self.out_b, self.save_b):
            h.addWidget(b)
        v.addWidget(bar)
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

    def load(self, path: str) -> None:
        if path == self.path:
            return
        self.player.stop()
        self.path = path
        self.mark_in = self.mark_out = None
        self.note.setText("")
        self.player.setSource(QUrl.fromLocalFile(path))
        self._update_marks()

    def stop(self) -> None:
        """Leaving the clip: stop and let go of the file."""
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
        self.mute_b.setText("Muted" if on else "Sound on")

    def _duration(self, ms: int) -> None:
        self.slider.setRange(0, ms)
        self.len_l.setText(clock(ms))
        self._update_marks()

    def _position(self, ms: int) -> None:
        if not self.slider.isSliderDown():
            self.slider.setValue(ms)
        self.pos_l.setText(clock(ms))

    def _state(self, state) -> None:
        from PySide6.QtMultimedia import QMediaPlayer
        self.play_b.setText("❚❚" if state == QMediaPlayer.PlaybackState.PlayingState else "▶")

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

    def _update_marks(self) -> None:
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
            self.note.setText(f"Trim failed: {r}")
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
