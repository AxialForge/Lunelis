"""
Asynchronous thumbnail loading for the grid.

Paint must never touch the disk: at ~30 tiles a screen a synchronous 2-3 ms
JPEG decode each is a visible stall on every scroll. Instead the grid asks
`get()`; a hit returns a ready QPixmap, a miss queues a load on a thread pool
and returns None (the grid draws a placeholder). Worker threads decode into
QImage, cover-crop and scale to the tile size (QImage is safe off the GUI
thread, QPixmap is not); the GUI thread converts to QPixmap on arrival.
Decoding is Pillow's - see load_square() for why.

Requests carry a generation number: when the tile size changes, loads queued
for the old size are dropped instead of arriving late.
"""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QImage, QPixmap

# Caches are bound by what their pixmaps weigh, not by how many there are (0.54:
# 1,500 pixmaps was 1.2 GB at the largest grid size, 2.4 GB at 150 % scaling).
# A pixmap takes width x height x 4 bytes.
GRID_CACHE_MB = 400          # the Library grid: ~3,000 tiles at the default size, ~400 at the largest
CACHE_MB = 200               # every other cache (the photo view's stand-ins, filmstrips, album covers)
MIN_KEPT = 48                # never fewer than this, however big the tiles (a screenful)


def pixmap_bytes(pix: QPixmap) -> int:
    return pix.width() * pix.height() * 4


class _Signals(QObject):
    loaded = Signal(int, int, QImage)   # file_id, generation, image (null = failed)


class _Load(QRunnable):
    def __init__(self, signals: _Signals, file_id: int, path: Path, px: int, gen: int,
                 cancelled) -> None:
        super().__init__()
        self.signals, self.file_id, self.path = signals, file_id, path
        self.px, self.gen, self.cancelled = px, gen, cancelled
        self.setAutoDelete(True)

    def run(self) -> None:
        if self.cancelled(self.gen):
            return
        from lunelis import turns
        self.signals.loaded.emit(self.file_id, self.gen, turns.apply(load_square(self.path, self.px), self.file_id))


def load_square(path: Path, px: int) -> QImage:
    """Decode `path`, cover-crop to a square and scale to px, as a QImage.

    Pillow, not QImageReader: PySide6 holds the GIL through QImageReader's
    decode, so four loader threads decoding made the GUI thread wait - an
    85-150 ms paint stall after every scrollbar jump. Pillow releases the GIL
    while decoding and resizing, so the GUI thread keeps painting.
    """
    try:
        with Image.open(path) as im:
            im.draft("RGB", (px, px))
            im = im.convert("RGB")
            w, h = im.size
            side = min(w, h)
            box = ((w - side) // 2, (h - side) // 2, (w - side) // 2 + side, (h - side) // 2 + side)
            im = im.resize((px, px), Image.Resampling.BILINEAR, box=box, reducing_gap=2.0)
            data = im.tobytes()
        return QImage(data, px, px, 3 * px, QImage.Format.Format_RGB888).copy()
    except Exception:
        return QImage()


def load_image(path: Path, height: int | None = None) -> QImage:
    """A thumbnail as a QImage, decoded by Pillow - safe on any thread. Qt's own
    image plugins aren't (the packaged app's On this day strips came back
    empty when a worker read them with QImage). Null if it can't be read."""
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            if height and im.height != height:
                im = im.resize((max(1, round(im.width * height / im.height)), height), Image.Resampling.BILINEAR)
            w, h = im.size
            data = im.tobytes()
        return QImage(data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
    except Exception:
        return QImage()


class ThumbCache(QObject):
    """file_id -> QPixmap at the current tile size."""

    ready = Signal(int)                 # file_id whose pixmap just arrived

    def __init__(self, cache_dir: Path, parent: QObject | None = None, budget_mb: int = CACHE_MB) -> None:
        super().__init__(parent)
        self.cache_dir = cache_dir
        self.px = 0
        self.gen = 0
        self.budget = budget_mb * 1024 * 1024       # bytes of pixmaps kept
        self._bytes = 0
        self._pix: OrderedDict[int, QPixmap] = OrderedDict()
        # The previous size's pixmaps, drawn scaled while the new size loads -
        # so resizing the grid never flashes grey placeholders.
        self._stale: dict[int, QPixmap] = {}
        self._stale_bytes = 0
        self._failed: set[int] = set()
        self._pending: set[int] = set()
        self._signals = _Signals()
        self._signals.loaded.connect(self._on_loaded)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(4)

    def set_tile_size(self, px: int) -> None:
        if px == self.px:
            return
        self.px = px
        self.gen += 1                    # everything in flight is now stale
        self.pool.clear()
        if self._pix:
            self._stale, self._stale_bytes = dict(self._pix), self._bytes
        self._pix.clear()
        self._bytes = 0
        self._pending.clear()

    def get(self, file_id: int, rel_path: str | None) -> QPixmap | None:
        pix = self._pix.get(file_id)
        if pix is not None:
            self._pix.move_to_end(file_id)
            return pix
        if rel_path and file_id not in self._pending and file_id not in self._failed:
            self._pending.add(file_id)
            self.pool.start(_Load(self._signals, file_id, self.cache_dir / rel_path,
                                  self.px, self.gen, self._is_stale))
        return self._stale.get(file_id)

    def reload(self, file_id: int) -> None:
        """The thumbnail file changed (an edit): load it again, showing the
        old one until the new one arrives."""
        pix = self._pix.pop(file_id, None)
        if pix is not None:
            self._bytes -= pixmap_bytes(pix)
            self._drop_stale(file_id)
            self._stale[file_id] = pix
            self._stale_bytes += pixmap_bytes(pix)
        self._failed.discard(file_id)

    def reset_failed(self) -> None:
        """Forget load failures - after a reload, thumbnails that didn't exist
        yet may have been generated since."""
        self._failed.clear()

    def failed(self, file_id: int) -> bool:
        return file_id in self._failed

    def forget_queue(self) -> None:
        """Drop queued (not yet started) loads - after a fast scroll they're
        for tiles no longer on screen."""
        self.pool.clear()
        self._pending.clear()

    def _is_stale(self, gen: int) -> bool:
        return gen != self.gen

    def _drop_stale(self, file_id: int) -> None:
        gone = self._stale.pop(file_id, None)
        if gone is not None:
            self._stale_bytes -= pixmap_bytes(gone)

    def cached_bytes(self) -> int:
        """What the cache holds now: this size's pixmaps and the stand-ins."""
        return self._bytes + self._stale_bytes

    def _on_loaded(self, file_id: int, gen: int, img: QImage) -> None:
        if gen != self.gen:
            return
        self._pending.discard(file_id)
        if img.isNull():
            self._failed.add(file_id)    # cache file missing/corrupt: show as unavailable
        else:
            old = self._pix.pop(file_id, None)
            if old is not None:
                self._bytes -= pixmap_bytes(old)
            pix = self._pix[file_id] = QPixmap.fromImage(img)
            self._bytes += pixmap_bytes(pix)
            self._drop_stale(file_id)
            # The previous size's stand-ins count too, and go first (oldest
            # first): the ones scrolled away from would otherwise stay for good.
            while self._stale and self._bytes + self._stale_bytes > self.budget:
                self._drop_stale(next(iter(self._stale)))
            while self._bytes > self.budget and len(self._pix) > MIN_KEPT:
                _fid, gone = self._pix.popitem(last=False)
                self._bytes -= pixmap_bytes(gone)
        self.ready.emit(file_id)
