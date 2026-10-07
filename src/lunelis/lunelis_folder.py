"""
The Lunelis folder (0.40): one folder, beside the Library, for everything
Lunelis makes or keeps for itself - so the Library holds only photos and
videos.

    <Lunelis folder>\\Exports\\2026\\          Export's default
                   \\Animations\\, Timelapses\\, Contact sheets\\ ...   each Create tool
                   \\Staging\\                 import overflow when the PC's disk is full
                   \\Backups\\Catalog\\        catalog snapshots
                   \\Duplicates\\              copies a migration didn't keep (0.41)
                   \\Trash\\                   removed files, kept for a while (0.41)
                   \\Migration logs\\          before / after inventories and manifests (0.41)

Setting `lunelis_folder` (None = off: everything stays where it was). A
folder chosen for one thing in Settings (the backup folder, the Create
folder, network staging) still wins over this.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

EXPORTS = "Exports"
STAGING = "Staging"
BACKUPS_CATALOG = "Backups\\Catalog"
DUPLICATES = "Duplicates"
TRASH = "Trash"
MIGRATION_LOGS = "Migration logs"
FIXED = (EXPORTS, STAGING, BACKUPS_CATALOG, DUPLICATES, TRASH, MIGRATION_LOGS)

# Create tool -> its folder (title_text in ui/create_page.py).
CREATE_FOLDERS = {
    "Animation": "Animations", "Collage": "Collages", "Contact sheet": "Contact sheets",
    "Timelapse": "Timelapses", "Slideshow video": "Slideshows", "Before and after": "Before and after",
    "Prints": "Prints", "Focus stack": "Focus stacks", "Star trails": "Star trails",
    "Median stack": "Median stacks", "Panorama": "Panoramas", "HDR": "HDRs", "Batch copies": "Batch copies",
}


def root(settings) -> Path | None:
    v = settings.get("lunelis_folder")
    return Path(v) if v else None


def path(settings, sub: str) -> Path | None:
    r = root(settings)
    return r.joinpath(*sub.split("\\")) if r else None


def exports(settings, year: int | None = None) -> Path | None:
    r = path(settings, EXPORTS)
    return r / str(year or date.today().year) if r else None


def create(settings, tool_title: str | None) -> Path | None:
    r = root(settings)
    if r is None:
        return None
    return r / CREATE_FOLDERS.get(tool_title or "", tool_title or "Creations")


def make_folders(settings) -> list[Path]:
    """Create the fixed folders (the tool folders appear when first used)."""
    r = root(settings)
    if r is None:
        return []
    made = []
    for sub in FIXED:
        p = r.joinpath(*sub.split("\\"))
        if not p.exists():
            p.mkdir(parents=True, exist_ok=True)
            made.append(p)
    return made
