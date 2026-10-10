"""Lunelis entry point: `python -m lunelis` or the `lunelis` gui-script."""
from __future__ import annotations

import os
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


def instance_name(data_dir) -> str:
    import hashlib
    return "lunelis-" + hashlib.sha1(str(data_dir).lower().encode("utf-8")).hexdigest()[:16]


def _only_instance(app) -> bool:
    """One Lunelis per data folder (0.53). A second one - a double-click while
    the first sits in the tray - used to open the same catalog: its job runner
    took the first one's running job for an interrupted one and ran it again,
    two workers on the same migration or backup. Now it asks the first to show
    its window and leaves."""
    from PySide6.QtCore import QLockFile
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    name = instance_name(paths.DATA_DIR)
    lock = QLockFile(str(paths.DATA_DIR / "lunelis.lock"))
    lock.setStaleLockTime(0)                           # a crashed Lunelis's lock is seen as stale by its PID
    if not lock.tryLock(100):
        sock = QLocalSocket()
        sock.connectToServer(name)
        if sock.waitForConnected(1500):
            sock.write(b"show")
            sock.waitForBytesWritten(1000)
            sock.disconnectFromServer()
        else:
            QMessageBox.information(None, "Lunelis", "Lunelis is already running - look for its icon near the "
                                    "clock. (If it isn't, wait a moment and try again.)")
        return False
    app._lunelis_lock = lock                           # held until the process ends
    QLocalServer.removeServer(name)
    server = QLocalServer(app)
    server.listen(name)
    app._lunelis_server = server
    return True


def _listen_for_second_start(app, window) -> None:
    server = getattr(app, "_lunelis_server", None)
    if server is None:
        return

    def show() -> None:
        while server.hasPendingConnections():
            server.nextPendingConnection().deleteLater()
        window.open_page(getattr(window, "_page_name", None) or "Library")
    server.newConnection.connect(show)


def main() -> int:
    if "--self-test" in sys.argv:                 # packaged-build check (CI); never touches real data
        from lunelis.selftest import run
        i = sys.argv.index("--self-test")
        rest = sys.argv[i + 1:]
        return run(rest[0] if rest else None, rest[1:])
    _wait_for_previous()
    if paths.FROZEN:
        # Out of the program folder: an update renames it, and Windows won't
        # rename a folder a process is standing in.
        try:
            os.chdir(os.path.expanduser("~"))
        except OSError:
            pass
    # Early dev builds kept the catalog + cache in the project folder; move
    # them into the data folder once (a same-drive rename, instant).
    paths.adopt_legacy_data()

    app = QApplication(sys.argv)
    app.setApplicationName("Lunelis")
    # The installer's answers (setup.json): a chosen data folder is settled
    # before anything opens the catalog; the rest is applied once the window is up.
    from lunelis import firstrun
    setup = firstrun.read()
    if setup is not None:
        firstrun.early(setup)
    _finish_data_move()
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    from lunelis import log
    log.setup()
    if not _only_instance(app):
        return 0
    log.LOG.info("Lunelis %s starting (%s), data folder %s", paths.version(),
                 "packaged" if paths.FROZEN else "from source", paths.DATA_DIR)

    from lunelis.catalog import backup
    try:
        backup.finish_pending_restore(paths.DATA_DIR, paths.DEFAULT_CATALOG_PATH)
    except (OSError, ValueError) as e:
        QMessageBox.warning(None, "Lunelis", f"Couldn't restore the catalog backup:\n{e}\n\n"
                            "The current catalog is unchanged.")

    if paths.LOCATION_PROBLEM:
        log.LOG.error("%s", paths.LOCATION_PROBLEM)
        QMessageBox.warning(None, "Lunelis", f"{paths.LOCATION_PROBLEM}.\n\nLunelis is using the default data "
                            f"folder ({paths.DATA_DIR}) for now. If you had moved your data folder, it is still where "
                            "you put it and nothing in it was lost: close Lunelis and repair or delete that file "
                            "(Help > Report a problem can help).")
    if not _catalog_ok(backup):
        return 1

    from lunelis.ui.main_window import MainWindow

    window = MainWindow(tray=True)
    _listen_for_second_start(app, window)
    if "--updated" in sys.argv:
        from lunelis import updater
        updater.cleanup()
        updater.record_installed_version(paths.version())
        log.LOG.info("updated to %s", paths.version())
        QTimer.singleShot(1500, lambda: window.status.setText(f"Updated to Lunelis {paths.version()}"))
    if "--update-failed" in sys.argv:
        from lunelis import updater
        log.LOG.error("The update wasn't applied. The swap script said:\n%s", updater.last_apply_log())
        QTimer.singleShot(1500, lambda: QMessageBox.warning(
            window, "Update", "The update couldn't be installed (a file was in use, or the program folder "
            "needs administrator rights), so this version was started again. Details are in the log; putting "
            "Lunelis in a folder of your own (e.g. Documents or %LOCALAPPDATA%\\Programs) avoids this."))
    # Started at sign-in (--tray): stay in the tray - if the tray is on.
    if "--tray" not in sys.argv or window.tray is None:
        window.show()
        if setup is not None:
            QTimer.singleShot(400, lambda: window.apply_setup(setup))
        else:
            QTimer.singleShot(600, window.maybe_welcome)
    return app.exec()


