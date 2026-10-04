# Interface audit (October 2026)

All 34 files in `src/lunelis/ui/` checked against the feature list's GUI
checklist (performance, layout, interaction, messages, reliability) for 0.16.
Status: ✅ fixed (version) · 📅 planned (version) · — open.

## Blockers and high

| Finding | Status |
|---|---|
| Rating a big selection rebuilt a set for every one of 159k rows (Ctrl+A + a star key froze for minutes) | ✅ 0.16 |
| Quarantine page checked every file on the NAS on the GUI thread, on every redraw | ✅ 0.16 - checked once, on a worker |
| Restoring from quarantine ran on the GUI thread | ✅ 0.16 - worker with progress and Stop |
| An error while emptying quarantine left the progress dialog up forever | ✅ 0.16 |
| After an HDR / panorama, every pending thumbnail in the catalog was made on the GUI thread | ✅ 0.16 - just the new file |
| Ratings, labels and flags had no undo and no confirmation for huge selections | ✅ 0.16 - Ctrl+Z (20 steps), confirm above 500 photos |
| Cancel on Export / Merge didn't stop them (queued into the busy worker) | ✅ 0.16 - and noted in CLAUDE.md |
| A QLabel was set from a worker thread while moving duplicates | ✅ 0.16 |
| Closing could destroy a running thread (process abort mid-copy); Damaged files' thread wasn't waited for | ✅ 0.16 - every running thread is waited for, with a notice |
| Closing after a big rating change wrote every sidecar on the GUI thread | ✅ 0.16 - more than 200 wait for the next start |
| Migrate: Discard / Release usable while the plan's worker ran | ✅ 0.16 - disabled while busy; Discard asks |
| Starting an import re-read the whole card on the GUI thread | ✅ 0.16 - on the import's worker |
| "Rebuild all thumbnails" deleted ~5 GB on the GUI thread | ✅ 0.16 - renamed aside, deleted in the background |
| Near-duplicate search couldn't be stopped | ✅ 0.16 - Stop button |
| Backups page: status of each set (NAS checks, counts) on the GUI thread | ✅ 0.16.2 - on a worker |
| Library status page: counts on the GUI thread | ✅ 0.16.2 - on a worker |
| Migrate page: plan summary (~140k items, NAS free space) on every show | ✅ 0.16.2 - on a worker; Start waits for it |

## Medium

| Finding | Status |
|---|---|
| Right-click didn't select the photo under the pointer | ✅ 0.16 |
| Window size, place and monitor forgotten | ✅ 0.16 |
| Grid size from Ctrl+wheel not remembered | ✅ 0.16 |
| A failed damage check looked like success | ✅ 0.16 |
| Tag merge / rename onto an existing tag without confirmation | ✅ 0.16 |
| Jobs panel: stray progress bars after Clear finished | ✅ 0.16 |
| Duplicates: UNC paths wrapped at backslashes | ✅ 0.16 |
| Error text in Export / Merge ignored the theme | ✅ 0.16 |
| Library reload (index + stats) on every return to the page and every 15 s during scans | ✅ 0.16.2 - on a worker, and skipped when nothing changed |
| Card inserted: whole card walked on the GUI thread; pulling it mid-walk raised an error | ✅ 0.16.2 - counted on a worker; a pulled card just gets no message |
| Start-up `isdir` on an unfinished NAS import's folder | ✅ 0.16.2 - on a worker |
| Search re-indexes without a limit on the GUI thread | ✅ 0.16.2 - 2,000 rows, the rest on a worker |
| Paste / Reset to many photos saved on the GUI thread | ✅ 0.16.2 - more than 20 on a worker |
| Photo view: neighbour preloads never cancelled when holding an arrow key | ✅ 0.16.2 |
| Preview thread could run the AI mask model | ✅ 0.16.2 - cached masks only |
| Undo of a crop while cropping didn't move the crop frame | ✅ 0.16.2 |
| Mask overlay rebuilt on every mouse move; Auto / Before on the GUI thread | ✅ 0.16.2 - only when the area changes, 60 ms while dragging; Auto on a pool thread; Before cached |
| AI model download synchronous with processEvents | ✅ 0.16.2 - Settings uses the pool-thread download |
| Settings refresh walks the cache and lists snapshots after every toggle | ✅ 0.16.2 - on a worker, at most once a minute |
| Import page: drive list (network free space) on the GUI thread; Clear the card on the GUI thread | ✅ 0.16.2 - both on a worker |
| Duplicates / Tags pages: heavy counts on the GUI thread | ✅ 0.16.2 - on a worker |
| Edit page doesn't fit at the 900 x 350 minimum | ✅ 0.16.2 - the bar folds; the photo can be 100 px tall |
| Import preview: a card pulled mid-preview says "No photos found" | ✅ 0.16.2 - says it was removed |
| Album tiles not reachable by keyboard | 📅 0.19 (culling / keyboard work) |
| Worker-result slots without a closed-catalog guard | ✅ 0.16.2 - `@unless_closed` |

## Low

| Finding | Status |
|---|---|
| Fonts in px ignore Windows "Make text bigger"; 9 px grid badges | ✅ 0.16.2 - points, scaled with the text size |
| Thumbnails on some pages blurry above 100 % scaling (no devicePixelRatio) | ✅ 0.16.2 |
| Status bar squeezes the message near 900 px during a scan | ✅ 0.16.2 - shorter bar, message cut with a tooltip |
| Job title wrong when the picked folder is a source | ✅ 0.16.2 |
| Raw exception text in some dialogs | ✅ 0.16.2 - system errors in words (widgets.plain) |
| Restore catalog from a backup set / Dismiss suggestions without confirmation | ✅ 0.16.2 - both ask |

## Missing features (not bugs)

| Missing | Plan |
|---|---|
| Undo for tags, albums, events, archive, stacks, paste/reset to many | — rating undo done in 0.16; the rest with a general history (0.19) |
| Drag and drop (onto albums, out to Explorer) | 📅 0.19 |
| Pause for scans, imports, exports, merges | — |
| Space to toggle selection; keyboard context menu | 📅 0.19 (culling mode) |
| Drive vanishing mid-operation explained in the interface | 📅 0.26 (offline-NAS view) |
