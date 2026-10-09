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

    from lunelis.migrate.execute import carry_sidecar, merge_user_data
    keep = [fid for fid in members if fid not in targets]
    marked = {r[0] for r in conn.execute("SELECT file_id FROM duplicate_group_files WHERE group_id = ?"
                                         " AND is_keeper = 1", (group_id,))}
    keeper = next((fid for fid in keep if fid in marked), min(keep))
    moved = []
    for fid in targets:
        root, rel, sidecar = members[fid]
        src, dst, s_src, s_dst = quarantine_paths(root, rel, sidecar, fid)
        merge_user_data(conn, fid, keeper)                # the kept copy gets its stars, albums, tags...
        carry_sidecar(conn, fid, keeper)                  # ...and its sidecar, if it has none
        move_pair(src, dst, s_src, s_dst)
        conn.execute("UPDATE files SET quarantined_at = ?, quarantine_path = ? WHERE id = ?",
                     (_now(), dst, fid))
        conn.commit()                             # per file: a crash can't lose track of one
        moved.append(dst)
    # The group now holds one live copy (or more): keep it, it records what happened.
    return moved


def quarantine_paths(root: str, rel: str, sidecar: str | None, fid: int) -> tuple[str, str, str | None, str | None]:
    """(photo, its quarantine place, sidecar, the sidecar's quarantine place).
    When a name is taken there, the copy and its sidecar both get the id -
    decided before anything moves (a clash half-way would lose track of one)."""
    src = os.path.join(root, *rel.split("/"))
    dst = os.path.join(root, QUARANTINE_DIR, *rel.split("/"))
    s_src = os.path.join(os.path.dirname(src), sidecar) if sidecar else None
    if s_src and not os.path.exists(s_src):
        s_src = None
    s_name = sidecar
    if os.path.exists(dst) or (sidecar and os.path.exists(os.path.join(os.path.dirname(dst), sidecar))):
        base, ext = os.path.splitext(dst)
        dst = f"{base} ({fid}){ext}"
        if sidecar:
            s_name = os.path.basename(dst) + sidecar[len(os.path.basename(src)):] \
                if sidecar.lower().startswith(os.path.basename(src).lower()) else f"({fid}) {sidecar}"
    return src, dst, s_src, (os.path.join(os.path.dirname(dst), s_name) if s_src else None)


def move_pair(src: str, dst: str, s_src: str | None, s_dst: str | None) -> None:
    """Rename a photo and its sidecar together: both move or neither does.
    Everything is checked before the first rename; a sidecar that then fails
    to move puts the photo back."""
    if os.path.exists(dst):
        raise QuarantineRefused(f"something already exists at {dst}")
    if s_src and s_dst and os.path.exists(s_dst):
        raise QuarantineRefused(f"something already exists at {s_dst}")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    move_one(src, dst)                            # same volume: a rename; another: copy, compare, then remove
    if s_src and s_dst:
        try:
            move_one(s_src, s_dst)
        except OSError:
            move_one(dst, src)                    # undo: the pair stays together where it was
            raise


def move_one(src: str, dst: str) -> None:
    """Move one file. A rename where it can be (instant); across drives or
    shares (the Lunelis folder's Duplicates / Trash) the file is copied, the
    copy compared byte for byte with the original, and only then is the
    original removed - a copy that doesn't match is deleted and the
    original stays."""
    try:
        os.rename(src, dst)
        return
    except OSError as e:
        if not (getattr(e, "winerror", None) == 17 or e.errno == 18):   # ERROR_NOT_SAME_DEVICE / EXDEV
            raise
    import shutil
    # Via a temp name (0.51): a share that drops mid-copy leaves no cut-off
    # file under the real name that would later look like a good copy.
    tmp = os.path.join(os.path.dirname(dst), ".lunelis-moving-" + os.path.basename(dst))
    try:
        shutil.copy2(src, tmp)
        if not _same_bytes(src, tmp):
            raise OSError(f"the copy at {dst} didn't match the original - the original was left in place")
        os.replace(tmp, dst)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    os.remove(src)


def _same_bytes(a: str, b: str, chunk: int = 4 * 1024 * 1024) -> bool:
    if os.path.getsize(a) != os.path.getsize(b):
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            x, y = fa.read(chunk), fb.read(chunk)
            if x != y:
                return False
            if not x:
                return True


def _quarantined_sidecar(qpath: str, rel: str, sidecar: str, file_id: int) -> str | None:
    """Where quarantine() put this file's sidecar (renamed with the id on a clash)."""
    folder, qname, name = os.path.dirname(qpath), os.path.basename(qpath), os.path.basename(rel)
    cands = [sidecar]
    if sidecar.lower().startswith(name.lower()):
        cands.append(qname + sidecar[len(name):])
    cands.append(f"({file_id}) {sidecar}")
    for c in cands:
        if os.path.exists(os.path.join(folder, c)):
            return os.path.join(folder, c)
    return None


def restore(conn: sqlite3.Connection, file_id: int) -> str:
    """Move a quarantined file (and its sidecar) back where it was."""
    row = conn.execute(
        "SELECT r.path, f.rel_path, f.quarantine_path, f.sidecar FROM files f"
        " JOIN roots r ON r.id = f.root_id WHERE f.id = ? AND f.quarantined_at IS NOT NULL",
        (file_id,)).fetchone()
    if row is None:
        raise QuarantineRefused("that file isn't in quarantine")
    root, rel, qpath, sidecar = row
    dst = os.path.join(root, *rel.split("/"))
    s_src = _quarantined_sidecar(qpath, rel, sidecar, file_id) if sidecar else None
    move_pair(qpath, dst, s_src, os.path.join(os.path.dirname(dst), sidecar) if s_src else None)
    conn.execute("UPDATE files SET quarantined_at = NULL, quarantine_path = NULL,"
                 " missing_since = NULL WHERE id = ?", (file_id,))
    conn.commit()
    return dst