def _catalog_ok(backup) -> bool:
    """A damaged or newer catalog is a message with a way out, never a
    traceback - and never silently a new, empty library."""
    from lunelis import log
    from lunelis.catalog import schema
    cat = paths.DEFAULT_CATALOG_PATH
    why = backup.problem(cat)
    if why is None:
        try:
            schema.migrate(cat)
            return True
        except schema.NewerCatalog as e:
            log.LOG.error("%s", e)
            QMessageBox.critical(None, "Lunelis", f"{e}\n\nInstall the newest Lunelis from Settings > Updates "
                                 "or github.com/AxialForge/Lunelis/releases. Your catalog is unchanged.")
            return False
        except Exception as e:
            import sqlite3
            damaged = isinstance(e, sqlite3.DatabaseError) and any(
                w in str(e).lower() for w in ("malformed", "not a database", "corrupt", "disk image"))
            if not damaged:
                # The catalog reads fine; upgrading it failed (its safety backup
                # couldn't be written - a backup folder on a sleeping share, a
                # full disk). Calling that "damaged" offered an empty catalog
                # as the way out (0.53).
                log.LOG.error("Couldn't upgrade the catalog: %s", e, exc_info=True)
                QMessageBox.critical(
                    None, "Lunelis", f"Lunelis couldn't get its catalog ready for this version:\n\n{e}\n\n"
                    "Your catalog and your photos are unchanged. This is usually the catalog backup folder "
                    "not being reachable (a network share that's asleep) or a full disk. Fix that and start "
                    "Lunelis again.")
                return False
            why = f"damaged: {e}"                      # e.g. a page damaged past the header
    log.LOG.error("Catalog problem at start-up: %s", why)
    snaps = backup.all_snapshots(cat, paths.DATA_DIR)
    box = QMessageBox(QMessageBox.Icon.Warning, "Lunelis",
                      f"Lunelis can't open its catalog ({why}).\n\nYour photos are not affected - the catalog "
                      "holds only Lunelis's own records (ratings, tags, albums...).")
    restore_b = None
    if snaps:
        box.setInformativeText(f"The newest catalog backup is {snaps[0].name}. Restoring it brings back "
                               "everything up to then; the damaged catalog is kept beside it.")
        restore_b = box.addButton("Restore the newest backup", QMessageBox.ButtonRole.AcceptRole)
    fresh_b = box.addButton("Start with an empty catalog", QMessageBox.ButtonRole.DestructiveRole)
    box.addButton("Quit", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    try:
        if restore_b is not None and box.clickedButton() is restore_b:
            backup.set_aside_damaged(cat)
            backup.restore(snaps[0], cat)
            log.LOG.info("Restored the catalog from %s", snaps[0])
            return True
        if box.clickedButton() is fresh_b:
            kept = backup.set_aside_damaged(cat)
            log.LOG.info("Damaged catalog set aside as %s; starting empty", kept)
            return True
    except (OSError, ValueError) as e:
        QMessageBox.critical(None, "Lunelis", f"That didn't work: {e}\n\nNothing was deleted.")
    return False


if __name__ == "__main__":
    raise SystemExit(main())
