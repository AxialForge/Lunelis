---
type: dataflow
title: Lunelis Data Flow
subtitle: Where every photo, setting and byte comes from, goes and stays
audience: Maintainers, reviewers and anyone judging privacy
sources: CLAUDE.md and src/lunelis (catalog, importers, raw, xmp, importing, edit, dupes, migrate, backups, jobs, recognize, geo, updater, gallery, darktable)
status: Draft for review (pilot of the documentation kit)
---

# The big picture

Lunelis is one Python program on one PC. It indexes photos where they already sit. It keeps its own records in a **data folder** and a single SQLite **catalog**. This document follows the data: what is read, what is written, where it is kept, and the few places where anything leaves the PC.

::: stats
44 | schema migrations
9 | background job kinds
4 | internet hosts the app contacts
28 | file types cataloged
:::

```mermaid System context. Photos stay on their disks, Lunelis keeps its records in the data folder, and the internet is optional and outbound only.
flowchart TD
  user([You]) --> app["<b>Lunelis</b><br/>one process"]
  app <--> data[("<b>Data folder</b><br/>catalog, caches, sidecars,<br/>snapshots, models")]
  app -- "read; write only what<br/>you ask for" --> photos["<b>Photo sources</b><br/>local, USB, NAS"]
  cards["Memory cards"] -- "copy in, verified" --> app
  app -- "copy out, verified" --> backups["Backup drives"]
  app <-. "ratings and<br/>XMP sidecars" .-> dt["darktable"]
  lan(["Phones and TVs<br/>on the home network"]) -. "shared albums only" .-> app
  app -. "opt-in or daily,<br/>outbound only" .-> net(("Internet<br/>GitHub, Hugging Face,<br/>OpenStreetMap"))
```

## Three things to hold on to

- **Originals are read, not changed.** Lunelis writes into photo folders in only a few cases: an XMP sidecar next to a photo, files you import, files a migration copies, and renames into a `_Lunelis Quarantine` folder. Each case is a flow chapter below.
- **Everything else lives in the data folder.** The catalog, thumbnails, edit previews, models, snapshots and the central sidecar store are never placed in a photo folder or the program folder (`paths.py`).
- **Nothing about a photo is sent anywhere.** The program contacts four hosts, all outbound. They receive an internet address and a request, never a photo, a name or a location.

The **library pass** is the chain that keeps the catalog current. One worker thread runs it in this order (`LibraryWorker.run` in `ui/main_window.py`): scan, find moved files, read sidecars, read metadata, Google Takeout dates, burst stacks and timelapses, search index, find moved files by content, thumbnails, near-duplicate fingerprints, scene tags, faces, places, suggestions, damage check. A step that fails is logged and the rest still run.

# Threads and trust boundaries

Lunelis is one operating-system process. Work is split across threads so the window never waits for a disk. SQLite connections cannot cross threads, so every worker opens its own connection to the same catalog file (`open_catalog` in `catalog/schema.py`, WAL mode, 20 second lock wait).

```mermaid Four layers inside the one process. Each background thread has its own catalog connection; the pools hold no connection and only read files or cached images.
flowchart TB
  subgraph L1["Layer 1 - UI thread (Qt)"]
    ui["Window, grid, photo view, Edit panel, pages<br/>its own catalog connection"]
  end
  subgraph L2["Layer 2 - background threads, one catalog connection each"]
    direction LR
    lib["Library<br/>worker"]
    jobs["Job<br/>runner"]
    xmp["Sidecar<br/>writer"]
    imp["Import<br/>worker"]
    oth["Export, merge,<br/>autopilot, page loaders"]
  end
  subgraph L3["Layer 3 - pools that only read files and images"]
    pools["Thumbnail loaders 4 - preview loaders 2<br/>render 1 - edit output 1 - file readers 8"]
  end
  subgraph L4["Layer 4 - storage"]
    direction LR
    db[("catalog.db<br/>WAL")]
    cache["cache/"]
    src["Photo sources"]
  end
  ui --> L2
  ui --> pools
  L2 --> db
  L2 --> src
  pools --> cache
  pools --> src
```

| Worker | Runs as | What it does |
|:--|:--|:--|
| Library worker | One `QThread`, started per pass | The library pass above. File readers inside a step run 8 at a time (`WORKERS = 8` in metadata, thumbnails and sidecar sync) |
| Job runner | One `QThread` for the whole session | Takes queued jobs one at a time. The 9 job kinds are listed in `jobs/engine.py` |
| Sidecar writer | A `QThread` per write | `export_pending`, started 1.2 s after the last change |
| Import worker | A `QThread` per import | Stage, verify and place card files (`ui/import_view.py`) |
| Thumbnail loaders | `QThreadPool`, 4 threads | Decode cached JPEGs for visible tiles. Cache of 1,500 pixmaps |
| Photo view and editor | Pools of 2, 1 and 1 threads | Preview decode, live render, edit outputs (written in order) |
| Family gallery | One server thread plus request threads | Only while an album is shared. One catalog connection per request |
| Page loaders | `Background` helper (`ui/background.py`) | Slow page reads, one run per key, own connection |

::: note Background work gives way to video
Video frames reach the screen through the UI thread. Every per-file worker calls `pace.breathe()`, which waits while a clip plays (at most 600 seconds). Without it, playback fell to about one frame a second (`pace.py`).
:::

## Trust boundaries

