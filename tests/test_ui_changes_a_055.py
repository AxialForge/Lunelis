"""0.55: the shared part of the UI list - one page header, tables, lists,
disabled buttons, muted text, Settings tabs and forms, dialogs' main button."""
import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QDialogButtonBox, QFormLayout, QLabel, QPushButton, QScrollArea, QTableWidget,
)

from lunelis.catalog.schema import open_catalog  # noqa: E402
from lunelis.settings import Settings  # noqa: E402
from lunelis.ui import theme  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def conn(tmp_path):
    c = open_catalog(tmp_path / "catalog.db")
    yield c
    c.close()


@pytest.fixture
def settings(app, conn):
    from lunelis.ui.settings_view import SettingsView
    view = SettingsView(conn)
    yield view
    if hasattr(view, "bg"):
        view.bg.wait()
    view.close()


def _tab_of(view, widget) -> str:
    for i in range(view.tabs.count()):
        if view.tabs.widget(i).isAncestorOf(widget):
            return view.tabs.tabText(i).replace("&&", "&")
    return ""


# --- 2: the one page header ------------------------------------------------------------------

def test_page_header_keeps_its_old_shape(app):
    from lunelis.ui.page_header import BAR_HEIGHT, page_header
    bar, row = page_header("Damaged files")                 # how today's callers use it
    assert bar.objectName() == "Toolbar" and bar.height() == BAR_HEIGHT == 64
    assert bar.title_label.text() == "Damaged files" and bar.title_label.objectName() == "PageTitle"
    assert bar.count_label.isHidden() and bar.description_label.isHidden()
    b = QPushButton("Rescan")
    row.addWidget(b)                                        # lands after the stretch: on the right
    bar.resize(900, 64)
    bar.show()
    QApplication.processEvents()
    assert b.geometry().right() > 800 and bar.title_label.x() < 60
    bar.close()


def test_page_header_count_and_description(app):
    from lunelis.ui.page_header import page_header
    bar, row = page_header("Tags", count="128 tags", description="Words you put on photos.")
    assert row is bar.row
    assert bar.count_label.text() == "128 tags" and bar.count_label.objectName() == "PageCount"
    assert not bar.count_label.isHidden() and not bar.description_label.isHidden()
    assert bar.height() > 64                                # the band grew by the one line
    bar.resize(900, bar.height())
    bar.show()
    QApplication.processEvents()
    # the count sits beside the title, the description below both
    assert 0 < bar.count_label.x() - bar.title_label.geometry().right() < 40
    assert bar.description_label.y() >= bar.title_label.geometry().bottom() - 2
    bar.set_count(129)
    assert bar.count_label.text() == "129"
    bar.set_count(None)
    bar.set_description("")
    assert bar.count_label.isHidden() and bar.description_label.isHidden() and bar.height() == 64
    bar.close()


# --- 5, 19, 20, 21: the stylesheet -------------------------------------------------------------

