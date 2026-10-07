"""
Library settings, stored in the catalog's `settings` table as JSON values.

Every setting has a default here, so an unset key means "default" and adding
a setting never needs a migration. Settings belong to the library (they
travel with the catalog and its backups); where the data folder itself lives
is the one exception - see paths.py.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

SIDECAR_MODES = ("central", "beside", "catalog")

DEFAULTS: dict[str, Any] = {
    # Where Lunelis writes ratings/labels: its own central store (photo
    # folders stay clean), next to the photos (visible to darktable/Lightroom),
    # or nowhere but the catalog.
    "sidecar_mode": "central",
    # Also keep sidecars that ALREADY exist next to photos (darktable's,
    # Lightroom's) in step - without ever creating new ones there.
    "update_existing_sidecars": True,
    # Central store location; None = <data dir>/sidecars.
    "sidecar_store_dir": None,
    # Catalog snapshots: where, and how many to keep.
    "catalog_backup_dir": None,          # None = <data dir>/backups
    "catalog_backups_keep": 10,
    "catalog_backup_every_hours": 24,
    # Duplicates: keep the copy in the first of these roots (ids) that has one.
    "preferred_roots": [],
    # Card import. Files are never renamed; the template only picks folders.
    "import_destination": None,          # None = ask at the first import (no machine-specific default)
    "import_template": r"{YYYY}\{M}-{D}-{YYYY}[ {import_name}]",
    "import_staging_local": None,        # None = <data dir>/staging
    "import_staging_network": None,      # None = no spill-over: pause when the local disk is full
    "import_local_reserve_gb": 50,       # never let staging take C: below this much free
    "import_recent": [],                 # folders imported from, newest first (Import page)
    # Tray: keep running in the notification area and watch for memory cards.
    "tray_enabled": True,
    "start_with_windows": False,
    "welcome_done": False,               # the first-run setup (installer or Welcome window) has been applied
    # Defaults offered when starting a background job (each job keeps its own).
    "job_default_when": "now",           # now | idle | window
    "job_idle_minutes": 5,
    "job_window_start_hour": 22,
    "job_window_end_hour": 6,
    "job_mb_per_s": 0,                   # 0 = no speed limit
    # Event suggestions from gaps in capture time.
    "event_gap_hours": 18,               # a longer gap between shots starts a new event
    "event_min_photos": 30,              # fewer photos than this isn't suggested
    # Editing: a filter every newly imported photo starts with (None = none).
    "import_filter": None,
    "export_last": None,                 # the Export dialog's last settings (edit/export.ExportOptions)
    "merge_last_dir": None,              # where the last HDR/panorama was saved (the save dialog starts there)
    "tags_recent": [],                   # the last tags used, offered first when tagging
    # Burst stacks: frames shot in quick succession show as one tile.
    "stack_bursts": True,
    "pair_raw_jpeg": True,               # a RAW+JPEG shot shows as one photo (pairs.py)
    "scene_tags_auto": False,            # look at new photos (scene tags) after each scan - on once the model is in
    "scene_auto_accept": False,          # accept a scene suggestion by itself when the model is at least this sure:
    "scene_auto_threshold": 0.9,
    # Faces (recognize/faces.py): found and recognised on this PC.
    "faces_auto": False,                 # look for faces in new photos after each scan - on once the models are in
    "faces_auto_confirm": False,         # name a face by itself when it's at least this alike a known person:
    "faces_auto_threshold": 0.6,
    "faces_overlay": False,              # the photo view shows face boxes and names (F)
    # Places (geo/places.py): from the built-in list, offline.
    "places_auto": True,                 # give photos with a location a Places tag after each scan
    "places_tag_no_location": False,     # photos without one get Places|No location
    "burst_gap_seconds": 1.0,            # frames at most this far apart (sub-second times) are one burst
    "burst_min_frames": 3,
    "burst_max_frames": 50,              # a longer fast run isn't a burst
    # The timelapse engine (timelapses.py), after each scan.
    "timelapse_detect": True,
    "timelapse_min_frames": 100,         # shorter sets: Photo > Make a timelapse from the selection
    "timelapse_split_gaps": False,       # a pause (battery swap) starts a new timelapse
    "timelapse_max_pause_minutes": 30,   # longer than this always ends one
    "timelapse_auto_stack": True,        # big ones show as one tile as soon as they're found
    "timelapse_auto_stack_frames": 500,               # fewer shots than this isn't a burst
    # Appearance.
    "theme": "system",                   # system | graphite | midnight | high_contrast
    "sidebar_collapsed": [],             # sidebar sections folded away
    "sidebar_compact": False,            # True = the sidebar shows icons only (Ctrl+B)
    "sidebar_auto": True,                # fold to icons by itself on narrow windows
    "grid_default_sort": "date_desc",
    "create_output_dir": None,           # where the Create tab writes (None: Pictures\Lunelis creations)
    "window_geometry": None,             # the window's size, place and monitor (Qt saveGeometry, hex)
    "grid_default_size": 180,            # tile edge in px (the Grid size slider starts here)
    "show_videos": True,                 # videos in the library grid
    "hover_info": True,                  # the info card over a photo the mouse rests on
    "start_page": "Library",             # Library | Albums | last (the page open when Lunelis closed)
    "last_page": "Library",
    "wheel_action": "zoom",              # the photo view's mouse wheel: zoom | step (next/previous photo)
    "log_preview": "builtin",            # S-Log3 clips: builtin (to Rec.709) | off | cube:<path to a .cube LUT>
    "log_thumbs_rev": 0,                 # 1 once log clips' thumbnails were remade with the preview look
    "noticed_upto": 0,                   # Lunelis noticed: the highest file id already looked at
    "noticed_auto": True,                # look for brackets, panoramas... after each scan
    "integrity_every": "week",           # regular file checks: off | week | month (jobs/rolling.py)
    "integrity_gb": 20,                  # how much is re-read each time
    "integrity_last": None,              # when the last regular check was planned (ISO)
    "map_online": False,                 # the Map page fetches OpenStreetMap tiles (off: dots on a plain grid)
    "autopilot": False,                  # after a card import, sort out the shoot (importing/autopilot.py)
    "autopilot_skip": [],                # autopilot stages turned off
    "export_presets": {},                # name -> ExportOptions (as a dict)
    "monitor_profile": "off",            # the photo view's colours: off | system (Windows' display profile)
    "proof_profile": None,               # soft-proofing: an .icc (a printer / paper) to preview against
    "gallery_port": 8735,                # the family gallery's port on this PC (gallery.py)
    "clock_24h": False,                  # 14:03 instead of 2:03 PM (photoinfo.clock)
    "location_opens": "map",             # map (Lunelis's Map) | browser (OpenStreetMap online)
    "date_format": "long",               # long | iso | day_first | short (photoinfo.DATE_FORMATS)
    "confirm_quit": True,                # ask before quitting while an export, merge or job runs
    "edit_live_quality": "fast",         # fast (half size while dragging) | sharp
    "detail_strip_height": 88,           # the photo view's filmstrip (drag its divider)
    "side_panel_width": 360,             # the photo view's Info / Edit panel (drag its divider)
    "edit_sections_closed": ["Masks", "Lens corrections", "Effects"],   # folded Edit panel sections
    # Updates (packaged builds): check the public releases once a day at start-up.
    "update_check": True,
    "update_last_check": None,           # ISO time of the last check
    "update_skip_version": None,         # "not this one" - stays quiet until a newer one
    # darktable plugin: ratings swapped through files in this folder (None = <data dir>/darktable).
    "darktable_sync": False,             # turned on by installing the plugin
    "darktable_exchange_dir": None,
}

JOB_WHEN = ("now", "idle", "window")


def validate(key: str, value: Any) -> None:
    """Refuse values that would break something later, with a message a
    person can act on (the Settings screen shows it next to the field)."""
    if key in ("scene_auto_threshold", "faces_auto_threshold") and not (
            isinstance(value, (int, float)) and 0.3 <= value <= 0.99):
        raise ValueError("Choose a value between 30 % and 99 %.")
    if key == "sidecar_mode" and value not in SIDECAR_MODES:
        raise ValueError(f"sidecar_mode must be one of {SIDECAR_MODES}")
    if key in ("sidebar_compact", "sidebar_auto") and not isinstance(value, bool):
        raise ValueError("sidebar_compact must be true or false")
    if key == "theme" and value not in ("system", "graphite", "midnight", "high_contrast"):
        raise ValueError("theme must be system, graphite, midnight or high_contrast")
    if key == "job_default_when" and value not in JOB_WHEN:
        raise ValueError(f"job_default_when must be one of {JOB_WHEN}")
    if key == "import_template":
        from lunelis.importing.templates import validate as check_template
        check_template(value)
    ranges = {
        "import_local_reserve_gb": (0, 100_000), "catalog_backups_keep": (1, 1000),
        "catalog_backup_every_hours": (1, 24 * 365), "job_idle_minutes": (1, 24 * 60),
        "job_window_start_hour": (0, 23), "job_window_end_hour": (0, 23), "job_mb_per_s": (0, 100_000),
        "event_gap_hours": (1, 24 * 14), "event_min_photos": (2, 100_000),
        "burst_gap_seconds": (0.1, 5), "burst_min_frames": (2, 50), "burst_max_frames": (3, 50),
        "timelapse_min_frames": (10, 10000), "timelapse_max_pause_minutes": (1, 240),
        "timelapse_auto_stack_frames": (50, 100000),
    }
    if key in ranges:
        lo, hi = ranges[key]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not lo <= value <= hi:
            raise ValueError(f"{key} must be a number from {lo} to {hi}")
    if key in ("import_destination",) and not (value or "").strip():
        raise ValueError("the import destination can't be empty")
    if key == "import_filter" and value is not None and not (isinstance(value, str) and value.strip()):
        raise ValueError("import_filter must be a filter name or None")
    if key == "preferred_roots" and not (isinstance(value, list) and all(isinstance(v, int) for v in value)):
        raise ValueError("preferred_roots must be a list of source ids")


class Settings:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def get(self, key: str) -> Any:
        if key not in DEFAULTS:
            raise KeyError(f"unknown setting {key!r}")
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else DEFAULTS[key]

    def set(self, key: str, value: Any) -> None:
        if key not in DEFAULTS:
            raise KeyError(f"unknown setting {key!r}")
        validate(key, value)
        self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)))
        self.conn.commit()

    def reset(self, key: str) -> None:
        self.conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        self.conn.commit()

    def all(self) -> dict[str, Any]:
        stored = {k: json.loads(v) for k, v in self.conn.execute("SELECT key, value FROM settings")}
        return {k: stored.get(k, d) for k, d in DEFAULTS.items()}
