"""
Card import (Phase 1, Step 11): card -> staging -> library, verified twice.

1. STAGE   Copy every photo/video off the card into a staging folder, hashing
           the card copy while reading it, then re-read the staged copy and
           compare. Staging is local (fast, so the card is free quickly) until
           the local disk would drop below `import_local_reserve_gb` free; then
           it spills over to the network staging folder. When every file is
           staged and verified the card can be removed.
2. PLACE   Read each staged file's metadata, pick its folder with the storage
           template, copy it into the library, verify the library copy against
           the card hash, and only then delete the staged copy. When every file
           is placed (or found already in the library) the card is safe to format.

Files are NEVER renamed. A different file whose name is already taken in the
target folder goes to a sibling folder ("6-19-2026 (2)"); an identical one
means it's already in the library and is skipped.

Everything is recorded per file in `import_items`, so an import interrupted
by a crash or power cut resumes where it stopped: staging needs the card
back, placing doesn't.
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from lunelis.dupes.hashing import SourceOffline, is_network_error, sample_hash
from lunelis.importers.formats import is_cataloged
from lunelis.importing import profiles as camera_profiles
from lunelis.importing.templates import Context, render, sibling

CHUNK = 4 * 1024 * 1024
TMP_PREFIX = ".lunelis-importing-"

# Where cameras keep media on a card comes from the camera profiles
# (importing/camera_profiles.json): DCIM for stills and most video; Sony puts
# XAVC video under PRIVATE/M4ROOT/CLIP (with XML sidecars) and AVCHD under
# PRIVATE/AVCHD. Kept for callers that only need the folder names.
CARD_MEDIA_DIRS = ("DCIM", "PRIVATE/M4ROOT/CLIP", "PRIVATE/AVCHD")

ProgressFn = Callable[[int, int, str], None]
StopFn = Callable[[], bool]


class NoStagingSpace(Exception):
    pass


class WaitingForSource(Exception):
    """The card (for staging) or a share (for placing) isn't there right now."""


@dataclass
class Settings_:
    destination: str | None              # None until the user picks one
    template: str
    staging_local: str
    staging_network: str | None
    reserve_bytes: int


def load_settings(conn: sqlite3.Connection, data_dir: Path) -> Settings_:
    from lunelis.settings import Settings
    s = Settings(conn)
    return Settings_(
        destination=s.get("import_destination"),
        template=s.get("import_template"),
        staging_local=s.get("import_staging_local") or str(data_dir / "staging"),
        staging_network=s.get("import_staging_network"),
        reserve_bytes=int(s.get("import_local_reserve_gb") * 1e9),
    )


# --- cards ---------------------------------------------------------------------

def volume_info(root: str) -> tuple[str | None, str | None]:
    """(serial, label) of the volume holding `root` - identifies a card."""
    if sys.platform != "win32":
        return None, None
    drive = os.path.splitdrive(os.path.abspath(root))[0] + "\\"
    label = ctypes.create_unicode_buffer(261)
    serial = ctypes.c_uint32()
    ok = ctypes.windll.kernel32.GetVolumeInformationW(
        drive, label, 261, ctypes.byref(serial), None, None, None, 0)
    return (f"{serial.value:08X}", label.value or None) if ok else (None, None)


def removable_drives_with_media() -> list[str]:
    """Removable drives (SD / CF / USB readers) that have a DCIM or PRIVATE folder."""
    return [root for root, card in removable_drives().items() if card]


def removable_drives() -> dict[str, bool]:
    """Every removable drive with a disk in it: {root: has camera media}.
    True = a memory card (DCIM / PRIVATE); False = a USB stick or drive."""
    if sys.platform != "win32":
        return {}
    out = {}
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    for i in range(26):
        if not mask & (1 << i):
            continue
        root = f"{chr(65 + i)}:\\"
        if ctypes.windll.kernel32.GetDriveTypeW(root) != 2:      # DRIVE_REMOVABLE
            continue
        if not os.path.isdir(root):                              # an empty card reader slot
            continue
        out[root] = any(os.path.isdir(os.path.join(root, d.split("/")[0])) for d in CARD_MEDIA_DIRS)
    return out


