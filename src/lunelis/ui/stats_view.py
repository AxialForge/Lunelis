"""
The Stats page (sidebar > Photos > Stats): how you shoot and what you keep.

- **At a glance:** cards for photos, days out shooting, keepers (Picks) and
  the keeper rate, your most used camera, lens and focal length, busiest hour
  and day.
- **Breakdown by** lens, camera, focal length, aperture, ISO or shutter
  speed - a bar per value: its length is how many photos, the accent part how
  many you kept (once you have keepers), with the figures in their own column.
- **Focal lengths on a lens:** where a zoom really gets used, grouped into
  ranges when a zoom has many.
- **Months:** photos per month.
- **Your year:** a recap picture of a year, saved to the Create folder.

The page keeps to a readable width on wide screens; every chart's figures
sit in a column of their own, so nothing runs off the edge.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QPainter
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout,
    QWidget,
)

from lunelis import stats
from lunelis.ui import theme
from lunelis.ui.background import Background

PAGE_MAX = 1120              # px: wider than this, the page centres instead of stretching
FOCAL_GROUP_OVER = 16        # a lens with more focal lengths than this shows ranges


class BarChart(QWidget):
    """Horizontal bars: total (grey) with the kept part (accent) over it. Labels,
    bars and figures each have their own column."""

    ROW = 28

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.rows: list[stats.Row] = []
        self.show_rate = True
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_rows(self, rows: list[stats.Row], show_rate: bool = True) -> None:
        self.rows, self.show_rate = rows, show_rate
        self.setFixedHeight(max(1, len(rows)) * self.ROW + 8)
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(560, max(1, len(self.rows)) * self.ROW + 8)

    def minimumSizeHint(self) -> QSize:
        return QSize(280, self.height())

    def paintEvent(self, _e) -> None:
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.rows:
            p.setPen(theme.qcolor(t.text_muted))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No photos with these details yet")
            return
        p.setFont(QFont(self.font()))
        fm = p.fontMetrics()

        def tail_text(r: stats.Row) -> str:
            return f"{r.photos:,}  ·  {r.rate * 100:.0f} % kept" if self.show_rate else f"{r.photos:,}"
        label_w = min(int(self.width() * 0.3), max(fm.horizontalAdvance(r.bucket) for r in self.rows) + 16)
        tail_w = max(fm.horizontalAdvance(tail_text(r)) for r in self.rows) + 16
        bar_x = label_w
        bar_w = max(40, self.width() - label_w - tail_w - 8)
        most = max(r.photos for r in self.rows) or 1
        for i, r in enumerate(self.rows):
            y = 4 + i * self.ROW
            p.setPen(theme.qcolor(t.text))
            p.drawText(QRectF(0, y, label_w - 10, self.ROW), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                       fm.elidedText(r.bucket, Qt.TextElideMode.ElideMiddle, label_w - 10))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.qcolor(t.surface_alt))
            p.drawRoundedRect(QRectF(bar_x, y + 7, bar_w, self.ROW - 14), 4, 4)            # the track
            p.setBrush(theme.qcolor(t.border))
            p.drawRoundedRect(QRectF(bar_x, y + 7, max(3.0, bar_w * r.photos / most), self.ROW - 14), 4, 4)
            if self.show_rate and r.keepers:
                p.setBrush(theme.qcolor(t.accent))
                p.drawRoundedRect(QRectF(bar_x, y + 7, max(3.0, bar_w * r.keepers / most), self.ROW - 14), 4, 4)
            p.setPen(theme.qcolor(t.text_muted))
            p.drawText(QRectF(bar_x + bar_w + 12, y, tail_w, self.ROW),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, tail_text(r))
        p.end()


def group_focal(rows: list[stats.Row], limit: int = FOCAL_GROUP_OVER) -> list[stats.Row]:
    """A zoom used at many focal lengths: ranges (20-29 mm, 30-39 mm...) instead of a bar per millimetre."""
    if len(rows) <= limit:
        return rows
    mm = [(int(float(r.bucket.split()[0])), r) for r in rows]
    lo, hi = mm[0][0], mm[-1][0]
    want = max(2, -(-(hi - lo + 1) // limit))
    step = next((s for s in (2, 5, 10, 20, 25, 50, 100, 200) if s >= want), want)
    lo = lo // step * step                                    # ranges on round numbers: 20-29, not 24-33
    out: dict[int, list[int]] = {}
    for v, r in mm:
        acc = out.setdefault(lo + (v - lo) // step * step, [0, 0])
        acc[0] += r.photos
        acc[1] += r.keepers
    return [stats.Row(f"{s}-{s + step - 1} mm", n, k) for s, (n, k) in sorted(out.items())]


class StatsView(QWidget):
    def __init__(self, conn, parent=None) -> None:
        super().__init__(parent)
        self.conn = conn
        self.bg = Background(self, conn)
        self.has_keepers = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        head = QWidget(objectName="Toolbar")
        head.setFixedHeight(64)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(24, 0, 24, 0)
        hl.addWidget(QLabel("Stats", objectName="PageTitle"))
        hl.addStretch(1)
        hl.addWidget(QLabel("A keeper is a photo you flagged Pick (P).", objectName="Help"))
        outer.addWidget(head)
        scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.Shape.NoFrame)
        holder = QWidget()
        hh = QHBoxLayout(holder)
        hh.setContentsMargins(0, 0, 0, 0)
        page = QWidget()
        page.setMaximumWidth(PAGE_MAX)
        hh.addWidget(page, 1, Qt.AlignmentFlag.AlignHCenter)
        v = QVBoxLayout(page)
        v.setContentsMargins(32, 20, 32, 32)
        v.setSpacing(16)

        v.addWidget(QLabel("At a glance", objectName="SectionTitle"))
        self.glance = QGridLayout()
        self.glance.setHorizontalSpacing(12)
        self.glance.setVerticalSpacing(12)
        v.addLayout(self.glance)
        self.keeper_note = QLabel(objectName="Help", wordWrap=True)
        self.keeper_note.hide()
        v.addWidget(self.keeper_note)

        card, cv = self._section()
        row = QHBoxLayout()
        row.addWidget(QLabel("Breakdown by", objectName="SectionTitle"))
        self.by = QComboBox()
        for key, title in stats.BY.items():
            self.by.addItem(title, key)
        self.by.currentIndexChanged.connect(self._load_by)
        row.addWidget(self.by)
        row.addStretch(1)
        cv.addLayout(row)
        self.by_chart = BarChart()
        cv.addWidget(self.by_chart)
        v.addWidget(card)

        card, cv = self._section()
        row = QHBoxLayout()
        row.addWidget(QLabel("Focal lengths on", objectName="SectionTitle"))
        self.lens = QComboBox()
        self.lens.setMinimumContentsLength(20)
        self.lens.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.lens.currentIndexChanged.connect(self._load_focal)
        row.addWidget(self.lens, 1)
        cv.addLayout(row)
        self.focal_chart = BarChart()
        cv.addWidget(self.focal_chart)
        v.addWidget(card)

        card, cv = self._section()
        cv.addWidget(QLabel("Photos per month", objectName="SectionTitle"))
        self.month_chart = BarChart()
        cv.addWidget(self.month_chart)
        v.addWidget(card)

        card, cv = self._section()
        row = QHBoxLayout()
        row.addWidget(QLabel("Your year", objectName="SectionTitle"))
        self.year = QComboBox()
        row.addWidget(self.year)
        self.recap_b = QPushButton("Make a recap picture", clicked=self.make_recap)
        row.addWidget(self.recap_b)
        row.addStretch(1)
        cv.addLayout(row)
        self.recap_note = QLabel(objectName="Help")
        self.recap_note.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.recap_note.linkActivated.connect(lambda href: QDesktopServices.openUrl(QUrl.fromLocalFile(href)))
        cv.addWidget(self.recap_note)
        v.addWidget(card)
        v.addStretch(1)
        scroll.setWidget(holder)
        outer.addWidget(scroll, 1)

    @staticmethod
    def _section() -> tuple[QFrame, QVBoxLayout]:
        card = QFrame(objectName="Card")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(18, 14, 18, 14)
        cv.setSpacing(10)
        return card, cv

    @staticmethod
    def _glance_card(label: str, value: str) -> QFrame:
        card = QFrame(objectName="Card")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(14, 10, 14, 12)
        cv.setSpacing(2)
        val = QLabel(value, objectName="GlanceValue")
        f = val.font()
        f.setPointSizeF(f.pointSizeF() * 1.45)
        f.setBold(True)
        val.setFont(f)
        val.setWordWrap(True)
        cv.addWidget(val)
        cv.addWidget(QLabel(label, objectName="Help"))
        return card

    def glance_values(self) -> list[str]:
        """The figures in the At a glance cards, in order."""
        out = []
        for i in range(self.glance.count()):
            card = self.glance.itemAt(i).widget()
            if card is not None:
                out.append(card.findChild(QLabel, "GlanceValue").text())
        return out

    # --- loading ------------------------------------------------------------------------------

    def refresh(self) -> None:
        def load(conn):
            lenses = [r.bucket for r in stats.keeper_rate(conn, "lens")]
            return stats.habits(conn), lenses, stats.per_month(conn), stats.years(conn)
        self.bg.run("overview", load, self._show_overview)
        self._load_by()

    def _show_overview(self, data) -> None:
        h, lenses, months, years = data
        while self.glance.count():
            w = self.glance.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self.has_keepers = h.keepers > 0
        hour = f"{h.hour:02d}:00" if h.hour is not None else "-"
        keepers = f"{h.keepers:,} ({h.rate * 100:.0f} %)" if self.has_keepers else "None yet"
        cells = [("Photos", f"{h.photos:,}"), ("Days out shooting", f"{h.days:,}"),
                 ("Keepers", keepers), ("Most used camera", h.camera or "-"),
                 ("Most used lens", h.lens or "-"), ("Favourite focal length", h.focal or "-"),
                 ("Busiest hour", hour), ("Busiest day", h.weekday or "-")]
        for n, (k, val) in enumerate(cells):
            r, c = divmod(n, 4)
            self.glance.addWidget(self._glance_card(k, val), r, c)
        for c in range(4):
            self.glance.setColumnStretch(c, 1)
        self.keeper_note.setVisible(not self.has_keepers)
        self.keeper_note.setText("No keepers yet: flag the photos you'd keep as Pick (P, or full-screen culling "
                                 "with Ctrl+K) and the charts show what you keep - by lens, aperture, shutter "
                                 "speed and more.")
        keep = self.lens.currentText()
        self.lens.blockSignals(True)
        self.lens.clear()
        self.lens.addItems(lenses)
        if keep in lenses:
            self.lens.setCurrentText(keep)
        self.lens.blockSignals(False)
        self._load_focal()
        self.month_chart.set_rows([stats.Row(m, n, s) for m, n, s in months], show_rate=False)
        keep_year = self.year.currentText()
        self.year.clear()
        self.year.addItems([str(y) for y in years])
        if keep_year:
            self.year.setCurrentText(keep_year)
        self.recap_b.setEnabled(bool(years))
        self._load_by()

    def _load_by(self) -> None:
        by = self.by.currentData()
        shape = group_focal if by == "focal" else (lambda rows: rows[:25])
        self.bg.run("by", lambda c: stats.keeper_rate(c, by),
                    lambda rows: self.by_chart.set_rows(shape(rows), show_rate=self.has_keepers))

    def _load_focal(self) -> None:
        lens = self.lens.currentText()
        if not lens:
            self.focal_chart.set_rows([])
            return
        self.bg.run("focal", lambda c: stats.focal_use(c, lens),
                    lambda rows: self.focal_chart.set_rows(group_focal(rows), show_rate=self.has_keepers))

    def make_recap(self) -> None:
        if not self.year.currentText():
            return
        year = int(self.year.currentText())
        from lunelis.create import engine
        folder = engine.output_dir(self.conn)

        def made(path: str) -> None:
            self.recap_note.setText(f'Made <a href="{path}">your {year} recap</a> in {folder}')
        self.bg.run("recap", lambda c: engine.save(stats.recap_image(c, year), engine.Preset("Recap", format="png"),
                                                  folder, f"Lunelis {year} recap"), made,
                    error=lambda e: self.recap_note.setText(f"Couldn't make the recap: {e}"))
