"""0.55: the page changes from the 10 October 2026 backlog's "UI changes" list
(2-4, 6, 11, 14-18, 22-25, 32, 34-38) - one header on every page, one filled
button, empty tables that say why, and the per-page tidying."""
import time

import pytest
from PIL import Image
from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtWidgets import QApplication, QPushButton, QStyleOptionViewItem

from lunelis.catalog.schema import open_catalog
from lunelis.importers.scan import add_root, scan_root

PAGES = ("Albums", "Tags", "Events", "Import", "Migrate", "Duplicates", "Damaged files", "Backups", "Quarantine",
         "Map", "On this day", "Stats", "Library status", "Sensor dust", "Create", "People", "Timelapses",
         "Google Takeout", "Review your shoot")


def _app():
    return QApplication.instance() or QApplication([])


def _pump(done=lambda: False, seconds: float = 20.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QApplication.processEvents()
        if done():
            return True
        time.sleep(0.01)
    return False


def _settle(page) -> None:
    """Background loads and worker threads must be over before a page goes (CLAUDE.md: exit 127)."""
    if hasattr(page, "bg"):
        page.bg.wait()
    _pump(lambda: getattr(page, "_thread", None) is None)
    from PySide6.QtCore import QThreadPool
    QThreadPool.globalInstance().waitForDone(5000)
    QApplication.processEvents()


@pytest.fixture
def lib(tmp_path):
    _app()
    folder = tmp_path / "L"
    folder.mkdir()
    for i in range(4):
        Image.new("RGB", (60, 40), (i * 50, 30, 10)).save(folder / f"p{i}.jpg")
    conn = open_catalog(tmp_path / "c.db")
    rid = add_root(conn, folder)
    scan_root(conn, rid)
    conn.commit()
    yield conn, [r[0] for r in conn.execute("SELECT id FROM files ORDER BY id")]
    conn.close()


@pytest.fixture
def window(tmp_path):
    _app()
    from lunelis.ui import main_window as mw
    w = mw.MainWindow()
    w.resize(1280, 800)
    w.show()
    yield w
    for i in range(w.pages.count()):
        _settle(w.pages.widget(i))
    if hasattr(w, "bg"):
        w.bg.wait()
    w._quitting = True
    w.close()


# --- 2 and 3: one header, one filled button -------------------------------------------------------

def test_every_page_has_the_banded_header_and_one_filled_button_at_most(window):
    from lunelis.ui.page_header import PageHeader
    w = window
    for name in PAGES:
        w.open_page(name)
        _pump(seconds=0.05)
        page = w.pages.currentWidget()
        heads = [h for h in page.findChildren(PageHeader) if h.isVisible()]
        assert len(heads) == 1, name
        filled = [b.text() for b in page.findChildren(QPushButton) if b.objectName() == "Primary" and b.isVisible()]
        assert len(filled) <= 1, (name, filled)
    # The Create tools: each is a page of its own, with the way back before the title.
    w.open_page("Create")
    for key, tool in w.create_page.tools.items():
        w.create_page.open_tool(key)
        _pump(seconds=0.05)
        head = next(h for h in tool.findChildren(PageHeader) if h.isVisible())
        assert head.title_label.text() == tool.title_text
        assert head.row.itemAt(0).widget().text().startswith("‹"), key
        filled = [b for b in tool.findChildren(QPushButton) if b.objectName() == "Primary" and b.isVisible()]
        assert len(filled) == 1, key
    w.create_page.home()


def test_the_tags_toggles_are_segments_and_the_header_steps_aside_for_suggestions(window):
    w = window
    w.open_page("Tags")
    page = w.pages.currentWidget()
    page = page if hasattr(page, "view_group") else w.tags_page
    assert all(b.objectName() == "Segment" for b in page.view_group.buttons())
    assert page.new_b.objectName() == "Primary" and not page.new_b.isHidden()
    page.show_suggestions()
    assert page.new_b.isHidden() and page.search.isHidden()
    assert page.review.accept_b.objectName() == "Primary"
    page.tabs.setCurrentIndex(0)
    assert not page.new_b.isHidden()


# --- 4 and 32: dialogs ------------------------------------------------------------------------------

def test_dialogs_fill_the_confirming_button_and_name_it(lib):
    conn, _ = lib
    from lunelis.dupes import manage
    from lunelis.ui.backups_view import NewBackupDialog
    from lunelis.ui.quarantine_view import ConfirmEmpty
    d = NewBackupDialog(conn)
    assert d.create_b.text() == "Create backup" and d.create_b.objectName() == "Primary" and d.create_b.isDefault()
    assert d.sources.objectName() == "CheckList"            # rows of a normal height
    d.deleteLater()
    c = ConfirmEmpty(manage.entries(conn))
    assert c.go.text() == "Empty quarantine" and c.go.objectName() == "Danger"
    c.deleteLater()


# --- 6: empty tables say why ------------------------------------------------------------------------

def test_empty_tables_show_a_line_instead_of_a_blank_box(lib):
    conn, _ = lib
    from lunelis.ui.damaged_view import DamagedView
    from lunelis.ui.import_view import ImportView
    from lunelis.ui.migrate_view import MigrateView
    from lunelis.ui.near_view import NearView
    imp = ImportView(conn)
    assert imp.table_box.showing_message and "filed into" in imp.table_box.note.text()
    imp.table.setRowCount(1)
    imp._table_note()
    assert not imp.table_box.showing_message
    mig = MigrateView(conn)
    mig.bg.wait()
    assert mig.folders_box.showing_message and mig.notes_box.showing_message
    dmg = DamagedView(conn)
    dmg.refresh()
    assert dmg.table_box.showing_message and dmg.detail_box.showing_message
    near = NearView(conn)
    near.refresh()
    assert near.list_box.showing_message and near.detail_box.showing_message
    for page in (imp, mig, dmg, near):
        _settle(page)
        page.deleteLater()


# --- 11: the Import form is one row ending in the filled Import -------------------------------------

def test_the_import_form_is_one_aligned_row_ending_in_import(lib):
    conn, _ = lib
    from lunelis.ui.import_view import ImportView
    page = ImportView(conn)
    page.resize(1100, 700)
    page.show()
    _pump(seconds=0.1)
    try:
        def middle(w) -> int:
            return w.mapTo(page, w.rect().center()).y()
        row = [page.dest, page.template, page.name, page.autopilot_cb, page.go]
        assert max(middle(w) for w in row) - min(middle(w) for w in row) <= 3
        xs = [w.mapTo(page, w.rect().topLeft()).x() for w in row]
        assert xs == sorted(xs)                                    # ...in that order, Import last
        assert page.go.objectName() == "Primary" and page.go.text() == "Import"
        filled = [b for b in page.findChildren(QPushButton) if b.objectName() == "Primary"]
        assert filled == [page.go]                                 # "Review it" is no longer filled
    finally:
        _settle(page)
        page.close()
        page.deleteLater()


# --- 14 and 16: Duplicates ----------------------------------------------------------------------------

def test_duplicates_summary_is_one_bold_line_with_a_quiet_second(lib):
    conn, _ = lib
    from lunelis.ui.dupes_view import DuplicatesView, Group
    page = DuplicatesView(conn)
    page.refresh()
    page.bg.wait()
    assert page.summary.text() == "No duplicates found" and "Find duplicates" in page.summary_detail.text()
    m = [(1, 1, "C:\\A", "x/p.jpg", None), (2, 1, "C:\\A", "y/p.jpg", None)]
    page._show_groups([Group(1, True, 1024, list(m), 1), Group(2, False, 2048, list(m), 1)])
    assert page.summary.text().startswith("2 files have copies · ") and page.summary.text().endswith("can be freed")
    assert "1 verified" in page.summary_detail.text() and "1 likely" in page.summary_detail.text()
    assert page.summary.objectName() == "SectionTitle" and page.summary_detail.objectName() == "Help"
    # The verb matches the page it sends things to.
    assert page.quarantine_b.text() == "Move all verified extras to Quarantine…"
    assert page.quarantine_b.objectName() != "Primary"
    _settle(page)
    page.deleteLater()


def test_near_duplicates_lists_a_picture_a_name_and_a_source_not_a_path():
    from lunelis.ui import near_view
    assert near_view._source("D:\\Photos\\2024\\") == "2024"
    assert near_view._folder("2024/06/IMG_1.jpg") == "2024 › 06" and near_view._folder("IMG_1.jpg") == "(top folder)"
    assert near_view._name("2024/06/IMG_1.jpg") == "IMG_1.jpg"


def test_the_near_duplicates_tab_is_a_list_beside_its_detail(lib):
    conn, _ = lib
    from PySide6.QtWidgets import QSplitter
    from lunelis.ui.near_view import NearView
    view = NearView(conn)
    split = view.findChild(QSplitter)
    assert split.orientation() == Qt.Orientation.Horizontal
    assert view.list.horizontalHeaderItem(0).text() == "Photo" and view.list.iconSize().width() == near_thumb()
    assert view.group_b.text() == "Move ticked copies to Quarantine"
    view.deleteLater()


def near_thumb() -> int:
    from lunelis.ui import near_view
    return near_view.THUMB


# --- 15, 17 and 18: Create tools and tick boxes ---------------------------------------------------------

def test_make_sits_under_the_options_and_the_form_is_a_centred_card(lib):
    conn, ids = lib
    from PySide6.QtWidgets import QFrame
    from lunelis.settings import Settings
    from lunelis.ui.create_page import FORM_W, CreatePage
    Settings(conn).set("create_step_by_step", False)
    page = CreatePage(conn)
    page.resize(1300, 800)
    page.show()
    try:
        page.set_library_context(ids, page._view_filter, None)
        page.open_tool("batch")
        _pump(seconds=0.2)
        tool = page.tools["batch"]
        card = next(c for c in tool.body_w.findChildren(QFrame) if c.objectName() == "Card")
        card_at = QRect(card.mapTo(tool, card.rect().topLeft()), card.size())
        make_at = tool.make_b.mapTo(tool, tool.make_b.rect().topLeft())
        assert card.width() <= FORM_W
        assert abs(card_at.center().x() - tool.width() // 2) <= 2            # in the middle of the page
        assert 0 <= make_at.y() - card_at.bottom() <= 24                      # Make straight under the form...
        assert abs(make_at.x() - card_at.left()) <= 2                         # ...at its left edge
        # Step by step keeps its four steps; Make follows whatever is on screen.
        tool.steps_cb.setChecked(True)
        assert tool.make_b.isHidden() and tool._actions_in is tool._foot_slot
        tool.go_step(1)
        assert tool._actions_in is tool._side_slot and not tool.next_b.isHidden()
        tool.go_step(3)
        assert not tool.make_b.isHidden() and tool._actions_in is tool._foot_slot
        tool.steps_cb.setChecked(False)
        # A tool with a preview keeps its options at the left, Make under them.
        anim = page.tools["animation"]
        assert anim._preview and anim._actions_in is anim._side_slot
    finally:
        Settings(conn).set("create_step_by_step", False)
        for t in page.tools.values():
            _settle(t)
        page.close()
        page.deleteLater()


def test_the_photo_strip_fits_its_thumbnails_and_ticks_sit_inside_them(lib):
    conn, ids = lib
    from PySide6.QtGui import QIcon, QPixmap
    from lunelis.ui.create_page import CELL, THUMB, PhotoPicker
    from lunelis.ui.scene_review import TICK, TickThumb
    picker = PhotoPicker(conn)
    picker.set_library_context(ids, picker._view_filter, None)
    picker.choose_default()
    try:
        strip = picker.strip
        assert isinstance(strip.itemDelegate(), TickThumb)
        assert strip.height() <= THUMB + 8 + 2 * strip.frameWidth() + 14      # no taller than a thumbnail needs
        assert strip.count() == 4 and strip.item(0).sizeHint() == CELL
        pm = QPixmap(90, 60)
        pm.fill(Qt.GlobalColor.gray)
        strip.item(0).setIcon(QIcon(pm))
        d, index = strip.itemDelegate(), strip.model().index(0, 0)
        opt = QStyleOptionViewItem()
        opt.rect = QRect(0, 0, CELL.width(), CELL.height())
        pic, tick = d.picture_rect(opt, index), d.tick_rect(opt, index)
        assert opt.rect.contains(pic) and pic.contains(tick) and tick.size() == QSize(TICK, TICK)
        # A click on the box unticks the photo; a click elsewhere on it doesn't.
        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QMouseEvent

        def click(at) -> None:
            for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
                d.editorEvent(QMouseEvent(kind, QPointF(at), QPointF(at), Qt.MouseButton.LeftButton,
                                          Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier),
                              strip.model(), opt, index)
        click(pic.bottomRight())
        assert strip.item(0).checkState() == Qt.CheckState.Checked
        click(tick.center())
        assert strip.item(0).checkState() == Qt.CheckState.Unchecked and len(picker.ids()) == 3
    finally:
        picker._thumb_timer.stop()
        picker.deleteLater()


def test_scene_suggestions_put_the_tick_in_the_picture_and_the_percentage_under_it(lib):
    conn, _ = lib
    from lunelis.ui.scene_review import SceneReview, TickThumb
    view = SceneReview(conn)
    d = view.photos.itemDelegate()
    assert isinstance(d, TickThumb) and d.caption and not d.click_ticks      # the list flips a photo on any click
    view.bg.wait()
    view.deleteLater()


# --- 22 and 23: Albums --------------------------------------------------------------------------------

def test_albums_offer_the_same_dashed_tile_for_albums_and_smart_albums(lib):
    conn, _ = lib
    from lunelis.ui.albums_view import AlbumsView, NewAlbumTile
    page = AlbumsView(conn)
    try:
        page.refresh()
        assert _pump(lambda: page._thread is None and page._smart is not None)
        first = page.smart.widgets[0]
        assert isinstance(first, NewAlbumTile) and first.text == "New smart album"
        assert isinstance(page.yours.widgets[0], NewAlbumTile) and page.yours.widgets[0].text == "New album"
        assert page.smart_help.text().startswith("None yet.")              # an empty-state line
        assert not [b for b in page.findChildren(QPushButton) if b.text().startswith("New smart album")]
        # A tile with no cover paints its name and "Nothing here yet" rather than a
        # blank grey square: its middle is no longer one flat colour.
        from lunelis.albums.model import Album
        from lunelis.ui.albums_view import TILE, AlbumTile
        tile = AlbumTile(Album("auto", "videos", "Videos", 0, None), page.thumbs)
        img = tile.grab().toImage()
        middle = {img.pixel(x, y) for x in range(20, TILE - 20, 2) for y in range(TILE // 2 - 24, TILE // 2 + 36, 2)}
        assert len(middle) > 1
        tile.deleteLater()
    finally:
        _pump(lambda: page._thread is None)
        page.deleteLater()


# --- 24 and 25: Library status ---------------------------------------------------------------------------

def test_library_status_folds_the_steps_when_idle_and_rows_are_links(lib):
    conn, ids = lib
    from lunelis.albums import archive
    from lunelis.ui import theme
    from lunelis.ui.main_window import SCAN_STEPS
    from lunelis.ui.status_view import StatusView
    archive.archive(conn, ids[:1])
    conn.commit()
    page = StatusView(conn, SCAN_STEPS)
    try:
        assert page.steps_card.isHidden() and not page.idle_card.isHidden()
        page.refresh()
        page.bg.wait()
        assert page.idle_line.text().startswith("Idle · last scan ")
        page.step(0, 0, 0)                                 # a scan: the nine steps come back
        assert not page.steps_card.isHidden() and page.idle_card.isHidden()
        page.finished(cancelled=False)
        page.bg.wait()
        assert page.steps_card.isHidden() and page.idle_line.text().startswith("Idle · last scan ")
        rows = [b for b in page.findChildren(QPushButton) if b.objectName() == "GlanceRow"]
        archived = next(b for b in rows if b.accessibleName().startswith("Archived"))
        went = []
        page.open_page.connect(went.append)
        archived.click()
        assert went == ["Albums"]
        # A problem figure that isn't nought takes the warning colour.
        conn.execute("UPDATE files SET missing_since = '2026-01-01' WHERE id = ?", (ids[1],))
        conn.commit()
        page.refresh()
        page.bg.wait()
        figures = {page.glance.itemAtPosition(r, c).widget().text(): page.glance.itemAtPosition(r, c + 1).widget().text()
                   for r in range(page.glance.rowCount()) for c in (0, 2) if page.glance.itemAtPosition(r, c)}
        assert theme.current().rating in figures["Missing (not found at the last scan)"]
        assert figures["Damaged"] == "0"
    finally:
        _settle(page)
        page.deleteLater()


# --- 35 and 36: Quarantine and Sensor dust -----------------------------------------------------------------

def test_quarantine_says_what_is_due_as_text_and_names_its_buttons(lib):
    conn, _ = lib
    from lunelis.ui.quarantine_view import QuarantineView
    page = QuarantineView(conn)
    try:
        page.refresh()
        assert _pump(lambda: page._thread is None)
        assert page.due_b.text() == "Empty expired…" and not page.due_b.isEnabled()
        assert page.due_text.text() == "Nothing past its time"
        assert page.empty_all_b.objectName() == "Danger"
        assert page.header.title_label.text() == "Quarantine"
    finally:
        _settle(page)
        page.deleteLater()


def test_sensor_dust_labels_the_camera_list_and_keeps_heal_for_later(lib):
    conn, _ = lib
    from PySide6.QtWidgets import QLabel
    from lunelis.ui.dust_view import DustView
    page = DustView(conn)
    try:
        page.refresh()
        assert "Camera:" in [lab.text() for lab in page.findChildren(QLabel)]
        assert page.camera.count() == 0 and page.camera.placeholderText() == "No cameras yet"
        assert not page.camera.isEnabled() and not page.look_b.isEnabled()
        assert not page.heal_b.isEnabled() and page.heal_b.objectName() != "Primary"
    finally:
        _settle(page)
        page.deleteLater()


# --- 37 and 38: On this day, Map, Stats --------------------------------------------------------------------

def test_on_this_day_centres_its_empty_line_and_offers_the_wider_look(lib):
    conn, _ = lib
    from datetime import date
    from lunelis.ui.calendar_view import CalendarView
    page = CalendarView(conn)
    try:
        page.set_day(date(1999, 2, 3))
        page.bg.wait()
        note = page.rows.itemAt(0).widget()
        from PySide6.QtWidgets import QLabel
        line = note.findChild(QLabel)
        assert "No photos from February 3" in line.text() and line.alignment() & Qt.AlignmentFlag.AlignHCenter
        assert page.empty_week_b.text() == "Within 3 days"
        page.empty_week_b.click()
        page.bg.wait()
        assert page.week_cb.isChecked() and page.empty_week_b is None      # already the wider look: no button
    finally:
        _settle(page)
        page.deleteLater()


def test_the_map_names_its_grid_lines():
    from lunelis.ui.map_view import degrees
    assert degrees(40, "N", "S") == "40° N" and degrees(-30, "N", "S") == "30° S"
    assert degrees(-81.5, "E", "W", 1) == "81.5° W" and degrees(0, "E", "W") == "0°"
    assert degrees(190, "E", "W") == "170° W"                # past the date line


def test_stats_fills_the_page_and_explains_keepers_under_their_tile(lib):
    conn, _ = lib
    from PySide6.QtWidgets import QLabel
    from lunelis.ui.stats_view import KEEPER_NOTE, PAGE_MAX, StatsView
    page = StatsView(conn)
    page.resize(1000, 700)
    page.show()
    try:
        page.refresh()
        page.bg.wait()
        _pump(seconds=0.1)
        assert page.by_chart.width() > 700                    # was pinned to about 560
        page.resize(1600, 700)
        _pump(seconds=0.1)
        assert page.by_chart.width() <= PAGE_MAX
        cards = [page.glance.itemAt(i).widget() for i in range(page.glance.count())]
        keepers = next(c for c in cards if "Keepers" in [lab.text() for lab in c.findChildren(QLabel)])
        assert KEEPER_NOTE in [lab.text() for lab in keepers.findChildren(QLabel)]
        others = [c for c in cards if c is not keepers]
        assert not any(KEEPER_NOTE in [lab.text() for lab in c.findChildren(QLabel)] for c in others)
    finally:
        _settle(page)
        page.close()
        page.deleteLater()


# --- the stylesheet block ------------------------------------------------------------------------------------

def test_the_danger_and_glance_styles_exist_in_every_theme():
    _app()
    from lunelis.ui import theme
    for t in theme.THEMES.values():
        qss = theme.stylesheet(t)
        red = dict(t.labels)["Red"]
        assert f"QPushButton#Danger {{ color: {red};" in qss
        assert "QPushButton#GlanceRow" in qss
