---
type: dataflow
title: Lunelis Data Flow
subtitle: Where every photo, setting and byte comes from, goes and stays
audience: Maintainers, reviewers and anyone judging privacy
sources: CLAUDE.md and src/lunelis (catalog, importers, raw, xmp, importing, edit, dupes, migrate, backups, jobs, recognize, geo, updater, gallery, darktable)
status: Draft for review (pilot of the documentation kit)
---

# The big picture

Lunelis is one Python program on one PC. It indexes photos where they already sit, in folders called **sources** (the code calls them roots). It keeps its own records in a **data folder** and one SQLite **catalog**. This document follows the data: what is read, what is written, where it is kept, and the few places where anything leaves the PC.

::: stats
44 | schema migrations
9 | background job kinds
4 | internet hosts the app contacts
28 | file types cataloged
:::

```mermaid System context. Photos stay on their disks, Lunelis keeps its records in the data folder, and the internet is optional and outbound only.
%%{init: {"flowchart": {"rankSpacing": 24, "nodeSpacing": 20, "padding": 8}}}%%
flowchart TB
  user([You]) --> app
  cards["Memory cards"] -- "copy in, verified" --> app
  lan(["Phones and TVs on the<br/>home network"]) -. "shared albums" .-> app
  app["<b>Lunelis</b> - one process"] <--> data[("<b>Data folder</b><br/>catalog, caches, sidecars,<br/>snapshots, models")]
  app -- "read; write only<br/>what you ask for" --> photos["<b>Photo sources</b><br/>local, USB, NAS"]
  app -- "copy out" --> backups["Backup drives"]
  app <-. "ratings,<br/>sidecars" .-> dt["darktable"]
  app -. "outbound only" .-> net(("Internet"))
```

- **Originals are read, not changed.** Lunelis writes into photo folders in a few cases only: an XMP sidecar next to a photo, files you import, files a migration copies, and renames into a `_Lunelis Quarantine` folder. Each case has its own chapter below.
- **Everything else lives in the data folder.** The catalog, thumbnails, edit previews, models, snapshots and the central sidecar store never go in a photo folder or the program folder (`paths.py`).
- **No photo data is sent anywhere.** The program contacts four hosts, all outbound. They see an internet address and a request, never a photo, a name or a location.

Each flow chapter that follows has the same parts: a diagram, a step table (step, from, to, data, stored in) and a box of rules that must not regress. The ends of the document hold the data inventory, the failure paths, what could not be confirmed (**To check**) and where the code and the project notes disagree (**Discrepancies**). Facts come from the code of version 0.46.0; file names are given so each one can be checked.

# Threads and trust boundaries

Lunelis is one operating-system process. Work is split across threads so the window never waits for a disk. A SQLite connection cannot cross threads, so every worker opens its own connection to the same catalog file (`open_catalog`: WAL mode, 20 second lock wait).

```mermaid Four layers inside the one process. Layer 2 threads each hold a catalog connection; the pools only read files and cached images.
%%{init: {"flowchart": {"rankSpacing": 30, "nodeSpacing": 20, "padding": 8}}}%%
flowchart LR
  L1["<b>1 UI thread</b><br/>window, grid,<br/>photo view, Edit panel<br/>own catalog connection"]
  L2["<b>2 Background threads</b><br/>Library worker, Job runner<br/>Sidecar writer, Import worker<br/>Export, merge, page loaders<br/>each: own catalog connection"]
  L3["<b>3 Pools</b> (no connection)<br/>Thumbnail loaders x4<br/>Preview loaders x2<br/>Render x1, outputs x1<br/>File readers x8"]
  L4[("<b>4 Storage</b><br/>catalog.db (WAL)<br/>cache/<br/>photo sources")]
  L1 --> L2
  L1 --> L3
  L2 --> L4
  L3 --> L4
```

| Worker | Runs as | What it does |
|:--|:--|:--|
| Library worker | One `QThread` per pass | The library pass (below). Readers inside a step run 8 at a time |
| Job runner | One `QThread` per session | Takes queued jobs one at a time. 9 kinds in `jobs/engine.py` |
| Sidecar writer, import worker | A `QThread` per write or import | `export_pending`, 1.2 s after the last change; stage, verify and place card files |
| Thumbnail loaders | `QThreadPool`, 4 threads | Decode cached JPEGs for visible tiles. 1,500 pixmaps kept |
| Photo view, editor | Pools of 2, 1 and 1 | Preview decode, live render, edit outputs in order |
| Family gallery | Server thread plus request threads | Only while an album is shared. One catalog connection per request |

The **library pass** runs on one worker, in this order: scan, find moved files, read sidecars, read metadata, Takeout dates, burst stacks and timelapses, search index, find moved files by content, thumbnails, near-duplicate fingerprints, scene tags, faces, places, suggestions, damage check. A failed step is logged and the rest still run. Every per-file worker calls `pace.breathe()`, which waits (at most 600 s) while a video plays, so playback is not starved.

## Trust boundaries

