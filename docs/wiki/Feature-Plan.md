# Feature plan

Every feature of Lunelis, built and planned, phase by phase, with the
version it arrived in or is planned for. Updated 2026-10-04 (v0.31.0), with the October 2026 feature list.

**Status:** ✅ done · 🔨 next · 📅 planned · 💤 someday

## Phase 1 - a library you can use every day ✅

All in **v0.2.0** (the first Windows build).

| Feature | What it does | Version |
|---|---|---|
| Catalog | One SQLite catalog of every file, with numbered, forward-only schema migrations. | ✅ 0.2.0 |
| Sources and scanning | Local drives, USB drives and network shares (NAS); rescan with F5; offline shares never mark photos missing. | ✅ 0.2.0 |
| Metadata | Camera, lens, exposure, date, GPS; files identified by their content, not their extension. | ✅ 0.2.0 |
| Thumbnails | 512-pixel thumbnails from the preview the camera already embedded, so even huge RAWs are quick. | ✅ 0.2.0 |
| Library grid | Every photo in one fast grid, sorted by date, name or size. | ✅ 0.2.0 |
| Ratings, labels, flags | 0-5 stars, five colour labels, Pick / Reject, by keyboard; filters for each. | ✅ 0.2.0 |
| XMP sidecars | Ratings and labels read from and written to sidecars (central folder, next to photos, or catalog only); darktable and Lightroom compatible. | ✅ 0.2.0 |
| Data folder and catalog backups | Everything Lunelis keeps lives in one data folder (movable); the catalog is backed up daily and before risky jobs. | ✅ 0.2.0 |
| Background jobs | Long work as jobs you can pause, resume, cancel, run only while idle or at night, and speed-limit. | ✅ 0.2.0 |
| Exact duplicates and quarantine | Byte-identical copies found and verified; extra copies set aside in quarantine, never deleted. | ✅ 0.2.0 |
| Moved files and damage check | Files moved or renamed outside Lunelis keep their ratings; empty, zero-filled or corrupt files are flagged with the best surviving copy. | ✅ 0.2.0 |
| Videos and Google Takeout | Video thumbnails and dates; dates and places from Takeout's JSON files. | ✅ 0.2.0 |
| Card import and tray | Import from a memory card into dated folders, verified twice; names never changed; Lunelis waits in the tray for cards. | ✅ 0.2.0 |
| Settings screen | Every setting, saved as you change it. | ✅ 0.2.0 |

## Phase 2 - managing the library ✅

| Feature | What it does | Version |
|---|---|---|
| Events | Trips and shoots as named date ranges, suggested from folder names and gaps in shooting time. | ✅ 0.2.0 |
| Migration / consolidation | Move a scattered library onto one drive with a folder template, one good copy of each photo, verified copy-then-set-aside. | ✅ 0.2.0 (built; not yet run on the real library) |
| Photo backups | Incremental, verified backups to USB drives, sticks or network folders; runs when the drive is plugged in; restore. | ✅ 0.2.0 |
| darktable plugin | darktable sees Lunelis's ratings, and Lunelis sees darktable's. | ✅ 0.2.0 |
| Themes and look | Graphite (light), Midnight (dark), High contrast, Follow Windows; foldable sidebar sections; Settings in tabs; a log and Report a problem. | ✅ 0.3.0 |
| Built-in updater | Checks the public releases page, verifies the download's checksum and swaps versions safely. | ✅ 0.3.1 |
| Photo view | The photo large with an Info panel and filmstrip; hover info in the grid; timeline scrubber; smooth grid size. | ✅ 0.4.0 |
| Albums page | Your albums as cover tiles, events inside, automatic albums (per camera...); import from any drive. | ✅ 0.5.0 |
| Near-duplicates | Resized and re-saved copies found by comparing images, with keeper rules. | ✅ 0.6.0 |
| Burst stacks | Quick-succession shots as one tile with a frame count. | ✅ 0.6.0 |
| Editing, part A | Light, colour, detail, crop and straighten, filters (looks), copy/paste edits, export to JPEG/TIFF/PNG; originals never changed. | ✅ 0.7.0 |
| Editing, part B | Tone curve; masks (gradient, radial, brush, AI subject and sky); lens corrections; HDR and panorama merges. | ✅ 0.8.0 |
| Tags | Nested keywords, Tag filter, Tags page, XMP both ways. | ✅ 0.9.0 |
| Search | One box (Ctrl+F) for names, folders, cameras, lenses, tags, events, albums, dates, stars and kinds. | ✅ 0.10.0 |
| Quarantine page | Everything set aside, why, restore, and empty safely (Recycle Bin for local drives). | ✅ 0.11.0 |
| Photo view zoom and noise reduction | Mouse-wheel zoom to full resolution; slider number boxes, foldable Edit sections; rebuilt noise reduction; General and Edit settings tabs. | ✅ 0.12.0 |
| Safer upgrades and imports | A catalog backup before every schema upgrade; no built-in import folders. | ✅ 0.12.1 |
| Wheel tilt, named screen parts | Tilt the wheel to change photo; the user manual names every part of the window. | ✅ 0.12.2 |
| Look and feel | Icon sidebar that folds to icons (Ctrl+B); scan progress and library state in the status bar; fits small and scaled screens. | ✅ 0.13.0 |
| **Library status page** | A sidebar page with each scan step and its progress, every source's health and last scan, damaged files and missing files, and Rescan buttons. | ✅ 0.14.0 |
| **Archive** | Archive photos out of the library (hidden from the grid, search and filters; one click brings them back), with an optional job that moves archived photos to an archive folder or drive. | ✅ 0.14.0 |
| **Edit page** | An editing workspace in the sidebar: the photo large, the Edit panel full height, a strip of photos to edit, and batch tools. | ✅ 0.15.0 |

