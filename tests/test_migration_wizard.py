"""0.43: the migration wizard - nine steps, nothing moves before step 8."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from lunelis.migrate import layout
from lunelis.ui import migration_wizard as mw
from lunelis.settings import Settings
from test_migrate import all_files, lib  # noqa: F401  (fixture)


def test_leftovers_and_the_layout_preview(lib, tmp_path):
    conn, tmp, a, b, target, ra, rb, ids = lib
    (a / "misc" / "notes.txt").write_text("x", encoding="utf-8")
    kinds = {k: n for k, n, _ in mw.leftovers([str(a), str(b)])}
    assert kinds == {"TXT": 1, "Sidecar (XMP)": 1}
    rows = mw.layout_preview(conn, [ra], layout.LayoutOptions())
    assert any(dst.replace(chr(92), "/").startswith("Library/Photos and Videos/2024/6-19-2024/Photos/") for _, dst in rows)
    assert any(dst.replace(chr(92), "/").startswith("Library/Undated/Photos/") for _, dst in rows)


def test_the_wizard_plans_runs_and_releases(lib, monkeypatch):
    QApplication.instance() or QApplication([])
    conn, tmp, a, b, target, ra, rb, ids = lib
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    from lunelis.jobs import engine
    w = mw.MigrationWizard(conn)
    assert w.pages.currentIndex() == 0
    w._next()
    assert w.pages.currentIndex() == 0 and "Tick" in w.status.text()        # nothing ticked
    for i in range(w.src_list.count()):
        w.src_list.item(i).setCheckState(Qt.CheckState.Checked)
    w._next()
    w.target.setText(str(target))
    for _ in range(5):                                                       # target -> dry run
        w._next()
        w.wait()
    assert w.pages.currentIndex() == 6 and w.summary is not None
    assert not all_files(target)                                             # nothing moved yet
    w._next()                                                                # Start the copy
    w.wait()
    assert w.pages.currentIndex() == 7
    assert Settings(conn).get("lunelis_folder").endswith("Lunelis")
    job = conn.execute("SELECT job_id FROM migrations WHERE id = ?", (w.migration_id,)).fetchone()[0]
    engine.run_job(conn, job)
    w.go(8)
    w.wait()
    assert "accounted for" in w.report_view.toPlainText() and w.release_b.isEnabled()
    w._do_release()
    w.wait()
    assert "moved to the Trash" in w.status.text()
    assert any("Trash" in p for p in all_files(target / "Lunelis"))
    w.close()
