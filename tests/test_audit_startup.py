"""0.37.3 (audit LRA-021/022/038/061/062): start-up never opens a damaged or
newer catalog as if it were fine, and small state files are written whole."""
import json
import sqlite3

import pytest

from lunelis import paths
from lunelis.catalog import backup, schema
from lunelis.catalog.schema import open_catalog


def test_a_damaged_or_empty_catalog_is_reported(tmp_path):
    cat = tmp_path / "catalog.db"
    assert backup.problem(cat) is None                          # not there yet: a first start
    cat.write_bytes(b"")
    assert backup.problem(cat) == "empty"
    cat.write_bytes(b"this is not a database at all" * 200)
    assert backup.problem(cat).startswith("damaged")
    open_catalog(tmp_path / "good.db").close()
    assert backup.problem(tmp_path / "good.db") is None


def test_a_catalog_from_a_newer_lunelis_is_refused(tmp_path):
    cat = tmp_path / "catalog.db"
    open_catalog(cat).close()
    c = sqlite3.connect(cat)
    c.execute("INSERT INTO schema_version (version, description) VALUES (999, 'from the future')")
    c.commit()
    c.close()
    with pytest.raises(schema.NewerCatalog):
        open_catalog(cat)


def test_restoring_never_leaves_no_catalog(tmp_path):
    cat = tmp_path / "catalog.db"
    conn = open_catalog(cat)
    snap = backup.snapshot(conn, tmp_path / "backups", "test", keep=5)
    conn.close()
    backup.restore(snap, cat)
    assert cat.exists() and backup.problem(cat) is None
    assert list(tmp_path.glob("catalog.db.before-restore-*"))
    hurt = backup.set_aside_damaged(cat)
    assert hurt.exists() and not cat.exists()


def test_json_state_files_are_written_whole(tmp_path):
    p = tmp_path / "x" / "location.json"
    paths.write_json_atomic(p, {"data_dir": "D:/Lunelis"})
    assert json.loads(p.read_text(encoding="utf-8")) == {"data_dir": "D:/Lunelis"}
    assert not list(p.parent.glob("*.tmp"))


def test_an_unreadable_location_file_is_reported(tmp_path, monkeypatch):
    bad = tmp_path / "location.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(paths, "LOCATION_FILE", bad)
    monkeypatch.delenv("LUNELIS_DATA_DIR", raising=False)
    monkeypatch.setattr(paths, "LOCATION_PROBLEM", None)
    paths.resolve_data_dir()
    assert paths.LOCATION_PROBLEM and "location.json" in paths.LOCATION_PROBLEM