| Boundary | What crosses it | Guard in the code |
|:--|:--|:--|
| You and the window | Keys, clicks, dialogs | Confirm before rating more than 500 photos, before downloads, before emptying quarantine |
| App and photo sources | Reads of originals. Writes: sidecars, imports, migration copies, quarantine renames | Read-only opens, temp name then replace, hash re-read, never rename or overwrite |
| App and data folder | Catalog, caches, models, snapshots | `paths.py` refuses a network path for the data folder |
| App and memory cards | Reads and hashes. One user-triggered delete | Clear the card re-hashes both copies first |
| App and backup drives | Copies out, reads back | Volume serial, hash on write and verify, nothing deleted |
| App and other programs | XMP text, exchange TSV files, darktable `luarc`, Takeout JSON | Surgical text edits, newest change wins |
| App to internet | Update check, downloads, map tiles | HTTPS, SHA-256 pins, opt-in, no photo data |
| Home network to app | Gallery requests | Home addresses only, key per album, PIN, rate limits |
| Installer, updater and program folder | `setup.json`, release zip, swap script | `.sha256` match, no `..` in the zip, old version restored on failure |
| Models and the process | ONNX files loaded into the program | SHA-256 checked at download only |

# Scanning folders into the catalog

::: strip
Where: Library pass, step 1 (`importers/scan.py`)
For: Every source folder
Answers: How do files get into the catalog?
:::

The scan makes the `files` table match the disk. It reads directory listings only: names, sizes and modified times. It never opens a photo.

```mermaid Scan. An offline source is refused rather than scanned as empty, and only a complete walk can flag files missing.
flowchart TD
  A["Add folder, F5, end of an import"] --> B{"Source<br/>reachable?"}
  B -- no --> X["Stop with a message.<br/>Nothing is marked missing"]
  B -- yes --> C["Walk with scandir,<br/>one listing per folder"]
  C --> D["Compare with files rows by<br/>source + relative path"]
  D --> E[("files, roots")]
  D --> F{"Walk<br/>complete?"}
  F -- yes --> G["Flag unseen files<br/>missing_since"]
  F -- "cancelled or<br/>unreadable folder" --> H["Flag nothing under it"]
  G --> E
  E --> I["Re-link moved files<br/>size + date"]
```

| Step | From | To | Data | Stored in |
|:--|:--|:--|:--|:--|
| 1 | You | Library worker | List of source ids. One pass at a time | Memory |
| 2 | Library worker | Source folder | A folder-exists check. A missing one raises `RootUnavailable` | Nothing |
| 3 | Source folder | Library worker | Name, size, time, XMP names. Hidden, system and link entries skipped | Memory |
| 4 | Library worker | Catalog | New file: insert. Size or time changed: update and clear hashes and thumbnail. Back again: clear the missing flag | `files` |
| 5 | Library worker | Catalog | Commit every 2,000 rows, so a stopped scan keeps its progress | `files` |
| 6 | Library worker | Catalog | After a complete walk: unseen files get `missing_since` | `files`, `roots` |
| 7 | Library worker | Catalog | A missing row takes over a new file with the same size and time | `files`, `file_moves` |

::: warn Scan rules that must not regress
- The scanner never deletes a `files` row. A file that is gone keeps its ratings, albums and faces.
- An unreachable source is never scanned as empty. A cancelled scan, or an unreadable subfolder, marks nothing missing under it.
- Sources cannot nest inside each other (`RootOverlap`). Each file has exactly one source and relative path, always with forward slashes.
- Quarantined files and the `_Lunelis Quarantine` folders are skipped, so set-aside copies are never flagged missing.
:::

# Reading metadata and sidecars

::: strip
Where: Library pass, steps 2 to 4 (`xmp/sync.py`, `importers/metadata.py`)
For: New or changed files and sidecars
Answers: How do dates, camera data, ratings and tags get into the catalog?
:::

```mermaid Three readers feed the catalog. All of them open originals and sidecars read-only, 8 files at a time, and write to the catalog from one thread.
flowchart TD
  S["XMP sidecar<br/>next to the photo"] --> P["Parse stars, reject,<br/>label, tags"]
  P --> R[("ratings, file_tags")]
  F["Photo or video<br/>read-only"] --> X["Sniff real format, then<br/>exifread or PyAV"]
  X --> E[("exif, files.format")]
  J["Takeout JSON<br/>Takeout sources only"] --> T["Fill a missing date<br/>or GPS only"]
  T --> E
```

| Step | From | To | Data | Stored in |
|:--|:--|:--|:--|:--|
| 1 | Catalog | Sidecar reader | Files whose sidecar time differs from the last synced time. Skipped while a Lunelis change is waiting to be written | Memory |
| 2 | Sidecar file | Library worker | `xmp:Rating` (-1 = reject), label, tags. `lunelis:EditStack` is parsed but never applied | Memory |
| 3 | Library worker | Catalog | Rating row only if one exists or the values are not default. Tags are only added, never removed | `ratings`, `file_tags`, `files.sidecar_synced_mtime` |
| 4 | Catalog | Metadata reader | Files with no `exif` row, or a row older than the file | Memory |
| 5 | Original file | Metadata reader | The first bytes (real format), then EXIF. Maker notes only for real camera RAW. Videos through PyAV | Memory |
| 6 | Library worker | Catalog | Fields plus the whole EXIF as zlib JSON. Format and RAW flag corrected from the bytes. Batches of 200 | `exif`, `files` |
| 7 | Library worker | Catalog | A failed read stores `read_error` and is not retried until the file changes. An offline share leaves files pending | `exif.read_error` |
| 8 | Takeout JSON | Catalog | Date and GPS where the file has none. Never overrides EXIF | `takeout_meta`, `exif` |

::: warn Metadata rules that must not regress
- The extension is a guess. Anything that decodes a file asks `files.format`, which comes from the bytes (`importers/formats.py`).
- Do not switch the EXIF reader to pyexiv2. It cannot open non-ASCII paths on Windows. exifread reads through a Python file handle.
- Maker notes are never decoded on non-RAW files. A Takeout-rewritten JPEG once stalled a run for minutes.
:::

