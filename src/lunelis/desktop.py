"""
Opening things in the system's file manager / default app: Explorer on
Windows, Finder on the Mac (xdg-open elsewhere). UI code calls these instead
of os.startfile / explorer / notepad, which exist on Windows only.
"""
from __future__ import annotations

import os
import subprocess
import sys


def open_path(path: str) -> None:
    """Open a folder in Explorer / Finder, or a file in its default app."""
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def reveal(path: str) -> None:
    """Show a file selected in its folder."""
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path)])


def open_text(path: str) -> None:
    """Open a text file (a log) in the plain text editor."""
    if sys.platform == "win32":
        subprocess.Popen(["notepad.exe", path])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-t", path])
    else:
        subprocess.Popen(["xdg-open", path])
