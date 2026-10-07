"""
Migration / consolidation, part 1: the plan (a dry run - nothing on disk changes).

Everything in the chosen sources is laid out in one target folder with a
storage template, one good copy of each photo:

- one copy of each VERIFIED byte-identical duplicate group moves; the others
  are skipped (and follow the keeper's fate - see execute.py);
- a damaged file whose intact copy exists elsewhere is skipped; a damaged
  file that is the only copy still moves, flagged;
- file names never change: a different file whose name is taken in its
  folder goes to a sibling folder ("6-19-2026 (2)"), and a RAW+JPEG pair
  (same stem) always lands in the same folder;
- photos in an event are filed by the event's start date ({event} works).

The plan is stored (migrations + migration_items) so the preview, the job
that carries it out and the report all read the same rows, and so a paused
or interrupted migration picks up exactly where it stopped.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from lunelis.dupes.detect import keeper_rank
from lunelis.importing.templates import Context, render, sibling, validate

VIDEO_FORMATS = {"mp4", "mov", "mpeg-ts"}
SPACE_MARGIN = 1.02                  # plan for 2% more than the files add up to...
SPACE_SLACK = 1_000_000_000          # ...plus 1 GB


class PlanError(ValueError):
    pass


@dataclass
class Options:
    sources: list[int]                  # root ids to migrate
    keep_sources: bool = True           # leave originals in place until the user releases them
    one_copy: bool = True               # one copy of each verified duplicate group
    skip_damaged_copies: bool = True    # skip a damaged file when an intact copy exists
    include_videos: bool = True
    only_archived: bool = False         # just the Archive: move it out to an archive drive
    # 0.40: the new library layout (layout.py) - Library\Photos and Videos\...\Photos|Videos|Timelapse
    library_layout: bool = False
    undated_by_mtime: bool = False      # file an undated photo by a believable modified date
    photo_subfolders: str = "none"      # none | camera | original


@dataclass
class Summary:
    migration_id: int
    target: str
    template: str
    options: dict
    state: str
    move_files: int = 0
    move_bytes: int = 0
    dup_files: int = 0
    dup_bytes: int = 0
    damaged_skipped: int = 0
    damaged_only_copy: int = 0
    undated: int = 0
    sibling_folders: int = 0
    probable_copies: int = 0
    probable_bytes: int = 0
    free_bytes: int | None = None
    folders: list[tuple[str, int, int]] = field(default_factory=list)   # (top folder, files, bytes)
    states: dict[str, int] = field(default_factory=dict)

    @property
    def enough_space(self) -> bool:
        need = self.move_bytes - self.probable_bytes      # probable copies are compared, not copied
        return self.free_bytes is None or self.free_bytes >= need * SPACE_MARGIN + SPACE_SLACK


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(p)).rstrip("\\/")


def _inside(a: str, b: str) -> bool:
    a, b = _norm(a), _norm(b)
    return a == b or a.startswith(b + os.sep)


def check_target(conn: sqlite3.Connection, target: str) -> None:
    """The target must be a reachable folder outside every source and
    outside Lunelis's data folder."""
    from lunelis import paths
    if not os.path.isdir(target):
        raise PlanError(f"{target} can't be reached - is the drive plugged in / the NAS awake?")
    for rid, path in conn.execute("SELECT id, path FROM roots"):
        if _inside(target, path) or _inside(path, target):
            raise PlanError(f"The target overlaps the source {path}. Choose a folder outside your "
                            "current sources - an empty drive or share is ideal.")
    if _inside(target, str(paths.DATA_DIR)) or _inside(str(paths.DATA_DIR), target):
        raise PlanError("The target can't be inside Lunelis's data folder.")


def _dt(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso[:19])
    except ValueError:
        return None


def _same_file(a, b) -> bool:
    """Probably the same file (rows as selected in plan()): same size and the
    same sampled fingerprint, else the same capture time, else (undated) the
    same modified time. Only a hint - bytes are compared before skipping."""
    if a[5] != b[5]:
        return False
    if a[12] and b[12]:
        return a[12] == b[12]
    if a[7] or b[7]:
        return a[7] == b[7]
    return a[13] == b[13]              # no capture time: copies keep the modified time


