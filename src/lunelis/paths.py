"""
Where Lunelis keeps its own files.

Everything Lunelis owns lives in one data folder, never inside the photo
library and never inside the program's folder:

    <data>/catalog.db          the catalog
    <data>/cache/thumbnails/   512px thumbnails (disposable)
    <data>/backups/            automatic catalog snapshots
    <data>/sidecars/           the central XMP sidecar store

The data folder is, in order:
  1. $LUNELIS_DATA_DIR (tests, development, portable installs)
  2. the "data_dir" in %APPDATA%/Lunelis/location.json (a location the user
     chose - e.g. a bigger drive; the file is tiny and always on C:)
  3. %LOCALAPPDATA%/Lunelis (default)

It's resolved once at import. Changing it takes a restart: the Settings
screen records {"move_to": ...} in location.json, and the next start moves the
folder's contents (main.py, before the catalog is opened) and then switches
"data_dir" - so an interrupted move just carries on at the following start,
with the old folder still the real one until every file has arrived.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

# Packaged (PyInstaller) builds unpack their bundled files next to the exe;
# a source checkout keeps them in the repo.
FROZEN = bool(getattr(sys, "frozen", False))
PROJECT_ROOT = Path(getattr(sys, "_MEIPASS", "")) if FROZEN else Path(__file__).resolve().parents[2]
ASSETS = PROJECT_ROOT / "assets"


def version() -> str:
    # pyproject.toml is the one place the version lives; the build bundles its metadata.
    from importlib.metadata import PackageNotFoundError, version as v
    try:
        return v("lunelis")
    except PackageNotFoundError:
        return "dev"


def launch_command(*args: str) -> tuple[str, list[str]]:
    # (program, arguments) that start Lunelis again: the exe itself when
    # packaged, `pythonw -m lunelis` from a source checkout.
    if FROZEN:
        return sys.executable, list(args)
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        exe = exe[:-len("python.exe")] + "pythonw.exe"
    return exe, ["-m", "lunelis", *args]

_APPDATA = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
_LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
LOCATION_FILE = _APPDATA / "Lunelis" / "location.json"


def resolve_data_dir() -> Path:
    env = os.environ.get("LUNELIS_DATA_DIR")
    if env:
        return Path(env)
    try:
        chosen = json.loads(LOCATION_FILE.read_text(encoding="utf-8")).get("data_dir")
        if chosen:
            return Path(chosen)
    except (OSError, ValueError):
        pass
    return _LOCALAPPDATA / "Lunelis"


def set_data_dir(path: Path) -> None:
    """Record a user-chosen data folder (takes effect on next start)."""
    LOCATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCATION_FILE.write_text(json.dumps({"data_dir": str(path)}, indent=2), encoding="utf-8")


def reload() -> None:
    """Re-resolve the data folder (after a pending move finished)."""
    global DATA_DIR, DEFAULT_CATALOG_PATH, THUMBNAIL_CACHE, BACKUP_DIR, SIDECAR_STORE, EDIT_CACHE
    DATA_DIR = resolve_data_dir()
    DEFAULT_CATALOG_PATH = DATA_DIR / "catalog.db"
    THUMBNAIL_CACHE = DATA_DIR / "cache" / "thumbnails"
    EDIT_CACHE = DATA_DIR / "cache" / "edits"         # rendered proxies of edited photos
    BACKUP_DIR = DATA_DIR / "backups"
    SIDECAR_STORE = DATA_DIR / "sidecars"


reload()


# --- moving the data folder ---------------------------------------------------------

def is_network_path(path: str | os.PathLike) -> bool:
    """A share (\\\\server\\share) or a drive letter mapped to one."""
    p = str(path)
    if p.startswith(("\\\\", "//")):
        return True
    if sys.platform == "win32":
        drive = os.path.splitdrive(os.path.abspath(p))[0]
        if drive:
            import ctypes
            return ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == 4     # DRIVE_REMOTE
    return False


def _inside(a: Path, b: Path) -> bool:
    a, b = os.path.normcase(os.path.abspath(a)), os.path.normcase(os.path.abspath(b))
    return a == b or a.startswith(b.rstrip("\\/") + os.sep)


def check_new_data_dir(new: str | os.PathLike, current: Path | None = None) -> None:
    """Refuse a data folder that can't work. Raises ValueError with the reason."""
    current = Path(current or DATA_DIR)
    new = Path(new)
    if is_network_path(new):
        # SQLite's write-ahead log needs shared memory, which network shares
        # don't provide: a catalog there corrupts or locks up.
        raise ValueError("The data folder has to be on a drive in this PC - the catalog "
                         "can't live on a network share.")
    if _inside(new, current) or _inside(current, new):
        raise ValueError("Choose a folder that's neither inside the current data folder nor contains it.")
    if (new / "catalog.db").exists():
        raise ValueError(f"{new} already holds a Lunelis catalog - choose an empty folder.")


