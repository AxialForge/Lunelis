# Lunelis 0.33 - Release Overview

What Lunelis is, what it is for and how it works, told three times: simply, in more depth, and in full technical detail. Then how it is released, where it came from and where it is going.

Read as far as you need. **Simple** is one page for anyone. **Medium** is for people who will use it every day. **Advanced** is for reviewers and future maintainers.

## Simple: Lunelis in one page

**What it is.** Lunelis is a photo library program for a Windows PC. You point it at the folders where your photos and videos already are: your PC, a USB drive or a network drive. It shows them all in one place, newest first, and helps you look after them.

**What it is for.** Big family and hobby photo collections get messy. There are copies of copies, old backup drives, phone exports, and folders nobody remembers. Lunelis lets you find, rate, sort, edit and share your photos. It finds the duplicates and the damaged files, makes backups, and helps you move everything onto one drive.

**How it works.** Lunelis keeps a notebook (the *catalog*) about every photo on your own PC. It never changes, renames or deletes your photos. Edits are kept as notes and only applied to new copies when you export. Anything it sets aside goes into a quarantine folder you can restore from. Nothing goes to the cloud, and there are no accounts or subscriptions.

![The library window on the demo library](../../release-package/0.33.0/screenshots/01_main_window_clean.png)

**Three things people like most:**

- **It is safe.** Your originals are never touched, and nothing is deleted behind your back.
- **It is fast on big libraries.** It was built for one real library of about 159,000 files spread over a NAS, old drives and Google Takeout exports.
- **It does the boring work for you.** After a card import it can pick the best shot of each burst, tag the scenes, name the trip and draft an album. Every step can be undone.

## Medium: what it does and how you use it

### The main areas

| Area | What Lunelis offers |
|---|---|
| Browse | A fast grid of the whole library with a timeline, sort, search (Ctrl+F) and filters for rating, label, flag, tag and backup state. Bursts fold into one tile. The **Map** shows photos by where they were taken, and **On this day** shows photos from the same date in past years. |
| Find | Full-text search, **smart albums** (saved rules such as "4 stars, ISO 3200 and up"), and **Ask your library**: a plain sentence like "sunset on a beach, 2024" turned into filters. |
| Look closely | A photo view with zoom to 400 %, a filmstrip and every camera detail. **Videos and GIFs play** with trim-to-a-new-file, and Sony S-Log3 clips play with a built-in look. |
| Rate and cull | Stars, colour labels, pick and reject flags. **Culling** is a full-screen, keyboard-only pass with side-by-side compare and undo. Everything is written to standard XMP sidecars. |
| Organise | Albums, events (trips found from gaps in time), nested tags, and optional **scene tags** suggested by a model that runs on this PC. |
| Edit | Non-destructive editing: light, colour, tone curve, crop, lens corrections, noise reduction, masks (including AI subject and sky), **retouch** (heal, clone, red-eye) and **virtual copies**. **My look** learns your editing style from your own edits. Exports to sRGB, Display P3 or Adobe RGB with sharpening and presets. |
| Create | Thirteen tools that make new files from your photos: animations, collages, batch copies, contact sheets, timelapses, slideshow videos, before-and-after, prints, focus stacks, star trails, median stacks, panoramas and HDR. |
| Bring photos in | Memory-card, phone and USB-stick import with folder templates and double verification. **Autopilot** sorts out the shoot afterwards, with **Review your shoot** to undo any step. |
| Clean up | Exact and near-duplicates with keeper rules, damaged-file checks, and **migration** of a scattered library onto one drive. |
| Keep safe | Automatic catalog backups, verified photo backups, a quarantine instead of deletion, rolling integrity checks, a **protection** marker on every photo, and a **sensor dust** map per camera. |
| Share at home | **Family gallery**: share an album with phones and TVs on your home network with a link or QR code. It is never reachable from the internet. |
| Notice | **Lunelis noticed** spots HDR brackets, panoramas, focus stacks, timelapses and star trails in your library and offers to build them. |

### A typical day

1. **Plug in the card.** Lunelis offers the import from the tray. Photos are copied, checked twice, and filed by date.
2. **Autopilot runs.** It picks burst covers, tags scenes, suggests an event name and drafts an album. A banner leads to **Review your shoot**.
3. **Cull.** Press Ctrl+K and go through the shoot with P (pick), X (reject) and the number keys.
4. **Edit the keepers.** Use My look as a starting point, or paste one edit to many photos on the Edit page.
5. **Share or make something.** Export with a preset, share the album on the home network, or make a slideshow on the Create page.

### What makes it different

