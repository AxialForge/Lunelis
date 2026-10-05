"""The updater: finding a release, a verified download, staging, and the
real PowerShell swap run against throwaway install folders."""
import functools
import hashlib
import http.server
import json
import shutil
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

import pytest

from lunelis import updater


@pytest.fixture
def server(tmp_path):
    root = tmp_path / "www"
    root.mkdir()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a: None
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield root, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def make_release(root: Path, base: str, version: str, *, good_checksum=True, extra=None):
    zpath = root / f"Lunelis-v{version}-windows.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("Lunelis/Lunelis.exe", b"MZ fake exe " + version.encode())
        z.writestr("Lunelis/_internal/marker.txt", version)
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    digest = hashlib.sha256(zpath.read_bytes()).hexdigest() if good_checksum else "0" * 64
    (root / (zpath.name + ".sha256")).write_text(f"{digest}  {zpath.name}\n")
    data = {"tag_name": f"v{version}", "body": "## What's new\n- things", "html_url": f"{base}/page",
            "assets": [{"name": zpath.name, "browser_download_url": f"{base}/{zpath.name}",
                        "size": zpath.stat().st_size},
                       {"name": zpath.name + ".sha256", "browser_download_url": f"{base}/{zpath.name}.sha256",
                        "size": 90}]}
    (root / "latest.json").write_text(json.dumps(data))
    return f"{base}/latest.json"


def test_versions():
    assert updater.is_newer("0.10.0", "0.9.3") and not updater.is_newer("0.3.0", "0.3.0")
    assert updater.parse_version("v1.2") == (1, 2, 0)


def test_check_download_and_stage(server, monkeypatch, tmp_path):
    root, base = server
    monkeypatch.setattr(updater, "LATEST_API", make_release(root, base, "0.4.0"))
    monkeypatch.setattr(updater, "updates_dir", lambda: tmp_path / "updates")
    rel = updater.check()
    assert (rel.version, rel.tag) == ("0.4.0", "v0.4.0") and "What's new" in rel.notes
    seen = []
    z = updater.download(rel, on_progress=lambda d, t: seen.append(d))
    assert seen and z.exists()
    staged = updater.stage(z)
    assert (staged / "Lunelis.exe").exists() and (staged / "_internal" / "marker.txt").read_text() == "0.4.0"


def test_a_bad_checksum_or_a_hostile_zip_is_refused(server, monkeypatch, tmp_path):
    root, base = server
    monkeypatch.setattr(updater, "updates_dir", lambda: tmp_path / "updates")
    monkeypatch.setattr(updater, "LATEST_API", make_release(root, base, "0.4.0", good_checksum=False))
    with pytest.raises(updater.UpdateError, match="checksum"):
        updater.download(updater.check())
    assert not list((tmp_path / "updates").glob("*.zip*"))
    monkeypatch.setattr(updater, "LATEST_API", make_release(root, base, "0.4.1", extra={"../evil.txt": "x"}))
    z = updater.download(updater.check())
    with pytest.raises(updater.UpdateError, match="unsafe"):
        updater.stage(z)


def test_no_release_or_no_network_is_a_clear_message(monkeypatch):
    monkeypatch.setattr(updater, "LATEST_API", "http://127.0.0.1:9/nothing")
    with pytest.raises(updater.UpdateError, match="couldn't check"):
        updater.check(timeout=2)


@pytest.mark.skipif(sys.platform != "win32", reason="the swap script is PowerShell")
def test_the_swap_script_replaces_the_install_and_keeps_the_old_one(tmp_path):
    exe = Path(shutil.which("where.exe"))               # any harmless real exe stands in for Lunelis.exe
    install, new = tmp_path / "Apps" / "Lunelis", tmp_path / "staged" / "Lunelis"
    for folder, version in ((install, "0.3.0"), (new, "0.4.0")):
        folder.mkdir(parents=True)
        shutil.copy(exe, folder / "Lunelis.exe")
        (folder / "version.txt").write_text(version)
    script = tmp_path / "apply.ps1"
    script.write_text(updater.APPLY_PS1, encoding="utf-8")
    gone = subprocess.Popen(["cmd", "/c", "exit"])
    gone.wait()                                          # "Lunelis" has already exited
    r = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                        "-ProcessId", str(gone.pid), "-Install", str(install), "-New", str(new)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert (install / "version.txt").read_text() == "0.4.0"
    olds = list(install.parent.glob("Lunelis.old-*"))
    assert len(olds) == 1 and (olds[0] / "version.txt").read_text() == "0.3.0"


def test_updates_tab_offers_a_newer_version(tmp_path):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from lunelis.catalog.schema import open_catalog
    from lunelis.ui.settings_view import SettingsView
    conn = open_catalog(tmp_path / "c.db")
    view = SettingsView(conn)
    older = updater.Release("0.0.1", "v0.0.1", "", "", "", "x.zip", 1, None)
    view.show_release(older)
    assert "up to date" in view.update_status.text()
    newer = updater.Release("99.0.0", "v99.0.0", "## New\n- a thing", "", "", "x.zip", 5_000_000, "u")
    view.show_release(newer)
    assert "99.0.0 is available" in view.update_status.text() and not view.skip_b.isHidden()
    view._skip_update()
    from lunelis.settings import Settings
    assert Settings(conn).get("update_skip_version") == "99.0.0"
    conn.close()


def test_an_update_refuses_a_shared_or_portable_program_folder(tmp_path, monkeypatch):
    from lunelis import paths, updater
    install = tmp_path / "Lunelis"
    (install / "_internal").mkdir(parents=True)
    (install / "Lunelis.exe").write_bytes(b"x")
    staged = tmp_path / "new"
    (staged / "_internal").mkdir(parents=True)
    (staged / "Lunelis.exe").write_bytes(b"y")
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "data")
    updater.check_install_folder(install, staged)                 # just Lunelis: fine
    (install / "OtherTool.exe").write_bytes(b"z")
    with pytest.raises(updater.UpdateError, match="also holds OtherTool.exe"):
        updater.check_install_folder(install, staged)
    (install / "OtherTool.exe").unlink()
    monkeypatch.setattr(paths, "DATA_DIR", install / "data")
    with pytest.raises(updater.UpdateError, match="data folder is inside"):
        updater.check_install_folder(install, staged)


def test_the_data_folder_cant_move_into_the_program_folder(tmp_path, monkeypatch):
    from lunelis import paths, updater
    monkeypatch.setattr(updater, "install_dir", lambda: tmp_path / "Program")
    with pytest.raises(ValueError, match="program folder"):
        paths.check_new_data_dir(tmp_path / "Program" / "data", tmp_path / "olddata")
    paths.check_new_data_dir(tmp_path / "elsewhere", tmp_path / "olddata")
