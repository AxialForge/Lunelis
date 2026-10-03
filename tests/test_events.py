"""Phase 2, step 1: events - the model, suggestions from folder names and
capture-time gaps, {event} in storage templates, named imports."""
import os
from datetime import date, datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from lunelis.catalog.schema import open_catalog  # noqa: E402
from lunelis.events import model, suggest  # noqa: E402
from lunelis.events.suggest import parse_folder_name  # noqa: E402
from lunelis.importing.templates import DEFAULT_TEMPLATE, PRESETS, Context, render  # noqa: E402
from lunelis.ui.library import Filter, LibraryIndex  # noqa: E402


class Lib:
    """A catalog with files and capture times, no real photos needed."""

    def __init__(self, tmp_path):
        self.conn = open_catalog(tmp_path / "catalog.db")
        self.tmp = tmp_path
        self.roots = {}

    def root(self, name: str) -> int:
        if name not in self.roots:
            path = self.tmp / name
            path.mkdir()
            self.roots[name] = self.conn.execute("INSERT INTO roots (path) VALUES (?)", (str(path),)).lastrowid
        return self.roots[name]

    def add(self, root: str, rel: str, taken: datetime | None, n: int = 1, step=timedelta(minutes=1)) -> list[int]:
        ids = []
        folder, _, stem = rel.rpartition("/")
        for i in range(n):
            name = f"{stem}{i:04d}.jpg"
            path = f"{folder}/{name}" if folder else name
            fid = self.conn.execute(
                "INSERT INTO files (root_id, rel_path, filename, ext, size_bytes, mtime) VALUES (?, ?, ?, 'jpg', ?, ?)",
                (self.root(root), path, name, 1000 + len(ids), "2026-01-01T00:00:00Z")).lastrowid
            self.conn.execute("INSERT INTO exif (file_id, captured_at) VALUES (?, ?)",
                              (fid, (taken + step * i).isoformat() if taken else None))
            ids.append(fid)
        self.conn.commit()
        return ids


@pytest.fixture
def lib(tmp_path):
    lb = Lib(tmp_path)
    yield lb
    lb.conn.close()


# --- folder names -----------------------------------------------------------------------

@pytest.mark.parametrize("folder, expected", [
    ("6-19-2026 Air Show", (date(2026, 6, 19), "Air Show")),
    ("9-6-2021_Air_Show", (date(2021, 9, 6), "Air Show")),
    ("2019-07-04 Fireworks", (date(2019, 7, 4), "Fireworks")),
    ("20190704_Fireworks", (date(2019, 7, 4), "Fireworks")),
    ("8-23-2025 -- 8-25-2025 summer vaca 2025", (date(2025, 8, 23), "summer vaca 2025")),
    ("6-20-2024 Myrtle Beach (2)", (date(2024, 6, 20), "Myrtle Beach")),
    ("Disney World 2017", (None, "Disney World 2017")),
    ("New York City day 3", (None, "New York City")),
    ("3-14-2026", (date(2026, 3, 14), None)),
    # workflow folders, not outings
    ("100MSDCF", (None, None)), ("JPEG Files", (None, None)), ("JPEG (.JPG)", (None, None)),
    ("Timelaps", (None, None)), ("t=laps", (None, None)), ("TL2", (None, None)),
    ("timelaps 10-20-2024", (None, None)), ("2024 Edits", (None, None)), ("After Shoot edits", (None, None)),
    ("2024 Album", (None, None)), ("Folder 1", (None, None)), ("Set_1", (None, None)),
    ("original-4-10-2021", (None, None)), ("Raw_October_13_2019", (None, None)), ("merge_1_r", (None, None)),
    ("image sequance-1-13-2021-1", (None, None)), ("9-1-2025-p", (date(2025, 9, 1), None)),
    ("Aug 21", (None, None)), ("Photos from 2019", (None, None)), ("JPEG\uf028", (None, None)),
])
def test_folder_names(folder, expected):
    assert parse_folder_name(folder) == expected


# --- the model ------------------------------------------------------------------------------

