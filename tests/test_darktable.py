"""Phase 2, step 4: the darktable plugin. The real lunelis.lua runs in Lua 5.4
(lupa - the same Lua darktable embeds) against a stand-in `darktable` module,
talking to the real Python side through the real exchange files."""
import os
import time

import pytest

lupa = pytest.importorskip("lupa.lua54")

from lunelis.catalog.ratings import set_ratings  # noqa: E402
from lunelis.catalog.schema import open_catalog  # noqa: E402
from lunelis.darktable import bridge  # noqa: E402
from lunelis.importers.scan import add_root, scan_root  # noqa: E402
from lunelis.settings import Settings  # noqa: E402

FAKE_DARKTABLE = """
local fake = {database = {}, prefs = {}, printed = {}}
fake.preferences = {
  register = function() end,
  read = function(module, name, kind) return fake.prefs[name] end,
  write = function(module, name, kind, value) fake.prefs[name] = value end,
}
fake.print = function(msg) fake.printed[#fake.printed + 1] = msg end
package.preload["darktable"] = function() return fake end
return fake
"""


class Darktable:
    """darktable with the plugin loaded: .images[name] are its library entries."""

    def __init__(self, folder, names, exchange):
        self.lua = lupa.LuaRuntime(unpack_returned_tuples=True)
        self.fake = self.lua.execute(FAKE_DARKTABLE)
        self.images = {}
        for i, name in enumerate(names, 1):
            img = self.lua.table_from({"path": str(folder), "filename": name, "rating": 0, "red": False,
                                       "yellow": False, "green": False, "blue": False, "purple": False})
            self.fake.database[i] = img
            self.images[name] = img
        self.script = bridge.LUA.read_text(encoding="utf-8").replace("@EXCHANGE@", str(exchange))

    def start(self):
        self.M = self.lua.execute(self.script)      # darktable loads the script: first sync
        return self

    def sync(self):
        return self.M.sync(True)


@pytest.fixture
def lib(tmp_path):
    photos = tmp_path / "Photos" / "2026" / "6-19-2026"
    photos.mkdir(parents=True)
    for n in ("a.ARW", "b.ARW", "c.jpg"):
        (photos / n).write_bytes(os.urandom(100))
    conn = open_catalog(tmp_path / "cat.db")
    rid = add_root(conn, tmp_path / "Photos")
    scan_root(conn, rid)
    ex = tmp_path / "exchange"
    Settings(conn).set("darktable_exchange_dir", str(ex))
    Settings(conn).set("darktable_sync", True)
    ids = {n: i for i, n in conn.execute("SELECT id, filename FROM files")}
    yield conn, photos, ex, ids
    conn.close()


def rating(conn, fid):
    return tuple(conn.execute("SELECT stars, flag, color_label FROM ratings WHERE file_id = ?", (fid,)).fetchone())


def test_lunelis_ratings_reach_darktable(lib):
    conn, photos, ex, ids = lib
    set_ratings(conn, [ids["a.ARW"]], stars=4, label="Red")
    set_ratings(conn, [ids["b.ARW"]], flag="reject")
    bridge.export(conn)
    set_ratings(conn, [ids["c.jpg"]], label="Green")
    bridge.export(conn)
    dt = Darktable(photos, ["a.ARW", "b.ARW", "c.jpg"], ex)
    dt.images["a.ARW"]["blue"] = True             # darktable's own labels - two of them
    dt.images["a.ARW"]["yellow"] = True
    dt.images["c.jpg"]["purple"] = True           # just one
    dt.start()
    a, b, c = dt.images["a.ARW"], dt.images["b.ARW"], dt.images["c.jpg"]
    assert (a["rating"], a["red"], a["blue"], a["yellow"]) == (4, True, True, True)   # added, none removed
    assert (c["green"], c["purple"]) == (True, False)                                 # one label: replaced
    assert b["rating"] == -1                                                          # rejected
    assert c["rating"] == 0
    # Nothing new: a second sync changes nothing and sends nothing back.
    assert dt.sync() == (0, 0)
    assert not (ex / bridge.FROM_DARKTABLE).exists()


