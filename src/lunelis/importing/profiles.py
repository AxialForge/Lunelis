"""
Camera profiles: how each brand lays out a memory card, as data.

`camera_profiles.json` (next to this file) holds the built-in profiles; a
`camera_profiles.json` in the data folder adds or replaces profiles by id,
so a new camera is a config change, not a code change. A profile says:

- `markers`: paths (with * wildcards) whose presence identifies the card;
- `media_dirs`: folders read recursively for photos and videos (a card with
  none of them is read whole - "import from a folder");
- `skip_dirs`: folders never read (proxies, thumbnails, camera databases);
- `sidecars`: for files of these extensions, which neighbouring files belong
  to them (`{stem}` = name without extension, `{name}` = full name). They are
  copied, verified and filed with their file, never on their own.
- `makes`: EXIF Make values, for matching a photo to its profile later.
"""
from __future__ import annotations

import fnmatch
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

BUILT_IN = Path(__file__).with_name("camera_profiles.json")


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    makes: tuple[str, ...] = ()
    markers: tuple[str, ...] = ()
    media_dirs: tuple[str, ...] = ("DCIM",)
    skip_dirs: tuple[str, ...] = ()
    sidecars: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = ()   # ((exts...), (name patterns...))
    note: str = ""

    def sidecar_names(self, filename: str) -> list[str]:
        """Candidate sidecar file names for this media file, in order."""
        stem, ext = os.path.splitext(filename)
        ext = ext.lower().lstrip(".")
        out: list[str] = []
        for exts, names in self.sidecars:
            if "*" in exts or ext in exts:
                for pat in names:
                    n = pat.format(stem=stem, name=filename)
                    if n not in out and n != filename:
                        out.append(n)
        return out


def _parse(d: dict) -> Profile:
    return Profile(
        id=d["id"], name=d.get("name", d["id"]), makes=tuple(d.get("makes", ())),
        markers=tuple(d.get("markers", ())), media_dirs=tuple(d.get("media_dirs", ("DCIM",))),
        skip_dirs=tuple(d.get("skip_dirs", ())),
        sidecars=tuple((tuple(e.lower() for e in s.get("for", ())), tuple(s.get("names", ())))
                       for s in d.get("sidecars", ())),
        note=d.get("note", ""))


def load(data_dir: str | os.PathLike | None = None) -> list[Profile]:
    """Built-in profiles, with the data folder's own on top (same id replaces)."""
    profiles = {d["id"]: _parse(d) for d in json.loads(BUILT_IN.read_text(encoding="utf-8"))["profiles"]}
    if data_dir is not None:
        user = Path(data_dir) / "camera_profiles.json"
        if user.is_file():
            try:
                extra = json.loads(user.read_text(encoding="utf-8")).get("profiles", [])
            except (OSError, ValueError):
                extra = []                    # a broken user file never stops an import
            for d in extra:
                if isinstance(d, dict) and d.get("id"):
                    profiles[d["id"]] = _parse(d)
    generic = profiles.pop("generic", Profile("generic", "Camera or folder"))
    return [*profiles.values(), generic]       # generic always last: it matches anything


def _exists(source: str, pattern: str) -> bool:
    parts = pattern.split("/")
    candidates = [source]
    for part in parts:
        nxt = []
        for base in candidates:
            if any(c in part for c in "*?["):
                try:
                    nxt += [os.path.join(base, n) for n in os.listdir(base) if fnmatch.fnmatch(n.upper(), part.upper())]
                except OSError:
                    pass
            else:
                p = os.path.join(base, part)
                if os.path.exists(p):
                    nxt.append(p)
        candidates = nxt
        if not candidates:
            return False
    return True


def detect(source: str, profiles: list[Profile] | None = None) -> Profile:
    """The profile for this card (or folder): the first whose marker is on it."""
    profiles = profiles if profiles is not None else load()
    for p in profiles:
        if p.markers and any(_exists(source, m) for m in p.markers):
            return p
    return next(p for p in profiles if p.id == "generic")


def for_make(make: str | None, profiles: list[Profile] | None = None) -> Profile | None:
    """The profile of a camera maker (EXIF Make), if one names it."""
    if not make:
        return None
    m = make.strip().lower()
    for p in profiles if profiles is not None else load():
        if any(m == x.lower() or m.startswith(x.lower()) for x in p.makes):
            return p
    return None
