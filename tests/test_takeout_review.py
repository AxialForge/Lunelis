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


def test_a_takeout_copy_hours_off_is_still_the_same_photo():
    """Found in the 0.44 rehearsal: Takeout copies of the airshow came back 8-12 h
    away from the camera's time and were migrated as new photos on another day."""
    from lunelis.dupes.similar import same_photo
    h = int("f0f0f0f0f0f0f0f0", 16)
    nas = (h, "2024-09-02T13:00:00", 1.5, (1, "a"), "sep03632.jpg", "ILCE-7RM5", 15_135_270)
    takeout = (h ^ 0b111, "2024-09-03T00:58:00", 1.5, (2, "b"), "sep03632.jpg", "ILCE-7RM5", 19_572_304)
    assert same_photo(nas, takeout, 10)
    other_day = (h ^ 0b111, "2024-09-05T00:58:00", 1.5, (2, "b"), "sep03632.jpg", "ILCE-7RM5", 19_572_304)
    assert not same_photo(nas, other_day, 10)                     # days apart: not the same moment
    burst = (h ^ 0b11, "2024-09-02T13:00:01", 1.5, (1, "a"), "sep03633.jpg", "ILCE-7RM5", 15_000_000)
    assert not same_photo(nas, burst, 10)                         # the next frame: another name


def test_an_edited_takeout_copy_is_still_the_same_shot():
    from lunelis.dupes.similar import same_photo
    h = int("f0f0f0f0f0f0f0f0", 16)
    nas = (h, "2024-09-02T15:55:09.244", 1.5, (1, "a"), "sep04748.jpg", "ILCE-7RM5", 15_000_000)
    edit = (h ^ 0b1111111111111, "2024-09-03T01:04:12", None, (2, "b"), "sep04748.jpg", None, 19_000_000)  # 13 bits
    assert same_photo(nas, edit, 4)
    other = (h ^ 0b1111111111111, "2024-09-02T15:55:09.244", 1.5, (2, "b"), "sep04748.jpg", None, 19_000_000)
    assert not same_photo(nas, other, 4)        # same moment but that different: not loosened


def test_an_identical_twin_of_a_takeout_copy_in_the_library_is_in_it_too(conn):
    # file 2 (Takeout) is a near-duplicate of 1 (the library); 6 is 2's identical "(1)" twin
    conn.execute("INSERT INTO files (id, root_id, rel_path, filename, ext, size_bytes, mtime)"
                 " VALUES (6, 2, 'Takeout/Google Photos/Photos from 2019/beach(1).jpg', 'beach(1).jpg', 'jpg', 1, 0)")
    gid = conn.execute("INSERT INTO duplicate_groups (method, hash_key, verified) VALUES ('exact', 'h', 1)").lastrowid
    conn.executemany("INSERT INTO duplicate_group_files (group_id, file_id) VALUES (?, ?)", [(gid, 2), (gid, 6)])
    conn.commit()
    assert tr.unticked(conn) == {2, 6}


def test_a_plane_in_a_clear_sky_pairs_only_by_name_and_shift():
    from lunelis.dupes.similar import same_photo
    sky = int("000008080c020000", 16)
    nas = (sky, "2024-09-02T15:53:47.052", 1.5, (1, "a"), "sep04726.jpg", "ILCE-7RM5", 16_485_022)
    tko = (int("000008180c010000", 16), "2024-09-03T01:05:18", None, (2, "b"), "sep04726.jpg", None, 21_041_860)
    assert same_photo(nas, tko, 4)
    other = (int("000008180c010000", 16), "2024-09-02T15:53:47.052", 1.5, (2, "b"), "sep04727.jpg", None, 21_041_860)
    assert not same_photo(nas, other, 4)                          # another sky shot: blank proves nothing
