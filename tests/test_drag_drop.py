"""v0.19: drag photos out (as files) and onto albums (as photos)."""
import json
import os

from PIL import Image
from PySide6.QtCore import QMimeData, QPointF, Qt
from PySide6.QtWidgets import QApplication

from lunelis.importers.scan import add_root, scan_root


def test_a_drag_carries_the_selection_as_files_and_ids(tmp_path):
    QApplication.instance() or QApplication([])
    from lunelis.ui import main_window as mw
    from lunelis.ui.grid import PhotoGrid
    w = mw.MainWindow()
    try:
        root = tmp_path / "D"
        root.mkdir()
        for i in range(3):
            Image.new("RGB", (30, 20)).save(root / f"D{i}.jpg")
        rid = add_root(w.conn, root)
        scan_root(w.conn, rid)
        w.reload()
        g = w.grid
        mine = [g.index.file_id(i) for i in range(len(g.index))
                if g.index.file_id(i) in {r[0] for r in w.conn.execute("SELECT id FROM files WHERE root_id = ?", (rid,))}]
        g.selected = set(mine[:2])
        pos = g.index.position(mine[0])
        assert sorted(g.drag_ids(pos)) == sorted(mine[:2])             # a selected tile drags the selection
        other = g.index.position(mine[2])
        assert g.drag_ids(other) == [mine[2]]                           # an unselected one only itself
        mime = g.drag_mime(mine[:2])
        files = sorted(os.path.basename(u.toLocalFile()) for u in mime.urls())
        assert files == sorted(os.path.basename(p) for p in w._paths_of(mine[:2]))
        assert json.loads(bytes(mime.data(PhotoGrid.DRAG_MIME)).decode()) == mine[:2]

        # dropped on an album: added, and Ctrl+Z takes it out again
        from lunelis.albums import model as albums
        aid = albums.create(w.conn, "Dropped")
        album = next(a for a in albums.your_albums(w.conn) if a.key == str(aid))
        w._dropped_on_album(album, mine[:2])
        assert next(a.count for a in albums.your_albums(w.conn) if a.key == str(aid)) == 2
        w.undo()
        assert next(a.count for a in albums.your_albums(w.conn) if a.key == str(aid)) == 0
    finally:
        w._quitting = True
        w.close()


def test_album_tiles_take_drops_and_the_sidebar_springs_open():
    QApplication.instance() or QApplication([])
    from PySide6.QtWidgets import QPushButton
    from lunelis import paths
    from lunelis.albums.model import Album
    from lunelis.ui.albums_view import AlbumTile
    from lunelis.ui.grid import PhotoGrid
    from lunelis.ui.main_window import SpringLoad
    from lunelis.ui.thumbcache import ThumbCache
    tile = AlbumTile(Album("album", "4", "Trip", 0, None), ThumbCache(paths.THUMBNAIL_CACHE))
    auto = AlbumTile(Album("auto", "picks", "Picks", 0, None), ThumbCache(paths.THUMBNAIL_CACHE))
    assert tile.acceptDrops() and not auto.acceptDrops()
    got = []
    tile.dropped.connect(lambda a, ids: got.append((a.name, ids)))

    class Drop:
        def __init__(self):
            self.m = QMimeData()
            self.m.setData(PhotoGrid.DRAG_MIME, b"[5, 6]")

        def mimeData(self):
            return self.m

        def acceptProposedAction(self):
            pass
    tile.dropEvent(Drop())
    assert got == [("Trip", [5, 6])]
    opened = []
    b = QPushButton("Albums")
    spring = SpringLoad(b, lambda: opened.append(1))
    assert b.acceptDrops() and spring.timer.interval() == SpringLoad.DELAY_MS
