# Lunelis — project guide for Claude Code

Lunelis is a local, Windows-first desktop photo library: RAW (Sony/Canon/Nikon/Fuji
and anything LibRaw reads) plus standard formats, full EXIF, ratings, albums,
duplicate detection and non-destructive editing, with face/content recognition in
Phase 2. It is Python 3.13 + PySide6, a single SQLite catalog, and XMP sidecars.
It is deliberately **not** a photo container: it indexes files where they already
sit and never moves, renames or rewrites them unless the user explicitly asks.

The full feature set, architecture and phased build order live in the design doc:
https://claude.ai/code/artifact/ac290a70-ec83-42a3-abf5-c4d47ce373d2 — the
"Phase 1 build order" section there is the work queue. UI mockups:
https://claude.ai/artifact/Y46mTotG3zBoRZzodt56Mt

## Non-negotiables (don't regress these)

- **Local-first, zero telemetry.** No network calls for core function, no
  accounts. Anything online (map tiles, model downloads) is opt-in and labeled.
- **Never touch the originals.** Index-only by default. Edits are an instruction
  stack in XMP; the source file, RAW or not, is never overwritten.
- **XMP sidecars carry ratings/labels portably; the catalog carries the rest.**
  Picks, events, albums and faces live only in the catalog, so it is snapshotted
  daily and before every job that moves or removes files (catalog/backup.py).
- **Lunelis's own files never go in the photo library or the program folder.**
  Catalog, thumbnails, backups and the central sidecar store live in the data
  folder (paths.py). Photo folders only ever gain files the user asked for.
- **Imported files are never renamed.** A storage template picks FOLDERS only;
  a different file with a taken name goes to a sibling folder ("... (2)").
- **Nothing is hard-deleted by a job.** Removals go to a quarantine folder on the
  same drive; a move deletes its source only after the copy is hash-verified.
- **Schema changes are migrations only.** Append to `MIGRATIONS` in
  `catalog/schema.py`; never edit a shipped migration or the live schema.
- **No RAW decode in the scroll path.** The grid and filmstrip only ever load
  cached JPEG proxies (embedded preview first, full decode on demand).