# Thumbnails and previews to the grid

::: strip
Where: Library pass, step 7, and the Library page
For: Every live file, then every screen of tiles
Answers: What does the grid actually read while you scroll?
:::

```mermaid Thumbnails are made once, in the background. The grid only ever loads the small cached JPEGs.
flowchart TD
  subgraph make["Thumbnail pass - library worker, 8 threads"]
    o["Original on disk or NAS"] --> p["Cheapest preview by real format:<br/>embedded RAW JPEG, camera preview,<br/>HEIC, video poster frame"]
    p --> j["512 px upright sRGB JPEG, quality 80"]
  end
  j --> c[("cache/thumbnails/0012/12819.jpg<br/>files.thumbnail_path")]
  subgraph show["Grid - UI thread"]
    i["LibraryIndex: all rows in memory"] --> g["PhotoGrid paints visible tiles only"]
    g -- "miss: draw placeholder" --> t["ThumbCache: 4 threads, Pillow"]
    t --> g
  end
  c --> t
  g -. "open a photo" .-> v["Photo view: 2560 px preview, 2 threads"]
```

| Step | From | To | Data | Stored in |
|:--|:--|:--|:--|:--|
| 1 | Catalog | Thumbnail pass | Live files with no thumbnail and no `thumb_error` | Memory |
| 2 | Original file | Thumbnail pass | Smallest embedded JPEG of 512 px or more, or the camera JPEG preview, or a decode with a reduced size | Memory |
| 3 | Thumbnail pass | Cache folder | Upright sRGB JPEG, 512 px long edge, written to a temp name then replaced | `cache/thumbnails/` |
| 4 | Thumbnail pass | Catalog | Path, error text, a 64-bit fingerprint (dHash) taken from the picture in memory | `files` |
| 5 | Catalog | Grid | One query loads every visible row as plain tuples. Stacks collapse in Python | Memory only |
| 6 | Cache folder | Grid | A visible tile with no pixmap queues a load. Pillow decodes, the UI thread converts | Memory (1,500 pixmaps) |
| 7 | Original file | Photo view | The biggest embedded preview, up to 2560 px. A full RAW decode only when you zoom past it | Memory |

::: important No RAW decode in the scroll path
The grid and the filmstrip only ever load cached JPEG thumbnails. Paint never touches the disk or the catalog. A RAW is decoded only on demand: in the editor, on an export, or when you zoom past the embedded preview in the photo view.
:::

Deleting the thumbnail folder makes the next pass rebuild it. Deleting single files does not (`forget_purged_cache`). A file that cannot be thumbnailed gets `thumb_error` and is not retried until the scanner sees it change.

# Ratings and tags to XMP sidecars

::: strip
Where: Every rating key, tag change and edit save
For: Anyone using darktable or Lightroom beside Lunelis
Answers: When and where does a sidecar get written?
:::

```mermaid Sidecar export. One setting picks where the file goes, and an existing sidecar can be kept in step as well.
flowchart TD
  A["Rating, flag, label,<br/>tag or edit saved"] --> B[("ratings.xmp_pending = 1")]
  B -- "1.2 s after the last change,<br/>or when closing" --> C["Sidecar writer thread"]
  C --> D{"sidecar_mode"}
  D -- "central (default)" --> E["data folder, sidecars/<br/>mirror of the photo folders"]
  D -- beside --> F["name.EXT.xmp<br/>next to the photo"]
  D -- catalog --> G["Nothing written"]
  C -- "existing sidecar,<br/>update on" --> H["Kept in step,<br/>never created"]
  E --> I["Clear pending only if the<br/>rating is still what was written"]
  F --> I
  H --> I
```

| Step | From | To | Data | Stored in |
|:--|:--|:--|:--|:--|
| 1 | You | Catalog | Stars 0 to 5, pick or reject, colour label, tag, or edit stack | `ratings`, `file_tags`, `edits` |
| 2 | Catalog | Sidecar writer | Every row with `xmp_pending = 1`: stars, flag, label, manual tags, edit text | Memory |
| 3 | Sidecar writer | Central store | `sidecars/<id>-<source name>/<folder>/<file>.EXT.xmp` | Data folder |
| 4 | Sidecar writer | Photo folder | Only in `beside` mode, or when a sidecar already exists and the update setting is on (default) | Photo folder |
| 5 | Sidecar writer | Sidecar file | Surgical text edit of changed fields only. Checked well-formed, then temp file and replace | XMP text |
| 6 | Sidecar writer | Catalog | Pending cleared if stars, flag and label still match. A failure keeps it pending with the error | `ratings.xmp_error` |
| 7 | Sidecar file | Catalog | Later outside edits come back at the next scan, as in the metadata chapter | `ratings` |

::: warn Sidecar rules that must not regress
- Never parse and re-serialize XMP. It renames darktable's namespace prefixes and drops the `xpacket` wrapper. Only the changed values are edited as text.
- Reject is `xmp:Rating = -1`. A pick has no XMP field and stays in the catalog; a pick-only change never creates a sidecar.
- Adobe's `crs:` develop settings are never written. The edit is `lunelis:EditStack` in Lunelis's own namespace.
- A Lunelis change that has not been written wins over the sidecar on disk.
:::

The default mode `central` keeps photo folders clean. Nothing in the code reads the central store back; it exists for other tools, for backups and for moving with its photo.

# Card import

::: strip
Where: Import page, tray pop-up
For: Memory cards, phones and USB sticks
Answers: How does a file get from a card into the library without loss?
:::

