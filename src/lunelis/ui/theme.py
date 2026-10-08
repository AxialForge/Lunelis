"""
Themes.

Every colour the UI uses comes from a token here, never a literal in a
widget, so a theme is just another set of tokens. Built in: Graphite (light,
the mockups' colours), Midnight (dark) and High Contrast; "system" follows
Windows' light/dark setting and switches live when Windows does.

`current()` is the theme in use. Widgets that paint themselves (the grid)
read it at paint time; everything else is styled by `stylesheet()` +
`apply_palette()`, which MainWindow.apply_theme() re-applies on a switch.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Theme:
    name: str
    title: str
    dark: bool
    sidebar_bg: str
    sidebar_text: str
    sidebar_muted: str
    sidebar_active_bg: str
    sidebar_footer: str
    sidebar_divider: str
    surface: str            # toolbar, cards, tables
    surface_alt: str        # filter bar, headers, read-only fields
    canvas: str             # behind the grid and cards
    border: str
    text: str
    text_muted: str
    text_faint: str
    field_bg: str
    tile_placeholder: str
    tile_unavailable: str
    badge_bg: str           # rgba
    badge_text: str
    selection: str
    rating: str
    reject_veil: str        # laid over rejected tiles
    chip_bg: str            # also primary buttons
    chip_text: str
    accent: str             # focus rings, links, the logo's indigo
    accent_text: str = "#ffffff"    # text drawn on the accent colour
    viewer_bg: str = "#111114"      # behind a photo in the detail view: neutral dark in every theme
    viewer_strip: str = "#1a1a1d"   # the filmstrip under it
    labels: tuple = ()      # (name, colour) for Red/Yellow/Green/Blue/Purple


# Picked to differ in lightness as well as hue, so they stay distinct for
# colour-blind users; the label name is also in the tooltip/menu.
_LABELS = (("Red", "#d0453b"), ("Yellow", "#f2c230"), ("Green", "#2e8b57"),
           ("Blue", "#3f7fd9"), ("Purple", "#7b4fb3"))

GRAPHITE = Theme(
    name="graphite", title="Graphite (light)", dark=False,
    sidebar_bg="#18181a", sidebar_text="#e8e8ea", sidebar_muted="#a5a5a8", sidebar_active_bg="#2c2c2e",
    sidebar_footer="#8e8e92", sidebar_divider="#2c2c2e",
    surface="#ffffff", surface_alt="#f7f7f8", canvas="#f2f2f2", border="#d9d9dc",
    text="#141414", text_muted="#55555a", text_faint="#6e6e73", field_bg="#eeeeef",
    tile_placeholder="#d9dbde", tile_unavailable="#c9ccd1",
    badge_bg="rgba(20,20,20,0.65)", badge_text="#ffffff",
    selection="#141414", rating="#a8841c", reject_veil="rgba(242,242,242,0.62)",
    chip_bg="#141414", chip_text="#ffffff", accent="#5b5bd6", labels=_LABELS,
)

MIDNIGHT = Theme(
    name="midnight", title="Midnight (dark)", dark=True,
    sidebar_bg="#0d0d10", sidebar_text="#ececf0", sidebar_muted="#9d9da6", sidebar_active_bg="#23232a",
    sidebar_footer="#8c8c95", sidebar_divider="#23232a",
    surface="#1a1a1f", surface_alt="#202026", canvas="#131317", border="#34343c",
    text="#ececf0", text_muted="#b0b0b8", text_faint="#9a9aa3", field_bg="#27272e",
    tile_placeholder="#2a2a31", tile_unavailable="#34343c",
    badge_bg="rgba(0,0,0,0.62)", badge_text="#ffffff",
    selection="#e6e6ec", rating="#e0b84a", reject_veil="rgba(19,19,23,0.66)",
    chip_bg="#e6e6ec", chip_text="#131317", accent="#8b8cf2", accent_text="#0d0d10",
    labels=(("Red", "#e5584e"), ("Yellow", "#f2c230"), ("Green", "#3fae72"),
            ("Blue", "#5b95ea"), ("Purple", "#9a72d6")),
)

HIGH_CONTRAST = Theme(
    name="high_contrast", title="High contrast", dark=True,
    sidebar_bg="#000000", sidebar_text="#ffffff", sidebar_muted="#ffffff", sidebar_active_bg="#333333",
    sidebar_footer="#e0e0e0", sidebar_divider="#ffffff",
    surface="#000000", surface_alt="#0a0a0a", canvas="#000000", border="#ffffff",
    text="#ffffff", text_muted="#ffffff", text_faint="#d8d8d8", field_bg="#1f1f1f",
    tile_placeholder="#1a1a1a", tile_unavailable="#333333",
    badge_bg="rgba(0,0,0,0.85)", badge_text="#ffff00",
    selection="#ffd400", rating="#ffd400", reject_veil="rgba(0,0,0,0.72)",
    chip_bg="#ffd400", chip_text="#000000", accent="#00e5ff", accent_text="#000000", viewer_bg="#000000",
    viewer_strip="#0a0a0a",
    labels=(("Red", "#ff5a4f"), ("Yellow", "#ffe600"), ("Green", "#3cf07a"),
            ("Blue", "#4fa8ff"), ("Purple", "#c78bff")),
)

THEMES = {t.name: t for t in (GRAPHITE, MIDNIGHT, HIGH_CONTRAST)}
CHOICES = (("system", "Follow Windows (light or dark)"),
           *((t.name, t.title) for t in (GRAPHITE, MIDNIGHT, HIGH_CONTRAST)))

_current: Theme = GRAPHITE


def current() -> Theme:
    return _current


def windows_is_dark() -> bool:
    app = QApplication.instance()
    if app is not None:
        from PySide6.QtCore import Qt
        return app.styleHints().colorScheme() == Qt.ColorScheme.Dark
    return False


def resolve(choice: str | None) -> Theme:
    """A setting value ('system', 'graphite', ...) -> the theme to use."""
    if choice in THEMES:
        return THEMES[choice]
    return MIDNIGHT if windows_is_dark() else GRAPHITE


def set_current(t: Theme) -> None:
    global _current
    _current = t


def qcolor(value: str) -> QColor:
    """QColor from a token, including 'rgba(r,g,b,a)' with a 0-1 alpha."""
    if value.startswith("rgba("):
        r, g, b, a = (p.strip() for p in value[5:-1].split(","))
        c = QColor(int(r), int(g), int(b))
        c.setAlphaF(float(a))
        return c
    return QColor(value)


def apply_palette(app: QApplication, t: Theme | None = None) -> None:
    """Pin the whole app to the theme. Without this, Windows' dark mode leaks
    into every widget the stylesheet doesn't name (tables, dialogs, combo
    popups) - dark tables in a light window, invisible button text."""
    t = t or _current
    app.setStyle("Fusion")
    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: t.canvas, QPalette.ColorRole.WindowText: t.text,
        QPalette.ColorRole.Base: t.surface, QPalette.ColorRole.AlternateBase: t.surface_alt,
        QPalette.ColorRole.Text: t.text, QPalette.ColorRole.Button: t.surface,
        QPalette.ColorRole.ButtonText: t.text, QPalette.ColorRole.ToolTipBase: t.surface,
        QPalette.ColorRole.ToolTipText: t.text, QPalette.ColorRole.PlaceholderText: t.text_faint,
        QPalette.ColorRole.Highlight: t.selection, QPalette.ColorRole.HighlightedText: t.chip_text,
        QPalette.ColorRole.Link: t.accent, QPalette.ColorRole.BrightText: t.chip_text,
    }
    for role, value in roles.items():
        pal.setColor(role, qcolor(value))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, qcolor(t.text_faint))
    app.setPalette(pal)


def label_color(name: str | None, t: Theme | None = None) -> str | None:
    return dict((t or _current).labels).get(name) if name else None


def _check_icon(t: Theme) -> str:
    """The tick in a checked checkbox, drawn in the theme's colour. Qt
    stylesheets take a file path (not data), so it's written to the cache."""
    from lunelis import paths
    folder = Path(paths.DATA_DIR) / "cache" / "ui"
    path = folder / f"check-{t.chip_text.lstrip('#')}.svg"
    if not path.exists():
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 14 14">'
                f'<path d="M3 7.2l2.6 2.6L11 4.4" fill="none" stroke="{t.chip_text}" stroke-width="2"'
                ' stroke-linecap="round" stroke-linejoin="round"/></svg>', encoding="utf-8")
        except OSError:
            return (paths.ASSETS / "icons" / "check.svg").as_posix()
    return path.as_posix()


