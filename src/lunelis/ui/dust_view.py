"""
The Sensor dust page (sidebar > Keep safe): a dust map per camera, and
healing it out of the photos after you've seen the map.

- Pick a camera; **Look for dust** reads its f/8-and-narrower frames
  (dust.py) on a worker. The map is the sensor as a rectangle: a circle per
  spot, solid while it's there, dashed once it was cleaned off; the text
  says when cleanings happened and whether there's new dust.
- **Heal on N photos** is the preview step: the count and the photos are
  shown first (Show the photos); healing adds Heal spots to their edits -
  never to the files. **Undo the last heal** takes those spots off again.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from lunelis import dust
from lunelis.ui import theme
from lunelis.ui.background import Background


class SensorMap(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.map: dust.DustMap | None = None
        self.setMinimumSize(360, 240)

    def set_map(self, m) -> None:
        self.map = m
        self.update()

    def paintEvent(self, e) -> None:
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width() - 20, self.height() - 20
        sw, sh = (w, w * 2 / 3) if w * 2 / 3 <= h else (h * 3 / 2, h)
        r = QRectF((self.width() - sw) / 2, (self.height() - sh) / 2, sw, sh)
        p.setPen(QPen(QColor(t.border), 2))
        p.setBrush(QColor(t.surface))
        p.drawRoundedRect(r, 6, 6)
        if self.map is None:
            p.setPen(QColor(t.text_muted))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, "The sensor, as the camera sees it")
            p.end()
            return
        for s in self.map.spots:
            c = QPointF(r.x() + s.x * sw, r.y() + s.y * sh)
            rad = max(5.0, s.r * sw)
            pen = QPen(QColor(t.accent), 2, Qt.PenStyle.DashLine if s.state == "cleaned" else Qt.PenStyle.SolidLine)
            p.setPen(pen)
            col = QColor(t.accent)
            col.setAlphaF(0.15 + 0.5 * s.confidence if s.state != "cleaned" else 0.0)
            p.setBrush(col)
            p.drawEllipse(c, rad, rad)
        p.end()


class DustView(QWidget):
    show_ids = Signal(list, str)
    healed = Signal()

    def __init__(self, conn, parent=None) -> None:
        super().__init__(parent)
        self.conn = conn
        self.bg = Background(self, conn)
        self.map: dust.DustMap | None = None
        self._plan: dict = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = QWidget(objectName="Toolbar")
        head.setFixedHeight(64)
        h = QHBoxLayout(head)
        h.setContentsMargins(24, 0, 24, 0)
        h.addWidget(QLabel("Sensor dust", objectName="PageTitle"))
        h.addSpacing(12)
        self.camera = QComboBox()
        self.camera.currentIndexChanged.connect(lambda _: self._show_saved())
        h.addWidget(self.camera)
        self.look_b = QPushButton("Look for dust", objectName="Primary", clicked=self.look)
        h.addWidget(self.look_b)
        h.addStretch(1)
        outer.addWidget(head)
        body = QVBoxLayout()
        body.setContentsMargins(24, 16, 24, 24)
        body.setSpacing(10)
        self.text = QLabel(objectName="Help", wordWrap=True)
        body.addWidget(self.text)
        self.sensor = SensorMap()
        body.addWidget(self.sensor, 1)
        row = QHBoxLayout()
        self.show_b = QPushButton("Show the photos", clicked=self._show_photos)
        self.heal_b = QPushButton("Heal", objectName="Primary", clicked=self.heal)
        self.undo_b = QPushButton("Undo the last heal", clicked=self.undo)
        for b in (self.show_b, self.heal_b, self.undo_b):
            row.addWidget(b)
        row.addStretch(1)
        body.addLayout(row)
        w = QWidget()
        w.setLayout(body)
        outer.addWidget(w, 1)
        self._update_buttons()

    def refresh(self) -> None:
        current = self.camera.currentData()
        self.camera.blockSignals(True)
        self.camera.clear()
        for model, n in dust.cameras(self.conn):
            self.camera.addItem(f"{model} ({n:,} at f/8+)", model)
        self.camera.setCurrentIndex(max(0, self.camera.findData(current)))
        self.camera.blockSignals(False)
        self.look_b.setEnabled(self.camera.count() > 0)
        if not self.camera.count():
            self.text.setText("No photos at f/8 or narrower with thumbnails yet - dust shows best on those.")
        self._show_saved()

    def _show_saved(self) -> None:
        cam = self.camera.currentData()
        self.set_map(dust.load(self.conn, cam) if cam else None)

    def set_map(self, m) -> None:
        self.map = m
        self.sensor.set_map(m)
        self._plan = dust.affected(self.conn, m, [s for s in m.spots if s.state != "cleaned"] or m.spots) \
            if m and m.spots else {}
        if m is not None:
            self.text.setText(dust.describe(m) + (f" Healing would add spots to {len(self._plan):,} photos "
                                                  "(as edits - the files never change)." if self._plan else ""))
        self.heal_b.setText(f"Heal on {len(self._plan):,} photos" if self._plan else "Heal")
        self._update_buttons()

    def _update_buttons(self) -> None:
        cam = self.camera.currentData()
        self.heal_b.setEnabled(bool(self._plan))
        self.show_b.setEnabled(bool(self._plan))
        self.undo_b.setEnabled(bool(cam) and self.conn.execute(
            "SELECT COUNT(*) FROM dust_heals WHERE camera = ?", (cam,)).fetchone()[0] > 0)

    def look(self) -> None:
        cam = self.camera.currentData()
        if not cam:
            return
        from lunelis import paths
        thumbs = paths.THUMBNAIL_CACHE
        self.look_b.setEnabled(False)
        self.text.setText(f"Looking at the {cam}'s frames…")

        def work(conn):
            m = dust.build(conn, cam, thumbs)
            dust.save(conn, m)
            return m

        def done(m):
            self.look_b.setEnabled(True)
            self.set_map(m)
        self.bg.run("dust", work, done, error=lambda e: (self.look_b.setEnabled(True),
                                                        self.text.setText(f"Couldn't look: {e}")))

    def _show_photos(self) -> None:
        if self._plan:
            self.show_ids.emit(sorted(self._plan), "Sensor dust")

    def heal(self) -> None:
        if not self.map or not self._plan:
            return
        from PySide6.QtWidgets import QMessageBox
        n = len(self._plan)
        if QMessageBox.question(self, "Heal the dust",
                                f"Add heal spots to {n:,} photo{'s' if n != 1 else ''}? They're edits - the files "
                                "don't change, and Undo the last heal takes them off again.") \
                != QMessageBox.StandardButton.Yes:
            return
        live = [s for s in self.map.spots if s.state != "cleaned"] or self.map.spots
        m = self.map
        self.heal_b.setEnabled(False)
        self.text.setText(f"Healing {n:,} photos…")

        def done(result):
            healed, skipped = result
            self.text.setText(f"Healed {healed:,} photo{'s' if healed != 1 else ''}"
                              + (f"; {skipped} turned or flipped in Lunelis were left alone" if skipped else "")
                              + ". Undo the last heal takes the spots off again.")
            self.healed.emit()
            self._update_buttons()
        self.bg.run("heal", lambda conn: dust.heal(conn, m, live), done,
                    error=lambda e: (self.text.setText(f"Couldn't heal: {e}"), self._update_buttons()))

    def undo(self) -> None:
        cam = self.camera.currentData()
        if cam:
            n = dust.undo(self.conn, cam)
            self.text.setText(f"Took the dust spots off {n:,} photo{'s' if n != 1 else ''}.")
            self.healed.emit()
            self._update_buttons()