def plan(conn: sqlite3.Connection, target: str, template: str, options: Options,
         preferred_roots: list[int] | None = None) -> int:
    """Work out where everything goes. Returns the migration id. No files move."""
    validate(template)
    if not options.sources:
        raise PlanError("Choose at least one source to migrate.")
    check_target(conn, target)
    if conn.execute("SELECT 1 FROM migrations WHERE state = 'running'").fetchone():
        raise PlanError("Another migration is still running - finish or cancel it first.")
    q = ",".join("?" * len(options.sources))
    rows = conn.execute(
        "SELECT f.id, f.root_id, r.path, f.rel_path, f.filename, f.size_bytes, f.format,"
        "       e.captured_at, e.camera_model, ev.name, ev.start_at,"
        "       EXISTS (SELECT 1 FROM damaged d WHERE d.file_id = f.id), f.sample_hash, f.mtime,"
        "       f.archived_at IS NOT NULL"
        " FROM files f JOIN roots r ON r.id = f.root_id LEFT JOIN exif e ON e.file_id = f.id"
        " LEFT JOIN event_files ef ON ef.file_id = f.id LEFT JOIN events ev ON ev.id = ef.event_id"
        " WHERE f.missing_since IS NULL AND f.excluded = 0 AND f.quarantined_at IS NULL AND r.enabled = 1"
        f" AND f.root_id IN ({q}) ORDER BY f.root_id, f.rel_path", options.sources).fetchall()
    if not options.include_videos:
        rows = [r for r in rows if (r[6] or "") not in VIDEO_FORMATS]
    if options.only_archived:
        rows = [r for r in rows if r[14]]
        if not rows:
            raise PlanError("There are no archived photos in the chosen sources.")
    by_id = {r[0]: r for r in rows}

    action: dict[int, tuple[str, int | None, str | None]] = {}     # file id -> (action, keeper, note)

    # One copy of each verified duplicate group (never a damaged one if avoidable).
    if options.one_copy:
        groups: dict[int, list[int]] = defaultdict(list)
        for gid, fid in conn.execute(
                "SELECT m.group_id, m.file_id FROM duplicate_group_files m"
                " JOIN duplicate_groups g ON g.id = m.group_id WHERE g.method = 'exact' AND g.verified = 1"):
            if fid in by_id:
                groups[gid].append(fid)
        rank = keeper_rank(preferred_roots)
        for members in groups.values():
            if len(members) < 2:
                continue
            intact = [m for m in members if not by_id[m][11]] or members
            keeper = min(((by_id[m][0], by_id[m][1], by_id[m][2], by_id[m][3]) for m in intact), key=rank)[0]
            for m in members:
                if m != keeper:
                    action[m] = ("skip_duplicate", keeper, "An identical copy moves instead")

    # Damaged files: skip when an intact copy of the same file exists.
    from lunelis.damage.check import SAME_FILE, survivors
    for fid, r in by_id.items():
        if not r[11] or fid in action:
            continue
        same = [s for s in survivors(conn, fid) if s[2] == SAME_FILE]
        if same and options.skip_damaged_copies:
            action[fid] = ("skip_damaged", same[0][0], "Damaged - an intact copy exists")
        else:
            action[fid] = ("move", None, "Damaged, and no intact copy was found - moved as it is")

    # Where each moving file goes, keeping RAW+JPEG pairs together and never renaming.
    # A RAW+JPEG pair (same folder, same stem) follows the event of either half.
    def pair_key(r):
        src_dir = r[3].rsplit("/", 1)[0] if "/" in r[3] else ""
        return r[1], src_dir.lower(), r[4].rsplit(".", 1)[0].lower()
    pair_event = {}
    for r in by_id.values():
        if r[9]:
            pair_event.setdefault(pair_key(r), (r[9], r[10]))

    lay = _layout_inputs(conn, by_id, options) if options.library_layout else None

    dest: dict[int, str] = {}
    by_pair: dict[tuple[str, str], list[int]] = defaultdict(list)
    for fid, r in by_id.items():
        if action.get(fid, ("move",))[0] != "move":
            continue
        ev_name, ev_start = (r[9], r[10]) if r[9] else pair_event.get(pair_key(r), (None, None))
        src_dir = r[3].rsplit("/", 1)[0] if "/" in r[3] else ""
        if lay is not None:
            folder = _layout_folder(lay, options, template, r, ev_name, ev_start, src_dir)
        else:
            # In a migration the "import name" of a photo is its event's name, so
            # the user's usual template names event folders ("6-17-2024 Myrtle Beach").
            folder = render(template, Context(
                taken=_dt(r[7]), camera=r[8], event=ev_name, event_start=_dt(ev_start), import_name=ev_name,
                original_folder=src_dir.rsplit("/", 1)[-1] or None))
        dest[fid] = folder
        by_pair[(folder.lower(), r[4].rsplit(".", 1)[0].lower())].append(fid)

    taken: dict[str, set[str]] = {}          # folder (lower) -> names in it (planned + on disk)

    def names_in(folder: str) -> set[str]:
        key = folder.lower()
        if key not in taken:
            on_disk = os.path.join(target, *folder.split("\\"))
            try:
                taken[key] = {n.lower() for n in os.listdir(on_disk)}
            except OSError:
                taken[key] = set()
        return taken[key]

    final: dict[int, tuple[str, str | None]] = {}
    for (_, _), fids in sorted(by_pair.items()):
        base = dest[fids[0]]
        # Split the group into "slots" - each one RAW+JPEG pair (or single file)
        # from one source folder - and give each slot the first folder where none
        # of its names is taken. A file that looks like a copy of one already in a
        # slot (same name, size and capture time, or the same sampled fingerprint)
        # joins that slot at the SAME path: when it's copied its bytes are compared,
        # and an identical file isn't copied twice (a different one then goes to a
        # sibling folder). Without this, unverified duplicates across two pools
        # would fill "(2)" folders with copies.
        slots: list[dict[str, int]] = []
        probable: set[int] = set()
        for f in sorted(fids, key=lambda f: (by_id[f][1], by_id[f][3].lower())):
            name = by_id[f][4].lower()
            twin = next((slot for slot in slots if name in slot and _same_file(by_id[slot[name]], by_id[f])), None)
            if twin is not None:
                probable.add(f)
                final[f] = (None, twin[name])          # resolved below, with its twin's folder
                continue
            for slot in slots:
                if name not in slot:
                    slot[name] = f
                    break
            else:
                slots.append({name: f})
        for slot in slots:
            names = set(slot)
            k, folder = 1, base
            while names_in(folder) & names:
                k += 1
                folder = sibling(base, k)
            names_in(folder).update(names)
            for f in slot.values():
                final[f] = (folder, f"Name taken - goes to {folder}" if k > 1 else None)
        for f in probable:
            twin_folder = final[final[f][1]][0]
            final[f] = (twin_folder, "Probably a copy of another file - compared when copying, not copied twice")

    opts = {**options.__dict__}
    mid = conn.execute("INSERT INTO migrations (target, template, options) VALUES (?, ?, ?)",
                       (os.path.normpath(target), template, json.dumps(opts))).lastrowid
    items = []
    for fid, r in by_id.items():
        act, keeper, note = action.get(fid, ("move", None, None))
        dest_rel = None
        if act == "move":
            folder, clash = final[fid]
            dest_rel = "/".join([*folder.split("\\"), r[4]])
            note = note or clash
        items.append((mid, fid, r[1], r[3], dest_rel, r[5], act, keeper, note))
    conn.executemany(
        "INSERT INTO migration_items (migration_id, file_id, src_root, src_rel, dest_rel, size, action,"
        " keeper_id, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", items)
    conn.commit()
    return mid


