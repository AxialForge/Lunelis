# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.16.2] - 2026-10-04

The rest of the interface audit (docs/Interface-Audit.md): nothing slow is
left on the interface thread, and small windows and big text work.

### Changed

- **Pages open at once and fill in:** Backups, Library status, Migrate,
  Duplicates, Tags and Settings read their figures (drive checks, counts of
  the whole library, the cache sizes) in the background instead of freezing
  the window while they work.
- **The library refreshes in the background** during a scan and when you
  come back to it - and not at all when nothing changed.
- **Memory cards:** counting the photos on a card that was just inserted,
  listing the drives (a sleeping network drive's free space) and Clear the
  card no longer freeze the window. A card pulled out while it's being read
  is said, rather than shown as "No photos found".
- **Search** brings at most 2,000 changed photos up to date before showing
  results; a bigger backlog finishes in the background and the results
  update.
- **Editing:** Paste and Reset on more than 20 photos save in the
  background; Auto works out its settings in the background; Before shows
  at once; a mask's overlay is redrawn only when the mask's area changes;
  holding an arrow key no longer queues up previews of every photo passed.
- **Text follows Windows' "Make text bigger"**, and the grid's badges are
  larger.
- **Small windows:** the Edit page fits the 900 x 350 minimum (its bar folds
  to short button names); the status line keeps room for the message during
  a scan.
- Thumbnails on the Duplicates, Similar and Quarantine pages are sharp on
  scaled (125 %, 150 %) displays.
- Errors from Windows ("[WinError 32] ...") are said in words.

### Fixed

- Undo / Redo while cropping now moves the crop frame too.
- A job started on a whole source was named after the first source in the
  list.
- Restoring the catalog from a backup and dismissing event suggestions now
  ask first.
- Downloading an AI model from Settings no longer runs on the interface
  thread.
- A worker finishing just as Lunelis closes can no longer touch the closed
  catalog.

## [0.16.1] - 2026-10-03

### Changed

- **One repository.** Lunelis's source and its releases now live together
  in the public AxialForge/Lunelis repository; the updater looks there.
  AxialForge/Lunelis-releases is archived after this version (it gets this
  one release so older installs can update).
- Nothing personal is in the repository: no screenshots or release
  documents, no real paths, share names or network addresses.

## [0.16.0] - 2026-10-03

0.15.0 was tagged but never built; its Edit page and everything else in it
ship in this release.

### Added

- **Camera profiles:** the card's layout is recognised (Sony, Canon, Nikon,
  Fujifilm, GoPro, or generic) from a profile file; more cameras are a
  `camera_profiles.json` in the data folder.
- **Sidecars travel with their file:** a Sony clip's `.XML` (and any `.XMP`)
  is copied, verified and filed beside its clip; SUB proxies and THMBNL
  thumbnails stay on the card; late sidecars are picked up.
- **A re-inserted card isn't copied again.**
- **Clear the card** once everything on it is verified (memory cards only,
  asks first).
- **Undo for ratings, labels and flags** (Ctrl+Z, 20 steps); changing more
  than 500 photos at once asks first.
- **Keyboard shortcut sheet** (? or Help > Keyboard shortcuts).
- **The sidebar folds to icons by itself** on windows narrower than 1100 px
  (Settings > Appearance).
- The window's size, place and monitor, and the grid size from Ctrl+wheel,
  are remembered.

### Changed

- The formats other work builds on - the edit stack, user and automatic
  tags, camera profiles, and what a keeper is (a Pick) - are written down in
  `docs/Schemas.md` and pinned by tests. Catalog schema 27.
- Right-click selects the photo under the pointer before the Photo menu
  opens.

### Fixed (from the interface audit, docs/Interface-Audit.md)

- Rating, labelling or flagging a large selection could freeze the window
  for minutes.
- The Quarantine page checked every file on the NAS on the interface thread;
  restoring froze the window; an error while emptying left its dialog open.
