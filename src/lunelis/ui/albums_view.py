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
from PySide6.QtGui import QPen, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QMenu, QMessageBox, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from lunelis import paths
from lunelis.albums import model as albums
from lunelis.catalog.schema import open_catalog
from lunelis.raw.thumbnails import cache_rel_path
from lunelis.ui import theme as themes
from lunelis.ui.background import window_closed
from lunelis.ui.theme import qcolor
from lunelis.ui.thumbcache import ThumbCache

TILE = 196


class _AutoWorker(QObject):
    done = Signal(object)            # (automatic albums, smart albums) | Exception

    def __init__(self, db: str | None = None) -> None:
        super().__init__()
        self.db = db

    def run(self) -> None:
        conn = open_catalog(self.db or paths.DEFAULT_CATALOG_PATH)
        try:
            # The smart albums are counted here too (0.54: each one's rules ran
            # on the window's thread at every visit).
            from lunelis.albums import smart
            self.done.emit((albums.auto_albums(conn), smart.smart_albums(conn)))
        except Exception as e:       # shown on the page, never fatal
            self.done.emit(e)
        finally:
            conn.close()


class AlbumTile(QWidget):
    """A square cover + name + count. Clicks open; right-click for actions."""

    clicked = Signal(object)         # Album
    menu = Signal(object, object)    # Album, global pos
    dropped = Signal(object, list)   # Album, file ids dragged onto it

    def __init__(self, album: albums.Album, thumbs: ThumbCache, parent=None) -> None:
        super().__init__(parent)
        self.album, self.thumbs = album, thumbs
        self.setFixedSize(TILE, TILE + 46)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)       # Tab reaches every tile; Enter opens
        self.setAcceptDrops(album.kind == "album")            # photos dragged from the library
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
        if self.hasFocus():
            p.setPen(QPen(qcolor(t.accent), 3))
            p.drawRoundedRect(QRectF(r).adjusted(1.5, 1.5, -1.5, -1.5), 10, 10)
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

    def dragEnterEvent(self, e) -> None:
        from lunelis.ui.grid import PhotoGrid
        if e.mimeData().hasFormat(PhotoGrid.DRAG_MIME):
            e.acceptProposedAction()
            self._hover = True
            self.update()

    def dragLeaveEvent(self, e) -> None:
        self._hover = False
        self.update()

    def dropEvent(self, e) -> None:
        import json
        from lunelis.ui.grid import PhotoGrid
        self._hover = False
        self.update()
        try:
            ids = [int(x) for x in json.loads(bytes(e.mimeData().data(PhotoGrid.DRAG_MIME)).decode("ascii"))]
        except (ValueError, TypeError):
            return
        e.acceptProposedAction()
        self.dropped.emit(self.album, ids)

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit(self.album)
        elif e.key() == Qt.Key.Key_Menu or (e.key() == Qt.Key.Key_F10
                                            and e.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.menu.emit(self.album, self.mapToGlobal(self.rect().center()))
        else:
            super().keyPressEvent(e)

    def focusInEvent(self, e) -> None:
        super().focusInEvent(e)
        self.update()
        parent = self.parentWidget()
        while parent is not None and not hasattr(parent, "ensureWidgetVisible"):
            parent = parent.parentWidget()
        if parent is not None:
            parent.ensureWidgetVisible(self)        # Tab scrolls the page to the tile

    def focusOutEvent(self, e) -> None:
        super().focusOutEvent(e)
        self.update()


class NewAlbumTile(QWidget):
    # A dashed square the size of a cover: "+ New album".
    clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(TILE, TILE + 46)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)       # Tab reaches it; Enter / Space make an album (0.54)
        self.setAccessibleName("New album")
        self._hover = False
        self._font = QFont(self.font())
        self._font.setPixelSize(14)

    def paintEvent(self, e) -> None:
        from PySide6.QtGui import QPen
        t = themes.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        lit = self._hover or self.hasFocus()
        pen = QPen(qcolor(t.accent if lit else t.border), 3 if self.hasFocus() else 2, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawRoundedRect(QRectF(1.5, 1.5, TILE - 3, TILE - 3), 10, 10)
        p.setPen(qcolor(t.text if lit else t.text_muted))
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

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
        else:
            super().keyPressEvent(e)

    def focusInEvent(self, e) -> None:
        super().focusInEvent(e)
        self.update()

    def focusOutEvent(self, e) -> None:
        super().focusOutEvent(e)
        self.update()


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
        # The spare width goes to the column after the last one - and to no other:
        # the column that had it at the old width now holds tiles, and a stretch
        # left on it opened an uneven gap there (0.54).
        for c in range(max(self.grid.columnCount(), cols) + 1):
            self.grid.setColumnStretch(c, 0)
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
    add_to_album = Signal(object, list)   # Album, file ids dropped on it
    open_suggestions = Signal()      # the event suggestions page
    share_album = Signal(int, str)   # album id, name: the family gallery
    changed = Signal()

    def __init__(self, conn, parent=None) -> None:
        super().__init__(parent)
        self.conn = conn
        self.thumbs = ThumbCache(paths.THUMBNAIL_CACHE, self)
        self.thumbs.set_tile_size(256)
        self.thumbs.ready.connect(lambda _: self._repaint_tiles())
        self._thread: QThread | None = None
        self._auto: list[albums.Album] | None = None
        self._smart: list[albums.Album] | None = None    # counted on the worker with the automatic ones
        self._again = False                              # asked to count again while a count was running

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
        self.auto_help = QLabel("Kept up to date by Lunelis.", objectName="Help", wordWrap=True)
        v.addWidget(self.auto_help)
        # While the automatic albums are built from the library: a moving bar and what's
        # happening, not a silent "Counting…" (0.46).
        from PySide6.QtWidgets import QProgressBar
        self.auto_busy = QProgressBar()
        self.auto_busy.setRange(0, 0)                  # indeterminate: it moves
        self.auto_busy.setTextVisible(False)
        self.auto_busy.setFixedHeight(6)
        self.auto_busy.hide()
        v.addWidget(self.auto_busy)
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
        self.count.setText(f"{len(mine):,} albums · {len(evs):,} events")
        # Smart and automatic albums: the last count is shown at once, the new
        # one comes from a worker (0.54: the smart albums were counted here).
        if self._smart is not None:
            self._show_smart(self._smart)
        else:
            self.smart_help.setText("Counting your smart albums…")
        if self._auto is not None:
            self.auto.set_widgets([self._tile(a) for a in self._auto])
        self._load_auto()

    def _show_smart(self, smarts: list) -> None:
        self.smart.set_widgets([self._tile(a) for a in smarts])
        self.smart_help.setText("Saved rules that keep themselves up to date - e.g. ISO above 3200, 5 stars and "
                                "one lens." if not smarts else "Kept up to date by their rules.")

    def _load_auto(self) -> None:
        if self._thread is not None:
            self._again = True                         # e.g. a smart album made meanwhile: count once more after
            return
        from lunelis.ui.background import db_file
        try:
            db = db_file(self.conn)
        except Exception:                              # the catalog was closed under the page
            return
        if not db:
            # An in-memory catalog can't be opened twice: counted here.
            from lunelis.albums import smart
            try:
                self._counted((albums.auto_albums(self.conn), smart.smart_albums(self.conn)))
            except Exception as e:
                self._counted(e)
            return
        if self._auto is None:
            self.auto_help.setText("Building the automatic albums from your library - favourites, videos, "
                                   "each camera, recent, no date… This takes a few seconds on a big library.")
            self.auto_busy.show()
        self._thread = QThread(self)
        self._worker = _AutoWorker(db)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.done.connect(self._auto_loaded)      # bound method: GUI thread
        self._thread.start()

    def _auto_loaded(self, result) -> None:
        self._thread.quit()
        self._thread.wait()
        self._thread = None
        if window_closed(self):                        # the catalog closed meanwhile: nothing to show
            return
        if self._again:
            self._again = False
            self._load_auto()                          # the newer count is the one to show
            if self._thread is not None:
                return
        self._counted(result)

    def _counted(self, result) -> None:
        self.auto_busy.hide()
        if isinstance(result, Exception):
            self.auto_help.setText(f"Couldn't count the automatic albums: {result}")
            if self._smart is None:
                self.smart_help.setText(f"Couldn't count the smart albums: {result}")
            return
        self._auto, self._smart = result
        self.auto_help.setText("Kept up to date by Lunelis.")
        self.auto.set_widgets([self._tile(a) for a in self._auto])
        self._show_smart(self._smart)

    def _tile(self, a: albums.Album) -> AlbumTile:
        t = AlbumTile(a, self.thumbs)
        t.clicked.connect(self.open_album.emit)
        t.menu.connect(self._menu)
        t.dropped.connect(self.add_to_album.emit)
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
        if a.kind == "album":
            m.addAction("Share on the home network…", lambda: self.share_album.emit(int(a.key), a.name))
        m.addSeparator()
        m.addAction({"album": "Remove album", "smart": "Remove smart album"}.get(a.kind, "Remove event"),
                    lambda: self._remove(a))
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
