"""
Storage templates: which FOLDER a photo goes into. Never its file name -
imported files always keep the name the camera gave them.

A template is a relative folder path with tokens:

    {YYYY} {YY}          year           2026, 26
    {M} {MM}             month          6, 06
    {D} {DD}             day            19, 09
    {month_name}         month name     June
    {date}               ISO date       2026-06-19
    {camera}             camera model   ILCE-7RM5
    {import_name}        the name typed when importing (may be empty)
    {event}              the event the photo belongs to (a named import is one)
    {import_date}        the day of the import, ISO
    {original_folder}    the folder the file was in on the card

Square brackets make a part optional: it's dropped unless every token inside
has a value. {a|b} uses a if it has a value, else b: {YYYY}\\{event|date}.

A photo in an event is filed by the event's START date, so a night shoot or a
week-long trip stays in one folder instead of splitting at midnight. The default reproduces the user's existing library layout:

    {YYYY}\\{M}-{D}-{YYYY}[ {import_name}]
        -> 2026\\6-19-2026              (no import name)
        -> 2026\\6-19-2026 Air Show     (named import)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

DEFAULT_TEMPLATE = r"{YYYY}\{M}-{D}-{YYYY}[ {import_name}]"

PRESETS: dict[str, str] = {
    "Year \\ M-D-Year (your current layout)": DEFAULT_TEMPLATE,
    "Year": r"{YYYY}[\{import_name}]",
    "Year \\ Month": r"{YYYY}\{MM}[ {import_name}]",
    "Year \\ Month \\ Day": r"{YYYY}\{MM}\{DD}[ {import_name}]",
    "Year \\ Date": r"{YYYY}\{date}[ {import_name}]",
    "Import date \\ name": r"Imports\{import_date}[ {import_name}]",
    "Camera \\ Year": r"{camera}\{YYYY}[\{import_name}]",
    "Year \\ Event (else the date)": r"{YYYY}\{event|date}",
}

UNDATED = "Undated"
_TOKEN = re.compile(r"\{([\w|]+)\}")
_OPTIONAL = re.compile(r"\[([^\[\]]*)\]")
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')      # not allowed in a Windows folder name
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


class TemplateError(ValueError):
    pass


@dataclass
class Context:
    taken: datetime | None          # capture time (camera wall clock); None = undated
    camera: str | None = None
    import_name: str | None = None
    import_date: date | None = None
    original_folder: str | None = None
    event: str | None = None
    event_start: datetime | None = None   # dates come from here when the photo is in an event


def _values(ctx: Context) -> dict[str, str | None]:
    t = ctx.event_start or ctx.taken
    return {
        "YYYY": f"{t.year:04d}" if t else None,
        "YY": f"{t.year % 100:02d}" if t else None,
        "M": str(t.month) if t else None,
        "MM": f"{t.month:02d}" if t else None,
        "D": str(t.day) if t else None,
        "DD": f"{t.day:02d}" if t else None,
        "month_name": t.strftime("%B") if t else None,
        "date": t.date().isoformat() if t else None,
        "camera": (ctx.camera or "").strip() or None,
        "import_name": (ctx.import_name or "").strip() or None,
        "import_date": (ctx.import_date or date.today()).isoformat(),
        "original_folder": (ctx.original_folder or "").strip() or None,
        "event": (ctx.event or "").strip() or None,
    }


def _lookup(values: dict[str, str | None], token: str) -> str | None:
    # 'event|date' -> the first alternative with a value.
    for alt in token.split("|"):
        if values.get(alt) is not None:
            return values[alt]
    return None


def validate(template: str) -> None:
    names = {alt for tok in _TOKEN.findall(template) for alt in tok.split("|")}
    unknown = names - set(_values(Context(None)).keys())
    if unknown:
        raise TemplateError(f"unknown token(s): {', '.join('{' + u + '}' for u in sorted(unknown))}")
    if template.count("[") != template.count("]"):
        raise TemplateError("unbalanced [ ]")
    if template.strip().startswith(("\\", "/")) or re.match(r"^[A-Za-z]:", template.strip()):
        raise TemplateError("a template is a folder path relative to the library, not an absolute path")


def _clean_segment(seg: str) -> str:
    seg = _BAD.sub("_", seg).strip().rstrip(".")
    if seg.lower() in _RESERVED:
        seg += "_"
    return seg


def render(template: str, ctx: Context) -> str:
    """The relative folder (backslash-separated) for a file. Dated tokens on an
    undated file make the whole path 'Undated[\\<import name>]'."""
    validate(template)
    values = _values(ctx)
    date_tokens = {"YYYY", "YY", "M", "MM", "D", "DD", "month_name", "date"}
    # Undated: some token needs a date and has no other alternative with a value.
    needs_date = any(set(tok.split("|")) & date_tokens and _lookup(values, tok) is None
                     for tok in _TOKEN.findall(template))
    if ctx.taken is None and ctx.event_start is None and needs_date:
        name = values["event"] or values["import_name"]
        return UNDATED + (f"\\{_clean_segment(name)}" if name else "")

    def optional(m: re.Match) -> str:
        inner = m.group(1)
        if any(_lookup(values, t) is None for t in _TOKEN.findall(inner)):
            return ""
        return inner

    text = _OPTIONAL.sub(optional, template)

    def token(m: re.Match) -> str:
        v = _lookup(values, m.group(1))
        # Token values can't introduce path separators (a camera named "A/B").
        return (v or "unknown").replace("\\", "_").replace("/", "_")

    text = _TOKEN.sub(token, text)
    segments = [_clean_segment(s) for s in re.split(r"[\\/]+", text) if s.strip()]
    if not segments:
        raise TemplateError("the template produced an empty folder")
    return "\\".join(segments)


def sibling(folder: str, n: int) -> str:
    """'2026\\6-19-2026' -> '2026\\6-19-2026 (2)': where a DIFFERENT file with an
    already-taken name goes, since files are never renamed."""
    head, _, last = folder.rpartition("\\")
    return f"{head}\\{last} ({n})" if head else f"{last} ({n})"