- Cancel didn't stop exports and merges.
- Closing during a long copy or move could abort the program; closing after
  a big rating change froze while writing sidecars.
- After an HDR or panorama merge, every pending thumbnail was made on the
  interface thread.
- Starting an import, rebuilding thumbnails and the near-duplicate search
  no longer freeze the window (the search can be stopped).
- A migration plan can't be discarded while its job runs.
- Tag merges ask first; a failed damage check says so; long network paths
  no longer wrap in Duplicates.

## [0.15.0] - 2026-10-01

### Added

- **Edit page** (sidebar > Photos > Edit, or Photo > Edit > Open in the Edit
  page): an editing workspace - the photo large, the Edit panel full height,
  always in edit mode - working through a chosen set of photos: the library's
  selection, what the library shows now, edited photos, picks, 4 and 5 stars,
  or recent imports. Batch tools: Copy this edit, Paste to all (each photo
  keeps its own crop and rotation), Reset all, Export all. Ratings, labels
  and flags act on the photo being edited.

### Fixed

- Closing the window while an edit preview was still loading could log
  "Cannot operate on a closed database" errors.

## [0.14.0] - 2026-10-01

### Added

- **Library status page** (sidebar > Keep safe): the nine steps of every scan
  with live progress, Rescan everything and Stop; the library at a glance
  (photos, archived, missing, damaged, waiting for thumbnails or metadata,
  duplicates to review, quarantine, catalog and photo backups - each linking
  to its page); every source with whether it's reachable now, its size,
  missing files, last scan and its own Rescan. The status bar's library state
  and scan step link to it.
- **Archive** (Photo > Archive, Ctrl+Shift+H): archived photos leave the
  Library grid, search, filters, events and automatic albums without moving
  on disk; they stay in your own albums and in a new **Archive** album. The
  same command brings them back.
- **Move the Archive to a drive** (Library menu, or *Only photos in the
  Archive* on the Migrate page): archived photos are copied to the archive
  drive, verified, and only then set aside from their old place; they stay
  archived. Catalog schema 26.

## [0.13.0] - 2026-10-01

### Added

- **Icon sidebar.** The sidebar has icons for every page and folds to a
  narrow strip of icons (Collapse, Library > Sidebar: icons only, or
  Ctrl+B); the choice is remembered.
- **Scan progress you can see.** While the library updates, the status bar
  shows the step ("Step 3 of 9 · Reading metadata") with a progress bar. The
  library's state ("Last scanned ...", "Library up to date") stays on the
  right of the status bar, with a link to Damaged files when there are any.

### Changed

- **Fits small and scaled screens.** The window no longer insists on at least
  1145 x 763 (more than 1920 x 1080 at 150 % offers): it goes down to about
  900 x 350. Pages that don't fit scroll, the sidebar's page list scrolls on
  short screens, the search box and the photo view's details line give way,
  and active filter chips stay in one strip.
- A larger, easier-to-read status bar.

## [0.12.2] - 2026-10-01

### Added

- **Tilt the mouse wheel left or right** in the photo view (and while
  editing) to go to the previous or next photo. Holding the tilt keeps
  going, about three photos a second.

### Documentation

- The user manual names the parts of every screen (Sidebar, Top bar, Filter
  bar, Photo grid, Timeline, Photo bar, Photo, Filmstrip, Info panel, Edit
  panel, Status bar, Tab strip, and each page), shows a lettered "Parts of
  this screen" picture for each main screen, says in which part every
  control is, and lists every part name in an appendix.

## [0.12.1] - 2026-10-01

### Changed

- **No built-in import folders.** The import destination and the network
  staging folder no longer default to one particular network share: the first
  import asks where imports go, and network staging is off until you set it.
  If you relied on the old defaults, set them once in Settings > Import.
- **A catalog backup before every schema upgrade.** When a new version changes
  the catalog's structure, Lunelis first saves a `before-upgrade-vN` backup,
  because an older version can't open an upgraded catalog.
