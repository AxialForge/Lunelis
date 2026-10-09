"""
The On this day page (sidebar > Photos > On this day): what you shot on this
date in other years.

- Opens on today; ‹ / › step a day, or pick any date. "Within 3 days" widens
  it to the week around the date.
- One row per year, newest first: how many photos, a strip of them, and
  "Show all" to see that year's photos in the library.
- The strip's photos can be selected (click, Ctrl / Shift-click); double-click
  opens one; right-click > Show in Library goes to its folder with it selected.
- Dates are the capture dates the camera recorded (local time), so a photo
  taken at 23:30 on New Year's Eve stays on the 31st.
"""
from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QDateEdit, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from lunelis.ui.background import Background

STRIP = 10
THUMB_H = 96


def day_keys(day: date, spread: int) -> list[str]:
    """'MM-DD' for the day and `spread` days either side. In a year without a
    Feb 29, leap-day photos show on Feb 28 (0.49: they never showed at all)."""
    import calendar
    keys = {(day + timedelta(days=d)).strftime("%m-%d") for d in range(-spread, spread + 1)}
    if "02-28" in keys and not calendar.isleap(day.year):
        keys.add("02-29")
    return sorted(keys)


def on_this_day(conn, day: date, spread: int = 0) -> list[tuple[int, list[int]]]:
    """[(year, file ids in time order)], newest year first."""
    import json
    keys = day_keys(day, spread)
    rows = conn.execute(
        "SELECT CAST(substr(e.captured_at, 1, 4) AS INTEGER), f.id FROM files f JOIN exif e ON e.file_id = f.id"
        " JOIN roots r ON r.id = f.root_id WHERE r.enabled = 1 AND f.missing_since IS NULL AND f.excluded = 0"
        " AND f.quarantined_at IS NULL AND f.archived_at IS NULL AND e.captured_at IS NOT NULL"
        " AND substr(e.captured_at, 6, 5) IN (SELECT value FROM json_each(?))"
        " AND NOT (f.pair_of IS NOT NULL AND f.is_raw = 0)"
        " ORDER BY e.captured_at", (json.dumps(keys),)).fetchall()
    years: dict[int, list[int]] = {}
    for y, fid in rows:
        years.setdefault(y, []).append(fid)
    return sorted(years.items(), reverse=True)


def _load(conn, day: date, spread: int):
    """Rows plus the strip's thumbnails (QImage is fine off the GUI thread)."""
    from lunelis import paths
    out = []
    for year, ids in on_this_day(conn, day, spread):
        step = max(1, len(ids) // STRIP)
        thumbs = []
        for fid in ids[::step][:STRIP]:
            row = conn.execute("SELECT thumbnail_path FROM files WHERE id = ?", (fid,)).fetchone()
            from lunelis.ui.thumbcache import load_image
            thumbs.append((fid, load_image(paths.THUMBNAIL_CACHE / row[0], THUMB_H) if row and row[0] else QImage()))
        out.append((year, ids, thumbs))
    return out


class _Tile(QLabel):
    """One photo in a year's strip: click selects, double-click opens."""

    def __init__(self, view, fid: int) -> None:
        super().__init__()
        self.view, self.fid = view, fid
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda _: self.view._menu(self.fid))
        self.mark(False)

    def mark(self, on: bool) -> None:
        from lunelis.ui import theme
        self.setStyleSheet(f"border: 3px solid {theme.current().accent if on else 'transparent'};")

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.view._click(self.fid, e.modifiers())

    def mouseDoubleClickEvent(self, e) -> None:
        self.view.open_photo.emit(self.fid)


