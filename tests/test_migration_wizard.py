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


def test_the_release_step_says_still_copying_instead_of_unaccounted(lib, monkeypatch):
    """0.55: opened mid-copy, step 9 listed every file not copied yet as UNACCOUNTED."""
    QApplication.instance() or QApplication([])
    conn, tmp, a, b, target, ra, rb, ids = lib
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    from lunelis.jobs import engine
    from lunelis.migrate import status
    w = mw.MigrationWizard(conn)
    for i in range(w.src_list.count()):
        w.src_list.item(i).setCheckState(Qt.CheckState.Checked)
    w._next()
    w.target.setText(str(target))
    for _ in range(5):
        w._next()
        w.wait()
    w._next()
    w.wait()
    p = status.read(conn, w.migration_id)
    assert p.copying and p.files_total > 0 and p.files_done == 0 and p.percent == 0
    assert w.run_panel.bar.value() == 0 and f"of {p.files_total:,} files" in w.run_panel.numbers.text()
    w.go(8)                                                   # the copy hasn't run yet
    w.wait()
    text = w.report_view.toPlainText()
    assert "still running" in text and "UNACCOUNTED" not in text
    assert not w.release_b.isEnabled() and not w.check_b.isEnabled()
    assert "until the copy has finished" in w.release_why.text()
    job = conn.execute("SELECT job_id FROM migrations WHERE id = ?", (w.migration_id,)).fetchone()[0]
    engine.run_job(conn, job)
    w._refresh() if w.isVisible() else w._show_release()      # what the timer does
    w.wait()
    p = status.read(conn, w.migration_id)
    assert p.state == "done" and p.files_done == p.files_total and p.percent == 100
    assert w.release_panel.bar.value() == 100 and "finished" in w.release_panel.headline.text()
    assert "Every source file is accounted for" in w.report_view.toPlainText()
    assert "In the new Library" in w.report_view.toPlainText()
    assert w.release_b.isEnabled() and w.release_why.text() == ""
    w.close()


def test_the_report_puts_the_answer_first_and_groups_the_rest():
    from lunelis.migrate.logs import Report
    rep = Report(1)
    rep.counts.update({"Copied to the Library": 90, "Left in place - not a photo or video": 7,
                       "An identical copy is in the Library": 3})
    html = mw.report_html(rep)
    assert "Every source file is accounted for" in html and "All 100 files" in html
    rep.unaccounted.append((r"X:\a.jpg", "its copy isn't there"))
    html = mw.report_html(rep)
    assert "1 file needs attention" in html and "100 of 101" in html and "a.jpg" in html
    assert status_time() == ("about a minute left", "about 30 minutes left", "about 2.5 hours left")


def status_time():
    from lunelis.migrate.status import time_left
    return time_left(30), time_left(1800), time_left(9000)
