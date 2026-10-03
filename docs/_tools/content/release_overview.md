# Lunelis 0.12.0 - Release Overview

What Lunelis is, how it is built, how it is released, where it came from and where it is going.

For readers who need the whole picture on a few pages: owners, reviewers and future maintainers.

## What Lunelis is

Lunelis is a photo library for Windows that runs entirely on your own PC. It catalogues photos and videos wherever they already live - local drives, USB drives and network storage (NAS) - without moving, renaming or changing them. On top of that catalogue it gives you everything needed to look after a large, messy, many-copies photo collection for the long term.

It was built to replace a mix of tools for one real library of about 159,000 files spread over a NAS, old backup drives and Google Takeout exports. It is designed so that nothing is ever lost by accident.

![The library window on the demo library](../../release-package/0.12.0/screenshots/01_main_window_clean.png)

### What it does

| Area | What Lunelis offers |
|---|---|
| Browse | A fast grid of the whole library with a timeline, sort, search (Ctrl+F) and filters for rating, label, flag and tag. Burst shots fold into one tile. |
| Look closely | A photo view with mouse-wheel zoom to 400 % (full resolution loads in the background), a filmstrip, and every detail the camera recorded. |
| Rate and organise | Stars, colour labels, pick and reject flags, nested tags, albums and events. All are written to standard XMP sidecars, so darktable and Lightroom can read them. |
| Edit | Non-destructive editing: filters, light, colour, tone curve, crop and straighten, lens corrections, noise reduction, and masks (gradient, radial, brush, and AI subject and sky). Also HDR and panorama merges, and exports to JPEG, TIFF and PNG. |
| Bring photos in | Memory-card import with a folder template and double verification. Lunelis notices cards from the tray. |
| Clean up | Exact and near-duplicate detection, damaged-file detection with the best surviving copy, and migration of a scattered library onto one drive. |
| Keep safe | Automatic catalog backups, incremental verified photo backups to USB or network drives, and a quarantine instead of deletion. |

### What makes it different

- **Originals are never touched.** Lunelis never edits, renames or rewrites a photo. Edits are stored as instructions and applied only to exported copies.
- **Nothing is deleted behind your back.** Set-aside files go to a `_Lunelis Quarantine` folder. Only the user can empty it, and local files then go to the Recycle Bin.
- **Local and free.** There are no accounts, no cloud and no subscriptions. It works offline; the only downloads are updates and, if you ask for them, two AI mask models with pinned checksums.
- **Built for big libraries on slow storage.** Thumbnails come from the previews cameras already embed. Long work runs as background jobs that can pause, run only while the PC is idle or only at night, and limit their read speed.

## Architecture

Lunelis is one Windows program (`Lunelis.exe`) containing a Python 3.13 interpreter. The user interface is PySide6 (Qt 6). Long work runs on Qt worker threads, so the window never freezes. Below the interface are core packages with no user interface at all; they can be tested on their own. Everything Lunelis knows lives in one SQLite catalog in the data folder; photo folders are only read, apart from three explicit actions (import copies in, migration moves, quarantine moves aside).

![Architecture: the program, its data folder, your photo folders and the optional internet services](../../release-package/0.12.0/architecture/architecture.png)

The diagram's source is `architecture/architecture.mmd` (Mermaid) in this package.

### The packages

| Package (src/lunelis/) | Responsibility |
|---|---|
| `ui` | Every window, page, dialog and widget: main window, grid, photo view, Edit panel, settings, tray. |
| `catalog` | The SQLite schema (25 numbered, forward-only migrations), ratings storage, compressed EXIF, catalog backups. |
| `importers` | Folder scanning, file-type detection by content, metadata, moved-file re-linking, Google Takeout dates, video metadata. |
| `raw` | Embedded RAW previews and the thumbnail pipeline. |
| `importing` | Memory-card import (card to staging to library, verified twice) and folder templates. |
| `xmp` | Reading and writing XMP sidecars, centrally or next to photos. |
| `albums`, `events`, `tags`, `stacks.py`, `search.py` | Organising: albums, events and their suggestions, nested tags, burst stacks, the full-text search index. |
| `dupes`, `damage` | Exact and near-duplicate detection, keeper rules, quarantine and the Quarantine page's logic; damaged-file checks. |
| `jobs` | The background jobs engine: pause, resume, cancel, idle-only, night-only, speed limit, retry when a network folder drops. |
| `migrate` | Migration and consolidation: a dry-run plan, then a verified copy-then-set-aside, one folder at a time. |
| `backups` | Photo backups to drives and network folders: incremental, verified, restorable, triggered when a drive is plugged in. |
| `edit` | The editing engine: edit stack, pipeline, masks, AI masks (onnxruntime), lens corrections (lensfun), noise reduction, HDR and panorama merges (OpenCV), export. |
| `darktable` | The darktable Lua plugin and its exchange files. |
| `updater.py` | Checks the public releases repository, verifies the SHA-256 checksum and swaps versions safely. |
| `settings.py`, `paths.py`, `log.py` | Settings with defaults, the data-folder location and moves, the log file. |

