# Roadmap

The full plan, with the reasoning behind each decision, is the design doc
("Lunelis - Feature Set"). This page is the short version.

## Phase 1 - a library you can use every day

| # | Step | Status |
|---|---|---|
| 0-1 | Project, catalog and migrations | Done |
| 2 | Scanning local and network folders | Done |
| 3 | Metadata, files identified by content | Done |
| 4 | Thumbnails from embedded previews | Done |
| 5 | Library grid | Done |
| 6 | Ratings, labels, flags, filters, XMP sidecar sync | Done |
| 7 | Data folder, settings, catalog backups, central sidecars | Done |
| 8 | Background jobs, duplicate detection, quarantine | Done |
| 9 | Moved-file re-linking, damaged-file check | Done |
| 10 | Video thumbnails and dates, Google Takeout dates | Done |
| 11 | Storage templates, SD card import, tray mode | Done |
| 12 | Settings screen | Done |

Phase 1 is complete. See [Settings](Settings.md) for Step 12.

## Phase 2 status

| # | Step | Status |
|---|---|---|
| 1 | Events | Done - see [Events](Events.md) |
| 2 | Migration / consolidation | Built - see [Migrate](Migration.md); not yet run on the real library |
| 3 | Backups (USB drive, stick or network folder; verify; restore) | Done - see [Backups](Backups.md) |
| - | Test build: Lunelis.exe (built by CI, v0.2.0) | Done - see [Getting started](Getting-Started.md#install) |
| 4 | darktable plugin | Done - see [darktable](darktable.md) |
| - | Look and feel (v0.3.0): themes, sidebar sections, Settings tabs, log | Done |
| - | Updater (v0.3.1: checks the public releases repo, verified, safe swap) | Done |
| - | Photo detail view, hover info, smooth grid slider, timeline scrubber (v0.4.0) | Done |
| - | Albums page (Google Photos-style, events inside), import from any drive (v0.5.0) | Done |
| 5 | Near-duplicates (keeper rules, set aside) + burst stacks (v0.6.0) | Done |
| 7 | Editing, Phase A: light, colour, detail, crop/straighten, filters, copy/paste, export (v0.7.0) | Done - see [Editing](Editing.md) |
| - | Editing, Phase B: tone curve, masks (gradient, radial, brush, AI subject/sky), lens corrections, HDR/panorama (v0.8.0) | Done - see [Editing](Editing.md) |
| 6 | Tags: nested keywords, Tag filter, Tags page, XMP both ways (v0.9.0) | Done - see [Tags](Tags.md) |
| - | Search box: full-text index, dates, keywords, fields (v0.10.0) | Done - see [Search](Search.md) |
| - | Quarantine page: restore, empty safely (v0.11.0) | Done - see [Quarantine](Quarantine.md) |
| - | Photo view zoom, noise reduction, Settings General/Edit tabs (v0.12.0); wheel tilt, manual names every screen part (v0.12.2) | Done |
| - | Look and feel: icon sidebar (Ctrl+B), scan progress in the status bar, small and scaled screens (v0.13.0) | Done |
| - | Library status page; Archive, with moving the Archive to a drive (v0.14.0) | Done |
| - | Edit page: an editing workspace with batch tools (v0.15.0) | Done |
| - | Camera profiles, Sony sidecars, clear the card; locked formats; interface audit fixes; `?` sheet; rating undo (v0.16.0) | Done |

## Phase 2 - managing the library

1. **Events** - trips and shoots as named date ranges, created by hand,
   suggested from gaps in capture time, or read from existing folder names.
2. **Migration / consolidation** - move everything onto one drive with a
   storage template, one good copy of each photo, with a verified
   copy-then-delete for every file.
3. **Backups** - to a USB drive, stick or network folder; incremental and
   verified; restore. Encryption later.
4. **darktable plugin** - so darktable sees ratings in the central sidecar folder.
5. **Smarter duplicates** - near-duplicates (resized, re-compressed copies),
   same-shot matching, keeper rules, burst stacks.
6. **Tags (keywords)** - done in v0.9.0 (albums in v0.5.0).
7. **Non-destructive editing** - Phase A (v0.7.0) and Phase B (v0.8.0)
   done; layers later.

## Next

The full timeline, version by version, is the [Feature plan](Feature-Plan.md).
In short:

| Version | What |
|---|---|
| 0.16 | Locked schemas, an interface audit, import hardening (camera profiles, Sony video sidecars, import history) |
| 0.17 | Create tab: GIF / MP4 / WebP maker, collage, batch tools, one export engine - done in v0.17.0 |
| 0.18 | Phone and USB-stick import (HEIC, Live Photos, motion photos) - done in v0.18.0 |
| 0.19 | Culling mode, smart albums, RAW+JPG pairing - done in v0.19.0 |
| 0.20 | Scene-aware tagging with a review queue - done in v0.20.0 |
| 0.21 | Ask your library; your shooting stats - done in v0.21.0 |
| 0.22 | Create tab round two - done in v0.22.0 |
| 0.23 / 0.24 | Video and GIF playback (done in v0.23.0); S-Log previews (done in v0.24.0) |
| 0.25 | Lunelis noticed - done in v0.25.0 |
| 0.26 | Offline-NAS view, protection indicator, map and calendar - done in v0.26.0 |
| 0.27 / 0.28 | Learn My Look (done in v0.27.0); Autopilot Import (done in v0.28.0) |
| 0.29 - 0.32 | Editing suite (done in v0.29.0), sensor dust map (done in v0.30.0), family gallery (done in v0.31.0), Create round three (done in v0.32.0) |

## Later

Face and content recognition (on this PC, or a GPU server on your network),
map and timeline views, natural-language search, a Mac version.
