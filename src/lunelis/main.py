"""Lunelis entry point: `python -m lunelis` or the `lunelis` gui-script."""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog

from lunelis import paths


def _finish_data_move() -> None:
    """A data-folder move chosen in Settings happens here, before anything
    opens the catalog. Across drives it copies, so show progress."""
    target = paths.pending_move()
    if target is None:
        return
    dlg = QProgressDialog(f"Moving Lunelis's data to {target}…", None, 0, 0)
    dlg.setWindowTitle("Lunelis")
    dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
    dlg.setMinimumDuration(0)
    dlg.show()

    def progress(done: int, total: int) -> None:
        dlg.setMaximum(total)
        dlg.setValue(done)
        QApplication.processEvents()

    try:
        paths.finish_pending_move(progress)
    except OSError as e:
        # The old folder is still complete and still in use; try again next start.
        QMessageBox.warning(None, "Lunelis", f"Couldn't finish moving the data folder:\n{e}\n\n"
                            "Lunelis will keep using the old location and try again next time.")
    finally:
        dlg.close()


def _wait_for_previous() -> None:
    """`--after PID`: a restart from Settings. Wait (up to a minute) for the
    old Lunelis to exit, so its catalog is closed before anything moves it."""
    if "--after" not in sys.argv or sys.platform != "win32":
        return
    try:
        pid = int(sys.argv[sys.argv.index("--after") + 1])
    except (IndexError, ValueError):
        return
    import ctypes
    k32 = ctypes.windll.kernel32
    handle = k32.OpenProcess(0x00100000, False, pid)          # SYNCHRONIZE
    if handle:
        k32.WaitForSingleObject(handle, 60_000)
        k32.CloseHandle(handle)


def main() -> int:
    if "--self-test" in sys.argv:                 # packaged-build check (CI); never touches real data
        from lunelis.selftest import run
        i = sys.argv.index("--self-test")
        rest = sys.argv[i + 1:]
        return run(rest[0] if rest else None, rest[1:])
    _wait_for_previous()
    # Early dev builds kept the catalog + cache in the project folder; move
    # them into the data folder once (a same-drive rename, instant).
    paths.adopt_legacy_data()

    app = QApplication(sys.argv)
    app.setApplicationName("Lunelis")
    _finish_data_move()
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    from lunelis import log
    log.setup()
    log.LOG.info("Lunelis %s starting (%s), data folder %s", paths.version(),
                 "packaged" if paths.FROZEN else "from source", paths.DATA_DIR)

    from lunelis.catalog import backup
    try:
        backup.finish_pending_restore(paths.DATA_DIR, paths.DEFAULT_CATALOG_PATH)
    except (OSError, ValueError) as e:
        QMessageBox.warning(None, "Lunelis", f"Couldn't restore the catalog backup:\n{e}\n\n"
                            "The current catalog is unchanged.")

    from lunelis.ui.main_window import MainWindow

    window = MainWindow(tray=True)
    if "--updated" in sys.argv:
        from lunelis import updater
        updater.cleanup()
        log.LOG.info("updated to %s", paths.version())
        QTimer.singleShot(1500, lambda: window.status.setText(f"Updated to Lunelis {paths.version()}"))
    if "--update-failed" in sys.argv:
        QTimer.singleShot(1500, lambda: QMessageBox.warning(
            window, "Update", "The update couldn't be installed (a file was in use, or the program folder "
            "needs administrator rights), so this version was started again. Details are in the log; putting "
            "Lunelis in a folder of your own (e.g. Documents or %LOCALAPPDATA%\\Programs) avoids this."))
    # Started at sign-in (--tray): stay in the tray - if the tray is on.
    if "--tray" not in sys.argv or window.tray is None:
        window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
