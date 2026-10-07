"""
Turning a photo or video without editing it.

Photo > Rotate left / right (Ctrl+[ / Ctrl+]) stores a quarter turn in the
catalog - the file isn't touched, no edit is made, and the edit history is
left alone. Thumbnails, the photo view, the filmstrip and the video player
all show it turned. Rotating back to upright removes the entry.

The handful of turned files is kept in memory (`_map`) so painting a tile
never asks the database.
"""
from __future__ import annotations

import sqlite3

_map: dict[int, int] = {}            # file id -> quarter turns clockwise (1-3)


def load(conn: sqlite3.Connection) -> None:
    _map.clear()
    _map.update({fid: q for fid, q in conn.execute("SELECT file_id, quarter FROM turns")})


def get(file_id: int) -> int:
    return _map.get(file_id, 0)


def turn(conn: sqlite3.Connection, ids: list[int], step: int) -> None:
    """Turn these files a quarter clockwise (step 1) or anticlockwise (-1)."""
    for fid in ids:
        q = (get(fid) + step) % 4
        if q:
            conn.execute("INSERT INTO turns (file_id, quarter) VALUES (?, ?)"
                         " ON CONFLICT(file_id) DO UPDATE SET quarter = excluded.quarter", (fid, q))
            _map[fid] = q
        else:
            conn.execute("DELETE FROM turns WHERE file_id = ?", (fid,))
            _map.pop(fid, None)
    conn.commit()


def apply(image, file_id: int):
    """The QImage or QPixmap turned as the file is (the same object when upright)."""
    q = get(file_id)
    if not q or image is None or image.isNull():
        return image
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QTransform
    return image.transformed(QTransform().rotate(90 * q), Qt.TransformationMode.SmoothTransformation)
