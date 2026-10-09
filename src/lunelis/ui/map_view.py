"""
The Map page (sidebar > Photos > Map): photos placed by their GPS.

- Photos with a location become dots, grouped into numbered circles where
  they crowd; click a circle to see those photos in the library.
- Drag to move, the mouse wheel (or + / -) to zoom; "Fit" shows everything.
- **Online map tiles are opt-in.** Until you turn them on (Settings key
  `map_online`), the page draws the dots on a plain grid of latitude and
  longitude and makes no network request at all. Turned on, tiles come from
  OpenStreetMap (credited on the map), are cached in the data folder
  (`map_tiles`) and are never fetched again once cached.
- Nothing about your photos is sent anywhere: only tile numbers are asked for.
- **Pins for photos without GPS** (geo/places.py): Photo > Set location on
  the map... (or Place photos here on this page) puts the map in placing
  mode - click where they were taken. Photos dragged from the library and
  dropped on the map are placed where they land. Either way Lunelis asks
  first, showing the place it found, and the files themselves never change.
"""
from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from lunelis.ui import theme
from lunelis.ui.background import Background, unless_closed

TILE = 256
MAX_ZOOM = 18
TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
CLUSTER_PX = 44


def lonlat_to_world(lon: float, lat: float, z: float) -> tuple[float, float]:
    """Web Mercator pixel coordinates at zoom z."""
    lat = max(-85.05112878, min(85.05112878, lat))
    n = TILE * (2 ** z)
    x = (lon + 180.0) / 360.0 * n
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def world_to_lonlat(x: float, y: float, z: float) -> tuple[float, float]:
    n = TILE * (2 ** z)
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lon, lat


