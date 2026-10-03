"""v0.3.0: themes, the sectioned sidebar, Settings tabs, the log, tick lists."""
import logging
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QListWidget, QListWidgetItem  # noqa: E402

from lunelis import log, paths  # noqa: E402
from lunelis.settings import Settings  # noqa: E402
from lunelis.ui import theme  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app):
    from lunelis.ui.main_window import MainWindow
    w = MainWindow()
    yield w
    w._quitting = True
    w.close()


def test_every_theme_has_every_token_and_a_stylesheet(app, tmp_path):
    for t in theme.THEMES.values():
        css = theme.stylesheet(t)
        assert t.canvas in css and t.accent in css
        assert "check-" in css                                 # the tick drawn in this theme's colour
    assert theme.resolve("midnight") is theme.MIDNIGHT
    assert theme.resolve("system") in (theme.GRAPHITE, theme.MIDNIGHT)
    from lunelis.catalog.schema import open_catalog
    conn = open_catalog(tmp_path / "t.db")
    with pytest.raises(ValueError):
        Settings(conn).set("theme", "neon")
    conn.close()


def test_switching_theme_restyles_live(window, app):
    Settings(window.conn).set("theme", "midnight")
    window.apply_theme()
    assert theme.current() is theme.MIDNIGHT
    assert theme.MIDNIGHT.canvas in app.styleSheet()
    Settings(window.conn).set("theme", "graphite")
    window.apply_theme()
    assert theme.current() is theme.GRAPHITE


def test_sidebar_sections_fold_and_remember(window):
    nav = window._nav
    assert set(nav) == {"Library", "Albums", "Tags", "Import", "Migrate", "Duplicates", "Damaged files", "Edit", "Library status", "Backups",
                        "Quarantine", "Settings"}                            # no greyed-out placeholders any more
    heads = [b for b in window.findChildren(type(nav["Library"]), "NavSection")]
    organise = next(h for h in heads if "ORGANIZE" in h.text())
    assert "&&" in organise.text()                            # a literal '&', not a shortcut marker
    organise.click()
    assert nav["Import"].isHidden() and not nav["Library"].isHidden()
    assert "Bring in & organize" in Settings(window.conn).get("sidebar_collapsed")
    window.open_page("Migrate")                               # a folded section still shows where you are
    assert not nav["Migrate"].isHidden()
    window.open_page("Library")
    assert nav["Migrate"].isHidden()
    organise.click()
    assert not nav["Import"].isHidden()


def test_settings_has_tabs(window):
    sv = window.settings_page
    assert [sv.tabs.tabText(i).replace("&&", "&") for i in range(sv.tabs.count())] == list(sv.TABS)
    sv.show_tab("Appearance")
    assert sv.tabs.tabText(sv.tabs.currentIndex()) == "Appearance"


def test_the_log_records_errors_and_diagnostics_collect_it(window, tmp_path, monkeypatch):
    for h in list(log.LOG.handlers):
        log.LOG.removeHandler(h)
    log.setup()
    logging.getLogger("lunelis.test").error("something broke: %s", "disk full")
    for h in log.LOG.handlers:
        h.flush()
    assert "something broke: disk full" in log.log_file().read_text(encoding="utf-8")
    text = log.diagnostics(window.conn)
    assert f"Lunelis {paths.version()}" in text and "something broke" in text and "Catalog schema" in text
    window.status.setText("A status line the user saw")
    for h in log.LOG.handlers:
        h.flush()
    assert "A status line the user saw" in log.log_file().read_text(encoding="utf-8")


def test_tick_lists_toggle_on_a_click_anywhere_in_the_row(app):
    from lunelis.ui.widgets import row_toggles
    lw = QListWidget()
    row_toggles(lw)
    it = QListWidgetItem("\\\\nas\\photos")
    it.setCheckState(Qt.CheckState.Unchecked)
    lw.addItem(it)
    lw.itemClicked.emit(it)
    assert it.checkState() == Qt.CheckState.Checked
    lw.itemClicked.emit(it)
    assert it.checkState() == Qt.CheckState.Unchecked


# --- v0.13.0: icon sidebar, status strip, small and scaled screens ----------------------------

def test_the_sidebar_folds_to_icons_and_remembers(window):
    from lunelis.ui.main_window import SIDEBAR_NARROW, SIDEBAR_WIDE
    lib = window._nav["Library"]
    assert window.sidebar.width() == SIDEBAR_WIDE and "Library" in lib.text() and not lib.icon().isNull()
    window.collapse_b.click()                                        # icons only
    assert Settings(window.conn).get("sidebar_compact") is True
    assert window.sidebar.width() in range(SIDEBAR_NARROW, SIDEBAR_WIDE + 1)   # animating (window not shown)
    window.set_sidebar_compact(True, animate=False)
    assert window.sidebar.width() == SIDEBAR_NARROW
    assert lib.text() == "" and lib.toolTip() == "Library"
    assert all(not h.isVisibleTo(window) for h in window._nav_heads)
    assert window._sidebar_action.isChecked()
    window._sidebar_action.trigger()                                 # Ctrl+B: back to names
    assert Settings(window.conn).get("sidebar_compact") is False and "Library" in lib.text()
    with pytest.raises(ValueError):
        Settings(window.conn).set("sidebar_compact", "yes")


