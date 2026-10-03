"""Every test run gets its own throwaway data folder, so nothing - catalog,
thumbnails, sidecars, backups - can ever land in the real library data."""
import os
import tempfile

os.environ["LUNELIS_DATA_DIR"] = tempfile.mkdtemp(prefix="lunelis-test-data-")
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
