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
    "stats": '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    "dust": '<rect x="3.5" y="5.5" width="17" height="13" rx="2"/><circle cx="9" cy="10" r="1.3"/>'
            '<circle cx="15" cy="14" r="1.8"/><circle cx="16" cy="9" r=".8"/>',
    "people": '<circle cx="9" cy="8" r="3.2"/><path d="M3.5 19.5a5.5 5.5 0 0 1 11 0"/>'
              '<circle cx="16.5" cy="9" r="2.4"/><path d="M15.5 14.2a4.5 4.5 0 0 1 5 5.3"/>',
    "map": '<path d="M9 4.5L3.5 6.5v13L9 17.5l6 2 5.5-2v-13L15 6.5z"/><path d="M9 4.5v13M15 6.5v13"/>',
    "calendar": '<rect x="3.5" y="5" width="17" height="15.5" rx="2"/><path d="M3.5 10h17M8 3v4M16 3v4"/>'
                '<path d="M12 13.5v3h2.5"/>',
    "create": '<path d="M4 20L15 9"/><path d="M13.5 7.5l3 3"/><path d="M17 3v3M15.5 4.5h3M20 9v2M19 10h2M10 3v2M9 4h2"/>',
    "collage": '<rect x="3" y="3" width="8" height="8" rx="1.5"/><rect x="13" y="3" width="8" height="5" rx="1.5"/>'
               '<rect x="13" y="10" width="8" height="11" rx="1.5"/><rect x="3" y="13" width="8" height="8" rx="1.5"/>',
    "animation": '<rect x="3" y="5" width="18" height="14" rx="1.5"/><path d="M7 5v14M17 5v14M3 9h4M3 15h4M17 9h4M17 15h4"/>',
    "batch": '<rect x="7" y="3" width="13" height="13" rx="1.5"/><path d="M4 7v12a1 1 0 0 0 1 1h12"/>',
    "contact": '<rect x="3.5" y="3.5" width="17" height="17" rx="1.5"/><path d="M3.5 9.5h17M3.5 15h17M9.5 3.5v17M15 3.5v17"/>',
    "slideshow": '<rect x="3" y="4" width="18" height="13" rx="1.5"/><path d="M10.5 8v5l4-2.5z"/><path d="M8 21h8"/>',
    "before_after": '<rect x="3.5" y="4.5" width="17" height="15" rx="1.5"/><path d="M12 3v18"/><path d="M6 15l2.5-3 2 2"/>',
    "print": '<path d="M7 9V3.5h10V9"/><rect x="3.5" y="9" width="17" height="7.5" rx="1.5"/><path d="M7 14h10v6.5H7z"/>',
    "focus": '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r="1"/>',
    "trails": '<path d="M5 19a9 9 0 0 1 14-11"/><path d="M8 19a6 6 0 0 1 9-7"/><path d="M11 19a3 3 0 0 1 4-3"/><circle cx="19" cy="5" r="1"/>',
    "median": '<rect x="4" y="4" width="13" height="13" rx="1.5"/><rect x="7" y="7" width="13" height="13" rx="1.5"/>',
    "panorama": '<path d="M3 7c6 1.5 12 1.5 18 0v10c-6-1.5-12-1.5-18 0z"/>',
    "hdr": '<circle cx="12" cy="12" r="4"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M5.6 18.4L7 17M17 7l1.4-1.4"/><path d="M12 8v8" />',
    "status": '<path d="M3 12h4.2l2.6-6.5 4.4 13 2.6-6.5H21"/>',
    "takeout": '<path d="M4 7h16v12.5H4z"/><path d="M8 7V5h8v2M9 12h6"/>',
    "timelapse": '<circle cx="12" cy="13" r="7.5"/><path d="M12 9v4l2.5 2.5M9.5 3h5M12 3v2.5"/>',
    # The photo view's toolbar (0.52: text arrows were a few pixels tall).
    # The photo view's tool rail (0.52).
    "peaking": '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="3"/><path d="M12 1.5v4M12 18.5v4M1.5 12h4M18.5 12h4"/>',
    "clipping": '<circle cx="12" cy="12" r="8.5"/><path d="M12 3.5v17"/><path d="M12 6l6 6M12 10.5l5 5M12 15l3 3"/>',
    "false_colour": '<path d="M12 3.5a8.5 8.5 0 1 0 0 17c1.4 0 2-1 2-2s-.8-1.6-.8-2.6 .8-1.9 2-1.9h1.8a3.5 3.5 0 0 0 3.5-3.5c0-4-3.8-7-8.5-7z"/>'
                    '<circle cx="7.5" cy="11" r="1.2"/><circle cx="10" cy="7" r="1.2"/><circle cx="15" cy="7.5" r="1.2"/>',
    "zones": '<rect x="3.5" y="5" width="17" height="14" rx="1.5"/><path d="M7.7 5v14M11.9 5v14M16.1 5v14"/>',
    "histogram": '<path d="M3 20h18"/><path d="M5 20v-5M8 20v-9M11 20v-13M14 20v-8M17 20v-4M20 20v-2"/>',
    "guides": '<rect x="3.5" y="3.5" width="17" height="17" rx="1.5"/><path d="M9.2 3.5v17M14.8 3.5v17M3.5 9.2h17M3.5 14.8h17"/>',
    "close": '<path d="M6 6l12 12M18 6L6 18"/>',
    "rotate_left": '<path d="M4.5 9.5a8 8 0 1 1 1.4 7.6"/><path d="M4 4.5v5h5"/>',
    "rotate_right": '<path d="M19.5 9.5a8 8 0 1 0-1.4 7.6"/><path d="M20 4.5v5h-5"/>',
    "chevron_left": '<path d="M14.5 5.5L8 12l6.5 6.5"/>',
    "chevron_right": '<path d="M9.5 5.5L16 12l-6.5 6.5"/>',
    "collapse": '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M9.5 4.5v15"/><path d="M15.5 9.5l-2.5 2.5 2.5 2.5"/>',
    "expand": '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M9.5 4.5v15"/><path d="M13 9.5l2.5 2.5-2.5 2.5"/>',
}

NAV_ICONS = {"Library": "library", "Albums": "albums", "Tags": "tags", "Import": "import", "Migrate": "migrate",
             "Duplicates": "duplicates", "Damaged files": "damaged", "Backups": "backups",
             "Quarantine": "quarantine", "Settings": "settings", "Library status": "status",
             "Edit": "edit", "Create": "create", "Stats": "stats", "Map": "map", "On this day": "calendar",
             "Sensor dust": "dust", "People": "people", "Timelapses": "timelapse", "Google Takeout": "takeout"}


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
