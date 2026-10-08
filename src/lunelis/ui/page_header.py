"""
The header every page starts with (0.48): a 64 px bar with the page's title
on the left and room for its controls on the right - so pages look alike.

    bar, row = page_header("Damaged files")
    row.addWidget(some_button)
"""
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget


def page_header(title: str) -> tuple[QWidget, QHBoxLayout]:
    bar = QWidget(objectName="Toolbar")
    bar.setFixedHeight(64)
    row = QHBoxLayout(bar)
    row.setContentsMargins(24, 0, 24, 0)
    row.setSpacing(12)
    row.addWidget(QLabel(title, objectName="PageTitle"))
    return bar, row