def _layout_inputs(conn: sqlite3.Connection, by_id: dict, options: Options) -> dict:
    """What the library layout needs beyond the rows: each day's first event,
    each timelapse frame's folder, and each source folder's modified times."""
    from lunelis.migrate import layout
    rows = [(_dt(r[7]), r[9], _dt(r[10])) for r in by_id.values()]
    days = layout.first_event_of_day(rows)
    frames: dict[int, tuple[datetime | None, int]] = {}
    partner = {fid: p for fid, p in conn.execute("SELECT id, pair_of FROM files WHERE pair_of IS NOT NULL")}
    halves: dict[int, list[int]] = defaultdict(list)
    for fid, p in partner.items():
        halves[p].append(fid)
    for ids, in conn.execute("SELECT file_ids FROM sequences WHERE status != 'dismissed' AND kind = 'timelapse'"):
        fids = json.loads(ids)
        start = _dt(by_id[fids[0]][7]) if fids and fids[0] in by_id else None
        for f in fids:
            for g in [f, *halves.get(f, []), partner.get(f)]:
                if g is not None:
                    frames[g] = (start, len(fids))
    folder_mtimes: dict[tuple, list[float]] = defaultdict(list)
    if options.undated_by_mtime:
        for r in by_id.values():
            folder_mtimes[(r[1], r[3].rsplit("/", 1)[0] if "/" in r[3] else "")].append(r[13])
    return {"days": days, "frames": frames, "folder_mtimes": folder_mtimes,
            "opts": layout.LayoutOptions(options.undated_by_mtime, options.photo_subfolders)}


