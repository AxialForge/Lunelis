"""Every test run gets its own throwaway data folder, so nothing - catalog,
thumbnails, sidecars, backups - can ever land in the real library data."""
import os
import tempfile

# Before anything imports Qt: every test file gets the windowless platform,
# however pytest was started (it used to depend on the caller setting it) (0.54).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["LUNELIS_DATA_DIR"] = tempfile.mkdtemp(prefix="lunelis-test-data-")
# ...and it is removed afterwards (0.50: they piled up in %TEMP%).
import atexit, shutil
atexit.register(shutil.rmtree, os.environ["LUNELIS_DATA_DIR"], ignore_errors=True)
# Offscreen Qt falls back to a much wider font unless it is shown Windows' own
# fonts; measure layouts with what the app really uses there (Segoe UI).
if os.name == "nt" and os.path.isdir(r"C:\Windows\Fonts"):
    os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

import pytest


@pytest.fixture(autouse=True)
def _no_crash_dialogs(monkeypatch):
    """An unexpected error in a test must fail that test - never open the app's
    modal "Something went wrong" dialog, which would hang the whole run."""
    try:
        from lunelis.ui import main_window as mw
    except Exception:
        yield
        return
    errors: list[str] = []
    import traceback

    def record(self, t, e):
        where = traceback.extract_tb(e.__traceback__)[-3:] if e.__traceback__ else []
        errors.append(f"{t.__name__}: {e} at " + " <- ".join(f"{f.name}:{f.lineno}" for f in reversed(where)))
    monkeypatch.setattr(mw.MainWindow, "_crashed", record)
    yield
    assert not errors, f"unexpected error(s) in the app: {errors}"


_exit_status = {"code": None}


def pytest_sessionfinish(session, exitstatus):
    """Let image loads and other pool work still running from the last tests
    finish while everything they use is still alive."""
    _exit_status["code"] = int(exitstatus)
    try:
        from PySide6.QtCore import QThreadPool
        from PySide6.QtWidgets import QApplication
        QThreadPool.globalInstance().waitForDone(30_000)
        if QApplication.instance() is not None:
            QApplication.processEvents()
    except Exception:
        pass


def pytest_unconfigure(config):
    """Leave with pytest's own verdict. Tearing down hundreds of leftover Qt
    widgets and threads at interpreter exit sometimes failed on the CI runner
    (all tests passed, exit code 1: 0.15.0, 0.16.2) - it says nothing about
    the app, which closes through MainWindow._finish_threads."""
    code = _exit_status["code"]
    if code is None or os.environ.get("LUNELIS_TEST_NORMAL_EXIT"):
        return
    import sys
    sys.stdout.flush()
    sys.stderr.flush()
    import faulthandler
    faulthandler.disable()                     # Qt's threads dying with the process aren't news
    if sys.platform == "win32":
        # os._exit still lets Windows unload every DLL, and Qt's teardown there
        # crashed (access violation) after a clean run. TerminateProcess skips it.
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.TerminateProcess(k32.GetCurrentProcess(), code)
    os._exit(code)


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    """A modal dialog nobody answers hangs the whole run (and blocks the
    per-test timeout). Any dialog a test didn't expect answers No / Cancel
    and fails that test, naming the dialog. Tests that expect one patch it
    themselves (their monkeypatch wins: it's applied after this)."""
    try:
        from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMessageBox
    except Exception:
        yield
        return
    shown: list[str] = []

    def box(kind, answer):
        def show(*a, **k):
            texts = [str(x) for x in a[1:3] if isinstance(x, str)]
            shown.append(f"{kind}: " + " / ".join(texts))
            return answer
        return staticmethod(show)
    no = QMessageBox.StandardButton.No
    for kind in ("question", "warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, kind, box(kind, no if kind == "question" else QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QInputDialog, "getText", box("getText", ("", False)))
    monkeypatch.setattr(QInputDialog, "getItem", box("getItem", ("", False)))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", box("getExistingDirectory", ""))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", box("getOpenFileName", ("", "")))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", box("getSaveFileName", ("", "")))

    def dialog_exec(self, *a, **k):
        shown.append(f"dialog: {self.windowTitle() or type(self).__name__}")
        return 0
    monkeypatch.setattr(QDialog, "exec", dialog_exec)
    yield
    assert not shown, f"a dialog opened that the test didn't expect: {shown}"


@pytest.fixture(autouse=True)
def _hang_watchdog(request):
    """A test stuck in native code (a Qt wait holding the GIL) stops even
    pytest-timeout. faulthandler's own C thread still fires: it prints every
    thread's stack - naming the test - and ends the run."""
    import faulthandler
    import sys
    sys.stderr.write("")
    faulthandler.dump_traceback_later(270, exit=True)
    yield
    faulthandler.cancel_dump_traceback_later()


@pytest.fixture(autouse=True)
def _no_leftover_windows():
    """Delete the windows and pages a test leaves behind. Each new MainWindow
    applies the app stylesheet, which re-styles EVERY live widget: hundreds of
    leftovers made each test slower than the last (a full run went from 17
    minutes to hours). Only windows made DURING the test: a module-scoped
    fixture's window lives on for the next test."""
    try:
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtWidgets import QApplication
    except Exception:
        yield
        return
    app = QApplication.instance()
    before = {id(w) for w in app.topLevelWidgets()} if app is not None else set()
    yield
    app = QApplication.instance()
    if app is None:
        return
    # Pool work still running (a page's drive checks, previews) reports to its
    # page: let it finish before the page goes, or it signals into a deleted one.
    from PySide6.QtCore import QThread, QThreadPool
    QThreadPool.globalInstance().waitForDone(10_000)
    QCoreApplication.processEvents()
    for w in app.topLevelWidgets():
        if id(w) in before:
            continue
        if any(t.isRunning() for t in w.findChildren(QThread)):
            continue                                # still working: leave it rather than kill a thread
        if getattr(w, "_closed", False) is False and hasattr(w, "_quitting"):
            w._quitting = True                      # a MainWindow a test didn't close
            try:
                w.close()
            except Exception:
                pass
        w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    import gc
    gc.collect()