- Messages after moving copies or originals into quarantine now point to the
  Quarantine page instead of Explorer; the wiki too.

### Fixed

- A "Signal source has been deleted" error when the window closed while an
  edit preview was still rendering.

### Build

- CI pins PyInstaller 6.22.3, pytest 9.1.1 and lupa 2.8, so a rebuild of a
  tag uses the same tools; `.coverage` is no longer tracked.
- Release documentation package and the tools that generate it
  (`docs/_tools/`): user manual with annotated screenshots of every control,
  release overview, developer guide, UI inventory.

## [0.12.0] - 2026-09-28

From the second round of testing notes.

### Added

- **Photo view:**
  - **Zoom:** the mouse wheel zooms around the pointer, from fit up to
    400 %. Past the preview, the full-resolution photo loads in the
    background (60 MP RAW: ~2.5 s). Drag or middle-drag to pan.
  - **Filmstrip:** resizable, and the size is remembered.
  - **Shooting details** (mode, metering, focus, drive, flash, white
    balance, style, stabilization, DRO, serial, firmware...) plus an **All
    metadata** list.
- **Edit panel:**
  - **Sliders:** a number box you can type into; double-click a slider to
    reset it.
  - **Sections:** every section folds, and stays folded.
  - **Look:** cleaner section headers.
  - **Zoom:** wheel zoom works here too, including while cropping or
    masking.
- **Noise reduction** rebuilt:
  - measures the photo's own noise;
  - non-local means on 16-bit luminance, with a Noise detail slider;
  - full-resolution color noise reduction;
  - cached per photo while editing, run in strips for exports.
- **Settings:**
  - **General tab:** the page to open on, what the photo view's wheel
    does, the date format, and asking before quitting while work is
    running.
  - **New Edit tab:** the new-photo filter, live preview quality, unfold
    all sections, AI models (download/remove), caches (sizes, clear).
  - **Appearance:** thumbnail size presets plus Custom.
  - **Layout:** tabs centred.

### Fixed

- The Edit panel's Reset (and Auto, Done...) crash, and pages pulling a
  maximized window out of full screen. Both were first released in 0.11.1.
- Timers could fire into a closed catalog after the window closed (the
  start-up backup, resume-imports and update checks); closing now stops
  them. Tests fail on any unexpected error instead of opening the error
  dialog.

## [0.11.1] - 2026-09-27

### Fixed

- Reset (and Auto, Done, Save as filter, Adjust sliders, Delete mask) in the
  Edit panel crashed with "reset() only accepts 0 argument(s)": a button's
  click passed a value on to a signal that takes none. Every button wired
  that way was fixed, and tests now click the real buttons.
- Opening a page (Tags, Albums, Quarantine, search) pulled a maximized
  window out of full screen.

## [0.11.0] - 2026-09-27

The Quarantine page.

### Added

- **Quarantine page** (sidebar > Keep safe): everything Lunelis has set
  aside, in one list.
  - **What's listed:** exact copies, near-duplicates, copies skipped by a
    migration, and migrated originals.
  - **Details:** why each was set aside, when, its size, a total per drive,
    and whether the copy that was kept is still there.
  - **Restore** puts files back where they were.
  - **Empty** removes them for good, with these safeguards:
    - it refuses any file whose kept copy is missing or a different size;
    - files on this PC go to the Recycle Bin;
    - files on network drives are deleted (Windows has no Recycle Bin
      there), only after an extra "I understand" tick;
    - the catalog is backed up first, and every removed file is written to
      a new `purged` log (migration 25).

## [0.10.0] - 2026-09-27

Search.

### Added

