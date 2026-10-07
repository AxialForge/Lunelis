"""0.39: the timelapse engine - steady-interval runs found, reviewed, stacked."""
from datetime import datetime, timedelta

import pytest

from lunelis import stacks, timelapses
from lunelis.catalog.schema import open_catalog
from lunelis.settings import Settings

T0 = datetime(2026, 6, 19, 18, 0, 0)


@pytest.fixture
def conn(tmp_path):
    c = open_catalog(tmp_path / "c.db")
    c.execute("INSERT INTO roots (id, path) VALUES (1, ?)", (str(tmp_path),))
    c.commit()
    yield c
    c.close()


def shoot(conn, n, interval, start=T0, cam="ILCE-7RM5", lens="FE 24-70mm", focal=24.0, folder="tl",
          raw_pairs=False, exposures=False):
    """n frames every `interval` s; returns the ids (the RAWs when paired)."""
    ids = []
    for i in range(n):
        t = start + timedelta(seconds=interval * i)
        for ext, is_raw in ((("arw", 1), ("jpg", 0)) if raw_pairs else (("jpg", 0),)):
            name = f"{folder}_{start:%H%M%S}_{i:05d}.{ext}"
            fid = conn.execute("INSERT INTO files (root_id, rel_path, filename, ext, size_bytes, mtime, is_raw)"
                               " VALUES (1, ?, ?, ?, 1, 0, ?)", (f"{folder}/{name}", name, ext, is_raw)).lastrowid
            conn.execute("INSERT INTO exif (file_id, captured_at, camera_make, camera_model, lens, focal_length_mm,"
                         " shutter_speed) VALUES (?, ?, 'Sony', ?, ?, ?, ?)",
                         (fid, t.isoformat(), cam, lens, focal, f"1/{100 + i}" if exposures else "1/100"))
            if is_raw or not raw_pairs:
                ids.append(fid)
    conn.commit()
    return ids


def test_a_steady_run_of_100_is_found_and_99_is_not(conn):
    a = shoot(conn, 120, 5, exposures=True)                       # exposure changes allowed
    shoot(conn, 99, 5, start=T0 + timedelta(days=1), folder="short")
    assert timelapses.refresh(conn) == 1
    [q] = timelapses.all_sequences(conn)
    assert q.file_ids == a and q.interval == 5.0 and q.status == "found"
    assert timelapses.refresh(conn) == 0                          # found again: not new


def test_raw_and_jpeg_count_as_one_frame(conn):
    raws = shoot(conn, 100, 10, raw_pairs=True)
    timelapses.refresh(conn)
    [q] = timelapses.all_sequences(conn)
    assert q.file_ids == raws and len(q.file_ids) == 100


def test_an_unsteady_interval_or_a_zoom_ends_the_run(conn):
    shoot(conn, 60, 5)
    shoot(conn, 60, 9, start=T0 + timedelta(seconds=5 * 60), folder="b")   # interval changes
    assert timelapses.refresh(conn) == 0


def test_a_pause_is_bridged_unless_split_is_on(conn):
    shoot(conn, 60, 5)
    shoot(conn, 60, 5, start=T0 + timedelta(seconds=5 * 59 + 300))       # 5 minute battery swap
    timelapses.refresh(conn)
    [q] = timelapses.all_sequences(conn)
    assert len(q.file_ids) == 120 and q.pauses == 1
    conn.execute("DELETE FROM sequences")
    Settings(conn).set("timelapse_split_gaps", True)
    Settings(conn).set("timelapse_min_frames", 50)
    timelapses.refresh(conn)
    assert sorted(len(q.file_ids) for q in timelapses.all_sequences(conn)) == [60, 60]


def test_bursts_are_not_timelapses_and_stop_at_50(conn):
    for i in range(60):                                           # 60 frames, 10 a second
        t = (T0 + timedelta(milliseconds=100 * i)).isoformat(timespec="milliseconds")
        fid = conn.execute("INSERT INTO files (root_id, rel_path, filename, ext, size_bytes, mtime)"
                           " VALUES (1, ?, ?, 'jpg', 1, 0)", (f"b/b{i}.jpg", f"b{i}.jpg")).lastrowid
        conn.execute("INSERT INTO exif (file_id, captured_at, camera_model) VALUES (?, ?, 'A7')", (fid, t))
    conn.commit()
    assert timelapses.refresh(conn) == 0
    assert stacks.detect(conn, max_frames=50) == []               # too long for a burst
    assert len(stacks.detect(conn, max_frames=60)) == 1


def test_answers_survive_and_stacking_shows_one_tile(conn):
    ids = shoot(conn, 120, 5)
    timelapses.refresh(conn)
    [q] = timelapses.all_sequences(conn)
    timelapses.stack(conn, q.id)
    assert stacks.stack_of(conn, ids[0]) is not None and stacks.members(conn, stacks.stack_of(conn, ids[0]))[:3] == ids[:3]
    stacks.rebuild(conn)                                          # the burst finder leaves it alone
    assert timelapses.all_sequences(conn)[0].stacked
    timelapses.set_status(conn, q.id, "dismissed")
    assert not timelapses.all_sequences(conn) and stacks.stack_of(conn, ids[0]) is None
    timelapses.refresh(conn)
    assert not timelapses.all_sequences(conn)                     # dismissed stays dismissed


def test_big_ones_stack_themselves(conn):
    Settings(conn).set("timelapse_auto_stack_frames", 150)
    shoot(conn, 160, 2)
    shoot(conn, 120, 2, start=T0 + timedelta(days=1), folder="small")
    timelapses.refresh(conn)
    assert sorted((len(q.file_ids), q.stacked) for q in timelapses.all_sequences(conn)) == [(120, False), (160, True)]


def test_manual_timelapse_burst_toggle_and_lookup(conn):
    ids = shoot(conn, 30, 3, raw_pairs=True)
    sid = timelapses.make_manual(conn, list(reversed(ids)))       # put in shooting order
    q = timelapses.get(conn, sid)
    assert q.file_ids == ids and q.origin == "manual" and q.status == "confirmed"
    jpeg = conn.execute("SELECT id FROM files WHERE filename LIKE '%00003.jpg'").fetchone()[0]
    conn.execute("UPDATE files SET pair_of = ? WHERE id = ?", (ids[3], jpeg))
    assert timelapses.sequence_of(conn, jpeg) == sid             # the JPEG half finds it too
    st = timelapses.to_burst(conn, sid)
    assert stacks.members(conn, st) == ids and timelapses.get(conn, sid).kind == "burst"
    back = timelapses.burst_to_timelapse(conn, st)
    assert back == sid and timelapses.get(conn, sid).kind == "timelapse" and stacks.stack_of(conn, ids[0]) is None
    with pytest.raises(ValueError):
        timelapses.to_burst(conn, timelapses.make_manual(conn, shoot(conn, 60, 2, start=T0 + timedelta(days=2), folder="x")))
