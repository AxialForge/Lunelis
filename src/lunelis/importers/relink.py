"""
Moved-file re-linking (Phase 1, Step 9).

A file moved or renamed outside Lunelis looks, to the scanner, like one file
going missing and a new one appearing. Without this, the missing entry keeps
the ratings, labels, EXIF and thumbnail and the new one starts blank. Here
each missing entry ("orphan") is matched to a new one ("newcomer") and the
orphan takes over the new location; the newcomer's blank row is dropped.

Matching, strongest evidence first - only UNIQUE 1:1 matches are linked:
  1. size + mtime            no reads; moves and Explorer copies keep mtime
  2. size + capture time + name  after the metadata pass (identical bytes have
                             identical EXIF)
  3. size + sample hash      when the orphan had been hashed earlier (reads
                             ~200 KB of each size-matching newcomer)

A newcomer that already carries user data (a rating, label, album, face,
event) is never merged - that would be two real entries, not one file moved.
"""
from __future__ import annotations

import os
import sqlite3
from collections import defaultdict
from dataclasses import dataclass

from lunelis.dupes.hashing import sample_hash

USER_DATA = """
    EXISTS (SELECT 1 FROM ratings WHERE file_id = f.id)
    OR EXISTS (SELECT 1 FROM album_files WHERE file_id = f.id)
    OR EXISTS (SELECT 1 FROM file_tags WHERE file_id = f.id)
    OR EXISTS (SELECT 1 FROM faces WHERE file_id = f.id)
    OR EXISTS (SELECT 1 FROM event_files WHERE file_id = f.id)
"""


@dataclass
class RelinkResult:
    linked: int = 0
    by_method: dict | None = None


def _orphans(conn):
    return conn.execute(
        "SELECT f.id, f.root_id, f.rel_path, f.filename, f.size_bytes, f.mtime, e.captured_at,"
        "       f.sample_hash"
        " FROM files f LEFT JOIN exif e ON e.file_id = f.id"
        " WHERE f.missing_since IS NOT NULL AND f.quarantined_at IS NULL").fetchall()


def _newcomers(conn, sizes: set[int]):
    """Live entries with no user data whose size matches some orphan."""
    rows = []
    sizes = sorted(sizes)
    for start in range(0, len(sizes), 500):
        chunk = sizes[start:start + 500]
        rows += conn.execute(
            f"SELECT f.id, f.root_id, f.rel_path, f.filename, f.size_bytes, f.mtime, e.captured_at,"
            f"       f.sample_hash, r.path"
            f" FROM files f JOIN roots r ON r.id = f.root_id LEFT JOIN exif e ON e.file_id = f.id"
            f" WHERE f.missing_since IS NULL AND f.excluded = 0 AND f.quarantined_at IS NULL"
            f" AND f.size_bytes IN ({','.join('?' * len(chunk))}) AND NOT ({USER_DATA})", chunk).fetchall()
    return rows


def _unique_pairs(orphans, newcomers, key) -> list[tuple]:
    """(orphan, newcomer) pairs whose key is unique on BOTH sides."""
    o_by, n_by = defaultdict(list), defaultdict(list)
    for o in orphans:
        k = key(o)
        if k is not None:
            o_by[k].append(o)
    for n in newcomers:
        k = key(n)
        if k is not None:
            n_by[k].append(n)
    return [(os_[0], n_by[k][0]) for k, os_ in o_by.items() if len(os_) == 1 and len(n_by.get(k, [])) == 1]