- **Search box** (Ctrl+F). Every word must match. It searches file and
  folder names, camera (also by friendly name: "a7r v", "a7rv"), lens,
  tags, events and albums.
  - **Phrases:** `"las vegas"`.
  - **Dates:** `2024`, `june 2024`, `2024-06`.
  - **Kinds and states:** `raw`, `video`, `edited`, `picks`, `untagged`.
  - **Stars:** `4 stars`.
  - **Fields:** `tag:`, `camera:`, `lens:`, `folder:`, `file:`, `event:`,
    `album:`.
  - **Leaving out:** `-word`.
  - **Speed:** an SQLite full-text index (migration 24) that builds in
    ~1.5 s for 159k photos and is kept current by triggers. Queries take
    milliseconds.
  - **Suggestions:** tags, events, albums, cameras and lenses.

### Fixed

- "1 photos" in the library's count.

## [0.9.0] - 2026-09-27

Tags.

### Added

- **Tags (keywords):**
  - **Tagging:** type in the photo view's Info panel, or press Ctrl+T for a
    selection. The selection dialog shows "3 of 5" and your recent tags.
    Tag names autocomplete and ignore case.
  - **Nested tags:** `Places > Ohio > Cleveland`. Filtering by a parent
    includes everything inside it.
  - **Finding:** a **Tag** filter in the filter bar (searchable), and a
    **Tags** page (sidebar > Photos) showing a tree with photo counts.
  - **Managing:** rename (onto an existing tag merges them), merge into,
    new tag inside, delete.
  - **Sidecars:** tags are written to XMP as `dc:subject` +
    `lr:hierarchicalSubject`, which darktable and Lightroom read. Tags
    added elsewhere are imported on the next scan (added only, never
    removed). darktable's automatic `darktable|...` tags are kept but never
    shown.

## [0.8.0] - 2026-09-27

Editing, Phase B.

### Added

- **Tone curve:** RGB plus red/green/blue curves, drawn over the photo's
  histogram. The spline is monotone, so a curve never reverses a tone.
- **Masks:** local adjustments, as many as you like, each with its own
  exposure, contrast, highlights, shadows, whites, blacks, temperature,
  tint, vibrance, saturation, sharpening and noise reduction.
  - **Kinds:** Gradient, Radial (with feather), Brush (size, feather, flow,
    Alt to erase), Subject and Sky.
  - **Tools:** Invert, and a red overlay (O).
  - **Positions** stay fixed on the photo when the crop changes.
- **Subject and Sky masks** from local AI models: "silueta" (44 MB) and
  "skyseg" (176 MB). Each is downloaded once, on first use, after you
  confirm, and checked against a pinned SHA-256. They run offline on this
  PC through onnxruntime, and the edges are refined against the photo.
- **Lens corrections:**
  - **Profile:** from lensfun, covering distortion, fringing and vignetting,
    matched from EXIF. It covers 98 % of the library's lens-tagged photos
    and is off by default, since camera JPEGs are already corrected.
  - **Manual sliders:** distortion, vignetting and red/blue fringing.
- **Merge to HDR** (exposure fusion with alignment, 2-9 frames) and **Merge
  to panorama** (OpenCV stitcher, auto-crop, 2-30 frames).
  - Asks where to save each time and never overwrites.
  - Saves a 16-bit TIFF or a JPEG.
  - Cataloged at once when saved inside a library folder, with the first
    source's date and camera; recorded in the new `merges` table.
- The self-test checks onnxruntime, the lens database and OpenCV in the
  built app.

## [0.7.0] - 2026-09-27

Non-destructive editing.

### Added

- **Edit** in the photo view (E):
  - **Adjustments:** Exposure, Contrast, Highlights, Shadows, Whites and
    Blacks; Temperature, Tint, Vibrance, Saturation and Hue; Fade,
    Vignette, Sharpening and Noise reduction.
  - **Crop and rotate:** crop with aspect presets, straighten (with no
    empty corners), rotate, flip.
  - **Tools:** Auto, Reset, Before/after, undo/redo.
  - **Live preview** while you drag.
  - **RAWs** are decoded properly for editing (rawpy, camera white
    balance).
  - **Saving:** automatic, with no Save button. The file itself is never
    written.
