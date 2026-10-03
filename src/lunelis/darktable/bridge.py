"""
The Lunelis side of the darktable plugin (lunelis.lua): the exchange files and
installing the script.

  export()  -> <exchange>/lunelis-ratings.tsv    every catalog rating (stars, reject,
                                                 label) with when it last changed
  import_() <- <exchange>/darktable-ratings.tsv  what changed in darktable; applied
                                                 unless Lunelis changed that photo later
  install() -> <darktable config>/lua/lunelis.lua + a `require "lunelis"` in luarc

Photos are matched by full path (case-insensitive). Picks stay Lunelis-only
(darktable has no picks); darktable's extra colour labels are never removed.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from lunelis.xmp.sidecar import LABELS

LUA = Path(__file__).with_name("lunelis.lua")
TO_DARKTABLE = "lunelis-ratings.tsv"
FROM_DARKTABLE = "darktable-ratings.tsv"
REQUIRE_LINE = 'require "lunelis"'


def exchange_dir(conn: sqlite3.Connection) -> Path:
    from lunelis import paths
    from lunelis.settings import Settings
    return Path(Settings(conn).get("darktable_exchange_dir") or paths.DATA_DIR / "darktable")


def darktable_config_dir() -> Path:
    """darktable's own settings folder (where luarc and lua/ live)."""
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "darktable"


def _utc_iso(sqlite_ts: str | None) -> str:
    """ratings.updated_at ('YYYY-MM-DD HH:MM:SS', UTC) -> ISO with offset, like the plugin writes."""
    if not sqlite_ts:
        return "1970-01-01T00:00:00+00:00"
    return sqlite_ts.replace(" ", "T")[:19] + "+00:00"


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def export(conn: sqlite3.Connection) -> int:
    """Write every rating in the catalog for the plugin. Returns rows written."""
    rows = []
    for root, rel, stars, flag, label, updated in conn.execute(
            "SELECT r.path, f.rel_path, rt.stars, rt.flag, rt.color_label, rt.updated_at"
            " FROM ratings rt JOIN files f ON f.id = rt.file_id JOIN roots r ON r.id = f.root_id"
            " WHERE f.missing_since IS NULL AND f.quarantined_at IS NULL ORDER BY f.id"):
        path = os.path.join(root, *rel.split("/"))
        value = -1 if flag == "reject" else (stars or 0)
        rows.append(f"{path}\t{value}\t{(label or '-').lower()}\t{_utc_iso(updated)}")
    _write_atomic(exchange_dir(conn) / TO_DARKTABLE,
                  f"#lunelis-ratings 1 {datetime.now(tz=timezone.utc).isoformat(timespec='seconds')}\n"
                  + "\n".join(rows) + ("\n" if rows else ""))
    return len(rows)


@dataclass
class ImportResult:
    applied: int = 0
    older: int = 0            # Lunelis changed the photo after darktable did: kept Lunelis's
    unknown: int = 0          # not a photo in the catalog


def _find(conn, path: str) -> int | None:
    norm = os.path.normcase(os.path.normpath(path))
    for rid, root in conn.execute("SELECT id, path FROM roots"):
        base = os.path.normcase(os.path.normpath(root)).rstrip("\\")
        if norm.startswith(base + "\\"):
            rel = os.path.relpath(os.path.normpath(path), os.path.normpath(root)).replace("\\", "/")
            row = conn.execute("SELECT id FROM files WHERE root_id = ? AND rel_path = ?", (rid, rel)).fetchone() \
                or conn.execute("SELECT id FROM files WHERE root_id = ? AND rel_path = ? COLLATE NOCASE",
                                (rid, rel)).fetchone()
            return row[0] if row else None
    return None