| Boundary | What crosses it | Guard in the code |
|:--|:--|:--|
| You and the window | Keys, clicks, dialogs | Confirm before rating over 500 photos, downloads, emptying quarantine |
| App and photo sources | Reads; writes only as listed below | Read-only opens, temp name then replace, hash re-read, no rename or overwrite |
| App and data folder | Catalog, caches, models, snapshots | `paths.py` refuses a network path |
| App and memory cards | Reads, hashes; one delete you trigger | Clear the card re-hashes both copies |
| App and backup drives | Copies out, reads back | Volume serial, hashes, nothing deleted |
| App and other programs | XMP text, TSV files, darktable `luarc`, Takeout JSON | Text edits, newest change wins |
| App to internet | Update check, downloads, map tiles | HTTPS, SHA-256, opt-in, no photo data |
| Home network to app | Gallery requests | Home addresses, key per album, PIN, rate limits |
| Installer, updater | `setup.json`, release zip, swap script | `.sha256`, no `..` in the zip, old version restored |
| Model files | ONNX loaded into memory | SHA-256 at download only |

## What Lunelis writes into photo folders

| In a photo folder | When | What happens to it |
|:--|:--|:--|
| `name.EXT.xmp` sidecar | `beside` mode, or one exists and the update setting is on | Stays. Edited as text, never re-serialized |
| `.lunelis-*.xmp.tmp` | While a sidecar is written | Renamed over it, or deleted on failure |
| `.lunelis-importing-*`, `.lunelis-migrating-*` | While a file is copied in | Renamed to the real name after the flush |
| Imported and migrated files | An import or a migration | Stay. Names never change |
| `_Lunelis Quarantine/` | Copies and originals set aside (no Lunelis folder set) | Stays until you empty it |

# Scanning folders into the catalog

The scan makes the `files` table match the disk. It reads directory listings only: names, sizes and times. It never opens a photo.

```mermaid Scan. An offline source is refused rather than scanned as empty, and only a complete walk can flag files missing.
%%{init: {"flowchart": {"rankSpacing": 24, "nodeSpacing": 20, "padding": 8}}}%%
flowchart LR
  subgraph c1["1 Trigger"]
    direction TB
    A["Add folder, F5,<br/>end of an import"] --> B["Source reachable?"]
    B -- no --> X["Stop with a message.<br/>Nothing marked missing"]
  end
  subgraph c2["2 Walk and compare"]
    direction TB
    C["scandir: one listing<br/>per folder"] --> D["Compare with files rows<br/>source + relative path"]
    D --> F["Walk complete?"]
  end
  subgraph c3["3 Write"]
    direction TB
    G["Flag unseen files<br/>missing_since"] --> E[("files, roots")] --> I["Re-link moved files"]
  end
  c1 --> c2 --> c3
```

| Step | From | To | Data | Stored in |
|:--|:--|:--|:--|:--|
| 1 | You | Library worker | Source ids from Add folder, F5 or an import. One pass at a time | Memory |
| 2 | Library worker | Source | A folder-exists check. A missing source raises `RootUnavailable`, never "empty" | Nothing |
| 3 | Source | Library worker | Per folder: name, size, time, XMP names. Hidden, system and link entries skipped | Memory |
| 4 | Library worker | Catalog | New: insert. Size or time changed: update, clear hashes and thumbnail. Back again: clear the missing flag. Commit every 2,000 rows | `files` |
| 5 | Library worker | Catalog | Complete walk only: unseen files get `missing_since` (not quarantined, not in unreadable or skipped folders) | `files`, `roots` |
| 6 | Library worker | Catalog | A missing row takes over a new file with the same size and time. Rows with user data never merge | `files`, `file_moves` |

::: warn Scan rules that must not regress
- The scanner never deletes a `files` row. A missing file keeps its ratings, albums and faces.
- An unreachable source is never scanned as empty. A cancelled scan or an unreadable subfolder marks nothing missing under it.
:::

# Reading metadata and sidecars

Sidecars are read first, then the files themselves. Readers run 8 files at a time and open everything read-only. Catalog writes stay on one thread, in batches.

```mermaid Three readers feed the catalog. Rows are committed in batches, so a stopped pass resumes where it left off.
%%{init: {"flowchart": {"rankSpacing": 24, "nodeSpacing": 20, "padding": 8}}}%%
flowchart LR
  S["XMP sidecar<br/>beside the photo"] --> S2["8 threads<br/>read it"] --> P["Parse stars, reject,<br/>label, tags"] --> R[("ratings,<br/>file_tags")]
  F["Photo or video"] --> F2["8 threads read it,<br/>read-only"] --> X["Sniff real format;<br/>exifread or PyAV"] --> E[("exif,<br/>files.format")]
  J["Takeout JSON<br/>(Takeout sources)"] --> J2["Match by the<br/>file's title"] --> T["Fill a missing date<br/>or GPS only"] --> E2[("takeout_meta,<br/>exif")]
```

| Step | From | To | Data | Stored in |
|:--|:--|:--|:--|:--|
| 1 | Sidecar | Library worker | Sidecars changed since the last sync, if no Lunelis change is waiting. Rating (-1 = reject), label, tags | Memory |
| 2 | Library worker | Catalog | A rating row only if one exists or values are not default. Tags only added. `lunelis:EditStack` parsed, never applied | `ratings`, `file_tags` |
| 3 | Original | Library worker | First bytes (real format), then EXIF. Maker notes only for real camera RAW. Videos through PyAV | Memory |
| 4 | Library worker | Catalog | Fields plus all EXIF as zlib JSON. Format and RAW flag set from the bytes. Batches of 200 | `exif`, `files` |
| 5 | Library worker | Catalog | A failed read stores `read_error`, retried only when the file changes. An offline share leaves files pending | `exif` |
| 6 | Takeout JSON | Catalog | Date and GPS where the file has none. Never overrides EXIF | `takeout_meta`, `exif` |