- **Filters:** Vivid, B&W Classic, Filmic, Warm, Cool, Matte, Punch and
  Soft. Each is previewed on the photo, has an Amount slider, and stacks
  with manual adjustments.
  - **Your own filters:** Save as filter...; Adjust sliders unpacks a filter
    into the sliders.
  - **Deleting a filter** leaves the photos that use it looking the same.
- **Copy / paste / reset edits** across a selection (Ctrl+Shift+C / V). Crops
  stay with each photo.
- **Settings > Import > Start new photos with:** a filter for every newly
  imported photo.
- **Export** (Ctrl+Shift+E):
  - selected photos with their edits, as JPEG, TIFF or PNG;
  - full size or a long edge;
  - metadata kept, without GPS, or removed (RAW exports keep camera, lens
    and exposure);
  - name patterns `{name}` `{date}` `{n}`; never overwrites; sRGB profile
    embedded.
  - Full-size exports render in strips: a 60 MP RAW peaks at 1.5 GB of
    memory and takes about 7 s.
- An **EDITED** badge in the grid. Edited photos' thumbnails show the edit.
  A cached 2560 px rendering is shown in the photo view.
- Edits are written to XMP sidecars as `lunelis:EditStack`. Adobe `crs:`
  settings are never touched.
- The self-test checks the edit engine and export in the built app.

## [0.6.0] - 2026-09-27

Smarter duplicates and burst stacks.

### Added

- **Near-duplicates** tab on the Duplicates page: the same photo saved as a
  different file (resized, re-compressed, re-encoded by Google Takeout,
  exported). Photos are fingerprinted from their thumbnails after each
  thumbnail pass. Every group is shown side by side, with the best copy
  kept (not damaged, most pixels, not a Takeout re-encode). Only plainly
  lesser copies are suggested, and edited exports never are. Set aside one
  group or all suggested copies. Each goes to quarantine after a
  confirmation, and its ratings, albums and event move to the kept copy.
  Tuned on the real library: 4,975 groups, 4,076 copies (18.2 GB)
  suggested.
- **Burst stacks**: frames shot in quick succession show as one tile with a
  frame count. Double-click opens a stack in the photo view; **S** opens or
  closes it in the grid. Photo > Stack > Make this the stack cover /
  Unstack. There's a **Stack bursts** toggle in the filter bar, and the
  burst gap and minimum shots are in Settings > Library. The real library
  has 4,777 stacks holding 29,291 frames.

### Fixed

- The exact-copies list on the Duplicates page no longer picks up
  near-duplicate groups.

## [0.5.0] - 2026-09-27

Albums, from the first round of testing notes.

### Added

- Albums page (sidebar > Photos > Albums), Google Photos style: cover tiles
  in three sections. Clicking any tile opens its photos in the library with
  a filter chip.
  - **Your albums:** a photo can be in any number of them. There's a New
    album tile, and Photo > Album > Add to album (`Ctrl+Shift+A`), New
    album from selection and Remove from this album.
  - **Events:** your events live here now, with Event suggestions one
    click away.
  - **Automatic:** Favorites (4-5 stars), Picks, Videos, RAW files,
    Recently imported, Screenshots, and your eight most-used cameras.
- The photo view's Info panel lists a photo's albums.
- Import from any drive: the Import page lists every drive and your recent
  folders. A USB drive imports whole; other drives open a folder picker.
- Samsung phone model codes show as names (SM-G998U -> Galaxy S21 Ultra).

### Changed

- Events moved from the sidebar into the Albums page.
- Migration merges album membership into the copy that's kept, along with
  ratings and events.

## [0.4.0] - 2026-09-27

Looking at photos, from the first round of testing notes.

### Added

