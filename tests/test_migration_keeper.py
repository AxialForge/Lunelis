"""0.41: which identical copy a migration keeps."""
from lunelis.catalog.schema import open_catalog
from lunelis.migrate.plan import migration_keeper_rank


def _cat(tmp_path):
    c = open_catalog(tmp_path / "c.db")
    c.execute("INSERT INTO roots (id, path) VALUES (1, 'P:/Photos'), (2, 'P:/Takeout')")
    for fid, root, rel, mtime in ((1, 1, "a/deep/x.jpg", 100), (2, 1, "x.jpg", 200), (3, 2, "x.jpg", 50),
                                   (4, 1, "b/x.jpg", 300)):
        c.execute("INSERT INTO files (id, root_id, rel_path, filename, ext, size_bytes, mtime) VALUES (?, ?, ?, 'x.jpg',"
                  " 'jpg', 1, ?)", (fid, root, rel, mtime))
    c.commit()
    return c


def rows(c, ids):
    return [c.execute("SELECT f.id, f.root_id, r.path, f.rel_path FROM files f JOIN roots r ON r.id = f.root_id"
                      " WHERE f.id = ?", (i,)).fetchone() for i in ids]


def test_keeper_order(tmp_path):
    c = _cat(tmp_path)
    ids = [1, 2, 3, 4]
    best = lambda: min(rows(c, ids), key=migration_keeper_rank(c, None, ids))[0]
    assert best() == 1                       # not Takeout, then the older file (100 < 200 < 300)
    c.execute("INSERT INTO ratings (file_id, stars) VALUES (4, 3)")
    c.commit()
    assert best() == 4                       # the copy with your work on it beats the older one
    assert min(rows(c, ids), key=migration_keeper_rank(c, [2], ids))[0] == 3   # a preferred source wins
    c.close()
