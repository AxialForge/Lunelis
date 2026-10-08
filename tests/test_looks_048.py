"""0.48: looks and layout - from the 25-item list."""
from PySide6.QtWidgets import QApplication


def _win(tmp_path, n=2):
    from test_audit_navigation import _window
    return _window(tmp_path, n)


def test_a_short_window_tightens_the_sidebar_so_every_section_fits(tmp_path):
    w, _ = _win(tmp_path)
    try:
        w.show()
        w.resize(1300, 1000)
        w._auto_sidebar()
        tall = w.sidebar.findChild(type(w.sidebar), "SidebarNav").sizeHint().height()
        w.resize(1300, 700)
        w._auto_sidebar()
        QApplication.processEvents()
        assert w.sidebar.property("dense") is True
        short = w.sidebar.findChild(type(w.sidebar), "SidebarNav").sizeHint().height()
        assert short < tall
    finally:
        w._quitting = True
        w.close()


def test_every_create_tool_has_its_own_icon_and_create_isnt_library():
    from lunelis.ui import icons
    from lunelis.ui.create_page import CARD_ICONS, TOOLS
    names = [CARD_ICONS[k] for k, *_ in TOOLS]
    assert len(set(names)) == len(TOOLS) == 13
    assert all(n in icons._PATHS for n in names)
    assert len({icons._PATHS[n] for n in names}) == 13                     # 13 different drawings
    assert icons._PATHS[icons.NAV_ICONS["Create"]] != icons._PATHS[icons.NAV_ICONS["Library"]]


def test_empty_pages_say_what_to_do(tmp_path):
    w, _ = _win(tmp_path, 1)
    try:
        for page, attr in (("People", "people_page"), ("Tags", "tags_page"), ("Damaged files", "damaged")):
            w.open_page(page)
            p = getattr(w, attr)
            for _ in range(3):                    # loads run on workers; their results arrive as events
                if hasattr(p, "bg"):
                    p.bg.wait()
                QApplication.processEvents()
        assert w.people_page.people_box.showing_message
        assert "Nobody named yet" in w.people_page.people_box.note.text()
        assert w.tags_page.tree_box.showing_message and "No tags yet" in w.tags_page.tree_box.note.text()
        assert w.damaged.table_box.showing_message
        from lunelis.ui.map_view import MapCanvas
        c = MapCanvas(tmp_path)
        c.resize(400, 300)
        c.grab()                                                           # paints the empty-map message
        assert not hasattr(w.map_page, "note_b")                          # one switch for map pictures
    finally:
        w._quitting = True
        w.close()


def test_a_theme_switch_recolours_links_and_create_icons(tmp_path):
    from lunelis.ui import theme
    w, _ = _win(tmp_path, 1)
    try:
        w.apply_theme("graphite")
        w.status.set_link("See ", "Library status", "Library status")
        card = next(iter(w.create_page.cards.values()))
        before_icon = card._pic.pixmap().toImage()
        light = theme.current().accent
        w.apply_theme("high_contrast")
        dark = theme.current().accent
        assert light != dark
        assert dark in w.status.text() and light not in w.status.text()
        assert card._pic.pixmap().toImage() != before_icon
    finally:
        w.apply_theme("graphite")
        w._quitting = True
        w.close()


def test_settings_tabs_keep_whole_names_at_1024(tmp_path):
    w, _ = _win(tmp_path, 1)
    try:
        w.show()
        w.resize(1024, 700)
        w.open_page("Settings")
        QApplication.processEvents()
        bar = w.settings_page.tabs.tabBar()
        from PySide6.QtCore import Qt
        assert bar.elideMode() == Qt.TextElideMode.ElideNone and bar.usesScrollButtons()
        fm = bar.fontMetrics()
        i = [bar.tabText(k) for k in range(bar.count())].index("Advanced")
        assert bar.tabRect(i).width() >= fm.horizontalAdvance("Advanced")
    finally:
        w._quitting = True
        w.close()


def test_pages_fit_a_1024_px_window(tmp_path):
    """0.48: Albums, Settings, Create, Google Takeout and Timelapses were wider
    than a 1024 px window (long check boxes, unwrapped labels, wide headers)."""
    from PySide6.QtWidgets import QScrollArea
    w, _ = _win(tmp_path, 1)
    try:
        w.show()
        w.resize(1024, 720)
        for name in ("Albums", "Create", "Google Takeout", "Timelapses"):   # Settings: measured in a real start (the test font is wider)
            w.open_page(name)
            QApplication.processEvents()
            page = w.pages.currentWidget()
            for sa in [page, *page.findChildren(QScrollArea)]:
                if isinstance(sa, QScrollArea) and sa.isVisible() and sa.widget() is not None:
                    assert sa.widget().minimumSizeHint().width() <= sa.viewport().width() + 2, name
    finally:
        w._quitting = True
        w.close()
