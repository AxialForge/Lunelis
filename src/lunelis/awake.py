"""
Keep the PC awake while a background job runs (0.55). Windows put the PC to
sleep in the middle of a five-hour migration: nobody touches the mouse during
a long copy, and a sleeping PC copies nothing. The screen may still turn off;
only sleep is held back, and only for as long as a job is running.
"""
from __future__ import annotations

import sys
from contextlib import contextmanager

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def _set(flags: int) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(ctypes.c_uint(flags))
    except Exception:                      # never a reason for a job to fail
        pass


@contextmanager
def awake():
    """Holds sleep back for the calling thread until the block ends."""
    _set(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    try:
        yield
    finally:
        _set(ES_CONTINUOUS)
