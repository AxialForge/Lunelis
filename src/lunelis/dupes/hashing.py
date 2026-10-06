"""
File hashing for duplicate detection.

sample_hash  sha256 of the size and three 64 KB slices (start, middle, end):
             ~200 KB read per file instead of the whole 20-40 MB. Two files
             with the same sample hash are *likely* identical - photos differ
             everywhere, so a shared size plus identical slices almost never
             happens by chance - but nothing is removed on a sample alone.
full_hash    sha256 of every byte. Required before a group can be acted on.

Both take an optional Throttle so a job can be capped in MB/s.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time

SLICE = 64 * 1024
CHUNK = 1024 * 1024

# WinError codes that mean "the network share went away", not "this file is bad".
NETWORK_ERRORS = {53, 59, 64, 67, 121, 1231, 1232, 2250}


class SourceOffline(OSError):
    """The file's folder is unreachable (NAS asleep, drive unplugged)."""


def is_network_error(e: OSError) -> bool:
    return getattr(e, "winerror", None) in NETWORK_ERRORS


OFFLINE = "offline: "                  # an error that says nothing about the file itself


def offline_error(e: BaseException, root: str) -> bool:
    """The share (or drive) went away, rather than this file being bad: the
    file is left pending and tried again, never marked as unreadable."""
    if not isinstance(e, OSError):
        return False
    return is_network_error(e) or not os.path.isdir(root)


class Throttle:
    """A shared MB/s cap across worker threads (None = unlimited)."""

    def __init__(self, mb_per_s: float | None) -> None:
        self.rate = mb_per_s * 1e6 if mb_per_s else None
        self._lock = threading.Lock()
        self._next = time.monotonic()

    def spend(self, nbytes: int) -> None:
        if not self.rate:
            return
        with self._lock:
            now = time.monotonic()
            self._next = max(self._next, now) + nbytes / self.rate
            wait = self._next - now - 0.25          # allow a small burst
        if wait > 0:
            time.sleep(wait)


def _open(path: str):
    try:
        return open(path, "rb")
    except OSError as e:
        if is_network_error(e):
            raise SourceOffline(e.errno, e.strerror, path) from e
        raise


def sample_hash(path: str, size: int, throttle: Throttle | None = None) -> tuple[str, int]:
    """(hex digest, bytes read). Files up to three slices long are read whole."""
    h = hashlib.sha256(f"{size}:".encode())
    read = 0
    with _open(path) as fh:
        if size <= 3 * SLICE:
            data = fh.read()
            h.update(data)
            read = len(data)
        else:
            for offset in (0, size // 2 - SLICE // 2, size - SLICE):
                fh.seek(offset)
                data = fh.read(SLICE)
                h.update(data)
                read += len(data)
    if throttle:
        throttle.spend(read)
    return h.hexdigest(), read


def full_hash(path: str, throttle: Throttle | None = None,
              should_cancel=None) -> tuple[str | None, int]:
    """(hex digest, bytes read); digest None if cancelled part-way."""
    h = hashlib.sha256()
    read = 0
    with _open(path) as fh:
        while True:
            try:
                chunk = fh.read(CHUNK)
            except OSError as e:           # the share dropped mid-file
                if is_network_error(e):
                    raise SourceOffline(e.errno, e.strerror, path) from e
                raise
            if not chunk:
                break
            h.update(chunk)
            read += len(chunk)
            if throttle:
                throttle.spend(len(chunk))
            if should_cancel and should_cancel():
                return None, read
    return h.hexdigest(), read
