"""ui/background.py: slow reads off the GUI thread, coalesced, never delivered late."""
import sqlite3
import threading

from PySide6.QtWidgets import QApplication, QWidget

from lunelis.catalog.schema import open_catalog
from lunelis.ui.background import Background


def _app():
    return QApplication.instance() or QApplication([])


def test_runs_on_a_worker_with_its_own_connection(tmp_path):
    _app()
    conn = open_catalog(tmp_path / "c.db")
    page = QWidget()
    bg = Background(page, conn)
    got = []
    gui = threading.get_ident()
    bg.run("k", lambda c: (threading.get_ident(), c is not conn, c.execute("SELECT 1").fetchone()[0]), got.append)
    assert bg.busy("k")
    bg.wait()
    worker, other_conn, one = got[0]
    assert worker != gui and other_conn and one == 1
    assert not bg.busy()


def test_a_burst_of_requests_costs_two_runs_and_shows_the_newest(tmp_path):
    _app()
    conn = open_catalog(tmp_path / "c.db")
    page = QWidget()
    bg = Background(page, conn)
    gate = threading.Event()
    calls, shown = [], []

    def slow(c, n):
        calls.append(n)
        gate.wait(5)
        return n

    for n in range(5):
        bg.run("k", lambda c, n=n: slow(c, n), shown.append)
    gate.set()
    bg.wait()
    assert calls == [0, 4] and shown == [4]


def test_errors_go_to_the_error_handler_and_nothing_arrives_after_close(tmp_path):
    _app()
    conn = open_catalog(tmp_path / "c.db")
    page = QWidget()
    bg = Background(page, conn)
    errors, shown = [], []
    bg.run("bad", lambda c: 1 / 0, shown.append, error=errors.append)
    bg.wait()
    assert shown == [] and isinstance(errors[0], ZeroDivisionError)

    bg.run("late", lambda c: "figures", shown.append)
    page._closed = True                       # the window closed the catalog meanwhile
    bg.wait()
    assert shown == []


def test_an_in_memory_catalog_runs_inline():
    _app()
    conn = sqlite3.connect(":memory:")
    page = QWidget()
    bg = Background(page, conn)
    got = []
    bg.run("k", lambda c: c is conn, got.append)
    assert got == [True] and not bg.busy()


def test_unless_closed_drops_a_result_after_the_window_closed():
    from lunelis.ui.background import unless_closed
    _app()

    class Page(QWidget):
        def __init__(self):
            super().__init__()
            self.got = []

        @unless_closed
        def _done(self, result):
            self.got.append(result)

    page = Page()
    page._done(1)
    page._closed = True
    page._done(2)
    assert page.got == [1]