def test_darktable_changes_come_back_and_newest_wins(lib):
    conn, photos, ex, ids = lib
    bridge.export(conn)
    dt = Darktable(photos, ["a.ARW", "b.ARW", "c.jpg"], ex).start()
    dt.images["c.jpg"]["rating"] = 5
    dt.images["c.jpg"]["green"] = True
    dt.images["b.ARW"]["rating"] = -1
    got, sent = dt.sync()
    assert sent == 2
    time.sleep(1.1)                                                # a later change in Lunelis...
    set_ratings(conn, [ids["b.ARW"]], stars=2)
    res = bridge.import_(conn)
    assert (res.applied, res.older) == (1, 1)
    assert rating(conn, ids["c.jpg"]) == (5, None, "Green")
    assert rating(conn, ids["b.ARW"]) == (2, None, None)           # ...wins over darktable's older one
    assert not (ex / bridge.FROM_DARKTABLE).exists()
    # Lunelis's value then goes to darktable on the next sync.
    bridge.export(conn)
    dt.sync()
    assert dt.images["b.ARW"]["rating"] == 2


def test_picks_stay_and_unknown_paths_are_ignored(lib):
    conn, photos, ex, ids = lib
    set_ratings(conn, [ids["a.ARW"]], flag="pick")
    (ex).mkdir(parents=True, exist_ok=True)
    (ex / bridge.FROM_DARKTABLE).write_text(
        "#darktable-ratings 1\n"
        f"{photos / 'a.ARW'}\t3\t-\t2999-01-01T00:00:00+00:00\n"
        f"{photos / 'not-in-lunelis.ARW'}\t5\t-\t2999-01-01T00:00:00+00:00\n", encoding="utf-8")
    res = bridge.import_(conn)
    assert (res.applied, res.unknown) == (1, 1)
    assert rating(conn, ids["a.ARW"]) == (3, "pick", None)          # darktable has no picks: kept


def test_tick_exports_only_when_something_changed(lib):
    conn, photos, ex, ids = lib
    state = {}
    bridge.tick(conn, state)
    first = (ex / bridge.TO_DARKTABLE).stat().st_mtime_ns
    time.sleep(0.05)
    bridge.tick(conn, state)
    assert (ex / bridge.TO_DARKTABLE).stat().st_mtime_ns == first
    set_ratings(conn, [ids["c.jpg"]], stars=1)
    bridge.tick(conn, state)
    assert "\t1\t" in (ex / bridge.TO_DARKTABLE).read_text(encoding="utf-8")
    Settings(conn).set("darktable_sync", False)
    assert bridge.tick(conn, state) is None


def test_install_and_uninstall(lib, tmp_path):
    conn, photos, ex, ids = lib
    cfg = tmp_path / "darktable-config"
    with pytest.raises(FileNotFoundError):
        bridge.install(conn, cfg)
    cfg.mkdir()
    (cfg / "luarc").write_text('require "tools/script_manager"\n', encoding="utf-8")
    target = bridge.install(conn, cfg)
    assert str(ex) in target.read_text(encoding="utf-8") and "@EXCHANGE@" not in target.read_text(encoding="utf-8")
    assert (cfg / "luarc").read_text(encoding="utf-8") == 'require "tools/script_manager"\nrequire "lunelis"\n'
    bridge.install(conn, cfg)                                      # twice: still one line
    assert (cfg / "luarc").read_text(encoding="utf-8").count(bridge.REQUIRE_LINE) == 1
    assert bridge.installed(cfg)
    bridge.uninstall(conn, cfg)
    assert (cfg / "luarc").read_text(encoding="utf-8") == 'require "tools/script_manager"\n'
    assert not bridge.installed(cfg) and not Settings(conn).get("darktable_sync")


def test_a_second_label_becomes_a_tag_and_a_damaged_file_is_cleared(lib):
    """0.49: darktable's second colour label was dropped, and a damaged
    exchange file errored on every check."""
    from lunelis.tags import model as tags
    conn, photos, ex, ids = lib
    bridge.export(conn)
    dt = Darktable(photos, ["a.ARW", "b.ARW", "c.jpg"], ex).start()
    dt.images["c.jpg"]["red"] = True
    dt.images["c.jpg"]["blue"] = True
    dt.sync()
    bridge.import_(conn)
    assert rating(conn, ids["c.jpg"])[2] == "Red"
    assert "darktable labels|Blue" in tags.tags_of(conn, ids["c.jpg"])
    (ex / bridge.FROM_DARKTABLE).write_bytes(b"\xff\xfe garbage\tnot-a-number\n" + b"\x00" * 20)
    res = bridge.import_(conn)                                       # no exception
    assert res.unknown >= 1 and not (ex / bridge.FROM_DARKTABLE).exists()
