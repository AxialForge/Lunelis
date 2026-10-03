"""
Tray mode and memory-card detection.

Lunelis can keep running in the notification area: closing the window hides
it, and a newly inserted memory card (a removable drive with a DCIM or
PRIVATE folder) pops a notification that opens the Import page.

"Start with Windows" writes a per-user Run entry
(HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run) - only when the user
ticks it - so Lunelis starts hidden in the tray at sign-in.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QObject, QTimer, Signal

from lunelis.importing.ingest import removable_drives_with_media

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "Lunelis"


class CardWatcher(QObject):
    """Polls for removable drives with camera media (every 2 s: drive polling
    is a handful of API calls, and needs no window-message plumbing)."""

    inserted = Signal(str)          # drive root, e.g. "F:\\"
    removed = Signal(str)

    def __init__(self, parent=None, interval_ms: int = 2000) -> None:
        super().__init__(parent)
        self.known = set(removable_drives_with_media())
        self._timer = QTimer(self, interval=interval_ms, timeout=self.poll)
        self._timer.start()

    def poll(self) -> None:
        now = set(removable_drives_with_media())
        for d in sorted(now - self.known):
            self.inserted.emit(d)
        for d in sorted(self.known - now):
            self.removed.emit(d)
        self.known = now


def autostart_command() -> str:
    """How Windows should start Lunelis at sign-in: windowless, into the tray."""
    from lunelis.paths import launch_command
    program, args = launch_command("--tray")
    return " ".join([f'"{program}"', *args])


def autostart_enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
            return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> None:
    if sys.platform != "win32":
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if enabled:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except OSError:
                pass