def _lum(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(int(h[i:i + 2], 16) / 255) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_muted_text_is_readable_in_every_theme(app):
    for t in theme.THEMES.values():
        for fg in (t.text_muted, t.text_faint):
            for bg in (t.surface, t.surface_alt, t.canvas, t.field_bg):
                assert _contrast(fg, bg) >= 4.5, (t.name, fg, bg)
        for fg in (t.sidebar_muted, t.sidebar_footer):
            assert _contrast(fg, t.sidebar_bg) >= 4.5, (t.name, fg)
    # the dark theme's greys went up a step
    assert _contrast(theme.MIDNIGHT.text_faint, theme.MIDNIGHT.surface) > 7
    assert _contrast(theme.MIDNIGHT.sidebar_footer, theme.MIDNIGHT.sidebar_bg) > 7


def test_no_text_under_12_px_in_the_stylesheet(app):
    for t in theme.THEMES.values():
        sizes = [float(m) for m in re.findall(r"font-size:\s*(\d+(?:\.\d+)?)px", theme._stylesheet(t))]
        assert sizes and min(sizes) >= 12, (t.name, min(sizes))


def test_disabled_buttons_look_off_and_do_not_hover(app):
    for t in theme.THEMES.values():
        css = theme.stylesheet(t)
        off = theme.disabled_text(t)
        assert f"QPushButton:disabled, QPushButton:disabled:hover {{\n        color: {off};" in css
        assert "QPushButton#Primary:disabled:hover" in css
        # clearly below the helper-text grey, on both the text and the border
        assert _contrast(off, t.surface) < _contrast(t.text_faint, t.surface) - 1.5, t.name
        assert _contrast(theme.disabled_border(t), t.surface) < _contrast(t.border, t.surface)
    b = QPushButton("Verify")
    b.setStyleSheet(theme.stylesheet(theme.MIDNIGHT))
    b.setEnabled(False)
    b.resize(120, 36)
    b.show()
    QApplication.processEvents()
    px = b.grab().toImage().pixelColor(6, 18)               # inside the button, left of the text
    b.close()
    assert px.name() == theme.MIDNIGHT.surface              # its own background, not the hover grey


def test_lists_share_the_tables_tinted_selection(app):
    from PySide6.QtWidgets import QListWidget
    for t in theme.THEMES.values():
        css = theme.stylesheet(t)
        tint = theme._mix(t.accent, t.surface, 0.22)
        assert f"QListWidget::item:selected, QListView::item:selected {{ background: {tint}; color: {t.text}; }}" in css
        assert f"selection-background-color: {t.selection}" not in css
    t = theme.MIDNIGHT
    lw = QListWidget()
    lw.setStyleSheet(theme.stylesheet(t))
    lw.addItems(["Beach", "Food", "Mountains"])
    lw.resize(300, 160)
    lw.show()
    lw.setCurrentRow(1)
    QApplication.processEvents()
    rect = lw.visualItemRect(lw.item(1))
    px = lw.viewport().grab().toImage().pixelColor(rect.right() - 12, rect.center().y())
    lw.close()
    assert px.lightness() < 110                             # a tint on the dark surface, not the white bar


def test_tables_get_padding_left_headers_and_no_column_lines(app):
    for t in theme.THEMES.values():
        css = theme.stylesheet(t)
        assert "QTableView::item, QTableWidget::item {\n        padding: 0 10px;" in css
        assert f"border-bottom: 1px solid {t.border};" in css
    tb = QTableWidget(2, 2)
    assert tb.showGrid()                                    # Qt's default, before the style is applied
    tb.setStyleSheet(theme.stylesheet(theme.GRAPHITE))
    tb.setHorizontalHeaderLabels(["Name", "Files"])
    tb.show()
    QApplication.processEvents()
    assert not tb.showGrid()
    align = tb.horizontalHeader().defaultAlignment()
    assert align & Qt.AlignmentFlag.AlignLeft and not (align & Qt.AlignmentFlag.AlignHCenter)
    tb.close()


# --- 7, 29: Settings tabs --------------------------------------------------------------------

def test_settings_library_is_two_tabs_and_small_tabs_are_merged(settings):
    sv = settings
    assert sv.TABS == ("General", "Appearance", "Library", "Recognition & autopilot", "Import", "Edit",
                       "Ratings & sidecars", "Duplicates & jobs", "Backups", "Advanced")
    assert [sv.tabs.tabText(i).replace("&&", "&") for i in range(sv.tabs.count())] == list(sv.TABS)
    # every control is still there, under its old attribute name, on the right tab
    where = {
        "roots": "Library", "skipped": "Library", "thumb_info": "Library",
        "scene_auto": "Recognition & autopilot", "scene_job_b": "Recognition & autopilot",
        "faces_auto": "Recognition & autopilot", "faces_threshold": "Recognition & autopilot",
        "places_auto": "Recognition & autopilot", "map_online": "Recognition & autopilot",
        "timelapse_cb": "Recognition & autopilot", "log_look": "Recognition & autopilot",
        "noticed_cb": "Recognition & autopilot",
        "dt_status": "Ratings & sidecars", "dt_exchange": "Ratings & sidecars", "dt_install_b": "Ratings & sidecars",
        "update_existing": "Ratings & sidecars",
        "version_label": "Advanced", "check_b": "Advanced", "auto_update": "Advanced", "data_dir": "Advanced",
    }
    for name, tab in where.items():
        assert _tab_of(sv, getattr(sv, name)) == tab, name
    assert all(_tab_of(sv, cb) == "Recognition & autopilot" for cb in sv.autopilot_stages.values())


def test_show_tab_takes_the_old_names_and_card_names(settings):
    sv = settings

    def now() -> str:
        return sv.tabs.tabText(sv.tabs.currentIndex()).replace("&&", "&")

    for name, tab in (("darktable", "Ratings & sidecars"), ("Updates", "Advanced"), ("Library", "Library"),
                      ("Faces", "Recognition & autopilot"), ("Scene tags", "Recognition & autopilot"),
                      ("Places", "Recognition & autopilot"), ("Autopilot", "Recognition & autopilot"),
                      ("Recognition & autopilot", "Recognition & autopilot"), ("General", "General")):
        sv.show_tab(name)
        assert now() == tab, name
    sv.show_tab("No such tab")                              # ignored, as before
    assert now() == "General"


def test_show_tab_scrolls_to_the_card_it_was_asked_for(settings):
    sv = settings
    sv.resize(1100, 600)
    sv.show()
    QApplication.processEvents()
    sv.show_tab("Faces")
    QApplication.processEvents()
    scroll = sv.tabs.currentWidget()
    assert isinstance(scroll, QScrollArea)
    card = sv._cards["_faces"]
    top = card.mapTo(scroll.viewport(), QPoint(0, 0)).y()
    assert scroll.verticalScrollBar().value() > 0           # the Scene tags card is above it
    assert 0 <= top < 120                                   # Faces starts at the top of the view


# --- 27, 28, 30: Settings forms ----------------------------------------------------------------

def test_a_settings_label_is_centred_on_its_field(settings):
    sv = settings
    sv.resize(1100, 700)
    sv.show()
    sv.show_tab("General")
    QApplication.processEvents()
    field = sv.start_page
    row = field.parentWidget()
    label = next(w for w in row.findChildren(QLabel) if w.text() == "Open Lunelis on")
    centre = lambda w: w.mapTo(row, QPoint(0, 0)).y() + w.height() / 2          # noqa: E731
    assert abs(centre(label) - centre(field)) <= 1.5


def test_card_sub_headings_are_small_uppercase_section_labels(settings):
    sv = settings
    heads = {w.text() for w in sv.findChildren(QLabel) if w.objectName() == "CardSection"}
    assert {"COLOUR", "NEW JOBS START WITH", "REGULAR FILE CHECKS", "SKIPPED FOLDERS",
            "AUTOPILOT AFTER AN IMPORT"} <= heads
    assert not [w for w in sv.findChildren(QLabel) if w.objectName() == "SubTitle"]
    for t in theme.THEMES.values():
        rule = re.search(r"QLabel#CardSection \{(.*?)\}", theme._stylesheet(t), re.S).group(1)
        assert "letter-spacing" in rule and "font-weight: 700" in rule and "padding: 14px" in rule


def test_idle_and_hours_rows_show_only_with_their_choice(settings, conn):
    sv = settings
    idle, start = sv._spins["job_idle_minutes"], sv._spins["job_window_start_hour"]

    def choose(key: str) -> None:
        sv.when.setCurrentIndex(sv.when.findData(key))

    choose("now")
    assert all(w.isHidden() for rows in sv._when_rows.values() for w in rows)
    choose("idle")
    assert not idle.isHidden() and start.isHidden()
    assert Settings(conn).get("job_default_when") == "idle"         # choosing still saves
    choose("window")
    assert idle.isHidden() and not start.isHidden()
    assert all(not w.isHidden() for w in sv._when_rows["window"])   # label, from, to, both boxes, the hint
    assert len(sv._when_rows["window"]) == 6
    # a hidden row keeps its value, and a reload shows the right rows again
    idle.setValue(25)
    Settings(conn).set("job_default_when", "now")
    sv.refresh()
    assert idle.isHidden() and start.isHidden() and Settings(conn).get("job_idle_minutes") == 25


# --- 4, 31, 40: dialogs -----------------------------------------------------------------------

def _is_primary(button, text: str) -> bool:
    return button.objectName() == "Primary" and button.isDefault() and button.text() == text


def test_dialogs_have_one_filled_button_named_after_the_action(app, conn):
    from lunelis.ui.export_dialog import ExportDialog
    from lunelis.ui.jobs import ScopeDialog
    from lunelis.ui.merge_dialog import MergeDialog
    from lunelis.ui.smart_dialog import SmartAlbumDialog
    dialogs = [(ExportDialog(conn, 3), "go", "Export"),
               (ScopeDialog(conn, "Find duplicates"), "go", "Find duplicates"),
               (ScopeDialog(conn, "Check integrity"), "go", "Check integrity"),
               (SmartAlbumDialog(), "save_b", "Create smart album"),
               (SmartAlbumDialog("Keepers", {"match": "all", "rules": []}), "save_b", "Save changes"),
               (MergeDialog(conn, "hdr", 3, "a.arw"), "go", "Choose where to save…")]
    for dlg, attr, text in dialogs:
        assert _is_primary(getattr(dlg, attr), text), text
        filled = [b for b in dlg.findChildren(QPushButton) if b.objectName() == "Primary"]
        assert len(filled) == 1, text
        cancel = dlg.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Cancel)
        assert cancel is not None and cancel.objectName() != "Primary"
        dlg.close()