- **Originals are never touched.** Lunelis never edits, renames or rewrites a photo. Edits are stored as instructions and only applied to exported copies.
- **Nothing is deleted behind your back.** Set-aside files go to a `_Lunelis Quarantine` folder. Only you can empty it. Local files then go to the Recycle Bin, and network files are checked byte for byte against the kept copy first.
- **Local and free.** There are no accounts, no cloud and no subscriptions. The only downloads are updates and, if you ask for them, the AI models (with pinned checksums) and map tiles.
- **Built for big libraries on slow storage.** Thumbnails come from the previews cameras already embed. Long work runs as background jobs that can pause, run only while the PC is idle or at night, and limit their read speed. When a NAS goes to sleep, its photos stay browsable and are marked OFFLINE.
- **Suggestions, not surprises.** Scene tags, events, noticed merges, dust heals and My look are all offered first. Nothing happens until you accept, and every step has an Undo.

## Advanced: how it is built

### Architecture

Lunelis is one Windows program (`Lunelis.exe`) that contains a Python 3.13 interpreter. The user interface is PySide6 (Qt 6). Long work runs on Qt worker threads, so the window never freezes. Below the interface are core packages with no user interface, which can be tested on their own.

Everything Lunelis knows is in one SQLite catalog in the data folder. Photo folders are only read, apart from three explicit actions:

- import copies files in;
- migration moves files;
- quarantine moves files aside.

![Architecture: the program, its data folder, your photo folders, the home network and the optional internet services](../../release-package/0.33.0/architecture/architecture.png)

The diagram's source is `architecture/architecture.mmd` (Mermaid) in this package.

### The packages

The source is about 37,700 lines of Python in `src/lunelis/`.

| Package (src/lunelis/) | Responsibility |
|---|---|
| `ui` | Every window, page, dialog and widget (47 modules): main window, grid, photo view, video player, Edit panel, Create page, culling, map, calendar, settings, tray. |
| `catalog` | The SQLite schema (37 numbered, forward-only migrations), ratings storage, compressed EXIF, catalog backups. |
| `importers` | Folder scanning, file-type detection by content, metadata, moved-file re-linking, Google Takeout dates, video metadata. |
| `raw` | Embedded RAW previews and the thumbnail pipeline. |
| `importing` | Card, phone and stick import (card to staging to library, verified twice), camera profiles, folder templates, and Autopilot. |
| `xmp` | Reading and writing XMP sidecars, centrally or next to photos. |
| `albums`, `events`, `tags`, `stacks.py`, `pairs.py` | Albums and smart albums, events and their suggestions, nested tags, burst stacks, RAW+JPEG pairs. |
| `search.py`, `ask.py` | The full-text search index, and the plain-sentence query reader. |
| `recognize` | Scene tags: a local CLIP model (onnxruntime) with embeddings stored per photo and a review queue. |
| `video` | Stream-copy trimming, S-Log3 detection and the built-in Rec.709 look, `.cube` LUTs. |
| `create` | The one Create engine and its 13 tools, including alignment and stacking for focus, star-trail and median stacks. |
| `dupes`, `damage`, `dust.py` | Exact and near-duplicates, keeper rules and quarantine; damaged-file checks; sensor dust maps and heals. |
| `jobs` | The background jobs engine (pause, resume, cancel, idle-only, night-only, speed limit, offline retry) and rolling integrity checks. |
| `migrate`, `backups` | Migration and consolidation (dry-run plan, then verified copy-then-set-aside); photo backups and per-file protection. |
| `edit` | The editing engine: stack (version 2), pipeline, masks, AI masks, retouch, lens corrections, noise reduction, HDR and panorama, My look, ICC profiles and export. |
| `noticed.py`, `reach.py`, `stats.py`, `history.py` | Lunelis noticed; source reachability for the OFFLINE marks; shooting stats; import history. |
| `gallery.py` | The family gallery: a small HTTP server for home addresses only, with one random key per album, an optional PIN and resized copies without location. |
| `darktable` | The darktable Lua plugin and its exchange files. |
| `updater.py`, `selftest.py` | Checks GitHub Releases on AxialForge/Lunelis, verifies SHA-256 and swaps versions safely; the frozen-build self-test. |
| `settings.py`, `paths.py`, `log.py` | Settings with defaults and validation, the data-folder location and moves, the log file. |

### Technology stack

