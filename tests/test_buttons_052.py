"""0.52: buttons wired straight to a no-argument signal passed their checked
state along - "open_wizard() only accepts 0 argument(s), 1 given". Each one
is clicked here, the way a person would."""
from PySide6.QtWidgets import QApplication, QPushButton

from lunelis.catalog.schema import open_catalog


def _click(widget, text: str) -> None:
    b = next(b for b in widget.findChildren(QPushButton) if b.text().startswith(text))
    b.click()


def test_the_buttons_that_open_something_work_when_clicked(tmp_path):
    QApplication.instance() or QApplication([])
    from lunelis.ui.migrate_view import MigrateView
    from lunelis.ui.migration_wizard import MigrationWizard
    from lunelis.ui.stack_tray import StackTray
    from lunelis.ui.thumbcache import ThumbCache
    conn = open_catalog(tmp_path / "c.db")
    got = []
    view = MigrateView(conn)
    view.open_wizard.connect(lambda: got.append("wizard"))
    _click(view, "Migration wizard")
    view.bg.wait()
    wiz = MigrationWizard(conn)
    wiz.open_takeout.connect(lambda: got.append("takeout"))
    _click(wiz, "Open the Google Takeout page")
    tray = StackTray(conn, ThumbCache(tmp_path / "thumbs"))
    tray.open_stack.connect(lambda: got.append("stack"))
    _click(tray, "Open the stack")
    assert got == ["wizard", "takeout", "stack"]
    for w in (view, wiz, tray):
        w.deleteLater()