def _layout_folder(lay: dict, options: Options, template: str, r, ev_name, ev_start, src_dir: str) -> str:
    from lunelis.migrate import layout
    taken, start = _dt(r[7]), _dt(ev_start)
    day = (start or taken).date() if (start or taken) else None
    if day in lay["days"]:                         # the whole day takes its first event's name
        ev_name = lay["days"][day][0]
    mtime_date = None
    if taken is None and start is None and options.undated_by_mtime and layout.believable_mtime(
            r[13], lay["folder_mtimes"].get((r[1], src_dir), [])):
        mtime_date = datetime.fromtimestamp(r[13])
    kind = layout.media_kind(r[6], VIDEO_FORMATS)
    return layout.place(lay["opts"], taken=taken, kind=kind, event=ev_name, event_start=start, camera=r[8],
                        original_folder=src_dir.rsplit("/", 1)[-1] or None,
                        timelapse=lay["frames"].get(r[0]), mtime_date=mtime_date)


def summary(conn: sqlite3.Connection, migration_id: int) -> Summary:
    target, template, options, state = conn.execute(
        "SELECT target, template, options, state FROM migrations WHERE id = ?", (migration_id,)).fetchone()
    s = Summary(migration_id, target, template, json.loads(options), state)
    folders: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for act, size, dest_rel, note, st in conn.execute(
            "SELECT action, size, dest_rel, note, state FROM migration_items WHERE migration_id = ?",
            (migration_id,)):
        s.states[st] = s.states.get(st, 0) + 1
        if act == "move":
            s.move_files += 1
            s.move_bytes += size
            top = dest_rel.split("/", 1)[0] if "/" in dest_rel else ""
            folders[top][0] += 1
            folders[top][1] += size
            if dest_rel.startswith(("Undated/", "Library/Undated/")):
                s.undated += 1
            if note and note.startswith("Name taken"):
                s.sibling_folders += 1
            if note and note.startswith("Probably a copy"):
                s.probable_copies += 1
                s.probable_bytes += size
            if note and note.startswith("Damaged"):
                s.damaged_only_copy += 1
        elif act == "skip_duplicate":
            s.dup_files += 1
            s.dup_bytes += size
        elif act == "skip_damaged":
            s.damaged_skipped += 1
    s.folders = sorted(((k, v[0], v[1]) for k, v in folders.items()), key=lambda t: t[0])
    try:
        s.free_bytes = shutil.disk_usage(target).free
    except OSError:
        s.free_bytes = None
    return s


def discard(conn: sqlite3.Connection, migration_id: int) -> None:
    """Throw away a plan that was never started."""
    row = conn.execute("SELECT state FROM migrations WHERE id = ?", (migration_id,)).fetchone()
    if row and row[0] == "planned":
        conn.execute("DELETE FROM migrations WHERE id = ?", (migration_id,))
        conn.commit()