DRIVE_KINDS = {2: "removable", 3: "fixed", 4: "network", 5: "cd"}


def all_drives() -> list[tuple[str, str, str | None, int | None, int | None]]:
    # [(root, kind, label, free bytes, total bytes)] for every drive letter:
    # kind = card (removable with camera media) | removable | fixed | network.
    if sys.platform != "win32":
        return []
    cards = set(removable_drives_with_media())
    out = []
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    for i in range(26):
        if not mask & (1 << i):
            continue
        root = f"{chr(65 + i)}:\\"
        kind = DRIVE_KINDS.get(ctypes.windll.kernel32.GetDriveTypeW(root))
        if kind is None or kind == "cd":
            continue
        if root in cards:
            kind = "card"
        label = volume_info(root)[1] if kind != "network" else None
        try:
            usage = shutil.disk_usage(root)
            free, total = usage.free, usage.total
        except OSError:
            free = total = None                  # e.g. a mapped share that's asleep
        out.append((root, kind, label, free, total))
    return out


def _profile(source: str):
    return profile_for(source)


def profile_for(source: str, sample: int = 8):
    """The card's profile from its layout; for a folder with no layout to go
    on (copied off a phone), from the EXIF Make of its first few photos."""
    from lunelis import paths
    profs = camera_profiles.load(paths.DATA_DIR)
    found = camera_profiles.detect(source, profs)
    if found.id != "generic":
        return found
    from lunelis.importers.metadata import read_file
    seen = 0
    for dirpath, dirs, files in os.walk(source):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in sorted(files):
            if not is_cataloged(name):
                continue
            try:
                make = read_file(os.path.join(dirpath, name)).get("camera_make")
            except Exception:
                make = None
            by_make = camera_profiles.for_make(make, profs)
            if by_make is not None and by_make.id != "generic":
                return by_make
            seen += 1
            if seen >= sample:
                return found
    return found


def discover(source: str, profile=None) -> list[tuple[str, int, float]]:
    """[(rel path, size, mtime)] of every photo/video to import. On a camera
    card only its profile's media folders are read (never its skip folders:
    proxies, thumbnails); any other folder is read whole."""
    profile = profile or _profile(source)
    skip = {os.path.normcase(os.path.join(source, *d.split("/"))) for d in profile.skip_dirs}
    roots = [os.path.join(source, *d.split("/")) for d in profile.media_dirs
             if os.path.isdir(os.path.join(source, *d.split("/")))] or [source]
    out = []
    for top in roots:
        for dirpath, dirs, files in os.walk(top):
            dirs[:] = [d for d in dirs if not d.startswith(".")
                       and os.path.normcase(os.path.join(dirpath, d)) not in skip]
            for name in files:
                if is_cataloged(name):
                    full = os.path.join(dirpath, name)
                    st = os.stat(full)
                    out.append((os.path.relpath(full, source).replace("\\", "/"), st.st_size, st.st_mtime))
    return sorted(out)


def sidecars_of(source: str, media: list[tuple[str, int, float]], profile=None) -> dict[str, list[tuple[str, int, float]]]:
    """{media rel: [(sidecar rel, size, mtime)]} - the files that belong to each
    photo or clip (Sony C0001.MP4 -> C0001M01.XML), by the profile's rules."""
    profile = profile or _profile(source)
    out: dict[str, list[tuple[str, int, float]]] = {}
    listing: dict[str, dict[str, str]] = {}          # folder -> {lower name: real name}
    for rel, _, _ in media:
        folder, name = (rel.rsplit("/", 1) if "/" in rel else ("", rel))
        if folder not in listing:
            try:
                listing[folder] = {n.lower(): n for n in os.listdir(os.path.join(source, *folder.split("/")))}
            except OSError:
                listing[folder] = {}
        found = []
        for cand in profile.sidecar_names(name):
            real = listing[folder].get(cand.lower())
            if real and real != name and not is_cataloged(real):
                srel = f"{folder}/{real}" if folder else real
                if all(srel != f[0] for f in found):
                    st = os.stat(os.path.join(source, *srel.split("/")))
                    found.append((srel, st.st_size, st.st_mtime))
        if found:
            out[rel] = found
    return out


