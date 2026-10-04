"""v0.19: one undo / redo history for ratings, tags, albums, events, archive,
stacks and edits."""
import pytest
from PIL import Image

from lunelis.albums import model as albums
from lunelis.catalog.schema import open_catalog
from lunelis.history import History
from lunelis.importers.scan import add_root, scan_root


@pytest.fixture
def lib(tmp_path):
    conn = open_catalog(tmp_path / "c.db")
    root = tmp_path / "Photos"
    root.mkdir()
    for i in range(4):
        Image.new("RGB", (30, 20), (50 * i, 9, 9)).save(root / f"P{i}.jpg")
    rid = add_root(conn, root)
    scan_root(conn, rid)
    ids = [r[0] for r in conn.execute("SELECT id FROM files WHERE root_id = ? ORDER BY filename", (rid,))]
    return conn, ids


def stars(conn, ids):
    got = dict(conn.execute(f"SELECT file_id, stars FROM ratings WHERE file_id IN ({','.join('?' * len(ids))})", ids))
    return [got.get(i, 0) for i in ids]


def test_ratings_undo_and_redo(lib):
    from lunelis.catalog.ratings import set_ratings
    conn, ids = lib
    h = History()
    h.before(conn, "5 stars", "ratings", ids[:2])
    set_ratings(conn, ids[:2], stars=5)
    assert stars(conn, ids[:2]) == [5, 5]
    assert h.undo(conn).label == "5 stars" and stars(conn, ids[:2]) == [0, 0]
    assert h.redo(conn) and stars(conn, ids[:2]) == [5, 5]
    assert h.redo(conn) is None and h.next_undo() == "5 stars"


def test_archive_tags_and_events(lib):
    from lunelis.albums import archive
    from lunelis.events import model as events
    from lunelis.tags import model as tags
    conn, ids = lib
    h = History()
    h.before(conn, "archive", "archive", ids[:2])
    archive.archive(conn, ids[:2])
    tags.add(conn, ids[2:], ["Trips|Vegas"])
    h.before(conn, "tags", "tags", ids[2:])
    tags.add(conn, ids[2:], ["Trips|Ohio"])
    tags.remove(conn, ids[2:], "Trips|Vegas")
    eid = events.create(conn, "Air show", ids[:1]) if hasattr(events, "create") else None
    h.before(conn, "event", "events", ids[1:3])
    events.add_files(conn, eid, ids[1:3])
    h.undo(conn)                                              # the event
    assert events.events_of(conn, ids[1:3]) == {}
    h.undo(conn)                                              # the tags
    assert tags.tags_of(conn, ids[2]) == ["Trips|Vegas"]
    h.undo(conn)                                              # the archive
    assert archive.split(conn, ids[:2])[0] == []
    h.redo(conn)
    assert sorted(archive.split(conn, ids[:2])[0]) == sorted(ids[:2])
    h.redo(conn)
    assert tags.tags_of(conn, ids[2]) == ["Trips|Ohio"]


def test_albums_a_new_one_goes_away_and_comes_back(lib):
    conn, ids = lib
    h = History()
    aid = albums.create(conn, "Best", ids[:2])
    h.record("new album", "album", ids[:2], {"exists": False, "name": None, "inside": set()}, aid)
    h.before(conn, "add", "album", ids[2:], aid)
    albums.add_files(conn, aid, ids[2:])
    h.undo(conn)
    assert next(a.count for a in albums.your_albums(conn) if a.key == str(aid)) == 2
    h.undo(conn)
    assert [a for a in albums.your_albums(conn) if a.key == str(aid)] == []
    h.redo(conn)
    assert next(a.count for a in albums.your_albums(conn) if a.key == str(aid)) == 2


def test_unstack_and_edits(lib):
    from lunelis import stacks
    from lunelis.edit import store
    from lunelis.edit.stack import Stack
    conn, ids = lib
    conn.execute("INSERT INTO stacks (id, kind, size, cover_file_id) VALUES (7, 'burst', 3, ?)", (ids[0],))
    conn.executemany("INSERT INTO stack_files (stack_id, file_id, position) VALUES (7, ?, ?)",
                     [(f, n) for n, f in enumerate(ids[:3])])
    conn.commit()
    h = History()
    h.before(conn, "unstack", "stack", stacks.members(conn, 7), 7)
    stacks.unstack(conn, 7)
    assert stacks.members(conn, 7) == []
    h.undo(conn)
    assert stacks.members(conn, 7) == ids[:3]
    assert not conn.execute("SELECT 1 FROM stack_dismissed WHERE file_id = ?", (ids[0],)).fetchone()
    h.redo(conn)
    assert stacks.members(conn, 7) == []
    h.before(conn, "paste", "edits", ids)
    for f in ids:
        store.save(conn, f, Stack(adjust={"exposure": 0.7}))
    h.undo(conn)
    assert not store.edited_ids(conn, ids)
    h.redo(conn)
    assert sorted(store.edited_ids(conn, ids)) == sorted(ids)


def test_the_window_undoes_and_redoes_with_the_keys(tmp_path):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from lunelis.ui import main_window as mw
    w = mw.MainWindow()
    try:
        root = tmp_path / "W"
        root.mkdir()
        Image.new("RGB", (30, 20)).save(root / "W1.jpg")
        rid = add_root(w.conn, root)
        scan_root(w.conn, rid)
        fid = w.conn.execute("SELECT id FROM files WHERE root_id = ?", (rid,)).fetchone()[0]
        w.reload()
        w.grid.selected = {fid}
        w.rate(stars=3)
        w.toggle_archive()
        assert w.conn.execute("SELECT archived_at IS NOT NULL FROM files WHERE id = ?", (fid,)).fetchone()[0]
        w._update_undo_actions()
        assert w.undo_action.text().startswith("&Undo archive")
        w.undo()
        assert not w.conn.execute("SELECT archived_at IS NOT NULL FROM files WHERE id = ?", (fid,)).fetchone()[0]
        w.undo()
        assert stars(w.conn, [fid]) == [0] and w.status.text().startswith("Undone: ★★★")
        w.redo()
        assert stars(w.conn, [fid]) == [3]
    finally:
        w._quitting = True
        w.close()