def test_status_strip_shows_the_scan_step_and_links_to_a_page(window):
    from lunelis.ui.main_window import SCAN_STEPS
    window._scan_step(2, 40, 100)
    assert f">Step 3 of {len(SCAN_STEPS)} · Reading metadata</a>" in window.scan_step.text()
    assert 'href="page:Library status"' in window.scan_step.text()
    assert window.scan_bar.maximum() == 100 and window.scan_bar.value() == 40
    window._scan_step(0, 0, 0)                                       # no total yet: a busy bar
    assert window.scan_bar.maximum() == 0

    class Found:
        found = {"truncated": 2}
    window._on_damage_done(Found())
    assert 'href="page:Damaged files"' in window.library_state.text()
    window.library_state.linkActivated.emit("page:Damaged files")
    assert window.pages.currentWidget() is window.damaged


def test_the_window_fits_small_and_scaled_screens(app):
    # Windows' UI font, as the real app gets it (offscreen Qt picks another).
    from PySide6.QtGui import QFont
    from lunelis.ui.main_window import MainWindow
    old = app.font()
    app.setFont(QFont("Segoe UI", 9))
    window = MainWindow()
    try:
        _fits(window)
    finally:
        window._quitting = True
        window.close()
        app.setFont(old)


def _fits(window):
    # 1366 x 768 at 125 % leaves about 1093 x 614 for the window.
    hint = window.minimumSizeHint()
    assert hint.width() <= 1000 and hint.height() <= 614
    for name in ("Import", "Events", "Duplicates", "Migrate", "Backups", "Quarantine"):
        window.open_page(name)
        hint = window.minimumSizeHint()
        assert hint.width() <= 1000 and hint.height() <= 614, name
    # Pages in scroll areas still look like themselves to the rest of the code.
    window.open_page("Import")
    assert window.pages.currentWidget() is window.importer
    assert window.pages.widget(window.pages.indexOf(window.importer)) is window.importer


# --- v0.16.0: the shortcut sheet, the automatic icon sidebar -------------------------------

def test_the_sidebar_folds_to_icons_on_a_narrow_window(window):
    from lunelis.ui.main_window import AUTO_SIDEBAR_WIDTH
    window.resize(AUTO_SIDEBAR_WIDTH + 200, 800)
    window._auto_sidebar()
    assert not window._sidebar_compact
    window.resize(AUTO_SIDEBAR_WIDTH - 200, 700)
    window._auto_sidebar()
    assert window._sidebar_compact                                   # folded by itself...
    assert Settings(window.conn).get("sidebar_compact") is False     # ...without changing your choice
    window.toggle_sidebar(False)                                     # you open it: it stays open
    window._auto_sidebar()
    assert not window._sidebar_compact
    window.resize(AUTO_SIDEBAR_WIDTH + 200, 800)                     # wide again, then narrow again
    window._auto_sidebar()
    window.resize(AUTO_SIDEBAR_WIDTH - 200, 700)
    window._auto_sidebar()
    assert window._sidebar_compact
    Settings(window.conn).set("sidebar_auto", False)                 # Settings > Appearance: off
    window._auto_sidebar()
    assert not window._sidebar_compact


def test_the_shortcut_sheet_lists_this_screens_keys_first(window, monkeypatch):
    from lunelis.ui.shortcuts import ShortcutSheet
    sheet = ShortcutSheet(window, "Photo view")
    assert sheet.rows[0][0] == "Photo view"
    keys = {k for _, k, _ in sheet.rows}
    assert {"Ctrl+O", "F5", "Ctrl+Shift+H", "?"} <= keys              # menu shortcuts read from the menus
    shown = []
    monkeypatch.setattr(ShortcutSheet, "exec", lambda self: shown.append(self.rows[0][0]) or 0)
    window.show_shortcuts()
    assert shown == ["Library"]


def test_window_size_is_remembered(app):
    from lunelis.ui.main_window import MainWindow
    w = MainWindow()
    w.show()
    w.resize(1320, 760)
    app.processEvents()
    w._quitting = True
    w.close()
    w2 = MainWindow()
    w2.show()
    app.processEvents()
    try:
        assert w2.height() == 760          # the offscreen test screen is 800 wide, so only the height can come back whole
        assert Settings(w2.conn).get("window_geometry")
    finally:
        w2._quitting = True
        w2.close()