```mermaid Card import. Two hash checks guard the copy, and the staged copy is deleted only after the library copy verifies.
flowchart TD
  A["Card or folder"] --> B["Find media in the<br/>camera's folders"]
  B --> C{"Exact bytes already<br/>in library?"}
  C -- "yes (full SHA-256)" --> Z["Counted as safe,<br/>not copied"]
  C -- no --> D["Copy to staging while hashing"]
  D --> E{"Staged copy<br/>re-reads the same?"}
  E -- yes --> F["Template picks the FOLDER.<br/>File name never changes"]
  F --> G{"Library copy<br/>re-reads the same?"}
  G -- yes --> H["Delete staged copy"]
  H --> I["Rescan the destination"]
  I -. "only when you ask" .-> J["Clear the card"]
```

| Step | From | To | Data | Stored in |
|:--|:--|:--|:--|:--|
| 1 | Card | Catalog | A list of media, companions (Live Photo video) and sidecars, one row each | `imports`, `import_items` |
| 2 | Card | Staging | Each file copied to a `.lunelis-importing-*` temp name, hashed as it is read, flushed, then renamed | Data folder `staging/import-<id>/` |
| 3 | Staging | Staging | The staged copy is read again and compared with the card hash | `import_items.sha256` |
| 4 | Staging | Library folder | Folder from the storage template and the capture date. A different file with the same name goes to a sibling folder such as `... (2)` | Your destination folder |
| 5 | Library folder | Library worker | The new copy is read again and compared with the card hash | `import_items.state` |
| 6 | Library worker | Staging | Staged copy deleted only now. The import is done when nothing is pending | `imports.state` |
| 7 | You | Card | Clear the card re-checks size, time and both hashes, then deletes the card files. A file that changed is left | The card |

Staging is local while the disk keeps `import_local_reserve_gb` free (50 GB by default). After that it spills to the network staging folder if one is set, else the import waits. A pulled card or a sleeping share puts the import in `waiting`, and it resumes from `import_items`.

::: warn Import rules that must not regress
- Imported files are never renamed. The template chooses folders only.
- "Already in the library" and "Clear the card" compare the **full SHA-256**. A sampled hash is a candidate filter, never proof (audit LRA-001).
- Staging must be outside every source, or a partial import would be cataloged.
:::

# Edits, previews and export

::: strip
Where: Edit panel, photo view, Export dialog, Create tools
For: Anyone editing photos
Answers: Where does an edit live, and what files does it make?
:::

```mermaid Edits are instructions, not pixels. Only the editor writes the cached look, through one ordered thread.
flowchart TD
  A["Edit panel"] -- "live preview,<br/>1 render thread" --> V["Screen only"]
  A -- "700 ms after<br/>the last change" --> B[("edits.stack, rev + 1<br/>ratings.xmp_pending")]
  B --> C["Sidecar writer:<br/>lunelis:EditStack"]
  A -- "leaving the editor" --> D["Output thread, 1"]
  D --> E[("cache/edits proxy, 2560 px<br/>+ edited thumbnail")]
  E --> G["Grid and photo view<br/>show the edit"]
  X["Export or Create"] --> R["Render full size in strips"]
  R --> N["New file in the folder you pick.<br/>Never overwrites"]
```

| Step | From | To | Data | Stored in |
|:--|:--|:--|:--|:--|
| 1 | Original file | Edit panel | A RAW through rawpy at half size when enough, a JPEG at its own pixels. Float RGB, upright | Memory |
| 2 | Edit panel | Screen | Preview at 960 px or less while a slider moves; full size when you let go | Memory |
| 3 | Edit panel | Catalog | The stack as text: filter, amount, sliders, crop, curves, masks. Identity stack deletes the row | `edits` (and `copies` for virtual copies) |
| 4 | Catalog | Sidecar | `lunelis:EditStack` as an attribute, when sidecars are written (previous chapter) | XMP text |
| 5 | Edit panel | Cache | 2560 px JPEG (quality 90) and a new 512 px thumbnail with the edited look | `cache/edits/`, `cache/thumbnails/` |
| 6 | Catalog, original | Export | Full render in strips, sharpening, colour profile. EXIF rebuilt from the catalog: all, without location, or none | A new JPEG, TIFF or PNG |
| 7 | Edit panel | Cache | AI mask maps, made from the upright source and kept as PNG | `cache/masks/` |

Resetting an edit re-makes the plain thumbnail first and then deletes the proxy. If the original cannot be read, the edited look stays rather than leaving a blank tile.

::: warn Editing rules that must not regress
- The source file is never overwritten. An export is always a new file ("name (2).jpg" if the name is taken).
- Only the edit controller writes proxies and thumbnails. A late background write once put an edited look back on a Reset photo.
- Never run `apply` at full size for an export. `apply_tiled` works in strips; 60 MP in one piece needs several GB.
:::

# Duplicates and quarantine

::: strip
Where: Duplicates page, Quarantine page, Library status
For: Libraries with copies on several disks
Answers: How are copies found, and what happens to the extras?
:::

```mermaid Duplicates. A sampled hash is a hint; only a full hash makes a group exact, and only exact groups can be set aside.
flowchart TD
  A["You start a job"] --> B[("jobs, job_folders")]
  B --> C["Job runner: one folder at a time"]
  C --> D["Sample hash: size + 3 slices of 64 KB"]
  D --> E[("sample_hash<br/>groups: sampled")]
  E --> F["Verify job: full SHA-256"]
  F --> G[("groups: exact, verified")]
  G --> H["You set extras aside"]
  H --> I["Snapshot, merge stars and tags<br/>into the keeper, rename"]
  I --> J["Quarantine page:<br/>Restore or Empty"]
```

