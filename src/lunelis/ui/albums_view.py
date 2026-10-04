"""
The Albums page (Google Photos style): cover tiles in three sections -
Your albums, Events, and Automatic albums (Favorites, Picks, Videos, RAW,
Recently imported, Screenshots, and one per camera). Clicking a tile shows
those photos in the library, as a filter you can clear with its chip.

Counting the automatic albums takes about a second on a large library, so
the page draws your albums and events at once and fills in the automatic
ones from a background thread.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QRect, QRectF, QSize, Qt, QThread, Signal
from PySide6.QtGui import QFont, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QMenu, QMessageBox, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from lunelis import paths
from lunelis.albums import model as albums
from lunelis.catalog.schema import open_catalog
from lunelis.raw.thumbnails import cache_rel_path
from lunelis.ui import theme as themes
from lunelis.ui.theme import qcolor
from lunelis.ui.thumbcache import ThumbCache

TILE = 196


class _AutoWorker(QObject):
    done = Signal(object)            # list[Album] | Exception

    def run(self) -> None:
        conn = open_catalog(paths.DEFAULT_CATALOG_PATH)
        try:
            self.done.emit(albums.auto_albums(conn))
        except Exception as e:       # shown on the page, never fatal
            self.done.emit(e)
        finally:
            conn.close()


class AlbumTile(QWidget):
    """A square cover + name + count. Clicks open; right-click for actions."""

    clicked = Signal(object)         # Album
    menu = Signal(object, object)    # Album, global pos

    def __init__(self, album: albums.Album, thumbs: ThumbCache, parent=None) -> None:
        super().__init__(parent)
        self.album, self.thumbs = album, thumbs
        self.setFixedSize(TILE, TILE + 46)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(album.blurb or album.name)
        self._title = QFont(self.font())
        self._title.setPixelSize(13)
        self._title.setBold(True)
        self._sub = QFont(self.font())
        self._sub.setPixelSize(12)
        self._hover = False

    def paintEvent(self, e) -> None:
        t = themes.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRect(0, 0, TILE, TILE)
        path = QPainterPath()
        path.addRoundedRect(QRectF(r), 10, 10)
        pix = None
        if self.album.cover_id:
            pix = self.thumbs.get(self.album.cover_id, cache_rel_path(self.album.cover_id))
        if pix is not None:
            p.save()
            p.setClipPath(path)
            p.drawPixmap(r, pix)
            p.restore()
        else:
            p.fillPath(path, qcolor(t.tile_placeholder))
        if self._hover:
            p.fillPath(path, qcolor("rgba(0,0,0,0.12)"))
        p.setPen(qcolor(t.text))
        p.setFont(self._title)
        fm = p.fontMetrics()
        p.drawText(QRect(2, TILE + 6, TILE - 4, 18), Qt.AlignmentFlag.AlignLeft,
                   fm.elidedText(self.album.name, Qt.TextElideMode.ElideRight, TILE - 4))
        p.setPen(qcolor(t.text_muted))
        p.setFont(self._sub)
        sub = f"{self.album.count:,} item{'s' if self.album.count != 1 else ''}"
        if self.album.kind == "event" and self.album.start_at:
            from lunelis.events.model import date_range_text
            sub = f"{date_range_text(self.album.start_at, self.album.end_at)} · {sub}"
        p.drawText(QRect(2, TILE + 25, TILE - 4, 18), Qt.AlignmentFlag.AlignLeft,
                   p.fontMetrics().elidedText(sub, Qt.TextElideMode.ElideRight, TILE - 4))

    def enterEvent(self, e) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, e) -> None:
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit(self.album)

    def contextMenuEvent(self, e) -> None:
        self.menu.emit(self.album, e.globalPos())


class NewAlbumTile(QWidget):
    # A dashed square the size of a cover: "+ New album".
    clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(TILE, TILE + 46)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover = False
        self._font = QFont(self.font())
        self._font.setPixelSize(14)

    def paintEvent(self, e) -> None:
        from PySide6.QtGui import QPen
        t = themes.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(qcolor(t.accent if self._hover else t.border), 2, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawRoundedRect(QRectF(1, 1, TILE - 2, TILE - 2), 10, 10)
        p.setPen(qcolor(t.text if self._hover else t.text_muted))
        p.setFont(self._font)
        p.drawText(QRect(0, 0, TILE, TILE), Qt.AlignmentFlag.AlignCenter, "+\nNew album")

    def enterEvent(self, e) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, e) -> None:
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


class TileFlow(QWidget):
    """Tiles in rows that re-flow with the page width."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(18)
        self.grid.setVerticalSpacing(18)
        self.widgets: list[QWidget] = []
        self._cols = 0

    def set_widgets(self, widgets: list[QWidget]) -> None:
        for w in self.widgets:
            w.setParent(None)
            w.deleteLater()
        self.widgets = widgets
        self._cols = 0
        self._reflow()

    def _reflow(self) -> None:
        cols = max(1, (self.width() + 18) // (TILE + 18))
        if cols == self._cols and self.grid.count() == len(self.widgets):
            return
        self._cols = cols
        while self.grid.count():
            self.grid.takeAt(0)
        for n, w in enumerate(self.widgets):
            self.grid.addWidget(w, n // cols, n % cols, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.grid.setColumnStretch(cols, 1)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._reflow()

    def sizeHint(self) -> QSize:
        cols = max(1, self._cols or 4)
        rows = -(-len(self.widgets) // cols)
        return QSize(cols * (TILE + 18), rows * (TILE + 46 + 18))


class AlbumsView(QWidget):
    open_album = Signal(object)      # Album -> the library, filtered
    open_suggestions = Signal()      # the event suggestions page
    changed = Signal()

    def __init__(self, conn, parent=None) -> None:
        super().__init__(parent)
        self.conn = conn
        self.thumbs = ThumbCache(paths.THUMBNAIL_CACHE, self)
        self.thumbs.set_tile_size(256)
        self.thumbs.ready.connect(lambda _: self._repaint_tiles())
        self._thread: QThread | None = None
        self._auto: list[albums.Album] | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = QWidget(objectName="Toolbar")
        head.setFixedHeight(64)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(24, 0, 24, 0)
        title = QLabel("Albums", objectName="PageTitle")
        hl.addWidget(title)
        hl.addStretch(1)
        self.count = QLabel(objectName="Count")
        hl.addWidget(self.count)
        outer.addWidget(head)

        scroll = QScrollArea(objectName="SettingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        page = QWidget(objectName="SettingsPage")
        v = QVBoxLayout(page)
        v.setContentsMargins(32, 20, 32, 32)
        v.setSpacing(10)

        v.addWidget(QLabel("Your albums", objectName="SectionTitle"))
        self.yours = TileFlow()
        v.addWidget(self.yours)
        v.addSpacing(16)
        row = QHBoxLayout()
        row.addWidget(QLabel("Events", objectName="SectionTitle"))
        row.addStretch(1)
        row.addWidget(QPushButton("Event suggestions…", clicked=lambda: self.open_suggestions.emit()))
        v.addLayout(row)
        self.events_help = QLabel(objectName="Help")
        self.events_help.setWordWrap(True)
        v.addWidget(self.events_help)
        self.events = TileFlow()
        v.addWidget(self.events)
        v.addSpacing(16)
        row = QHBoxLayout()
        row.addWidget(QLabel("Smart albums", objectName="SectionTitle"))
        row.addStretch(1)
        row.addWidget(QPushButton("New smart album…", clicked=self._new_smart))
        v.addLayout(row)
        self.smart_help = QLabel(objectName="Help")
        self.smart_help.setWordWrap(True)
        v.addWidget(self.smart_help)
        self.smart = TileFlow()
        v.addWidget(self.smart)
        v.addSpacing(16)
        v.addWidget(QLabel("Automatic", objectName="SectionTitle"))
        self.auto_help = QLabel("Kept up to date by Lunelis.", objectName="Help")
        v.addWidget(self.auto_help)
        self.auto = TileFlow()
        v.addWidget(self.auto)
        v.addStretch(1)
        scroll.setWidget(page)
        outer.addWidget(scroll, 1)

    # --- loading -------------------------------------------------------------------------

    def refresh(self) -> None:
        mine = albums.your_albums(self.conn)
        new = NewAlbumTile()
        new.clicked.connect(self._new_album)
        self.yours.set_widgets([new] + [self._tile(a) for a in mine])
        evs = albums.event_albums(self.conn)
        self.events.set_widgets([self._tile(a) for a in evs])
        self.events_help.setText("Trips and shoots - each photo is in at most one. Event suggestions finds them "
                                 "in your folder names and capture times." if evs else
                                 "No events yet. Event suggestions finds them in your folder names and capture "
                                 "times, or select photos in the library and use Photo > Event.")
        from lunelis.albums import smart
        smarts = smart.smart_albums(self.conn)
        self.smart.set_widgets([self._tile(a) for a in smarts])
        self.smart_help.setText("Saved rules that keep themselves up to date - e.g. ISO above 3200, 5 stars and "
                                "one lens." if not smarts else "Kept up to date by their rules.")
        self.count.setText(f"{len(mine):,} albums · {len(evs):,} events")
        if self._auto is not None:
            self.auto.set_widgets([self._tile(a) for a in self._auto])
        self._load_auto()

    def _load_auto(self) -> None:
        if self._thread is not None:
            return
        if self._auto is None:
            self.auto_help.setText("Counting…")
        self._thread = QThread(self)
        self._worker = _AutoWorker()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.done.connect(self._auto_loaded)      # bound method: GUI thread
        self._thread.start()

    def _auto_loaded(self, result) -> None:
        self._thread.quit()
        self._thread.wait()
        self._thread = None
        if isinstance(result, Exception):
            self.auto_help.setText(f"Couldn't count the automatic albums: {result}")
            return
        self._auto = result
        self.auto_help.setText("Kept up to date by Lunelis.")
        self.auto.set_widgets([self._tile(a) for a in result])

    def _tile(self, a: albums.Album) -> AlbumTile:
        t = AlbumTile(a, self.thumbs)
        t.clicked.connect(self.open_album.emit)
        t.menu.connect(self._menu)
        return t

    def _repaint_tiles(self) -> None:
        for flow in (self.yours, self.events, self.smart, self.auto):
            for w in flow.widgets:
                w.update()

    # --- actions ---------------------------------------------------------------------------

    def _new_album(self) -> None:
        name, ok = QInputDialog.getText(self, "New album", "Name:")
        if ok and name.strip():
            albums.create(self.conn, name)
            self.refresh()
            QMessageBox.information(self, "New album", f"\"{name.strip()}\" is ready. In the library, select "
                                    "photos and choose Photo > Album > Add to album (Ctrl+Shift+A).")

    def _new_smart(self) -> None:
        from lunelis.albums import smart
        from lunelis.ui.smart_dialog import SmartAlbumDialog
        dlg = SmartAlbumDialog(parent=self)
        if dlg.exec() and dlg.result_value:
            name, rules = dlg.result_value
            smart.create(self.conn, name, rules)
            self.refresh()

    def _edit_smart(self, a: albums.Album) -> None:
        from lunelis.albums import smart
        from lunelis.ui.smart_dialog import SmartAlbumDialog
        dlg = SmartAlbumDialog(a.name, smart.get(self.conn, int(a.key)), self)
        if dlg.exec() and dlg.result_value:
            name, rules = dlg.result_value
            smart.update(self.conn, int(a.key), rules, name)
            self.refresh()

    def _menu(self, a: albums.Album, pos) -> None:
        if a.kind == "auto":
            return
        m = QMenu(self)
        m.addAction("Open", lambda: self.open_album.emit(a))
        if a.kind == "smart":
            m.addAction("Edit the rules…", lambda: self._edit_smart(a))
        m.addAction("Rename…", lambda: self._rename(a))
        m.addSeparator()
        m.addAction("Remove album" if a.kind == "album" else "Remove event", lambda: self._remove(a))
        m.exec(pos)

    def _rename(self, a: albums.Album) -> None:
        name, ok = QInputDialog.getText(self, "Rename", "Name:", text=a.name)
        if not ok or not name.strip():
            return
        if a.kind in ("album", "smart"):
            albums.rename(self.conn, int(a.key), name)
        else:
            from lunelis.events import model as events
            events.rename(self.conn, int(a.key), name)
        self.refresh()

    def _remove(self, a: albums.Album) -> None:
        what = {"album": "album", "smart": "smart album"}.get(a.kind, "event")
        if QMessageBox.question(self, f"Remove {what}?",
                                f"Remove the {what} \"{a.name}\"?\n\nIts {a.count:,} photos stay exactly where and "
                                f"as they are - only the {what} goes.") != QMessageBox.StandardButton.Yes:
            return
        if a.kind in ("album", "smart"):
            albums.delete(self.conn, int(a.key))
        else:
            from lunelis.events import model as events
            events.delete(self.conn, int(a.key))
        self.refresh()
        self.changed.emit()
