"""
The header every page starts with (0.48): a 64 px bar with the page's title
on the left and room for its controls on the right - so pages look alike.

    bar, row = page_header("Damaged files")
    row.addWidget(some_button)

Since 0.55 it is the ONE header style: banded bar, title left, a muted count
beside it, actions right, an optional one-line description below.

    bar, row = page_header("Tags", count="128 tags",
                           description="Words you put on photos.")
    row.addWidget(new_tag_button)          # lands on the right
    bar.set_count("129 tags")              # later, when the number changes
    bar.set_description("")                # an empty text hides the line

The row already holds  title, count, stretch  - so whatever a page adds goes
to the right edge. To put something beside the title instead, use
`row.insertWidget(bar.LEFT, widget)`.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

BAR_HEIGHT = 64                 # the band without a description line


class PageHeader(QWidget):
    """The banded bar. A class (not closures hung on a plain widget - see the
    closure gotcha in CLAUDE.md) so pages can change the count later (0.55)."""

    LEFT = 2                    # row index right after the title and the count

    def __init__(self, title: str, count: str | None = None, description: str | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Toolbar")
        # A subclass of QWidget paints no stylesheet background without this:
        # the band would come out see-through (0.55).
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.row = QHBoxLayout()
        self.row.setContentsMargins(24, 0, 24, 0)
        self.row.setSpacing(12)
        self.title_label = QLabel(title, objectName="PageTitle")
        self.count_label = QLabel("", objectName="PageCount")
        self.row.addWidget(self.title_label)
        self.row.addWidget(self.count_label)
        self.row.addStretch(1)
        outer.addLayout(self.row, 1)

        self.description_label = QLabel("", objectName="PageDescription")
        self.description_label.setContentsMargins(24, 0, 24, 12)
        outer.addWidget(self.description_label)

        self.set_count(count)
        self.set_description(description)

    def set_title(self, text: str) -> None:
        self.title_label.setText(text)

    def set_count(self, text: str | int | None) -> None:
        """The muted figure beside the title ("128 tags"); None or "" hides it."""
        text = "" if text is None else str(text)
        self.count_label.setText(text)
        self.count_label.setVisible(bool(text))

    def set_description(self, text: str | None) -> None:
        """One muted line under the title; None or "" hides it and the bar is
        64 px again."""
        text = text or ""
        self.description_label.setText(text)
        self.description_label.setVisible(bool(text))
        if text:
            # Tall enough for the band plus the line, at any Windows text size.
            self.setMinimumHeight(BAR_HEIGHT)
            self.setMaximumHeight(16777215)
            self.setFixedHeight(BAR_HEIGHT - 14 + self.description_label.sizeHint().height())
        else:
            self.setFixedHeight(BAR_HEIGHT)


def page_header(title: str, count: str | int | None = None,
                description: str | None = None) -> tuple[PageHeader, QHBoxLayout]:
    """(bar, row) as before; `count` and `description` are optional (0.55)."""
    bar = PageHeader(title, count, description)
    return bar, bar.row