def import_(conn: sqlite3.Connection) -> ImportResult:
    """Apply darktable's changes, then clear the file (the plugin re-sends
    anything it changes later). Newest change wins."""
    from lunelis.catalog import ratings
    res = ImportResult()
    src = exchange_dir(conn) / FROM_DARKTABLE
    try:
        lines = src.read_text(encoding="utf-8").splitlines()
    except OSError:
        return res
    for line in lines:
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        path, rating, labels, when = parts[0], int(parts[1]), parts[2], parts[3]
        fid = _find(conn, path)
        if fid is None:
            res.unknown += 1
            continue
        cur = conn.execute("SELECT stars, flag, color_label, updated_at FROM ratings WHERE file_id = ?",
                           (fid,)).fetchone()
        if cur and _utc_iso(cur[3]) > when:
            res.older += 1
            continue
        dt_labels = [l for l in labels.split(",") if l and l != "-"]
        cur_label = cur[2] if cur else None
        # One label in Lunelis: keep ours if darktable still has it, else darktable's first.
        label = cur_label if cur_label and cur_label.lower() in dt_labels else \
            next((n for n in LABELS if n.lower() in dt_labels), None)
        stars = max(rating, 0)
        flag = "reject" if rating < 0 else (cur[1] if cur and cur[1] == "pick" else None)
        if cur and (cur[0] or 0, cur[1], cur_label) == (stars, flag, label):
            continue
        ratings.set_ratings(conn, [fid], stars=stars, flag=flag, label=label)
        res.applied += 1
    try:
        src.unlink()
    except OSError:
        pass
    return res


def sync(conn: sqlite3.Connection) -> ImportResult:
    """darktable's changes in, then everything out."""
    res = import_(conn)
    export(conn)
    return res


def tick(conn: sqlite3.Connection, state: dict) -> ImportResult | None:
    """The app's periodic check (cheap when nothing changed): pick up
    darktable's file if there is one, and rewrite ours only when a rating
    changed since the last export. `state` persists between calls."""
    from lunelis.settings import Settings
    if not Settings(conn).get("darktable_sync"):
        return None
    res = None
    if (exchange_dir(conn) / FROM_DARKTABLE).exists():
        res = import_(conn)
    newest = conn.execute("SELECT MAX(updated_at) || COUNT(*) FROM ratings").fetchone()[0]
    if newest != state.get("exported") or not (exchange_dir(conn) / TO_DARKTABLE).exists():
        export(conn)
        state["exported"] = newest
    return res


# --- installing the plugin ---------------------------------------------------------------

def installed(config_dir: Path | None = None) -> bool:
    cfg = config_dir or darktable_config_dir()
    return (cfg / "lua" / "lunelis.lua").exists() and REQUIRE_LINE in _read(cfg / "luarc")


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def install(conn: sqlite3.Connection, config_dir: Path | None = None) -> Path:
    """Copy the script into darktable's lua folder (with this library's
    exchange folder filled in) and load it from luarc. darktable picks it up
    at its next start. Only adds a line to luarc - nothing else is changed."""
    cfg = config_dir or darktable_config_dir()
    if not cfg.is_dir():
        raise FileNotFoundError(f"darktable's settings folder wasn't found ({cfg}) - "
                                "install and run darktable once first")
    ex = exchange_dir(conn)
    ex.mkdir(parents=True, exist_ok=True)
    script = LUA.read_text(encoding="utf-8").replace("@EXCHANGE@", str(ex))
    target = cfg / "lua" / "lunelis.lua"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(script, encoding="utf-8", newline="\n")
    luarc = cfg / "luarc"
    text = _read(luarc)
    if REQUIRE_LINE not in text:
        luarc.write_text((text.rstrip("\n") + "\n" if text.strip() else "") + REQUIRE_LINE + "\n",
                         encoding="utf-8", newline="\n")
    from lunelis.settings import Settings
    Settings(conn).set("darktable_sync", True)
    export(conn)
    return target


def uninstall(conn: sqlite3.Connection, config_dir: Path | None = None) -> None:
    """Take the script out of darktable (luarc line + the file) and stop syncing."""
    from lunelis.settings import Settings
    Settings(conn).set("darktable_sync", False)
    cfg = config_dir or darktable_config_dir()
    luarc = cfg / "luarc"
    text = _read(luarc)
    if REQUIRE_LINE in text:
        luarc.write_text("".join(l for l in text.splitlines(keepends=True) if l.strip() != REQUIRE_LINE),
                         encoding="utf-8", newline="\n")
    try:
        (cfg / "lua" / "lunelis.lua").unlink()
    except OSError:
        pass
