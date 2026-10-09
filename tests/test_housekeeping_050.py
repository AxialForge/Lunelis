"""0.50: old bookkeeping rows leave the catalog; anything still needed stays."""
from lunelis.catalog import housekeeping
from lunelis.catalog.schema import open_catalog


def test_old_rows_go_and_needed_ones_stay(tmp_path):
    c = open_catalog(tmp_path / "c.db")
    old, new = "2025-01-01 00:00:00", "2026-10-01 00:00:00"
    c.execute("INSERT INTO jobs (id, kind, title, state, finished_at) VALUES (1, 'verify', 'v', 'done', ?)", (old,))
    c.execute("INSERT INTO jobs (id, kind, title, state, finished_at) VALUES (2, 'verify', 'v', 'done', ?)", (new,))
    c.execute("INSERT INTO jobs (id, kind, title, state, finished_at) VALUES (3, 'migrate', 'm', 'done', ?)", (old,))
    c.execute("INSERT INTO jobs (id, kind, title, state) VALUES (4, 'verify', 'v', 'queued')")
    c.execute("INSERT INTO migrations (id, target, template, options, state, job_id, created_at)"
              " VALUES (1, 'T', 't', '{}', 'released', 3, ?)", (old,))
    c.execute("INSERT INTO migrations (id, target, template, options, state, created_at)"
              " VALUES (2, 'T', 't', '{}', 'planned', ?)", (old,))
    c.execute("INSERT INTO imports (id, source, template, destination, state, finished_at)"
              " VALUES (1, 'F:', 't', 'D', 'done', ?)", (old,))
    c.execute("INSERT INTO roots (id, path) VALUES (1, ?)", (str(tmp_path),))
    c.execute("INSERT INTO files (id, root_id, rel_path, filename, ext, size_bytes, mtime) VALUES (1, 1, 'a', 'a', 'jpg', 1, 0)")
    c.execute("INSERT INTO import_items (import_id, source_rel, size, mtime) VALUES (1, 'DCIM/a.jpg', 1, 0)")
    c.commit()
    res = housekeeping.prune(c, now="2026-10-08 00:00:00")
    assert (res.jobs, res.import_items, res.plans) == (1, 1, 1)
    assert [r[0] for r in c.execute("SELECT id FROM jobs ORDER BY id")] == [2, 3, 4]    # recent, a migration's, running
    assert [r[0] for r in c.execute("SELECT id FROM migrations")] == [1]              # the one that ran stays
    assert c.execute("SELECT COUNT(*) FROM imports").fetchone()[0] == 1               # the import itself stays
    assert housekeeping.prune_daily(c) is not None and housekeeping.prune_daily(c) is None   # once a day
    c.close()