::: warn Metadata rules that must not regress
- The extension is a guess. Decoders ask `files.format`, which comes from the bytes (`importers/formats.py`).
- No pyexiv2 (it cannot open non-ASCII paths on Windows). No maker notes on non-RAW files.
:::

# Thumbnails and previews to the grid

```mermaid Thumbnails are made once, in the background. The grid only ever loads the small cached JPEGs.
%%{init: {"flowchart": {"rankSpacing": 24, "nodeSpacing": 20, "padding": 8}}}%%
flowchart LR
  subgraph c1["Background: thumbnail pass"]
    direction TB
    o["Original on disk or NAS"] --> p["Cheapest preview<br/>by real format"] --> j["512 px sRGB JPEG, q80"]
  end
  c[("cache/thumbnails<br/>files.thumbnail_path")]
  subgraph c2["UI thread: the grid"]
    direction TB
    i["LibraryIndex:<br/>rows in memory"] --> g["PhotoGrid paints<br/>visible tiles only"] --> t["ThumbCache:<br/>4 threads, Pillow"]
  end
  c1 --> c --> c2
  c2 -. "open a photo" .-> v["Photo view:<br/>2560 px preview"]
```

| Step | From | To | Data | Stored in |
|:--|:--|:--|:--|:--|
| 1 | Original | Thumbnail pass | Smallest embedded JPEG of 512 px or more (RAW), camera JPEG preview, HEIC, or a video poster frame. Decoded in 8 threads | Memory |
| 2 | Thumbnail pass | Cache | Upright sRGB JPEG, 512 px long edge, quality 80, written to a temp name then replaced | `cache/thumbnails/` |
| 3 | Thumbnail pass | Catalog | Path or error text, plus a 64-bit fingerprint (dHash) taken from the picture in memory | `files` |
| 4 | Catalog | Grid | One query loads every row as plain tuples. Burst stacks collapse in Python | Memory only |
| 5 | Cache | Grid | A visible tile with no pixmap queues a load. Pillow decodes; the UI thread converts | Memory, 1,500 pixmaps |
| 6 | Original | Photo view | Biggest embedded preview, up to 2560 px. A full RAW decode only when you zoom past it | Memory |

::: important No RAW decode in the scroll path
The grid and the filmstrip load only cached JPEG thumbnails. Paint never touches the disk or the catalog. A RAW is decoded only on demand: in the editor, on an export, or when you zoom past the embedded preview. Deleting the thumbnail folder rebuilds it on the next pass; a file that fails keeps `thumb_error` until it changes.
:::

# Ratings and tags to XMP sidecars

```mermaid Sidecar export. One setting picks where the file goes, and an existing sidecar can be kept in step as well.
%%{init: {"flowchart": {"rankSpacing": 22, "nodeSpacing": 18, "padding": 8}}}%%
flowchart LR
  subgraph c1["1 Change"]
    direction TB
    A["Rating, flag, label,<br/>tag or edit saved"] --> B[("ratings.xmp_pending = 1")] --> C["Sidecar writer thread:<br/>1.2 s after the last change,<br/>or at close"]
  end
  subgraph c2["2 Where it goes (sidecar_mode)"]
    direction TB
    E["central: data folder,<br/>sidecars/ (default)"] ~~~ F["beside: name.EXT.xmp<br/>next to the photo"] ~~~ H["existing sidecar next to the<br/>photo: kept in step, never created"] ~~~ G["catalog: nothing written"]
  end
  subgraph c3["3 Record"]
    direction TB
    I["Clear pending only if the<br/>rating is still what was written"]
  end
  c1 --> c2 --> c3
```

| Step | From | To | Data | Stored in |
|:--|:--|:--|:--|:--|
| 1 | You | Catalog | Stars 0 to 5, pick or reject, colour label, tag, or edit stack | `ratings`, `file_tags`, `edits` |
| 2 | Catalog | Sidecar writer | Every row with `xmp_pending = 1`: stars, flag, label, manual tags, edit text | Memory |
| 3 | Sidecar writer | Central store | `sidecars/<id>-<source name>/<folder>/<file>.EXT.xmp` | Data folder |
| 4 | Sidecar writer | Photo folder | Only in `beside` mode, or if a sidecar exists and the update setting is on (default) | Next to the photo |
| 5 | Sidecar writer | Sidecar | Text edit of changed fields only, checked well-formed, temp file then replace. A newer unwritten Lunelis change wins over the file | XMP text |
| 6 | Sidecar writer | Catalog | Pending cleared if stars, flag and label still match. A failure keeps it pending, with the error | `ratings.xmp_error` |

::: warn Sidecar rules that must not regress
- Never parse and re-serialize XMP: it renames darktable's namespace prefixes. Only changed values are edited as text.
- Reject is `xmp:Rating = -1`. A pick has no XMP field and never creates a sidecar. Adobe `crs:` settings are never written.
:::

