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


def test_the_stack_trays_close_button_shows_its_icon(tmp_path):
    # 0.53: it was an empty square - its "x" didn't fit beside the padding.
    QApplication.instance() or QApplication([])
    from lunelis.ui.stack_tray import StackTray
    from lunelis.ui.thumbcache import ThumbCache
    tray = StackTray(open_catalog(tmp_path / "c.db"), ThumbCache(tmp_path / "thumbs"))
    assert not tray.close_b.icon().isNull() and tray.close_b.width() >= 30
    tray.show()
    tray.close_b.click()
    assert tray.isHidden()
    tray.deleteLater()


def test_an_album_opens_with_a_way_back_and_library_means_all_of_it(tmp_path):
    # 0.53: an album looked like the Library with a chip - no Back, and it stayed on.
    from types import SimpleNamespace
    from test_audit_navigation import _window
    from lunelis.albums import model as albums
    w, ids = _window(tmp_path, 8)
    try:
        aid = albums.create(w.conn, "Trip", ids[:3])
        w.open_page("Library")
        everything = len(w.index)                                 # (the test catalog may hold other tests' photos)
        w.open_page("Albums")
        w.show_album(SimpleNamespace(kind="album", key=str(aid), name="Trip"))
        assert w.pages.currentWidget() is w.grid and len(w.index) == 3
        assert not w.scope_bar.isHidden() and w.scope_title.text() == "Trip" and "3 photos" in w.scope_count.text()
        assert w._nav["Albums"].isChecked()                       # the sidebar stays on Albums
        w.scope_back_b.click()                                    # Back: the Albums page again
        assert w.pages.currentWidget() is w.albums_page and w.scope_bar.isHidden()
        w._nav["Library"].click()                                 # Library is the whole library
        assert len(w.index) == everything and w.scope_bar.isHidden() and w.filter.album_id is None
        # Straight from an album to the Library in the sidebar: also everything.
        w.open_page("Albums")
        w.show_album(SimpleNamespace(kind="album", key=str(aid), name="Trip"))
        w._nav["Library"].click()
        assert len(w.index) == everything and w._nav["Library"].isChecked() and w.scope_bar.isHidden()
    finally:
        w._quitting = True
        w.close()
