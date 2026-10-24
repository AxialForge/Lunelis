"""The Mac port (0.59): lunelis.macos and the desktop helpers. The statfs /
diskutil checks run on a real Mac only (CI's macos-latest); the rest anywhere."""
import os
import sys

import pytest

from lunelis import macos

mac_only = pytest.mark.skipif(sys.platform != "darwin", reason="needs a Mac")


def test_mount_point_of_a_path_that_isnt_there_yet(tmp_path):
    here = macos.mount_point(tmp_path)
    assert macos.mount_point(tmp_path / "not" / "yet") == here
    assert os.path.ismount(here)


def test_volume_info_names_one_volume_for_two_folders_on_it(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    a, b = macos.volume_info(str(tmp_path / "a")), macos.volume_info(str(tmp_path / "b"))
    assert a[0] and a == b


@mac_only
def test_statfs_reads_the_boot_volume():
    v = macos.volume("/")
    assert v.mount == "/" and v.local and v.fstype == "apfs"
    assert macos.has_trash(os.path.expanduser("~")) and not macos.is_network("/")
    assert macos.drive_kind("/") == "fixed"


@mac_only
def test_volumes_lists_no_boot_disk_link():
    for v in macos.mounted_volumes():
        assert v.startswith("/Volumes/") and not os.path.islink(v)


@mac_only
def test_split_mount(tmp_path):
    mount, rest = macos.split_mount(str(tmp_path))
    assert os.path.join(mount, rest) == os.path.abspath(tmp_path)


@mac_only
def test_the_data_volume_counts_as_the_boot_disk(tmp_path):
    """tmp_path is under /private/var, firmlinked into /System/Volumes/Data:
    that's the boot disk to the user, and one drive with the home folder."""
    assert macos.volume(tmp_path).mount == "/"
    assert macos.mount_point(tmp_path) == macos.mount_point(os.path.expanduser("~")) == "/"
    assert macos.drive_kind(str(tmp_path)) == "fixed"
    from lunelis.migrate.execute import _same_drive
    assert _same_drive(str(tmp_path), os.path.expanduser("~"))


@mac_only
def test_idle_seconds_reads_a_number():
    assert macos.idle_seconds() >= 0


@mac_only
def test_keep_awake_starts_and_stops_caffeinate():
    from lunelis.awake import awake
    with awake():
        pass                                     # no error, and nothing left running
    p = macos.keep_awake()
    assert p is not None and p.poll() is None
    p.terminate()
    p.wait(5)


@mac_only
def test_data_folder_is_in_application_support():
    from lunelis import paths
    assert paths._LOCALAPPDATA.name == "Application Support"
    assert paths.LOCATION_FILE.parent.parent.name == "Preferences"      # outside the data folder


@pytest.mark.skipif(sys.platform == "win32", reason="would really open Explorer")
def test_desktop_helpers_never_use_windows_tools_off_windows(monkeypatch):
    from lunelis import desktop
    calls = []
    monkeypatch.setattr(desktop.subprocess, "Popen", lambda args, *a, **k: calls.append(args))
    desktop.reveal("/x/y.jpg")
    desktop.open_path("/x")
    desktop.open_text("/x/n.md")
    assert len(calls) == 3 and all(c[0] in ("open", "xdg-open") for c in calls)


def test_explorer_and_startfile_only_in_desktop_py():
    """Explorer / Notepad / os.startfile exist on Windows only: UI code goes
    through lunelis.desktop so the Mac gets Finder (the Mac port audit)."""
    import pathlib
    src = pathlib.Path(macos.__file__).parent
    bad = []
    for p in src.rglob("*.py"):
        if p.name == "desktop.py":
            continue
        text = p.read_text(encoding="utf-8")
        for needle in ("os.startfile", '"explorer"', "notepad.exe"):
            if needle in text:
                bad.append(f"{p.relative_to(src)}: {needle}")
    assert bad == []


def test_windows_calls_sit_behind_a_platform_check():
    """Every module that calls kernel32/shell32/winreg also checks
    sys.platform, so importing it on a Mac can't fail."""
    import pathlib
    src = pathlib.Path(macos.__file__).parent
    bad = [str(p.relative_to(src)) for p in src.rglob("*.py")
           if any(n in (t := p.read_text(encoding="utf-8")) for n in ("windll", "import winreg"))
           and "sys.platform" not in t and "os.name" not in t]
    assert bad == []