# Card import

```mermaid Card import. Two hash checks guard the copy, and the staged copy is deleted only after the library copy verifies.
%%{init: {"flowchart": {"rankSpacing": 22, "nodeSpacing": 18, "padding": 8}}}%%
flowchart LR
  subgraph c1["1 Card to staging"]
    direction TB
    A["Find media in the camera's<br/>folders; skip exact copies<br/>already in the library"] --> C["Copy to staging,<br/>hash while reading"] --> D["Re-read staged copy:<br/>same hash?"]
  end
  subgraph c2["2 Staging to library"]
    direction TB
    E["Template picks the FOLDER;<br/>file name unchanged"] --> F["Copy into the library;<br/>re-read: same hash?"] --> H["Delete staged copy"]
  end
  subgraph c3["3 Afterwards"]
    direction TB
    I["Rescan the destination,<br/>join the event"] --> J["Clear the card:<br/>only when you ask"]
  end
  c1 --> c2 --> c3
```

| Step | From | To | Data | Stored in |
|:--|:--|:--|:--|:--|
| 1 | Card | Catalog | List of media, companions (Live Photo video) and sidecars, one row each | `imports`, `import_items` |
| 2 | Card | Staging | Copied to a `.lunelis-importing-*` temp name, hashed while read, flushed, renamed, re-read and compared | `staging/import-<id>/` |
| 3 | Staging | Library folder | Folder from the template and capture date. A different file with the same name goes to a sibling folder, `... (2)` | Your destination |
| 4 | Library folder | Library worker | The new copy is re-read and compared with the card hash. Only then is the staged copy deleted | `import_items.state` |
| 5 | You | Card | Clear the card re-checks size, time and both hashes, then deletes card files. A changed file is left | The card |

Staging is local while the disk keeps `import_local_reserve_gb` free (50 GB by default), then spills to the network staging folder if set, else the import waits. A pulled card or sleeping share sets `waiting`; the import resumes from `import_items`.

::: warn Import rules that must not regress
- Imported files are never renamed. The template chooses folders only.
- "Already in the library" and "Clear the card" compare the **full SHA-256**; a sampled hash is never proof (audit LRA-001).
:::

# Edits, previews and export

```mermaid Edits are instructions, not pixels. Only the editor writes the cached look, through one ordered thread.
%%{init: {"flowchart": {"rankSpacing": 22, "nodeSpacing": 18, "padding": 8}}}%%
flowchart LR
  subgraph c1["1 Editing"]
    direction TB
    A["Edit panel<br/>(UI thread)"] --> V["Live preview:<br/>1 render thread,<br/>screen only"]
  end
  subgraph c2["2 Save, 700 ms after the last change"]
    direction TB
    B[("edits.stack, rev + 1<br/>ratings.xmp_pending")] --> C["Sidecar writer:<br/>lunelis:EditStack"]
  end
  subgraph c3["3 Leaving the editor"]
    direction TB
    D["Output thread (1)"] --> E[("cache/edits proxy, 2560 px,<br/>+ edited thumbnail")]
  end
  c1 --> c2 --> c3
  X["Export or Create"] --> R["Render full size<br/>in strips"] --> N["New file in the folder you pick.<br/>Never overwrites"]
```

| Step | From | To | Data | Stored in |
|:--|:--|:--|:--|:--|
| 1 | Original | Edit panel | RAW through rawpy (half size when enough), JPEG at its own pixels. Float RGB, upright | Memory |
| 2 | Edit panel | Catalog | The stack as text: filter, sliders, crop, curves, masks. An identity stack deletes the row | `edits`, `copies` |
| 3 | Catalog | Sidecar | `lunelis:EditStack`, when sidecars are written (previous chapter) | XMP text |
| 4 | Edit panel | Cache | 2560 px JPEG (quality 90) and a 512 px thumbnail with the edited look | `cache/edits/`, `cache/thumbnails/` |
| 5 | Original | Export | Full render in strips, sharpening, colour profile. EXIF rebuilt from the catalog: all, no location, or none | A new JPEG, TIFF or PNG |
| 6 | Edit panel | Cache | AI mask maps, made from the upright source and kept as PNG | `cache/masks/` |

::: warn Editing rules that must not regress
- The source file is never overwritten. An export is always a new file ("name (2).jpg" if the name is taken).
- Only the edit controller writes proxies and thumbnails. A late background write once put an edited look back on a reset photo.
- Exports use `apply_tiled` (strips). Never `apply` at full size: 60 MP in one piece needs several GB.
:::

# Duplicates and quarantine

```mermaid Duplicates. A sampled hash is a hint; only a full hash makes a group exact, and only exact groups can be set aside.
%%{init: {"flowchart": {"rankSpacing": 22, "nodeSpacing": 18, "padding": 8}}}%%
flowchart LR
  subgraph c1["1 Find"]
    direction TB
    A["You start a job<br/>(jobs, job_folders)"] --> C["Job runner, one folder<br/>at a time: sample hash"]
  end
  subgraph c2["2 Prove"]
    direction TB
    D[("groups: sampled")] --> F["Verify job:<br/>full SHA-256"] --> G[("groups: exact,<br/>verified")]
  end
  subgraph c3["3 Set aside"]
    direction TB
    H["You set extras aside:<br/>snapshot, merge stars and<br/>tags into keeper, rename"] --> J["Quarantine page:<br/>Restore or Empty"]
  end
  c1 --> c2 --> c3
```

