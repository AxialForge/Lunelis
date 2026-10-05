"""
First-run setup: the choices made in the installer (or the Welcome window),
applied once by the program.

The installer (packaging/installer/lunelis.iss) asks its questions and writes
them to %APPDATA%\\Lunelis\\setup.json; it never touches the catalog itself.
The next start of Lunelis reads the file, applies it, and renames it to
setup.applied.json, so it runs exactly once. Updates (the in-app updater, or
the installer run again over an existing install) never write a new one, so
nothing is asked twice.

    {"version": 1,
     "data_dir": "D:\\\\Lunelis data" | "",         # "" = the default
     "folders": ["D:\\\\Photos", "\\\\\\\\nas\\\\photos"],
     "tray": true, "start_with_windows": false,
     "import_destination": "D:\\\\Photos" | "",
     "models": ["subject", "sky", "scene"]}

Two halves, because the data folder must be settled before anything opens
the catalog:
  - early(): in main.py before the data folder is used - a chosen data folder
    is recorded (or, if a library already exists at the old place, its move
    is requested so the normal data-folder move carries it over);
  - apply(conn): once the catalog is open - settings, start-up entry and the
    photo folders. Returns what the window should do next: the new roots to
    scan and the models to download (those run in the background).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from lunelis import paths

SETUP_FILE = paths.LOCATION_FILE.parent / "setup.json"
APPLIED_FILE = SETUP_FILE.with_name("setup.applied.json")
MODELS = ("subject", "sky", "scene")


@dataclass
class Plan:
    """What apply() leaves for the window: scan these, fetch those, tell the user that."""
    root_ids: list[int] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def read() -> dict | None:
    """The pending setup, or None (no file, or one this version can't read)."""
    if os.environ.get("LUNELIS_DATA_DIR"):        # tests and the documentation tools never pick it up
        return None
    try:
        data = json.loads(SETUP_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != 1:
        return None
    return data


def early(setup: dict) -> None:
    """Settle the data folder before the catalog is opened."""
    want = (setup.get("data_dir") or "").strip()
    if not want:
        return
    target = Path(os.path.expandvars(want))
    current = paths.DATA_DIR
    if os.path.normcase(os.path.abspath(target)) == os.path.normcase(os.path.abspath(current)):
        return
    if (target / "catalog.db").exists() and not (current / "catalog.db").exists() \
            and not paths.is_network_path(target):
        # An existing library (copied from another PC, or kept from an earlier install): use it as it is.
        paths.set_data_dir(target)
        paths.reload()
        return
    try:
        paths.check_new_data_dir(target, current)
    except (OSError, ValueError) as e:
        # A network share, inside the program folder...: keep the default, say why later.
        setup.setdefault("_problems", []).append(f"The data folder stays at {current}: {e}")
        return
    if (current / "catalog.db").exists():
        paths.request_move(target, current)     # main.py finishes it before the catalog opens
    else:
        paths.set_data_dir(target)
        paths.reload()


def apply(conn, setup: dict) -> Plan:
    """Apply settings, the start-up entry and the photo folders; mark the setup done."""
    from lunelis.importers.scan import RootOverlap, RootUnavailable, add_root
    from lunelis.settings import Settings

    plan = Plan(problems=list(setup.get("_problems", [])))
    s = Settings(conn)
    if "tray" in setup:
        s.set("tray_enabled", bool(setup["tray"]))
    if "start_with_windows" in setup:
        on = bool(setup["start_with_windows"])
        try:
            from lunelis.ui.tray import set_autostart
            set_autostart(on)
            s.set("start_with_windows", on)
        except OSError as e:
            plan.problems.append(f"Couldn't change Start with Windows: {e}")
    dest = (setup.get("import_destination") or "").strip()
    if dest:
        try:
            s.set("import_destination", dest)
        except ValueError as e:
            plan.problems.append(f"Import folder not set: {e}")
    for folder in setup.get("folders") or []:
        folder = str(folder).strip()
        if not folder:
            continue
        try:
            plan.root_ids.append(add_root(conn, folder))
        except (RootOverlap, RootUnavailable, OSError) as e:
            plan.problems.append(f"{folder}: {e}")
    plan.models = [m for m in setup.get("models") or [] if m in MODELS]
    s.set("welcome_done", True)
    done()
    return plan


def done() -> None:
    """Keep the file as a record, under a name that isn't read again."""
    try:
        os.replace(SETUP_FILE, APPLIED_FILE)
    except OSError:
        try:
            SETUP_FILE.unlink()
        except OSError:
            pass


def download_model(kind: str, on_progress=None, should_cancel=None) -> None:
    """Fetch one optional model (each checks its own SHA-256)."""
    if kind == "scene":
        from lunelis.recognize import clip
        if not clip.available():
            clip.download(on_progress, should_cancel)
    else:
        from lunelis.edit import ai
        if not ai.available(kind):
            ai.download(kind, on_progress, should_cancel)


def model_sizes() -> dict[str, int]:
    """Bytes per optional model, for the Welcome window."""
    from lunelis.edit import ai
    from lunelis.recognize import clip
    return {"subject": ai.MODELS["subject"].size, "sky": ai.MODELS["sky"].size, "scene": clip.TOTAL}