| Step | From | To | Data | Stored in |
|:--|:--|:--|:--|:--|
| 1 | You | Catalog | A scope of sources or folders, a schedule (now, idle, night window), a speed limit | `jobs`, `job_folders` |
| 2 | Original file | Job runner | About 200 KB per file, only for files that share a size and capture time with another file | `files.sample_hash` |
| 3 | Job runner | Catalog | A folder is marked done only after its work is committed. A restart resumes at the first pending folder | `duplicate_groups` |
| 4 | Original file | Job runner | Verify job: every byte, SHA-256 | `files.content_hash` |
| 5 | You | Catalog | Before the first move of a batch: a catalog snapshot | `backups/` zip |
| 6 | Catalog | Catalog | Stars, labels, tags, albums and sidecar of the extra go to the kept copy | `ratings`, `file_tags`, `album_files` |
| 7 | Source folder | `_Lunelis Quarantine` | A rename on the same drive (a copy, compare, remove across drives). The sidecar goes with it | `files.quarantined_at` |
| 8 | You | Recycle Bin or disk | Empty: the kept copy must verify on disk first. Local files to the Recycle Bin, network files removed after an extra tick | `purged`; catalog row deleted |

Near-duplicates are a separate path. The thumbnail pass saves a 64-bit fingerprint, and `dupes/similar.py` groups photos by complete linkage. Pairs must be within 4 bits, not RAW, the same moment, related names and the same shape. Groups are method `similar`.

::: warn Duplicate rules that must not regress
- Only verified byte-identical groups can be set aside, and never down to zero live copies.
- A job never hard-deletes. Emptying the quarantine is the one removal of photos and it is a user action with its own snapshot.
- The sampled hash is a candidate filter, never proof of identity.
:::

# Migration

::: strip
Where: Migrate page and wizard
For: Moving or consolidating a library into one target
Answers: How do files move without losing a rating, a copy or a name?
:::

```mermaid Migration. The same catalog row is repointed to the verified copy, so ratings, tags and thumbnails follow the file.
flowchart TD
  A["Plan: a dry run.<br/>Reads the catalog only"] --> B[("migrations,<br/>migration_items")]
  B --> C["Start: snapshot, before.csv,<br/>add target as a source"]
  C --> D["Copy to temp name,<br/>hash while reading"]
  D --> E{"Copy re-reads<br/>the same?"}
  E -- yes --> F["Sidecar copied,<br/>compared byte for byte"]
  F --> G["Repoint the SAME row,<br/>move central sidecar"]
  G --> H{"Keep originals<br/>to review?"}
  H -- yes --> I["state kept"]
  H -- no --> J["Original renamed into<br/>a set-aside folder"]
  I --> K["Finish: merge duplicates,<br/>manifest, after.csv, report"]
  J --> K
```

| Step | From | To | Data | Stored in |
|:--|:--|:--|:--|:--|
| 1 | Catalog | Plan | Per file: move, skip duplicate (an exact twin moves), or skip damaged (an intact copy exists). Folder from the template | `migration_items` |
| 2 | You | Catalog | Start: snapshot, then `before.csv` listing every file in every source | Logs folder |
| 3 | Source file | Target | Copy to `.lunelis-migrating-*`, hash while reading, re-read and compare. A name clash goes to a sibling folder | Target folder |
| 4 | Source sidecar | Target | Copied beside the photo and compared byte for byte | Target folder |
| 5 | Migration | Catalog | `root_id` and `rel_path` of the same row are repointed. The move is logged. State becomes `copied` | `files`, `file_moves` |
| 6 | Source file | Set-aside folder | State `done`: renamed to the Lunelis folder's Trash, or `_Lunelis Quarantine/migration-<id>` on its own drive. State `kept` in review mode | Source drive |
| 7 | Catalog | Logs folder | At the end: `manifest.csv` (SHA-256 of each copy), `after.csv`, and an accounted-for report | `Migration logs/` |

A crash between any two steps is safe. Before step 5 the source is untouched. After it the item says `copied` and a resume only finishes the original. In review mode the scanner skips the kept originals, or the next rescan would catalog them again.

::: warn Migration rules that must not regress
- A plan changes nothing on disk. Nothing moves until you start it.
- Never delete, overwrite or rename. Originals are released only when the accounted-for report finds nothing missing.
- Probable copies are planned to the same destination, so a mirrored library does not fill "(2)" folders.
:::

# Backups and catalog snapshots

::: strip
Where: Backups page, Settings, Library status
For: Anyone who wants a second copy of photos or the catalog
Answers: What is copied where, and how is it checked?
:::

```mermaid Two separate safety nets. Snapshots protect the catalog; backup sets protect photo files.
flowchart TD
  subgraph snap["Catalog snapshots - on this PC"]
    s1["Daily on start,<br/>and before risky jobs"] --> s2["VACUUM INTO a temp file,<br/>zip it"]
    s2 --> s3[("data folder, backups/<br/>catalog-DATE-reason.zip<br/>newest 10 kept")]
  end
  subgraph set["Backup set - a drive, stick or share"]
    b1["Job: folder by folder"] --> b2{"Same size and<br/>date in the set?"}
    b2 -- "moved in library" --> b3["Rename inside backup"]
    b2 -- "new or changed" --> b4["Old copy to previous-versions.<br/>Copy, re-read, compare"]
    b3 --> b5[("backup_files: SHA-256")]
    b4 --> b5
    b5 --> b6["End: catalog snapshot,<br/>sidecars, backup.json"]
  end
```

