"""
Near-duplicates: the same photo saved as a different file - resized,
re-compressed, re-encoded by Google Takeout, exported from an editor.
Byte-identical copies are the exact pass (detect.py); this finds the rest.

1. Every photo gets a perceptual fingerprint (files.perceptual_hash): a
   64-bit difference hash (dHash) of its thumbnail - the local 512 px cache,
   so it costs no NAS reads. Visually identical images get (nearly) equal
   fingerprints whatever their size or JPEG quality.
2. Fingerprints within MAX_DISTANCE bits are candidates (found with a
   5-chunk multi-index: two 64-bit hashes that differ in <= 4 bits share at
   least one of 5 chunks exactly, so only bucket-mates are compared).
3. A candidate pair is the SAME photo only if nothing contradicts it
   (rules tuned on the real 159k library, 2026-09-27):
   - RAWs never: a RAW's copies are byte-identical (the exact pass's job),
     and a RAW + its JPEG are different files on purpose;
   - same size = the exact pass's job (byte-identical copies), not this one;
   - capture times must be the SAME moment - equal to the sub-second when
     both have sub-seconds, else to the second. Burst frames 0.125 s apart
     look identical but are different shots;
   - the names are related: equal, or one inside the other (an export
     "20191012-_DSC0040.jpg" of "_DSC0040.JPG"). Copies keep their name;
     unrelated names at one moment are a burst or another photo;
   - the aspect ratio agrees (a crop is a different picture);
   - near-blank fingerprints (black frames, flat sky) are skipped.
   Groups are complete: every member matches every other directly, so a
   chain A~B~C never pulls in a C unlike A (it made 3,000-photo groups).
   Edited exports (a folder or name with "edit"/"export") do group with
   their original, but are never suggested as the copy to set aside.
4. Groups are stored as duplicate_groups method 'similar', rebuilt each run.

Nothing is moved here. Setting copies aside is quarantine_similar(), always
the user's explicit decision, one group at a time or all at once.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from PIL import Image

MAX_DISTANCE = 4
BUCKET_CAP = 400            # a huge bucket (black frames, clear sky) says nothing - skip it
TIME_TOLERANCE_S = 2.0
LIVE = "f.missing_since IS NULL AND f.excluded = 0 AND f.quarantined_at IS NULL"


def dhash(img: Image.Image) -> str:
    """64-bit difference hash as 16 hex digits."""
    g = img.convert("L").resize((9, 8), Image.Resampling.BILINEAR)
    px = g.load()
    bits = 0
    for y in range(8):
        for x in range(8):
            bits = (bits << 1) | (px[x, y] > px[x + 1, y])
    return f"{bits:016x}"


def fingerprint_file(path: Path) -> str | None:
    try:
        with Image.open(path) as im:
            im.draft("L", (64, 64))
            return dhash(im)
    except Exception:
        return None


def compute_missing(conn: sqlite3.Connection, cache_dir: Path, *, workers: int = 8,
                    should_cancel: Callable[[], bool] | None = None,
                    on_progress: Callable[[int, int], None] | None = None) -> int:
    """Fingerprint every photo that has a thumbnail but no fingerprint yet.
    Returns how many fingerprints were written."""
    rows = conn.execute(f"SELECT f.id, f.thumbnail_path FROM files f WHERE {LIVE}"
                        " AND f.thumbnail_path IS NOT NULL AND f.perceptual_hash IS NULL").fetchall()
    done = written = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for start in range(0, len(rows), 2000):
            if should_cancel and should_cancel():
                break
            chunk = rows[start:start + 2000]
            hashes = list(pool.map(lambda r: (fingerprint_file(cache_dir / r[1]), r[0]), chunk))
            conn.executemany("UPDATE files SET perceptual_hash = ? WHERE id = ?",
                             [(h, fid) for h, fid in hashes if h])
            conn.commit()
            done += len(chunk)
            written += sum(1 for h, _ in hashes if h)
            if on_progress:
                on_progress(done, len(rows))
    return written


def _chunks(h: int) -> list[int]:
    # 5 chunks (13,13,13,13,12 bits), each tagged with its index.
    out, shift = [], 0
    for i, width in enumerate((13, 13, 13, 13, 12)):
        out.append((i << 16) | ((h >> shift) & ((1 << width) - 1)))
        shift += width
    return out


def _t(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso[:26]).timestamp()
    except ValueError:
        return None


def find_groups(conn: sqlite3.Connection, max_distance: int = MAX_DISTANCE) -> list[list[int]]:
    """Groups (lists of file ids) of photos that are the same image."""
    rows = conn.execute(
        f"SELECT f.id, f.perceptual_hash, e.captured_at, e.width_px, e.height_px, e.orientation,"
        f"       f.root_id, f.rel_path, f.filename, e.camera_model, f.size_bytes"
        f" FROM files f JOIN roots r ON r.id = f.root_id LEFT JOIN exif e ON e.file_id = f.id"
        f" WHERE {LIVE} AND r.enabled = 1 AND f.perceptual_hash IS NOT NULL AND f.is_raw = 0").fetchall()
    info = {}
    buckets: dict[int, list[int]] = defaultdict(list)
    for fid, ph, taken, w, h, orient, root, rel, name, camera, size in rows:
        hv = int(ph, 16)
        if not 8 <= bin(hv).count("1") <= 56:
            continue                       # a near-blank fingerprint (black frame, flat sky) proves nothing
        if (orient or 1) >= 5:
            w, h = h, w
        folder = rel.rsplit("/", 1)[0] if "/" in rel else ""
        info[fid] = (hv, taken, (w / h) if w and h else None, (root, folder.lower()),
                     name.lower(), camera, size)
        for c in _chunks(hv):
            buckets[c].append(fid)

    # Candidate pairs, then groups where EVERY member matches every other
    # (no chaining: A~B and B~C must not pull in an unrelated C).
    matches: dict[int, set[int]] = defaultdict(set)
    seen = set()
    for members in buckets.values():
        if len(members) < 2 or len(members) > BUCKET_CAP:
            continue
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                if (a, b) in seen:
                    continue
                seen.add((a, b))
                if same_photo(info[a], info[b], max_distance):
                    matches[a].add(b)
                    matches[b].add(a)
    grouped: set[int] = set()
    out = []
    for seed in sorted(matches, key=lambda f: (-len(matches[f]), f)):
        if seed in grouped:
            continue
        group = [seed]
        for cand in sorted(matches[seed] - grouped):
            if all(cand in matches[m] for m in group):
                group.append(cand)
        if len(group) > 1:
            grouped.update(group)
            out.append(sorted(group))
    return out


def same_moment(ta: str | None, tb: str | None) -> bool:
    # Equal capture times - to the sub-second when both have one.
    if not ta or not tb:
        return True                        # one side has no date (e.g. an export): can't contradict
    if "." in ta[19:] and "." in tb[19:]:
        return ta[:23] == tb[:23]
    return ta[:19] == tb[:19]


SAME_NAME_HOURS = 36
EDITED_DISTANCE = 16          # same name, shifted time: an edited copy of the same shot


def shifted_copy(ta: str | None, tb: str | None) -> bool:
    """Times a minute to SAME_NAME_HOURS apart: a time-zone or upload shift."""
    try:
        gap = abs((datetime.fromisoformat(ta[:19]) - datetime.fromisoformat(tb[:19])).total_seconds())
    except (TypeError, ValueError):
        return False
    return 60 < gap <= SAME_NAME_HOURS * 3600


def related_names(a: str, b: str) -> bool:
    # 'dsc0040.jpg' ~ '20191012-_dsc0040.jpg' (an export keeps the original's name inside its own).
    sa, sb = a.rsplit(".", 1)[0], b.rsplit(".", 1)[0]
    return sa == sb or sa in sb or sb in sa


def same_photo(a, b, max_distance: int) -> bool:
    ha, ta, ra, fa, na, ca, size_a = a
    hb, tb, rb, fb, nb, cb, size_b = b
    # The camera's own name, a few hours off (a Google Takeout copy): an edited
    # version of the shot may differ more than a plain re-encode (0.44 rehearsal:
    # 5-14 bits on airshow edits).
    shifted = na == nb and shifted_copy(ta, tb)
    if bin(ha ^ hb).count("1") > (EDITED_DISTANCE if shifted else max_distance):
        return False
    if size_a == size_b:
        return False                       # same size: byte-identical copies, the exact pass's job
    if not same_moment(ta, tb) and not shifted:
        # A different moment (e.g. the next burst frame) - unless it's the same file name
        # a few hours off: Google Takeout copies of edited exports came back 8-12 hours
        # away from the camera's time (found in the 0.44 rehearsal). Under a minute
        # apart is a burst frame, never a shifted copy.
        return False
    if not related_names(na, nb):
        return False                       # copies keep their name (Takeout, exports, pool copies);
                                           # unrelated names are other photos - or a burst
    if ra and rb and abs(ra - rb) > 0.02 * max(ra, rb):
        return False                       # cropped differently: not the same picture
    return True


def is_edit(rel_path: str) -> bool:
    # An edited export - kept, never suggested as the copy to set aside.
    low = rel_path.lower()
    return any(w in low for w in ("edit", "export"))


def rebuild(conn: sqlite3.Connection, groups: list[list[int]]) -> int:
    """Replace all 'similar' groups with these."""
    conn.execute("DELETE FROM duplicate_group_files WHERE group_id IN"
                 " (SELECT id FROM duplicate_groups WHERE method = 'similar')")
    conn.execute("DELETE FROM duplicate_groups WHERE method = 'similar'")
    for g in groups:
        gid = conn.execute("INSERT INTO duplicate_groups (method, hash_key, verified) VALUES ('similar', ?, 0)",
                           (",".join(map(str, g)),)).lastrowid
        conn.executemany("INSERT INTO duplicate_group_files (group_id, file_id) VALUES (?, ?)",
                         [(gid, fid) for fid in g])
    conn.commit()
    return len(groups)


def refresh(conn: sqlite3.Connection, cache_dir: Path, *, should_cancel=None, on_progress=None) -> int | None:
    """Fingerprint new photos and, if any were added, regroup. Returns the
    number of groups, or None when nothing changed."""
    if not compute_missing(conn, cache_dir, should_cancel=should_cancel, on_progress=on_progress):
        return None
    if should_cancel and should_cancel():
        return None
    return rebuild(conn, find_groups(conn))


@dataclass
class SimilarGroup:
    id: int
    members: list[tuple]          # (file id, root path, rel path, thumbnail, size, width, height), best first
    keeper: int
    extras: list[int]             # the copies suggested to set aside (never an edit, never the keeper)

    @property
    def extra_bytes(self) -> int:
        return sum(m[4] or 0 for m in self.members if m[0] in self.extras)


def _stem(rel: str) -> str:
    return rel.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()


def suggest(keeper: tuple, m: tuple, takeout_paths: set[str]) -> bool:
    """Is member m a copy worth suggesting to set aside next to the kept one?
    Only when it's plainly a lesser copy: fewer pixels, a Takeout re-encode,
    or the very same name. A same-size copy under another name
    ('DSC00913.jpeg' beside a bigger 'DSC00913 2.jpeg') may be the original
    of an edit - it stays unsuggested, and the user can still tick it."""
    if is_edit(m[2]):
        return False
    pixels = lambda x: (x[5] or 0) * (x[6] or 0)  # noqa: E731
    return pixels(m) < pixels(keeper) or m[1] in takeout_paths or _stem(m[2]) == _stem(keeper[2])


def load(conn: sqlite3.Connection, preferred_roots: list[int] | None = None) -> list[SimilarGroup]:
    """Every near-duplicate group with 2+ live members, best copy first."""
    rows = conn.execute(
        f"SELECT m.group_id, f.id, r.path, f.rel_path, f.thumbnail_path, f.size_bytes, e.width_px, e.height_px"
        f" FROM duplicate_groups g JOIN duplicate_group_files m ON m.group_id = g.id"
        f" JOIN files f ON f.id = m.file_id JOIN roots r ON r.id = f.root_id"
        f" LEFT JOIN exif e ON e.file_id = f.id"
        f" WHERE g.method = 'similar' AND {LIVE}").fetchall()
    by: dict[int, list] = defaultdict(list)
    for gid, *member in rows:
        by[gid].append(tuple(member))
    rank = keeper_rank(conn, preferred_roots)
    from lunelis.importers.takeout import takeout_roots
    takeout = {r for (r,) in conn.execute(
        f"SELECT path FROM roots WHERE id IN ({','.join(map(str, takeout_roots(conn))) or 'NULL'})")}
    out = []
    for gid, members in by.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda m: rank(m[0]))
        keeper = members[0]
        extras = [m[0] for m in members[1:] if suggest(keeper, m, takeout)]
        out.append(SimilarGroup(gid, members, keeper[0], extras))
    out.sort(key=lambda g: (-len(g.extras), -g.extra_bytes, g.id))
    return out


# --- which copy to keep -------------------------------------------------------------

def keeper_rank(conn: sqlite3.Connection, preferred_roots: list[int] | None = None):
    """Sort key for file ids: best copy first. Not damaged, then the most
    pixels, then not a Takeout re-encode, then a preferred source, then the
    biggest file (least compressed), then the shallowest path."""
    from lunelis.importers.takeout import takeout_roots
    takeout = set(takeout_roots(conn))
    order = {rid: i for i, rid in enumerate(preferred_roots or [])}

    def rank(fid: int):
        r = conn.execute(
            "SELECT f.root_id, f.rel_path, f.size_bytes, e.width_px, e.height_px,"
            " EXISTS (SELECT 1 FROM damaged d WHERE d.file_id = f.id)"
            " FROM files f LEFT JOIN exif e ON e.file_id = f.id WHERE f.id = ?", (fid,)).fetchone()
        root, rel, size, w, h, damaged = r
        return (bool(damaged), is_edit(rel), -((w or 0) * (h or 0)), root in takeout,
                order.get(root, len(order)), -(size or 0), rel.count("/"), fid)
    return rank


def quarantine_similar(conn: sqlite3.Connection, group_id: int, file_ids: list[int], *,
                       backup_dir: Path | None = None) -> list[str]:
    """Set near-duplicate copies aside (same-drive rename into the quarantine
    folder, never a delete). Never the last copy of a group; ratings, labels,
    events and albums of the set-aside copies are merged into the kept one."""
    from lunelis.dupes.quarantine import QuarantineRefused, _now
    from lunelis.migrate.execute import merge_user_data
    g = conn.execute("SELECT method FROM duplicate_groups WHERE id = ?", (group_id,)).fetchone()
    if not g or g[0] != "similar":
        raise QuarantineRefused("not a near-duplicate group")
    members = [r[0] for r in conn.execute(
        f"SELECT f.id FROM duplicate_group_files m JOIN files f ON f.id = m.file_id"
        f" WHERE m.group_id = ? AND {LIVE}", (group_id,))]
    targets = [f for f in file_ids if f in members]
    keep = [f for f in members if f not in targets]
    if not keep:
        raise QuarantineRefused("that would leave no copy - keep at least one")
    if backup_dir is not None:
        from lunelis.catalog.backup import snapshot
        snapshot(conn, backup_dir, "before-quarantine")
    keeper = min(keep, key=keeper_rank(conn))          # the best remaining copy gets the user data
    moved = []
    for fid in targets:
        root, rel, sidecar = conn.execute("SELECT r.path, f.rel_path, f.sidecar FROM files f"
                                          " JOIN roots r ON r.id = f.root_id WHERE f.id = ?", (fid,)).fetchone()
        merge_user_data(conn, fid, keeper)
        from lunelis.dupes.quarantine import move_pair, quarantine_paths
        src, dst, s_src, s_dst = quarantine_paths(root, rel, sidecar, fid)
        move_pair(src, dst, s_src, s_dst)                  # photo and sidecar together, or neither
        conn.execute("UPDATE files SET quarantined_at = ?, quarantine_path = ? WHERE id = ?", (_now(), dst, fid))
        conn.commit()
        moved.append(dst)
    return moved