| Step | From | To | Data | Stored in |
|:--|:--|:--|:--|:--|
| 1 | You | Catalog | A scope, a schedule (now, idle, night), a speed limit. A folder is marked done only after its work is committed | `jobs`, `job_folders` |
| 2 | Original | Job runner | Sample hash: size plus 3 slices of 64 KB (about 200 KB a file), only for files sharing size and capture time | `files.sample_hash` |
| 3 | Original | Job runner | Verify job: every byte, SHA-256. Identical files form an exact group | `content_hash` |
| 4 | Catalog | Snapshot, catalog | Before a batch: a snapshot. Stars, tags, albums and sidecar of the extra go to the kept copy | `backups/`, `ratings` |
| 5 | Source | `_Lunelis Quarantine` | A rename on the same drive (copy, compare, remove across drives), sidecar included | `files.quarantined_at` |
| 6 | You | Recycle Bin | Empty: the kept copy must verify on disk. Local to the Recycle Bin; network removed after an extra tick | `purged` |
| 7 | Thumbnail pass | Catalog | Near-duplicates: a dHash per photo, grouped by complete linkage (4 bits, not RAW, same moment). Method `similar` | `perceptual_hash` |

::: warn Duplicate rules that must not regress
- Only verified byte-identical groups can be set aside, never down to zero live copies.
- A job never hard-deletes. Emptying quarantine is a user action with its own snapshot.
:::

# Migration

```mermaid Migration. The same catalog row is repointed to the verified copy, so ratings, tags and thumbnails follow the file.
%%{init: {"flowchart": {"rankSpacing": 22, "nodeSpacing": 18, "padding": 8}}}%%
flowchart LR
  subgraph c1["1 Plan and start"]
    direction TB
    A["Plan: a dry run,<br/>reads the catalog only"] --> B["Start: snapshot, before.csv,<br/>add the target as a source"]
  end
  subgraph c2["2 Per file"]
    direction TB
    C["Copy to a temp name, hash while<br/>reading; re-read: same hash?"] --> E["Sidecar copied<br/>and compared"] --> F["Repoint the SAME row"]
  end
  subgraph c3["3 Original and end"]
    direction TB
    G["Keep to review: state kept.<br/>Otherwise renamed aside"] --> H["Finish: merge duplicates,<br/>manifest, after.csv, report"]
  end
  c1 --> c2 --> c3
```

| Step | From | To | Data | Stored in |
|:--|:--|:--|:--|:--|
| 1 | Catalog | Plan | Per file: move, skip duplicate (an exact twin moves), or skip damaged (an intact copy exists). Folder from the template | `migration_items` |
| 2 | You | Logs folder | Start: snapshot, then `before.csv` listing every file in every source | `Migration logs/` |
| 3 | Source | Target | Copy to `.lunelis-migrating-*`, hashed while read, re-read and compared. A name clash goes to a sibling folder | Target folder |
| 4 | Source | Target | The sidecar is copied beside the photo and compared byte for byte | Target folder |
| 5 | Migration | Catalog | `root_id` and `rel_path` of the same row repointed, move logged, state `copied` | `files`, `file_moves` |
| 6 | Source | Set-aside | State `done`: renamed to the Lunelis folder's Trash, or `_Lunelis Quarantine/migration-<id>` on its own drive. `kept` in review mode, and the scanner skips it | Source drive |
| 7 | Catalog | Logs folder | End: `manifest.csv` (SHA-256 of each copy), `after.csv`, an accounted-for report | `Migration logs/` |

::: warn Migration rules that must not regress
- A plan changes nothing on disk. Never delete, overwrite or rename. Originals are released only when the report finds nothing missing.
- A crash is safe: before step 5 the source is untouched; after it a resume only finishes the original.
:::

# Backups and catalog snapshots

```mermaid Two separate safety nets. Snapshots protect the catalog; backup sets protect photo files.
%%{init: {"flowchart": {"rankSpacing": 22, "nodeSpacing": 18, "padding": 8}}}%%
flowchart LR
  subgraph c1["1 Catalog snapshot"]
    direction TB
    s1["Daily on start, and<br/>before risky jobs"] --> s2["VACUUM INTO a temp file,<br/>then zip it"] --> s3[("backups/catalog-DATE-<br/>reason.zip, newest 10")]
  end
  subgraph c2["2 Backup set (a job)"]
    direction TB
    b1["Folder by folder: same size<br/>and date? Moved: rename inside"] --> b3["New or changed: old copy to<br/>previous-versions; copy, re-read"] --> b4[("backup_files:<br/>SHA-256")]
  end
  subgraph c3["3 End and later"]
    direction TB
    b5["End: catalog snapshot,<br/>sidecars, backup.json"] --> v["Verify job re-reads<br/>every copy"] --> r["Restore: files back;<br/>catalog at next start"]
  end
  c1 --> c2 --> c3
```

