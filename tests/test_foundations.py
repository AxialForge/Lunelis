"""Step 7: data location, settings, catalog snapshots, compressed EXIF migration."""
import json
import sqlite3
import zipfile
from datetime import datetime, timedelta

import pytest

from lunelis import paths
from lunelis.catalog import backup, schema
from lunelis.catalog.exifblob import pack, unpack, unpack_dict
from lunelis.catalog.schema import MIGRATIONS, current_version, migrate, open_catalog
from lunelis.settings import DEFAULTS, Settings


# --- data location --------------------------------------------------------------

def test_tests_never_use_the_real_data_folder():
    assert "lunelis-test-data-" in str(paths.DATA_DIR)          # set by conftest.py


def test_adopt_legacy_data_moves_once_and_never_overwrites(tmp_path):
    project, data = tmp_path / "project", tmp_path / "data"
    (project / "cache" / "thumbnails" / "0000").mkdir(parents=True)
    (project / "cache" / "thumbnails" / "0000" / "1.jpg").write_bytes(b"jpg")
    (project / "cache" / "thumbnails" / ".gitkeep").write_bytes(b"")
    (project / "lunelis.db").write_bytes(b"db")
    (project / "lunelis.db-wal").write_bytes(b"wal")

    moved = paths.adopt_legacy_data(data, project)
    assert set(moved) == {"lunelis.db", "lunelis.db-wal", "cache/thumbnails"}
    assert (data / "catalog.db").read_bytes() == b"db"
    assert (data / "catalog.db-wal").read_bytes() == b"wal"
    assert (data / "cache" / "thumbnails" / "0000" / "1.jpg").exists()
    assert (project / "cache" / "thumbnails" / ".gitkeep").exists()   # repo placeholder stays

    (project / "lunelis.db").write_bytes(b"newer dev db")
    assert paths.adopt_legacy_data(data, project) == []                   # data folder wins
    assert (data / "catalog.db").read_bytes() == b"db"


def test_location_file_is_honoured(tmp_path, monkeypatch):
    monkeypatch.delenv("LUNELIS_DATA_DIR")
    monkeypatch.setattr(paths, "LOCATION_FILE", tmp_path / "location.json")
    paths.set_data_dir(tmp_path / "BigDrive" / "Lunelis")
    assert paths.resolve_data_dir() == tmp_path / "BigDrive" / "Lunelis"


# --- settings -----------------------------------------------------------------------

def test_settings_defaults_and_round_trip(tmp_path):
    conn = open_catalog(tmp_path / "c.db")
    s = Settings(conn)
    assert s.get("sidecar_mode") == "central" and s.get("update_existing_sidecars") is True
    s.set("sidecar_mode", "beside")
    s.set("catalog_backups_keep", 3)
    assert Settings(conn).all() == {**DEFAULTS, "sidecar_mode": "beside", "catalog_backups_keep": 3}
    s.reset("sidecar_mode")
    assert s.get("sidecar_mode") == "central"
    with pytest.raises(ValueError):
        s.set("sidecar_mode", "somewhere")
    with pytest.raises(KeyError):
        s.get("no_such_setting")


# --- snapshots -------------------------------------------------------------------------

def test_snapshot_keep_and_restore(tmp_path):
    db = tmp_path / "catalog.db"
    conn = open_catalog(db)
    conn.execute("INSERT INTO roots (path) VALUES (?)", ("C:\\Photos",))
    conn.commit()
    folder = tmp_path / "backups"
    Settings(conn).set("catalog_backups_keep", 2)
    first = backup.snapshot(conn, folder, "before migration")
    assert first.name.endswith("-before-migration.zip")
    with zipfile.ZipFile(first) as z:
        assert z.namelist() == ["catalog.db"]

    for i in range(3):
        (folder / f"catalog-2020010{i + 1}-000000-daily.zip").write_bytes(b"old")
    backup.snapshot(conn, folder)
    assert len(backup.list_snapshots(folder)) == 2                       # pruned to "keep"

    conn.execute("DELETE FROM roots")                                     # an accident...
    conn.commit()
    conn.close()
    newest = backup.list_snapshots(folder)[0]
    backup.restore(newest, db)                                           # ...undone
    conn = open_catalog(db)
    assert conn.execute("SELECT path FROM roots").fetchone()[0] == "C:\\Photos"
    assert list(tmp_path.glob("catalog.db.before-restore-*"))           # replaced one kept
    conn.close()


