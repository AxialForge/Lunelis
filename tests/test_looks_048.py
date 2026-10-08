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
