"""0.55 UI changes, stream B: the main window, the photo view, the Edit panel
and the video view (backlog 2026-10-10, UI changes 1, 3, 6, 8, 9, 10, 12, 13,
26, 33, 39)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PIL import Image  # noqa: E402
from PySide6.QtCore import QObject, QThread  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from lunelis.catalog.schema import open_catalog  # noqa: E402
from lunelis.importers.scan import add_root, scan_root  # noqa: E402
from lunelis.settings import Settings  # noqa: E402
from lunelis.ui import theme  # noqa: E402


def _pump(n: int = 20) -> None:
    for _ in range(n):
        QApplication.processEvents()


@pytest.fixture
def window(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    from lunelis.ui.main_window import MainWindow
    root = tmp_path / "Photos"
    root.mkdir()
    for i in range(4):
        Image.effect_noise((96, 64), 40 + i).convert("RGB").save(root / f"IMG_{i:04d}.jpg", "JPEG")
    w = MainWindow()
    rid = add_root(w.conn, root)
    scan_root(w.conn, rid)
    w.reload()
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    ids = [r[0] for r in w.conn.execute("SELECT id FROM files WHERE root_id = ? ORDER BY filename", (rid,))]
    yield w, ids
    if hasattr(w, "bg"):
        w.bg.wait()                      # a background load must not outlive the closed catalog
    w.apply_theme("graphite")
    w._quitting = True
    w.close()


# --- 1: the sidebar highlights the right entry ------------------------------------------------

def test_a_sub_page_highlights_its_parent_in_the_sidebar(window):
    w, _ = window
    w.open_page("Tags")
    w.show_page("Events")                                  # how Albums > event suggestions gets there
    assert w._nav["Albums"].isChecked() and not w._nav["Tags"].isChecked()
    w.open_page("Sensor dust")
    w.open_page("Review your shoot")                       # the status-bar link, from any page
    assert w._nav["Import"].isChecked() and not w._nav["Sensor dust"].isChecked()
    w.open_page("Library")
    assert w._nav["Library"].isChecked()


def test_the_sidebar_scrolls_to_the_page_on_screen(window):
    from lunelis.ui.main_window import NAV
    w, _ = window
    assert "Quarantine" in dict(NAV)["Keep safe"]
    w.resize(1440, 900)                                    # too short for every entry, not short enough to tighten
    w.show()
    _pump()
    w.open_page("Quarantine")
    _pump(40)
    b = w._nav["Quarantine"]
    assert b.isChecked() and not b.visibleRegion().isEmpty()
    w.open_page("Library")
    _pump(40)
    assert not w._nav["Library"].visibleRegion().isEmpty()


# --- 33: the Photo menu in groups ----------------------------------------------------------

def test_the_photo_menu_is_grouped_and_keeps_every_action(window):
    from lunelis.ui.main_window import PHOTO_MENU_GROUPS
    w, _ = window
    groups, current = [], []
    for a in w.photo_menu.actions():
        if a.isSeparator():
            groups.append(tuple(current))
            current = []
        else:
            current.append(a.text())
    groups.append(tuple(current))
    assert tuple(groups) == PHOTO_MENU_GROUPS              # the table's order, and nothing left over
    assert ("&Rating", "&Label", "&Flag") in groups
    texts = [t for g in groups for t in g]
    assert "Mark unnamed &faces as strangers" in texts and not any("are strangers" in t for t in texts)
    subs = {a.text() for a in w.photo_menu.actions() if a.menu()}
    assert subs == {"&Rating", "&Label", "&Flag", "&Tags", "&Album", "&Event", "E&dit", "&Stack", "C&reate",
                    "&Merge", "Se&lect"}
    keys = {a.text(): a.shortcut().toString() for a in w.photo_menu.actions() if not a.isSeparator()}
    assert keys["&Undo"] == "Ctrl+Z" and keys["C&ull full screen…"] == "Ctrl+K"
    assert keys["Arc&hive"] == "Ctrl+Shift+H" and keys["E&xport…"] == "Ctrl+Shift+E"
    assert keys["Rotate &left"] == "Ctrl+[" and keys["Set &location on the map…"] == "Ctrl+Shift+L"


# --- 12: a filter pill that is on ----------------------------------------------------------

def test_a_filter_pill_that_is_on_is_filled_and_says_its_value(window):
    from lunelis.ui.main_window import Filter, MainWindow
    w, _ = window
    pills = {attr: b for attr, (b, _name) in w._pills.items()}
    assert set(pills) == {"min_stars", "label", "flag", "backup", "tag"}
    assert pills["min_stars"].text() == "Rating ▾" and not pills["min_stars"].property("on")
    w.set_filter(Filter(min_stars=4, label="Red", flag="pick", backup="none"))
    assert pills["min_stars"].text() == "Rating: 4+ ▾" and pills["min_stars"].property("on") is True
    assert pills["label"].text() == "Label: Red ▾" and pills["flag"].text() == "Flag: Picks ▾"
    assert pills["backup"].text() == "Backup: Not backed up ▾"
    assert pills["tag"].text() == "Tag ▾" and not pills["tag"].property("on")
    # The pills say it: no chip repeats them. Clear all is still there.
    assert w.chips.count() == 0 and not w.clear_all.isHidden()
    w.set_filter(Filter(min_stars=5))
    assert pills["min_stars"].text() == "Rating: 5 ▾" and not pills["label"].property("on")
    w.set_filter(Filter())
    assert all(not b.property("on") for b in pills.values()) and pills["label"].text() == "Label ▾"
    assert MainWindow.pill_values(Filter(tag="Places|Lake")) == {"tag": "Places › Lake"}
    for t in theme.THEMES.values():                        # filled with the theme's accent, in every theme
        css = theme.stylesheet(t)
        rule = css.split('QToolButton#FilterMenu[on="true"]:hover {', 1)[1].split("}", 1)[0]
        assert f"background: {t.accent};" in rule and f"color: {t.accent_text};" in rule


# --- 13: Ask is a mode of the one search box --------------------------------------------------

def test_ask_is_a_mode_of_the_search_box_not_a_second_row(window):
    w, _ = window
    w.show()
    _pump()
    bar = w.toolbar
    assert bar.isAncestorOf(w.ask_bar) and w.ask_bar.box.parentWidget() is bar
    search_at = w.search.geometry()
    assert w.search.isVisible() and not w.ask_bar.box.isVisible() and not w.ask_b.isChecked()
    w.ask_b.click()                                        # the toggle beside the box
    _pump()
    assert w.ask_b.isChecked() and w.ask_bar.isVisible() and w.ask_bar.box.isVisible()
    assert not w.search.isVisible()                        # one box on screen
    assert w.ask_bar.box.geometry().left() == search_at.left()             # where the search box was
    assert w.ask_bar.box.placeholderText().startswith("Ask")
    w.ask_bar.say("3 best matches")
    w.ask_b.click()                                        # and off again
    _pump()
    assert w.search.isVisible() and not w.ask_bar.box.isVisible() and not w.ask_bar.isVisible()
    assert w.ask_bar.note.text() == ""
    w.open_ask()                                           # Ctrl+Shift+F
    assert w.ask_b.isChecked() and not w.search.isVisible()
    w.ask_bar.close_bar()                                  # Esc in the box
    assert not w.ask_b.isChecked() and w.search.isVisible()


# --- 26: the status bar ---------------------------------------------------------------------

def test_the_catalog_snapshot_says_saved_not_backed_up(window, tmp_path):
    w, _ = window
    for _ in range(200):                                   # a snapshot of its own may be running
        if getattr(w, "_backup_thread", None) is None:
            break
        _pump(5)
    made = tmp_path / "catalog-copy.db"
    made.write_bytes(b"x" * 2_000_000)
    w._backup_thread, w._backup_worker = QThread(w), QObject()
    w._on_backup_done(made)
    assert w.status.text() == "Catalog saved (2 MB)"
    w._backup_thread, w._backup_worker = QThread(w), QObject()
    w._on_backup_done(OSError("disk full"))
    assert "backed up" not in w.status.text() and "disk full" in w.status.text()


def test_the_damaged_files_link_is_in_the_warning_colour(window):
    from lunelis.ui.main_window import warning_colour
    w, _ = window
    for name in ("graphite", "midnight", "high_contrast"):
        w.apply_theme(name)
        t = theme.current()
        warn = warning_colour()
        assert warn != t.accent and warn.startswith("#")
        w.library_state.set_links([("Last scanned today", "Library status"), ("1 damaged file", "Damaged files", True)])
        html = w.library_state.text()
        plain_link, damaged = html.split(" · ")
        assert t.accent in plain_link and warn not in plain_link
        assert warn in damaged and 'href="page:Damaged files"' in damaged
    w.apply_theme("graphite")                              # a theme switch recolours both links
    html = w.library_state.text()
    assert warning_colour() in html and theme.current().accent in html
    assert w.library_state.toolTip() == "Last scanned today · 1 damaged file"


# --- 9, 10, 3: the photo view ---------------------------------------------------------------

def test_the_photo_bar_leaves_the_details_to_the_info_panel(window):
    w, ids = window
    w.open_detail(ids[0])
    d = w.detail
    d._summary_line = "Aug 19, 2025  ·  Sony"
    d._show_summary()
    assert d.name.text() == "IMG_0000.jpg"
    assert d.summary.text() == ""                          # the Info panel beside it says all this
    assert d.name.toolTip() == "Aug 19, 2025  ·  Sony"     # ...and it's one hover away
    d.set_editing(True)                                    # the Edit panel covers Info: the line is back
    assert d.side.currentWidget() is d.develop and d.summary.text() == "Aug 19, 2025  ·  Sony"
    d.set_editing(False)
    assert d.summary.text() == ""
    w.close_detail()


def test_the_tool_rail_has_a_divider_and_a_selected_state(window):
    w, ids = window
    d = w.detail
    lay = d.rail_divider.parentWidget().layout()
    order = [lay.indexOf(d.tool_b[n]) for n in ("zones", "histogram")]
    assert order[0] < lay.indexOf(d.rail_divider) < order[1]
    assert all(b.toolTip() and b.isCheckable() for b in d.tool_b.values())
    w.open_detail(ids[0])
    d.toggle_tool("clipping")
    assert d.tool_b["clipping"].isChecked() and not d.tool_b["zones"].isChecked()
    d.toggle_tool("clipping")
    for t in theme.THEMES.values():
        # The rail is on the viewer's dark background in every theme: the icon is a light grey there.
        off, on = d._rail_colours(t)
        assert off == t.sidebar_muted and on == t.accent_text
        css = theme.stylesheet(t)
        assert f"background: {t.accent}; border: 1px solid {t.sidebar_text};" in css
        assert "QFrame#RailDivider" in css
    w.close_detail()


def test_one_filled_button_and_accent_only_on_toggles(window):
    from PySide6.QtWidgets import QPushButton
    w, ids = window
    # The photo view: Edit and Faces are toggles (accent when on); Done is the one filled button while editing.
    d = w.detail
    assert d.edit_b.objectName() == "Toggle" and d.faces_b.objectName() == "Toggle"
    assert d.develop.done_b.objectName() == "Primary"
    assert d.develop.crop_b.objectName() == "Toggle" and d.develop.before_b.objectName() == "Toggle"
    # The Edit page: the shared header, Export all the only filled button (its Done is hidden).
    from lunelis.ui.page_header import PageHeader
    page = w.edit_page
    assert isinstance(page.bar, PageHeader) and page.bar.title_label.text() == "Edit"
    filled = [b for b in page.bar.findChildren(QPushButton) if b.objectName() == "Primary"]
    assert filled == [page.export_b] and page.view.develop.done_b.isHidden()
    w.grid.selected = set(ids[:2])
    w.open_page("Edit")
    assert page.bar._count_text == "2 photos"              # shown beside the title while the bar is unfolded
    # The Library toolbar: nothing filled; Ask is a toggle.
    assert not [b for b in w.toolbar.findChildren(QPushButton) if b.objectName() == "Primary"]
    assert w.ask_b.isCheckable() and w.ask_b.objectName() == "Ask"
    for t in theme.THEMES.values():
        css = theme.stylesheet(t)
        rule = css.split("QPushButton#Ask:checked:hover, QPushButton#Toggle:checked:hover {", 1)[1].split("}", 1)[0]
        assert f"background: {t.accent};" in rule and f"color: {t.accent_text};" in rule
    w.open_page("Library")


# --- 8, 6: the Edit panel -------------------------------------------------------------------

@pytest.fixture
def panel(tmp_path):
    QApplication.instance() or QApplication([])
    from lunelis.ui.develop import DevelopPanel
    conn = open_catalog(tmp_path / "panel.db")
    p = DevelopPanel()
    yield p, conn
    p.deleteLater()
    conn.close()


def test_the_edit_panel_opens_with_only_light_and_color_unfolded(panel):
    p, conn = panel
    assert conn.execute("SELECT 1 FROM settings WHERE key = 'edit_sections_closed'").fetchone() is None
    p.restore_sections(conn)
    opened = {title for title, s in p.sections.items() if s.header.isChecked()}
    assert opened == {"Light", "Color"}
    assert {"Filters", "Crop & rotate", "Retouch", "Masks"} <= set(p.sections) - opened
    # Nothing was saved by opening it...
    assert conn.execute("SELECT 1 FROM settings WHERE key = 'edit_sections_closed'").fetchone() is None
    # ...and the first fold saves the panel as it is on screen, so the rest stay shut.
    p.sections["Filters"].header.click()
    saved = set(Settings(conn).get("edit_sections_closed"))
    assert saved == set(p.sections) - {"Light", "Color", "Filters"}
    p.restore_sections(conn)
    assert {t for t, s in p.sections.items() if s.header.isChecked()} == {"Light", "Color", "Filters"}


def test_a_saved_choice_of_sections_is_left_alone(panel):
    p, conn = panel
    Settings(conn).set("edit_sections_closed", ["Light"])                  # they folded Light, opened the rest
    p.restore_sections(conn)
    assert {t for t, s in p.sections.items() if not s.header.isChecked()} == {"Light"}
    Settings(conn).set("edit_sections_closed", [])                         # Settings > "Unfold every section"
    p.restore_sections(conn)
    assert all(s.header.isChecked() for s in p.sections.values())


def test_the_masks_list_says_why_it_is_empty(panel):
    from lunelis.edit.masks import Mask
    p, _conn = panel
    assert p.mask_stack.showing_message and "No masks yet" in p.mask_stack.note.text()
    assert p.mask_stack.height() == 92
    m = Mask(kind="radial", shape=(0.5, 0.5, 0.2, 0.2, 0.5))
    p.show_masks((m,), 0)
    assert not p.mask_stack.showing_message and p.mask_list.count() == 1
    p.show_masks((), -1)
    assert p.mask_stack.showing_message and p.mask_list.count() == 0


# --- 39: the video view's trim row ---------------------------------------------------------

def test_the_trim_row_is_labelled_and_its_buttons_show_the_times(tmp_path):
    QApplication.instance() or QApplication([])
    from lunelis.ui.video_player import VideoPlayer
    conn = open_catalog(tmp_path / "video.db")
    p = VideoPlayer(conn)                                  # never shown: a shown player hangs offscreen at exit
    try:
        row = p.in_b.parentWidget().layout()
        assert p.trim_l.text() == "Trim:" and row.indexOf(p.trim_l) == 0 and row.indexOf(p.in_b) == 1
        assert (p.in_b.text(), p.out_b.text()) == ("Start here", "End here")
        assert p.clear_in_b.toolTip() and p.clear_out_b.toolTip()
        p._marks_dragged(3_000, None)
        assert (p.in_b.text(), p.out_b.text()) == ("Start: 0:03", "End here")
        p._marks_dragged(3_000, 65_000)
        assert (p.in_b.text(), p.out_b.text()) == ("Start: 0:03", "End: 1:05")
        p._marks_dragged(None, None)                       # both cleared: the plain names again
        assert (p.in_b.text(), p.out_b.text()) == ("Start here", "End here")
    finally:
        p.stop()
        p.deleteLater()
        conn.close()
        # A test run that ENDS on a test that made a player never exits (the
        # process hangs after "passed" - tests/test_slog.py's player test does
        # the same when run alone). Deleting the player here and giving Qt's
        # media threads a moment to wind down lets the run finish.
        from PySide6.QtCore import QCoreApplication, QEvent
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        import time
        end = time.monotonic() + 0.5
        while time.monotonic() < end:
            QApplication.processEvents()
            time.sleep(0.02)
