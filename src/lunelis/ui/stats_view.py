"""
The Stats page (sidebar > Photos > Stats): how you shoot and what you keep.

- **At a glance:** photos, days out shooting, keepers (Picks) and the keeper
  rate, your most used camera, lens and focal length, busiest hour and day.
- **Keeper rate by** lens, camera, focal length, aperture, ISO or shutter
  speed - a bar per value: its length is how many photos, the dark part how
  many you kept, with the rate written beside it.
- **Focal lengths on a lens:** where a zoom really gets used.
- **Months:** photos and shoot days per month.
- **Your year:** a recap picture of a year, saved to the Create folder.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QPainter
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from lunelis import stats
from lunelis.ui import theme
from lunelis.ui.background import Background


class BarChart(QWidget):
    """Horizontal bars: total (light) with the kept part (accent) over it."""

    ROW = 26

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.rows: list[stats.Row] = []
        self.show_rate = True

    def set_rows(self, rows: list[stats.Row], show_rate: bool = True) -> None:
        self.rows, self.show_rate = rows, show_rate
        self.setMinimumHeight(max(1, len(rows)) * self.ROW + 8)
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(500, max(1, len(self.rows)) * self.ROW + 8)

    def paintEvent(self, _e) -> None:
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.rows:
            p.setPen(theme.qcolor(t.text_muted))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No photos with these details yet")
            return
        f = QFont(self.font())
        p.setFont(f)
        fm = p.fontMetrics()
        label_w = min(int(self.width() * 0.34), max(fm.horizontalAdvance(r.bucket) for r in self.rows) + 12)
        tail_w = fm.horizontalAdvance("88,888 · 100 % kept") + 12 if self.show_rate else fm.horizontalAdvance("88,888") + 12
        bar_w = max(40, self.width() - label_w - tail_w)
        most = max(r.photos for r in self.rows) or 1
        for i, r in enumerate(self.rows):
            y = 4 + i * self.ROW
            p.setPen(theme.qcolor(t.text))
            p.drawText(QRectF(0, y, label_w - 8, self.ROW), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                       fm.elidedText(r.bucket, Qt.TextElideMode.ElideMiddle, label_w - 8))
            w = bar_w * r.photos / most
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.qcolor(t.border))
            p.drawRoundedRect(QRectF(label_w, y + 5, max(2.0, w), self.ROW - 10), 4, 4)
            if self.show_rate and r.keepers:
                p.setBrush(theme.qcolor(t.accent))
                p.drawRoundedRect(QRectF(label_w, y + 5, max(2.0, bar_w * r.keepers / most), self.ROW - 10), 4, 4)
            p.setPen(theme.qcolor(t.text_muted))
            tail = f"{r.photos:,}" + (f" · {r.rate * 100:.0f} % kept" if self.show_rate else "")
            p.drawText(QRectF(label_w + w + 8, y, tail_w + 40, self.ROW), Qt.AlignmentFlag.AlignVCenter, tail)
        p.end()


class StatsView(QWidget):
    def __init__(self, conn, parent=None) -> None:
        super().__init__(parent)
        self.conn = conn
        self.bg = Background(self, conn)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        head = QWidget(objectName="Toolbar")
        head.setFixedHeight(64)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(24, 0, 24, 0)
        hl.addWidget(QLabel("Stats", objectName="PageTitle"))
        hl.addStretch(1)
        hl.addWidget(QLabel("A keeper is a photo you flagged Pick.", objectName="Help"))
        outer.addWidget(head)
        scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.Shape.NoFrame)
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(32, 20, 32, 32)
        v.setSpacing(14)

        v.addWidget(QLabel("At a glance", objectName="SectionTitle"))
        self.glance = QGridLayout()
        self.glance.setHorizontalSpacing(28)
        v.addLayout(self.glance)

        row = QHBoxLayout()
        row.addWidget(QLabel("Keeper rate by", objectName="SectionTitle"))
        self.by = QComboBox()
        for key, title in stats.BY.items():
            self.by.addItem(title, key)
        self.by.currentIndexChanged.connect(self._load_by)
        row.addWidget(self.by)
        row.addStretch(1)
        v.addLayout(row)
        self.by_chart = BarChart()
        v.addWidget(self.by_chart)

        row = QHBoxLayout()
        row.addWidget(QLabel("Focal lengths on", objectName="SectionTitle"))
        self.lens = QComboBox()
        self.lens.setMinimumContentsLength(24)
        self.lens.currentIndexChanged.connect(self._load_focal)
        row.addWidget(self.lens)
        row.addStretch(1)
        v.addLayout(row)
        self.focal_chart = BarChart()
        v.addWidget(self.focal_chart)

        v.addWidget(QLabel("Photos per month", objectName="SectionTitle"))
        self.month_chart = BarChart()
        v.addWidget(self.month_chart)

        row = QHBoxLayout()
        row.addWidget(QLabel("Your year", objectName="SectionTitle"))
        self.year = QComboBox()
        row.addWidget(self.year)
        self.recap_b = QPushButton("Make a recap picture", clicked=self.make_recap)
        row.addWidget(self.recap_b)
        row.addStretch(1)
        v.addLayout(row)
        self.recap_note = QLabel(objectName="Help")
        self.recap_note.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.recap_note.linkActivated.connect(lambda href: QDesktopServices.openUrl(QUrl.fromLocalFile(href)))
        v.addWidget(self.recap_note)
        v.addStretch(1)
        scroll.setWidget(page)
        outer.addWidget(scroll, 1)

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
        hour = f"{h.hour:02d}:00" if h.hour is not None else "-"
        cells = [("Photos", f"{h.photos:,}"), ("Days out shooting", f"{h.days:,}"),
                 ("Keepers", f"{h.keepers:,} ({h.rate * 100:.0f} %)"), ("Most used camera", h.camera or "-"),
                 ("Most used lens", h.lens or "-"), ("Favourite focal length", h.focal or "-"),
                 ("Busiest hour", hour), ("Busiest day", h.weekday or "-")]
        for n, (k, val) in enumerate(cells):
            r, c = divmod(n, 4)
            self.glance.addWidget(QLabel(k, objectName="Help"), r * 2, c)
            self.glance.addWidget(QLabel(val, objectName="GlanceValue"), r * 2 + 1, c)
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

    def _load_by(self) -> None:
        by = self.by.currentData()
        self.bg.run("by", lambda c: stats.keeper_rate(c, by), lambda rows: self.by_chart.set_rows(rows[:25]))

    def _load_focal(self) -> None:
        lens = self.lens.currentText()
        if not lens:
            self.focal_chart.set_rows([])
            return
        self.bg.run("focal", lambda c: stats.focal_use(c, lens), self.focal_chart.set_rows)

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
