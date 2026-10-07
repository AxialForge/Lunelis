"""0.42: the Google Takeout review - what comes into the new Library."""
import pytest

from lunelis.catalog.schema import open_catalog
from lunelis.importers import takeout_review as tr


@pytest.fixture
def conn(tmp_path):
    c = open_catalog(tmp_path / "c.db")
    c.execute("INSERT INTO roots (id, path) VALUES (1, ?), (2, ?)",
              (str(tmp_path / "Photos"), str(tmp_path / "Takeout")))
    files = [(1, 1, "2019/beach.jpg", "2019-07-04T10:00:00"),
             (2, 2, "Takeout/Google Photos/Photos from 2019/beach.jpg", "2019-07-04T10:00:00"),   # in the library
             (3, 2, "Takeout/Google Photos/Trip to Rome/colosseum.jpg", "2018-05-02T09:00:00"),
             (4, 2, "Takeout/Google Photos/Trip to Rome/colosseum-edited.jpg", "2018-05-02T09:00:00"),
             (5, 2, "Takeout/Google Photos/Photos from 2017/IMG_0001(1).jpg", None)]
    for fid, rid, rel, taken in files:
        c.execute("INSERT INTO files (id, root_id, rel_path, filename, ext, size_bytes, mtime)"
                  " VALUES (?, ?, ?, ?, 'jpg', 1, 0)", (fid, rid, rel, rel.rsplit("/", 1)[-1]))
        c.execute("INSERT INTO exif (file_id, captured_at) VALUES (?, ?)", (fid, taken))
    gid = c.execute("INSERT INTO duplicate_groups (method, hash_key, verified) VALUES ('similar', '1,2', 0)").lastrowid
    c.executemany("INSERT INTO duplicate_group_files (group_id, file_id) VALUES (?, ?)", [(gid, 1), (gid, 2)])
    c.commit()
    yield c
    c.close()


def test_items_by_year_and_album_with_marks(conn):
    its = {i.file_id: i for i in tr.items(conn)}
    assert set(its) == {2, 3, 4, 5}                          # only the Takeout source
    assert its[2].in_library and not its[2].included          # already in the library: unticked
    assert its[3].included and its[3].album == "Trip to Rome" and its[3].year == "2018"
    assert its[4].edited and its[5].numbered and its[5].year == "2017"   # undated: the year folder
    t = tr.tree(list(its.values()))
    assert list(t) == ["2019", "2018", "2017"] and list(t["2018"]) == ["Trip to Rome"]
    assert tr.counts(list(its.values())) == {"total": 4, "included": 3, "in_library": 1, "edited": 1, "numbered": 1}


def test_choices_stick_and_feed_the_migration(conn):
    assert tr.unticked(conn) == {2}
    tr.set_included(conn, [2], True)                          # the user wants it anyway
    tr.set_included(conn, [4], False)
    assert tr.unticked(conn) == {4}
    assert {i.file_id: i.included for i in tr.items(conn)} == {2: True, 3: True, 4: False, 5: True}


def test_an_unticked_takeout_photo_stays_put_and_is_accounted_for(tmp_path):
    from lunelis.importers.metadata import extract_pending
    from lunelis.importers.scan import add_root, scan_root
    from lunelis.importing.templates import DEFAULT_TEMPLATE
    from lunelis.migrate import logs
    from lunelis.migrate.plan import Options, plan
    from test_migrate import _run, all_files, jpeg
    src = tmp_path / "My Takeout"
    jpeg(src / "Google Photos/Trip/keep.jpg", "2018:05:02 09:00:00", 1)
    jpeg(src / "Google Photos/Trip/skip.jpg", "2018:05:02 09:05:00", 2)
    (src / "Google Photos/Trip/skip.jpg.supplemental-metadata.json").write_text('{"title": "skip.jpg"}', encoding="utf-8")
    target = tmp_path / "New"
    target.mkdir()
    c = open_catalog(tmp_path / "c.db")
    rid = add_root(c, src)
    scan_root(c, rid)
    extract_pending(c)
    skip = c.execute("SELECT id FROM files WHERE filename = 'skip.jpg'").fetchone()[0]
    tr.set_included(c, [skip], False)
    mid = plan(c, str(target), DEFAULT_TEMPLATE, Options([rid], library_layout=True))
    _, out = _run(c, mid, tmp_path)
    assert out.state == "done"
    assert [p.rsplit("/", 1)[-1] for p in all_files(target)] == ["keep.jpg"]   # no JSON, no unticked photo
    assert (src / "Google Photos/Trip/skip.jpg").exists()
    rep = logs.accounted(c, mid)
    assert rep.clean, rep.text()
    assert rep.counts["Left in place - unticked on the Google Takeout page"] == 1
    assert rep.counts["Left in place - not a photo or video"] == 1          # the JSON
    c.close()


def test_the_page_lists_and_ticks(conn):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from lunelis.ui.takeout_view import TakeoutView
    page = TakeoutView(conn)
    page.refresh()
    assert "4 photos and videos" in page.summary.text() and "3 ticked" in page.summary.text()
    rome = page.tree.topLevelItem(1).child(0)                 # 2018 > Trip to Rome
    assert rome.text(0) == "Trip to Rome"
    rome.setCheckState(0, Qt.CheckState.Unchecked)           # untick the whole album
    assert tr.unticked(conn) == {2, 3, 4}
    page.filter.setCurrentIndex(page.filter.findData("unticked"))
    assert page.tree.topLevelItemCount() == 2                 # 2019 and 2018 have unticked items
    page.deleteLater()
