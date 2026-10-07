"""0.41: with a Lunelis folder, copies a migration didn't keep go to its
Duplicates, and copied originals to its Trash - with their original paths,
and back again with Restore."""
from lunelis.dupes.quarantine import restore
from lunelis.migrate.plan import Options, plan
from lunelis.importing.templates import DEFAULT_TEMPLATE
from lunelis.settings import Settings
from test_migrate import _run, all_files, lib  # noqa: F401  (fixture)


def test_duplicates_and_originals_go_to_the_lunelis_folder(lib):
    conn, tmp, a, b, target, ra, rb, ids = lib
    lf = tmp / "Lunelis"
    Settings(conn).set("lunelis_folder", str(lf))
    dup = next(i for rel, i in ids.items() if rel.startswith("2024 Album/"))
    mid = plan(conn, str(target), DEFAULT_TEMPLATE, Options([ra, rb], keep_sources=False))
    _, out = _run(conn, mid, tmp)
    assert out.state == "done"
    assert all_files(lf / "Duplicates") == [f"Migration {mid}/PoolB/2024 Album/6-19-2024 Air Show/DSC001.JPG"]
    trash = all_files(lf / "Trash")
    assert f"Migration {mid}/PoolA/misc/scan.jpg" in trash and f"Migration {mid}/PoolB/phone/DSC002.JPG" in trash
    assert not [f for f in all_files(a) + all_files(b)]          # sources emptied, nothing deleted
    # Restore puts a set-aside duplicate back where it was.
    back = restore(conn, dup)
    assert back.replace("\\", "/").endswith("PoolB/2024 Album/6-19-2024 Air Show/DSC001.JPG")
    assert (b / "2024 Album/6-19-2024 Air Show/DSC001.JPG").exists()


def test_a_cross_drive_move_verifies_before_removing(tmp_path, monkeypatch):
    import os
    from lunelis.dupes import quarantine
    src, dst = tmp_path / "a.bin", tmp_path / "out" / "a.bin"
    src.write_bytes(b"x" * 1000)
    dst.parent.mkdir()
    real = os.rename

    def cross(a, b):
        e = OSError("not same device")
        e.winerror = 17
        raise e
    monkeypatch.setattr(os, "rename", cross)
    quarantine.move_one(str(src), str(dst))
    assert not src.exists() and dst.read_bytes() == b"x" * 1000
    # A copy that doesn't match is removed and the original kept.
    src.write_bytes(b"y" * 1000)
    monkeypatch.setattr(quarantine, "_same_bytes", lambda a, b, chunk=0: False)
    dst2 = tmp_path / "out" / "b.bin"
    try:
        quarantine.move_one(str(src), str(dst2))
        raise AssertionError("should refuse")
    except OSError:
        pass
    assert src.exists() and not dst2.exists()
    monkeypatch.setattr(os, "rename", real)


def test_trash_retention_only_offers_what_is_past_its_time():
    from datetime import datetime, timezone
    from lunelis.dupes.manage import Entry, due
    now = datetime(2026, 10, 7, tzinfo=timezone.utc)
    old = Entry("f1", "migration", "a", "b", 1, "2026-06-01T00:00:00+00:00", None, None, 1, None, False)
    new = Entry("f2", "migration", "a", "b", 1, "2026-10-01T00:00:00+00:00", None, None, 2, None, False)
    assert due([old, new], 0, now) == []                       # forever: nothing offered
    assert due([old, new], 30, now) == [old]
    assert due([old, new], 365, now) == []