- Photo detail view (double-click a photo or press Enter): the photo large,
  a filmstrip underneath and an Info panel on the right.
  - The Info panel has clickable stars, labels and Pick/Reject; date,
    camera, lens, exposure, size, event, location with a map link,
    condition, file and sidecar; and Show in folder / Open with default
    app.
  - RAWs show their full-size embedded preview (about 0.7 s for a 60 MP
    ARW from the NAS). The neighbouring photos are loaded ahead, so
    stepping through is instant.
  - Zoom to 100 % with a double-click or Z. Left/Right, the mouse wheel and
    the filmstrip step through the library in its current order and
    filters. Esc goes back.
- Hover info: rest the mouse on a photo to see its date, camera, lens,
  exposure, size, rating and event (Settings > Appearance to turn it off).
- Timeline scrubber: in date sorts, years and months down the right edge.
  Hover to see the month; click or drag to jump, snapping to month starts.
- Ctrl + mouse wheel over the grid changes the thumbnail size.
- Sony model codes show as names (ILCE-7RM5 -> α7R V).

### Changed

- The grid size slider is smooth: tiles scale while you drag and sharpen
  when you let go, instead of flashing grey placeholders at every step.

## [0.3.1] - 2026-09-27

### Added

- Built-in updater (Settings > Updates):
  - checks the public releases page (AxialForge/Lunelis-releases), once a
    day at start-up or on demand;
  - shows what's new, then downloads the update and checks it against its
    published checksum;
  - swaps it in and restarts. The previous version is kept until the new
    one starts, and it starts again if the swap fails.
  - "Skip this version" is available, and a status-bar button appears when
    an update is waiting.
- Each release now carries a `.sha256` checksum, and its notes come from
  this changelog.

## [0.3.0] - 2026-09-27

Look and feel, from the first round of testing notes.

### Added

- Themes: Graphite (light), Midnight (dark) and High contrast, plus "Follow
  Windows", which switches live with Windows' light/dark setting.
  Settings > Appearance.
- Sidebar: the Lunelis logo, collapsible sections (Photos · Bring in &
  organize · Keep safe) that remember how you left them, and a centred
  Settings button and footer. Pages that aren't built yet are no longer
  shown greyed out.
- Settings in tabs across the top, with new Appearance (theme, default sort
  and thumbnail size, show videos), Thumbnails (count, rebuild), Updates
  (version) and Advanced (log) sections.
- A log file (`%LOCALAPPDATA%\Lunelis\logs\lunelis.log`) recording what
  Lunelis does and every error. An unexpected error now shows a message
  instead of failing silently, and Help > Report a problem collects the
  details to copy.
- The library remembers your sort and grid size.
- darktable plugin (Settings > darktable > Install): stars, colour labels and
  rejects sync both ways between Lunelis and darktable through an exchange
  folder, newest change winning. darktable's extra colour labels are never
  removed, and picks stay in Lunelis. It syncs at darktable start, on view
  changes, on exit and on "Sync with Lunelis"; Lunelis checks every 20 s.

### Changed

- Clearer controls throughout: bigger, higher-contrast tick boxes; clicking
  anywhere on a row of a tick list (Migrate, Backups, job sources) ticks it;
  focus outlines, hover states and styled scroll bars and sliders.

## [0.2.0] - 2026-09-27

The first test build: `Lunelis.exe` for Windows, built by CI.

### Added

- Windows build: a one-folder `Lunelis.exe` (no Python needed), zipped and
  attached to each GitHub Release. The build checks itself before it's
  published (`Lunelis.exe --self-test report.txt`): RAW, HEIC and video
  libraries, assets, catalog migrations, the window.
- Help > About Lunelis (version, data folder) and Help > Open the data folder.

- SQLite catalog with a numbered migration runner (schema v1: roots, files, exif,
  ratings, tags, albums, people, faces, duplicate groups).
- App window shell with the Lunelis icon; `python -m lunelis` entry point.
- Folder scan: Library > Add folder… / Rescan (F5) catalogs every photo and video
  under a folder in the background. Rescans only touch what changed; files that
  disappear are flagged as missing, never deleted, and an offline drive or share
  is refused rather than treated as empty.
