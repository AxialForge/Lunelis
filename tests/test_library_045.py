"""0.45: Library browsing - the wide scrollbar, the stack tray, selecting many."""
from PySide6.QtCore import QEvent, QPointF
from PySide6.QtGui import QEnterEvent
from PySide6.QtWidgets import QApplication


def test_the_scrollbar_widens_while_in_use(tmp_path):
    QApplication.instance() or QApplication([])
    from lunelis.ui.grid import PhotoGrid, WideOnUseBar
    from lunelis.ui.thumbcache import ThumbCache
    g = PhotoGrid(ThumbCache(tmp_path))
    bar = g.verticalScrollBar()
    assert isinstance(bar, WideOnUseBar) and bar.objectName() == "GridScroll"
    assert not bar.property("active")
    bar.enterEvent(QEnterEvent(QPointF(1, 1), QPointF(1, 1), QPointF(1, 1)))
    assert bar.property("active")
    bar.leaveEvent(QEvent(QEvent.Type.Leave))
    assert not bar.property("active")
    g.deleteLater()