| Step | From | To | Data | Stored in |
|:--|:--|:--|:--|:--|
| 1 | Catalog | Snapshot folder | A consistent copy made while the catalog stays open, zipped. Newest `catalog_backups_keep` (10) kept | `backups/` |
| 2 | Library | Backup drive | New or changed files, hashed while read, re-read, compared. A mirror of each source's folders | `<backup>/<id>-<name>/` |
| 3 | Backup drive | Backup drive | A changed file's old copy moves to `_Lunelis/previous-versions/<time>/`. Nothing is deleted from a backup | Backup drive |
| 4 | Job runner | Catalog | Size, time, SHA-256 per copy. The first backup fills `files.content_hash`. At the end: a snapshot (5 kept), sidecars, `backup.json` | `backup_files`, `_Lunelis/` |
| 5 | Backup drive | Library | Restore: missing or damaged files come back (a damaged one is quarantined first). The catalog returns at the next start | `restore-pending.json` |
| 6 | Job runner | Catalog | Verify job re-reads each copy against its hash. A missing drive makes the job wait; a full one pauses it | `backup_files.problem` |

::: warn Snapshots and restores
- A snapshot is taken before every job that moves or removes files, and before every schema upgrade (kept, never pruned).
- Picks, events, albums, faces and edits live only in the catalog, so snapshots protect them.
:::

# The darktable bridge

```mermaid darktable bridge. Two TSV files in one exchange folder; the newest change wins. XMP sidecars are a second channel.
%%{init: {"flowchart": {"rankSpacing": 22, "nodeSpacing": 18, "padding": 8}}}%%
flowchart LR
  C1[("Lunelis<br/>ratings")] -- "when a rating<br/>changed" --> T1["lunelis-ratings.tsv<br/>every rating"] --> L["lunelis.lua in darktable:<br/>applies newer rows"] --> D1["darktable<br/>library"]
  D2["darktable edits<br/>a rating"] --> T2["darktable-ratings.tsv<br/>changed rows"] -- "every 20 s: apply if<br/>newer, delete file" --> C2[("Lunelis<br/>ratings")]
  X["XMP sidecars"] <-.-> D3["darktable"]
```

| Step | From | To | Data | Stored in |
|:--|:--|:--|:--|:--|
| 1 | You | darktable | Install copies `lunelis.lua` into darktable's `lua` folder and adds one `require` line to `luarc` | `%LOCALAPPDATA%\darktable\` |
| 2 | Catalog | Exchange file | Every rating: full path, stars or -1 for reject, label, UTC change time | `lunelis-ratings.tsv` |
| 3 | Exchange file | darktable | The plugin applies only rows newer than its `last_applied` preference | darktable's library |
| 4 | darktable | Exchange file | Rows changed since its last snapshot | `darktable-ratings.tsv` |
| 5 | Exchange file | Catalog | Matched by full path, case-insensitive. A row older than the catalog's change is skipped | `ratings` |
| 6 | Catalog | Sidecar | An applied change queues a sidecar write like any rating | See the sidecar chapter |

The exchange folder is `darktable/` in the data folder unless you set another. The check runs on the UI thread every 20 seconds and is cheap when nothing changed. Picks never cross, because darktable has none. darktable may hold several colour labels; Lunelis keeps one and never removes the extras.

# Models and recognition

```mermaid Recognition. Everything runs on the CPU in this process. Each path reads different pixels, and only names you confirm become tags.
%%{init: {"flowchart": {"rankSpacing": 22, "nodeSpacing": 18, "padding": 8}}}%%
flowchart LR
  D["Download, you confirm;<br/>SHA-256 checked"] --> M[("data folder, models/")]
  T["512 px thumbnails"] --> C["CLIP image model"] --> E[("embeddings,<br/>512 numbers")] --> S["Scene suggestions<br/>with a confidence"]
  O["Original, resized<br/>to 1600 px"] --> F["YuNet finds, SFace<br/>makes 128 numbers"] --> FA[("faces, crops")] --> PT["You confirm a name:<br/>People tag"]
  G["GPS or a<br/>map pin"] --> PL["Nearest place in<br/>the built-in list"] --> PT2["Places tag"]
```

| Step | From | To | Data | Stored in |
|:--|:--|:--|:--|:--|
| 1 | Internet | Models folder | You confirm. Each file is checked against its SHA-256 and size, then moved into place | `models/` |
| 2 | Cache | CLIP | Cached thumbnails only, 16 at a time: never a RAW, never the NAS. Scored against 40 label phrases: 25 % or more, 3 per photo at most | `embeddings`, `file_tags` |
| 3 | Original | YuNet, SFace | Upright copy, 1600 px at most. A face needs score 0.8 and 2.5 % of the long edge. Videos skipped | Memory |
| 4 | YuNet, SFace | Catalog | Box as fractions, 128-number fingerprint, model name, 160 px crop. Each photo once per model | `faces`, `cache/faces/` |
| 5 | Catalog | Catalog | Suggested to a named person at cosine 0.40, grouped with look-alikes at 0.45. Only a face you confirm tags the photo | `faces`, `file_tags` |
| 6 | Catalog | Catalog | Nearest of 31,735 listed towns: named within 40 km, country only within 250 km. A pin beats GPS | `photo_places`, `locations` |
| 7 | Original | Mask maker | Subject and sky masks, made on the upright source and cached as PNG. Ask compares your words with stored fingerprints | `cache/masks/` |

