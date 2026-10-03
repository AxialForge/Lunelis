"""Step 12: the Settings screen and what sits behind it - skipped folders,
settings validation, moving the data folder, restore-at-next-start."""
import json
import os
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lunelis import paths  # noqa: E402
from lunelis.catalog import backup  # noqa: E402
from lunelis.catalog.schema import open_catalog  # noqa: E402
from lunelis.catalog.ratings import set_ratings  # noqa: E402
from lunelis.importers.scan import (  # noqa: E402
    add_root, catalog_stats, exclude_folder, excluded_folders, include_folder, scan_root,
)
from lunelis.settings import Settings  # noqa: E402
from lunelis.ui.library import Filter, LibraryIndex  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "Photos"
    for rel in ("2026/a.jpg", "2026/b.jpg", "Exports/web/c.jpg", "Exports/d.jpg", "exports2/e.jpg"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(os.urandom(64))
    conn = open_catalog(tmp_path / "catalog.db")
    rid = add_root(conn, root)
    scan_root(conn, rid)
    yield conn, rid, root
    conn.close()


def _visible(conn) -> set[str]:
    idx = LibraryIndex()
    idx.load(conn, "date_desc", Filter())
    return {conn.execute("SELECT filename FROM files WHERE id = ?", (row[0],)).fetchone()[0]
            for row in idx.rows}


# --- skipped folders ----------------------------------------------------------------

def test_skipping_a_folder_hides_its_files_but_keeps_ratings(library):
    conn, rid, root = library
    c_id = conn.execute("SELECT id FROM files WHERE filename = 'c.jpg'").fetchone()[0]
    set_ratings(conn, [c_id], stars=4)
    assert exclude_folder(conn, rid, "Exports") == 2           # c.jpg, d.jpg - not exports2/
    assert _visible(conn) == {"a.jpg", "b.jpg", "e.jpg"}
    assert catalog_stats(conn)["excluded"] == 2
    assert catalog_stats(conn)["missing"] == 0

    # A rescan doesn't walk it, and doesn't call its files missing either.
    (root / "Exports" / "new.jpg").write_bytes(b"x" * 10)
    r = scan_root(conn, rid)
    assert r.added == 0 and r.missing == 0
    assert conn.execute("SELECT COUNT(*) FROM files WHERE filename = 'new.jpg'").fetchone()[0] == 0

    # Stop skipping: the old files come straight back with their rating, the
    # new one arrives with the next scan.
    assert include_folder(conn, rid, "Exports") == 0
    assert _visible(conn) == {"a.jpg", "b.jpg", "c.jpg", "d.jpg", "e.jpg"}
    assert conn.execute("SELECT stars FROM ratings WHERE file_id = ?", (c_id,)).fetchone()[0] == 4
    assert scan_root(conn, rid).added == 1


def test_skipped_folders_match_case_insensitively_and_never_a_whole_source(library):
    conn, rid, _ = library
    exclude_folder(conn, rid, "exports\\WEB")                  # Windows-style, other case
    assert excluded_folders(conn) == [(rid, "exports/WEB")]
    assert "c.jpg" not in _visible(conn)
    with pytest.raises(ValueError):
        exclude_folder(conn, rid, "")


# --- settings validation --------------------------------------------------------------

def test_settings_refuse_bad_values(tmp_path):
    conn = open_catalog(tmp_path / "catalog.db")
    s = Settings(conn)
    for key, bad in (("import_template", r"{YYYY}\{nope}"), ("import_local_reserve_gb", -1),
                     ("catalog_backups_keep", 0), ("job_window_start_hour", 24),
                     ("job_default_when", "sometimes"), ("import_destination", "  "),
                     ("preferred_roots", ["1"]), ("job_mb_per_s", True)):
        with pytest.raises(ValueError):
            s.set(key, bad)
        assert s.get(key) == s.all()[key]                      # unchanged (still the default)
    s.set("job_default_when", "idle")
    s.set("import_local_reserve_gb", 75)
    assert (s.get("job_default_when"), s.get("import_local_reserve_gb")) == ("idle", 75)
    conn.close()


# --- moving the data folder ------------------------------------------------------------

def test_move_tree_merges_and_removes_the_old_folder(tmp_path):
    src, dst = tmp_path / "old", tmp_path / "new"
    (src / "cache" / "thumbnails").mkdir(parents=True)
    (src / "catalog.db").write_bytes(b"db")
    (src / "cache" / "thumbnails" / "1.jpg").write_bytes(b"t")
    (dst / "cache").mkdir(parents=True)                        # a half-finished earlier attempt
    assert paths.move_tree(src, dst) == 2
    assert (dst / "catalog.db").read_bytes() == b"db"
    assert (dst / "cache" / "thumbnails" / "1.jpg").read_bytes() == b"t"
    assert not src.exists()


def test_move_tree_across_drives_copies_everything_before_deleting(tmp_path, monkeypatch):
    src, dst = tmp_path / "old", tmp_path / "new"
    (src / "sub").mkdir(parents=True)
    (src / "a").write_bytes(b"1")
    (src / "sub" / "b").write_bytes(b"22")
    monkeypatch.setattr(paths, "_same_volume", lambda a, b: False)
    real_copy = paths.shutil.copy2
    calls = []

    def flaky(a, b):
        calls.append(a)
        if len(calls) == 2:
            raise OSError("drive unplugged")
        return real_copy(a, b)

    monkeypatch.setattr(paths.shutil, "copy2", flaky)
    with pytest.raises(OSError):
        paths.move_tree(src, dst)
    # Interrupted: every original is still there.
    assert (src / "a").exists() and (src / "sub" / "b").exists()
    monkeypatch.setattr(paths.shutil, "copy2", real_copy)
    assert paths.move_tree(src, dst) == 2                      # run again: finishes
    assert (dst / "a").read_bytes() == b"1" and (dst / "sub" / "b").read_bytes() == b"22"
    assert not src.exists()
    assert not list(dst.rglob("*.partial"))


def test_data_folder_checks(tmp_path):
    cur = tmp_path / "Lunelis"
    cur.mkdir()
    for bad in (r"\\nas\share\Lunelis", cur / "inner", tmp_path):
        with pytest.raises(ValueError):
            paths.check_new_data_dir(bad, cur)
    taken = tmp_path / "Other"
    taken.mkdir()
    (taken / "catalog.db").write_bytes(b"")
    with pytest.raises(ValueError):
        paths.check_new_data_dir(taken, cur)
    paths.check_new_data_dir(tmp_path / "D" / "Lunelis", cur)  # fine
    assert paths.is_network_path(r"\\nas\photos")


def test_move_is_requested_then_done_at_next_start(tmp_path, monkeypatch):
    monkeypatch.delenv("LUNELIS_DATA_DIR")
    monkeypatch.setattr(paths, "LOCATION_FILE", tmp_path / "location.json")
    old, new = tmp_path / "C" / "Lunelis", tmp_path / "D" / "Lunelis"
    old.mkdir(parents=True)
    (old / "catalog.db").write_bytes(b"db")
    paths.set_data_dir(old)
    paths.request_move(new, current=old)
    assert paths.resolve_data_dir() == old                     # nothing changes until restart
    assert paths.pending_move() == new
    try:
        assert paths.finish_pending_move() == new
        assert paths.DATA_DIR == new and paths.DEFAULT_CATALOG_PATH == new / "catalog.db"
        assert (new / "catalog.db").read_bytes() == b"db"
        assert json.loads((tmp_path / "location.json").read_text()) == {"data_dir": str(new)}
        assert paths.pending_move() is None
    finally:
        monkeypatch.undo()
        paths.reload()                                         # back to the test data folder


def test_cancel_move(tmp_path, monkeypatch):
    monkeypatch.delenv("LUNELIS_DATA_DIR")
    monkeypatch.setattr(paths, "LOCATION_FILE", tmp_path / "location.json")
    paths.request_move(tmp_path / "D" / "Lunelis", current=tmp_path / "C" / "Lunelis")
    paths.cancel_move()
    assert paths.pending_move() is None


# --- restore at next start -------------------------------------------------------------

def test_restore_is_requested_then_done_at_next_start(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    conn = open_catalog(data / "catalog.db")
    Settings(conn).set("import_local_reserve_gb", 11)
    snap = backup.snapshot(conn, data / "backups", "manual")
    Settings(conn).set("import_local_reserve_gb", 99)
    conn.close()

    not_a_backup = tmp_path / "x.zip"
    with zipfile.ZipFile(not_a_backup, "w") as z:
        z.writestr("hello.txt", "hi")
    with pytest.raises(ValueError):
        backup.request_restore(data, not_a_backup)

    backup.request_restore(data, snap)
    assert backup.finish_pending_restore(data, data / "catalog.db") == snap
    assert backup.finish_pending_restore(data, data / "catalog.db") is None     # once only
    conn = open_catalog(data / "catalog.db")
    assert Settings(conn).get("import_local_reserve_gb") == 11
    conn.close()


# --- the page itself ---------------------------------------------------------------------

def test_settings_page_saves_as_you_go(app, library):
    from lunelis.ui.settings_view import SettingsView
    conn, rid, _ = library
    view = SettingsView(conn)
    s = Settings(conn)

    view._spins["import_local_reserve_gb"].setValue(80)
    assert s.get("import_local_reserve_gb") == 80

    view.template.setCurrentText(r"{YYYY}\{nope}")               # invalid: shown, not saved
    assert view.template_example.objectName() == "Error"
    assert s.get("import_template") == r"{YYYY}\{M}-{D}-{YYYY}[ {import_name}]"
    view.template.setCurrentText(r"{YYYY}\{MM}[ {import_name}]")
    assert s.get("import_template") == r"{YYYY}\{MM}[ {import_name}]"
    assert "2026\\06 Air Show" in view.template_example.text()

    view.when.setCurrentIndex(view.when.findData("window"))
    assert s.get("job_default_when") == "window"

    # Turning a source off hides it, without deleting anything.
    changed = []
    view.library_changed.connect(lambda: changed.append(1))
    from PySide6.QtCore import Qt
    view.roots.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
    assert conn.execute("SELECT enabled FROM roots WHERE id = ?", (rid,)).fetchone()[0] == 0
    assert changed and _visible(conn) == set()
    assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 5


def test_job_dialog_starts_from_the_settings_defaults(app, library):
    from lunelis.ui.jobs import ScopeDialog
    conn, rid, _ = library
    s = Settings(conn)
    s.set("job_default_when", "window")
    s.set("job_window_start_hour", 1)
    s.set("job_window_end_hour", 5)
    s.set("job_mb_per_s", 40)
    dlg = ScopeDialog(conn, "Find duplicates")
    dlg.roots.item(0).setCheckState(__import__("PySide6.QtCore").QtCore.Qt.CheckState.Checked)
    dlg._accept()
    _, scope, options = dlg.result_value
    assert scope == [(rid, None)]
    assert options == {"schedule": {"mode": "window", "start_hour": 1, "end_hour": 5}, "mb_per_s": 40}


def test_date_formats():
    from datetime import datetime
    from lunelis.ui import photoinfo
    t = datetime(2026, 6, 19, 14, 3)
    try:
        for key, want in (("long", "Jun 19, 2026 · 2:03 PM"), ("iso", "2026-06-19 14:03"),
                          ("day_first", "19 Jun 2026 · 14:03"), ("short", "6/19/2026 2:03 PM")):
            photoinfo.set_date_format(key)
            assert photoinfo.format_date(t) == want
    finally:
        photoinfo.set_date_format("long")


def test_general_tab_and_window_behaviour(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from lunelis import paths
    from lunelis.catalog.schema import open_catalog
    from lunelis.settings import Settings
    from lunelis.ui import main_window as mw
    monkeypatch.setattr(paths, "DEFAULT_CATALOG_PATH", tmp_path / "catalog.db")
    conn = open_catalog(paths.DEFAULT_CATALOG_PATH)
    Settings(conn).set("start_page", "Albums")
    conn.close()
    w = mw.MainWindow()                  # its own connection, as in the app (workers open theirs)
    conn = w.conn
    try:
        QApplication.processEvents()
        assert w.pages.currentWidget() is w.albums_page                  # opens on the chosen page
        sv = w.settings_page
        sv.refresh()
        assert sv.tabs.tabText(sv.TABS.index("Edit")) == "Edit"
        sv.wheel.setCurrentIndex(sv.wheel.findData("step"))
        assert w.detail.canvas.wheel_mode == "step"
        sv.thumb_size.setCurrentIndex(sv.thumb_size.findData(260))
        assert Settings(conn).get("grid_default_size") == 260
        assert w._busy_work() == []
        w._export_thread = object()
        assert w._busy_work() == ["an export"]
        w._export_thread = None
    finally:
        w._quitting = True
        w.close()
        conn.close()