def clusters(points: list[tuple[int, float, float]], z: float, cell: int = CLUSTER_PX) -> list[tuple[float, float, list[int]]]:
    """(world x, world y, file ids): points binned by screen cell at zoom z."""
    bins: dict[tuple[int, int], list] = {}
    for fid, lat, lon in points:
        x, y = lonlat_to_world(lon, lat, z)
        bins.setdefault((int(x // cell), int(y // cell)), []).append((fid, x, y))
    out = []
    for members in bins.values():
        cx = sum(m[1] for m in members) / len(members)
        cy = sum(m[2] for m in members) / len(members)
        out.append((cx, cy, [m[0] for m in members]))
    return out


def located(conn) -> list[tuple[int, float, float]]:
    """Every photo with a location: its own GPS, or a pin dropped on this map."""
    from lunelis.geo import places
    return places.located(conn)


def _count_unlocated(conn) -> int:
    from lunelis.geo import places
    return len(places.without_location(conn))


class MapCanvas(QWidget):
    picked = Signal(list)                  # file ids under a clicked circle
    placed = Signal(float, float)          # placing mode: the spot clicked (lat, lon)
    tiles_failed = Signal(str)             # OpenStreetMap couldn't be reached (once per run)
    dropped = Signal(list, float, float)   # photos dragged from the library onto the map (ids, lat, lon)

    def __init__(self, tiles_dir: Path, parent=None) -> None:
        super().__init__(parent)
        self.tiles_dir = tiles_dir
        self.points: list[tuple[int, float, float]] = []
        self.z = 2.0
        self.cx, self.cy = lonlat_to_world(0, 20, self.z)       # the world point at the middle
        self.online = False
        self.net = None                    # made only when online tiles are turned on
        self._tiles: dict[tuple[int, int, int], QImage] = {}
        self._asked: set[tuple[int, int, int]] = set()
        self._drag: QPointF | None = None
        self._moved = False
        self._clusters: list[tuple[float, float, list[int]]] = []
        self.placing = False                  # the next click drops a pin
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setMinimumSize(200, 150)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # --- data and view ---------------------------------------------------------------------

    def set_points(self, points) -> None:
        self.points = list(points)
        self._recluster()
        self.update()

    def fit(self) -> None:
        if not self.points:
            return
        lats = [p[1] for p in self.points]
        lons = [p[2] for p in self.points]
        w, h = max(1, self.width() - 80), max(1, self.height() - 80)
        z = MAX_ZOOM
        while z > 1:
            x0, y0 = lonlat_to_world(min(lons), max(lats), z)
            x1, y1 = lonlat_to_world(max(lons), min(lats), z)
            if x1 - x0 <= w and y1 - y0 <= h:
                break
            z -= 1
        self.z = float(min(z, 15))
        x0, y0 = lonlat_to_world(min(lons), max(lats), self.z)
        x1, y1 = lonlat_to_world(max(lons), min(lats), self.z)
        self.cx, self.cy = (x0 + x1) / 2, (y0 + y1) / 2
        self._recluster()
        self.update()

    def centre_on(self, lat: float, lon: float, z: int = 15) -> None:
        """Show one spot up close (Info > the location of a photo)."""
        self.z = float(min(z, MAX_ZOOM))
        self.cx, self.cy = lonlat_to_world(lon, lat, self.z)
        self._recluster()
        self.update()

    def zoom(self, steps: int, at: QPointF | None = None) -> None:
        nz = max(1, min(MAX_ZOOM, int(self.z) + steps))
        if nz == self.z:
            return
        at = at or QPointF(self.width() / 2, self.height() / 2)
        wx = self.cx + at.x() - self.width() / 2
        wy = self.cy + at.y() - self.height() / 2
        k = 2 ** (nz - self.z)
        self.cx = wx * k - (at.x() - self.width() / 2)
        self.cy = wy * k - (at.y() - self.height() / 2)
        self.z = float(nz)
        self._recluster()
        self.update()

    def _recluster(self) -> None:
        self._clusters = clusters(self.points, self.z)

    def set_online(self, on: bool) -> None:
        self.online = on
        if on and self.net is None:
            from PySide6.QtNetwork import QNetworkAccessManager
            self.net = QNetworkAccessManager(self)
            self.net.finished.connect(self._tile_arrived)
        self.update()

    # --- tiles -----------------------------------------------------------------------------

    def _tile_path(self, z: int, x: int, y: int) -> Path:
        return self.tiles_dir / str(z) / str(x) / f"{y}.png"

    def tile(self, z: int, x: int, y: int) -> QImage | None:
        key = (z, x, y)
        if key in self._tiles:
            return self._tiles[key]
        p = self._tile_path(z, x, y)
        if p.exists():
            img = QImage(str(p))
            if not img.isNull():
                self._tiles[key] = img
                return img
        if self.online and self.net is not None and key not in self._asked:
            self._asked.add(key)
            from PySide6.QtNetwork import QNetworkRequest
            from lunelis import paths
            req = QNetworkRequest(QUrl(TILE_URL.format(z=z, x=x, y=y)))
            req.setRawHeader(b"User-Agent", f"Lunelis/{paths.version()} (+https://github.com/AxialForge/Lunelis)".encode())
            req.setAttribute(QNetworkRequest.Attribute.User, f"{z}/{x}/{y}")
            self.net.get(req)
        return None

    @unless_closed
    def _tile_arrived(self, reply) -> None:
        from PySide6.QtNetwork import QNetworkReply, QNetworkRequest
        key = reply.request().attribute(QNetworkRequest.Attribute.User)
        data = bytes(reply.readAll())
        ok = reply.error() == QNetworkReply.NetworkError.NoError
        error = reply.errorString()
        reply.deleteLater()
        if not ok:
            if not getattr(self, "_told", False):
                self._told = True
                self.tiles_failed.emit(error)
            return
        if not key:
            return
        z, x, y = (int(v) for v in str(key).split("/"))
        img = QImage.fromData(data)
        if img.isNull():
            return
        p = self._tile_path(z, x, y)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        self._tiles[(z, x, y)] = img
        self.update()

    # --- painting --------------------------------------------------------------------------

    def _origin(self) -> tuple[float, float]:
        return self.cx - self.width() / 2, self.cy - self.height() / 2

    def paintEvent(self, e) -> None:
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(t.canvas))
        if not self.points and not self.placing:
            # An empty map says why (0.48), instead of a blank grid.
            p.setPen(QColor(t.text_muted))
            p.drawText(self.rect().adjusted(40, 40, -40, -40),
                       Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                       "No photos with a location yet.\n\nPhotos from phones and GPS cameras appear here by "
                       "themselves. To place others, select them in the library and use Photo > Set location on "
                       "the map, or drag them onto Map in the sidebar.")
            p.end()
            return
        ox, oy = self._origin()
        z = int(self.z)
        n = 2 ** z
        drew_tiles = False
        if self.online or self.tiles_dir.exists():
            for tx in range(int(ox // TILE), int((ox + self.width()) // TILE) + 1):
                for ty in range(max(0, int(oy // TILE)), min(n, int((oy + self.height()) // TILE) + 1)):
                    img = self.tile(z, tx % n, ty) if (self.online or self._tile_path(z, tx % n, ty).exists()) else None
                    if img is not None:
                        p.drawImage(QRectF(tx * TILE - ox, ty * TILE - oy, TILE, TILE), img)
                        drew_tiles = True
        if not drew_tiles:
            self._graticule(p, ox, oy)
        accent = QColor(t.accent)
        font = QFont(self.font())
        font.setBold(True)
        p.setFont(font)
        for x, y, ids in self._clusters:
            sx, sy = x - ox, y - oy
            if not (-40 < sx < self.width() + 40 and -40 < sy < self.height() + 40):
                continue
            r = 7 if len(ids) == 1 else min(26, 11 + 3 * math.log2(len(ids)))
            p.setPen(QPen(QColor("white"), 2))
            p.setBrush(accent)
            p.drawEllipse(QPointF(sx, sy), r, r)
            if len(ids) > 1:
                p.setPen(QColor("white"))
                p.drawText(QRectF(sx - r, sy - r, 2 * r, 2 * r), Qt.AlignmentFlag.AlignCenter,
                           f"{len(ids)}" if len(ids) < 1000 else f"{len(ids) // 1000}k")
        if drew_tiles:
            p.setPen(QColor(t.text_muted))
            p.drawText(self.rect().adjusted(0, 0, -8, -6), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
                       "© OpenStreetMap contributors")
        p.end()

    def _graticule(self, p: QPainter, ox: float, oy: float) -> None:
        t = theme.current()
        p.setPen(QPen(QColor(t.border), 1))
        step = 30 if self.z < 4 else 10 if self.z < 7 else 1 if self.z < 11 else 0.1
        lon0, lat1 = world_to_lonlat(ox, oy, self.z)
        lon1, lat0 = world_to_lonlat(ox + self.width(), oy + self.height(), self.z)
        lon = math.floor(lon0 / step) * step
        while lon <= lon1:
            x, _ = lonlat_to_world(lon, 0, self.z)
            p.drawLine(QPointF(x - ox, 0), QPointF(x - ox, self.height()))
            lon += step
        lat = math.floor(lat0 / step) * step
        while lat <= lat1:
            _, y = lonlat_to_world(0, lat, self.z)
            p.drawLine(QPointF(0, y - oy), QPointF(self.width(), y - oy))
            lat += step

    # --- mouse -----------------------------------------------------------------------------

    def lat_lon_at(self, pos: QPointF) -> tuple[float, float]:
        ox, oy = self._origin()
        lon, lat = world_to_lonlat(ox + pos.x(), oy + pos.y(), self.z)
        lon = (lon + 180.0) % 360.0 - 180.0
        return max(-85.0, min(85.0, lat)), lon

    def cluster_at(self, pos: QPointF) -> list[int] | None:
        ox, oy = self._origin()
        best, dist = None, 1e9
        for x, y, ids in self._clusters:
            d = math.hypot(x - ox - pos.x(), y - oy - pos.y())
            r = 7 if len(ids) == 1 else min(26, 11 + 3 * math.log2(len(ids)))
            if d <= r + 3 and d < dist:
                best, dist = ids, d
        return best

    def mousePressEvent(self, e) -> None:
        self._drag = e.position()
        self._moved = False

    def mouseMoveEvent(self, e) -> None:
        if self._drag is not None and e.buttons() & Qt.MouseButton.LeftButton:
            d = e.position() - self._drag
            if abs(d.x()) + abs(d.y()) > 3:
                self._moved = True
            self.cx -= d.x()
            self.cy -= d.y()
            self._drag = e.position()
            self.update()
            return
        if self.placing:
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.setToolTip("Click where the photos were taken")
            return
        ids = self.cluster_at(e.position())
        self.setToolTip(f"{len(ids)} photo{'s' if len(ids) != 1 else ''} - click to see them" if ids else "")
        self.setCursor(Qt.CursorShape.PointingHandCursor if ids else Qt.CursorShape.OpenHandCursor)

    def mouseReleaseEvent(self, e) -> None:
        if not self._moved and self.placing and e.button() == Qt.MouseButton.LeftButton:
            self._drag = None
            self.placed.emit(*self.lat_lon_at(e.position()))
            return
        if not self._moved:
            ids = self.cluster_at(e.position())
            if ids:
                self.picked.emit(list(ids))
        self._drag = None

    def wheelEvent(self, e) -> None:
        self.zoom(1 if e.angleDelta().y() > 0 else -1, e.position())

    def dragEnterEvent(self, e) -> None:
        from lunelis.ui.grid import PhotoGrid
        if e.mimeData().hasFormat(PhotoGrid.DRAG_MIME):
            e.acceptProposedAction()

    def dragMoveEvent(self, e) -> None:
        self.dragEnterEvent(e)

    def dropEvent(self, e) -> None:
        import json
        from lunelis.ui.grid import PhotoGrid
        if not e.mimeData().hasFormat(PhotoGrid.DRAG_MIME):
            return
        ids = [int(x) for x in json.loads(bytes(e.mimeData().data(PhotoGrid.DRAG_MIME)).decode("ascii"))]
        e.acceptProposedAction()
        if ids:
            self.dropped.emit(ids, *self.lat_lon_at(e.position()))

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom(1)
        elif e.key() == Qt.Key.Key_Minus:
            self.zoom(-1)
        else:
            super().keyPressEvent(e)


class MapView(QWidget):
    show_ids = Signal(list)
    show_unlocated = Signal(list)          # "Without a location": those photos in the library
    places_changed = Signal()              # pins dropped (place tags changed)

    def __init__(self, conn, parent=None) -> None:
        super().__init__(parent)
        from lunelis import paths
        self.conn = conn
        self.bg = Background(self, conn)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = QWidget(objectName="Toolbar")
        head.setFixedHeight(64)
        h = QHBoxLayout(head)
        h.setContentsMargins(24, 0, 24, 0)
        h.addWidget(QLabel("Map", objectName="PageTitle"))
        h.addSpacing(12)
        self.count = QLabel(objectName="Count")
        h.addWidget(self.count)
        h.addStretch(1)
        self.online_b = QPushButton("Show map tiles (online)")
        self.online_b.setToolTip("Map pictures come from OpenStreetMap over the internet. Only tile numbers are "
                                 "asked for - nothing about your photos is sent.")
        self.online_b.clicked.connect(self._toggle_online)
        h.addWidget(self.online_b)
        h.addWidget(QPushButton("Fit", clicked=lambda: self.canvas.fit()))
        self.unlocated_b = QPushButton("Without a location", clicked=self._show_unlocated)
        self.unlocated_b.setToolTip("The photos with no GPS and no pin, in the library - select some, then "
                                    "Photo > Set location on the map, or drag them onto the Map in the sidebar")
        h.addWidget(self.unlocated_b)
        outer.addWidget(head)
        # Placing mode: "Click where these 12 photos were taken".
        self.place_bar = QWidget(objectName="Toolbar")
        pb = QHBoxLayout(self.place_bar)
        pb.setContentsMargins(24, 6, 24, 6)
        self.place_label = QLabel(objectName="SectionTitle")
        pb.addWidget(self.place_label, 1)
        pb.addWidget(QPushButton("Cancel", clicked=self.stop_placing))
        self.place_bar.hide()
        outer.addWidget(self.place_bar)
        self._placing: list[int] = []
        note_row = QWidget()
        nr = QHBoxLayout(note_row)
        nr.setContentsMargins(24, 6, 24, 6)
        self.note = QLabel(objectName="Help")
        self.note.setWordWrap(True)
        nr.addWidget(self.note, 1)
        # One switch for map pictures: the header's (0.48 - there were two).
        self.note_row = note_row
        outer.addWidget(note_row)
        self.canvas = MapCanvas(paths.DATA_DIR / "map_tiles")
        self.canvas.picked.connect(self.show_ids.emit)
        self.canvas.tiles_failed.connect(self._tiles_failed)
        self.canvas.placed.connect(lambda lat, lon: self._place(self._placing, lat, lon))
        self.canvas.dropped.connect(self._place)
        outer.addWidget(self.canvas, 1)
        self._fitted = False

    def refresh(self) -> None:
        from lunelis.settings import Settings
        on = Settings(self.conn).get("map_online")
        self.canvas.set_online(on)
        self._update_online(on)
        self.bg.run("points", located, self._points)

    def _points(self, pts) -> None:
        self.canvas.set_points(pts)
        held = getattr(self, "_notice", None)
        self.count.setText((held.prefix() if held else "") + f"{len(pts):,} photo{'s' if len(pts) != 1 else ''} with a location")
        self.bg.run("unlocated", _count_unlocated, lambda n: self.unlocated_b.setText(f"Without a location ({n:,})"))
        if not self._fitted and pts:
            self._fitted = True
            self.canvas.fit()

    # --- pins ---------------------------------------------------------------------------------

    def start_placing(self, ids: list[int]) -> None:
        """Placing mode: the next click on the map pins these photos."""
        self._placing = list(ids)
        if not self._placing:
            return
        n = len(self._placing)
        self.place_label.setText(f"Click on the map where {'this photo was' if n == 1 else f'these {n:,} photos were'}"
                                 " taken. Zoom and drag as usual; Cancel stops.")
        self.place_bar.show()
        self.canvas.placing = True
        self.canvas.setFocus()

    def stop_placing(self) -> None:
        self._placing = []
        self.place_bar.hide()
        self.canvas.placing = False
        self.canvas.setCursor(Qt.CursorShape.OpenHandCursor)

    def _place(self, ids: list[int], lat: float, lon: float) -> None:
        from PySide6.QtWidgets import QMessageBox
        from lunelis.geo import places
        if not ids:
            return
        place = places.lookup(lat, lon)
        n = len(ids)
        already = len(places.pinned(self.conn, ids)) + sum(
            1 for fid in ids if (loc := places.location_of(self.conn, fid)) and loc[2] == "gps")
        text = (f"Put {'this photo' if n == 1 else f'these {n:,} photos'} at {place.label}?\n\n"
                f"({lat:.4f}, {lon:.4f}) - they'll be tagged {place.tag.replace('|', ' > ')}. The files "
                "themselves don't change.")
        if already:
            text += f"\n\n{already:,} of them already have a location; this pin replaces it."
        if QMessageBox.question(self, "Set location", text) != QMessageBox.StandardButton.Yes:
            return
        places.set_location(self.conn, ids, lat, lon)
        self.stop_placing()
        self.places_changed.emit()
        self.refresh()
        from lunelis.ui.notice import Notice
        self._notice = getattr(self, "_notice", None) or Notice()
        self.count.setText(self._notice.say(f"Placed {n:,} photo{'s' if n != 1 else ''} at {place.label}"))

    def _show_unlocated(self) -> None:
        from lunelis.geo import places
        self.show_unlocated.emit(places.without_location(self.conn))

    def _update_online(self, on: bool) -> None:
        self.online_b.setText("Hide map tiles" if on else "Show map tiles (online)")
        self.note.setText("" if on else "Dots on a plain grid of latitude and longitude. Map pictures come from "
                                        "OpenStreetMap over the internet - nothing is fetched until you turn them "
                                        "on (here, or Settings > Library > Places).")
        self.note_row.setVisible(not on)

    def _tiles_failed(self, error: str) -> None:
        self.note.setText(f"Couldn't reach OpenStreetMap for the map pictures ({error}). The dots still work; "
                          "pictures appear once the connection is back.")
        self.note_row.setVisible(True)

    def _toggle_online(self) -> None:
        from lunelis.settings import Settings
        on = not Settings(self.conn).get("map_online")
        Settings(self.conn).set("map_online", on)
        self.canvas.set_online(on)
        self._update_online(on)