- `python -m lunelis.importers.scan <folder>` for scanning and timing from a terminal.
- Metadata: after every scan, camera, lens, exposure, capture time (with UTC
  offset), GPS, orientation and the camera's own maker notes are read for new
  and changed files. Unreadable files are recorded, never fatal, and not retried
  until they change. `python -m lunelis.importers.metadata` runs it standalone.
- Files are identified by their contents, not their extension, so Google
  Takeout's re-saved JPEGs named `.ARW`/`.DNG` are no longer treated as RAW.
- Thumbnails: after metadata, every photo gets a 512px JPEG in
  `cache/thumbnails/`, upright and in sRGB. RAW and camera JPEGs use the
  preview the camera already embedded, so a 120 MB ARW or a 30 MB in-camera
  JPEG costs a few hundred KB of reading. HEIC is supported. Files that can't
  be previewed are marked "preview unavailable" instead of stopping the run,
  and deleting the cache folder simply rebuilds it.
  `python -m lunelis.raw.thumbnails` runs it standalone.
- Library grid: the main window now matches the Library mockup - sidebar,
  toolbar with sort (date, name, size, recently imported) and grid size, and a
  6-column grid of square thumbnails with format and rating badges. Scrolling
  stays smooth across 159k photos; click, Ctrl-click, Shift-click, arrow keys,
  Ctrl+A and Esc select. Thumbnails still being generated fill in as they land.
- Ratings, flags and colour labels: keys 0-5 set stars, 6-9 red/yellow/green/
  blue labels (press again to remove), P/X/U pick/reject/unflag - also in the
  new Photo menu and on right-click. Tiles show stars, labels and PICK/REJECT;
  rejects are dimmed.
- Filter bar: Rating (★1+ ... ★5, unrated), Label and Flag filters with
  removable chips and Clear all.
- XMP sidecars: ratings and labels already in darktable / Adobe / culling-tool
  sidecars are imported on every scan, and changes made in Lunelis are written
  back in the background - into the sidecar that already exists, or a new
  darktable-style `name.EXT.xmp`. Only the rating and label change; darktable
  edit history and everything else in the file is left byte for byte.
- Lunelis's own data (catalog, thumbnails, backups, sidecars) now lives in
  `%LOCALAPPDATA%\Lunelis` instead of the program folder; an existing catalog is
  moved there automatically on first start.
- Automatic catalog backups: a zipped snapshot daily and before any job that
  moves or removes files, newest 10 kept. `python -m lunelis.catalog.backup`
  lists, takes and restores them.
