"""v0.19: Space toggles the selection, the menu key opens the Photo menu at
the photo, album tiles are reachable with Tab and open with Enter."""
from PIL import Image
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from lunelis.importers.scan import add_root, scan_root


def key(widget, k, mods=Qt.KeyboardModifier.NoModifier):
    widget.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, k, mods))


def test_space_toggles_and_the_menu_key_opens_the_photo_menu(tmp_path):
    QApplication.instance() or QApplication([])
    from lunelis.ui import main_window as mw
    w = mw.MainWindow()
    try:
        root = tmp_path / "K"
        root.mkdir()
        for i in range(3):
            Image.new("RGB", (30, 20)).save(root / f"K{i}.jpg")
        scan_root(w.conn, add_root(w.conn, root))
        w.reload()
        g = w.grid
        g._set_current(0, Qt.KeyboardModifier.NoModifier)
        first = g.index.file_id(0)
        key(g, Qt.Key.Key_Space)
        assert first not in g.selected
        key(g, Qt.Key.Key_Space)
        assert first in g.selected
        g._set_current(1, Qt.KeyboardModifier.ControlModifier)
        opened = []
        g.customContextMenuRequested.disconnect()
        g.customContextMenuRequested.connect(opened.append)
        key(g, Qt.Key.Key_F10, Qt.KeyboardModifier.ShiftModifier)
        assert opened and g._tile_rect(1).contains(opened[0])
        key(g, Qt.Key.Key_Menu)
        assert len(opened) == 2
    finally:
        w._quitting = True
        w.close()


def test_album_tiles_take_focus_and_open_with_enter(tmp_path):
    QApplication.instance() or QApplication([])
    from lunelis import paths
    from lunelis.albums.model import Album
    from lunelis.ui.albums_view import AlbumTile
    from lunelis.ui.thumbcache import ThumbCache
    tile = AlbumTile(Album("album", "1", "Trip", 3, None), ThumbCache(paths.THUMBNAIL_CACHE))
    assert tile.focusPolicy() == Qt.FocusPolicy.StrongFocus
    opened, menus = [], []
    tile.clicked.connect(opened.append)
    tile.menu.connect(lambda a, pos: menus.append(a))
    key(tile, Qt.Key.Key_Return)
    key(tile, Qt.Key.Key_Menu)
    assert [a.name for a in opened] == ["Trip"] and [a.name for a in menus] == ["Trip"]