def companions_of(source: str, media: list[tuple[str, int, float]], profile=None) -> dict[str, list[tuple[str, int, float]]]:
    """{photo rel: [(companion rel, size, mtime)]} - e.g. IMG_0001.HEIC -> IMG_0001.MOV,
    by the profile's rules. Companions are media themselves, found in `media`."""
    profile = profile or _profile(source)
    by_lower = {rel.lower(): (rel, size, mtime) for rel, size, mtime in media}
    out: dict[str, list[tuple[str, int, float]]] = {}
    for rel, _, _ in media:
        folder, name = (rel.rsplit("/", 1) if "/" in rel else ("", rel))
        for cand in profile.companion_names(name):
            crel = f"{folder}/{cand}" if folder else cand
            hit = by_lower.get(crel.lower())
            if hit and hit[0] != rel and all(hit[0] != c[0] for c in out.get(rel, [])):
                out.setdefault(rel, []).append(hit)
    return out


# --- creating ------------------------------------------------------------------

def create_import(conn: sqlite3.Connection, source: str, cfg: Settings_, name: str | None = None) -> int:
    if not cfg.destination:
        raise ValueError("Choose which library folder imports go into first (Settings > Import).")
    profile = _profile(source)
    items = discover(source, profile)
    companions = companions_of(source, items, profile)
    owned = {c[0] for cs in companions.values() for c in cs}
    items = [i for i in items if i[0] not in owned]           # a companion is filed with its photo
    sidecars = sidecars_of(source, items + [c for cs in companions.values() for c in cs], profile)
    serial, label = volume_info(source)
    # Files this card already gave us (same card, same path, size and time) are
    # recorded as such without being copied again - with where they went, so
    # their sidecars and companions can still be filed beside them.
    before: dict[tuple[str, int, float], str | None] = {}
    if serial:
        before = {(r, s, round(m, 1)): d for r, s, m, d in conn.execute(
            "SELECT i.source_rel, i.size, i.mtime, i.dest_path FROM import_items i JOIN imports im ON im.id = i.import_id"
            " WHERE im.volume_serial = ? AND i.state IN ('placed', 'already_in_library')", (serial,))}
    imp = conn.execute(
        "INSERT INTO imports (source, volume_serial, volume_label, name, template, destination, status, profile)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (source, serial, label, (name or "").strip() or None, cfg.template, cfg.destination,
         f"{len(items):,} files to copy", profile.id)).lastrowid

    def add(rel, size, mtime, parent=None, kind=None) -> int:
        key = (rel, size, round(mtime, 1))
        dest = before.get(key)
        # Only while its library copy is still there: copies deleted since must be imported again.
        seen = key in before and bool(dest) and os.path.isfile(dest) and os.path.getsize(dest) == size
        return conn.execute(
            "INSERT INTO import_items (import_id, source_rel, size, mtime, parent_id, kind, state, note, dest_path)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (imp, rel, size, mtime, parent, kind, "already_in_library" if seen else "pending",
             "Imported before from this card" if seen else None, before.get(key) if seen else None)).lastrowid

    for rel, size, mtime in items:
        parent = add(rel, size, mtime)
        taken: set[str] = set()                    # IMG_0001.AAE belongs to the photo AND its video: once
        for crel, csize, cmtime in companions.get(rel, ()):
            add(crel, csize, cmtime, parent, "companion")
        for owner in [rel] + [c[0] for c in companions.get(rel, ())]:
            for srel, ssize, smtime in sidecars.get(owner, ()):
                if srel not in taken:
                    taken.add(srel)
                    add(srel, ssize, smtime, parent, "sidecar")      # all beside the photo
    conn.commit()
    return imp


def summary(conn: sqlite3.Connection, import_id: int) -> dict:
    counts = dict(conn.execute("SELECT state, COUNT(*) FROM import_items WHERE import_id = ? GROUP BY state",
                               (import_id,)).fetchall())
    total = sum(counts.values())
    left_on_card = counts.get("pending", 0) + counts.get("failed", 0)
    in_library = counts.get("placed", 0) + counts.get("already_in_library", 0)
    return {**counts, "total": total,
            "card_removable": total > 0 and left_on_card == 0,
            "safe_to_format": total > 0 and in_library == total}