def test_events_hold_photos_and_their_date_range(lib):
    a = lib.add("P", "x/a", datetime(2026, 6, 17, 22), n=3)
    b = lib.add("P", "x/b", datetime(2026, 6, 19, 9), n=2)
    eid = model.create(lib.conn, "  Trip  ", a + b)
    e = model.get(lib.conn, eid)
    assert (e.name, e.photos, e.start_at[:10], e.end_at[:10]) == ("Trip", 5, "2026-06-17", "2026-06-19")
    assert e.dates() == "Jun 17 - 19, 2026"

    # A photo is in one event at a time: adding it elsewhere moves it.
    other = model.create(lib.conn, "Night shoot", a[:1])
    assert model.events_of(lib.conn, a) == {a[0]: other, a[1]: eid, a[2]: eid}
    # Emptying an event removes it; the photos are untouched.
    model.remove_files(lib.conn, a[:1])
    assert model.get(lib.conn, other) is None
    model.delete(lib.conn, eid)
    assert lib.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 5
    with pytest.raises(ValueError):
        model.create(lib.conn, "   ", b)


def test_date_range_text():
    t = model.date_range_text
    assert t("2026-06-19T10:00:00", "2026-06-19T18:00:00") == "Jun 19, 2026"
    assert t("2026-06-30T10:00:00", "2026-07-02T18:00:00") == "Jun 30 - Jul 2, 2026"
    assert t("2025-12-30T10:00:00", "2026-01-02T18:00:00") == "Dec 30, 2025 - Jan 2, 2026"
    assert t(None, None) == "No date"


def test_library_filters_by_event(lib):
    a = lib.add("P", "x/a", datetime(2026, 6, 17), n=3)
    lib.add("P", "x/b", datetime(2026, 6, 18), n=2)
    eid = model.create(lib.conn, "Trip", a)
    idx = LibraryIndex()
    idx.load(lib.conn, "date_desc", Filter(event_id=eid, event_name="Trip"))
    assert sorted(r[0] for r in idx.rows) == a
    assert Filter(event_id=eid, event_name="x") == Filter(event_id=eid)   # the name is only for the chip


# --- suggestions ------------------------------------------------------------------------------

def test_folder_suggestions_follow_the_real_library_patterns(lib):
    # A trip in day folders, copied to the old pool too.
    for d in (17, 18, 19, 20):
        lib.add("new", f"2024/6-{d}-2024 Myrtle Beach/m", datetime(2024, 6, d, 10), n=5)
        lib.add("old", f"2024 Album/6-{d}-2024 Myrtle Beach/m", datetime(2024, 6, d, 10), n=5)
    # Day subfolders inside a trip belong to the trip; a stray older subfolder doesn't.
    lib.add("new", "2022/8-16-2022 Las Vegas Vaca/Aug 17 grand canyon tour/g", datetime(2022, 8, 17, 9), n=10)
    lib.add("new", "2022/8-16-2022 Las Vegas Vaca/Clips/c", datetime(2022, 8, 16, 20), n=3)
    stray = lib.add("new", "2022/8-16-2022 Las Vegas Vaca/Last vaca/l", datetime(2022, 5, 18, 9), n=4)
    # Workflow folders aren't events.
    lib.add("new", "2024/7-17-2024/Timelaps/t", datetime(2024, 7, 17, 20), n=10)
    # A words-only album spanning years isn't one either.
    lib.add("old", "Family/f", datetime(2015, 1, 1), n=3, step=timedelta(days=400))

    r = suggest.suggest(lib.conn, gap_hours=18, min_photos=5)
    folders = {s.name: s for s in r.suggestions if s.kind == "folder"}
    assert set(folders) == {"Myrtle Beach", "Las Vegas Vaca"}
    mb = folders["Myrtle Beach"]
    assert (len(mb.file_ids), len(mb.folders), mb.dates()) == (40, 8, "Jun 17 - 20, 2024")
    assert len(folders["Las Vegas Vaca"].file_ids) == 13 and not set(stray) & set(folders["Las Vegas Vaca"].file_ids)
    # The timelapse night and the stray May photos are left for the capture-time pass.
    gaps = [s for s in r.suggestions if s.kind == "gap"]
    assert {len(s.file_ids) for s in gaps} == {10}                 # timelapse (stray has only 4 < 5)


