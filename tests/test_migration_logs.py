"""0.41: before / after inventories, the manifest, and the accounted-for report
that has to be clean before originals are released."""
import csv

import pytest

from lunelis.migrate import execute, logs
from lunelis.migrate.plan import Options, plan
from lunelis.importing.templates import DEFAULT_TEMPLATE
from lunelis.settings import Settings
from test_migrate import _run, lib  # noqa: F401  (fixture)


def rows(p):
    with open(p, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_logs_and_a_clean_report_then_release(lib):
    conn, tmp, a, b, target, ra, rb, ids = lib
    Settings(conn).set("lunelis_folder", str(tmp / "Lunelis"))
    (a / "misc" / "notes.txt").write_text("hello", encoding="utf-8")        # a non-photo leftover
    mid = plan(conn, str(target), DEFAULT_TEMPLATE, Options([ra, rb], keep_sources=True))
    _, out = _run(conn, mid, tmp)
    assert out.state == "done"
    d = tmp / "Lunelis" / "Migration logs" / f"Migration {mid}"
    assert {p.name for p in d.iterdir()} >= {"before.csv", "manifest.csv", "after.csv", "report.csv", "summary.txt"}
    before = rows(d / "before.csv")
    assert any(r["path"] == "misc/notes.txt" for r in before)
    man = rows(d / "manifest.csv")
    assert all(len(r["sha256"]) == 64 for r in man if r["action"] == "move")
    rep = logs.accounted(conn, mid)
    assert rep.clean, rep.text()
    assert rep.counts["Left in place - not a photo or video"] == 1
    assert rep.counts["An identical copy is in the Library"] == 1
    assert rep.counts["Sidecar - travelled with its photo"] == 1
    assert "Every source file is accounted for" in (d / "summary.txt").read_text(encoding="utf-8")
    assert execute.release(conn, mid) > 0


def test_release_is_refused_while_anything_is_unaccounted(lib):
    conn, tmp, a, b, target, ra, rb, ids = lib
    mid = plan(conn, str(target), DEFAULT_TEMPLATE, Options([ra, rb], keep_sources=True))
    _run(conn, mid, tmp)
    # A copy in the Library goes missing after the run.
    dest = conn.execute("SELECT dest_rel FROM migration_items WHERE migration_id = ? AND action = 'move'"
                        " LIMIT 1", (mid,)).fetchone()[0]
    (target / dest).unlink()
    rep = logs.accounted(conn, mid)
    assert not rep.clean and "isn't at" in rep.unaccounted[0][1]
    with pytest.raises(execute.MigrationError):
        execute.release(conn, mid)
    assert conn.execute("SELECT COUNT(*) FROM migration_items WHERE migration_id = ? AND state = 'released'",
                        (mid,)).fetchone()[0] == 0


def test_the_page_shows_the_report(lib, monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox
    QApplication.instance() or QApplication([])
    from lunelis.ui.migrate_view import MigrateView
    conn, tmp, a, b, target, ra, rb, ids = lib
    mid = plan(conn, str(target), DEFAULT_TEMPLATE, Options([ra, rb], keep_sources=True))
    _run(conn, mid, tmp)
    shown = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append((self.text(), self.informativeText())))
    page = MigrateView(conn)
    page.bg.wait()                                     # its own loads finish before the catalog closes
    page.migration_id = mid
    page._show_report(logs.accounted(conn, mid))
    assert shown and "source files checked" in shown[0][0] and "can be released" in shown[0][1]
    page.bg.wait()
