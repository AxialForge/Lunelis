"""0.49: correctness, from the 25-item list."""
from datetime import datetime

from PySide6.QtWidgets import QApplication


def test_import_example_uses_the_cards_own_first_date(tmp_path):
    QApplication.instance() or QApplication([])
    from lunelis.catalog.schema import open_catalog
    from lunelis.ui.import_view import ImportView
    conn = open_catalog(tmp_path / "c.db")
    v = ImportView(conn)
    v.preview = [("DCIM/a.jpg", 1, datetime(2024, 9, 2, 10)), ("DCIM/b.jpg", 1, datetime(2024, 9, 3, 9))]
    v.name.setText("Air Show")
    v._update_example()
    assert "9-2-2024 Air Show" in v.example.text() and "First folder" in v.example.text()
    folders = [v.table.item(i, 0).text() for i in range(v.table.rowCount())]
    assert all("9-2-2024 Air Show" in f for f in folders)          # the table agrees: one event folder
    v.deleteLater()
    conn.close()


def test_a_photo_that_cant_be_decoded_greys_the_edit_panel():
    QApplication.instance() or QApplication([])
    from PySide6.QtWidgets import QAbstractSlider
    from lunelis.ui.develop import DevelopPanel
    p = DevelopPanel()
    p.set_editable(False)
    assert not any(s.isEnabled() for s in p.findChildren(QAbstractSlider))
    p.set_editable(True)
    assert all(s.isEnabled() for s in p.findChildren(QAbstractSlider))
    p.deleteLater()


def test_leap_day_photos_show_on_feb_28_in_other_years():
    from datetime import date
    from lunelis.ui.calendar_view import day_keys
    assert day_keys(date(2026, 2, 28), 0) == ["02-28", "02-29"]
    assert day_keys(date(2028, 2, 28), 0) == ["02-28"]                  # a leap year has its own Feb 29
    assert day_keys(date(2028, 2, 29), 0) == ["02-29"]


def test_event_names_lose_a_month_day_prefix():
    from lunelis.events.suggest import _clean_name
    assert _clean_name("03-02 Ski trip") == "Ski trip"
    assert _clean_name("12-25 Christmas") == "Christmas"
    assert _clean_name("3-2-1 Blastoff") == "3-2-1 Blastoff"            # not a date prefix


def test_a_result_message_survives_the_pages_reload(monkeypatch):
    from lunelis.ui import notice
    n = notice.Notice()
    n.say("Placed 40 photos at Rome")
    assert n.prefix().startswith("Placed 40 photos")
    t = [notice.time.monotonic()]
    monkeypatch.setattr(notice.time, "monotonic", lambda: t[0] + notice.HOLD_S + 1)
    assert n.prefix() == ""                                         # gone after a few seconds


def test_the_library_count_says_when_tiles_hide_files(tmp_path):
    from test_audit_navigation import _window
    w, ids = _window(tmp_path, 4)
    try:
        sid = w.conn.execute("INSERT INTO stacks (kind, cover_file_id, size) VALUES ('burst', ?, 3)", (ids[0],)).lastrowid
        w.conn.executemany("INSERT INTO stack_files (stack_id, file_id, position) VALUES (?, ?, ?)",
                           [(sid, f, n) for n, f in enumerate(ids[:3])])
        w.conn.commit()
        w.reload()
        w._update_count(0)
        assert "files)" in w.count.text() and "as one tile" in w.count.toolTip()
    finally:
        w._quitting = True
        w.close()


def test_database_is_locked_is_a_plain_message_not_a_crash():
    import sqlite3
    from lunelis.ui.main_window import catalog_busy
    assert catalog_busy(sqlite3.OperationalError, sqlite3.OperationalError("database is locked"))
    assert not catalog_busy(sqlite3.OperationalError, sqlite3.OperationalError("no such table: x"))
    assert not catalog_busy(ValueError, ValueError("locked"))