def test_accepting_and_dismissing_suggestions(lib):
    ids = lib.add("P", "2026/6-19-2026 Air Show/a", datetime(2026, 6, 19, 9), n=4)
    lib.add("P", "2026/3-14-2026/d", datetime(2026, 3, 14, 9), n=40)
    r = suggest.suggest(lib.conn, min_photos=30)
    folder = next(s for s in r.suggestions if s.kind == "folder")
    gap = next(s for s in r.suggestions if s.kind == "gap")
    assert gap.name == "Mar 14, 2026"

    eid = suggest.accept(lib.conn, folder, "Cleveland Air Show")
    assert model.get(lib.conn, eid).source == "folder"
    assert sorted(model.events_of(lib.conn, ids)) == ids
    suggest.dismiss(lib.conn, [gap.key])
    assert suggest.suggest(lib.conn, min_photos=30).suggestions == []   # both handled, neither comes back


# --- templates and named imports ------------------------------------------------------------------

def test_event_token_and_start_date_filing():
    t, start = datetime(2026, 6, 19), datetime(2026, 6, 17, 22)
    ev = PRESETS["Year \\ Event (else the date)"]
    assert render(ev, Context(t)) == "2026\\2026-06-19"
    assert render(ev, Context(t, event="Air Show", event_start=start)) == "2026\\Air Show"
    # A trip's later days - and its undated clips - file under its start date.
    assert render(DEFAULT_TEMPLATE, Context(t, import_name="Trip", event="Trip", event_start=start)) == \
        "2026\\6-17-2026 Trip"
    assert render(DEFAULT_TEMPLATE, Context(None, import_name="Trip", event="Trip", event_start=start)) == \
        "2026\\6-17-2026 Trip"


def test_named_imports_become_events_once_cataloged(lib):
    ids = lib.add("Library", "2026/6-19-2026 Air Show/DSC", datetime(2026, 6, 19, 9), n=3)
    dest = str(lib.tmp / "Library")
    imp = lib.conn.execute("INSERT INTO imports (source, template, destination, state, name)"
                           " VALUES ('F:\\', ?, ?, 'done', 'Air Show')", (DEFAULT_TEMPLATE, dest)).lastrowid
    for fid in ids:
        rel = lib.conn.execute("SELECT rel_path FROM files WHERE id = ?", (fid,)).fetchone()[0]
        lib.conn.execute("INSERT INTO import_items (import_id, source_rel, size, mtime, dest_path, state)"
                         " VALUES (?, 'x', 1, 0, ?, 'placed')", (imp, os.path.join(dest, *rel.split("/"))))
    lib.conn.commit()
    assert model.link_imports(lib.conn) == 3
    (e,) = model.all_events(lib.conn)
    assert (e.name, e.photos, e.source) == ("Air Show", 3, "import")
    assert model.link_imports(lib.conn) == 0                           # once only


# --- the page ---------------------------------------------------------------------------------------

def test_events_page_creates_ticked_suggestions(lib):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from lunelis.ui.events_view import EventsView
    lib.add("P", "2026/6-19-2026 Air Show/a", datetime(2026, 6, 19, 9), n=4)
    view = EventsView(lib.conn)
    view._found(suggest.suggest(lib.conn))
    assert view.sug.rowCount() == 1
    view.sug.item(0, 1).setText("Cleveland Air Show")                  # renamed before creating
    view._tick("folder")
    view._thread = object()                                             # don't start the re-search thread
    view._create()
    assert [e.name for e in model.all_events(lib.conn)] == ["Cleveland Air Show"]
    assert view.table.rowCount() == 1 and view.table.item(0, 2).text() == "4"
    assert view.table.item(0, 3).text() == "Folder name"
    view._thread = None