| Step | From | To | Data | Stored in |
|:--|:--|:--|:--|:--|
| 1 | Catalog | Snapshot folder | A consistent copy made while the catalog stays open, zipped. Newest `catalog_backups_keep` (10) kept | `backups/` |
| 2 | Library files | Backup drive | New or changed files, hashed while read, then re-read and compared. Mirror of each source's folders | `<backup>/<id>-<source name>/` |
| 3 | Backup drive | Backup drive | A changed file's old copy moves to `_Lunelis/previous-versions/<time>/`. Nothing is deleted from a backup | Backup drive |
| 4 | Job runner | Catalog | Size, time, SHA-256 and verify time of each copy. The first backup also fills `files.content_hash` | `backup_files` |
| 5 | Job runner | Backup drive | At the end: a catalog snapshot (5 kept there), the central sidecar store, `backup.json` | `_Lunelis/` |
| 6 | Backup drive | Library, catalog | Restore: missing or damaged files return (a damaged one is quarantined first). The catalog is restored at the next start | Library, `restore-pending.json` |
| 7 | Job runner | Backup drive | Verify job re-reads every copy against its stored hash to catch silent damage | `backup_files.problem` |

Removable destinations are remembered by volume serial, so a new drive letter is fine. A drive that is plugged in can start its set by itself (`auto_on_connect`). A missing drive makes the job wait. A full drive pauses it and the set says why.

::: warn Snapshots and restores
- A snapshot is taken before every job that moves or removes files, and before every schema upgrade (kept, never pruned).
- Picks, events, albums, faces and edits live only in the catalog, so the snapshot is their only protection.
- Restoring never swaps a catalog that is open. It is recorded and done at the next start, and the replaced catalog is copied aside.
:::

# The darktable bridge

::: strip
Where: Settings, Install the darktable plugin
For: People who rate in darktable and in Lunelis
Answers: How do ratings cross between the two programs?
:::

```mermaid darktable bridge. Two TSV files in one exchange folder; the newest change wins, and XMP sidecars are a second channel.
flowchart TD
  C[("Lunelis catalog:<br/>ratings")] -- "when any rating changed" --> T1["lunelis-ratings.tsv<br/>all ratings, path, time"]
  T1 --> L["lunelis.lua<br/>in darktable"]
  L -- "applies rows newer<br/>than its last run" --> D["darktable library"]
  D -- "diff against snapshot" --> T2["darktable-ratings.tsv<br/>changed rows only"]
  T2 -- "every 20 s: apply if newer,<br/>then delete the file" --> C
  X["XMP sidecars"] <-.-> D
```

| Step | From | To | Data | Stored in |
|:--|:--|:--|:--|:--|
| 1 | You | darktable config | Install copies `lunelis.lua` to darktable's `lua` folder and adds one `require` line to `luarc` | `%LOCALAPPDATA%\darktable\` |
| 2 | Catalog | Exchange folder | Every rating: full path, stars or -1 for reject, label, UTC change time | `lunelis-ratings.tsv` |
| 3 | Exchange file | darktable | The plugin applies only rows newer than its `last_applied` preference | darktable's library |
| 4 | darktable | Exchange folder | Rows changed since its last snapshot | `darktable-ratings.tsv` |
| 5 | Exchange file | Catalog | Matched by full path (case-insensitive). A row older than the catalog's change is kept out | `ratings` |
| 6 | Catalog | Sidecar | Applied changes queue a sidecar write like any rating | Sidecar export (see the ratings chapter) |

The exchange folder is `darktable/` in the data folder unless you set another. The check runs on the UI thread every 20 seconds and is cheap when nothing changed. Picks never cross: darktable has no picks. darktable may hold several colour labels; Lunelis keeps one and never removes the extras.

# Models and recognition

::: strip
Where: Settings, Faces, People, Tags, Map, Ask
For: Anyone using scene tags, faces, masks or place names
Answers: What runs on the PC, on which pixels, and where do the results go?
:::

```mermaid Recognition. Everything runs on the CPU in this process. Each path reads different pixels, and only names you confirm become tags.
flowchart TD
  M[("data folder, models/<br/>checked at download")] -.-> C
  M -.-> F
  T["512 px thumbnails"] --> C["CLIP: image to<br/>512 numbers"]
  C --> E[("embeddings")]
  E --> S["Scene suggestions:<br/>file_tags with confidence"]
  O["Original: RAW preview or JPEG,<br/>resized to 1600 px"] --> F["YuNet finds, SFace<br/>makes 128 numbers"]
  F --> FA[("faces, face_scans,<br/>crops in cache/faces")]
  FA -- "you confirm a name" --> PT["People tag"]
  G["GPS or a map pin"] --> PL["Nearest place in the<br/>built-in list"]
  PL --> PT2["Places tag"]
```

| Step | From | To | Data | Stored in |
|:--|:--|:--|:--|:--|
| 1 | Internet | Models folder | You confirm a download. Each file is checked against its SHA-256 and size, then moved into place | `models/clip`, `models/faces`, `models/*.onnx` |
| 2 | Cache folder | CLIP | Cached thumbnails only, 16 at a time. Never a RAW, never the NAS | `embeddings` (512 floats) |
| 3 | CLIP | Catalog | Compared with 40 label phrases. Suggested at 25 % or more, at most 3 per photo | `file_tags` with `confidence` |
| 4 | Original file | YuNet and SFace | An upright copy of at most 1600 px. A face must score 0.8 and be 2.5 % of the long edge. Videos skipped | Memory |
| 5 | YuNet and SFace | Catalog | Box as fractions, 128-number fingerprint, model name, a 160 px crop. Each photo is looked at once per model | `faces`, `face_scans`, `cache/faces/` |
| 6 | Catalog | Catalog | A new face is suggested to a named person at cosine 0.40 and grouped with look-alikes at 0.45 | `faces` |
| 7 | You | Catalog | Only a face you confirm tags the photo `People\|name` | `file_tags` |
| 8 | Catalog | Catalog | Place tag from the nearest of 31,735 listed towns: named within 40 km, country-only within 250 km. A pin beats GPS | `photo_places`, `locations` |

The masks (subject and sky) are made on the upright source and cached as PNG. They are used only through `ai.maps_for`, and a missing map means no effect, never a wrong one. The Ask box matches your sentence against the stored picture fingerprints and falls back to plain word search when the scene model is missing.