::: warn Recognition rules that must not regress
- Everything that understands pixels goes through the `Recognizer` interface, so it can move to an external service later.
- Suggestions carry a confidence; every normal tag query filters `confidence IS NULL`. Only manual tags reach XMP; face data never does.
:::

# Network use

```mermaid Network use. Four outbound hosts, each started by a setting or a click, and one inbound path that stays on the home network.
%%{init: {"flowchart": {"rankSpacing": 40, "nodeSpacing": 14, "padding": 8}}}%%
flowchart LR
  lan(["Home network<br/>device"]) -- "album link + key" --> app["<b>Lunelis</b>"]
  app -- "daily, packaged build" --> a["api.github.com: newest release"]
  app -- "you press Update" --> b["github.com: zip + .sha256"]
  app -- "you confirm" --> c["github.com, huggingface.co:<br/>model files"]
  app -- "online map on" --> d["tile.openstreetmap.org: tiles"]
```

| Destination | Why | When | What is sent | Default |
|:--|:--|:--|:--|:--|
| api.github.com | Is a newer release out? | 8 s after start if packaged and the last check is over 20 h old, or the Check button | HTTPS GET, internet address, `User-Agent: Lunelis-updater` | On (`update_check`) |
| github.com (release files) | Update zip and `.sha256` | Only after you choose to install | HTTPS GET, same headers | Off until you click |
| github.com (OpenCV models, rembg) | Face and subject-mask models | After you confirm: Settings, Edit panel, installer, Welcome | HTTPS GET, `User-Agent: Lunelis` | Off |
| huggingface.co | CLIP scene model (4 files), sky mask | Same as above | HTTPS GET, `User-Agent: Lunelis` | Off |
| tile.openstreetmap.org | Map pictures | While the Map shows tiles and `map_online` is on. Each tile fetched once, kept in `map_tiles` | Zoom, column, row in the URL; `User-Agent: Lunelis/<version>` | Off |
| www.openstreetmap.org (your browser) | Show a photo's place | Only if "location opens" is set to the browser, and you click | The coordinates, in a URL your browser opens | Off |
| Home network, port 8735 (in) | Family gallery | While at least one album is shared | 1600 px JPEGs without EXIF; originals if allowed | Off until shared |

A search of `src/lunelis` for `urllib`, `QNetworkAccessManager`, `socket` and `http` finds only the files above. There is no account, no telemetry and no crash upload; Help > Report a problem shows text for you to copy.

::: note What comes down is checked, but only so far
- Model downloads are hashed on arrival and deleted on a mismatch. Existing model files are later accepted by size only.
- An update needs a matching `.sha256`. It comes from the same release as the zip, so it shows the download is intact, not who made it. The build is unsigned.
- Map tiles are decoded as images before saving; they are not hashed.
:::

::: warn Family gallery
Plain HTTP on all addresses of the PC (`0.0.0.0`). A request from outside the home ranges is refused first. Then: a random key per album, an optional PIN (PBKDF2, 200,000 rounds), 5 wrong PINs lock an address for 10 minutes, 20 lock an album for an hour, at most 32 connections, 1600 px copies with no EXIF. Home-network traffic is not encrypted.
:::

# Data inventory

| Data | Where it lives | Kept for | Leaves the PC? |
|:--|:--|:--|:--|
| Photos and videos | Your source folders | Until you remove them | Only by your action: export, import, backup, shared album |
| Catalog: ratings, picks, tags, albums, events, faces, settings, EXIF | `catalog.db` in the data folder | For ever | No. In snapshots and backup sets |
| Edit stacks, virtual copies | `edits`, `copies`; `lunelis:EditStack` in sidecars | For ever | Sidecar text goes into backups |
| Thumbnails, edit previews, masks | `cache/` | Until cleared | No |
| Sidecars | `sidecars/` (central) or beside the photo | For ever | In backup sets |
| Face fingerprints, names, crops | `faces`, `people`, `cache/faces` | For ever | No. In snapshots, so a shared snapshot shares them |
| Scene fingerprints, suggestions | `embeddings`, `file_tags` | For ever | No |
| Place tags, pins | `photo_places`, `locations` | For ever | Tags in sidecar text; pins no |
| Models | `models/` | Until removed | They come in only |
| Catalog snapshots | `backups/*.zip` or your folder | Newest 10; upgrade snapshots never pruned | In backup sets (5 kept) |
| Set-aside files | `_Lunelis Quarantine`, or Lunelis folder Duplicates and Trash | Until you empty them | No |
| Staging, update files | `staging/`, `updates/` | Until placed or cleaned | The update zip comes in |
| Map tiles | `map_tiles/` | For ever | They come in only |
| Gallery copies | `gallery_cache/` | Until cleared | Served on the home network |
| Log | `logs/lunelis.log`, 5 files of 2 MB | Rotated | No. May name folders and files |
| Location and set-up records | `location.json`, `setup.json`, Windows Run and Uninstall keys | Until changed | No |

# Failure paths