# --- copying with verification ---------------------------------------------------

def _copy_hashed(src: str, dst: str, should_stop: StopFn | None = None) -> str | None:
    """Copy src -> dst (via a temp name), return the sha256 of what was READ.
    Keeps the modified time. None if stopped part-way (nothing left behind)."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = os.path.join(os.path.dirname(dst), TMP_PREFIX + os.path.basename(dst))
    h = hashlib.sha256()
    try:
        with open(src, "rb") as fin, open(tmp, "wb") as fout:
            while chunk := fin.read(CHUNK):
                h.update(chunk)
                fout.write(chunk)
                if should_stop and should_stop():
                    raise InterruptedError
            fout.flush()
            os.fsync(fout.fileno())
        shutil.copystat(src, tmp)
        os.replace(tmp, dst)
        return h.hexdigest()
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _staging_dir(cfg: Settings_, size: int) -> str:
    local = Path(cfg.staging_local)
    local.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(local).free - size >= cfg.reserve_bytes:
        return str(local)
    if cfg.staging_network:
        try:
            os.makedirs(cfg.staging_network, exist_ok=True)
            if shutil.disk_usage(cfg.staging_network).free > size:
                return cfg.staging_network
        except OSError:
            pass
    raise NoStagingSpace("no room to stage: the local reserve is reached and the network "
                         "staging folder is unavailable or full")


# --- 1. stage ----------------------------------------------------------------

def stage(conn: sqlite3.Connection, import_id: int, cfg: Settings_, *,
          should_stop: StopFn | None = None, on_progress: ProgressFn | None = None) -> dict:
    source, = conn.execute("SELECT source FROM imports WHERE id = ?", (import_id,)).fetchone()
    if not os.path.isdir(source):
        _set(conn, import_id, "waiting", "Waiting for the card to be inserted again")
        raise WaitingForSource(source)
    _set(conn, import_id, "staging", "Copying from the card…")
    stopped = _stage_pending(conn, import_id, source, cfg, should_stop, on_progress)
    if not stopped and _late_sidecars(conn, import_id, source):
        _stage_pending(conn, import_id, source, cfg, should_stop, on_progress)
    s = summary(conn, import_id)
    if s["card_removable"]:
        _set(conn, import_id, "staged", "Copied and verified - the card can be removed")
    return s


def _late_sidecars(conn: sqlite3.Connection, import_id: int, source: str) -> int:
    """Sidecars a camera writes a moment after its clip: look again before the
    card is declared removable. Returns how many were added."""
    pid, = conn.execute("SELECT profile FROM imports WHERE id = ?", (import_id,)).fetchone()
    from lunelis import paths
    profs = camera_profiles.load(paths.DATA_DIR)
    profile = next((p for p in profs if p.id == pid), None) or camera_profiles.detect(source, profs)
    media = conn.execute("SELECT id, source_rel, size, mtime FROM import_items WHERE import_id = ?"
                         " AND parent_id IS NULL", (import_id,)).fetchall()
    known = {r for r, in conn.execute("SELECT source_rel FROM import_items WHERE import_id = ?", (import_id,))}
    by_rel = {rel: mid for mid, rel, _, _ in media}
    added = 0
    for rel, found in sidecars_of(source, [(r, s, m) for _, r, s, m in media], profile).items():
        for srel, size, mtime in found:
            if srel not in known:
                conn.execute("INSERT INTO import_items (import_id, source_rel, size, mtime, parent_id, kind)"
                             " VALUES (?, ?, ?, ?, ?, 'sidecar')", (import_id, srel, size, mtime, by_rel[rel]))
                added += 1
    conn.commit()
    return added


def _stage_pending(conn, import_id, source, cfg, should_stop, on_progress) -> bool:
    """Stage every pending item. Returns True if it was stopped part-way."""
    todo = conn.execute("SELECT id, source_rel, size, parent_id FROM import_items WHERE import_id = ?"
                        " AND state = 'pending' ORDER BY id", (import_id,)).fetchall()
    for n, (item, rel, size, parent) in enumerate(todo, 1):
        if should_stop and should_stop():
            return True
        src = os.path.join(source, *rel.split("/"))
        try:
            if parent is None:
                found = _in_library_already(conn, src, size)
                if found:
                    found_path, digest = found
                    conn.execute("UPDATE import_items SET state = 'already_in_library', sha256 = ?, dest_path = ?,"
                                 " note = 'Already in your library' WHERE id = ?", (digest, found_path, item))
                    conn.commit()
                    if on_progress:
                        on_progress(n, len(todo), rel)
                    continue
            dst = os.path.join(_staging_dir(cfg, size), f"import-{import_id}", *rel.split("/"))
            digest = _copy_hashed(src, dst, should_stop)
            if _sha256(dst) != digest:                    # the staged copy must read back identically
                os.unlink(dst)
                raise OSError("staged copy doesn't match the card")
            conn.execute("UPDATE import_items SET state = 'staged', sha256 = ?, staged_path = ?, error = NULL"
                         " WHERE id = ?", (digest, dst, item))
        except InterruptedError:
            return True
        except NoStagingSpace:
            conn.commit()
            _set(conn, import_id, "waiting", "Out of staging space")
            raise
        except OSError as e:
            if not os.path.isdir(source):                 # card pulled out mid-copy
                conn.commit()
                _set(conn, import_id, "waiting", "Waiting for the card to be inserted again")
                raise WaitingForSource(source) from e
            conn.execute("UPDATE import_items SET state = 'failed', error = ? WHERE id = ?",
                         (f"{type(e).__name__}: {e}"[:300], item))
        conn.commit()
        if on_progress:
            on_progress(n, len(todo), rel)
    return False


# --- 2. place ---------------------------------------------------------------------

def _in_library_already(conn: sqlite3.Connection, path: str, size: int) -> tuple[str, str] | None:
    """Before copying: is this exact file (any name) already in the library?
    (library path, sha256) when it is - checked byte for byte, since it then
    counts as safely off the card. Cheap when it isn't: a size lookup."""
    if not conn.execute("SELECT 1 FROM files WHERE size_bytes = ? AND missing_since IS NULL AND excluded = 0"
                        " AND quarantined_at IS NULL LIMIT 1", (size,)).fetchone():
        return None
    from lunelis.importers.metadata import read_file
    try:
        taken = read_file(path).get("captured_at")
    except Exception:
        taken = None
    try:
        return _already_in_library(conn, path, size, taken)
    except OSError:
        return None