## Phase 3 - the timeline from October 2026 📅

From *Lunelis: Features to Add* (October 3, 2026), in its build order.
Decisions made with it: scene tagging moves up (faces stay later); a
**keeper** is a photo flagged **Pick**; Create-tab outputs go to one
configurable output folder (Pictures\Lunelis creations by default, never
the data folder).

### 0.16 - foundations, interface audit, import hardening ✅ 0.16.0

| Feature | What it does |
|---|---|
| Locked schemas | Written down and tested: the edit-stack format, the tag schema (source user/auto, confidence, embeddings), the camera-profile format, and what a keeper is. |
| Interface audit | The 31 interface files checked for speed, layout, keyboard, undo, messages and reliability; blockers fixed. Includes the `?` shortcut sheet and the automatic icon sidebar on narrow windows. |
| Camera profiles as data | Each camera brand's card layout, file types and sidecar rules in a config file, chosen from EXIF Make and Model; a generic profile for anything else. |
| Whole-card scan | Every DCIM folder (100MSDCF, 101MSDCF...) feeds one import. |
| Sony video sidecars | A clip and its .XML / .XMP sidecar are copied, verified and filed together, with a re-check for late sidecars. SUB proxies and THMBNL thumbnails are skipped. |
| Import history | A re-inserted card never imports twice (hash, file name and time). |
| Clear the card | Offered only after every file is verified. |
| Interface audit, the rest (0.16.2) | Every page reads in the background; text follows Windows' text size; the Edit page fits the smallest window. |

### 0.17 - Create tab, round one ✅ 0.17.0

| Feature | What it does |
|---|---|
| Create section | A new sidebar section with a card per tool; a shared photo picker (selection, album, folder). |
| Shared export engine | Size, format, quality and social presets (an editable JSON file), always with the original size too. |
| GIF, MP4 and WebP maker | A burst or selection to an animation: trim, reorder, speed, loop. MP4 / WebP for quality, GIF for compatibility. |
| Collage | Grid templates, spacing, border, rounded corners, background, aspect presets (1:1, 4:5, 9:16, 16:9, 3:2, 2:3), drag-swap cells, pan and zoom per cell. Free placement is in the engine; drawing your own cells on the page comes with round two (0.22). |
| Batch tools | Watermark, resize, convert, rename and strip EXIF - always to new files. |

### 0.18 - phone and USB-stick import ✅ 0.18.0

| Feature | What it does |
|---|---|
| Stick detection | A one-click import offer when a USB stick appears. |
| Phone formats | HEIC/HEIF, iPhone Live Photos (HEIC + MOV kept together), Android motion photos, .AAE edit sidecars. |
| Capture time first | EXIF dates, because phone transfers change file dates. |
| Library-wide dedup | The same photo arriving twice is recognised by its content. |
| Later | Direct MTP for Android; LAN / Wi-Fi sync. |

### 0.19 - culling and library tools ✅ 0.19.0

| Feature | What it does |
|---|---|
| Culling mode | Full-screen, keyboard-driven pick / reject / rate, A/B compare of 2-4 shots zoomed together, burst grouping. Later: sharpness and closed-eye checks. |
| Smart albums | Saved searches, e.g. ISO above 3200 AND 5 stars AND a given lens. |
| RAW+JPG pairing | One tile per shot, built on burst stacks. |

### 0.20 - scene-aware tagging ✅ 0.20.0

