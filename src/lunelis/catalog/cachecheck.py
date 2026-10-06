"""
The caches belong to one catalog.

Thumbnails, edit previews, face crops and mask maps are stored by the
catalog's file / face numbers (`cache/thumbnails/0012/12819.jpg`). A different
catalog - a fresh install, a restored or older one - numbers its photos from
1 again, so the old pictures turned up beside the wrong photos: the grid and
the filmstrip showed one photo while the photo view showed another.

Each catalog now carries its own id (settings `catalog_uuid`) and the cache
folder records which catalog it was made for (`cache/catalog.id`). When they
differ, or the cache has no record (made before 0.37.7), the number-keyed
caches are set aside and rebuilt: every thumbnail is made again by the next
library pass.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path

KEYED = ("thumbnails", "edits", "faces", "masks")      # folders named by catalog numbers
MARKER = "catalog.id"


def catalog_id(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = 'catalog_uuid'").fetchone()
    if row:
        return json.loads(row[0])
    cid = uuid.uuid4().hex
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('catalog_uuid', ?)", (json.dumps(cid),))
    conn.commit()
    return cid


def ensure(conn: sqlite3.Connection, data_dir: Path) -> bool:
    """Make the caches match this catalog. True when they had to be set aside
    (the thumbnails are then rebuilt by the next library pass)."""
    cache = Path(data_dir) / "cache"
    for left in cache.glob("old-*"):                 # a removal cut short by closing Lunelis
        threading.Thread(target=shutil.rmtree, args=(left,), kwargs={"ignore_errors": True}, daemon=True).start()
    cid = catalog_id(conn)
    marker = cache / MARKER
    try:
        if marker.read_text(encoding="ascii").strip() == cid:
            return False
    except OSError:
        pass
    stale = [cache / k for k in KEYED if (cache / k).is_dir() and any((cache / k).iterdir())]
    if stale:
        aside = cache / f"old-{datetime.now():%Y%m%d-%H%M%S}"
        aside.mkdir(parents=True, exist_ok=True)
        for d in stale:
            os.replace(d, aside / d.name)            # a rename: instant, even for 160k files
        # Lunelis's own cache, not your photos: removed in the background.
        threading.Thread(target=shutil.rmtree, args=(aside,), kwargs={"ignore_errors": True},
                         daemon=True).start()
    conn.execute("UPDATE files SET thumbnail_path = NULL, thumb_error = NULL WHERE thumbnail_path IS NOT NULL"
                 " OR thumb_error IS NOT NULL")
    conn.commit()
    cache.mkdir(parents=True, exist_ok=True)
    tmp = marker.with_suffix(".tmp")
    tmp.write_text(cid, encoding="ascii")
    os.replace(tmp, marker)
    return bool(stale)