def _already_in_library(conn: sqlite3.Connection, path: str, size: int, taken: str | None,
                        digest: str | None = None) -> tuple[str, str] | None:
    """(library path, sha256) of the library file holding exactly these bytes,
    or None. Size, capture time and the sampled fingerprint pick the
    candidates; only a full SHA-256 match counts - a card file may be cleared
    on the strength of this. The file's own digest is worked out only when a
    candidate turns up, unless it's passed in."""
    rows = conn.execute(
        "SELECT f.id, r.path, f.rel_path, f.sample_hash FROM files f JOIN roots r ON r.id = f.root_id"
        " LEFT JOIN exif e ON e.file_id = f.id WHERE f.size_bytes = ? AND f.missing_since IS NULL AND f.excluded = 0"
        " AND f.quarantined_at IS NULL AND (e.captured_at IS ? OR e.captured_at IS NULL)",
        (size, taken)).fetchall()
    if not rows:
        return None
    mine, _ = sample_hash(path, size)
    for fid, root, rel, known in rows:
        if known is None:
            try:
                known, _ = sample_hash(os.path.join(root, *rel.split("/")), size)
            except OSError:
                continue
            conn.execute("UPDATE files SET sample_hash = ? WHERE id = ?", (known, fid))
        if known != mine:
            continue
        if digest is None:
            digest = _sha256(path)
        found = os.path.join(root, *rel.split("/"))
        try:
            if _sha256(found) == digest:
                return found, digest
        except OSError:
            continue
    return None


def _taken(value: str | None) -> datetime | None:
    """A capture time read from a file, or None when there isn't a usable one."""
    try:
        return datetime.fromisoformat(value[:19]) if value else None
    except ValueError:
        return None


