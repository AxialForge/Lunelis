"""
The log: `<data dir>/logs/lunelis.log` (rotated, 5 x 2 MB).

Startup, the data folder, version, background work finishing, and every
error - including crashes anywhere (uncaught exceptions on the GUI thread,
worker threads, and Qt's own warnings) - so a problem on someone's PC can be
looked at afterwards. Help > Report a problem collects it.
"""
from __future__ import annotations

import logging
import logging.handlers
import platform
import sys
import threading
from pathlib import Path

LOG = logging.getLogger("lunelis")
_crash_hook = None                     # set by the GUI: shows a dialog after logging


def log_dir() -> Path:
    from lunelis import paths
    return Path(paths.DATA_DIR) / "logs"


def log_file() -> Path:
    return log_dir() / "lunelis.log"


def setup(level: str = "INFO") -> None:
    """Idempotent. Called once at start-up (after a data-folder move)."""
    if any(isinstance(h, logging.handlers.RotatingFileHandler) for h in LOG.handlers):
        return
    try:
        log_dir().mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(log_file(), maxBytes=2_000_000, backupCount=5,
                                                       encoding="utf-8")
    except OSError:
        return                         # read-only data folder: run without a log rather than not at all
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(threadName)s %(name)s: %(message)s"))
    LOG.addHandler(handler)
    LOG.setLevel(getattr(logging, level.upper(), logging.INFO))
    sys.excepthook = _excepthook
    threading.excepthook = lambda a: _excepthook(a.exc_type, a.exc_value, a.exc_traceback, a.thread.name)
    try:
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler

        def qt_messages(kind, context, message):
            if kind in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
                LOG.warning("Qt: %s", message)
        qInstallMessageHandler(qt_messages)
    except ImportError:
        pass


def _excepthook(exc_type, exc, tb, thread: str | None = None) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    LOG.error("Unhandled error%s", f" in {thread}" if thread else "", exc_info=(exc_type, exc, tb))
    if _crash_hook is not None and thread is None:
        try:
            _crash_hook(exc_type, exc)
        except Exception:              # the dialog itself must never crash the app
            pass


def set_crash_hook(fn) -> None:
    global _crash_hook
    _crash_hook = fn


def tail(lines: int = 300) -> str:
    try:
        text = log_file().read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "(no log yet)"
    return "\n".join(text[-lines:])


def diagnostics(conn=None) -> str:
    """What to paste into a bug report: versions, folders, library size, recent log."""
    from lunelis import paths
    parts = [f"Lunelis {paths.version()} ({'packaged' if paths.FROZEN else 'from source'})",
             f"Windows {platform.version()} / Python {platform.python_version()}",
             f"Data folder: {paths.DATA_DIR}"]
    try:
        import PySide6
        import rawpy
        parts.append(f"PySide6 {PySide6.__version__}, LibRaw {'.'.join(map(str, rawpy.libraw_version))}")
    except Exception:
        pass
    if conn is not None:
        try:
            from lunelis.importers.scan import catalog_stats
            s = catalog_stats(conn)
            v = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
            parts.append(f"Catalog schema v{v}: {s['files']:,} files in {s['roots']} sources, "
                         f"{s['missing']:,} missing")
        except Exception as e:
            parts.append(f"Catalog: {e}")
    parts += ["", "--- recent log ---", tail(200)]
    return "\n".join(parts)