def test_daily_snapshot_only_when_due(tmp_path):
    conn = open_catalog(tmp_path / "catalog.db")
    Settings(conn).set("catalog_backup_dir", str(tmp_path / "b"))
    assert backup.snapshot_if_due(conn, tmp_path) is not None
    assert backup.snapshot_if_due(conn, tmp_path) is None                # taken moments ago
    old = datetime.now() - timedelta(hours=30)
    for p in backup.list_snapshots(tmp_path / "b"):
        p.rename(p.with_name(f"catalog-{old:%Y%m%d-%H%M%S}-daily.zip"))
    assert backup.snapshot_if_due(conn, tmp_path) is not None
    conn.close()


# --- migrations -----------------------------------------------------------------------

def test_raw_exif_migration_compresses_existing_rows(tmp_path, monkeypatch):
    db = tmp_path / "old.db"
    upto7 = [m for m in MIGRATIONS if m[0] <= 7]
    monkeypatch.setattr(schema, "MIGRATIONS", upto7)
    migrate(db)
    raw = json.dumps({"EXIF LensModel": "FE 24-105mm F4 G OSS", "MakerNote FocusMode": "AF-C"})
    c = sqlite3.connect(db)
    c.execute("INSERT INTO roots (id, path) VALUES (1, 'C:\\\\P')")
    c.execute("INSERT INTO files (id, root_id, rel_path, filename, ext, size_bytes, mtime)"
              " VALUES (1, 1, 'a.jpg', 'a.jpg', 'jpg', 1, 'x')")
    c.execute("INSERT INTO exif (file_id, camera_model, raw_json) VALUES (1, 'ILCE-7RM5', ?)", (raw,))
    c.commit()
    c.close()

    monkeypatch.undo()
    assert migrate(db) == MIGRATIONS[-1][0]
    c = sqlite3.connect(db)
    cols = [r[1] for r in c.execute("PRAGMA table_info(exif)")]
    assert "raw_json" not in cols and "raw_exif" in cols
    blob = c.execute("SELECT raw_exif FROM exif").fetchone()[0]
    assert unpack_dict(blob)["MakerNote FocusMode"] == "AF-C"
    c.close()


def test_failed_python_migration_rolls_back(tmp_path, monkeypatch):
    db = tmp_path / "c.db"
    latest = migrate(db)

    def boom(conn):
        conn.execute("CREATE TABLE half_done (x)")
        raise RuntimeError("disk full")

    monkeypatch.setattr(schema, "MIGRATIONS", MIGRATIONS + [(latest + 1, "boom", boom)])
    with pytest.raises(RuntimeError):
        migrate(db)
    c = sqlite3.connect(db)
    assert current_version(c) == latest
    assert not c.execute("SELECT 1 FROM sqlite_master WHERE name = 'half_done'").fetchone()
    c.close()


def test_pack_round_trip():
    assert pack(None) is None and unpack(None) is None
    assert unpack(pack('{"a":"é"}')) == '{"a":"é"}'


def test_launch_command_from_source_and_packaged(monkeypatch):
    prog, args = paths.launch_command("--tray")
    assert prog.lower().endswith(("pythonw.exe", "python")) and args == ["-m", "lunelis", "--tray"]
    monkeypatch.setattr(paths, "FROZEN", True)
    monkeypatch.setattr(paths.sys, "executable", r"C:\Apps\Lunelis\Lunelis.exe")
    assert paths.launch_command("--after", "12") == (r"C:\Apps\Lunelis\Lunelis.exe", ["--after", "12"])
    from lunelis.ui.tray import autostart_command
    assert autostart_command() == r'"C:\Apps\Lunelis\Lunelis.exe" --tray'