| What fails | What the program does | What the user sees |
|:--|:--|:--|
| NAS or drive offline | The scan refuses the source. Metadata and thumbnail passes leave its files pending. Jobs go to `waiting` and retry every 60 s. An import waits | "Folder unavailable"; photos marked OFFLINE (checked every 60 s); "Waiting for ... to come back online" |
| Staging disk full | The import pauses; no file is half placed | "Out of staging space" |
| Backup drive full or removed | The job pauses or waits. Nothing is deleted from the backup | The set's status line says why |
| Power cut or crash | Scans keep committed batches. A job resumes at its first pending folder; an import from `import_items`; a migration at its last safe state | "Resuming after an interruption" |
| Catalog damaged or empty | Start-up will not open it, and never starts silently empty | A box: restore the newest snapshot (bad catalog kept beside it), start empty, or quit |
| Catalog from a newer version | Refused, untouched | A message to install the newest Lunelis |
| `location.json` unreadable | The default data folder is used for now; nothing moves | A warning naming the file |
| A sidecar cannot be written | The change stays pending in the catalog and is retried (60 s; 5 min after an error) | "Couldn't write N sidecar(s) - kept in the catalog" |
| A photo cannot be decoded | `thumb_error` or `read_error` is stored; skipped until it changes | A placeholder tile; a row in Damaged files |
| A model is missing | The step is skipped silently; Ask falls back to word search | A "Download and turn on" button in Settings |
| A download fails its hash | The partial file is deleted; nothing is installed | "Didn't match its checksum - nothing was installed" |
| The update cannot be swapped in | The script puts the old folder back and starts it | A warning on the next start |
| One library-pass step errors | It is logged; the other steps still run | "Finished, but these stopped on an error: ..." |

# To check

- **Start-up scan.** No timer or start-up call that starts a scan was found in `ui/main_window.py`. Scans start from Add folder, F5, Library status, Settings, an import and the migration wizard. Confirm this is intended.
- **Redirect hosts.** The code names `github.com` and `huggingface.co`. The final download servers after their redirects are not in the code.
- **Model files at rest.** `available()` in `recognize/clip.py` and `recognize/faces.py` accepts a file of the right size without re-hashing it.
- **Update trust.** The `.sha256` and the zip come from the same release. Whether signing is planned is not stated in the code.
- **Gallery exposure.** The server binds to all addresses and checks the client address. How Windows Firewall treats it was not examined. PIN and cookie travel unencrypted.
- **Pending flag race.** After a sidecar write, `xmp_pending` is cleared when stars, flag and label still match (`xmp/sync.py`). A tag or edit saved during that write is not compared. Not tested.
- **RAW decode in the photo view.** Preloading a neighbour that is an edited RAW with no cached proxy decodes it at half size (`ui/detail_view.py`). That is outside the grid and filmstrip, but it is a RAW decode while paging.
- **Proxy settings.** Whether `QNetworkAccessManager` follows the Windows proxy settings was not examined.
- **darktable plugin.** `CLAUDE.md` says the Lua script was tested only under a Lua 5.4 stand-in, never in a real darktable. Not re-checked here.
- **Outside the program.** The Inno Setup installer and the CI workflows are not in `src/lunelis` and are not covered.
- **Speeds.** Rates quoted in `CLAUDE.md` were measured on its author's library and not re-measured.

# Discrepancies

| Where | The documents say | The code does |
|:--|:--|:--|
| `docs/_tools/architecture.mmd` | "SQLite schema v37" | `MIGRATIONS` ends at version 44 |
| `architecture.mmd` | The jobs engine runs "dust maps"; one box holds "Import + Autopilot, migrate, photo backups" | No dust job kind (`dust_view.py` uses a page loader). Import and autopilot have their own threads; migrate and backups are job kinds |
| `CLAUDE.md`, Architecture | Edits are "an instruction stack in XMP" | The stack is written to the sidecar but never read back: `import_sidecars` ignores `lunelis:EditStack`, and nothing reads the central store. Edits survive a catalog loss only through snapshots and backups |
| `CLAUDE.md`, thumbnail and metadata rules | Video thumbnails "not yet"; CR3 and video have no EXIF reader | Videos get a poster-frame thumbnail (PyAV) and a metadata probe. Only CR3 still has no reader |
| `CLAUDE.md`, thumbnail rules | Cache path `<id//1000>/<id>.jpg` | `cache_rel_path` zero-pads to four digits: `0012/12819.jpg` |
| `CLAUDE.md`, scanner rules | A change nulls `content_hash`, `perceptual_hash`, `thumbnail_path` | The update also clears `sample_hash` and `thumb_error` |
| `CLAUDE.md`, non-negotiables | "Lunelis's own files never go in the photo library" | Each source can hold `_Lunelis Quarantine`. Temp files (`.lunelis-*`) sit in the target folder while written. A Lunelis folder you choose can hold exports, trash and logs |
| `CLAUDE.md`, backup rule | A snapshot is taken before every job that moves or removes files | True, but Duplicates, Near-duplicates and Quarantine snapshot into `paths.BACKUP_DIR` (`data/backups`), not the folder in Settings that daily snapshots use. Start-up recovery also looks only in `data/backups` |
| `geo/places.py` docstring | About 34,000 places | The list holds 31,735 rows |
| `CLAUDE.md`, Quarantine rules | `empty()` is the only hard removal | Clear the card (`importing/ingest.py`) also deletes, from the card, after a double hash check and your confirmation |
