"""0.38: scrolling the Settings page past a drop-down never changes it."""
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication


def test_the_wheel_scrolls_past_a_dropdown(tmp_path):
    QApplication.instance() or QApplication([])
    from lunelis.catalog.schema import open_catalog
    from lunelis.ui.settings_view import SettingsView
    conn = open_catalog(tmp_path / "c.db")
    view = SettingsView(conn)
    try:
        combo = view.date_fmt
        before = combo.currentIndex()
        ev = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120),
                         Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(combo, ev)
        assert combo.currentIndex() == before
    finally:
        view.deleteLater()
        conn.close()
