"""
Slow reads off the GUI thread, for pages.

    self.bg = Background(self, conn)
    self.bg.run("figures", load_figures, self._show_figures)

- `fn(conn)` runs on a worker thread with its own connection to the same
  catalog file (`db=False`: `fn()` with no catalog at all). An in-memory
  catalog can't be opened twice, so there it runs inline.
- One run per key at a time. Asking again while one runs queues a single
  re-run with the newest request, so a burst of refreshes costs two loads at
  most and the page always ends up showing current figures.
- `then(result)` runs on the GUI thread - never after the page is gone or the
  window has closed the catalog. An exception goes to `error(exc)` if given,
  otherwise to the log.
- The threads are children of the page, so MainWindow._finish_threads waits
  for them on close (a QThread destroyed while running aborts the process).
- `wait()` (tests, or before a page acts on the newest figures) runs the event
  loop until nothing is pending.

The worker connection only sees committed data: write, commit, then refresh.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import QApplication

log = logging.getLogger(__name__)


def db_file(conn) -> str:
    """The catalog file this connection is on ("" for an in-memory one)."""
    return conn.execute("PRAGMA database_list").fetchone()[2] or ""


class _Job(QObject):
    done = Signal(str, object, bool)          # key, result or exception, failed

    def __init__(self, key: str, fn: Callable, db: str | None) -> None:
        super().__init__()
        self.key, self.fn, self.db = key, fn, db

    def run(self) -> None:
        try:
            if self.db is None:
                result = self.fn()
            else:
                from lunelis.catalog.schema import open_catalog
                conn = open_catalog(self.db)
                try:
                    result = self.fn(conn)
                finally:
                    conn.close()
        except Exception as e:                # handed back, never lost
            self.done.emit(self.key, e, True)
            return
        self.done.emit(self.key, result, False)


class Background(QObject):
    def __init__(self, page, conn=None) -> None:
        super().__init__(page)
        self.page, self.conn = page, conn
        self._running: dict[str, tuple[QThread, _Job]] = {}
        self._queued: dict[str, tuple] = {}
        self._handlers: dict[str, tuple] = {}

    def busy(self, key: str | None = None) -> bool:
        return bool(self._running) if key is None else key in self._running

    def run(self, key: str, fn: Callable, then: Callable, error: Callable | None = None,
            db: bool = True) -> None:
        if self._closed():
            return
        if key in self._running:
            self._queued[key] = (fn, then, error, db)
            return
        path = None
        if db:
            try:
                path = db_file(self.conn)
            except Exception:                 # the catalog was closed under us: nothing to show
                return
            if not path:                      # in-memory: only this connection can see it
                self._deliver(then, error, *self._inline(fn))
                return
        thread = QThread(self.page)
        job = _Job(key, fn, path)
        job.moveToThread(thread)
        thread.started.connect(job.run)
        job.done.connect(self._done)          # queued: this object lives on the GUI thread
        self._running[key] = (thread, job)
        self._handlers[key] = (then, error)
        thread.start()

    def _inline(self, fn) -> tuple[object, bool]:
        try:
            return fn(self.conn), False
        except Exception as e:
            return e, True

    @Slot(str, object, bool)
    def _done(self, key: str, result, failed: bool) -> None:
        thread, _job = self._running.pop(key)
        thread.quit()
        thread.wait()
        thread.deleteLater()
        then, error = self._handlers.pop(key)
        if key in self._queued:               # a newer request came in: its result is the one to show
            self.run(key, *self._queued.pop(key))
            return
        self._deliver(then, error, result, failed)

    def _deliver(self, then, error, result, failed: bool) -> None:
        if self._closed():
            return
        if failed:
            if error is not None:
                error(result)
            else:
                log.error("background read failed", exc_info=result)
            return
        then(result)

    def _closed(self) -> bool:
        try:
            win = self.page.window()
        except RuntimeError:                  # the page's C++ side is gone
            return True
        return bool(getattr(win, "_closed", False) or getattr(self.page, "_closed", False))

    def wait(self, timeout: float = 30.0) -> None:
        end = time.monotonic() + timeout
        while (self._running or self._queued) and time.monotonic() < end:
            QApplication.processEvents()
            for thread, _job in list(self._running.values()):
                thread.wait(10)
        QApplication.processEvents()
