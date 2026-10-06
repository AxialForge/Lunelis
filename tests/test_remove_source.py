"""0.37.6: Settings > Library > Remove a source - out of the catalog, files untouched."""
import pytest
from PIL import Image

from lunelis.albums import model as albums
from lunelis.catalog.schema import open_catalog
from lunelis.importers.scan import RootInUse, add_root, remove_root, scan_root
from lunelis.jobs import engine


def test_removing_a_source_takes_its_photos_out_and_leaves_the_disk_alone(tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    for d in (a, b):
        d.mkdir()
        for i in range(3):
            Image.new("RGB", (20, 20), (i * 40, 0, 0)).save(d / f"p{i}.jpg")
    conn = open_catalog(tmp_path / "c.db")
    ra, rb = add_root(conn, a), add_root(conn, b)
    for r in (ra, rb):
        scan_root(conn, r)
    ids = [r[0] for r in conn.execute("SELECT id FROM files WHERE root_id = ?", (ra,))]
    conn.execute("INSERT INTO ratings (file_id, stars) VALUES (?, 5)", (ids[0],))
    album = albums.create(conn, "Mixed")
    albums.add_files(conn, album, [r[0] for r in conn.execute("SELECT id FROM files")])
    conn.commit()
    assert remove_root(conn, ra) == 3
    assert conn.execute("SELECT COUNT(*) FROM roots").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 3          # B's stay
    assert conn.execute("SELECT COUNT(*) FROM album_files").fetchone()[0] == 3
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert sorted(p.name for p in a.iterdir()) == ["p0.jpg", "p1.jpg", "p2.jpg"]  # nothing on disk changed
    assert add_root(conn, a) != ra                                                 # can come back
    conn.close()


def test_a_source_a_job_is_working_in_isnt_removed(tmp_path):
    a = tmp_path / "A"
    a.mkdir()
    Image.new("RGB", (20, 20)).save(a / "p.jpg")
    conn = open_catalog(tmp_path / "c.db")
    ra = add_root(conn, a)
    scan_root(conn, ra)
    engine.create_job(conn, "verify", "v", [(ra, None)])
    with pytest.raises(RootInUse):
        remove_root(conn, ra)
    assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 1
    conn.close()