def _arrow_icon(t: Theme, up: bool) -> str:
    """A small chevron for combo and spin boxes, in the theme's muted text colour."""
    from lunelis import paths
    folder = Path(paths.DATA_DIR) / "cache" / "ui"
    colour = t.text_muted
    path = folder / f"arrow-{'up' if up else 'down'}-{colour.lstrip('#')}.svg"
    if not path.exists():
        try:
            folder.mkdir(parents=True, exist_ok=True)
            d = "M3 8.5L7 4.5l4 4" if up else "M3 5.5l4 4 4-4"
            path.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 14 14">'
                f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="1.6"'
                ' stroke-linecap="round" stroke-linejoin="round"/></svg>', encoding="utf-8")
        except OSError:
            return ""
    return path.as_posix()


BASE_POINT_SIZE = 9.0          # Windows' message font at 100 % text size (Segoe UI 9 pt)


def text_scale() -> float:
    """Windows' "Make text bigger" (Settings > Accessibility > Text size), as a
    factor: Qt's default font follows it, our pixel sizes didn't."""
    app = QApplication.instance()
    pt = app.font().pointSizeF() if app is not None else BASE_POINT_SIZE
    return max(1.0, pt / BASE_POINT_SIZE) if pt > 0 else 1.0


def font_pt(px: float) -> float:
    """A size designed in pixels at 100 %, in points, grown with the text size."""
    return round(px * 0.75 * text_scale(), 2)