class CalendarView(QWidget):
    show_ids = Signal(list, str)          # file ids, a name for the filter chip
    open_photo = Signal(int)
    show_in_library = Signal(int)         # right-click > Show in Library: the photo in its folder

    def __init__(self, conn, parent=None) -> None:
        super().__init__(parent)
        self.conn = conn
        self.bg = Background(self, conn)
        self.day = date.today()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = QWidget(objectName="Toolbar")
        head.setFixedHeight(64)
        h = QHBoxLayout(head)
        h.setContentsMargins(24, 0, 24, 0)
        h.addWidget(QLabel("On this day", objectName="PageTitle"))
        h.addSpacing(16)
        self.prev_b = QPushButton("‹", clicked=lambda: self.set_day(self.day - timedelta(days=1)))
        self.prev_b.setToolTip("The day before")
        self.picker = QDateEdit(calendarPopup=True)
        self.picker.setDisplayFormat("MMMM d")
        self.picker.dateChanged.connect(lambda d: self.set_day(d.toPython()))
        self.next_b = QPushButton("›", clicked=lambda: self.set_day(self.day + timedelta(days=1)))
        self.next_b.setToolTip("The day after")
        self.today_b = QPushButton("Today", clicked=lambda: self.set_day(date.today()))
        for w in (self.prev_b, self.picker, self.next_b, self.today_b):
            h.addWidget(w)
        self.week_cb = QCheckBox("Within 3 days")
        self.week_cb.setToolTip("The week around the date, not just the day")
        self.week_cb.toggled.connect(lambda _: self.refresh())
        h.addSpacing(12)
        h.addWidget(self.week_cb)
        h.addStretch(1)
        self.summary = QLabel(objectName="Count")
        h.addWidget(self.summary)
        outer.addWidget(head)
        scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.Shape.NoFrame)
        body = QWidget()
        self.rows = QVBoxLayout(body)
        self.rows.setContentsMargins(24, 16, 24, 24)
        self.rows.setSpacing(18)
        self.rows.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        self.year_rows: list[tuple[int, list[int], QPushButton]] = []
        self.tiles: list[_Tile] = []
        self.selected: set[int] = set()
        self._anchor: int | None = None
        self._sync_picker()

    def _click(self, fid: int, mods) -> None:
        order = [t.fid for t in self.tiles]
        if mods & Qt.KeyboardModifier.ShiftModifier and self._anchor in order:
            a, b = sorted((order.index(self._anchor), order.index(fid)))
            self.selected |= set(order[a:b + 1])
        elif mods & Qt.KeyboardModifier.ControlModifier:
            self.selected ^= {fid}
            self._anchor = fid
        else:
            self.selected = {fid}
            self._anchor = fid
        for t in self.tiles:
            t.mark(t.fid in self.selected)

    def _menu(self, fid: int) -> None:
        if fid not in self.selected:
            self._click(fid, Qt.KeyboardModifier.NoModifier)
        from PySide6.QtGui import QCursor
        m = QMenu(self)
        m.addAction("Open", lambda: self.open_photo.emit(fid))
        m.addAction("Show in Library", lambda: self.show_in_library.emit(fid))
        if len(self.selected) > 1:
            ids = [t.fid for t in self.tiles if t.fid in self.selected]
            m.addAction(f"Show the {len(ids)} selected in the library",
                        lambda: self.show_ids.emit(ids, "Selected on this day"))
        m.exec(QCursor.pos())

    def _sync_picker(self) -> None:
        self.picker.blockSignals(True)
        self.picker.setDate(QDate(self.day.year, self.day.month, self.day.day))
        self.picker.blockSignals(False)

    def set_day(self, day: date) -> None:
        self.day = day
        self._sync_picker()
        self.refresh()

    def refresh(self) -> None:
        day, spread = self.day, 3 if self.week_cb.isChecked() else 0
        self.bg.run("day", lambda conn: _load(conn, day, spread), self._show)

    def _show(self, rows) -> None:
        while self.rows.count() > 1:
            w = self.rows.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self.year_rows = []
        self.tiles, self.selected, self._anchor = [], set(), None
        when = self.day.strftime("%B %d").replace(" 0", " ")
        span = f"the week around {when}" if self.week_cb.isChecked() else when
        total = sum(len(ids) for _, ids, _ in rows)
        self.summary.setText(f"{total:,} photo{'s' if total != 1 else ''} from {len(rows)} year{'s' if len(rows) != 1 else ''}"
                             if rows else "")
        if not rows:
            self.rows.insertWidget(0, QLabel(f"No photos from {span} in any year yet.", objectName="Help"))
            return
        this_year = date.today().year
        for n, (year, ids, thumbs) in enumerate(rows):
            box = QWidget()
            v = QVBoxLayout(box)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(6)
            top = QHBoxLayout()
            ago = this_year - year
            title = f"{year}" + (f" · {ago} year{'s' if ago != 1 else ''} ago" if ago > 0 else " · this year")
            top.addWidget(QLabel(title, objectName="SectionTitle"))
            top.addWidget(QLabel(f"{len(ids):,} photo{'s' if len(ids) != 1 else ''}", objectName="Count"))
            top.addStretch(1)
            show = QPushButton("Show all")
            show.clicked.connect(lambda _=False, ids=ids, y=year: self.show_ids.emit(list(ids), f"{span}, {y}"))
            top.addWidget(show)
            v.addLayout(top)
            strip = QHBoxLayout()
            strip.setSpacing(6)
            for fid, img in thumbs:
                lab = _Tile(self, fid)
                self.tiles.append(lab)
                lab.setFixedHeight(THUMB_H + 6)
                if not img.isNull():
                    lab.setPixmap(QPixmap.fromImage(img))
                strip.addWidget(lab)
            strip.addStretch(1)
            v.addLayout(strip)
            self.rows.insertWidget(n, box)
            self.year_rows.append((year, ids, show))
