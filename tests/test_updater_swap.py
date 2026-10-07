"""0.37.6: the update's swap script really swaps the program folder - even when
Lunelis was started from inside that folder (the Start menu does that)."""
import os
import shutil
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows update script")


def _fake_app(folder, marker):
    folder.mkdir(parents=True)
    shutil.copy2(os.path.join(os.environ["SystemRoot"], "System32", "where.exe"), folder / "Lunelis.exe")
    (folder / "version.txt").write_text(marker, encoding="utf-8")


def test_the_swap_works_from_a_process_standing_in_the_program_folder(tmp_path, monkeypatch):
    from lunelis import updater
    install, new = tmp_path / "Programs" / "Lunelis", tmp_path / "updates" / "new" / "Lunelis"
    _fake_app(install, "old")
    _fake_app(new, "new")
    monkeypatch.setattr(updater, "updates_dir", lambda: tmp_path / "updates")
    script = tmp_path / "updates" / "apply-update.ps1"
    script.write_text(updater.APPLY_PS1, encoding="utf-8")
    # A stand-in for Lunelis that has already closed.
    gone = subprocess.Popen(["cmd.exe", "/c", "exit"], cwd=str(install))
    gone.wait()
    # The bug: the script was started in the program folder (Lunelis's working folder).
    r = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                        "-ProcessId", str(gone.pid), "-Install", str(install), "-New", str(new)],
                       cwd=str(install), capture_output=True, text=True, timeout=120)
    log = (tmp_path / "updates" / "apply-update.log").read_text(encoding="utf-8")
    assert r.returncode == 0, (r.stdout, r.stderr, log)
    assert (install / "version.txt").read_text(encoding="utf-8") == "new", log
    assert list((tmp_path / "Programs").glob("Lunelis.old-*")), log
    time.sleep(0.5)


def test_the_swap_script_really_starts(tmp_path):
    """0.38.1: Lunelis launched PowerShell with DETACHED_PROCESS, which exits at
    once without running the script - updates closed Lunelis and did nothing."""
    import sys
    import pytest
    if sys.platform != "win32":
        pytest.skip("Windows only")
    from lunelis import updater
    script = tmp_path / "probe.ps1"
    script.write_text("param([string]$Say)\nSet-Content -LiteralPath (Join-Path $PSScriptRoot 'ran.txt') -Value $Say\n",
                      encoding="utf-8")
    updater.run_script(script, "-Say", "yes").wait(60)
    assert (tmp_path / "ran.txt").read_text().strip() == "yes"
    assert not updater.LAUNCH_FLAGS & 0x00000008
