"""
Catalog snapshots.

Picks, events, albums and (later) faces live only in the catalog - XMP can't
hold them - so the catalog is snapshotted automatically: once per
`catalog_backup_every_hours` on start-up, and before every job that moves or
removes files. A snapshot is `VACUUM INTO` (a consistent, compacted copy made
while the catalog stays open), then zipped; the newest `catalog_backups_keep`
are kept.

    python -m lunelis.catalog.backup            # list snapshots
    python -m lunelis.catalog.backup --now      # take one
    python -m lunelis.catalog.backup --restore catalog-20260927-081500-daily.zip
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import sys
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from lunelis.settings import Settings

_NAME = re.compile(r"^catalog-(\d{8}-\d{6})-([a-z0-9-]+)\.zip$")


def backup_dir(settings: Settings, data_dir: Path) -> Path:
    from lunelis import lunelis_folder
    return Path(settings.get("catalog_backup_dir")
                or lunelis_folder.path(settings, lunelis_folder.BACKUPS_CATALOG) or data_dir / "backups")


def list_snapshots(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted((p for p in folder.iterdir() if _NAME.match(p.name)), reverse=True)


def snapshot(conn: sqlite3.Connection, folder: Path, reason: str = "manual",
             keep: int | None = None) -> Path:
    """Write a zipped, consistent copy of the open catalog. Returns its path."""
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    reason = re.sub(r"[^a-z0-9-]+", "-", reason.lower()).strip("-") or "manual"
    tmp_db = folder / f".tmp-{stamp}-{os.getpid()}.db"
    final = folder / f"catalog-{stamp}-{reason}.zip"
    try:
        conn.execute("VACUUM INTO ?", (str(tmp_db),))
        tmp_zip = final.with_suffix(".zip.tmp")
        with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            z.write(tmp_db, "catalog.db")
        os.replace(tmp_zip, final)
    finally:
        for leftover in (tmp_db, final.with_suffix(".zip.tmp")):
            try:
                leftover.unlink()
            except OSError:
                pass
    if keep is None:
        keep = Settings(conn).get("catalog_backups_keep")
    for old in list_snapshots(folder)[keep:]:
        old.unlink()
    return final


def last_snapshot_time(folder: Path) -> datetime | None:
    for p in list_snapshots(folder):
        return datetime.strptime(_NAME.match(p.name).group(1), "%Y%m%d-%H%M%S")
    return None


def snapshot_if_due(conn: sqlite3.Connection, data_dir: Path) -> Path | None:
    """The daily snapshot: taken only if the newest one is older than the setting."""
    s = Settings(conn)
    folder = backup_dir(s, data_dir)
    last = last_snapshot_time(folder)
    if last and datetime.now() - last < timedelta(hours=s.get("catalog_backup_every_hours")):
        return None
    return snapshot(conn, folder, "daily")


def restore(snapshot_zip: Path, catalog_path: Path) -> Path:
    """Replace the catalog with a snapshot. The app must be closed. The catalog
    being replaced is itself kept alongside as `catalog.db.before-restore-<time>`."""
    with zipfile.ZipFile(snapshot_zip) as z:
        tmp = catalog_path.with_suffix(".db.restoring")
        with z.open("catalog.db") as src, open(tmp, "wb") as dst:
            while chunk := src.read(1 << 20):
                dst.write(chunk)
    check = sqlite3.connect(str(tmp))
    try:
        ok = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()                    # Windows can't replace a file that's open
    if ok != "ok":
        tmp.unlink()
        raise ValueError(f"snapshot failed its integrity check: {ok}")
    if catalog_path.exists():
        # Copied, not moved, aside: catalog.db exists at every moment, so a
        # crash here can never leave Lunelis to start on an empty catalog.
        keep = catalog_path.with_name(f"{catalog_path.name}.before-restore-{int(time.time())}")
        shutil.copy2(catalog_path, keep)
        for suffix in ("-wal", "-shm"):
            side = Path(str(catalog_path) + suffix)
            if side.exists():
                os.replace(side, Path(str(keep) + suffix))
    os.replace(tmp, catalog_path)
    # An older catalog may number later photos differently: its thumbnails are
    # rebuilt (catalog/cachecheck.py) rather than trusted.
    try:
        (catalog_path.parent / "cache" / "catalog.id").unlink()
    except OSError:
        pass
    return catalog_path


def problem(catalog_path: Path) -> str | None:
    """Why the catalog can't be opened ("empty", "damaged: ..."), or None when
    it's fine or doesn't exist yet. Read-only: nothing is written or checkpointed."""
    if not catalog_path.exists():
        return None
    if catalog_path.stat().st_size == 0:
        return "empty"
    try:
        c = sqlite3.connect(catalog_path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            c.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()     # the header and schema read
        finally:
            c.close()
    except sqlite3.DatabaseError as e:
        return f"damaged: {e}"
    return None


def set_aside_damaged(catalog_path: Path) -> Path:
    """Move a damaged catalog (and its -wal/-shm) out of the way, kept."""
    keep = catalog_path.with_name(f"{catalog_path.name}.damaged-{datetime.now():%Y%m%d-%H%M%S}")
    os.replace(catalog_path, keep)
    for suffix in ("-wal", "-shm"):
        side = Path(str(catalog_path) + suffix)
        if side.exists():
            os.replace(side, Path(str(keep) + suffix))
    return keep


PENDING_RESTORE = "restore-pending.json"


def request_restore(data_dir: Path, snapshot_zip: Path) -> None:
    """Restore `snapshot_zip` at the next start (the catalog can't be swapped
    while the app has it open). The zip is checked now so a bad pick fails
    here, not at start-up."""
    with zipfile.ZipFile(snapshot_zip) as z:
        if "catalog.db" not in z.namelist():
            raise ValueError(f"{snapshot_zip.name} isn't a Lunelis catalog backup")
    (data_dir / PENDING_RESTORE).write_text(json.dumps({"zip": str(snapshot_zip)}), encoding="utf-8")


def finish_pending_restore(data_dir: Path, catalog_path: Path) -> Path | None:
    """Called at start-up before the catalog is opened. Returns the snapshot
    restored, if any. The request is cleared either way, so a broken zip
    can't stop Lunelis from starting."""
    marker = data_dir / PENDING_RESTORE
    if not marker.exists():
        return None
    try:
        src = Path(json.loads(marker.read_text(encoding="utf-8"))["zip"])
    finally:
        marker.unlink()
    restore(src, catalog_path)
    return src


