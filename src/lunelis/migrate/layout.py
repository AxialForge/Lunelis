"""
The library layout a migration builds (0.40): photos, videos and timelapses
apart inside each day, undated files in their own tree.

    <target>\\Library\\Photos and Videos\\2024\\6-19-2024 Air Show\\Photos\\...
                                                         \\Videos\\...
                                                         \\Timelapse\\18-00 (786 frames)\\...
    <target>\\Library\\Undated\\Photos\\...   \\Videos\\...

- The day folder comes from the storage template ({YYYY}\\{M}-{D}-{YYYY}
  [ {event}]); a day with more than one event is named after its first.
  A photo in an event that runs past midnight is filed by the event's start.
- Videos go to Videos; the frames of a timelapse (timelapses.py; not
  dismissed) to their own Timelapse\\<start time> (<frames> frames) folder.
- Photos is flat for the day unless a subfolder is chosen: by camera, or the
  folder the photo came from.
- A photo with no capture date is filed by the date in its name when it has
  one (20170808_174715.jpg - phones and Google name files so; a damaged
  file keeps its day this way), else goes to Undated - or, when that option
  is on, is filed by its modified date if that date is believable (see
  believable_mtime).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from lunelis.importing.templates import Context, _clean_segment, render

LIBRARY = "Library"
PHOTOS_AND_VIDEOS = "Photos and Videos"
UNDATED = "Undated"
DAY_TEMPLATE = r"{YYYY}\{M}-{D}-{YYYY}[ {event}]"
SUBFOLDERS = ("none", "camera", "original")
EARLIEST = datetime(1995, 1, 1)           # older "modified" dates are camera clocks that were never set


@dataclass
class LayoutOptions:
    undated_by_mtime: bool = False         # file an undated photo by a believable modified date
    photo_subfolders: str = "none"         # none | camera | original
    day_template: str = DAY_TEMPLATE


def media_kind(fmt: str | None, video_formats: set[str], filename: str | None = None) -> str:
    """Photos or Videos - by the format read from the file, else (a damaged
    file has none) by its extension."""
    if fmt:
        return "Videos" if fmt in video_formats else "Photos"
    from lunelis.importers.formats import VIDEO_EXTS
    ext = (filename or "").rsplit(".", 1)[-1].lower() if filename and "." in filename else ""
    return "Videos" if ext in VIDEO_EXTS else "Photos"


_NAME_DATE = re.compile(r"(?<!\d)((?:19|20)\d\d)(\d\d)(\d\d)[_\-T ]?(\d\d)(\d\d)(\d\d)(?:\d{1,3})?(?!\d)")


def date_from_name(filename: str) -> datetime | None:
    """20170808_174715.jpg / IMG_20190704_101500.jpg / PXL_20240915_113955123.jpg:
    the time a phone or Google put in the name. None when there's no such
    date, or it isn't a real one (or is in the future / before 1995)."""
    m = _NAME_DATE.search(filename)
    if not m:
        return None
    try:
        when = datetime(*(int(g) for g in m.groups()))
    except ValueError:
        return None
    return when if EARLIEST <= when <= datetime.now() else None


def timelapse_folder(start: datetime | None, frames: int) -> str:
    when = f"{start:%H-%M}" if start else "Timelapse"
    return f"{when} ({frames:,} frames)"


def believable_mtime(mtime: float | None, folder_mtimes: list[float], now: datetime | None = None) -> bool:
    """A modified date that's probably when the photo was taken: not in the
    future, not before 1995, and not the day the whole folder was copied (a
    folder of 10+ files where 80 % share one modified day was copied then)."""
    if not mtime:
        return False
    when = datetime.fromtimestamp(mtime)
    if when > (now or datetime.now()) or when < EARLIEST:
        return False
    if len(folder_mtimes) >= 10:
        days = [datetime.fromtimestamp(m).date() for m in folder_mtimes if m]
        same = sum(1 for d in days if d == when.date())
        if same >= 0.8 * len(days):
            return False
    return True


def place(opts: LayoutOptions, *, taken: datetime | None, kind: str, event: str | None = None,
          event_start: datetime | None = None, camera: str | None = None, original_folder: str | None = None,
          timelapse: tuple[datetime | None, int] | None = None, mtime_date: datetime | None = None) -> str:
    """The folder (backslash-separated, under the target) a file goes to."""
    when = taken
    if when is None and event_start is None and opts.undated_by_mtime and mtime_date is not None:
        when = mtime_date
    if when is None and event_start is None:
        return "\\".join((LIBRARY, UNDATED, kind))
    day = render(opts.day_template, Context(taken=when, camera=camera, event=event, event_start=event_start,
                                             import_name=event))
    parts = [LIBRARY, PHOTOS_AND_VIDEOS, day]
    if kind == "Photos" and timelapse is not None:
        parts += ["Timelapse", timelapse_folder(*timelapse)]
    else:
        parts.append(kind)
        if kind == "Photos":
            sub = {"camera": camera, "original": original_folder}.get(opts.photo_subfolders)
            if sub and sub.strip():
                parts.append(_clean_segment(sub.strip()))
    return "\\".join(parts)


def first_event_of_day(rows: list[tuple[datetime | None, str | None, datetime | None]]) -> dict:
    """{date: (event name, event start)} - the earliest-starting event on each
    day, from (taken, event name, event start) rows; the whole day takes it."""
    out: dict = {}
    for taken, name, start in rows:
        if not name:
            continue
        day = (start or taken).date() if (start or taken) else None
        if day is None:
            continue
        cur = out.get(day)
        if cur is None or (start and cur[1] and start < cur[1]):
            out[day] = (name, start)
    return out
