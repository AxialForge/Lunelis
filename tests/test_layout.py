"""0.40: the new library layout - Photos / Videos / Timelapse inside each day,
an Undated tree, the first event naming the day."""
import json
import os
from datetime import datetime

from lunelis.migrate import layout
from lunelis.migrate.plan import Options, plan, summary
from lunelis.importing.templates import DEFAULT_TEMPLATE
from test_migrate import jpeg, lib  # noqa: F401  (fixture)

O = layout.LayoutOptions()
T = datetime(2024, 6, 19, 18, 0)


def test_place_puts_each_kind_in_its_folder():
    assert layout.place(O, taken=T, kind="Photos", event="Air Show") == \
        r"Library\Photos and Videos\2024\6-19-2024 Air Show\Photos"
    assert layout.place(O, taken=T, kind="Videos") == r"Library\Photos and Videos\2024\6-19-2024\Videos"
    assert layout.place(O, taken=T, kind="Photos", timelapse=(T, 786)) == \
        r"Library\Photos and Videos\2024\6-19-2024\Timelapse\18-00 (786 frames)"
    assert layout.place(O, taken=None, kind="Videos") == r"Library\Undated\Videos"
    sub = layout.LayoutOptions(photo_subfolders="camera")
    assert layout.place(sub, taken=T, kind="Photos", camera="ILCE-7RM5").endswith(r"\Photos\ILCE-7RM5")
    # An event past midnight stays on its start day.
    assert "6-19-2024 Party" in layout.place(O, taken=datetime(2024, 6, 20, 1), kind="Photos",
                                              event="Party", event_start=datetime(2024, 6, 19, 21))


def test_undated_by_a_believable_modified_date():
    on = layout.LayoutOptions(undated_by_mtime=True)
    when = datetime(2015, 3, 2, 9).timestamp()
    assert layout.believable_mtime(when, [when])
    assert not layout.believable_mtime(datetime(1990, 1, 1).timestamp(), [])
    assert not layout.believable_mtime(datetime(2099, 1, 1).timestamp(), [])
    copied = [when + i for i in range(20)]                       # a whole folder copied that day
    assert not layout.believable_mtime(when, copied)
    assert layout.place(on, taken=None, kind="Photos", mtime_date=datetime(2015, 3, 2)) == \
        r"Library\Photos and Videos\2015\3-2-2015\Photos"
    assert layout.place(O, taken=None, kind="Photos", mtime_date=datetime(2015, 3, 2)) == r"Library\Undated\Photos"


def test_the_first_event_names_the_whole_day():
    rows = [(datetime(2024, 6, 19, 9), "Breakfast", datetime(2024, 6, 19, 8)),
            (datetime(2024, 6, 19, 20), "Fireworks", datetime(2024, 6, 19, 19)),
            (datetime(2024, 6, 20, 9), None, None)]
    days = layout.first_event_of_day(rows)
    assert days == {datetime(2024, 6, 19).date(): ("Breakfast", datetime(2024, 6, 19, 8))}


def test_a_migration_plan_uses_the_library_layout(lib):
    conn, tmp, a, b, target, ra, rb, ids = lib
    mid = plan(conn, str(target), DEFAULT_TEMPLATE, Options(sources=[ra, rb], library_layout=True))
    dest = {src: d for src, d in conn.execute(
        "SELECT src_rel, dest_rel FROM migration_items WHERE migration_id = ? AND action = 'move'", (mid,))}
    assert dest["2024/6-19-2024 Air Show/DSC002.JPG"] == "Library/Photos and Videos/2024/6-19-2024/Photos/DSC002.JPG"
    assert dest["2024/6-19-2024 Air Show/DSC002.ARW"].rsplit("/", 1)[0] == \
        dest["2024/6-19-2024 Air Show/DSC002.JPG"].rsplit("/", 1)[0]          # the pair stays together
    assert dest["misc/scan.jpg"] == "Library/Undated/Photos/scan.jpg"
    assert summary(conn, mid).undated == 1


def test_timelapse_frames_get_their_own_folder_in_a_plan(lib):
    conn, tmp, a, b, target, ra, rb, ids = lib
    frames = [ids["2024/6-19-2024 Air Show/DSC001.JPG"], ids["2024/6-19-2024 Air Show/DSC002.ARW"]]
    conn.execute("INSERT INTO sequences (key, file_ids, frames) VALUES ('k', ?, 2)", (json.dumps(frames),))
    conn.commit()
    mid = plan(conn, str(target), DEFAULT_TEMPLATE, Options(sources=[ra], library_layout=True))
    dest = dict(conn.execute("SELECT src_rel, dest_rel FROM migration_items WHERE migration_id = ?"
                             " AND action = 'move'", (mid,)))
    assert dest["2024/6-19-2024 Air Show/DSC001.JPG"].startswith(
        "Library/Photos and Videos/2024/6-19-2024/Timelapse/10-00 (2 frames)/")


def test_rehearsal_fixes_damaged_videos_and_dates_in_names():
    """0.44 rehearsal: zero-filled Epcot files had no readable date or format -
    the videos went to Undated/Photos, and all of them to Undated though their
    names hold the time."""
    assert layout.media_kind(None, {"mp4"}, "20170808_185617.mp4") == "Videos"
    assert layout.media_kind(None, {"mp4"}, "20170808_174715.jpg") == "Photos"
    assert layout.date_from_name("20170808_174715.jpg") == datetime(2017, 8, 8, 17, 47, 15)
    assert layout.date_from_name("PXL_20240915_113955123.jpg") == datetime(2024, 9, 15, 11, 39, 55)
    assert layout.date_from_name("IMG_20190704_101500.jpg") == datetime(2019, 7, 4, 10, 15)
    assert layout.date_from_name("DSC01234.ARW") is None
    assert layout.date_from_name("20171308_174715.jpg") is None          # month 13: not a date