- **Recognition sits behind its own interface** so it can move to an external
  service (e.g. Immich's ML microservice) without a data-model change.
- **Phase discipline.** Follow docs/wiki/Feature-Plan.md's version order. Scene
  tagging (embeddings + review queue) is scheduled for 0.20 (decided
  2026-10-03); face recognition stays deferred. Don't pull work forward.

## Commands

```bash
py -3.13 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt -e .   # editable install - required, see Gotchas
python -m pytest                        # all tests (use a throwaway data folder - conftest.py)
python -m lunelis                       # run the app
python -m lunelis.catalog.backup --now  # snapshot the catalog; --restore <zip> to roll back
```

Data folder: `%LOCALAPPDATA%\Lunelis` (catalog.db, cache\thumbnails, backups,
sidecars), overridable with `LUNELIS_DATA_DIR` or `%APPDATA%\Lunelis\location.json`.

Python 3.13 is the floor: the pinned PySide6 6.11 / rawpy 0.27 set is what has
wheels here, and a current rawpy matters because CR3 support tracks LibRaw.

## Architecture

Source to screen: file on a root (local drive or NAS) → importer walks + hashes +
reads EXIF → catalog row → RAW pipeline writes a JPEG proxy into
`<data>/cache/thumbnails/` → UI grid reads only proxies. Edits write XMP and re-render
the proxy. Recognition (Phase 2) is a background worker feeding a review queue.

### Directory map

```
src/lunelis/
  main.py             app entry: QApplication + ui.main_window.MainWindow
  __main__.py         `python -m lunelis`
  paths.py            the data folder (catalog, cache, backups, sidecar store);
                      adopt_legacy_data() moves an old repo-root catalog in once
  settings.py         library settings (JSON in the catalog), defaults in DEFAULTS
  catalog/schema.py   SQLite schema + migration runner (SQL or Python steps,
                      each all-or-nothing); open_catalog() is the only way code
                      should get a connection (migrates, foreign keys, WAL, Row)
  catalog/backup.py   snapshot (VACUUM INTO + zip), snapshot_if_due, restore
  catalog/exifblob.py raw EXIF JSON <-> zlib blob (exif.raw_exif)
  importers/formats.py  the ONE list of cataloged / RAW / video extensions,
                      plus sniff(): what a file really is, from its bytes
  importers/scan.py   add_root() + scan_root(): walk a root, sync `files`
                      (Step 2). `python -m lunelis.importers.scan DIR` scans
                      then nothing else - run metadata separately.
  importers/video.py  PyAV: probe() container metadata (creation time UTC ->
                      local, GPS, duration, size), poster_frame() upright frame
  importers/takeout.py Google Takeout JSON -> takeout_meta + dates/GPS where
                      the file has none; takeout_roots() finds exports
  importers/relink.py relink(): a missing entry takes over its moved file's new
                      location (size+mtime -> EXIF -> hash tiers, unique 1:1 only)
  damage/check.py     check(): rebuild `damaged` (zero-byte/-filled, unrecognised,
                      truncated, corrupt, changed on disk); survivors(): intact copies
  importers/metadata.py extract_pending(): EXIF for every file without a
                      current `exif` row (Step 3); sets files.format/is_raw
                      from content. `python -m lunelis.importers.metadata`.
  raw/previews.py     TIFF-IFD walker: every embedded JPEG in a RAW, and
                      best_preview() = smallest one that's >= 512px
  raw/thumbnails.py   render() one file -> 512px upright sRGB image;
                      generate_pending() fills cache/thumbnails (Step 4).
                      `python -m lunelis.raw.thumbnails`.
  catalog/ratings.py  set_ratings(): stars/flag/label in the catalog, marks
                      xmp_pending. The ONLY way UI code changes a rating.
  xmp/sidecar.py      choose_sidecar / read_sidecar / write_sidecar - surgical
                      text edits of xmp:Rating + xmp:Label (+ darktable labels)
  jobs/engine.py      the jobs engine: create_job / run_job over job_folders,
                      pause / resume / cancel, schedule (now / idle / window),
                      waits (not fails) when a share is offline. Kinds in KINDS.
  dupes/hashing.py    sample_hash (size + 3x64 KB) / full_hash, Throttle, SourceOffline
  dupes/detect.py     process_folder (sampled pass, per folder, catalog-wide),
                      verify_group (full sha256 -> 'exact' groups), keeper_rank
  dupes/quarantine.py quarantine() / restore(): rename into `_Lunelis Quarantine`
                      on the same drive; verified groups only, never the last copy
  xmp/sync.py         import_sidecars (sidecar -> catalog), export_pending
                      (catalog -> sidecar), both parallel like metadata
  ui/main_window.py   sidebar/toolbar/filter bar (Library mockup) + LibraryWorker:
                      scan -> relink -> sidecar import -> metadata -> Takeout
                      JSON -> relink ->
                      thumbnails -> damage check on one
                      QThread with its own catalog connection; XmpWriter writes
                      sidecars 1.2 s after the last rating keypress, and on close.
                      Library menu = dev trigger until Step 9.
  ui/jobs.py          JobRunner (QThread, own connection), JobsDialog, ScopeDialog
  ui/dupes_view.py    Duplicates page: groups table (model-based), copies,
                      keeper choice, verify job, quarantine worker
  importing/templates.py storage templates: tokens + [optional] parts -> folder
  importing/ingest.py card import: discover -> stage (local, spill to network,
                      verified) -> place (template, verified vs card hash) ->
                      cleanup; per-file state in import_items, resumable
  ui/import_view.py   Import page: cards, destination preview, progress
  ui/tray.py          CardWatcher (polls removable drives with DCIM/PRIVATE),
                      Start-with-Windows (HKCU Run key, only when the user ticks it)
  ui/settings_view.py Settings page: saves on change (settings.validate first);
                      folder moves on a thread; data-dir move / restore are
                      recorded and done by main.py at the next start
  events/model.py     events: create/add (moves)/remove/rename/delete, date range =
                      capture range of members; link_imports() after scans
  events/suggest.py   suggestions from folder names + capture-time gaps
  ui/events_view.py   Events page: your events + suggestions (thread)
  migrate/plan.py     migration dry run: sources + target + template -> migration_items
                      (move / skip_duplicate / skip_damaged, dest_rel, notes)
  migrate/execute.py  job kind "migrate": copy -> verify -> sidecar -> repoint the
                      SAME file row -> quarantine original (or keep for review);
                      finish() handles skipped copies; release() for review mode
  ui/migrate_view.py  Migrate page: form, preview, start, release
  backups/core.py     backup sets, job kinds "backup"/"backup_verify" (+ finish:
                      catalog snapshot, sidecars, manifest), restore_files/catalog
  ui/backups_view.py  Backups page; MainWindow._check_backup_drives auto-starts sets
  darktable/lunelis.lua  the darktable Lua script (Lua 5.4, darktable API)
  darktable/bridge.py export/import of the exchange files, install/uninstall,
                      tick() (MainWindow every 20 s)
  ui/damaged_view.py  Damaged files page: problem, file, best surviving copy
  ui/grid.py          PhotoGrid: QAbstractScrollArea that paints only visible
                      rows; layout is arithmetic (row = i // cols)
  ui/library.py       LibraryIndex: every visible file, sorted, as raw row tuples
  ui/thumbcache.py    async thumbnail loads (Pillow on a QThreadPool) + LRU
  ui/theme.py         colour tokens + stylesheet; nothing else hardcodes colour
assets/icons, logo/   brand assets; app_icon.png is the window/taskbar icon
tests/                pytest; pythonpath=src in pyproject.toml; conftest.py points
                      LUNELIS_DATA_DIR at a temp folder so tests never touch real data
```

### Catalog shape (schema v1)

`files` is keyed by `(root_id, rel_path)` — paths are stored relative to their
root so a remounted NAS doesn't orphan rows. `content_hash` (exact) and
`perceptual_hash` (Phase 2) are the duplicate signals. `file_tags.confidence`
is NULL for manual tags and 0..1 for model suggestions, which is how auto-tags
stay distinguishable from the user's own. `faces.person_id` stays NULL until
confirmed in Recognition Review. `files.missing_since` (migration 2) flags a
file not found on the last complete scan.

### Scanner rules

- Reads directory metadata only (path, size, mtime). No hashing, no decoding,
  no EXIF - those are Steps 3/4/7 and must not slow the walk.
- Never deletes a `files` row. Gone → `missing_since`; back → cleared.
- An unreachable root raises `RootUnavailable`; it is never scanned as empty.
  A cancelled scan, or an unreadable subfolder, marks nothing missing under it.
- Size or mtime changed → `content_hash`, `perceptual_hash`, `thumbnail_path`
  are nulled, because they describe bytes that no longer exist.
- Roots can't nest or contain each other (`RootOverlap`) - each file has
  exactly one `(root_id, rel_path)`. `rel_path` always uses forward slashes.
- Measured 2026-09-26 on D:\ (41k files, 16.7k photos): first scan 0.17s,
  unchanged rescan 0.10s.

### Metadata rules

- Reader is exifread (pure Python, reads via a Python file handle). Formats
  with no reader yet (CR3, video) get an `exif` row with `read_error` set.
- An `exif` row is current when `extracted_mtime` equals `files.mtime`. Rows
  with `read_error` are current too - failures aren't retried until the file
  changes. `captured_at` is camera wall-clock; the offset is `captured_offset`.
- `files.is_raw` from the scan is an extension guess; the metadata pass
  replaces it with `is_raw_content()` (RAW extension AND RAW container bytes).
  Anything that decodes a file dispatches on `files.format`, never `ext`.
- One bad field (a garbage date) drops that field, never the whole file.
- Files are read `WORKERS` (8) at a time on a thread pool; catalog writes stay
  on the calling thread. Over SMB a read is mostly round-trip wait: measured on
  the NAS, 21 files/s serial, 90 with 8 threads, 129 with 16. 8 leaves the NAS
  responsive while the user browses.
- Measured 2026-09-26: 242 A7R V ARWs (E:\3-14-2026) 4.5s cold; D:\ + E: shoot,
  16.9k files, 18.8s. Catalog 41 MB for 16.9k files (raw_json dominates).

### Thumbnail rules

- 512px long edge, JPEG q80, `<data>/cache/thumbnails/<id//1000>/<id>.jpg`, path in
  `files.thumbnail_path` (relative to the cache). Written via .tmp + replace.
- Cheapest adequate source wins, by sniffed format: RAW → smallest embedded
  JPEG >= 512px (IFD0/SubIFDs; RAF header) → rawpy thumb → rawpy half-size
  decode. Camera JPEG → MPF "Large Thumbnail" → full JPEG with a DCT draft
  decode. HEIC via pillow-heif. Video: not yet (`thumb_error`).
- RAW previews and MPF previews carry no usable orientation: apply the
  primary image's EXIF orientation (catalog value for RAW). Standard images
  use `ImageOps.exif_transpose` on themselves.
- Failures set `thumb_error` and aren't retried until the scanner sees the
  file change. Deleting the cache folder makes the next pass rebuild it
  (`forget_purged_cache`); deleting individual files does not.
- Measured 2026-09-27: 242 A7R V ARWs in 0.5s with 8 workers (508/s),
  ~31 KB per thumbnail → ~5 GB for the whole library. NAS camera JPEGs:
  42 files/s via MPF vs 9/s decoding the main image.

### Sidecar rules

- Where ratings are written is the `sidecar_mode` setting: `central` (default;
  `<data>/sidecars/<root id>-<root name>/<rel dir>/<file>.EXT.xmp`, photo folders
  untouched), `beside` (next to the photo), or `catalog`. With
  `update_existing_sidecars` (default on) a sidecar that ALREADY exists next to
  the photo (darktable's) is kept in step too - but none is ever created there
  unless the mode is `beside`. Import still reads sidecars next to photos.

- Which sidecar: `name.EXT.xmp` (darktable) wins; else `name.xmp` (Adobe),
  which belongs to the RAW when a RAW with that stem is in the folder. New
  sidecars are `name.EXT.xmp` - the user's editor is darktable.
- Reject = `xmp:Rating="-1"`. Pick has no XMP field: catalog only, and a
  pick-only change never creates a sidecar.
- Writes only touch fields whose value changed, checked well-formed before an
  atomic replace; an unparseable sidecar is left alone (SidecarError).
- Conflicts: an unexported Lunelis change (`ratings.xmp_pending = 1`) wins
  over the sidecar; otherwise the scan's sidecar mtime vs
  `files.sidecar_synced_mtime` decides whether to re-import. Export clears
  pending only if the row still holds what was written.
- Writing to the user's photo folders (incl. the NAS) is by design here - but
  never while testing against the real catalog: rate only in a temp library.
- Measured 2026-09-27: 7,297 real sidecars (6,627 darktable, 670 Adobe-style)
  imported in 9.3 s; every one round-trips byte-identical on a no-op write.

### Jobs & duplicates rules

- A job is a list of folders; a folder is marked done only after its work is
  committed, and every hash is committed as it's made. Start-up re-queues
  jobs left 'running' (power cut) - they resume at the first pending folder.
- `should_stop` is polled from hashing threads too: it must be a plain flag,
  never a catalog query (sqlite connections are thread-bound).
- Candidates for a byte-identical copy share size AND capture time (or have
  no capture time). Hashing a folder hashes its candidates anywhere in the
  catalog, so a folder's duplicates are complete when it finishes.
- Groups: 'sampled' = likely, 'exact' = verified by full sha256. Only exact
  groups can be quarantined, never down to zero live copies, and a catalog
  snapshot is taken before the first move of a batch.
- Quarantined files are hidden from the grid (`quarantined_at`) and the
  scanner skips `_Lunelis Quarantine` folders.
- Measured 2026-09-27: old pool / 2019 Album, 15,841 files, sampled pass
  424 s / 17.8 GB before the capture-time key (it hashed 91k same-size
  files); the key cuts per-folder candidates ~12x. That folder alone:
  13,510 likely groups, 689.7 GB in extra copies.

### Import rules

- Two verifications per file: the staged copy is re-read against the hash
  taken while reading the card; the library copy is re-read against the same
  hash. The staged copy is deleted only after the library copy verifies.
- "Card can be removed" = nothing left pending on the card; "safe to format"
  = every file placed or already in the library.
- Staging: `<data>/staging/import-<id>/` while the local disk keeps
  `import_local_reserve_gb` free, else the network staging folder (must be
  OUTSIDE every source so partial imports are never cataloged). Temp copies
  are `.lunelis-importing-*` (dot-prefixed, so scans skip them).
- Already in the library = same size, capture time and sampled fingerprint as
  a live file (hashing library candidates on demand).
- The window's tray/card code only runs with `MainWindow(tray=True)` (the real
  app); tests and scripts construct it without, so `close()` really closes.
- Verified 2026-09-27: 6 real A7R V ARWs (0.8 GB) from a DCIM-layout folder
  into a temp library in 3.7-6.2 s, byte-identical, names and mtimes kept.

### Settings rules

- Every setting has a default in `settings.DEFAULTS`; `settings.validate()`
  guards `set()` (template, ranges, enums), so a bad value never reaches the
  catalog - the Settings page shows the error instead.
- "Live file" = `missing_since IS NULL AND excluded = 0` (plus quarantined_at /
  roots.enabled where relevant). Any new query over files must include
  `excluded = 0`, or skipped folders leak back into the grid, jobs and dupes.
- Skipped folders (`excluded_folders`, migration 14): scans don't walk them
  and never flag their files missing; `apply_exclusions()` sets
  `files.excluded` with no disk access (0.1 s for 20k files on the real catalog).
- The data folder moves at the NEXT start: `location.json` gets `move_to`,
  `main.py` runs `paths.finish_pending_move()` before anything opens the
  catalog, then `paths.reload()`. `data_dir` switches only after every file
  arrived, so an interrupted cross-drive move just resumes. Refused while an
  import is unfinished (import_items hold absolute staged paths).
- Restarts from Settings pass `--after <pid>`; the new process waits for the
  old one to exit so the catalog is closed before it's moved or restored.

### Event rules

- Membership is explicit (`event_files`, file_id PRIMARY KEY = one event per
  photo); `events.start_at/end_at` are recomputed from members on every
  change, and an event left empty is deleted. Removing an event never touches
  photos. `event_files` counts as user data for relink (a newcomer in an event
  is never merged away).
- Folder suggestions: OUTERMOST named folder under a root wins (trip day
  folders and Clips/JPEG subfolders belong to the trip). Dated folders keep
  only the capture run around their date (3-day gap); words-only folders must
  span <= 21 days. Same `name_key` within 2 days merges (day folders, the two
  NAS pools). Tuned on the real library 2026-09-27: 122 raw -> 15 real events.
- `_clean_name` judges the name WITHOUT years ("2024 Album" is an album,
  "Epcot 2017" an outing) and strips dates, parentheticals and "day N".
- Named imports: `imports.event_start` = earliest capture on the card, fixed
  once (a resumed import files identically); every file renders with
  `event_start`, so a trip and its undated clips land in one folder.
- Suggestion keys are what the suggestion IS (`folder:<name_key>:<start day>`,
  `gap:<start minute>`), so dismissals survive recomputation.

### Migration rules

- A plan is a dry run: plan() reads only the catalog and lists the target's
  folders. Nothing on disk changes until start(), which snapshots the
  catalog and adds the target as a root (it must not overlap any root).
- A moved file keeps its catalog row (id): root_id/rel_path are repointed,
  file_moves gets method 'migrate', the central sidecar moves with it.
- Item states: planned -> copied (dest verified + row repointed) -> done
  (original in `_Lunelis Quarantine/migration-<id>/`) | kept (review mode) ->
  released. A crash leaves at worst 'copied', and a resume only finishes the
  original. Never delete; never overwrite; never rename.
- Probable copies (same name + size + sample hash, else capture time, else
  mtime) are planned to the SAME dest path. At copy time an identical dest
  that another row already holds means skip_duplicate + merge_user_data.
  Without this the real library plan put 83,770 files in "(2)" folders
  (old pool mirrors new pool); with it, 75,908 probable copies (3.19 TB).
- Review mode: the scanner skips kept originals (kept_sources()), or the
  next rescan would catalog every original again as a new file.
- A RAW+JPEG pair follows the event of either half (else a half-tagged pair
  splits across folders).
- In a migration Context.import_name = the event name, so the user's
  `{YYYY}\{M}-{D}-{YYYY}[ {import_name}]` names event folders.

### Backup rules

- `backup_files` is keyed by (set, file_id): a file moved/migrated in the
  library is RENAMED inside the backup (same size+mtime), never re-copied.
- Changed file: old copy -> `_Lunelis/previous-versions/<time>/`; nothing is
  deleted from a backup, ever (library deletions stay in it).
- Every copy: hashed while read, re-read and compared, sha256 stored;
  `backup_verify` re-reads against it (bit rot). The first backup fills
  files.content_hash when empty (free integrity baseline).
- Removable destinations are stored with the volume serial; resolve_dest()
  finds the drive under any letter. A missing drive -> SourceOffline -> the
  job waits; a full drive -> job paused, set status says why.
- Kinds needing their job's options are listed in engine.OPTION_KINDS
  (they get `options=`); kinds with an "afterwards" in engine.FINISHERS.
- Restore never overwrites: a damaged library file is quarantined first; a
  healthy one is skipped. The catalog restore copies the zip off the drive
  before requesting the restart (it may be unplugged by then).

### darktable plugin rules

- Two files in the exchange folder: `lunelis-ratings.tsv` (Lunelis, full
  state: path, stars / -1 reject, label, updated UTC) and
  `darktable-ratings.tsv` (plugin: rows changed since its snapshot; Lunelis
  deletes it after applying). Paths matched case-insensitively.
- The plugin applies only rows newer than its `last_applied` preference, and
  diffs against `darktable-snapshot.tsv` to find local edits. On the first run
  it only takes a snapshot (existing darktable ratings already came in via XMP).
- Newest wins: Lunelis skips a darktable row older than ratings.updated_at.
- Labels: darktable can hold several; one label -> replaced, several -> only
  added. Picks are Lunelis-only (the pick flag survives a darktable import).
- Tested in pytest by running the REAL lunelis.lua under lupa (Lua 5.4, the
  same Lua darktable embeds) with a stand-in `darktable` module. Not yet run in
  a real darktable: none is installed on the dev PC (2026-09-27; the config
  there is darktable 5.0 with 97 images, last used April 2025).
- The .spec bundles the .lua (datas) and pyproject has package-data for it.

### Theme rules

- Every colour is a token on `ui.theme.Theme`; `theme.current()` is the one in
  use. Widgets that paint themselves (grid) read `current()` at paint time.
  The stylesheet is set APP-wide (`app.setStyleSheet`) so dialogs follow;
  `MainWindow.apply_theme()` re-applies it and the palette live.
- Never hardcode a colour in a widget (`setStyleSheet("background: #141414")`);
  use an objectName with a rule in `stylesheet()` (Primary, PageTitle...).
- The checkbox tick is an SVG written per theme into `<data>/cache/ui/`
  (Qt stylesheets can't take data URLs, and one colour can't suit light
  and dark checked boxes).

### Viewing rules (v0.4.0)

- The detail view's big image is `raw.thumbnails.render(path, orientation,
  edge=2560)` (for a RAW, the biggest embedded preview) on a 2-thread pool,
  8 cached. The grid's 512 px thumbnail stands in until it arrives;
  neighbours are pre-decoded.
- The photo on screen is the rating target: `MainWindow._detail_moved` sets
  `grid.selected = {fid}`, so the global 0-9/P/X/U shortcuts just work.
- ThumbCache keeps the previous size's pixmaps (`_stale`) and draws them
  scaled until the new size arrives. The grid delays the new decode size
  until the slider settles (RESIZE_SETTLE_MS). Clearing on every size step
  made the slider flash grey (the user's "should slide better" note).
- The scrubber lives in the grid's right viewport margin (date sorts only;
  the scroll bar is hidden then). Its month bubble is a QLabel on the grid,
  because the 64 px strip would clip it.
- `photoinfo.breakable()` puts zero-width spaces after separators so a NAS
  path wraps, but never between a share's leading `\\`.

### Editing rules (edit/, ui/develop.py, ui/export_dialog.py)

- An edit = `Stack(filter, amount, adjust, geometry)` (edit/stack.py). All
  adjustments default to 0, so filters scale by amount and add to manual
  sliders. Text form `v=1;f=Name@40;exposure=0.3;crop=...` lives in
  `edits.stack` and in the sidecar as `lunelis:EditStack` (XML-escaped).
  Never write `crs:` - real Camera Raw sidecars are in the library.
- `store.save` bumps `rev` and sets `ratings.xmp_pending` (inserting a
  ratings row if needed) so the sidecar export carries the stack.
- Pipeline order: geometry -> one per-channel tone LUT (WB, exposure,
  whites/blacks, shoulder, contrast, fade) -> shadows/highlights on a
  blurred-luma mask -> colour -> vignette/denoise/sharpen. Radii scale with
  max(h, w)/2000 so preview == export. Keep pointwise work in the LUT: a
  simple edit went 250 ms -> 47 ms at 1.7 MP that way.
- Exports use `apply_tiled` (strips + a low-res mask) - never `apply` at
  full size (60 MP in one piece = several GB of temporaries).
- Caches: proxy `cache/edits/<rel>.jpg` (2560 px) + the normal thumbnail
  overwritten with the edited look. ONLY EditMode (single-thread
  `out_pool`, in order) and BatchOutputs write them; the photo view's
  preview loader renders a missing proxy in memory only.
- The thumbnail pass renders edited photos via `edit.render.edited_thumbnail`
  (PENDING_SQL joins `edits`).
- Paste never copies geometry. Deleting a user filter first flattens it into
  the photos that use it.

### Quarantine page rules (dupes/manage.py)

- Entries: files with quarantined_at (reason from migration_items / group
  method) + migration_items action='move' state done/released (the row
  was repointed; only the item knows the original). The kept copy is
  resolved per entry and checked ON DISK (same size for byte copies).
- `empty()` is the only hard removal in Lunelis: kept copy must be OK;
  local -> Recycle Bin (SHFileOperationW FOF_ALLOWUNDO), network ->
  os.remove after the dialog's extra tick; snapshot first; row in
  `purged`; the file's catalog row is deleted (user data was merged into
  the keeper when it was set aside). Tests monkeypatch `_recycle`.

### Create rules (create/, ui/create_page.py)

- Outputs are ALWAYS new files: engine.save / animation.write use
  export.free_path ("name (2).ext") - never overwrite, never write beside
  the originals. One folder: Settings `create_output_dir`, else
  engine.pictures_folder() / "Lunelis creations" (FOLDERID_Pictures, may be
  OneDrive). Batch makes a new "Batch <stamp>" subfolder per run.
- Pixels come from edit/export.rendered (shared with Export) - previews use
  the cached thumbnails (create_page.thumb_image), saved files never do.
- Presets: create/presets.json (packaged: pyproject package-data AND
  Lunelis.spec datas) + <data>/create_presets.json; a broken user file
  raises PresetError (shown, built-ins used meanwhile).
- MP4 = PyAV libx264 (bundled in the av wheel), yuv420p, so frames are
  cropped to even sizes; MP4 has no loop flag - `loops` repeats the frames.
  GIF is capped at GIF_MAX_EDGE (1080) and quantized per frame.
- Collage spacing/border/corners are % of the canvas's short side so the
  preview matches the file; cells cover-fit (crop, never stretch); swap
  moves photos (with zoom/pan) and keeps the cells. Free cells (Cell.rect)
  are supported by the engine; the page has no UI to draw them yet (0.22).
- Tools run on MakeWorker (own connection: commit before making) with
  Cancel wired via lambda (the Cancel gotcha).

### Scene tag rules (recognize/)

- Everything that understands pixels goes through the Recognizer interface
  (recognize/__init__.py): embed_images / embed_texts, model_id stored with
  each embedding. clip.py = CLIP ViT-B/32 quantized ONNX (Xenova), files
  pinned by SHA-256 in clip.FILES; the BPE tokenizer is written out there
  (no `regex` package) and matches CLIP's ids (a photo of a cat -> 49406 320
  1125 539 320 2368 49407).
- Thumbnails only (scenes.thumbnail_image) - never a RAW decode, never the NAS.
- Suggestions = file_tags rows with confidence; EVERY normal tag query has
  `confidence IS NULL` (tags_of, counts_in, all_tags, filter_sql, XMP export).
  A new tag query must too. tags.add() on a suggested tag accepts it.
- Tests use a fake model (tests/test_scenes.py FakeModel); the real model is
  never downloaded by a test.

### Search rules (search.py)

- search_fts (FTS5, rowid = file id; columns name, folder, camera, lens,
  tags, events, albums) + search_dirty. Triggers on files / exif /
  file_tags / tags / album_files / albums / event_files / events mark
  dirty; `search.refresh()` re-indexes dirty rows (LibraryWorker after
  stacks; the search box before each query). Adding a new searchable
  source = a column or text in `refresh()` + a trigger in SCHEMA.
- User words -> FTS5 prefix phrases (`24-105` -> `"24 105"*`); a lone
  letter / roman numeral joins the word before ("a7r v"), else "v"* matched
  every v-word (a7r v: 39,995 exact, was 29k mixed). Dates, stars, kinds
  are SQL conditions (aliases f / e / rt), not FTS.

### Tag rules (tags/model.py)

- A tag's `name` is its full path ("Places|Ohio|Cleveland"); parents are
  real rows, made on demand. Unique ignoring case (migration 23 index).
  Filter by a tag = that name OR `name LIKE 'name|%'` (escape _ and %).
- Any tag change sets `ratings.xmp_pending`. Export writes dc:subject (all
  levels, flat) + lr:hierarchicalSubject (paths); `set_keywords` keeps
  darktable's `darktable|...` entries and their flat words.
- Import from sidecars only ADDS tags (never removes) and ignores
  darktable's automatic tags. Only manual tags (confidence NULL) export.

### Editing Phase B rules (curves, masks, AI, lens, merge)

- Curves live in the tone LUT (`_curves` key in `effective()`), Fritsch-
  Carlson monotone spline. Filters never carry curves.
- Masks (edit/masks.py) are in the rotated/flipped/straightened UNCROPPED
  frame; `masks.Frame` maps strips of the cropped output onto it. Blend =
  x + alpha*(apply_adjustments(x, local) - x), after the global edit. Brush
  strokes rasterize once at 1024 px (cached per apply_tiled call).
- AI maps are made on the UPRIGHT SOURCE (pre-geometry), cached as PNG in
  cache/masks/, and `pipeline.ai_in_frame` turns them with the geometry.
  Every renderer gets them via `ai.maps_for(file_id, stack, source)`; a
  missing map = no effect, never a wrong one. Models: pinned URL + SHA-256
  + size in ai.MODELS; downloaded only after the user confirms.
- Lens corrections run first, on the upright source, in linear light
  (edit/lens.py). The edit session caches the corrected display image per
  lens setting (0.3-0.9 s per correction). Profile off by default.
- Merges only read sources; `merge.save` refuses an existing path; the
  result is cataloged with `scan.catalog_file` (no root rescan) + the first
  source's date/camera, recorded in `merges`.

### Near-duplicate rules (dupes/similar.py)

- Fingerprint = 64-bit dHash of the 512 px thumbnail (`files.perceptual_hash`),
  made in LibraryWorker after thumbnails; `similar.refresh` regroups only when
  new fingerprints were written. Groups are `duplicate_groups` method
  'similar', rebuilt whole each time; dupes_view's exact list must filter
  `method IN ('exact','sampled')`.
- A pair matches only if: <= 4 bits apart, not RAW, not the same size,
  same moment (sub-second when both have it), related names (equal or one
  inside the other), aspect within 2 %. Fingerprints with popcount outside
  8..56 are skipped (near-blank).
- Groups are complete-linkage (every member matches every other). Union-find
  chained night skies into a 3,126-photo group.
- Suggested extras (`similar.suggest`): fewer pixels than the keeper, a
  Takeout copy, or the keeper's exact name - never an edit, never a
  same-pixels copy under another name. `quarantine_similar` merges user data
  into the best *remaining* copy (keeper_rank), never the SQL-order first.

### Burst stack rules (stacks.py)

- A burst = one camera + one folder, each frame within burst_gap_seconds of
  the last (sub-second times both sides; otherwise same second - an interval
  timer at 1 s is not a burst), >= burst_min_frames distinct stems (RAW+JPEG
  = one shot). Videos never stack. Rebuilt after every metadata pass
  (~0.6 s / 159k).
- A rebuild keeps unchanged stacks (same member set) and a user-chosen cover
  (`cover_chosen`) while it's still a member; `stack_dismissed` files never
  re-stack.
- Collapsing happens in Python in `LibraryIndex._collapse` over `all_rows`,
  not in SQL: a filter that drops the cover shows the stack's first
  remaining frame. `refresh_ratings` patches both `rows` and `all_rows`.
- Stacks only affect the grid. Albums, events, duplicates, backups and
  migration see every frame.

### Album rules

- Your albums = `albums` (is_smart = 0) + `album_files`; a photo can be in
  many (events: at most one). Removing an album or a membership never
  touches files. Migration's merge_user_data carries album membership too.
- Automatic albums are SQL conditions in `albums.model.AUTO` (+
  `camera:<model>`), applied as `Filter.auto` inside the library query
  (aliases f / e / rt). An automatic album that equals the whole library is
  hidden; Takeout files are never picked as covers (re-encoded copies can
  have lost their rotation).
- Counting automatic albums takes ~0.8 s on 159k: the Albums page runs it on
  a thread (bound-method callback) and draws your albums/events at once.
- Import page drives: `ingest.all_drives()`; removable drives import whole,
  fixed/network drives open a folder picker (never import all of C:\).

### Video & Takeout rules

- Videos go through PyAV opened on a Python file handle (non-ASCII paths), never
  an ffmpeg.exe. Poster frame ~10% in (max 1 s), rotated by the frame's
  display-matrix angle (verified +90 Sony and -90 phone clips upright).
- MP4/MOV `creation_time` is UTC -> PC-local wall clock + offset, matching photo
  EXIF (checked: Sony clips fall inside the same shoot's EXIF times). Apple's
  `com.apple.quicktime.creationdate` (local + offset) wins when present.
- Takeout JSON never overrides a file's own date/GPS (`date_source` records
  where captured_at came from: exif / video / takeout). Match by the JSON's
  `title` (Google truncates JSON filenames), `(n)` numbering moves into the
  media name, a name in several folders is resolved by year/month folder,
  `-edited` copies inherit, ambiguity = no date (a wrong date is worse).
- A root is a Takeout export by its own folder name or a `Google Photos`
  folder inside - never by "takeout" anywhere in the full path.

### Moved-file & damage rules

- Re-linking merges two rows for ONE file: the orphan (with ratings, EXIF,
  thumbnail, id) takes the newcomer's location; the newcomer's blank row is
  deleted. Only unique 1:1 matches, never a newcomer with user data. Every
  merge is logged in `file_moves`; the central sidecar is moved with it.
- The damage check reads only files whose header was unrecognisable (a
  zero-filled file can't pass sniff()); everything else comes from errors
  already recorded. `changed_on_disk` comes only from the integrity job.
- Lunelis never repairs or replaces a damaged file itself; the page points
  at the best surviving copy (same file elsewhere > RAW original > Takeout).
- Real library 2026-09-27: 127 damaged (80 zero-filled, 44 corrupt, 2 empty,
  1 truncated), 25 with no intact copy anywhere; check read 80 files in 1.8 s.

### Grid rules

- Paint never touches the disk or the catalog: `LibraryIndex.rows` is in
  memory, pixmaps come from `ThumbCache` (a miss queues a load and paints a
  placeholder). Prefetch one screen above and below.
- A tile's thumbnail path is `thumbnail_path or cache_rel_path(id)` - the
  cache path is a pure function of the id, so thumbnails a background pass
  writes after the index loaded still appear.
- Selection is a set of file ids, not positions, so it survives re-sorts.
- Colours only from `ui/theme.py` tokens (the doc's theme presets need it).
- Measured 2026-09-27 on 159k files: index load 252 ms, window up ~350 ms,
  paint 2-4 ms cached, 3-8 ms right after a scrollbar jump.

### The real library (2026-09-26)

~159k files, ~4.6 TB: `\\nas\Photo_Pool\Photos`
(71.6k, 3.1 TB, 18k ARW), `...\Main_Pool\Photo_and_Video` (old
pool, 68.9k, likely overlaps the main pool), `D:\Google Takeout 9-12-2026`
(16.6k; its JSON sidecars live in a separate `_json/<album>/` tree), plus
Timelapes and Video folders on the photo pool. Twice the design doc's 50-80k
estimate - size the proxy cache and grid for that. NAS scans: 71.6k files in
24.8s, 68.9k in 27.5s.

## Gotchas / constraints

- **A Python closure stored on a widget can crash the NEXT test (0.38).**
  `card.refresh = refresh` (a closure over the card's own child widgets)
  makes a reference cycle; the garbage collector later destroys those Qt
  widgets at a random moment and the process dies silently (exit 127) in a
  later, unrelated test. Same for an `eventFilter` override on a page that
  is being destroyed. Keep closures in Qt connections only, and put event
  filters on one long-lived QObject (settings_view `_wheel_guard`). In tests,
  build pages with the module's fixtures instead of closing the catalog
  under a live page.
- **No GPU for faces / scene tags (0.38, measured).** onnxruntime-directml
  1.24 crashes (access violation, uncatchable) creating a session for the
  quantized CLIP vision model unless graph optimisation is ORT_ENABLE_BASIC,
  and even then was 2.5x slower than CPU with cosine 0.996 vs CPU output;
  OpenCV OpenCL faces were slower too. The passes are bound by photo
  decoding. Don't re-add without full-precision models and a benchmark.
- **lensfunpy 1.18 reads lensfun database version 1 only.** A
  `<lensdatabase version="2">` file raises XMLFormatError; lens.check_profile
  refuses it with a reason. The v1 archive is lensfun.github.io/db/version_1.tar.bz2.
- **MP4 with no moov = cut-off recording.** damage/check.missing_moov walks
  top-level box headers only (a few seeks per file); a zeroed first box means
  zero-filled, not truncated - untrunc can't help.

- **Never share a LibraryIndex between views (0.37).** MainWindow.reload /
  reload_later `apply()` new rows INTO `self.index`; the photo view used to
  hold that same object, so a background reload (scan timer, jobs) moved
  other photos under its `pos` - the filmstrip's highlight showed a different
  photo than the picture ("VERY BAD" from the user). DetailView.open() takes
  `index.snapshot()` and `follow()`s the library after each reload by FILE
  ID. Anything else that keeps a position into the library index must do
  the same.
- **`clicked=self.some_signal.emit` is a bug** when the signal takes no
  arguments: clicked(bool) passes `checked`, and the emit raises "back() only
  accepts 0 argument(s)" at click time (tests that never click won't see it).
  Always `clicked=lambda: self.sig.emit()`.
- **Read thumbnails on worker threads with Pillow** (thumbcache.load_image),
  not QImage(path): On this day's strip came back empty in the installed app
  while working from source.
- **Release notes are Markdown read as HTML too:** "People|<name>" swallowed
  the rest of the notes in the updater and on GitHub. updater.safe_markdown
  and packaging/release_notes.py escape < > outside `code`.
- **Inno Setup has no inline `;` comments in [Setup] values** and no
  `{userpics}` constant (0.34.0's setup died before its first page - CI's
  install test caught it). Pictures: the registry's Shell Folders value.

- **The test process ends with TerminateProcess** (tests/conftest.py,
  pytest_unconfigure): os._exit still unloads every DLL on Windows, and Qt's
  teardown there hit an access violation after clean runs - the source of the
  "all passed, exit code 1" CI failures. faulthandler is disabled first so the
  dying Qt threads print nothing. Unexpected dialogs fail the test
  (_no_modal_dialogs) and a native hang dumps every stack (_hang_watchdog).
- **Companions vs sidecars** (importing/ingest): a companion is cataloged
  media filed with its photo (Live Photo .MOV); a sidecar isn't media. A
  sidecar can belong to the photo AND its companion (IMG_0001.AAE): it is
  added once. A clashing companion is 'failed' (stays on the card), never
  "kept" like a stale sidecar - Clear the card would otherwise delete a
  video that isn't in the library.

- **CI: all tests pass, then `exit code 1`.** (0.15.0, 0.16.2.) Interpreter
  shutdown with hundreds of leftover Qt widgets / pool threads failed on the
  GitHub runner only - never locally. tests/conftest.py now drains the global
  QThreadPool at session end and leaves via `os._exit(pytest's status)`
  (set LUNELIS_TEST_NORMAL_EXIT=1 to debug a real shutdown); CI runs pytest
  with `-X faulthandler`. A failing test still exits 1.

- **Slow reads go through `ui/background.py` (Background).** One run per key,
  a burst of requests coalesces into one re-run with the newest, results
  arrive on the GUI thread and never after close (`@unless_closed` for
  hand-written worker slots). Its worker opens its OWN connection: it only
  sees COMMITTED data - write, commit, then refresh (tests too: a test that
  writes without commit and then refreshes a page sees the old figures).
  Tests wait with `page.bg.wait()`. A lambda (or plain function) connected to
  a signal emitted on another thread runs THERE - on PySide6 6.11 even when
  connected with QueuedConnection (audit LRA-055: the "Folder unavailable"
  QMessageBox opened on the scan thread). Connect worker signals to bound
  methods of a QObject that lives on the GUI thread (`@Slot`), never lambdas;
  tests/test_audit_wiring.py checks main_window for it.

- **Cancel must not be a slot of a worker moved to its thread.**
  `progress.canceled.connect(worker.cancel)` (worker = a QObject after
  moveToThread) is a queued call into a thread busy in run(): it arrives when
  the job has finished, so Cancel did nothing for exports and merges. Use
  `canceled.connect(lambda: worker.cancel())` - it runs on the GUI thread and
  only sets a flag. (The mirror image of the lambda-on-worker-signal gotcha.)

- **The updater's swap script must not stand in the program folder.** A
  process's working folder can't be renamed on Windows, and Lunelis started
  from the Start menu has the program folder as its working folder; the
  PowerShell it launched inherited it, so every Rename-Item failed "in use" and
  updates never applied (0.34-0.37.5). `Set-Location` alone isn't enough - it
  moves PowerShell's location, not the process's folder: the script also sets
  `[Environment]::CurrentDirectory`, Popen gets `cwd=updates_dir()`, and the
  packaged app chdirs to the home folder at start. tests/test_updater_swap.py
  runs the real script.

- **Background work must give way to video playback** (`lunelis/pace.py`).
  Video frames are presented through the GUI thread; Python worker threads
  holding the GIL (library pass, thumbnails, jobs) starved it to ~1 fps on the
  real display (offscreen measurements looked fine - test on QT_QPA_PLATFORM=
  windows). Every per-file worker function and job stop-callback calls
  `pace.breathe()`; VideoPlayer sets the flag while playing.

- **A sampled hash is a candidate filter, never proof of identity.**
  `sample_hash` reads three 64 KB slices; two files can share size, capture
  time and slices and differ in between. Anything that deletes or skips
  copying on "it's already there" (import's `already_in_library`, Clear the
  card) must compare the full SHA-256 (audit LRA-001: Clear the card deleted a
  photo whose bytes were nowhere in the library).

- **Foreign keys without ON DELETE can't be altered - use triggers.**
  `migrations.job_id` and `migration_items.file_id` (migration 16) had no ON
  DELETE, so "Clear finished" and emptying quarantine failed after any
  migration. SQLite can't change a foreign key without rebuilding the table,
  and rebuilding `migrations` would cascade-delete `migration_items`. Migration
  40 adds BEFORE DELETE triggers instead.

- **Never open the real catalog with a normal sqlite3 connection - not even to
  read.** 2026-10-01 a stale WAL from a different (211-page) database sat next
  to the real catalog.db (a catalog had been copied in by hand); closing a
  "read-only" diagnostic connection checkpointed it and truncated the 265 MB
  catalog to 864 KB. Copying db+wal and opening the copy destroys the copy the
  same way. Inspect real catalogs only via copies opened with
  `file:...?immutable=1`, and swap catalogs only through catalog/backup.restore
  (it moves -wal/-shm aside). migrate() now snapshots before any upgrade.

- **QTabWidget ignores `::tab-bar { alignment: center }` in document mode**
  (tab bar x stayed 0). Settings uses document mode off + a styled pane
  (#SettingsTabs), and WA_StyledBackground so the band colour fills.
- **Moving a layout into another widget doesn't move its widgets.** The
  Edit panel's fold-into-sections pass left layout rows (mask buttons,
  Edit/Done) painted at the top; a row layout needs `holder.setLayout(lay)`
  so its widgets are reparented.
- **A local `from x import Settings` anywhere in a function makes
  `Settings` local for the WHOLE function** - MainWindow.__init__ used it
  before a later local import and raised UnboundLocalError (12 tests).
  Module-level import only.

- **Never `QPushButton(..., clicked=self.some_signal.emit)` for a
  no-argument Signal.** clicked(bool) passes `checked` along and PySide
  raises "reset() only accepts 0 argument(s), 1 given" (the Edit panel's
  Reset crashed for the user). Use `clicked=lambda: self.sig.emit()`, and
  test with `button.click()`, not `signal.emit()`.
- **`showNormal()` un-maximizes.** open_page() used it to raise the window
  from the tray and pulled full-screen windows back to normal size.

- **The real library's sidecars carry darktable's automatic tags**
  (`darktable|format|arw`, and the flat words darktable, format, arw in
  dc:subject) - 6 of 600 sampled, and no real keywords. Reading them as
  tags would have tagged thousands of photos "arw".
- **A self-closing rdf:Description can't hold elements.** Our new sidecars
  are `<rdf:Description .../>`; adding a keyword bag has to open it into
  `<rdf:Description ...> ... </rdf:Description>` (sidecar._set_bag).

- **OpenCV's MergeMertens divides float input by 255.** Float frames in
  0..1 gave an all-black HDR; pass them x255 (still float - no 8-bit loss).
- **Normalizing an AI model's output (min-max, as rembg does) invents
  masks:** on an aerial photo with no sky the noise became a "sky". Use the
  raw sigmoid through a soft threshold (0.2..0.8).
- **Sony camera JPEGs are already lens-corrected in camera.** Applying the
  lensfun profile to them double-corrects; that's why the profile is off
  by default and described as mostly for RAWs.
- **Patch scripts through bash heredocs corrupt backslashes** - twice in
  one session: `"\\/"` became `"\/"` in scan.py, and a `\` line
  continuation became a literal `\n` in Lunelis.spec (the local exe build
  caught it before CI). Use the Edit tool for any line with a backslash.

- **A background writer must not race a Reset.** The photo view's preview
  loader used to render + WRITE a missing proxy/thumbnail; a Reset in the
  meantime cleared them first, then the late write put the edited look
  back on an unedited photo. Only the edit controller writes, through one
  ordered single-thread pool.
- **Lunelis.spec excluded piexif** (unused when the build was set up). Export
  needs it; the self-test's "Editing + export" check exists so a missing
  module fails CI instead of the user's first export.
- **Pillow can't put a piexif block in a TIFF** ("Error setting from
  dictionary"): TIFF exports get Make/Model/DateTime/Software as `tiffinfo`.
- **"&" in QToolButton text is a mnemonic** - "B&W Classic" showed as
  "BW Classic". Escape as "&&" (same as QLabel/QAction text).

- **Near-duplicate grouping must not chain.** Union-find over "within 4 bits"
  linked dark frames and night skies through undated files into groups of
  3,126 / 1,291 / 885 photos. Complete linkage + related names + skipping
  near-blank fingerprints brought the largest group to 6.
- **Same pixels, different name, bigger file = maybe an edit.**
  `DSC00913 2.jpeg` (13.3 MB) beside `DSC00913.jpeg` (11.7 MB) at the same
  dimensions is an Apple Photos edit + original. Suggesting the original for
  quarantine because the edit is bigger is wrong; only plainly lesser copies
  are suggested.

- **Uncompressed Sony ARWs are all exactly the same size.** "Same size" as the
  only duplicate prefilter made one folder's job hash 91k files (every RAW of
  that body). Identical bytes imply identical EXIF, so the candidate key is
  size + capture time; files with no capture time fall back to size alone.
- **Windows dark mode leaks into unstyled widgets.** Tables, dialogs and combo
  popups rendered dark with invisible button text inside the light window.
  `theme.apply_palette()` pins Fusion + a token palette app-wide; don't drop
  it for "native" looks.
- **The grid's covering index must include every exif column it reads.**
  Adding `duration_s` to the grid query without adding it to
  `idx_exif_file_captured` silently brings back the ~500 ms load (the join
  falls back to the ~2 KB rows). Migration 12 widened the index.
- **"takeout" in the full path is not a Takeout export.** The detector matched
  a test folder named after its test; only the root's own name counts.
- **Surviving-copy lookups need `idx_files_filename_nocase`.** Matching by
  `LOWER(filename)` scanned all 159k rows per lookup (127 lookups 7.2 s);
  `filename = ? COLLATE NOCASE` / `LIKE 'stem.%'` on the NOCASE index: 6 ms.
  Escape `_` in LIKE patterns - Sony names start with one.
- **Long paths in QTableWidget: word wrap off, elide middle.** With wrap on,
  UNC paths broke at backslashes and showed as "\...".
- **QTableWidget: `clearContents()` before shrinking.** `setRowCount(n)` alone
  left the previous group's cell-widget buttons floating over the table.

- **Restoring a catalog: close every connection to a file before replacing it.**
  Windows refuses `os.replace` over a file that's open, so the snapshot's own
  integrity-check connection has to be closed first (catalog/backup.py).
- **Tests must never see the real data folder.** paths.py resolves the data
  folder at import; tests/conftest.py sets LUNELIS_DATA_DIR before anything
  imports lunelis, so a test that opens the default catalog gets a temp one.

- **`executescript()` is not transactional on its own.** Symptom: a migration
  that failed partway left its tables created but no `schema_version` row, so
  every later launch died on "table already exists". Cause: `executescript`
  COMMITs first and then runs in autocommit. The fix is one script wrapped in
  `BEGIN; ... COMMIT;` with the version INSERT inside it. The obvious fix
  (`conn.commit()` after the version insert, as the original did) doesn't help:
  the DDL was already committed by then.
- **Normalise root paths with `abspath` + `GetLongPathNameW`, never
  `Path.resolve()`.** `resolve()` turns a mapped drive (`Z:\`) into its UNC
  target, so the user sees a path they didn't pick. Plain `abspath` keeps 8.3
  short names (`C:\Users\EXAMPL~1`), so one folder could be added twice under
  two spellings and slip past the overlap check. Both steps are needed.
- **Don't switch the EXIF reader to pyexiv2.** It's faster (14.5 vs 18 ms per
  ARW) and decodes more Sony AF fields, which makes it look like the obvious
  upgrade, but Exiv2 opens paths through the ANSI API on Windows: any file
  under `café` or `日本` fails with "No such file or directory". Takeout album
  folders hit this constantly. exifread reads through a Python file handle.
- **Never decode maker notes on non-RAW files.** Symptom: extraction across
  D:\ stalled at 1 GB RAM and 570 s CPU with no progress. Cause: Google
  Photos / Takeout rewrote Sony JPEGs and moved the maker-note block without
  fixing its internal offsets; exifread's `details=True` then chases ~20k
  garbage entries one read at a time (831 ms for one 8 MB JPEG, far worse on
  others). `details=False` reads the same file in 1 ms. Maker notes are
  decoded only when `is_raw_content()` says the bytes are a camera RAW.
- **Extensions lie.** Takeout ships Picasa-re-saved JPEGs named `.ARW` (79 on
  D:\) and `.DNG` (55), PNGs named `.webp`, GIF contact photos named `.jpg`.
  Hence `sniff()`. And GIF must be tested before MPEG-TS there: `G` (0x47) is
  the TS sync byte, so a loose TS check swallows every GIF.
- **Don't use `rawpy.extract_thumb()` as the first choice for thumbnails.** It
  always returns the LARGEST embedded JPEG - on an A7R V the 9.4 MB full-size
  one - when a 280 KB 1616px preview sits right next to it. 80 ms vs 6.3 ms
  per file locally, and 34x the bytes over SMB. `raw/previews.py` walks the
  IFDs and takes the smallest preview that's big enough.
- **Thumbnail pass on in-camera JPEGs is I/O-bound, not CPU-bound.** Symptom:
  the Takeout folder crawled at 16 files/s with D:\ pinned at 296 MB/s. A
  JPEG must be read in full to decode, and Sony JPEGs are 15-35 MB. They also
  carry an MPF "Large Thumbnail" (200-750 KB) - use it (`_mpf_preview`).
- **After probing MPF frames, `seek(0)` before falling back.** Pillow stays on
  the last frame `seek()`ed; a too-small MPF preview then became the
  thumbnail instead of the main image. Caught by
  `test_mpf_preview_too_small_falls_back_to_main_image`.
- **Decode grid thumbnails with Pillow, not QImageReader.** Symptom: 85-150 ms
  paint stalls after every scrollbar jump even though loads ran on a
  QThreadPool. Cause: PySide6 holds the GIL through QImageReader's decode, so
  four loader threads starved the GUI thread. Pillow releases the GIL while
  decoding/resizing: jump paints dropped to 3-8 ms. "Move it to a thread" is
  not enough in Python if the thread's work holds the GIL.
- **The grid query forces `INDEXED BY idx_exif_file_captured`.** The planner
  picks exif's primary key, which reads each ~2 KB exif row (raw_json) just
  for `captured_at`: 497 ms vs 252 ms at 159k files. Dropping the hint looks
  like a harmless cleanup and doubles the load time.
- **Never parse-and-reserialize XMP (ElementTree, lxml round trips).** It
  renames darktable's namespace prefixes (ns0:), drops the `<?xpacket?>`
  wrapper and reflows the file, so darktable/Lightroom see a foreign file
  full of changes. `xmp/sidecar.py` edits the Rating/Label text in place;
  ElementTree is used only to *validate* the result.
- **darktable allows several colour labels per photo; we model one.** Only
  rewrite `darktable:colorlabels` when the label actually changes, or a
  rating edit silently drops the user's second label.
- **Sync passes must restore the connection's row_factory, not set None.**
  The UI's connection uses sqlite3.Row; resetting it to None broke every
  `row["col"]` after the first sidecar import.
- **The catalog can't live on a network share.** SQLite's WAL needs shared
  memory that SMB doesn't provide - corruption or lock-ups. `paths.
  check_new_data_dir()` refuses UNC paths and mapped network drives
  (GetDriveTypeW == DRIVE_REMOTE); don't relax it for "just the NAS".
- **Styled checkboxes/radios need explicit indicators.** Once QCheckBox has a
  stylesheet, Fusion draws the unchecked box white-on-white (invisible inside
  a Card), and a styled `::indicator:checked` loses its tick - the tick is
  `assets/icons/check.svg` via `image: url()` (a file path; Qt stylesheets
  don't take data URLs). Don't style QSpinBox borders either: the up/down
  buttons break.
- **Worker results must be handled by a bound method, not a lambda/closure.**
  `worker.done.connect(lambda r: ...)` runs in the WORKER's thread (no
  receiver object -> direct call), so touching the GUI connection raised
  "SQLite objects created in a thread can only be used in that same
  thread" (the Migrate page, and the Settings folder move). Connect to
  `self._on_done` and stash any continuation on self.
- **Quarantined files are not missing.** The scanner used to flag a
  quarantined duplicate as missing on the next rescan (it's no longer at its
  rel_path); scan_root now skips rows with quarantined_at set.
- **A QThread still running at exit kills the process (exit 127 / 1) AFTER
  "N passed".** CI failed v0.5.0 with every test green: a page's worker
  thread (album counts, an import preview) outlived its test/window.
  Tests must pump events until the page's `_thread` is None;
  MainWindow.closeEvent quits and waits every page's `_thread`/`_uthread`.
  Check `echo $?` after pytest, not just the summary line.
- **`&` in a Qt button or tab label is a keyboard-shortcut marker.**
  "Bring in & organize" rendered as "BRING IN _ORGANIZE". Use `&&` for a
  literal ampersand (sidebar sections, Settings tab names).
- **Tick-box lists: don't make items Qt-"user checkable" AND toggle on click.**
  A click on the box would then toggle twice. `ui.widgets.row_toggles()`
  keeps the check state on non-checkable items and toggles on click/Enter.
- **Mac-made folder names carry private-use characters** (U+F0xx stand in
  for `: ? *` etc.) - a folder shown as "JPEG" was "JPEG\uf028" and slipped
  past the generic-name list. `_clean_name` strips U+F000-U+F8FF first.
- **Offscreen screenshots have no fonts** (every glyph is a box). Render with
  the native platform and `WA_DontShowOnScreen` instead.
- **Don't swap the grid for QListView/IconMode.** It lays out every item
  up front; at 160k that's seconds per resize or sort.
- **Worker threads open the catalog through `_thread_catalog()`, never
  `main_window.open_catalog`.** Tests patch `mw.open_catalog` to hand the
  window their own connection; a scan / backup / sidecar worker calling it
  got that main-thread connection - "SQLite objects created in a thread can
  only be used in that same thread" on GitHub only (timing), and the worker's
  `finally: conn.close()` would close the test's catalog. Failed the 0.21.0
  build after 434 passes.
- **Tests delete the windows they open (`_no_leftover_windows`).** Without
  it every app-wide stylesheet change restyled all earlier tests' widgets and
  the suite slowed to hours. It waits for the thread pool first and skips a
  window whose QThread still runs - deleting a page mid-work killed the
  process with exit 127 and no traceback.
- **"Home network" is an explicit list, not `ip.is_private`.** Python counts
  documentation and benchmark ranges (203.0.113.0/24, 198.18.0.0/15...) as
  private; the family gallery (gallery.py HOME_NETS) would have let them in.
- **Text from a photo's file is never markup.** Camera / lens / names come
  from EXIF anyone can write; an Info-panel label in AutoText rendered a
  crafted `<a href="file://...">` as a live link (and `<img>` can fetch a UNC
  path). `put()` escapes unless told `rich=True`, `_link` only opens
  `event:` and openstreetmap.org.
- **An update replaces the whole program folder** (and deletes the old one
  later): `updater.check_install_folder` refuses when the data folder or
  anything that isn't Lunelis lives in it; the data folder can't be moved
  into it either.
- **A PIN lockout must count the attempt before checking it.** Checking
  `locked()` and recording the failure after PBKDF2 let ~120 parallel
  guesses through per window (ThreadingHTTPServer). `_Limiter.attempt()`
  reserves the slot first; one PIN check at a time.
- **Settings defaults: insert whole lines.** Patching `"key": value,` as a
  prefix and appending new keys after it pushed the original line's comment
  onto the new keys - comments drifted down the DEFAULTS block for several
  versions before anyone noticed. Match the full line.
- **Timers and late signals after close:** every timer that reads the
  catalog is stopped in closeEvent (`_reach_timer`, `_integrity_timer`...),
  and a window that outlives a test (CullView) handles previews through
  `@unless_closed` - a preview landing after close hit a closed catalog.

## Roadmap (unbuilt)

Damaged-file check (added to the design doc's build order 2026-09-27, after
duplicate detection v1): flag zero-filled / zero-byte / truncated / unreadable
files alongside duplicates and show where an intact copy survives. The first
survey found 41 zero-filled files damaged identically in both NAS pools.

Phase 1 is complete (2026-09-27); Phase 2 step 1 (events) done and step 2 (migration) built
2026-09-27 - migration NOT run on the real library (user: nothing moves until the software is done). Phase 2, in order: events → migration /
consolidation → backups → darktable Lua plugin → near-duplicates / keeper
rules → albums/tags → basic editing. Schema v1 doesn't yet cover things the doc asks for
later (color labels, nested albums, hierarchical tags, stacks, RAW+JPEG
pairing); each lands as a new migration when its step arrives.

## Packaging (Windows build)

- `Lunelis.spec` (repo root, named after the repo for the template's CI):
  PyInstaller ONE-FOLDER build -> `dist\Lunelis\Lunelis.exe` + `_internal\`.
  Not one-file: that unpacks ~150 MB of Qt to %TEMP% on every start and
  trips antivirus more often.
- `packaging/make_version_info.py` writes the exe's version resource from
  pyproject.toml (the one place the version lives) - run it before PyInstaller.
  `paths.version()` reads the same version from package metadata, which the
  spec bundles with copy_metadata("lunelis") - so the package must be
  pip-installed (`pip install .` / `-e .`) before building.
- Frozen-app differences live in paths.py: FROZEN, ASSETS under sys._MEIPASS,
  launch_command() (the exe itself vs `pythonw -m lunelis`) used by Start
  with Windows and restarts. Never hardcode `-m lunelis` or repo-relative
  asset paths anywhere else.
- `Lunelis.exe --self-test <report> [sample files...]` checks a build
  (LibRaw, HEIF, PyAV, assets, migrations, window) against a throwaway data
  folder; CI runs it before attaching the zip to the release. 2026-09-27:
  passed locally, incl. a real A7R V ARW, an iPhone HEIC and a GoPro MP4.
- Release: bump pyproject version + CHANGELOG in one commit, tag vX.Y.Z,
  push the tag; CI (python-release.yml) tests, builds, self-tests, zips
  `Lunelis-vX.Y.Z-windows.zip` and `Lunelis-vX.Y.Z-setup.exe` onto the
  GitHub Release. Unsigned (no paid
  code signing): SmartScreen "More info > Run anyway" once per version.

## Face rules (recognize/faces.py, ui/people_view.py, the photo view overlay)

- Models: OpenCV zoo YuNet + SFace, URLs pinned to opencv_zoo commit
  47534e27..., SHA-256 pinned; run through cv2.FaceDetectorYN /
  FaceRecognizerSF (no extra package). Detection runs on
  `thumbnails.render(path, orientation, 1600)` (a RAW's embedded preview):
  ~0.07 s per photo. Boxes are FRACTIONS of the upright image.
- States live in `faces`: found (person NULL, maybe `cluster`), suggested
  (`suggested_person_id` + `suggestion`), named (`person_id` + confirmed 1),
  ignored. ONLY confirmed faces tag the photo (People|<name>) -
  `_sync_tags` keeps the tag equal to "has a confirmed face of that person"
  after every confirm / reject / ignore / merge / delete; never add or
  remove People tags any other way.
- "Not this person" goes to face_rejections; suggest() skips those pairs.
  Re-scans (a new model) delete only auto, unconfirmed faces of that photo;
  named and hand-drawn (`source = 'user'`) faces stay, and a found face
  overlapping a kept one (IoU > 0.4) isn't added twice.
- Thresholds: suggest at cosine 0.40, group at 0.45 (incremental: each new
  face joins the nearest group centroid or starts one - no O(n^2) pass).
- Face crops (160 px) are cached in <data>/cache/faces/<id // 1000>/<id>.jpg
  at scan time; the People page reads only those.
- Tests use a FakeBackend (colour = identity); the real models are
  checked by hand on real photos (never committed - the repo is public).

## Place rules (geo/places.py, the Map's pins)

- The place list `geo/places.tsv.gz` is BUILT by packaging/build_places.py
  from GeoNames (cities15000 + admin1 + countryInfo, CC BY 4.0) and committed;
  rebuild only to refresh. PPLX (city districts) and historical / abandoned
  places are left out, else Rome came out as "Esquilino". Nearest place
  within 40 km wins on distance minus 5 km per tenfold population (the city
  beats its suburb; a small town far from a city keeps its name).
- Effective location = pin (`locations`) else EXIF GPS (0,0 = none). Pins
  never touch files or sidecars. `photo_places` records the Places tag
  Lunelis gave and the coordinates it came from: a tag is only (re)written
  when those change, so a place tag the user removed by hand stays removed.
- Unknown tags: Places|<Country>|Unknown, Places|Unknown, and
  Places|No location (setting, off by default - it would tag most of a
  no-GPS library). Faces: ignored = 2 is a stranger -> People|Unknown on the
  photo, kept equal to "has a stranger face" by `_sync_unknown`; the name
  "Unknown" is refused for people.

## Installer and first-run setup (0.34)

- `packaging/installer/lunelis.iss` (Inno Setup 6, free): a PER-USER install
  (`PrivilegesRequired=lowest`, no UAC) into `%LOCALAPPDATA%\Programs\Lunelis`,
  Start menu entry, Settings > Apps entry. CI builds it from `dist\Lunelis`
  with `/DAppVersion=`, installs it silently on the runner, self-tests the
  installed exe, uninstalls, and attaches `Lunelis-vX.Y.Z-setup.exe` + `.sha256`.
  Locally ISCC is at `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe`; run it
  from PowerShell - Git Bash rewrites `/D...` arguments into paths.
- **The installer asks, the program applies.** The setup pages write
  `%APPDATA%\Lunelis\setup.json`; `firstrun.read()/early()/apply()` apply it
  once at the next start and rename it `setup.applied.json`. `early()` runs
  BEFORE the catalog opens (a chosen data folder: set_data_dir, or
  request_move when a library already exists, or adopt a copied library).
  The installer never opens the catalog or writes settings itself, so there's
  one code path (the Welcome window, `ui/welcome.py`, produces the same dict
  for zip installs).
- **Install once.** Updates stay in the app: the updater swaps the whole
  program folder, so `carry_uninstaller()` copies `<program>\uninstall` (the
  installer's UninstallFilesDir) into the new version and `--updated` writes
  the new DisplayVersion under the Uninstall key. The AppId GUID in the .iss
  and `updater.UNINSTALL_KEY` must match - never change the AppId.
- A setup.exe over an existing install (install record, a catalog, a
  location.json or a setup.applied.json) skips the setup pages: nothing is
  asked twice and no setup.json is written.
- The program folder must hold only Lunelis: the installer refuses a
  non-empty folder because `[UninstallDelete]` removes `{app}` whole (updates
  add files the installer never listed). The uninstaller deletes the Run
  value and offers to send the data folder to the Recycle Bin (default No).
- Testing the installer on the dev PC would touch the real install, the real
  Run key and the real `%APPDATA%\Lunelis`: test in CI or a throwaway account.

## Updater

- `updater.py`: releases are read from AxialForge/Lunelis itself (public since
  0.16.1; the app has no token and must never carry one). Up to 0.16.0 they
  came from AxialForge/Lunelis-releases (archived): installs older than 0.16.1
  only look there, which is why 0.16.1 was published to both.
- Never install without the `.sha256` asset; zip entries with `..` or an
  absolute path are refused.
- The swap (APPLY_PS1) runs after Lunelis exits: rename the install folder
  to `.old-<time>`, move the new one in, start it with `--updated` (which
  cleans the old folder). Any failure puts the old one back and starts it
  with `--update-failed`. A new version on another drive is copied next to
  the install first (Move-Item can't move a folder across drives).
- The repo is PUBLIC: nothing personal goes in it - no real paths, share
  names, network addresses, names, screenshots or release documents
  (`docs/release-package/` is git-ignored and stays local). Docs use the demo
  library only. The full private history up to 0.16.0 is in
  `C:\Project Folder\Lunelis-full-history-2026-10-03.bundle`.
- Release notes = the version's CHANGELOG section (packaging/release_notes.py,
  written as a UTF-8 file, not stdout: cp1252 mangles the middle dot).

## Wiki

User documentation lives in `docs/wiki/` (Markdown, `Home.md` is the index;
GitHub's own wiki isn't available for this private repo on the free plan).
When a user-visible feature lands or changes, update its wiki page in the same
commit, and the status table in `docs/wiki/Roadmap.md`.

## Release

Version lives in `pyproject.toml`. Bump it and update `CHANGELOG.md` in one
commit, then:

```bash
git tag v0.1.0 && git push origin v0.1.0
```

CI (`.github/workflows/python-release.yml`) runs pytest on every push and, on a
tag, builds with PyInstaller from `Lunelis.spec` — that spec doesn't exist yet,
so the first tagged release needs one (and `main.py`'s repo-root paths for
assets and the catalog need a frozen-app branch first).