def _scale_fonts(qss: str) -> str:
    import re
    return re.sub(r"font-size:\s*(\d+(?:\.\d+)?)px", lambda m: f"font-size: {font_pt(float(m.group(1)))}pt", qss)


def _tint(hex_colour: str, alpha: float) -> str:
    """The accent as a see-through tint: a table's selected row (0.48 - it was a
    heavy near-black bar in the light theme)."""
    c = QColor(hex_colour)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {int(alpha * 255)})"


def stylesheet(t: Theme | None = None) -> str:
    return _scale_fonts(_stylesheet(t))


def _stylesheet(t: Theme | None = None) -> str:
    t = t or _current
    check = _check_icon(t)
    down, up = _arrow_icon(t, False), _arrow_icon(t, True)
    red = dict(t.labels)["Red"]
    return f"""
    QMainWindow, QWidget#Main {{ background: {t.canvas}; }}
    QToolTip {{ background: {t.surface}; color: {t.text}; border: 1px solid {t.border}; padding: 4px 6px; }}
    QMenuBar {{ background: {t.surface}; color: {t.text}; border-bottom: 1px solid {t.border}; }}
    QMenuBar::item {{ padding: 4px 10px; background: transparent; }}
    QMenuBar::item:selected {{ background: {t.field_bg}; }}
    QMenu {{ background: {t.surface}; color: {t.text}; border: 1px solid {t.border}; }}
    QMenu::item {{ padding: 6px 24px; }}
    QMenu::item:selected {{ background: {t.field_bg}; }}
    QMenu::item:disabled {{ color: {t.text_faint}; }}
    QMenu::separator {{ height: 1px; background: {t.border}; margin: 4px 8px; }}

    QWidget#Sidebar {{ background: {t.sidebar_bg}; }}
    QScrollArea#SidebarScroll, QWidget#SidebarNav {{ background: transparent; border: none; }}
    QScrollArea#SidebarScroll QScrollBar:vertical {{ width: 10px; background: transparent; }}
    QScrollArea#SidebarScroll QScrollBar::handle:vertical {{ background: {t.sidebar_muted}; border-radius: 4px; margin: 1px; min-height: 24px; }}
    QLabel#AppName {{ color: {t.sidebar_text}; font-size: 18px; font-weight: 700; }}
    QPushButton#NavSection {{
        color: {t.sidebar_footer}; background: transparent; border: none; text-align: left;
        font-size: 11px; font-weight: 700; letter-spacing: 1px; padding: 14px 12px 4px 12px;
    }}
    QPushButton#NavSection:hover {{ color: {t.sidebar_text}; }}
    QPushButton#NavItem {{
        color: {t.sidebar_muted}; background: transparent; border: none; border-radius: 6px;
        padding: 8px 12px 8px 12px; font-size: 14px; text-align: left;
    }}
    QWidget#Sidebar[compact="true"] QPushButton#NavItem,
    QWidget#Sidebar[compact="true"] QPushButton#NavBottom {{ padding: 9px 0; text-align: center; }}
    QFrame#NavRule {{ background: {t.sidebar_divider}; margin: 6px 6px; }}
    QWidget#Sidebar[dense="true"] QPushButton#NavItem {{ padding: 4px 12px; }}
    QWidget#Sidebar[dense="true"] QPushButton#NavSection {{ padding: 6px 12px 2px 12px; }}
    QWidget#Sidebar[dense="true"][compact="true"] QPushButton#NavItem {{ padding: 5px 0; }}
    QPushButton#NavItem:hover:enabled {{ background: {t.sidebar_active_bg}; color: {t.sidebar_text}; }}
    QPushButton#NavItem:checked {{ color: {t.sidebar_text}; background: {t.sidebar_active_bg}; font-weight: 600; }}
    QPushButton#NavBottom {{
        color: {t.sidebar_muted}; background: transparent; border: 1px solid {t.sidebar_divider};
        border-radius: 6px; padding: 8px 12px; font-size: 14px;
    }}
    QPushButton#NavBottom:hover, QPushButton#NavBottom:checked {{
        background: {t.sidebar_active_bg}; color: {t.sidebar_text};
    }}
    QLabel#SidebarFooter {{
        color: {t.sidebar_footer}; font-size: 12px; border-top: 1px solid {t.sidebar_divider};
        padding: 12px 0 0 0; margin-top: 8px;
    }}

    QWidget#Toolbar {{ background: {t.surface}; border-bottom: 1px solid {t.border}; }}
    QScrollArea#PageScroll, QScrollArea#PageScroll > QWidget > QWidget {{ background: {t.canvas}; }}
    QLineEdit#Search {{
        background: {t.field_bg}; border: none; border-radius: 8px; padding: 0 12px;
        font-size: 13px; color: {t.text}; min-height: 36px; max-width: 520px; min-width: 120px;
    }}
    QLineEdit#Search:disabled {{ color: {t.text_faint}; }}
    QLabel#ToolLabel {{ color: {t.text_muted}; font-size: 13px; }}
    QLabel#PageTitle {{ color: {t.text}; font-size: 18px; font-weight: 700; }}
    QComboBox#Sort {{
        color: {t.text_muted}; font-size: 13px; border: none; background: transparent; padding: 4px 6px;
    }}
    QFrame#VDivider {{ background: {t.border}; max-width: 1px; min-width: 1px; min-height: 24px; max-height: 24px; }}

    QWidget#FilterBar {{ background: {t.surface_alt}; border-bottom: 1px solid {t.border}; }}
    QLabel#FilterLabel {{ color: {t.text_faint}; font-size: 12px; }}
    QLabel#Count {{ color: {t.text_faint}; font-size: 12px; }}
    QToolButton#FilterMenu {{
        color: {t.text_muted}; font-size: 12px; border: 1px solid {t.border}; border-radius: 12px;
        padding: 3px 10px; background: {t.surface};
    }}
    QToolButton#FilterMenu::menu-indicator {{ image: none; width: 0; }}
    QTabWidget#SettingsTabs {{ background: {t.surface}; }}
    QTabWidget#SettingsTabs::tab-bar {{ alignment: center; }}
    QTabWidget#SettingsTabs::pane {{ border: none; border-top: 1px solid {t.border}; top: -1px; }}
    QPushButton#SectionHeader {{
        text-align: left; font-size: 11px; font-weight: 600; color: {t.text_muted};
        background: {t.surface_alt}; border: none; border-radius: 6px; padding: 7px 8px; margin-top: 6px;
    }}
    QPushButton#SectionHeader:hover {{ color: {t.text}; }}
    QDoubleSpinBox#SliderNumber {{
        background: transparent; color: {t.text_muted}; border: 1px solid transparent;
        border-radius: 4px; padding: 0 4px;
    }}
    QDoubleSpinBox#SliderNumber:hover, QDoubleSpinBox#SliderNumber:focus {{
        background: {t.field_bg}; color: {t.text}; border: 1px solid {t.border};
    }}
    QPushButton#Chip {{
        background: {t.chip_bg}; color: {t.chip_text}; font-size: 12px; border: none;
        border-radius: 12px; padding: 4px 10px;
    }}
    QPushButton#ClearAll {{
        color: {t.text_faint}; font-size: 12px; border: none; background: transparent;
        text-decoration: underline; padding: 4px 2px;
    }}

    QDialog, QMessageBox {{ background: {t.canvas}; color: {t.text}; }}
    QLabel {{ color: {t.text}; }}
    QTableView, QTableWidget, QListWidget, QListView, QTreeView {{
        background: {t.surface}; alternate-background-color: {t.surface_alt}; color: {t.text};
        gridline-color: {t.border}; border: 1px solid {t.border}; border-radius: 8px;
        selection-background-color: {t.selection}; selection-color: {t.chip_text}; outline: 0;
    }}
    QListWidget::item, QListView::item {{ padding: 6px 4px; border-radius: 4px; }}
    QListWidget::item:hover, QListView::item:hover, QTableView::item:hover {{ background: {t.field_bg}; color: {t.text}; }}
    QListWidget::item:selected, QListView::item:selected {{ background: {t.selection}; color: {t.chip_text}; }}
    QTableView::item:selected, QTreeView::item:selected {{ background: {_tint(t.accent, 0.22)}; color: {t.text}; }}
    QTableView::item:selected:!active, QTreeView::item:selected:!active {{ background: {_tint(t.accent, 0.12)}; color: {t.text}; }}
    QHeaderView::section {{
        background: {t.surface_alt}; color: {t.text_muted}; border: none;
        border-bottom: 1px solid {t.border}; padding: 6px 8px; font-weight: 600;
    }}
    QTableCornerButton::section {{ background: {t.surface_alt}; border: none; }}
    QPushButton {{
        background: {t.surface}; color: {t.text}; border: 1px solid {t.border};
        border-radius: 6px; padding: 7px 14px; min-height: 18px;
    }}
    QPushButton:hover {{ background: {t.field_bg}; border-color: {t.text_faint}; }}
    QPushButton:focus {{ border-color: {t.accent}; }}
    QPushButton:disabled {{ color: {t.text_faint}; border-color: {t.border}; }}
    QPushButton:flat {{ border: none; background: transparent; }}
    QToolButton#MenuButton {{
        background: {t.surface}; color: {t.text}; border: 1px solid {t.border}; border-radius: 6px;
        padding: 6px 10px; min-height: 20px;
    }}
    QToolButton#MenuButton:hover {{ border-color: {t.accent}; }}
    QToolButton#MenuButton::menu-indicator {{ image: none; width: 0px; }}
    QFrame#CreateCard {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 10px; }}
    QFrame#CreateCard:hover, QFrame#CreateCard:focus {{ border-color: {t.accent}; }}
    QFrame#CreateCard QLabel {{ border: none; background: transparent; }}
    QLabel#CreatePreview {{ background: {t.surface_alt}; border: 1px solid {t.border}; border-radius: 8px; }}
    QPushButton#Primary {{
        background: {t.chip_bg}; color: {t.chip_text}; border: 1px solid {t.chip_bg};
        font-weight: 600; padding: 9px 18px;
    }}
    QPushButton#Primary:hover {{ background: {t.accent}; border-color: {t.accent}; color: {t.accent_text}; }}
    QPushButton#Primary:disabled {{ background: {t.field_bg}; border-color: {t.border}; color: {t.text_muted}; }}
    QComboBox {{
        background: {t.surface}; color: {t.text}; border: 1px solid {t.border};
        border-radius: 6px; padding: 5px 8px; min-height: 20px;
    }}
    QComboBox:focus {{ border-color: {t.accent}; }}
    QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 22px;
        border: none; background: transparent; }}
    QComboBox::down-arrow {{ image: url({down}); width: 12px; height: 12px; }}
    QComboBox#Sort::drop-down {{ width: 18px; }}
    QComboBox QAbstractItemView {{ background: {t.surface}; color: {t.text};
        selection-background-color: {t.field_bg}; selection-color: {t.text}; }}
    QSpinBox, QDoubleSpinBox {{
        background: {t.surface}; color: {t.text}; border: 1px solid {t.border};
        border-radius: 6px; padding: 5px 4px 5px 8px; min-height: 20px;
    }}
    QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {t.accent}; }}
    QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right;
        width: 20px; border: none; background: transparent; }}
    QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-origin: border;
        subcontrol-position: bottom right; width: 20px; border: none; background: transparent; }}
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({up}); width: 10px; height: 10px; }}
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({down}); width: 10px; height: 10px; }}
    QSpinBox::up-button:hover, QSpinBox::down-button:hover, QDoubleSpinBox::up-button:hover,
    QDoubleSpinBox::down-button:hover {{ background: {t.field_bg}; }}
    QProgressBar {{ border: 1px solid {t.border}; border-radius: 4px; background: {t.surface_alt};
        color: {t.text}; text-align: center; }}
    QProgressBar::chunk {{ background: {t.rating}; border-radius: 3px; }}
    QSplitter::handle {{ background: {t.canvas}; }}
    QSlider::groove:horizontal {{ height: 4px; background: {t.border}; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {t.text_muted}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ background: {t.surface}; border: 2px solid {t.text_muted}; width: 12px;
        height: 12px; margin: -6px 0; border-radius: 8px; }}
    QSlider::handle:horizontal:hover {{ border-color: {t.accent}; }}
    QStatusBar {{ background: {t.surface}; color: {t.text_muted}; border-top: 1px solid {t.border}; min-height: 32px; }}
    QStatusBar::item {{ border: none; }}
    QStatusBar QLabel {{ color: {t.text_muted}; font-size: 13px; padding: 0 6px; }}
    QStatusBar QLabel#ScanStep {{ color: {t.text}; font-weight: 600; }}
    QStatusBar QLabel#LibraryState {{ color: {t.text}; }}
    QProgressBar#ScanProgress {{ background: {t.field_bg}; border: none; border-radius: 3px; }}
    QProgressBar#ScanProgress::chunk {{ background: {t.accent}; border-radius: 3px; }}
    QScrollBar:vertical {{ background: transparent; width: 12px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {t.border}; border-radius: 5px; min-height: 32px; margin: 2px; }}
    QScrollBar::handle:vertical:hover {{ background: {t.text_faint}; }}
    QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 0; }}
    QScrollBar::handle:horizontal {{ background: {t.border}; border-radius: 5px; min-width: 32px; margin: 2px; }}
    QScrollBar#GridScroll[active="true"] {{ width: 20px; }}
    QScrollBar#GridScroll[active="true"]::handle:vertical {{ background: {t.text_faint}; border-radius: 8px; margin: 2px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    QTabWidget::pane {{ border: none; background: {t.canvas}; }}
    QTabBar {{ background: {t.surface}; }}
    QTabBar::tab {{
        background: transparent; color: {t.text_muted}; padding: 10px 14px; margin: 0 2px;
        border: none; border-bottom: 2px solid transparent; font-size: 13px;
    }}
    QTabBar::tab:hover {{ color: {t.text}; }}
    QTabBar::tab:selected {{ color: {t.text}; border-bottom: 2px solid {t.accent}; font-weight: 600; }}

    QScrollArea#SettingsScroll, QWidget#SettingsPage {{ background: {t.canvas}; }}
    QFrame#Card {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 10px; }}
    QFrame#Card QLabel {{ background: transparent; }}
    QLabel#SectionTitle {{ font-size: 15px; font-weight: 700; }}
    QLabel#SubTitle {{ font-weight: 600; padding-top: 6px; }}
    QLabel#Help {{ color: {t.text_muted}; font-size: 12px; }}
    QLabel#EmptyNote {{ color: {t.text_muted}; font-size: 14px; }}
    QLabel#Example {{ color: {t.text_muted}; font-size: 12px; font-family: Consolas, monospace; }}
    QLabel#Error {{ color: {red}; font-size: 12px; }}
    QLabel#ThemeSwatch {{ border: 1px solid {t.border}; border-radius: 6px; }}
    QWidget#DetailPanel {{ background: {t.surface}; }}
    QLabel#ScrubBubble {{ background: {t.chip_bg}; color: {t.chip_text}; border-radius: 12px;
        padding: 4px 12px; font-weight: 600; }}
    QFrame#HoverCard {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 8px; }}
    QLabel#HoverTitle {{ font-weight: 600; background: transparent; }}
    QLabel#HoverLines {{ color: {t.text_muted}; font-size: 12px; background: transparent; }}
    QPushButton#StarButton {{ border: none; background: transparent; font-size: 22px; padding: 0 2px; min-height: 0; }}
    QPushButton#FlagButton {{ padding: 3px 10px; min-height: 0; border-radius: 11px; }}
    QPushButton#LabelButton {{ padding: 0; min-height: 0; min-width: 0; }}
    QPushButton#FlagButton:checked {{ background: {t.chip_bg}; color: {t.chip_text}; border-color: {t.chip_bg}; }}
    QLineEdit {{
        background: {t.surface}; color: {t.text}; border: 1px solid {t.border};
        border-radius: 6px; padding: 5px 8px; min-height: 22px;
    }}
    QLineEdit:focus {{ border-color: {t.accent}; }}
    QLineEdit#PathField {{ background: {t.surface_alt}; }}
    QCheckBox, QRadioButton {{ color: {t.text}; background: transparent; spacing: 8px; padding: 2px 0; }}
    QCheckBox::indicator, QRadioButton::indicator, QAbstractItemView::indicator {{
        width: 16px; height: 16px; border: 1.5px solid {t.text_muted}; background: {t.surface};
    }}
    QCheckBox::indicator:hover, QRadioButton::indicator:hover, QAbstractItemView::indicator:hover {{
        border-color: {t.accent};
    }}
    QCheckBox::indicator, QAbstractItemView::indicator {{ border-radius: 4px; }}
    QRadioButton::indicator {{ border-radius: 9px; }}
    QCheckBox::indicator:checked, QAbstractItemView::indicator:checked {{
        background: {t.chip_bg}; border-color: {t.chip_bg};
        image: url({check}); }}
    QRadioButton::indicator:checked {{
        border-color: {t.chip_bg};
        background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
                                    stop:0 {t.chip_bg}, stop:0.45 {t.chip_bg},
                                    stop:0.55 {t.surface}, stop:1 {t.surface});
    }}
    QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{ background: {t.surface_alt}; }}
    """