def request_move(new: str | os.PathLike, current: Path | None = None) -> None:
    """Move the data folder at the next start (checked first)."""
    current = Path(current or DATA_DIR)
    check_new_data_dir(new, current)
    LOCATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCATION_FILE.write_text(json.dumps({"data_dir": str(current), "move_to": str(Path(new))}, indent=2),
                             encoding="utf-8")


def pending_move() -> Path | None:
    if os.environ.get("LUNELIS_DATA_DIR"):
        return None
    try:
        to = json.loads(LOCATION_FILE.read_text(encoding="utf-8")).get("move_to")
    except (OSError, ValueError):
        return None
    return Path(to) if to else None


def cancel_move() -> None:
    try:
        data = json.loads(LOCATION_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    data.pop("move_to", None)
    LOCATION_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _same_volume(a: Path, b: Path) -> bool:
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


def move_tree(src: str | os.PathLike, dst: str | os.PathLike,
              on_progress=None) -> int:
    """Move everything in `src` into `dst` (merging), then remove `src` if
    it's left empty. On one drive it's renames. Across drives every file is
    copied (to a .partial name, size-checked, then renamed) and only when ALL
    have arrived are the originals deleted - an interruption leaves `src`
    complete, and running it again finishes the job. Returns files moved."""
    src, dst = Path(src), Path(dst)
    if not src.is_dir():
        return 0
    dst.mkdir(parents=True, exist_ok=True)
    files = [p for p in src.rglob("*") if p.is_file()]
    if _same_volume(src, dst):
        for i, f in enumerate(files):
            t = dst / f.relative_to(src)
            t.parent.mkdir(parents=True, exist_ok=True)
            os.replace(f, t)
            if on_progress:
                on_progress(i + 1, len(files))
    else:
        for i, f in enumerate(files):
            t = dst / f.relative_to(src)
            t.parent.mkdir(parents=True, exist_ok=True)
            tmp = t.with_name(t.name + ".partial")
            shutil.copy2(f, tmp)
            if tmp.stat().st_size != f.stat().st_size:
                tmp.unlink()
                raise OSError(f"copy of {f} came out the wrong size")
            os.replace(tmp, t)
            if on_progress:
                on_progress(i + 1, len(files))
        for f in files:
            f.unlink()
    for d in sorted((p for p in src.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            d.rmdir()
        except OSError:
            pass
    try:
        src.rmdir()
    except OSError:
        pass
    return len(files)


def finish_pending_move(on_progress=None) -> Path | None:
    """Called at start-up, before the catalog is opened. Returns the new
    data folder if a move was carried out."""
    new = pending_move()
    if new is None:
        return None
    old = resolve_data_dir()
    move_tree(old, new, on_progress)
    set_data_dir(new)
    reload()
    return new


def adopt_legacy_data(data_dir: Path = DATA_DIR, project_root: Path = PROJECT_ROOT) -> list[str]:
    """Move a catalog + thumbnail cache left in the project folder by early
    development builds into the data folder, once. Never overwrites: if the
    data folder already has a catalog, nothing moves. Returns what moved."""
    moved: list[str] = []
    if FROZEN:
        return moved                       # only early source checkouts ever kept data there
    old_db = project_root / "lunelis.db"
    new_db = data_dir / "catalog.db"
    if not old_db.exists() or new_db.exists():
        return moved
    data_dir.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(old_db) + suffix)
        if src.exists():
            shutil.move(str(src), str(new_db) + suffix)
            moved.append(src.name)
    old_cache = project_root / "cache" / "thumbnails"
    new_cache = data_dir / "cache" / "thumbnails"
    if old_cache.is_dir() and not new_cache.exists():
        new_cache.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old_cache), str(new_cache))   # same drive: a rename, not a copy
        old_cache.mkdir(parents=True, exist_ok=True)  # keep the repo's .gitkeep folder
        gitkeep = new_cache / ".gitkeep"
        if gitkeep.exists():
            shutil.move(str(gitkeep), str(old_cache / ".gitkeep"))
        moved.append("cache/thumbnails")
    return moved
