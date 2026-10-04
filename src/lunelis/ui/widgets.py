"""Small shared widget helpers."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidget


def row_toggles(lw: QListWidget) -> None:
    """A tick-box list where clicking anywhere on a row (or Space/Enter) ticks
    or unticks it - not just the 16-pixel box. Items keep their check state
    but aren't Qt-"user checkable", so one click is one toggle."""
    def flip(item) -> None:
        if item is None or item.data(Qt.ItemDataRole.CheckStateRole) is None:
            return
        on = item.checkState() == Qt.CheckState.Checked
        item.setCheckState(Qt.CheckState.Unchecked if on else Qt.CheckState.Checked)

    lw.itemClicked.connect(flip)
    lw.itemActivated.connect(flip)
    lw.setCursor(Qt.CursorShape.PointingHandCursor)


def plain(e: BaseException) -> str:
    """An error in words for a dialog. Lunelis's own errors already are; the
    system's ("[WinError 32] The process cannot access the file...") get a
    sentence, with the file, and the details stay in the log."""
    import errno
    import logging
    import sqlite3
    if isinstance(e, sqlite3.OperationalError) and "locked" in str(e):
        return "The catalog was busy with something else for too long. Try again in a moment."
    if not isinstance(e, OSError) or type(e).__module__ not in ("builtins",):
        return str(e)
    logging.getLogger("lunelis.errors").info("shown as plain words: %r", e)
    where = f" ({e.filename})" if getattr(e, "filename", None) else ""
    win = getattr(e, "winerror", None)
    if isinstance(e, FileNotFoundError) or e.errno == errno.ENOENT:
        return f"It isn't there any more{where}."
    if isinstance(e, PermissionError) or win in (5, 32, 33):
        return (f"Windows wouldn't let Lunelis use it{where} - another program may have it open, "
                "or it's read-only.")
    if win in (53, 64, 67, 121, 1231) or e.errno in (errno.EHOSTUNREACH, errno.ETIMEDOUT):
        return f"The network drive didn't answer{where}. Is the NAS awake and connected?"
    if win == 112 or e.errno == errno.ENOSPC:
        return f"The drive is full{where}."
    if win in (21, 1005):
        return f"The drive isn't ready{where} - was it removed?"
    return f"{e.strerror or e}{where}."


def sharp(pm, edge: int, widget):
    """`pm` scaled to fit `edge` x `edge` on screen, with the screen's pixels:
    on a 150 % display a 72-pixel thumbnail is 108 real pixels, not 72 blown up."""
    from PySide6.QtCore import Qt
    ratio = max(widget.devicePixelRatioF() if widget is not None else 1.0, 1.0)
    out = pm.scaled(round(edge * ratio), round(edge * ratio), Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
    out.setDevicePixelRatio(ratio)
    return out