def test_the_primary_dialog_button_still_accepts(app, conn):
    from lunelis.ui.smart_dialog import SmartAlbumDialog
    dlg = SmartAlbumDialog()
    dlg.name.setText("Low light")
    dlg.save_b.click()
    assert dlg.result_value and dlg.result_value[0] == "Low light"


def test_export_fields_share_one_left_and_one_right_edge(app, conn):
    from lunelis.ui.export_dialog import ExportDialog
    dlg = ExportDialog(conn, 2)
    dlg.resize(620, dlg.sizeHint().height())
    dlg.show()
    QApplication.processEvents()
    form = dlg.findChild(QFormLayout)
    lefts, rights = set(), set()
    for row in range(form.rowCount()):
        field = form.itemAt(row, QFormLayout.ItemRole.FieldRole).widget()
        inner = [field.layout().itemAt(i).widget() for i in range(field.layout().count())] if field.layout() else [field]
        x0 = min(w.mapTo(dlg, QPoint(0, 0)).x() for w in inner)
        x1 = max(w.mapTo(dlg, QPoint(0, 0)).x() + w.width() for w in inner)
        lefts.add(x0)
        if not isinstance(field, QLabel):                   # the hint line is as wide as its text
            rights.add(x1)
        label = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
        lab = label.widget() if label is not None else None
        if lab is not None and lab.text():                  # label centred on its field (27)
            assert abs(lab.geometry().center().y() - field.geometry().center().y()) <= 1, lab.text()
            assert lab.alignment() & Qt.AlignmentFlag.AlignVCenter
    assert len(lefts) == 1 and len(rights) == 1, (lefts, rights)
    dlg.close()


def test_the_tags_dialog_has_a_heading_and_top_padding(app, conn):
    from lunelis.ui.tag_editor import TagDialog
    dlg = TagDialog(conn, [])
    head = dlg.findChild(QLabel, "DialogHeading")
    assert head is not None and head.text() == "Tag photos"
    assert dlg.layout().itemAt(0).widget() is head          # the first line
    assert dlg.layout().contentsMargins().top() >= 16
    assert "font-weight: 700" in re.search(r"QLabel#DialogHeading \{(.*?)\}", theme._stylesheet(), re.S).group(1)
    dlg.close()