## Technology stack

| Component | Version in 0.12.0 | Used for |
|---|---|---|
| Python | 3.13.1 (CI: latest 3.13) | The language and the bundled runtime. |
| PySide6 / Qt | 6.11.2 / 6.11.2 | The user interface and worker threads. |
| SQLite | 3.45.3 (bundled with Python), FTS5 | The catalog and the search index. |
| rawpy / LibRaw | 0.27.1 / 0.22.1 | Reading RAW files for full-resolution viewing and editing. |
| Pillow, pillow-heif | 12.3.0, 1.8.0 | JPEG, PNG, TIFF and HEIC images. |
| exifread, piexif | 3.5.1, 1.1.3 | Reading EXIF; writing EXIF into exported JPEGs. |
| PyAV (FFmpeg libraries) | 18.1.0 (libavcodec 62.28) | Video metadata and poster frames, with no separate ffmpeg.exe. |
| NumPy | 2.5.3 | All image processing arrays. |
| OpenCV (headless) | 5.0.0.93 | Noise reduction, lens remapping, HDR merging and panorama stitching. |
| onnxruntime | 1.30.0 | The local AI subject and sky mask models. |
| lensfunpy / lensfun | 1.18.0 | Lens profiles for distortion, vignetting and fringing correction. |
| PyInstaller | 6.22.3 locally; `>=6.11` in CI | Building the one-folder `Lunelis.exe`. |
| pytest, lupa | 9.1.1, `>=2.0` | The test suite (322 tests); lupa runs the darktable Lua plugin in tests. |
| GitHub Actions | `windows-latest` runner | Tests on every push; builds, self-tests and releases on version tags. |

Exact runtime versions are pinned in `requirements.txt`; `pyproject.toml` holds the minimum versions.

## How a release is made

1. **Change and test.** Work is committed to `main` in AxialForge/Lunelis (private). Every push runs the full test suite on a Windows runner.
2. **Bump the version.** The version lives only in `pyproject.toml`. The same commit adds the release's section to `CHANGELOG.md`.
3. **Tag.** Pushing a tag `vX.Y.Z` starts the release job of the *Test & Release (Python)* workflow.
4. **Build.** CI installs the pinned requirements and runs `pyinstaller Lunelis.spec`, which makes a one-folder build.
5. **Self-test the build.** CI starts `Lunelis.exe --self-test`. This checks, inside the frozen program, that every native library loads and that editing, export, the AI runtime and the lens database work. A failure stops the release.
6. **Package.** The build is zipped as `Lunelis-vX.Y.Z-windows.zip`, a `.sha256` checksum file is written, and release notes are taken from the CHANGELOG section.
7. **Release.** The zip, checksum and notes are attached to a GitHub Release on the private repository.
8. **Publish for the updater.** The GitHub Release on the public **AxialForge/Lunelis** repository is what the built-in updater reads; CI creates it, so there is no separate publishing step.
9. **Update.** Installed copies find the new release (Settings > Updates, or the daily check), verify the checksum, and swap versions while keeping the previous one until the new one starts.

Lunelis is not code-signed, so Windows SmartScreen asks once per version.

## Version history

All releases so far were tagged in late September 2026; dates are those recorded in `CHANGELOG.md`.

