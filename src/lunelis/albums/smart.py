"""
Smart albums: saved rules that pick photos by themselves, and stay current.

    {"match": "all", "rules": [{"field": "iso", "op": ">=", "value": 3200},
                               {"field": "stars", "op": "=", "value": 5},
                               {"field": "lens", "op": "contains", "value": "24-70"}]}

Stored in albums (is_smart = 1, smart_rule_json). `condition()` turns the
rules into an SQL condition over the library query's tables (f = files,
e = exif, rt = ratings), so opening a smart album is just a library filter
(Filter.smart) and its count is one query - it changes as photos do.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from lunelis.albums.model import LIVE, Album

KINDS = {
    "photo": "f.is_raw = 0 AND COALESCE(f.format, '') NOT IN ('mp4', 'mov', 'mpeg-ts')",
    "raw": "f.is_raw = 1",
    "video": "COALESCE(f.format, '') IN ('mp4', 'mov', 'mpeg-ts')",
}


@dataclass(frozen=True)
class Field:
    title: str
    kind: str                       # num | text | enum | date | tag | search
    column: str = ""
    choices: tuple[str, ...] = ()


FIELDS: dict[str, Field] = {
    "stars": Field("Stars", "num", "COALESCE(rt.stars, 0)"),
    "flag": Field("Flag", "enum", "COALESCE(rt.flag, 'none')", ("pick", "reject", "none")),
    "label": Field("Colour label", "enum", "COALESCE(rt.color_label, 'none')",
                   ("Red", "Yellow", "Green", "Blue", "Purple", "none")),
    "camera": Field("Camera", "text", "e.camera_model"),
    "lens": Field("Lens", "text", "e.lens"),
    "iso": Field("ISO", "num", "e.iso"),
    "aperture": Field("Aperture (f/)", "num", "e.aperture"),
    "focal": Field("Focal length (mm)", "num", "e.focal_length_mm"),
    # Any "a/b" is a divided by b, not only "1/b": 0.6 s stored as "3/5" was
    # read as 3 seconds. Plain and decimal seconds ("2", "0.6", "2.5s") as
    # before; "x/0" is no value rather than an error (0.54).
    "shutter_s": Field("Shutter (seconds)", "num",
                       "(CASE WHEN INSTR(e.shutter_speed, '/') > 0 THEN"
                       " CAST(SUBSTR(e.shutter_speed, 1, INSTR(e.shutter_speed, '/') - 1) AS REAL)"
                       " / NULLIF(CAST(SUBSTR(e.shutter_speed, INSTR(e.shutter_speed, '/') + 1) AS REAL), 0)"
                       " ELSE CAST(REPLACE(e.shutter_speed, 's', '') AS REAL) END)"),
    "date": Field("Date taken", "date", "SUBSTR(e.captured_at, 1, 10)"),
    "kind": Field("Kind", "enum", "", tuple(KINDS)),
    "tag": Field("Tag", "tag"),
    "text": Field("Search words", "search"),
}

OPS = {
    "num": (">=", "<=", "=", ">", "<"),
    "text": ("contains", "is", "is not"),
    "enum": ("is", "is not"),
    "date": ("on or after", "on or before", "is"),
    "tag": ("has", "doesn't have"),
    "search": ("matches",),
}


class RuleError(ValueError):
    pass


def check(smart: dict) -> None:
    # The wrong shape altogether is a RuleError like any other bad rule, not
    # an AttributeError or TypeError from deep inside (0.54).
    if not isinstance(smart, dict):
        raise RuleError("the rules aren't in a form Lunelis can read")
    if smart.get("match", "all") not in ("all", "any"):
        raise RuleError("match must be all or any")
    rules = smart.get("rules") or []
    if not isinstance(rules, list) or not rules:
        raise RuleError("a smart album needs at least one rule")
    for r in rules:
        if not isinstance(r, dict) or not isinstance(r.get("field"), str):
            raise RuleError("a rule isn't in a form Lunelis can read")
        f = FIELDS.get(r.get("field"))
        if f is None:
            raise RuleError(f"unknown field {r.get('field')!r}")
        if r.get("op") not in OPS[f.kind]:
            raise RuleError(f"{f.title}: the test must be one of {OPS[f.kind]}")
        v = r.get("value")
        if f.kind == "num":
            try:
                float(v)
            except (TypeError, ValueError):
                raise RuleError(f"{f.title}: {v!r} isn't a number") from None
        elif f.kind == "enum" and v not in f.choices:
            raise RuleError(f"{f.title}: {v!r} isn't one of {f.choices}")
        elif f.kind == "date":
            from datetime import date
            try:
                date.fromisoformat(str(v))
            except ValueError:
                raise RuleError(f"{f.title}: {v!r} isn't a date like 2026-06-19") from None
        elif not str(v or "").strip():
            raise RuleError(f"{f.title}: give a value")


def _one(r: dict) -> tuple[str, list]:
    f = FIELDS[r["field"]]
    op, v = r["op"], r["value"]
    if f.kind == "num":
        return f"{f.column} {op} ?", [float(v)]
    if f.kind == "text":
        if op == "contains":
            return f"COALESCE({f.column}, '') LIKE ? ESCAPE '!'", [
                "%" + str(v).replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"]
        return (f"COALESCE({f.column}, '') {'=' if op == 'is' else '!='} ? COLLATE NOCASE", [str(v)])
    if f.kind == "enum":
        if r["field"] == "kind":
            cond = KINDS[v]
            return (f"({cond})" if op == "is" else f"NOT ({cond})"), []
        return f"{f.column} {'=' if op == 'is' else '!='} ?", [v]
    if f.kind == "date":
        sql = {"on or after": ">=", "on or before": "<=", "is": "="}[op]
        return f"{f.column} {sql} ?", [str(v)]
    if f.kind == "tag":
        from lunelis.tags.model import filter_sql
        cond, params = filter_sql(str(v))
        return (f"({cond})" if op == "has" else f"NOT ({cond})"), list(params)
    from lunelis.search import filter_sql as search_sql
    cond, params = search_sql(str(v))
    return f"({cond})", list(params)


def condition(smart: dict) -> tuple[str, list]:
    """The rules as one SQL condition (+ params) for the library query."""
    check(smart)
    parts, params = [], []
    for r in smart["rules"]:
        sql, p = _one(r)
        parts.append(f"({sql})")
        params.extend(p)
    joiner = " AND " if smart.get("match", "all") == "all" else " OR "
    return "(" + joiner.join(parts) + ")", params


def describe(smart: dict) -> str:
    """'ISO >= 3200 and Stars = 5' - for the tile and the filter chip."""
    words = []
    if not isinstance(smart, dict) or not isinstance(smart.get("rules", []), list):
        return ""
    for r in smart.get("rules", []):
        # A rule that isn't one (hand-edited, from a newer version, a number
        # that isn't a number) is left out of the words - it used to raise
        # here and take the whole Albums list with it (0.54).
        if not isinstance(r, dict):
            continue
        f = FIELDS.get(r.get("field")) if isinstance(r.get("field"), str) else None
        if f is None:
            continue
        v = r.get("value")
        if f.kind == "num":
            try:
                if float(v).is_integer():
                    v = int(float(v))
            except (TypeError, ValueError, OverflowError):
                pass
        words.append(f"{f.title} {r.get('op')} {v}")
    return (" and " if smart.get("match", "all") == "all" else " or ").join(words)


# --- stored ---------------------------------------------------------------------------------

def create(conn: sqlite3.Connection, name: str, smart: dict) -> int:
    check(smart)
    name = " ".join((name or "").split())
    if not name:
        raise RuleError("a smart album needs a name")
    aid = conn.execute("INSERT INTO albums (name, is_smart, smart_rule_json) VALUES (?, 1, ?)",
                       (name, json.dumps(smart))).lastrowid
    conn.commit()
    return aid


def update(conn: sqlite3.Connection, album_id: int, smart: dict, name: str | None = None) -> None:
    check(smart)
    conn.execute("UPDATE albums SET smart_rule_json = ?, name = COALESCE(?, name) WHERE id = ? AND is_smart = 1",
                 (json.dumps(smart), (" ".join(name.split()) or None) if name else None, album_id))
    conn.commit()


def get(conn: sqlite3.Connection, album_id: int) -> dict:
    row = conn.execute("SELECT smart_rule_json FROM albums WHERE id = ? AND is_smart = 1", (album_id,)).fetchone()
    if row is None:
        raise RuleError("no such smart album")
    return json.loads(row[0])


def smart_albums(conn: sqlite3.Connection) -> list[Album]:
    """Each smart album with how many photos it finds now and a cover."""
    out = []
    base = ("FROM files f JOIN roots r ON r.id = f.root_id LEFT JOIN exif e ON e.file_id = f.id"
            f" LEFT JOIN ratings rt ON rt.file_id = f.id WHERE {LIVE} AND r.enabled = 1 AND f.archived_at IS NULL")
    for aid, name, rule in conn.execute(
            "SELECT id, name, smart_rule_json FROM albums WHERE is_smart = 1 ORDER BY name COLLATE NOCASE"):
        # One album whose stored rules can't be used shows as empty, with a
        # word about why; every other album is still listed (0.54).
        n, cover, blurb = 0, None, "Its rules can't be read - edit the album to fix them"
        try:
            smart = json.loads(rule or "{}")
            blurb = describe(smart) or blurb
            cond, params = condition(smart)
            n, cover = conn.execute(
                f"SELECT COUNT(*), MAX(CASE WHEN f.thumbnail_path IS NOT NULL THEN f.id END) {base} AND {cond}",
                params).fetchone()
        except (ValueError, TypeError, AttributeError, KeyError, OverflowError, sqlite3.Error):
            n, cover = 0, None          # RuleError and a JSON error are both ValueErrors
        out.append(Album("smart", str(aid), name, n, cover, blurb=blurb))
    return out
