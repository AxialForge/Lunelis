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