| Component | Version in 0.33.0 | Used for |
|---|---|---|
| Python | 3.13.1 (CI: latest 3.13) | The language and the bundled runtime. |
| PySide6 / Qt | 6.11.2 | The user interface, worker threads, video playback and the map. |
| SQLite | 3.45.3 (bundled with Python), FTS5 | The catalog and the search index. |
| rawpy / LibRaw | 0.27.1 / 0.22.1 | Reading RAW files for full-resolution viewing and editing. |
| Pillow, pillow-heif | 12.3.0, 1.8.0 | JPEG, PNG, TIFF, GIF, WebP and HEIC images. |
| exifread, piexif | 3.5.1, 1.1.3 | Reading EXIF; writing EXIF into exported JPEGs. |
| PyAV (FFmpeg libraries) | 18.1.0 | Video metadata, poster frames, trimming and Create videos, with no separate ffmpeg.exe. |
| NumPy | 2.5.3 | All image-processing arrays. |
| OpenCV (headless) | 5.0.0 | Noise reduction, lens remapping, alignment, HDR, panoramas, stacking, retouch. |
| onnxruntime | 1.30.0 | The local AI models: subject and sky masks, and CLIP scene tags. |
| lensfunpy / lensfun | 1.18.0 | Lens profiles for distortion, vignetting and fringing correction. |
| PyInstaller | 6.22.3 locally; `>=6.11` in CI | Building the one-folder `Lunelis.exe`. |
| pytest, lupa | 9.1.1, 2.8 | The test suite (about 560 tests); lupa runs the darktable Lua plugin in tests. |
| GitHub Actions | `windows-latest` runner | Tests on every push; builds, self-tests and releases on version tags. |

Exact runtime versions are pinned in `requirements.txt`. `pyproject.toml` holds the minimum versions.

### Catalog changes since 0.12

| # | Migration |
|---|---|
| 26 | Archive: archived photos leave the library but stay in albums |
| 27 | Imports: camera profile, sidecars filed with their clip, notes |
| 28 | Phones: import item kinds; motion photos |
| 29 | RAW+JPEG pairs |
| 30 | Scene tags: embeddings per photo per model; rejected suggestions |
| 31 | Lunelis noticed: suggested merges and stacks |
| 32 | Rolling integrity checks; backups looked up per file |
| 33 | Autopilot runs, their stages and what they made |
| 34 | Edit stack version 2 (retouch spots): stored stacks rewritten |
| 35 | Virtual copies |
| 36 | Sensor dust maps and their heals |
| 37 | Family gallery shares |

Every migration runs once, inside one transaction, the first time a new version starts. Since 0.12.1, a catalog backup is taken just before any schema upgrade.

### Security model, in short

- **No inbound access** except the family gallery. It only answers home-network addresses (an explicit list of private ranges), needs a long random key per album, and can add a PIN. The PIN is stored as a salted hash, with a lockout after five wrong tries, a rate limit and request size limits.
- **Downloads are pinned.** Updates are checked against the release's SHA-256 before anything is unpacked. AI models have fixed SHA-256 fingerprints in the code.
- **Untrusted input is treated as such.** Metadata is always shown as plain text. Crafted Takeout JSON, `.cube`, Sony XML and oversized images are size-limited. All SQL is parameterised.
- **Destructive steps are verified.** Moves are copy, verify, then set aside. A permanent delete on a network share compares the kept copy byte for byte first.

The October 2026 audit (`docs/Audit-2026-10.md`) lists every finding and its fix.

## How a release is made

1. **Change and test.** Work is committed to `main` in the public AxialForge/Lunelis repository. Every push runs the full test suite on a Windows runner.
2. **Bump the version.** The version lives only in `pyproject.toml`. The same commit adds the release's section to `CHANGELOG.md`.
3. **Tag.** Pushing a tag `vX.Y.Z` starts the release job of the *Test & Release (Python)* workflow.
4. **Build.** CI installs the pinned requirements, writes the exe's version information and runs `pyinstaller Lunelis.spec` (a one-folder build).
5. **Self-test the build.** CI starts `Lunelis.exe --self-test`. Inside the frozen program it checks that every native library loads and that editing, export, the AI runtime, video and the lens database work. A failure stops the release.
6. **Package and publish.** The build is zipped as `Lunelis-vX.Y.Z-windows.zip` with a `.sha256` file. The notes come from the CHANGELOG, and everything is attached to a GitHub Release on AxialForge/Lunelis.
7. **Update.** Installed copies find the new release (Settings > Updates, or the daily check), verify the checksum, and swap versions. The previous version is kept until the new one starts.

Lunelis is not code-signed, so Windows SmartScreen asks once per version.

## Version history

Dates are those recorded in `CHANGELOG.md`. The full notes for every version are in *RELEASE_HISTORY* in this package.

