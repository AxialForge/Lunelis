"""0.56.1: the Jobs panel colours a job's state - running, queued, finished, failed."""
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from lunelis.catalog.schema import open_catalog
from lunelis.ui import jobs, theme


def _contrast(a: str, b: str) -> float:
    def lum(c):
        v = [x / 255 for x in QColor(c).getRgb()[:3]]
        v = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in v]
        return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_every_state_has_a_readable_colour_in_every_theme():
    for t in (theme.GRAPHITE, theme.MIDNIGHT):
        seen = {s: jobs.state_colour(s, t) for s in jobs.STATE_TEXT}
        assert all(seen.values())
        assert len({seen[s] for s in ("running", "queued", "done", "failed")}) == 4
        for s, c in seen.items():
            assert _contrast(c, t.surface) >= 3.0, (t, s, c)
    assert jobs.state_colour("something new") is None


def test_the_state_cell_is_coloured_and_keeps_its_word(tmp_path):
    QApplication.instance() or QApplication([])
    conn = open_catalog(tmp_path / "c.db")
    for state in ("running", "queued", "done", "failed"):
        conn.execute("INSERT INTO jobs (kind, title, state) VALUES ('full_hash', ?, ?)", (state, state))
    conn.commit()
    d = jobs.JobsDialog(conn, jobs.JobRunner())
    d._timer.stop()
    got = {}
    for r in range(d.table.rowCount()):
        cell = d.table.item(r, 1)
        got[cell.text().split()[-1]] = cell.foreground().color().name()
    assert set(got) == {"Running", "Queued", "Done", "Failed"}
    assert got["Running"] == jobs.state_colour("running") and got["Failed"] == jobs.state_colour("failed")
    assert len(set(got.values())) == 4
    d.close()
