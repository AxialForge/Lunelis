"""v0.15.0: the Edit page - an editing workspace with batch tools."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PIL import Image  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from lunelis.edit import store  # noqa: E402
from lunelis.edit.stack import Stack  # noqa: E402
from lunelis.importers.scan import add_root, scan_root  # noqa: E402
from lunelis.ui.library import Filter, LibraryIndex  # noqa: E402


@pytest.fixture
def window(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    from lunelis.ui.main_window import MainWindow
    root = tmp_path / "Photos"
    root.mkdir()
    for i in range(5):
        Image.effect_noise((96, 64), 40 + i).convert("RGB").save(root / f"IMG_{i:04d}.jpg", "JPEG")
    w = MainWindow()
    scan_root(w.conn, add_root(w.conn, root))
    w.reload()
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    ids = [r[0] for r in w.conn.execute("SELECT id FROM files WHERE rel_path LIKE 'IMG_%' ORDER BY filename")]
    yield w, ids
    w._quitting = True
    w.close()


def test_filter_by_ids(window):
    w, ids = window
    idx = LibraryIndex()
    idx.load(w.conn, None, Filter(ids=tuple(ids[:2])))
    assert {idx.file_id(i) for i in range(len(idx))} == set(ids[:2])


def test_edit_page_works_through_the_selection_and_stays_editing(window):
    w, ids = window
    w.grid.selected = set(ids[1:4])
    w.open_page("Edit")
    page = w.edit_page
    assert page.source.currentData() == "selection" and sorted(page.ids()) == sorted(ids[1:4])
    assert page.view.editing and page.view.edit.active
    page.view.set_editing(False)                          # Done / Esc can't leave the workspace
    assert page.view.editing
    assert page.view.back_b.isHidden() and page.view.edit_b.isHidden()
    page.source.setCurrentIndex(page.source.findData("view"))   # what the library shows: everything
    assert len(page.ids()) == len(ids)
    # Rating keys act on the photo being edited, not the library's selection.
    cur = page.current()
    w.rate(stars=4)
    assert w.conn.execute("SELECT stars FROM ratings WHERE file_id = ?", (cur,)).fetchone()[0] == 4


def test_batch_paste_reset_and_export(window, monkeypatch):
    w, ids = window
    w.grid.selected = set(ids)
    w.open_page("Edit")
    page = w.edit_page
    store.save(w.conn, ids[0], Stack(adjust={"exposure": 0.5}))
    page._copy() if page.current() == ids[0] else w._copy_edit_of(ids[0])
    w._paste_edit_to(page.ids())
    edited = store.edited_ids(w.conn, ids)
    assert set(edited) == set(ids)
    assert all(store.get(w.conn, f).adjust.get("exposure") == 0.5 for f in ids)
    w._reset_edits_of(page.ids())
    assert not store.edited_ids(w.conn, ids)
    seen = []
    monkeypatch.setattr(w, "export_photos", lambda got=None: seen.append(got))
    page.export_all.disconnect()
    page.export_all.connect(w.export_photos)
    page.export_b.click()
    assert sorted(seen[0]) == sorted(ids)


def test_leaving_the_edit_page_saves_the_photo(window):
    w, ids = window
    w.grid.selected = {ids[0]}
    w.open_page("Edit")
    saved = []
    w.edit_page.view.edit.finish = lambda: saved.append(True)
    w.open_page("Library")
    assert saved