| Version | Date | Highlights | Breaking changes |
|---|---|---|---|
| 0.2.0 | 2026-09-27 | First Windows build. Catalog, scanning of local and network folders, metadata, thumbnails, library grid, ratings, labels, flags and XMP sidecars. Also: data folder and catalog backups, background jobs, exact duplicates and quarantine, damaged-file check, videos and Google Takeout, card import, tray mode, Settings, events, migration and photo backups. | First release; catalog schema 17. |
| 0.3.0 | 2026-09-27 | Themes (Graphite, Midnight, High contrast, Follow Windows), sidebar sections, Settings in tabs, the log file and Report a problem. | None. |
| 0.3.1 | 2026-09-27 | Built-in updater from the public releases repository, with SHA-256 checksums. | Older builds must be updated by hand once. |
| 0.4.0 | 2026-09-27 | Photo view with Info panel and filmstrip, hover info, timeline scrubber, smooth grid size. | None. |
| 0.5.0 | 2026-09-27 | Albums page (with events inside), import from any drive, phone model names. | Events moved from the sidebar into Albums. Catalog schema 18. |
| 0.6.0 | 2026-09-27 | Near-duplicates with keeper rules; burst stacks. | Catalog schema 19. |
| 0.7.0 | 2026-09-27 | Editing, part A: light, colour, detail, crop and straighten, filters, copy and paste of edits, export, EDITED badge. | Catalog schema 21. |
| 0.8.0 | 2026-09-27 | Editing, part B: tone curve, masks including AI subject and sky, lens corrections, HDR and panorama merges. | Catalog schema 22. Optional model downloads (220 MB). |
| 0.9.0 | 2026-09-27 | Tags: nested keywords, Tag filter, Tags page, XMP in both directions. | Tag names must be unique regardless of capital letters. Catalog schema 23. |
| 0.10.0 | 2026-09-27 | Search box: full-text index with dates, kinds, stars and fields. | Catalog schema 24. |
| 0.11.0 | 2026-09-27 | Quarantine page: restore, and empty safely (Recycle Bin for local drives). | Catalog schema 25. |
| 0.11.1 | 2026-09-27 | Fixes: the Edit panel's Reset crash; pages pulling a maximized window out of full screen. | None. |
| 0.12.0 | 2026-09-28 | Mouse-wheel zoom and full-resolution loading, resizable filmstrip, shooting details and all metadata. Edit sliders with number boxes, foldable sections, rebuilt noise reduction. A fuller General tab and a new Edit tab in Settings. | None (schema 25). |

**About schema changes:** every catalog change is a numbered, forward-only migration that runs automatically the first time a new version starts. Each migration runs inside one transaction, so a failure rolls it back completely. An older version cannot open a catalog that a newer version has upgraded. To go back, restore an automatic catalog backup taken before the upgrade (Settings > Backups > Restore a backup); no extra backup is made at the moment of upgrading.

## Known limitations

- **Windows only.** Windows 10 and 11, 64-bit. A Mac version is on the long-term list.
- **Not code-signed.** SmartScreen warns once per version; signing certificates are a recurring cost the project does not take on.
- **Built-in network defaults.** This version's settings defaults include two fixed network paths (the import destination and the network staging folder) from the developer's own network. On other PCs they do not exist, and users must set their own folders before the first card import.
- **Canon CR3 metadata** is not read yet; the files are catalogued and shown.
- **The catalog must live on a local drive.** SQLite's locking is not reliable on network shares, so moving the data folder onto a share is refused.
- **No downgrades, and no backup at the moment of upgrade.** See *About schema changes* above. Rolling back relies on the newest daily catalog backup.
- **Merges ignore edits.** HDR and panorama merges read the original files, so edits on the source photos are not applied.
- **Videos** are catalogued, dated and given thumbnails, but cannot be edited or merged.
- **Migration has not yet been run on the real 159,000-file library**; it is tested on copies and test folders only.
- **Wording that predates the Quarantine page.** Two confirmation messages (after *Release originals...* on the Migrate page and *Move extra copies to quarantine...* on the Duplicates page) still say to empty quarantine in Explorer, not on the Quarantine page.
- **A harmless shutdown message.** If the window closes while an edit preview is still rendering, a "Signal source has been deleted" message can be printed to the console. Nothing is lost.

## Roadmap

| When | What |
|---|---|
| Next | Fix the built-in network defaults; update the old quarantine wording; read CR3 metadata; run the migration on the real library once the software is complete. |
| Editing | Layers, and more local-adjustment tools. |
| Backups | Optional encryption of photo backups. |
| Later | Face and content recognition (on this PC, or on a GPU server on your own network), map and timeline views, natural-language search, a Mac version. |
