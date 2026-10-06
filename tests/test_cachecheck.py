"""0.37.7: thumbnails stored by photo number are rebuilt when the cache was
made for another catalog (it showed another library's pictures)."""
from PIL import Image

from lunelis.catalog import backup, cachecheck
from lunelis.catalog.schema import open_catalog
from lunelis.importers.scan import add_root, scan_root
from lunelis.raw.thumbnails import generate_pending


def test_another_catalogs_thumbnails_are_rebuilt(tmp_path):
    data = tmp_path / "data"
    lib = tmp_path / "lib"
    lib.mkdir()
    Image.new("RGB", (60, 40), (200, 0, 0)).save(lib / "red.jpg")
    old = open_catalog(tmp_path / "old.db")
    scan_root(old, add_root(old, lib))
    assert cachecheck.ensure(old, data) is False                   # first use: nothing to rebuild
    generate_pending(old, data / "cache" / "thumbnails")
    old.close()
    new = open_catalog(tmp_path / "new.db")                          # a different catalog, numbered from 1
    other = tmp_path / "other"
    other.mkdir()
    Image.new("RGB", (60, 40), (0, 0, 200)).save(other / "blue.jpg")
    scan_root(new, add_root(new, other))
    assert cachecheck.ensure(new, data) is True
    assert new.execute("SELECT COUNT(*) FROM files WHERE thumbnail_path IS NOT NULL").fetchone()[0] == 0
    generate_pending(new, data / "cache" / "thumbnails")
    rel = new.execute("SELECT thumbnail_path FROM files").fetchone()[0]
    r, g, b = Image.open(data / "cache" / "thumbnails" / rel).convert("RGB").getpixel((10, 10))
    assert b > 150 and r < 80                                        # its own photo, not the red one
    assert cachecheck.ensure(new, data) is False                     # matched now: nothing again
    new.close()


def test_restoring_a_catalog_backup_rebuilds_the_thumbnails(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    cat = data / "catalog.db"
    conn = open_catalog(cat)
    cachecheck.ensure(conn, data)
    snap = backup.snapshot(conn, data / "backups", "t", keep=3)
    conn.close()
    backup.restore(snap, cat)
    assert not (data / "cache" / "catalog.id").exists()
