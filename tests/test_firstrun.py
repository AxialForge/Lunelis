"""First-run setup (firstrun.py): the installer's answers, applied once."""
import json

import pytest

from lunelis import firstrun, paths, updater
from lunelis.catalog.schema import open_catalog
from lunelis.settings import Settings


@pytest.fixture
def setup_env(tmp_path, monkeypatch):
    """A setup.json and location.json in a throwaway %APPDATA%, read as if not under test."""
    appdata = tmp_path / "appdata" / "Lunelis"
    appdata.mkdir(parents=True)
    monkeypatch.setattr(firstrun, "SETUP_FILE", appdata / "setup.json")
    monkeypatch.setattr(firstrun, "APPLIED_FILE", appdata / "setup.applied.json")
    monkeypatch.setattr(paths, "LOCATION_FILE", appdata / "location.json")
    autostart = []
    import lunelis.ui.tray as tray
    monkeypatch.setattr(tray, "set_autostart", autostart.append)    # never the real Run key
    return appdata, autostart


def write(appdata, **data):
    (appdata / "setup.json").write_text(json.dumps({"version": 1, **data}), encoding="utf-8-sig")


def test_read_ignores_tests_and_unknown_versions(setup_env, monkeypatch):
    appdata, _ = setup_env
    write(appdata, folders=[])
    assert firstrun.read() is None                     # LUNELIS_DATA_DIR is set under test
    monkeypatch.delenv("LUNELIS_DATA_DIR")
    assert firstrun.read()["folders"] == []
    (appdata / "setup.json").write_text('{"version": 2}', encoding="utf-8")
    assert firstrun.read() is None
    (appdata / "setup.json").write_text("not json", encoding="utf-8")
    assert firstrun.read() is None


def test_apply_sets_up_once(setup_env, tmp_path):
    appdata, autostart = setup_env
    photos = tmp_path / "Photos"
    photos.mkdir()
    write(appdata, folders=[str(photos), str(tmp_path / "gone")], tray=False, start_with_windows=True,
          models=["scene", "sky", "nonsense"])
    setup = json.loads((appdata / "setup.json").read_text(encoding="utf-8-sig"))
    conn = open_catalog(tmp_path / "catalog.db")
    plan = firstrun.apply(conn, setup)
    s = Settings(conn)
    assert len(plan.root_ids) == 1
    assert conn.execute("SELECT COUNT(*) FROM roots").fetchone()[0] == 1
    assert any("gone" in p for p in plan.problems)     # a missing folder is reported, not fatal
    assert plan.models == ["scene", "sky"]
    assert s.get("tray_enabled") is False
    assert s.get("start_with_windows") is True and autostart == [True]
    assert s.get("welcome_done") is True
    assert not (appdata / "setup.json").exists()       # applied once: kept only as a record
    assert (appdata / "setup.applied.json").exists()


def test_early_uses_a_chosen_empty_data_folder(setup_env, tmp_path, monkeypatch):
    appdata, _ = setup_env
    current = tmp_path / "default"
    current.mkdir()
    monkeypatch.setattr(paths, "DATA_DIR", current)
    monkeypatch.setattr(paths, "reload", lambda: None)
    chosen = tmp_path / "D" / "Lunelis data"
    firstrun.early({"data_dir": str(chosen)})
    assert json.loads(paths.LOCATION_FILE.read_text(encoding="utf-8")) == {"data_dir": str(chosen)}


def test_early_moves_an_existing_library_and_adopts_a_copied_one(setup_env, tmp_path, monkeypatch):
    appdata, _ = setup_env
    monkeypatch.setattr(paths, "reload", lambda: None)
    current = tmp_path / "default"
    current.mkdir()
    (current / "catalog.db").write_bytes(b"")
    monkeypatch.setattr(paths, "DATA_DIR", current)
    chosen = tmp_path / "bigger drive"
    firstrun.early({"data_dir": str(chosen)})
    assert json.loads(paths.LOCATION_FILE.read_text(encoding="utf-8"))["move_to"] == str(chosen)

    # A library copied from another PC: used as it is, nothing moved.
    paths.LOCATION_FILE.unlink()
    empty = tmp_path / "fresh"
    empty.mkdir()
    monkeypatch.setattr(paths, "DATA_DIR", empty)
    copied = tmp_path / "copied"
    copied.mkdir()
    (copied / "catalog.db").write_bytes(b"")
    firstrun.early({"data_dir": str(copied)})
    assert json.loads(paths.LOCATION_FILE.read_text(encoding="utf-8")) == {"data_dir": str(copied)}


def test_early_refuses_a_network_data_folder(setup_env, tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "default")
    setup = {"data_dir": r"\\nas\share\Lunelis"}
    firstrun.early(setup)
    assert not paths.LOCATION_FILE.exists()
    assert "network" in setup["_problems"][0]


def test_update_carries_the_uninstaller(tmp_path):
    target, staged = tmp_path / "Lunelis", tmp_path / "new"
    (target / "uninstall").mkdir(parents=True)
    (target / "uninstall" / "unins000.exe").write_bytes(b"x")
    (target / "Lunelis.exe").write_bytes(b"old")
    staged.mkdir()
    (staged / "Lunelis.exe").write_bytes(b"new")
    updater.check_install_folder(target, staged)        # the uninstaller isn't "something else"
    updater.carry_uninstaller(target, staged)
    assert (staged / "uninstall" / "unins000.exe").read_bytes() == b"x"


def test_welcome_window_answers_like_the_installer(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from lunelis.ui import welcome
    monkeypatch.setattr(welcome, "suggested_folders", lambda: [tmp_path])
    d = welcome.WelcomeDialog(tray_on=True, autostart_on=False)
    try:
        d.auto_cb.setChecked(True)
        d.model_cbs["subject"].setChecked(True)
        for _ in range(4):
            d._go(1)
        assert d.next_b.text() == "Start" and "reading 1 folder" in d.summary.text()
        s = d.result_setup()
        assert s == {"version": 1, "folders": [str(tmp_path)], "tray": True, "start_with_windows": True,
                     "models": ["subject"]}
        d.tray_cb.setChecked(False)                    # no tray: no start-up in the tray either
        assert d.result_setup()["start_with_windows"] is False
    finally:
        d.close()
    again = welcome.WelcomeDialog(existing=[str(tmp_path.parent)])   # reopened: sources aren't offered again
    try:
        assert again.folders.count() == 0
    finally:
        again.close()


def test_the_window_applies_a_setup(tmp_path, monkeypatch, setup_env):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from lunelis.ui import main_window as mw
    started, fetched = [], []
    w = mw.MainWindow()
    try:
        monkeypatch.setattr(w, "start", started.append)
        monkeypatch.setattr(w, "_download_models", fetched.append)
        photos = tmp_path / "Photos"
        photos.mkdir()
        w.apply_setup({"version": 1, "folders": [str(photos)], "tray": True, "models": ["sky"]})
        assert len(started) == 1 and fetched == [["sky"]]
        assert Settings(w.conn).get("welcome_done") is True
        w.maybe_welcome()                               # done: no Welcome window
    finally:
        w._quitting = True
        w.close()