::: warn Recognition rules that must not regress
- Everything that reads pixels to understand them goes through the `Recognizer` interface, so it can move to an external service later without a data-model change.
- Suggestions carry a confidence. Every normal tag query filters `confidence IS NULL`, so auto-tags never mix with yours until you accept them.
- Only manual tags (confidence null) are exported to XMP. Face fingerprints and boxes are never written to sidecars.
- No GPU path. DirectML crashed on the quantized CLIP model and was slower than the CPU.
:::

# Network use

::: strip
Where: Settings > Updates, Map, model downloads, the family gallery
For: Anyone judging what leaves the PC
Answers: Which hosts are contacted, when, and with what?
:::

```mermaid Network use. Four outbound hosts, each started by a setting or a click, and one inbound path that never leaves the home network.
flowchart TD
  app["<b>Lunelis</b>"]
  app -- "daily, packaged build" --> a["api.github.com<br/>newest release"]
  app -- "you press Update" --> b["github.com<br/>release zip + .sha256"]
  app -- "you confirm" --> c["github.com, huggingface.co<br/>model files"]
  app -- "online map on" --> d["tile.openstreetmap.org<br/>map tiles"]
  lan(["Home network device"]) -- "album link + key" --> app
```

| Destination | Why | When | What is sent | Default |
|:--|:--|:--|:--|:--|
| `api.github.com` | Is a newer release out? | 8 s after start, if packaged and the last check is over 20 h old. Also the Check button | HTTPS GET for the latest release. Internet address and `User-Agent: Lunelis-updater` | On (`update_check`) |
| `github.com` (release assets) | Download the update and its `.sha256` | Only after you choose to install | HTTPS GET, same headers | Off until you click |
| `github.com` (OpenCV model collection, pinned commit; rembg release) | Face models; subject mask model | After you confirm in Settings, the Edit panel, the installer or the Welcome window | HTTPS GET, `User-Agent: Lunelis` | Off |
| `huggingface.co` | CLIP scene model (4 files); sky mask model | Same as above | HTTPS GET, `User-Agent: Lunelis` | Off |
| `tile.openstreetmap.org` | Map pictures | While the Map shows tiles and `map_online` is on. Each tile fetched once, kept in `map_tiles` | Zoom, column and row in the URL, address, `User-Agent: Lunelis/<version>` | Off |
| `www.openstreetmap.org` (your browser) | Show a photo's place | Only if "A photo's location opens" is set to the browser, and you click | The photo's coordinates, in a URL your browser opens | Off (opens Lunelis's Map) |
| Home network, port 8735 (inbound) | Family gallery | While at least one album is shared | Resized JPEGs without EXIF, or originals if you allowed it | Off until you share |

Nothing else opens a connection: a search of `src/lunelis` for `urllib`, `QNetworkAccessManager`, `socket` and `http` finds only the files above. There is no account, no telemetry and no crash upload. Help > Report a problem shows text for you to copy.

::: note Integrity of what comes down
- Every model download is hashed as it arrives and deleted on a mismatch. Existing model files are accepted later by size only.
- An update is installed only with a matching `.sha256`. The checksum comes from the same release page as the zip, so it proves the download is intact, not who made it. The build is unsigned.
- Map tiles are decoded as images before they are saved. They are not hashed.
:::

::: warn Family gallery
The gallery is plain HTTP on all addresses of the PC (`0.0.0.0`). A request from any address outside the home ranges is refused first. The rest of the guard: a random key per album, an optional PIN (PBKDF2, 200,000 rounds), 5 wrong PINs lock an address for 10 minutes, 20 wrong PINs lock an album for an hour, at most 32 connections, and 1600 px copies with no EXIF. Traffic on the home network is not encrypted.
:::

# Data inventory

| Data | Where it lives | Kept for | Leaves the PC? |
|:--|:--|:--|:--|
| Photos and videos | Your source folders | Until you remove them | Only by your action: export, import, backup, shared album |
| Catalog (ratings, picks, tags, albums, events, faces, settings) | `catalog.db` in the data folder | For ever | No. Included in snapshots and backup sets |
| EXIF | `exif` table, whole tag set as zlib JSON | For ever | No |
| Edit stacks and virtual copies | `edits`, `copies` tables, and `lunelis:EditStack` in sidecars | For ever | Sidecar text goes into backups |
| Thumbnails and edit previews | `cache/thumbnails`, `cache/edits` | Until the cache is cleared | No |
| Sidecars | `sidecars/` (central), or beside the photo | For ever | In backup sets (`_Lunelis/sidecars`) |
| Face fingerprints, names, crops | `faces`, `people`, `cache/faces` | For ever | No. In snapshots, so a shared snapshot shares them |
| Scene fingerprints and suggestions | `embeddings`, `file_tags` | For ever | No |
| Place tags and pins | `photo_places`, `locations` | For ever | Place tags yes (sidecar text); pins no |
| Models | `models/` | Until you remove them | They come in; nothing goes out |
| Catalog snapshots | `backups/*.zip` or your chosen folder | Newest 10 (upgrade snapshots are never pruned) | In backup sets (5 kept there) |
| Set-aside files | `_Lunelis Quarantine`, or the Lunelis folder's Duplicates and Trash | Until you empty them | No |
| Staging and update files | `staging/`, `updates/` | Until placed or cleaned up | The update zip comes in |
| Map tiles | `map_tiles/` | For ever | They come in |
| Gallery copies | `gallery_cache/` | Until cleared | Served on the home network |
| Log | `logs/lunelis.log`, 5 files of 2 MB | Rotated | No. May name folders and files |
| Start-up and location records | `location.json`, `setup.json`, Windows Run and Uninstall keys | Until changed | No |

# Failure paths

