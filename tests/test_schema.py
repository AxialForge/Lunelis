"""Step 1 sanity check: a fresh catalog migrates cleanly and is re-runnable."""
import sqlite3

import pytest

from lunelis.catalog import schema
from lunelis.catalog.schema import migrate, current_version, open_catalog


def test_fresh_catalog_migrates(tmp_path):
    db_path = tmp_path / "lunelis.db"
    version = migrate(db_path)
    assert version == schema.MIGRATIONS[-1][0]

    conn = sqlite3.connect(str(db_path))
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    for expected in [
        "roots", "files", "exif", "ratings", "tags", "file_tags",
        "albums", "album_files", "people", "faces",
        "duplicate_groups", "duplicate_group_files", "schema_version",
    ]:
        assert expected in tables, f"missing table: {expected}"
    conn.close()


def test_migrate_is_idempotent(tmp_path):
    db_path = tmp_path / "lunelis.db"
    migrate(db_path)
    version_again = migrate(db_path)  # must not error or re-run migration 1
    assert version_again == schema.MIGRATIONS[-1][0]


def test_open_catalog_returns_usable_connection(tmp_path):
    db_path = tmp_path / "lunelis.db"
    conn = open_catalog(db_path)
    conn.execute(
        "INSERT INTO roots (path, kind) VALUES (?, ?)", ("C:\\Photos", "local")
    )
    conn.commit()
    row = conn.execute("SELECT path FROM roots").fetchone()
    assert row["path"] == "C:\\Photos"
    conn.close()


def test_failed_migration_rolls_back_cleanly(tmp_path, monkeypatch):
    # A migration that dies partway must leave no half-created tables behind,
    # or the next startup fails on "table already exists".
    db_path = tmp_path / "lunelis.db"
    latest = migrate(db_path)
    broken = schema.MIGRATIONS + [
        (latest + 1, "broken", "CREATE TABLE half_done (id INTEGER); SELECT * FROM no_such_table;"),
    ]
    monkeypatch.setattr(schema, "MIGRATIONS", broken)
    with pytest.raises(sqlite3.OperationalError):
        migrate(db_path)

    conn = sqlite3.connect(str(db_path))
    assert current_version(conn) == latest
    assert conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name = 'half_done'"
    ).fetchone()[0] == 0
    conn.close()


def test_upgrading_a_catalog_backs_it_up_first(tmp_path):
    """An older Lunelis can't open an upgraded catalog, so a backup taken just
    before the upgrade is the way back."""
    db_path = tmp_path / "catalog.db"
    latest = schema.MIGRATIONS[-1][0]
    full = list(schema.MIGRATIONS)
    schema.MIGRATIONS[:] = full[:-1]                            # a catalog from the version before
    try:
        assert migrate(db_path) == latest - 1
    finally:
        schema.MIGRATIONS[:] = full
    assert not (tmp_path / "backups").exists()                 # a fresh catalog has nothing to keep

    assert migrate(db_path) == latest
    snaps = list((tmp_path / "backups").glob(f"catalog-*-before-upgrade-v{latest - 1}.zip"))
    assert len(snaps) == 1
    assert migrate(db_path) == latest                           # already current: no second backup
    assert len(list((tmp_path / "backups").iterdir())) == 1