def _event_start(conn: sqlite3.Connection, import_id: int, name: str | None,
                 staged: list[str]) -> datetime | None:
    # A named import is an event, filed by its start date: the earliest
    # capture time on the card. Worked out once and stored, so an import
    # that's resumed after a restart files the rest into the same folder.
    if not (name or "").strip():
        return None
    row = conn.execute("SELECT event_start FROM imports WHERE id = ?", (import_id,)).fetchone()
    if row and row[0]:
        return datetime.fromisoformat(row[0])
    from lunelis.importers.metadata import read_file
    earliest = None
    for path in staged:
        try:
            at = read_file(path).get("captured_at")
        except Exception:
            continue
        t = _taken(at)
        if t is not None:
            earliest = t if earliest is None or t < earliest else earliest
    if earliest is not None:
        conn.execute("UPDATE imports SET event_start = ? WHERE id = ?", (earliest.isoformat(), import_id))
        conn.commit()
    return earliest


def place(conn: sqlite3.Connection, import_id: int, cfg: Settings_ | None = None, *,
          should_stop: StopFn | None = None, on_progress: ProgressFn | None = None) -> dict:
    from lunelis.importers.metadata import read_file

    name, template, destination, created = conn.execute(
        "SELECT name, template, destination, created_at FROM imports WHERE id = ?", (import_id,)).fetchone()
    import_date = date.fromisoformat(created[:10])
    if not os.path.isdir(destination):
        _set(conn, import_id, "waiting", f"Waiting for {destination}")
        raise WaitingForSource(destination)
    _set(conn, import_id, "placing", "Filing into the library…")
    # Photos and clips first; their sidecars then follow them into the same folder.
    todo = conn.execute("SELECT id, source_rel, size, sha256, staged_path, parent_id FROM import_items"
                        " WHERE import_id = ? AND state = 'staged' ORDER BY parent_id IS NOT NULL, id",
                        (import_id,)).fetchall()
    event_start = _event_start(conn, import_id, name, [t[4] for t in todo if t[5] is None])
    for n, (item, rel, size, digest, staged, parent) in enumerate(todo, 1):
        if should_stop and should_stop():
            break
        if parent is not None:
            try:
                _place_sidecar(conn, item, parent, rel, size, digest, staged, should_stop)
            except InterruptedError:
                break
            except OSError as e:
                if is_network_error(e) or not os.path.isdir(destination):
                    conn.commit()
                    _set(conn, import_id, "waiting", f"Waiting for {destination}")
                    raise WaitingForSource(destination) from e
                conn.execute("UPDATE import_items SET state = 'failed', error = ? WHERE id = ?",
                             (f"{type(e).__name__}: {e}"[:300], item))
            conn.commit()
            if on_progress:
                on_progress(n, len(todo), rel)
            continue
        try:
            unread = None
            try:
                meta = read_file(staged)
            except Exception as e:
                meta, unread = {}, f"Couldn't read its date ({type(e).__name__}) - filed as undated"
            taken_s = meta.get("captured_at")
            taken = _taken(taken_s)
            if taken is None:
                taken_s = None
            found = _already_in_library(conn, staged, size, taken_s, digest) if digest else None
            if found:
                found = found[0]
                os.unlink(staged)
                conn.execute("UPDATE import_items SET state = 'already_in_library', dest_path = ?,"
                             " staged_path = NULL WHERE id = ?", (found, item))
                conn.commit()
                continue
            comps = conn.execute(
                "SELECT source_rel, size, sha256 FROM import_items WHERE parent_id = ? AND kind = 'companion'",
                (item,)).fetchall()
            folder = render(template, Context(
                taken=taken, camera=meta.get("camera_model"), import_name=name, import_date=import_date,
                original_folder=os.path.dirname(rel).rsplit("/", 1)[-1] or None,
                event=(name or "").strip() or None, event_start=event_start))
            filename = os.path.basename(rel)
            k, target_folder, dest, duplicate = 1, folder, None, False
            while True:
                dest = os.path.join(destination, *target_folder.split("\\"), filename)
                # A Live Photo is never split: its video needs the same folder free too.
                if any(_taken_by_other(os.path.join(os.path.dirname(dest), os.path.basename(crel)), csize, cdig)
                       for crel, csize, cdig in comps):
                    k += 1
                    target_folder = sibling(folder, k)
                    continue
                if not os.path.exists(dest):
                    break
                if os.path.getsize(dest) == size and _sha256(dest) == digest:
                    duplicate = True                       # this exact file is already there
                    break
                k += 1                                     # a different file has the name: never rename,
                target_folder = sibling(folder, k)         # use a sibling folder instead
            if duplicate:
                os.unlink(staged)
                conn.execute("UPDATE import_items SET state = 'already_in_library', dest_path = ?,"
                             " staged_path = NULL WHERE id = ?", (dest, item))
            else:
                if _copy_hashed(staged, dest, should_stop) != digest or _sha256(dest) != digest:
                    os.unlink(dest)
                    raise OSError("library copy doesn't match the card")
                os.unlink(staged)                          # only now: the library copy is verified
                conn.execute("UPDATE import_items SET state = 'placed', dest_path = ?, staged_path = NULL,"
                             " error = NULL, note = COALESCE(?, note) WHERE id = ?", (dest, unread, item))
        except InterruptedError:
            break
        except OSError as e:
            if is_network_error(e) or isinstance(e, SourceOffline) or not os.path.isdir(destination):
                conn.commit()
                _set(conn, import_id, "waiting", f"Waiting for {destination}")
                raise WaitingForSource(destination) from e
            conn.execute("UPDATE import_items SET state = 'failed', error = ? WHERE id = ?",
                         (f"{type(e).__name__}: {e}"[:300], item))
        conn.commit()
        if on_progress:
            on_progress(n, len(todo), rel)
    s = summary(conn, import_id)
    if s["safe_to_format"]:
        _set(conn, import_id, "done", "All in the library and verified - safe to format the card", finished=True)
        if cfg is not None:
            cleanup_staging(import_id, cfg)
    return s


