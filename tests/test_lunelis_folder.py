"""0.40: one Lunelis folder beside the Library for everything Lunelis makes or keeps."""
from datetime import date
from pathlib import Path

from lunelis import lunelis_folder
from lunelis.catalog import backup
from lunelis.catalog.schema import open_catalog
from lunelis.create import engine
from lunelis.importing.ingest import load_settings
from lunelis.settings import Settings


def test_outputs_follow_the_lunelis_folder_unless_chosen(tmp_path):
    conn = open_catalog(tmp_path / "c.db")
    s = Settings(conn)
    lf = tmp_path / "Lunelis"
    assert lunelis_folder.root(s) is None and backup.backup_dir(s, tmp_path) == tmp_path / "backups"
    s.set("lunelis_folder", str(lf))
    made = lunelis_folder.make_folders(s)
    assert {p.relative_to(lf).as_posix() for p in made} == {
        "Exports", "Staging", "Backups/Catalog", "Duplicates", "Trash", "Migration logs"}
    assert engine.output_dir(conn, "Timelapse") == lf / "Timelapses"
    assert engine.output_dir(conn, "Something new") == lf / "Something new"
    assert lunelis_folder.exports(s) == lf / "Exports" / str(date.today().year)
    assert backup.backup_dir(s, tmp_path) == lf / "Backups" / "Catalog"
    assert load_settings(conn, tmp_path).staging_network == str(lf / "Staging")
    # A folder chosen for one thing still wins.
    s.set("create_output_dir", str(tmp_path / "mine"))
    s.set("catalog_backup_dir", str(tmp_path / "bk"))
    assert engine.output_dir(conn, "Timelapse") == tmp_path / "mine"
    assert backup.backup_dir(s, tmp_path) == tmp_path / "bk"
    conn.close()