| Version | Date | Highlights | Catalog |
|---|---|---|---|
| 0.2.0 | 2026-09-27 | First Windows build: catalog, scanning, metadata, thumbnails, grid, ratings, XMP; backups, jobs, duplicates, quarantine, card import, events, migration. | 17 |
| 0.3.0 - 0.6.0 | 2026-09-27 | Themes and Settings tabs; the built-in updater; photo view and timeline; Albums page; near-duplicates and burst stacks. | 18 - 19 |
| 0.7.0 - 0.8.0 | 2026-09-27 | Editing parts A and B: light, colour, crop, filters, export; tone curve, masks with AI, lens corrections, HDR and panorama. | 21 - 22 |
| 0.9.0 - 0.11.1 | 2026-09-27 | Tags, the search box, the Quarantine page. | 23 - 25 |
| 0.12.0 - 0.12.2 | 2026-09-28 - 10-01 | Zoom, metadata, slider number boxes; no built-in import folders; a backup before upgrades. | 25 |
| 0.13.0 | 2026-10-01 | Icon sidebar, visible scan progress, small and scaled screens. | 25 |
| 0.14.0 | 2026-10-01 | Library status page; Archive. | 26 |
| 0.15.0 / 0.16.x | 2026-10-01 - 10-04 | Edit page with batch tools; camera profiles; one public repository; interface audit. | 27 |
| 0.17.0 | 2026-10-04 | Create page: animations, collages, batch copies. | 27 |
| 0.18.0 | 2026-10-04 | Phone and USB-stick import, Live Photos, motion photos. | 28 |
| 0.19.0 | 2026-10-04 | Culling, smart albums, RAW+JPEG pairing. | 29 |
| 0.20.0 | 2026-10-04 | Scene suggestions with a review queue. | 30 |
| 0.21.0 | 2026-10-04 | Ask your library; shooting stats. | 30 |
| 0.22.0 | 2026-10-04 | Contact sheets, timelapses, slideshow videos, before-and-after, prints. | 30 |
| 0.23.0 - 0.24.0 | 2026-10-04 | Video and GIF playback with trim; S-Log3 looks and LUTs. | 30 |
| 0.25.0 | 2026-10-04 | Lunelis noticed. | 31 |
| 0.26.0 | 2026-10-04 | Offline sources stay browsable, protection marker, rolling checks, Map, On this day. | 32 |
| 0.27.0 - 0.28.0 | 2026-10-04 | My look; Autopilot Import with Review your shoot. | 33 |
| 0.29.0 | 2026-10-04 | Editing suite: retouch, virtual copies, colour profiles, export presets. | 35 |
| 0.30.0 | 2026-10-04 | Sensor dust map. | 36 |
| 0.31.0 | 2026-10-04 | Family gallery. | 37 |
| 0.32.0 | 2026-10-04 | Create round three: focus stack, star trails, median, panorama, HDR. | 37 |
| 0.33.0 | 2026-10-04 | Hardening after the October audit: security, feature gaps, ease of use. | 37 |

**About schema changes:** an older version cannot open a catalog that a newer version has upgraded. To go back, restore the catalog backup taken just before the upgrade (Settings > Backups > Restore a backup).

## Known limitations

- **Windows only.** Windows 10 and 11, 64-bit.
- **Not code-signed.** SmartScreen warns once per version. Signing certificates are a recurring cost the project does not take on.
- **No installer yet.** Lunelis ships as a zip to unpack. An installer with first-run setup is the next planned piece of work.
- **The update checksum comes from the same place as the download.** It protects against broken downloads, not against a compromised release page (audit finding S11).
- **Canon CR3 metadata** is not read yet. The files are catalogued and shown.
- **The catalog must live on a local drive.** SQLite's locking is not reliable on network shares, so moving the data folder onto a share is refused.
- **No downgrades.** See *About schema changes* above.
- **A source can't be re-pointed.** If a drive letter or network path changes, the folder must keep its old path; adding it again at a new path catalogues the photos as new.
- **HDR and panorama merges read the originals.** Edits on the source photos are not applied (focus, star-trail and median stacks do use them).
- **Migration has not yet been run on the real 159,000-file library.** It is tested on copies and test folders only.
- **The working colour space is fixed** (sRGB primaries, floating point). Wide-gamut output is a conversion at export.

## Roadmap

| When | What |
|---|---|
| Next | A Windows installer with a first-run setup: libraries, start with Windows, tray, model downloads and data-folder location. Updates stay in the app. |
| Soon | Run the migration on the real library; read CR3 metadata. |
| Editing | Layers; a camera-matched RAW look. |
| Library | Faces, found and grouped on this PC; a full-screen slideshow viewer. |
| Backups | Optional encryption of photo backups. |
| Later | A smaller download; a Mac version. |