def _taken_by_other(path: str, size: int, digest: str | None) -> bool:
    """A different file already has this name here."""
    if not os.path.exists(path):
        return False
    return not (os.path.getsize(path) == size and digest and _sha256(path) == digest)


def _place_sidecar(conn, item, parent, rel, size, digest, staged, should_stop) -> None:
    """A sidecar goes beside its photo or clip, wherever that was filed (or
    found already in the library). Never renamed; a different file already
    there is kept and this one is left out, with a note."""
    p_state, p_dest = conn.execute("SELECT state, dest_path FROM import_items WHERE id = ?", (parent,)).fetchone()
    if p_state not in ("placed", "already_in_library") or not p_dest:
        return                                            # its file isn't filed yet: next pass
    dest = os.path.join(os.path.dirname(p_dest), os.path.basename(rel))
    if os.path.exists(dest):
        if not (os.path.getsize(dest) == size and digest and _sha256(dest) == digest):
            # A different file has the name. It's kept, and this one stays on the
            # card: its bytes aren't in the library, so the card isn't safe to format.
            conn.execute("UPDATE import_items SET state = 'failed', error = ? WHERE id = ?",
                         ("A different file with this name is already beside its photo - left on the card", item))
            return
        os.unlink(staged)
        conn.execute("UPDATE import_items SET state = 'already_in_library', dest_path = ?, staged_path = NULL"
                     " WHERE id = ?", (dest, item))
        return
    if _copy_hashed(staged, dest, should_stop) != digest or _sha256(dest) != digest:
        os.unlink(dest)
        raise OSError("library copy doesn't match the card")
    os.unlink(staged)
    conn.execute("UPDATE import_items SET state = 'placed', dest_path = ?, staged_path = NULL, error = NULL"
                 " WHERE id = ?", (dest, item))


def clearable(conn: sqlite3.Connection, import_id: int) -> list[str]:
    """Card files (rel paths) that are verified in the library - the only ones
    'Clear the card' may delete. Empty until the whole import is."""
    if not summary(conn, import_id)["safe_to_format"]:
        return []
    return [r for r, in conn.execute("SELECT source_rel FROM import_items WHERE import_id = ?"
                                     " AND state IN ('placed', 'already_in_library')", (import_id,))]