| What fails | What the program does | What the user sees |
|:--|:--|:--|
| A NAS or drive goes offline | The scan refuses the source. Metadata and thumbnail passes leave its files pending. Jobs switch to `waiting` and retry every 60 s. An import waits | A "Folder unavailable" box, photos marked OFFLINE (checked every 60 s), a job saying "Waiting for ... to come back online" |
| Staging disk full | The import pauses, never partly places a file | "Out of staging space" |
| Backup drive full or removed | The job pauses or waits. Nothing is deleted from the backup | The set's status line says why |
| Power cut or crash | Scans keep committed batches. A job resumes at its first pending folder. An import resumes from `import_items`. A migration resumes at the last safe state | "Resuming after an interruption" |
| Catalog damaged or empty | Start-up refuses to open it. It never starts silently empty | A box offering to restore the newest snapshot (the bad catalog is kept beside it), start empty, or quit |
| Catalog from a newer version | Refused, untouched | A message to install the newest Lunelis |
| Data folder file unreadable | The default folder is used for now. Nothing is moved | A warning naming the file |
| A sidecar cannot be written | The rating stays pending in the catalog and is retried (60 s, or 5 min after an error) | "Couldn't write N sidecar(s) - kept in the catalog" |
| A photo cannot be decoded | `thumb_error` or `read_error` is stored and the file is skipped until it changes | A placeholder tile, a row in Damaged files |
| A model is missing | The step is skipped silently. Ask falls back to word search | A "Download and turn on" button in Settings |
| A download does not match its hash | The partial file is deleted and nothing is installed | "Didn't match its checksum - nothing was installed" |
| The update cannot be swapped in | The script puts the old folder back and starts it | A warning on the next start |
| One library-pass step raises an error | It is logged and the other steps still run | "Finished, but these stopped on an error: ..." |

# To check

- **Start-up scan.** No timer or start-up call that starts a scan was found in `ui/main_window.py`. Scans start from Add folder, F5, Library status, Settings, an import and the migration wizard. Confirm this is intended.
- **Redirect hosts.** The code names `github.com` and `huggingface.co`. The final download servers after their redirects are not in the code.
- **Model files at rest.** `available()` in `recognize/clip.py` and `recognize/faces.py` accepts a file of the right size without re-hashing it. A file edited in place at the same size would be loaded.
- **Update trust.** The `.sha256` and the zip come from the same release. Whether signing is planned is not stated in the code.
- **Gallery exposure.** The server binds to all addresses and checks the client address. How Windows Firewall treats it was not examined. PIN and cookie travel unencrypted.
- **Pending flag race.** After a sidecar write, `xmp_pending` is cleared when stars, flag and label still match (`xmp/sync.py`). A tag or edit saved during that write is not compared. Not tested.
- **RAW decode in the photo view.** Preloading a neighbour that is an edited RAW with no cached proxy decodes the RAW at half size (`ui/detail_view.py`). This is outside the grid and filmstrip, but it is a RAW decode while paging.
- **Proxy settings.** Whether `QNetworkAccessManager` follows the Windows proxy settings was not examined.
- **darktable plugin.** `CLAUDE.md` says the Lua script was tested only under a Lua 5.4 stand-in, not in a real darktable. Not re-checked here.
- **Outside the program.** The Inno Setup installer and the CI workflows are not in `src/lunelis` and are not covered.
- **Speeds.** Figures in `CLAUDE.md` (scan, metadata and thumbnail rates) were measured on its author's library and were not re-measured.

# Discrepancies

| Where | The documents say | The code does |
|:--|:--|:--|
| `docs/_tools/architecture.mmd` | "SQLite schema v37" | `MIGRATIONS` ends at version 44 |
| `architecture.mmd` | Jobs engine runs "dust maps"; one box holds "Import + Autopilot, migrate, photo backups" | There is no dust job kind (`dust_view.py` uses a page loader). Import and autopilot have their own threads; migrate and backups are job kinds |
| `CLAUDE.md`, Architecture | Edits are "an instruction stack in XMP" | The stack is written to the sidecar but never read back. `import_sidecars` ignores `lunelis:EditStack`, and nothing reads the central store. Edits survive a catalog loss only through snapshots and backups |
| `CLAUDE.md`, thumbnail and metadata rules | Video thumbnails "not yet"; CR3 and video have no EXIF reader | Videos get a poster-frame thumbnail (PyAV) and a metadata probe. Only CR3 still has no reader |
| `CLAUDE.md`, thumbnail rules | Cache path `<id//1000>/<id>.jpg` | `cache_rel_path` zero-pads to four digits: `0012/12819.jpg` |
| `CLAUDE.md`, scanner rules | A changed size or time nulls `content_hash`, `perceptual_hash`, `thumbnail_path` | The update also clears `sample_hash` and `thumb_error` |
| `CLAUDE.md`, non-negotiables | "Lunelis's own files never go in the photo library" | Each source can hold `_Lunelis Quarantine`. Sidecar temp files (`.lunelis-*.xmp.tmp`) and import or migration temp files sit in the target folder while being written. A "Lunelis folder" you choose can hold exports, trash and logs |
| `CLAUDE.md`, backup rule | A snapshot is taken before every job that moves or removes files | True, but Duplicates, Near-duplicates and Quarantine snapshot into `paths.BACKUP_DIR` (`data/backups`), not the folder set in Settings that daily snapshots use. Start-up recovery also looks only in `data/backups` |
| `geo/places.py` docstring | About 34,000 places | The list holds 31,735 rows |
| `CLAUDE.md`, Quarantine rules | `empty()` is the only hard removal | Clear the card (`importing/ingest.py`) also deletes, from the card, after a double hash check and your confirmation. Update and staging clean-ups remove Lunelis's own temp folders |
