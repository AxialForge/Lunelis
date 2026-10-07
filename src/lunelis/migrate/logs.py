"""
Migration logs and the accounted-for report (0.41).

For migration N, in <Lunelis folder>\\Migration logs\\Migration N (else
<data folder>\\migration logs\\Migration N):

  before.csv     every file in every source when it started - photos,
                 videos, and everything else (documents, Thumbs.db, camera
                 XML, Takeout JSON) - path, size, modified time
  manifest.csv   one row per planned file: source, size, SHA-256 of what was
                 copied, target, action, state, where the original went, note
  after.csv      every file in the target when it finished
  report.csv     every file of before.csv and what became of it
  summary.txt    the same, in words

The accounted-for report proves every source file is somewhere: copied to
the Library (and there now, the right size), set aside as a duplicate or
damaged copy whose kept copy is in the Library, a sidecar that travelled
with its photo, or left in place for a stated reason (not a photo or video,
excluded). Anything else is UNACCOUNTED, and the originals can't be
released while there is any.
"""
from __future__ import annotations

import csv
import os
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from lunelis.dupes.quarantine import QUARANTINE_DIR

SKIP_DIRS = {QUARANTINE_DIR.lower(), "$recycle.bin", "system volume information", "@eadir", ".snapshot"}
MOVED_STATES = ("copied", "done", "kept", "released")


def logs_dir(conn: sqlite3.Connection, migration_id: int) -> Path:
    from lunelis import lunelis_folder, paths
    from lunelis.settings import Settings
    base = lunelis_folder.path(Settings(conn), lunelis_folder.MIGRATION_LOGS) or Path(paths.DATA_DIR) / "migration logs"
    return base / f"Migration {migration_id}"


def inventory(root: str, skip: tuple[str, ...] = ()) -> list[tuple[str, int, float]]:
    """(rel path with '/', size, mtime) for every file under root, on disk."""
    out = []
    skip_n = [os.path.normcase(os.path.normpath(s)) for s in skip if s]

    def walk(folder: str, rel: str) -> None:
        try:
            entries = list(os.scandir(folder))
        except OSError:
            return
        for e in entries:
            r = f"{rel}/{e.name}" if rel else e.name
            try:
                if e.is_dir(follow_symlinks=False):
                    if e.name.lower() in SKIP_DIRS or os.path.normcase(os.path.normpath(e.path)) in skip_n:
                        continue
                    walk(e.path, r)
                elif e.is_file(follow_symlinks=False):
                    st = e.stat()
                    out.append((r, st.st_size, st.st_mtime))
            except OSError:
                continue
    walk(root, "")
    return out