def merge(conn: sqlite3.Connection, orphan_id: int, newcomer_id: int, method: str,
          *, sidecar_store=None, thumbnail_cache=None) -> None:
    """The orphan entry takes over the newcomer's location; the newcomer's
    blank row (and anything derived from it: EXIF, thumbnail) goes."""
    new = conn.execute(
        "SELECT f.root_id, f.rel_path, f.filename, f.sidecar, f.sidecar_mtime, f.mtime, r.path,"
        "       f.thumbnail_path FROM files f JOIN roots r ON r.id = f.root_id WHERE f.id = ?",
        (newcomer_id,)).fetchone()
    old = conn.execute(
        "SELECT f.root_id, f.rel_path, f.filename, r.path FROM files f JOIN roots r ON r.id = f.root_id"
        " WHERE f.id = ?", (orphan_id,)).fetchone()
    to_root, to_path, to_name, sidecar, sidecar_mtime, mtime, to_root_path, new_thumb = new
    from_root, from_path, from_name, from_root_path = old

    conn.execute("DELETE FROM files WHERE id = ?", (newcomer_id,))        # cascades exif, groups
    conn.execute(
        "UPDATE files SET root_id = ?, rel_path = ?, filename = ?, sidecar = ?, sidecar_mtime = ?,"
        " mtime = ?, missing_since = NULL WHERE id = ?",
        (to_root, to_path, to_name, sidecar, sidecar_mtime, mtime, orphan_id))
    conn.execute("INSERT INTO file_moves (file_id, from_root, from_path, to_root, to_path, method)"
                 " VALUES (?, ?, ?, ?, ?, ?)", (orphan_id, from_root, from_path, to_root, to_path, method))
    conn.commit()

    # Lunelis's own files follow the entry: the central sidecar is keyed by
    # location, the newcomer's thumbnail (keyed by its id) is now an orphan.
    if sidecar_store is not None:
        from lunelis.xmp.sync import central_path
        src = central_path(sidecar_store, from_root, from_root_path, from_path, from_name)
        if os.path.exists(src):
            dst = central_path(sidecar_store, to_root, to_root_path, to_path, to_name)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.replace(src, dst)
    if thumbnail_cache is not None and new_thumb:
        try:
            os.unlink(os.path.join(thumbnail_cache, *new_thumb.split("/")))
        except OSError:
            pass


def relink(conn: sqlite3.Connection, *, use_hashes: bool = True,
           sidecar_store=None, thumbnail_cache=None) -> RelinkResult:
    """Run every tier in turn; call after scanning (tier 1) and again after
    the metadata pass (tiers 2-3). Cheap when nothing is missing."""
    result = RelinkResult(0, defaultdict(int))
    orphans = _orphans(conn)
    if not orphans:
        return result
    newcomers = _newcomers(conn, {o[4] for o in orphans})
    linked_o, linked_n = set(), set()

    def run_tier(method, pairs):
        for o, n in pairs:
            if o[0] in linked_o or n[0] in linked_n:
                continue
            merge(conn, o[0], n[0], method, sidecar_store=sidecar_store, thumbnail_cache=thumbnail_cache)
            linked_o.add(o[0])
            linked_n.add(n[0])
            result.linked += 1
            result.by_method[method] += 1

    def remaining():
        return ([o for o in orphans if o[0] not in linked_o],
                [n for n in newcomers if n[0] not in linked_n])

    run_tier("size+mtime", _unique_pairs(orphans, newcomers, lambda r: (r[4], r[5])))
    os_, ns = remaining()
    run_tier("exif", _unique_pairs(os_, ns, lambda r: (r[4], r[6], r[3].lower()) if r[6] else None))

    if use_hashes:
        os_, ns = remaining()
        hashed = [o for o in os_ if o[7]]
        sizes = {o[4] for o in hashed}
        for n in ns:
            if n[7] is None and n[4] in sizes:
                try:
                    digest, _ = sample_hash(os.path.join(n[8], *n[2].split("/")), n[4])
                except OSError:
                    continue
                conn.execute("UPDATE files SET sample_hash = ? WHERE id = ?", (digest, n[0]))
                n_list = list(n)
                n_list[7] = digest
                ns[ns.index(n)] = tuple(n_list)
        conn.commit()
        run_tier("hash", _unique_pairs(hashed, ns, lambda r: (r[4], r[7]) if r[7] else None))
    result.by_method = dict(result.by_method)
    return result
