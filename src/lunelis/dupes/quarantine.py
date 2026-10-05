"""
Quarantine: the only way a job ever "removes" a photo.

A quarantined file is renamed into `<root>/_Lunelis Quarantine/<rel_path>` -
the same share/drive, so it's instant and needs no free space - together with
its sidecar if one sits next to it. Nothing is deleted; emptying the
quarantine is the user's decision, made on the Quarantine page (dupes/manage.py).
`restore()` puts a file back.

Safety rules, enforced here rather than trusted to callers:
- only members of a VERIFIED ('exact') group can be quarantined;
- never the last live copy in the group;
- the catalog is snapshotted before the first move of a batch.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

QUARANTINE_DIR = "_Lunelis Quarantine"


class QuarantineRefused(Exception):
    pass


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def quarantine(conn: sqlite3.Connection, group_id: int, file_ids: list[int], *,
               backup_dir: Path | None = None) -> list[str]:
    """Move the given members of a verified group into quarantine. Returns
    the new paths. Refuses (and moves nothing) if the rules above fail."""
    g = conn.execute("SELECT method, verified FROM duplicate_groups WHERE id = ?", (group_id,)).fetchone()
    if not g or g[0] != "exact" or not g[1]:
        raise QuarantineRefused("only verified byte-identical groups can be quarantined")
    members = {fid: (root, rel, sidecar) for fid, root, rel, sidecar in conn.execute(
        "SELECT f.id, r.path, f.rel_path, f.sidecar FROM duplicate_group_files m"
        " JOIN files f ON f.id = m.file_id JOIN roots r ON r.id = f.root_id"
        " WHERE m.group_id = ? AND f.quarantined_at IS NULL AND f.missing_since IS NULL AND f.excluded = 0",
        (group_id,))}
    targets = [fid for fid in file_ids if fid in members]
    if len(targets) != len(set(file_ids)):
        raise QuarantineRefused("a file isn't a live member of this group")
    if len(members) - len(targets) < 1:
        raise QuarantineRefused("that would leave no copy - keep at least one")

    if backup_dir is not None:
        from lunelis.catalog.backup import snapshot
        snapshot(conn, backup_dir, "before-quarantine")

    moved = []
    for fid in targets:
        root, rel, sidecar = members[fid]
        src = os.path.join(root, *rel.split("/"))
        dst = os.path.join(root, QUARANTINE_DIR, *rel.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        s_src = os.path.join(os.path.dirname(src), sidecar) if sidecar else None
        s_name = sidecar
        if os.path.exists(dst) or (sidecar and os.path.exists(os.path.join(os.path.dirname(dst), sidecar))):
            # Taken already: this copy and its sidecar both get the id, decided
            # before anything moves (a clash half-way would lose track of one).
            base, ext = os.path.splitext(dst)
            dst = f"{base} ({fid}){ext}"
            if sidecar:
                s_name = os.path.basename(dst) + sidecar[len(os.path.basename(src)):] \
                    if sidecar.lower().startswith(os.path.basename(src).lower()) else f"({fid}) {sidecar}"
        os.rename(src, dst)                       # same volume: instant, no copy
        if s_src and os.path.exists(s_src):
            os.rename(s_src, os.path.join(os.path.dirname(dst), s_name))
        conn.execute("UPDATE files SET quarantined_at = ?, quarantine_path = ? WHERE id = ?",
                     (_now(), dst, fid))
        conn.commit()                             # per file: a crash can't lose track of one
        moved.append(dst)
    # The group now holds one live copy (or more): keep it, it records what happened.
    return moved


def restore(conn: sqlite3.Connection, file_id: int) -> str:
    """Move a quarantined file (and its sidecar) back where it was."""
    root, rel, qpath, sidecar = conn.execute(
        "SELECT r.path, f.rel_path, f.quarantine_path, f.sidecar FROM files f"
        " JOIN roots r ON r.id = f.root_id WHERE f.id = ? AND f.quarantined_at IS NOT NULL",
        (file_id,)).fetchone()
    dst = os.path.join(root, *rel.split("/"))
    if os.path.exists(dst):
        raise QuarantineRefused(f"something already exists at {dst}")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    os.rename(qpath, dst)
    if sidecar:
        s_src = os.path.join(os.path.dirname(qpath), sidecar)
        if os.path.exists(s_src):
            os.rename(s_src, os.path.join(os.path.dirname(dst), sidecar))
    conn.execute("UPDATE files SET quarantined_at = NULL, quarantine_path = NULL,"
                 " missing_since = NULL WHERE id = ?", (file_id,))
    conn.commit()
    return dst
