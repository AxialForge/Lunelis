"""
Line icons for the sidebar and toolbars.

Drawn here as small SVGs on a 24 x 24 grid (rounded 1.8 px strokes, the
Lunelis logo's soft geometry) instead of shipped image files, so they are
always crisp at any display scale and take their colour from the theme:
`icon(name, normal, active)` paints `normal` for the usual state and `active`
for a checked button.
"""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

# Path data only; the <svg> wrapper below adds the stroke style.
_PATHS = {
    "library": '<rect x="3.5" y="3.5" width="7" height="7" rx="1.6"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.6"/>'
               '<rect x="3.5" y="13.5" width="7" height="7" rx="1.6"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.6"/>',
    "albums": '<rect x="6.5" y="3.5" width="14" height="12" rx="2"/><path d="M3.5 7.5v10a3 3 0 0 0 3 3h11"/>'
              '<path d="M8.5 13.5l3.2-3.4 2.6 2.4 2-1.8 2.2 2.8"/>',
    "tags": '<path d="M3.5 12.2V4.6a1.1 1.1 0 0 1 1.1-1.1h7.6l8.3 8.3a1.6 1.6 0 0 1 0 2.3l-6.2 6.2a1.6 1.6 0 0 1-2.3 0z"/>'
            '<circle cx="8.2" cy="8.2" r="1.4"/>',
    "import": '<path d="M12 3.5v10"/><path d="M8 9.8l4 4 4-4"/><path d="M3.5 14.5v3a3 3 0 0 0 3 3h11a3 3 0 0 0 3-3v-3"/>',
    "migrate": '<path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>'
               '<path d="M8.5 13.5h7"/><path d="M13 10.8l2.7 2.7-2.7 2.7"/>',
    "duplicates": '<rect x="8.5" y="8.5" width="12" height="12" rx="2"/><path d="M15.5 8.5v-3a2 2 0 0 0-2-2h-8a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h3"/>',
    "damaged": '<path d="M5.5 3.5h9l4 4v13h-13z"/><path d="M14.5 3.5v4h4"/><path d="M9 11.5l2.2 2.2-1.6 1.8 2.6 2.5"/>',
    "backups": '<rect x="3.5" y="13.5" width="17" height="7" rx="2"/><path d="M7 17h.01"/><path d="M12 3.5v7"/>'
               '<path d="M8.8 6.7L12 3.5l3.2 3.2"/>',
    "quarantine": '<rect x="3.5" y="4" width="17" height="4.5" rx="1.2"/><path d="M5 8.5v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-10"/>'
                  '<path d="M10 12.5h4"/>',
    "settings": '<path d="M4 6h9M17 6h3M4 12h2.5M10.5 12h9.5M4 18h7M15 18h5"/>'
                '<circle cx="15" cy="6" r="2"/><circle cx="8.5" cy="12" r="2"/><circle cx="13" cy="18" r="2"/>',
    "edit": '<path d="M4 20l4.4-1L19 8.4a2.1 2.1 0 0 0 0-3l-.4-.4a2.1 2.1 0 0 0-3 0L5 15.6z"/><path d="M14 6.5l3.5 3.5"/>',
    "create": '<rect x="3" y="3" width="8" height="8" rx="1.5"/><rect x="13" y="3" width="8" height="5" rx="1.5"/>'
              '<rect x="13" y="10" width="8" height="11" rx="1.5"/><rect x="3" y="13" width="8" height="8" rx="1.5"/>',
    "status": '<path d="M3 12h4.2l2.6-6.5 4.4 13 2.6-6.5H21"/>',
    "collapse": '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M9.5 4.5v15"/><path d="M15.5 9.5l-2.5 2.5 2.5 2.5"/>',
    "expand": '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M9.5 4.5v15"/><path d="M13 9.5l2.5 2.5-2.5 2.5"/>',
}

NAV_ICONS = {"Library": "library", "Albums": "albums", "Tags": "tags", "Import": "import", "Migrate": "migrate",
             "Duplicates": "duplicates", "Damaged files": "damaged", "Backups": "backups",
             "Quarantine": "quarantine", "Settings": "settings", "Library status": "status",
             "Edit": "edit", "Create": "create"}


def _pixmap(name: str, color: str, size: int) -> QPixmap:
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
           f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{_PATHS[name]}</svg>')
    app = QApplication.instance()
    ratio = max(app.devicePixelRatio() if app else 1.0, 1.0)
    pix = QPixmap(int(size * ratio), int(size * ratio))
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    QSvgRenderer(QByteArray(svg.encode())).render(painter, QRectF(0, 0, pix.width(), pix.height()))
    painter.end()
    pix.setDevicePixelRatio(ratio)
    return pix


def icon(name: str, normal: str, active: str | None = None, size: int = 20) -> QIcon:
    ic = QIcon()
    ic.addPixmap(_pixmap(name, normal, size), QIcon.Mode.Normal, QIcon.State.Off)
    ic.addPixmap(_pixmap(name, active or normal, size), QIcon.Mode.Normal, QIcon.State.On)
    ic.addPixmap(_pixmap(name, active or normal, size), QIcon.Mode.Active, QIcon.State.Off)
    return ic


def names() -> list[str]:
    return list(_PATHS)
