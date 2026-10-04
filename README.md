# Lunelis

Local, Windows-first photo library app. RAW (Sony/Canon/Nikon/Fuji + others) and
standard formats, full EXIF, ratings, albums, face/content recognition, non-destructive
editing. Fully local for now - no server dependency; recognition is architected to be
swappable onto a network service (e.g. Immich's ML microservice) later if a GPU server
gets built, without a data-model change.

Full feature set, architecture, and build order:
https://claude.ai/code/artifact/ac290a70-ec83-42a3-abf5-c4d47ce373d2

UI mockups:
https://claude.ai/artifact/Y46mTotG3zBoRZzodt56Mt

## Stack

Python 3.13 + PySide6 (Qt), SQLite catalog, XMP sidecars as the source of truth for
edits/ratings/tags/faces. `rawpy` (LibRaw) for RAW decode, `piexif` for standard-format
EXIF. Chosen over Electron because recognition runs in-process (InsightFace/ONNX,
Phase 2) and Python's ML ecosystem is native here, not bridged.

## Requirements

- Windows 10/11, Python 3.13 (`py -3.13`). The pinned PySide6/rawpy builds have
  no wheels for older interpreters in this pin set.

## Setup

```bash
py -3.13 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt -e .
python -m pytest        # confirms the catalog schema is sound
python -m lunelis       # opens the app (or just `lunelis` once installed)
```

## Status: v0.21.0 - ask your library, stats

Catalog, scanning, EXIF, thumbnails, the library grid, ratings/labels with XMP
sync, the foundations (data folder, settings, catalog backups, central sidecar
store), the jobs engine with duplicate detection + quarantine, moved-file
re-linking, the damaged-file check, video metadata/thumbnails, Google Takeout
dates, memory-card import (storage templates, verified staging, tray) and the
Settings screen are done and tested against a real ~159k-file library. Phase 2
has begun: events (from a selection, a named import, or suggested from folder
names and capture-time gaps) and migration / consolidation (dry-run preview,
verified copy, catalog repointed, originals to quarantine or kept for review)
are built, and so are backups (incremental, verified, restore). v0.2.0 is the
first Windows build: download `Lunelis-v0.2.0-windows.zip` from Releases, unzip,
run `Lunelis.exe`. The darktable plugin is done (unreleased); next: smarter
duplicates.

Lunelis keeps its own data in `%LOCALAPPDATA%\Lunelis` - never in your photo
folders. Ratings go to a central sidecar store there by default.

Keys in the grid: `0`-`5` stars · `6`-`9` red/yellow/green/blue label ·
`P` pick · `X` reject · `U` unflag.

Current layout:
```
src/lunelis/
  catalog/schema.py   - SQLite schema + migration runner (Step 1, done)
  main.py             - app entry point / window shell (Step 0, done)
  __main__.py         - `python -m lunelis`
  paths.py            - the data folder (catalog, cache, backups, sidecars)
  settings.py         - library settings
  catalog/backup.py   - automatic catalog snapshots + restore
  jobs/engine.py      - pausable, resumable background jobs
  dupes/              - duplicate detection, verification, quarantine
  importers/video.py  - video dates, length and poster frames (PyAV)
  importers/takeout.py - Google Takeout JSON dates and locations
  importing/          - card import: storage templates, staging, placing
  importers/relink.py - moved/renamed files keep their ratings
  damage/check.py     - damaged-file check + surviving copies
  importers/formats.py - which extensions are cataloged / RAW
  importers/scan.py   - folder roots + incremental scan (Step 2, done)
  importers/metadata.py - EXIF extraction into the catalog (Step 3, done)
  catalog/ratings.py  - stars / flags / colour labels in the catalog (Step 6)
  xmp/sidecar.py      - find, read and surgically edit XMP sidecars
  xmp/sync.py         - sidecar <-> catalog import/export
  ui/main_window.py   - sidebar, toolbar, filter bar, background workers
  ui/grid.py          - the virtualized library grid (Step 5, done)
  ui/library.py       - the ordered photo list the grid shows
  ui/thumbcache.py    - async thumbnail loading + memory cache
  ui/theme.py         - colour tokens and the stylesheet
  raw/previews.py     - finds the embedded JPEG previews inside RAW files
  raw/thumbnails.py   - 512px thumbnail cache for every file (Step 4, done)
assets/
  icons/, logo/       - brand assets
tests/
```

## Wiki

How to use Lunelis - sources, browsing, ratings and sidecars, jobs, duplicates,
damaged files, backups - is in the [wiki](docs/wiki/Home.md).

## Development

Architecture, non-negotiables and the accumulated gotchas are in
[CLAUDE.md](CLAUDE.md). Read it before changing anything structural.

## License

MIT — see [LICENSE](LICENSE).