| Feature | What it does |
|---|---|
| Local scene model | An opt-in CLIP-style model (pinned download, SHA-256 checked) reads the cached 512 px thumbnails - no RAW decode - and stores an embedding per photo. |
| Scene tags | Zero-shot labels from an editable list (beach, night sky, food, macro...) as nested tags such as Scene > Beach, with a confidence. |
| Review queue | Accept, reject, or bulk-accept above a threshold. Only accepted (manual) tags reach XMP sidecars. |
| Tagging job | Resumable, pausable, idle-only, incremental after the first pass. |

### 0.21 - ask your library, shooting stats ✅ 0.21.0

| Feature | What it does |
|---|---|
| Ask your library | Natural-language search ("sunset on a beach, A7R V, 2024"), "find similar" and "more like these", with chips showing how the sentence was understood. |
| Your shooting stats | Keeper rate (Picks) by lens, focal length, aperture, ISO and shutter speed; focal-length use per lens; shoots per month; habits; a yearly recap image. |

### 0.22 - Create tab, round two ✅ 0.22.0

Contact sheet (PDF / PNG with captions), timelapse builder (deflicker,
stabilization), slideshow video (music, transitions), before and after
(slider or side by side), print layout (4x6, 5x7, 8x10 sheets), and free collage
layout (draw, move and resize your own cells - the engine already lays them out).

### 0.23 - video and GIF playback ✅ 0.23.0 · 0.24 - S-Log previews ✅ 0.24.0

Videos and animated GIFs play in the library and the photo view, with
trim. Then Sony S-Log3 footage shown through a LUT (to Rec.709) so it looks
right without grading; your own .cube LUTs.

### 0.25 - Lunelis noticed ✅ 0.25.0

Spots sequences that could become something - HDR brackets, panoramas,
focus stacks, timelapses, star trails - checks they really overlap, and
offers to build them ("14 frames look like a panorama. Build it?"). Never
automatic; dismissals are remembered.

### 0.26 - resilience and views ✅ 0.26.0

| Feature | What it does |
|---|---|
| Offline-NAS view | Thumbnails and details stay visible while the NAS is unreachable, marked offline. |
| Protection indicator | Protected / not protected per photo from backup state; scheduled integrity checks. |
| Map and calendar | Photos by GPS on a map (opt-in, labelled online tiles) and an "on this day" calendar. |

### 0.27 - Learn My Look ✅ 0.27.0 · 0.28 - Autopilot Import ✅ 0.28.0

**Learn My Look:** a small local model learns your editing style from your
own edit history and suggests an edit (never applied automatically, with
before / after and how many examples it learned from).
**Autopilot Import:** card in, finished shoot out - verified import, bursts
collapsed to the best frame, scene tags, an event with a suggested name,
suggested edits in your style, a draft album and highlight reel, then a
*Review your shoot* screen. Every stage skippable; nothing destructive
before the final review.

### 0.29 - editing suite ✅ 0.29.0

Healing and clone, spot removal, red-eye, more local adjustments; a preset
library with import / export; virtual copies; colour management (ICC
working space and output, soft-proofing, monitor profile); export presets
for web, print and social with output sharpening.

### 0.30 - sensor dust map ✅ 0.30.0

Finds dust spots shared across one camera's photos (strongest at f/8 and
narrower, in smooth bright areas), shows a map with a confidence per spot,
and batch-heals them after a preview. Notices sensor cleanings and new dust.

### 0.31 - family gallery (LAN) ✅ 0.31.0

A read-only web gallery of chosen albums on the home network only: QR code
and link per album, optional PIN, no accounts, resized copies (originals
only if you allow it), a TV slideshow mode. LAN-bound, one token per album,
rate-limited.

### 0.32 - Create tab, round three

Focus stacking, star trails and median stacks, and panorama / HDR merges as
Create tools.

## Someday 💤

| Feature | Notes |
|---|---|
| Faces | Find and group people on this PC, behind the recognition interface so it can move to a GPU server. |
| Full-screen slideshow viewer | Any selection, album or search on screen, with timing and transitions (the Create tab makes slideshow videos in 0.22). |
| Canon CR3 metadata | CR3 files are catalogued and shown; their camera details aren't read yet. |
| Editing layers | After the edit-stack schema has settled. |
| Encrypted backups | Optional encryption of photo backups. |
| RAW look matching the camera | Default RAW rendering that matches the camera's own JPEG. |
| Smaller download | AI and lens components as optional add-ons. |
| Mac version | The same library on macOS. |

## Rules every new feature keeps

Local-first with zero telemetry (anything online is opt-in and labelled);
originals are never modified or renamed; nothing is hard-deleted by a job;
schema changes are migrations only; no RAW decode in the scroll path;
recognition sits behind its own interface; Lunelis's own files stay in the
data folder; Create outputs are new files you asked for and never overwrite
sources; each user-visible feature updates its wiki page and the Roadmap in
the same commit.
