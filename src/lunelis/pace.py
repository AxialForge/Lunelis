"""
Background work gives way while a video plays.

Video frames reach the screen through the window's own thread, and Python
runs one thread's bytecode at a time: a library pass, a thumbnail batch or a
job working through a big library left playback at a few frames a second.
While a clip plays, `breathe()` - called between files by every background
pass and job - waits, so the video gets the machine; when it's paused or left,
the work carries on where it was.
"""
from __future__ import annotations

import threading
import time

_playing = threading.Event()
MAX_WAIT_S = 600.0                 # a forgotten, looping clip mustn't stall the library for ever


def set_playing(on: bool) -> None:
    if on:
        _playing.set()
    else:
        _playing.clear()


def playing() -> bool:
    return _playing.is_set()


def breathe(should_stop=None) -> None:
    """Wait while a video plays (or until `should_stop()` / MAX_WAIT_S). Cheap
    when nothing plays: one flag check."""
    if not _playing.is_set() or threading.current_thread() is threading.main_thread():
        return
    end = time.monotonic() + MAX_WAIT_S
    while _playing.is_set() and time.monotonic() < end:
        if should_stop is not None and should_stop():
            return
        time.sleep(0.1)