- Ratings and labels are written to a central sidecar store by default, so
  photo folders stay clean; sidecars that already exist next to photos (e.g.
  darktable's) are still kept in step. Library settings are stored in the catalog.
- The catalog is about half the size: full EXIF is now stored compressed.
- Jobs: long work runs as background jobs you can pause, resume and cancel
  (Library > Jobs, Ctrl+J). They keep their progress through a restart or
  power cut, can be limited to idle time or overnight and to a speed cap, and
  wait instead of failing when the NAS goes to sleep.
- Find duplicates (Library > Find duplicates...): pick sources or one folder;
  each folder is compared against the whole library as it finishes. A quick
  sampled hash finds likely copies; "Verify" compares every byte.
- Duplicates page: every group of identical copies, biggest savings first,
  the copy to keep (choose a preferred source), and "Move extra copies to
  quarantine" - a same-drive `_Lunelis Quarantine` folder, never a delete,
  verified groups only, never the last copy, catalog backed up first.
- Hash everything (Library menu): an optional full-hash job per folder, the
  integrity baseline for later checks.
- The app keeps its light theme even when Windows is in dark mode.
- Moved or renamed photos keep their ratings, labels, EXIF and thumbnail: a
  rescan recognises the file at its new place (by size and date, then EXIF,
  then hash) instead of treating it as deleted + new.
- Damaged files page: empty, zero-filled, unrecognisable, cut-short and
  corrupt files, found without re-reading the library, each with the best
  intact copy that survives elsewhere - or a clear "no intact copy".
- Check integrity (Library menu): re-hashes files against the "Hash
  everything" baseline and flags any whose contents changed silently.
- Videos: thumbnails (an upright frame from near the start), recording date,
  length (shown on the tile), size and GPS where the camera records it.
- Memory-card import (Import page, `Ctrl+I`): shows exactly which library
  folders a card's photos will go into, copies them to a local staging folder
  (spilling over to a network folder when the local disk is short), verifies
  every copy, files them with a storage template and verifies again. Says
  when the card can be removed and when it's safe to format. File names are
  never changed; photos already in the library are skipped; an interrupted
  import resumes (filing by itself, copying when the same card returns).
- Storage templates: your existing `Year\M-D-Year` layout by default, plus
  Year, Year\Month, Year\Month\Day, Year\Date, Import date, Camera\Year and
  custom templates with an optional import name.
- Tray mode: Lunelis keeps running in the tray, notices memory cards and
  offers to import them; optional Start with Windows.
- Settings screen (sidebar, `Ctrl+,`), saved as you change it:
  - sources on/off, and folders to skip inside a source (photos hidden,
    ratings kept, nothing on disk touched);
  - import destination, folder template with a live example, staging
    folders and free-space reserve;
  - where ratings are written as sidecars, with an offer to write existing
    ratings to the new place, and moving the sidecar folder;
  - the preferred source for keeping duplicates;
  - defaults for new jobs (when to run, idle time, hours, speed limit);
  - catalog backup folder, frequency and count, plus Back up now and Restore;
  - moving the data folder (done at restart, and safe to interrupt);
  - tray mode and Start with Windows.
- Events: trips and shoots as catalog objects (a photo is in one event at a
  time). Make one from selected photos (`Ctrl+E`), by naming a card import, or
  accept suggestions found in your folder names (`6-19-2026 Air Show`, trip
  day folders, the same folder in two pools) and in gaps in capture time.
  The Events page lists them, and "Show photos" filters the library.
- Named imports are events and file by the event's start date, so a
  multi-day trip lands in one folder; new `{event}` template token with
  `{event|date}` fallbacks and a "Year \ Event" preset.
- Migrate & consolidate: bring sources onto one drive with a storage
  template. A dry-run preview shows every destination, skipped duplicate and
  damaged copy, name clash, undated file and whether there's space. The
  migration runs as a pausable, resumable job: copy, verify by hash, carry
  sidecars, repoint the same catalog entry (ratings and events stay), then
  quarantine the original, or keep originals until "Release originals".
  RAW+JPEG pairs stay together, file names never change, and unverified
  copies are compared at copy time rather than copied twice.
- Backups (sidebar, Backups): mirror chosen sources to a USB drive, stick or
  network folder. Incremental and verified by hash; nothing is deleted from
  a backup, and a changed file's previous copy is kept. Files moved in the
  library are renamed in the backup, not re-copied. The catalog and
  sidecars go along. Runs by itself when the drive is plugged in, and finds
  the drive by serial whatever its letter. Verify re-reads every copy;
  Restore brings back missing or damaged files (damaged ones quarantined
  first), everything into a new folder, or the catalog.
- Fixed: a rescan flagged quarantined duplicates as missing.
- Wiki: user documentation in `docs/wiki/` - getting started, sources,
  browsing, ratings and sidecars, jobs, duplicates, damaged files, videos and
  Takeout, safety and backups, troubleshooting, roadmap, developer notes.
- Google Takeout: dates and locations from Google's JSON files for photos that
  have none of their own (Snapchat saves, screenshots, re-encoded uploads), so
  they sort where they belong instead of on the day the export was unzipped.
  A file's own EXIF is never overridden.

### Fixed

- A migration that fails partway now rolls back completely instead of leaving
  tables that break every later startup.