def _write(path: Path, header: list[str], rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    os.replace(tmp, path)


def _skip_dirs(conn: sqlite3.Connection) -> tuple[str, ...]:
    from lunelis import lunelis_folder
    from lunelis.settings import Settings
    r = lunelis_folder.root(Settings(conn))
    return (str(r),) if r else ()


def write_before(conn: sqlite3.Connection, migration_id: int) -> Path:
    """The before inventory: every file in every source of this migration."""
    roots = dict(conn.execute("SELECT id, path FROM roots"))
    src = sorted({r[0] for r in conn.execute(
        "SELECT DISTINCT src_root FROM migration_items WHERE migration_id = ?", (migration_id,))})
    rows = []
    for rid in src:
        for rel, size, mtime in inventory(roots[rid], _skip_dirs(conn)):
            rows.append((rid, roots[rid], rel, size, datetime.fromtimestamp(mtime).isoformat(timespec="seconds")))
    path = logs_dir(conn, migration_id) / "before.csv"
    _write(path, ["root_id", "source", "path", "size", "modified"], rows)
    return path


def write_manifest(conn: sqlite3.Connection, migration_id: int) -> Path:
    roots = dict(conn.execute("SELECT id, path FROM roots"))
    target = conn.execute("SELECT target FROM migrations WHERE id = ?", (migration_id,)).fetchone()[0]
    rows = []
    for src_root, src_rel, size, sha, dest, act, state, aside, note, err in conn.execute(
            "SELECT src_root, src_rel, size, sha256, dest_rel, action, state, quarantine_path, note, error"
            " FROM migration_items WHERE migration_id = ? ORDER BY src_root, src_rel", (migration_id,)):
        rows.append((os.path.join(roots.get(src_root, "?"), *src_rel.split("/")), size, sha or "",
                     os.path.join(target, *dest.split("/")) if dest else "", act, state, aside or "",
                     note or err or ""))
    path = logs_dir(conn, migration_id) / "manifest.csv"
    _write(path, ["source", "size", "sha256", "target", "action", "state", "set_aside_to", "note"], rows)
    return path


def write_after(conn: sqlite3.Connection, migration_id: int) -> Path:
    target = conn.execute("SELECT target FROM migrations WHERE id = ?", (migration_id,)).fetchone()[0]
    rows = [(rel, size, datetime.fromtimestamp(m).isoformat(timespec="seconds"))
            for rel, size, m in inventory(target, _skip_dirs(conn))]
    path = logs_dir(conn, migration_id) / "after.csv"
    _write(path, ["path", "size", "modified"], rows)
    return path


@dataclass
class Report:
    migration_id: int
    counts: Counter = field(default_factory=Counter)        # what -> files
    unaccounted: list[tuple[str, str]] = field(default_factory=list)   # (source path, why)
    rows: list[tuple[str, int, str, str]] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.unaccounted

    def text(self) -> str:
        n = sum(self.counts.values())
        lines = [f"Migration {self.migration_id}: {n:,} source files checked."]
        for what, k in sorted(self.counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {k:>9,}  {what}")
        if self.unaccounted:
            lines.append(f"\n{len(self.unaccounted):,} UNACCOUNTED - the originals can't be released yet:")
            lines += [f"  {p}  ({why})" for p, why in self.unaccounted[:200]]
        else:
            lines.append("\nEvery source file is accounted for.")
        return "\n".join(lines)


def accounted(conn: sqlite3.Connection, migration_id: int, write: bool = True) -> Report:
    """Check every file of the before inventory against what happened."""
    rep = Report(migration_id)
    before = logs_dir(conn, migration_id) / "before.csv"
    if not before.exists():
        write_before(conn, migration_id)
    target = conn.execute("SELECT target FROM migrations WHERE id = ?", (migration_id,)).fetchone()[0]
    items = {(r[0], r[1].lower()): r for r in conn.execute(
        "SELECT src_root, src_rel, action, state, dest_rel, size, keeper_id, error FROM migration_items"
        " WHERE migration_id = ?", (migration_id,))}
    moved_ids = {r[0] for r in conn.execute(
        "SELECT file_id FROM migration_items WHERE migration_id = ? AND action = 'move' AND state IN"
        f" ({','.join('?' * len(MOVED_STATES))})", (migration_id, *MOVED_STATES))}
    cataloged = {(r[0], r[1].lower()): r[2] for r in conn.execute(
        "SELECT root_id, rel_path, CASE WHEN excluded = 1 THEN 'excluded' ELSE 'cataloged' END FROM files")}
    stems_moved = {(r[0], r[1].lower()) for r in conn.execute(
        "SELECT src_root, src_rel FROM migration_items WHERE migration_id = ? AND state IN"
        f" ({','.join('?' * len(MOVED_STATES))})", (migration_id, *MOVED_STATES))}
    from lunelis.importers.formats import CATALOGED_EXTS

    with open(before, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rid, src, rel, size = int(row["root_id"]), row["source"], row["path"], int(row["size"])
            full = os.path.join(src, *rel.split("/"))
            it = items.get((rid, rel.lower()))
            what, why = None, None
            if it:
                act, state, dest, isize, keeper, err = it[2:]
                if act == "move" and state in MOVED_STATES:
                    there = os.path.join(target, *dest.split("/"))
                    if os.path.isfile(there) and os.path.getsize(there) == isize:
                        what = "Copied to the Library"
                    else:
                        why = f"its copy isn't at {there}"
                elif act == "skip_takeout":
                    what = "Left in place - unticked on the Google Takeout page"
                elif act in ("skip_duplicate", "skip_damaged") and keeper in moved_ids:
                    what = ("An identical copy is in the Library" if act == "skip_duplicate"
                            else "Damaged - an intact copy is in the Library")
                else:
                    why = err or f"not copied yet ({state})"
            else:
                ext = rel.rsplit(".", 1)[-1].lower() if "." in rel else ""
                stem = rel[:-4] if rel.lower().endswith(".xmp") else None
                if stem and (rid, stem.lower()) in stems_moved:
                    what = "Sidecar - travelled with its photo"
                elif cataloged.get((rid, rel.lower())) == "excluded":
                    what = "Left in place - excluded from the library"
                elif ext not in CATALOGED_EXTS:
                    what = "Left in place - not a photo or video"
                else:
                    why = "a photo or video that isn't in the migration (added after it was planned?)"
            if why:
                rep.unaccounted.append((full, why))
                rep.rows.append((full, size, "UNACCOUNTED", why))
            else:
                rep.counts[what] += 1
                rep.rows.append((full, size, what, ""))
    if rep.unaccounted:
        rep.counts["UNACCOUNTED"] = len(rep.unaccounted)
    if write:
        d = logs_dir(conn, migration_id)
        _write(d / "report.csv", ["source", "size", "accounted", "why_not"], rep.rows)
        (d / "summary.txt").write_text(rep.text(), encoding="utf-8")
    return rep
