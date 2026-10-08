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