def clear_card(conn: sqlite3.Connection, import_id: int) -> tuple[int, list[str]]:
    """Delete from the card the files this import verified into the library,
    once everything is. A file that changed on the card since (size or time)
    is left alone. Returns (deleted, skipped rel paths). The user asks for
    this, after a confirmation that it can't be undone - no job ever does."""
    source, = conn.execute("SELECT source FROM imports WHERE id = ?", (import_id,)).fetchone()
    deleted, skipped = 0, []
    rows = conn.execute("SELECT source_rel, size, mtime, dest_path FROM import_items WHERE import_id = ?"
                        " AND state IN ('placed', 'already_in_library')", (import_id,)).fetchall()
    if not clearable(conn, import_id):
        return 0, [r for r, _, _, _ in rows]
    for rel, size, mtime, dest in rows:
        path = os.path.join(source, *rel.split("/"))
        try:
            st = os.stat(path)
            if st.st_size != size or abs(st.st_mtime - mtime) > 2:
                skipped.append(rel)
                continue
            # The last word before deleting from the card: the library copy is
            # there right now and holds exactly these bytes. Anything else stays.
            if not dest or not os.path.isfile(dest) or os.path.getsize(dest) != size \
                    or _sha256(dest) != _sha256(path):
                skipped.append(rel)
                continue
            os.remove(path)
            deleted += 1
        except FileNotFoundError:
            pass
        except OSError:
            skipped.append(rel)
    conn.execute("UPDATE imports SET status = ? WHERE id = ?",
                 (f"Card cleared: {deleted:,} files deleted" + (f", {len(skipped):,} left" if skipped else ""),
                  import_id))
    conn.commit()
    return deleted, skipped


def cleanup_staging(import_id: int, cfg: Settings_) -> None:
    """Remove this import's staging folders once they hold no files - they're
    Lunelis's own temp folders. A folder that still has a file is left alone."""
    for base in (cfg.staging_local, cfg.staging_network):
        if not base:
            continue
        d = os.path.join(base, f"import-{import_id}")
        try:
            if not os.path.isdir(d):
                continue
            if any(files for _, _, files in os.walk(d)):
                continue
            shutil.rmtree(d)
        except OSError:
            pass


def _set(conn, import_id, state, status, finished=False):
    fin = ", finished_at = datetime('now')" if finished else ""
    conn.execute(f"UPDATE imports SET state = ?, status = ?{fin} WHERE id = ?", (state, status, import_id))
    conn.commit()


def run(conn: sqlite3.Connection, import_id: int, cfg: Settings_, *, should_stop: StopFn | None = None,
        on_progress: Callable[[str, int, int, str], None] | None = None) -> dict:
    """Stage (if anything's left on the card) then place."""
    if conn.execute("SELECT 1 FROM import_items WHERE import_id = ? AND state = 'pending' LIMIT 1",
                    (import_id,)).fetchone():
        stage(conn, import_id, cfg, should_stop=should_stop,
              on_progress=lambda d, t, f: on_progress and on_progress("stage", d, t, f))
        if should_stop and should_stop():
            return summary(conn, import_id)
    return place(conn, import_id, cfg, should_stop=should_stop,
                 on_progress=lambda d, t, f: on_progress and on_progress("place", d, t, f))


def retry_failed(conn: sqlite3.Connection, import_id: int) -> int:
    """Send failed files back to the step they need: re-place if the staged
    copy is still there, otherwise re-copy from the card."""
    n = 0
    for item, staged in conn.execute("SELECT id, staged_path FROM import_items WHERE import_id = ?"
                                     " AND state = 'failed'", (import_id,)).fetchall():
        state = "staged" if staged and os.path.exists(staged) else "pending"
        conn.execute("UPDATE import_items SET state = ?, error = NULL WHERE id = ?", (state, item))
        n += 1
    conn.commit()
    return n


def unfinished(conn: sqlite3.Connection) -> list[tuple[int, str, str | None]]:
    """[(import id, state, volume serial)] for imports not yet done/cancelled."""
    return conn.execute("SELECT id, state, volume_serial FROM imports"
                        " WHERE state NOT IN ('done', 'cancelled') ORDER BY id").fetchall()