def _main(argv: list[str]) -> int:
    import argparse

    from lunelis.catalog.schema import open_catalog
    from lunelis.paths import DATA_DIR, DEFAULT_CATALOG_PATH

    ap = argparse.ArgumentParser(prog="python -m lunelis.catalog.backup")
    ap.add_argument("--now", action="store_true", help="take a snapshot now")
    ap.add_argument("--restore", metavar="ZIP", help="restore a snapshot (close Lunelis first)")
    args = ap.parse_args(argv)
    if args.restore:
        conn = open_catalog(DEFAULT_CATALOG_PATH)
        folder = backup_dir(Settings(conn), DATA_DIR)
        conn.close()
        src = Path(args.restore)
        if not src.is_absolute():
            src = folder / src
        restore(src, DEFAULT_CATALOG_PATH)
        print(f"restored {src.name} -> {DEFAULT_CATALOG_PATH}")
        return 0
    conn = open_catalog(DEFAULT_CATALOG_PATH)
    try:
        folder = backup_dir(Settings(conn), DATA_DIR)
        if args.now:
            t = time.perf_counter()
            p = snapshot(conn, folder, "manual")
            print(f"{p}  ({p.stat().st_size / 1e6:.0f} MB, {time.perf_counter() - t:.1f}s)")
        for p in list_snapshots(folder):
            print(f"{p.name}  {p.stat().st_size / 1e6:8.1f} MB")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
