"""
Is a source folder reachable right now - and, when a file operation fails,
saying so in words.

A sleeping NAS can take 30+ seconds to fail a directory listing, so
`reachable()` asks on a helper thread and gives up after `timeout` seconds
(treating a slow answer as "offline for now").

`explain(e, action, path)` turns a failure into a sentence. When the file's
drive or share has stopped answering (a network error, or its folder is
gone), it says that - and that nothing was changed - instead of a bare
"[WinError 64] The specified network name is no longer available".
"""
from __future__ import annotations

import os
import threading


def reachable(path: str, timeout: float = 4.0) -> bool:
    result: list[bool] = []

    def ask() -> None:
        try:
            result.append(os.path.isdir(path))
        except OSError:
            result.append(False)

    t = threading.Thread(target=ask, daemon=True)
    t.start()
    t.join(timeout)
    return bool(result and result[0])


def _root_of(path: str) -> str:
    """The share (\\\\server\\share) or drive (D:\\) a path lives on."""
    p = os.path.normpath(path)
    if p.startswith("\\\\"):
        parts = p.split("\\")
        return "\\\\" + "\\".join(parts[2:4])
    drive, _ = os.path.splitdrive(p)
    return drive + "\\" if drive else p


def gone_offline(e: BaseException, path: str | None = None) -> bool:
    from lunelis.dupes.hashing import SourceOffline, is_network_error
    if isinstance(e, SourceOffline):
        return True
    if isinstance(e, OSError) and is_network_error(e):
        return True
    if path and isinstance(e, (FileNotFoundError, OSError)):
        return not reachable(_root_of(path), timeout=2.0)
    return False


def explain(e: BaseException, action: str, path: str | None = None) -> str:
    """'<action> failed: ...' in words; a vanished drive or NAS named as such."""
    if gone_offline(e, path):
        where = _root_of(path) if path else "the drive or NAS"
        return (f"{where} stopped answering while {action}. Nothing was changed - "
                "try again when it's back (a sleeping NAS can take a minute to wake).")
    text = getattr(e, "strerror", None) or str(e) or type(e).__name__
    return f"Couldn't finish {action}: {text}"
