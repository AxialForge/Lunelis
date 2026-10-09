---
type: engine
title: Duplicate Detection Engine
subtitle: From first scan to safe quarantine, with the real thresholds
audience: Maintainers and curious power users
sources: src/lunelis/dupes/ (hashing, detect, similar, quarantine, manage), ui/dupes_view.py, ui/near_view.py, jobs/engine.py, settings.py, CLAUDE.md
---

<!-- Describes Lunelis 0.46.0. Every number is read from the code; file:line references are to that version. Measured figures from the author's library are quoted from CLAUDE.md or CHANGELOG.md and were not re-measured. -->

# The problem it solves

::: strip
Where: Library > Find duplicates, and the Duplicates page
For: Maintainers and power users with several photo folders
Answers: How are copies found, proven, and set aside without loss?
:::

The same photo ends up in many places: a backup folder, an old pool, a Google Takeout export, a resized copy sent to a friend. Finding them by file name does not work, because names change. Reading every byte of a multi-terabyte library to compare it is far too slow, especially over a network share.

The duplicates engine solves this in two separate tracks. Each track only *proposes*. Nothing leaves your folders until you press a button, and even then it is renamed into a quarantine folder, never deleted.

| Track | What it finds | How it knows | Code |
|:--|:--|:--|:--|
| Exact copies | Files with identical bytes | Cheap sample hash first, then a full SHA-256 | `dupes/detect.py`, `dupes/hashing.py` |
| Near-duplicates | The same picture saved as a different file (resized, re-compressed, re-encoded, exported) | A 64-bit fingerprint of the thumbnail, then strict match rules | `dupes/similar.py` |

::: stats
192 KB | most a sample hash reads from one file (3 slices of 64 KB)
~12x | fewer candidates per folder once capture time joins file size as the key
4 bits | most two 64-bit fingerprints may differ to count as one picture
1 | place that removes files for good: Empty, on the Quarantine page
:::

## What the real library looked like

CLAUDE.md records what the engine met on the author's own library (about 159,000 files, 4.6 TB). These numbers shaped the rules in this document.

- One folder of 15,841 files from an old photo pool: the first sampled pass took 424 s and read 17.8 GB, because it hashed 91,000 files that merely had the same size. Adding the capture time cut the per-folder candidates about 12 times.
- That one folder held 13,510 likely groups and 689.7 GB in extra copies.
- The first near-duplicate pass (CHANGELOG 0.6.0) suggested 4,076 copies (18.2 GB) in 4,975 groups. The rules have been tightened since, so treat that as history.
- Grouping by chaining ("A looks like B, B looks like C") once produced a group of 3,126 night-sky photos. The rule that replaced it brought the largest group down to 6.

## Words used in this document

| Word | Meaning |
|:--|:--|
| Source | A folder tree Lunelis watches. The code calls it a root. |
| Copy / extra | Any member of a group. The *keeper* is the one that stays; the others are *extras*. |
| Sample hash | SHA-256 of the file size plus three 64 KB slices. A filter, never proof. |
| Full hash | SHA-256 of every byte, stored as `content_hash`. Proof of identical bytes. |
| Likely / Verified | Page labels for a *sampled* group and an *exact* (verified) group. |
| Fingerprint | The 64-bit difference hash (dHash) of a thumbnail, `files.perceptual_hash`. |
| Bits apart | How many of the 64 fingerprint bits differ (the Hamming distance). |
| Set aside | Rename into `_Lunelis Quarantine` on the same source. Reversible. |

::: note Version
This document describes Lunelis 0.46.0 (`pyproject.toml`). Line numbers will drift in later releases; function names are the stable anchor.
:::

# How it works

The engine has two tracks that meet at the same last step. The exact track runs as background *jobs*, folder by folder. The near-duplicate track runs after the thumbnails exist.

```mermaid Overview: two tracks find candidates and prove them; both end at the same keeper choice and the same reversible quarantine.
flowchart TB
  subgraph EX["Exact copies: background jobs"]
    direction LR
    A["Candidates:<br>same size +<br>capture time"] --> B["Sample hash:<br>3 x 64 KB"]
    B --> C["Likely<br>group"]
    C --> D["Verify:<br>full SHA-256"]
    D --> E["Exact group<br>(verified)"]
  end
  subgraph NE["Near-duplicates: after thumbnails"]
    direction LR
    F["Thumbnail"] --> G["64-bit<br>dHash"]
    G --> H["Buckets:<br>5 chunks"]
    H --> I["Match<br>rules"]
    I --> J["Group: every<br>pair matches"]
  end
  E --> K["Keeper chosen"]
  J --> K
  K --> L["Set aside into<br>_Lunelis Quarantine"]
  L --> M["Restore, or Empty<br>(Quarantine page)"]
```

## Where each stage runs

| Stage | Started by | Runs as |
|:--|:--|:--|
| Candidates and sample hash | Library > Find duplicates... (`ui/main_window.py:834`) | Job kind `duplicates`, one folder at a time |
| Verify | "Verify all likely groups..." or "Verify this group" | Job kind `verify`; the single-group button runs on a worker thread |
| Hash everything | Library > Hash everything (`ui/main_window.py:835`) | Job kind `full_hash`: an integrity baseline, not a duplicate check |
| Fingerprint | Every thumbnail made (`raw/thumbnails.py:278`) | Inside the thumbnail pass; older photos by `similar.compute_missing` |
| Compare fingerprints | After each scan (`ui/main_window.py:238`) or "Find again" | Library worker thread; regroups only when new fingerprints exist |
| Set aside | Buttons on the Duplicates page | Worker threads for "all"; one exact group runs on the interface thread |

## The jobs engine in one minute

A job is a kind plus options plus a list of folders (`jobs/engine.py:172-214`). The kinds used here are `duplicates`, `verify` and `full_hash`. The engine knows nothing about what a kind does to a folder; it only keeps the bookkeeping, which is what makes the work safe to stop.

- **One folder at a time.** A folder is marked done only after its work is committed (`jobs/engine.py:329`), and every hash is committed as soon as it is made (`dupes/detect.py:166-167`). Pause, quit or a power cut loses at most the folder in progress.
- **Resume.** At start-up, jobs left `running` or `waiting` are queued again (`recover_interrupted`, line 226).
- **Offline shares wait, they do not fail.** A network error raises `SourceOffline`; the job switches to `waiting` and is retried every 60 s (`OFFLINE_RETRY_S`, line 32). The file is not marked bad (`hashing.py:38-43`).
- **Schedule.** `now`, `idle` (no input for N minutes) or a nightly `window` of hours (`schedule_allows`, line 256).
- **Speed limit.** One shared MB/s cap across all hashing threads (`Throttle`, `hashing.py:46-62`).
- **Threads.** Eight workers for the sample pass; at most four for full hashing (`jobs/engine.py:56,85`; `detect.py:208`).

::: tip Cheap to stop
Pausing a duplicates job is safe at any moment. The next run skips every file whose `sample_hash` is already stored and starts again at the first unfinished folder.
:::

# The Duplicates page

The page is the human face of the engine (`ui/dupes_view.py`, `ui/near_view.py`). It has two tabs and never deletes anything.

## Exact copies tab

Three cards across the top explain the sequence: **Found** (same size, matching samples), **Verified** (every byte compared) and **Set aside** (moved to a quarantine folder on the same drive).

| Control | What it does |
|:--|:--|
| Summary line | "N files have copies, X GB could be freed (V verified, X GB ready; L likely, still to verify)" |
| Show | All groups, Verified only, Likely only (`dupes_view.py:257-261`) |
| Verify all likely groups... | Queues one `verify` job over every source that has an unverified likely group (`main_window.py:3010`) |
| Set aside all verified extras... | After a confirmation, a worker thread sets aside every verified group's extras (`QuarantineWorker`, line 178) |
| When copies are identical, keep the one in | Stores `preferred_roots` and reloads the groups (`_prefer_changed`, line 366) |
| Group list | Photo (thumbnail and name), Copies, Space the extras take, Status. Biggest saving first |
| Detail panel | The kept copy ("Stays where it is"), the extras (each with **Show** and **Keep this one**), and one action: **Verify this group** or **Set aside N extra copies** |

The list shows verified groups plus likely groups that are not yet fully hashed. A likely group whose every member already has a full hash is hidden, on the assumption that a verified group represents it (`dupes_view.py:92`). See the Discrepancies chapter for the catch.

## Near-duplicates tab

| Control | What it does |
|:--|:--|
| Summary line | "G groups, N copies suggested (X GB), D of T photos compared" |
| Find again | Fingerprints any photo missing one, then regroups everything (`SimilarWorker`, `near_view.py:36-80`) |
| Group list | Copies, Suggested, Kept copy path |
| Detail table | Thumbnail, location, dimensions and size, and either **Kept**, **Edit - kept**, or a tick box "Set aside" |
| Set aside ticked copies | Sets aside the ticked rows of the chosen group, after a confirmation |
| Set aside all suggested... | Sets aside every pre-ticked copy in every group, after a confirmation. The text advises looking at a few groups first |
| Stop | Sets a cancel flag; the worker checks it between groups |

::: note Ticked means suggested
A copy starts ticked only if the engine calls it plainly lesser. You can tick others by hand. You cannot tick the keeper or an edit: those rows have no tick box.
:::

# Exact copies

## Step 1: choose candidates, folder by folder

The job works through one folder at a time. A folder counts as done only when its duplicates are known across the whole catalog, so the first result appears after the first folder, not after the whole library.

```mermaid Candidate selection: a file is read only if another live file could be a byte-for-byte copy of it.
flowchart TB
  A["A file in the folder<br>being processed"] --> B{"Same size as another<br>live file? (size above 0)"}
  B -- no --> X["Never read"]
  B -- yes --> C{"That file has the same<br>capture time, or either<br>has no capture time?"}
  C -- no --> X
  C -- yes --> D{"Sample hash<br>already stored?"}
  D -- yes --> R["Reuse it"]
  D -- no --> E["Read 3 slices<br>and hash"]
  E --> F{"Same sample hash<br>as another file?"}
  F -- yes --> G["Likely group"]
  F -- no --> H["No group"]
```

The first test is a SQL query: sizes in this folder that also occur on some other live file (`detect.py:157-161`). Sizes are looked up 500 at a time (`detect.py:117`). The second test compares capture times as stored text, so "to the millisecond" really means equal text (`detect.py:131-137`).

**Why capture time?** Uncompressed Sony RAW files are all exactly the same size. With size as the only key, every RAW of that camera body counted as a candidate for every other, and one 15,841-file folder hashed 91,000 files. Identical bytes imply identical EXIF, so two real copies always share a capture time. A file with no capture time matches on size alone, so no true copy is missed.

```chart Files hashed for one 15,841-file folder (CLAUDE.md, "Jobs & duplicates rules"). The second bar is derived: 91,000 divided by the stated factor of about 12.
{"type":"hbar","labels":["Key = size only","Key = size + capture time"],"series":[{"name":"Files hashed","values":[91000,7600]}],"height":2.0}
```

## Step 2: the sample hash

For each candidate, Lunelis computes one SHA-256 over the text `"<size>:"` followed by three 64 KB slices (`hashing.py:74-91`). The slices start at byte 0, at the middle (`size // 2 - 32 KB`) and at the last 64 KB. A file of 192 KB or less is read whole. A sample hash therefore reads at most 196,608 bytes, whatever the file size.

```chart Bytes read per file, in MB. A sample hash is fixed at 3 x 64 KB; a full hash reads the whole file. The 20 and 40 MB sizes are the range quoted in hashing.py's docstring.
{"type":"hbar","labels":["Sample hash (any size)","Full hash, 20 MB photo","Full hash, 40 MB photo"],"series":[{"name":"MB read","values":[0.19,20,40]}],"height":2.2}
```

- **Eight files at a time** on a thread pool (`detect.py:28,93`), sharing the speed limit.
- **Saved at once.** The hashes of a folder are written and committed before grouping (`detect.py:166-167`).
- **Errors are listed, not fatal.** A file that cannot be read is reported with its error; a network error stops the folder and the job waits (`detect.py:85-90`).

## Step 3: likely groups

Files that share a sample hash form a group with method `sampled` (`rebuild_groups`, `detect.py:178-202`). The page calls these **Likely**. A group with fewer than two live members is deleted.

::: warn A sample is only a filter
Two different files can share a size, a date and three slices. The code never acts on a sampled group: `quarantine()` refuses it outright (`quarantine.py:37-39`). Verify first.
:::

## Step 4: verify

`verify_group` (`detect.py:207-229`) reads **every byte** of every member in 1 MB pieces (`hashing.py:21,94-115`), four files at a time. A file that already has a `content_hash` is not read again. New hashes are stored only where `content_hash` is still empty. Members are then regrouped by full hash into `exact` groups with `verified = 1`.

If the sample was a coincidence, the files end up with different full hashes, and no exact group forms. The old likely group stays in the catalog but is hidden from the page once all its members have a full hash.

A verified group stops counting as verified when one of its files changes on disk: a trigger sets `verified = 0` whenever a file's size or modified time changes (schema migration 41, `catalog/schema.py:877-882`).

## Step 5: choose the keeper

Identical copies have identical content, so the keeper is chosen by *where* the copy sits. `keeper_rank` (`detect.py:234-246`) sorts by this tuple; the first wins.

| Order | Test | Source |
|:--|:--|:--|
| 1 | Position in your "keep the copy in" source (none chosen: all tie) | `preferred_roots` |
| 2 | Not a Takeout copy (the word "takeout" anywhere in source path plus file path) | `detect.py:243` |
| 3 | Fewest folder levels | `rel.count("/")` |
| 4 | Shortest relative path | `len(rel)` |
| 5 | Lowest file id (cataloged first) | `fid` |

Your own choice beats the rules. **Keep this one** stores `is_keeper = 1` for that file and 0 for the rest of the group (`dupes_view.py:101-105`), and the page prefers the marked file (`dupes_view.py:95`). The mark lives on the group row, so it disappears if that group is rebuilt (see Known limits).

# Near-duplicates

## Step 1: the fingerprint

Each thumbnail is shrunk to 9 by 8 grey squares. For each of the 8 rows, 8 comparisons ask "is this square brighter than its right-hand neighbour?" That gives 64 bits, kept as 16 hex digits (`dhash`, `similar.py:53-61`). Pictures that look alike get nearly the same bits, whatever their size or JPEG quality.

- **Free for new photos.** The fingerprint is taken from the picture in memory while the thumbnail is made (`raw/thumbnails.py:278`), so it costs no extra read.
- **Older photos** are fingerprinted from the cached 512 px thumbnail, never from the original or the NAS, 2,000 at a time on 8 threads, with a 64 by 64 JPEG draft decode (`similar.py:64-94`).
- **Stale fingerprints are dropped.** When the scanner sees a file's size or date change it clears `perceptual_hash` along with the hashes and thumbnail (`importers/scan.py:361`).

## Step 2: find candidates without comparing everything

Comparing every pair of 150,000 photos is out of the question. Instead each fingerprint is cut into five chunks of 13, 13, 13, 13 and 12 bits (`_chunks`, `similar.py:97-103`). Two fingerprints that differ in at most 4 bits cannot disturb all five chunks, so at least one chunk is identical. Each photo is filed into five buckets (chunk number plus chunk value); only photos sharing a bucket are compared.

```mermaid Candidate search: five buckets per photo; a bucket of more than 400 photos is skipped as meaningless.
flowchart LR
  A["Photo<br>64-bit fingerprint"] --> B["Cut into 5 chunks<br>13+13+13+13+12 bits"]
  B --> C["File into 5 buckets<br>chunk no. + value"]
  C --> D{"Bucket has 2 to<br>400 photos?"}
  D -- yes --> E["Compare each pair<br>inside the bucket"]
  D -- no --> F["Skip bucket"]
```

Three filters apply before a pair is even considered (`find_groups`, `similar.py:115-149`):

- **RAW files are excluded** (`is_raw = 0`). A RAW's copies are exact copies, and a RAW and its JPEG are different files on purpose.
- **Near-blank fingerprints** (fewer than 8 or more than 56 bits set: black frames, flat sky) are not put into the chunk buckets. They can still be paired by the same-name rule below.
- **Same-name buckets.** Photos with the same file name (any folder, 2 to 8 of them) get their own bucket, so a copy whose picture moved more than 4 bits can still be compared.

## Step 3: a pair is one photo only if nothing contradicts it

`same_photo` (`similar.py:216-242`) applies the tests in this order.

```mermaid The near-duplicate decision: the real tests, in code order. Any failed test rejects the pair.
flowchart TB
  A["Pair from one bucket"] --> B{"Same file name and<br>times 61 s to 36 h apart?"}
  B -- yes --> S["Shifted copy:<br>allow 16 bits (4 if<br>either is near blank);<br>skip the moment test"]
  B -- no --> C{"Either picture<br>near blank?"}
  C -- yes --> R["Not the same photo"]
  C -- no --> T["Allow 4 bits"]
  S --> D{"Bits apart within<br>the allowance?"}
  T --> D
  D -- no --> R
  D -- yes --> E{"Same file size?"}
  E -- yes --> R
  E -- no --> F{"Same moment?<br>(or shifted copy)"}
  F -- no --> R
  F -- yes --> G{"Names equal, or one<br>inside the other?"}
  G -- no --> R
  G -- yes --> H{"Aspect ratio<br>within 2 %?"}
  H -- no --> R
  H -- yes --> M["Same photo"]
```

| Test | Rule | Why |
|:--|:--|:--|
| Same file size | Rejected | Byte-identical copies are the exact pass's job |
| Same moment | Capture times equal to the sub-second when both have one (first 23 characters), else to the second (first 19). A missing time never contradicts | Burst frames 0.125 s apart look alike but are different shots |
| Related names | Name without extension is equal, or one contains the other (`dsc0040` inside `20191012-_dsc0040`) | Copies keep their name; unrelated names at one moment are another photo |
| Aspect ratio | Within 2 % of the larger ratio, after applying EXIF orientation | A crop is a different picture |

### The shifted-copy exception

A Google Takeout copy of an edited photo can come back hours away from the camera's clock. If the two files have the **same name** and capture times **more than 60 seconds and up to 36 hours apart** (`similar.py:197-207`), the moment test is waived and the fingerprints may differ by up to **16 bits** (`EDITED_DISTANCE`, line 198). A gap of a minute or less is a burst frame, never a shifted copy. If either picture is near blank, the allowance stays at 4 bits (`similar.py:226`).

## Step 4: groups where everybody matches everybody

Pairs are joined into groups by a greedy complete-linkage pass (`similar.py:166-181`). Seeds are taken in order of most matches (ties by file id); a candidate joins only if it matches **every** current member. Byte-identical files (same size and same fingerprint) count as matching, so they do not keep an edit of the same shot out of the group.

```mermaid Why chaining is refused: A and C are 6 bits apart, so they never share a group even though B matches both.
flowchart LR
  A(("A")) ---|"3 bits"| B(("B"))
  B ---|"3 bits"| C(("C"))
  A -.-|"6 bits: no match"| C
```

```chart Largest near-duplicate group on the author's library (CLAUDE.md, Gotchas): chaining versus complete linkage.
{"type":"hbar","labels":["Chaining: group 1","Chaining: group 2","Chaining: group 3","Complete linkage: largest"],"series":[{"name":"Photos in the group","values":[3126,1291,885,6]}],"height":2.4}
```

## Step 5: the keeper and what is suggested

`keeper_rank` in `similar.py:342-358` sorts by the following; the first wins.

| Order | Test |
|:--|:--|
| 1 | Not damaged (no row in `damaged`) |
| 2 | Not an edit (the path contains "edit" or "export") |
| 3 | Most pixels (width times height from EXIF) |
| 4 | Not in a Takeout source (`takeout_roots`, by the source's own folder name) |
| 5 | Position in your "keep the copy in" source |
| 6 | Biggest file |
| 7 | Fewest folder levels |
| 8 | Lowest file id |

Only a **plainly lesser** copy is pre-ticked (`suggest`, `similar.py:301-310`). It must not be an edit, and it must have fewer pixels than the keeper, or sit in a Takeout source, or have the same file stem as the keeper. A copy with the same pixels under another name might be the original of an edit, so it is left unticked; you can still tick it.

## When it runs

`similar.refresh` (`similar.py:272-282`) fingerprints what is missing, then regroups only if the number of fingerprinted live files differs from the stored `similar_grouped_count`. Regrouping replaces all `similar` groups at once (`rebuild`, line 251), so groups are always consistent. "Find again" always regroups.

# Setting aside, restoring and emptying

## Setting aside

Set aside is the only action the engine takes on your files. `quarantine()` (`quarantine.py:33-72`) does this for exact groups; `quarantine_similar()` (`similar.py:361-393`) does the same for near-duplicates with slightly different checks.

1. **Check the request.** Exact: the group must be method `exact` and verified, every requested file must be a live member, and at least one live copy must remain. Near: the group must be method `similar` and at least one live member must remain.
2. **Snapshot the catalog** into `<data>/backups` as `catalog-<time>-before-quarantine.zip` (`quarantine.py:51-53`). The bulk buttons snapshot once, before the first move.
3. **Pick the copy that receives your work.** Exact: the marked keeper, else the lowest id among the remaining copies. Near: the best remaining copy by the keeper ranking above.
4. **Merge your work into it** (`merge_user_data`, `migrate/execute.py:155-178`). See the table below.
5. **Exact only: carry the sidecar.** If the set-aside copy has an XMP sidecar and the keeper has none, a copy goes beside the keeper, named for it, never overwriting a file that is there (`carry_sidecar`, line 181).
6. **Move the pair.** The photo and its sidecar are renamed into `<source>\_Lunelis Quarantine\<same relative path>` (`move_pair`, `quarantine.py:94-109`).
7. **Commit per file.** `quarantined_at` and `quarantine_path` are written and committed right after each move (`quarantine.py:67-69`), so a crash cannot lose track of one.

The move is a rename inside the same source, so it is instant and needs no free space. If the name is already taken in quarantine, both the photo and its sidecar get the file id added, such as `IMG_0412 (5231).ARW` (`quarantine_paths`, lines 75-91). That decision is made before anything moves.

### What moves to the keeper

| Moved to the keeper | Rule |
|:--|:--|
| Stars, pick flag, colour label | The keeper's own non-empty value wins; an empty one takes the set-aside copy's. Sets `xmp_pending` so the sidecar is rewritten |
| Albums | Added, keeping the album position |
| Tags | Manual tags only (confidence empty); suggested scene tags are not carried |
| Event | Only if the keeper is in none |
| Not moved | Edit stacks, face data, map pins, notes: they stay with the set-aside row |

## The life of a copy

```mermaid One copy's life: set aside is reversible; Empty is the only exit, and it is always your decision.
stateDiagram-v2
  direction LR
  state "In the library" as Live
  state "Set aside" as Aside
  state "Past its time" as Due
  [*] --> Live
  Live --> Aside: Set aside
  Aside --> Live: Restore
  Aside --> Due: older than keep days
  Due --> Emptied: you confirm
  Aside --> Emptied: Empty selected
  Emptied --> [*]
```

## Restoring

`restore()` (`quarantine.py:157-172`) renames the photo, and its sidecar, back to where they were, refusing if something already sits at the old path. It clears `quarantined_at`, `quarantine_path` and `missing_since`. The scanner never calls a quarantined file missing and skips the `_lunelis quarantine` folder (`importers/scan.py:33-36,327-329`).

## Emptying

Emptying lives on the Quarantine page (`dupes/manage.py:225-288`). For each file it demands, in order:

1. the file is still in quarantine;
2. the **kept copy exists** on disk and, for byte copies, has the same size;
3. the kept copy is **byte-for-byte the same** (a full SHA-256 of both, `manage.py:291-306`);
4. then the file goes to the **Recycle Bin** on a local NTFS or ReFS fixed drive, or is deleted for good elsewhere (network share, USB stick, FAT or exFAT volume, `paths.has_recycle_bin`);
5. a row is written to the `purged` table, the file's catalog row is deleted, and its sidecars follow on a best-effort basis.

A catalog snapshot named `before-empty-quarantine` is taken first. "Keep set-aside files" offers 0 (forever, the default), 30, 90 or 365 days; after that the files are only *offered* for removal (`manage.due`, line 134). Nothing is removed by itself.

# Thresholds, settings and what it writes

## Decision points: exact copies

| Question | Threshold | Where in the code |
|:--|:--|:--|
| Sample slice size | 64 KB, three slices (start, middle, end) | `hashing.py:20,84` |
| Read whole instead of sampling | Size up to 192 KB (3 x 64 KB) | `hashing.py:79` |
| Full-hash read size | 1 MB at a time | `hashing.py:21` |
| Speed-limit burst allowance | 0.25 s | `hashing.py:60` |
| Candidate must be larger than | 0 bytes | `detect.py:159` |
| Candidate capture time | Equal text, or either side has none | `detect.py:131-137` |
| Worker threads, sample pass | 8 | `detect.py:28`, `engine.py:204,286` |
| Worker threads, full hash | 4 | `detect.py:208`, `engine.py:56,85` |
| Group exists when | 2 or more live members | `detect.py:187` |
| Offline retry | Every 60 s | `engine.py:32` |

## Decision points: near-duplicates

| Question | Threshold | Where in the code |
|:--|:--|:--|
| Fingerprint size | 64 bits (9 x 8 grey squares) | `similar.py:55-61` |
| Thumbnail draft decode | 64 x 64 | `similar.py:67` |
| Old photos per batch | 2,000, on 8 threads | `similar.py:73,82` |
| Maximum bits apart | 4 | `similar.py:47` |
| Chunks per fingerprint | 5 (13, 13, 13, 13, 12 bits) | `similar.py:100` |
| Biggest bucket compared | 400 photos | `similar.py:48,156` |
| Near-blank fingerprint | Fewer than 8 or more than 56 bits set | `similar.py:127,223` |
| Same-name bucket size | 2 to 8 photos | `similar.py:148` |
| Shifted copy | More than 60 s and up to 36 h apart, same name | `similar.py:197,207` |
| Bits allowed for a shifted copy | 16 (4 if near blank) | `similar.py:198,226` |
| Aspect ratio | Within 2 % of the larger | `similar.py:240` |
| Edit words | "edit" or "export" anywhere in the relative path | `similar.py:248` |

## Settings

| Setting | Default | Range | Effect |
|:--|:--|:--|:--|
| `preferred_roots` | empty (any source) | List of source ids | The keeper is the copy in the first listed source. Set on the page or in Settings (`settings.py:32,168`) |
| `job_default_when` | `now` | `now`, `idle`, `window` | Default "when to run" for a new job (`settings.py:45,146`) |
| `job_idle_minutes` | 5 | 1 to 1,440 | Idle means no keyboard or mouse for this long |
| `job_window_start_hour` | 22 | 0 to 23 | Start of the nightly window |
| `job_window_end_hour` | 6 | 0 to 23 | End of the window; may be earlier than the start (past midnight) |
| `job_mb_per_s` | 0 (no limit) | 0 to 100,000 | Read-speed cap for new jobs from the Find duplicates dialog |
| `trash_keep_days` | 0 (forever) | Page offers 0, 30, 90, 365; not range-checked | Age after which set-aside files are offered for removal (`settings.py:88`) |
| `similar_grouped_count` | 0 | Internal | Fingerprints the last grouping used; not a user setting (`settings.py:89`) |
| `catalog_backups_keep` | 10 | 1 to 1,000 | Snapshots kept; older ones, including `before-quarantine` ones, are pruned (`settings.py:29`) |

The thresholds in the two decision tables above are constants in the code, not settings.

## What it writes

| Where | What | When |
|:--|:--|:--|
| `files.sample_hash` | Sample hash of a candidate | Duplicates job; cleared if the file changes |
| `files.content_hash` | Full SHA-256, only where empty | Verify, Hash everything, integrity and backup runs |
| `files.perceptual_hash` | 64-bit fingerprint | Thumbnail pass, `compute_missing`; cleared if the file changes |
| `duplicate_groups`, `duplicate_group_files` | Groups of method `sampled`, `exact`, `similar`; `verified`; `is_keeper` | Each pass. Similar groups are rebuilt whole |
| `jobs`, `job_folders` | State, folders done, bytes read | Whenever a job runs |
| `files.quarantined_at`, `quarantine_path` | Where and when a copy was set aside | Set aside; cleared by Restore |
| `ratings`, `album_files`, `file_tags`, `event_files` | Your work merged into the keeper | Set aside |
| Sidecar beside the keeper | A copy of the set-aside copy's XMP | Exact set aside, only if the keeper has none |
| `<source>\_Lunelis Quarantine\...` | The renamed photo and its sidecar | Set aside |
| `<data>\backups\catalog-...-before-quarantine.zip` | A catalog snapshot | Before the first move of a batch |
| `settings` | `preferred_roots`, `similar_grouped_count`, `trash_keep_days` | On change or after grouping |
| `purged`; Recycle Bin or deletion | Record of what was emptied | Only when you empty the quarantine |

# How it keeps your files safe

| Guarantee | Enforced by |
|:--|:--|
| A sample hash never acts alone: only verified exact groups can be set aside | `quarantine()` raises "only verified byte-identical groups" (`quarantine.py:37-39`) |
| The last live copy is never moved | `quarantine.py:48-49`; `quarantine_similar`, `similar.py:375-377` |
| Only live members of the named group can be requested | `quarantine.py:45-47` |
| The catalog is snapshotted before moves | `quarantine.py:51-53`; `similar.py:378-380`; both pages pass the backup folder |
| Nothing is deleted: a set-aside is a rename on the same source | `quarantine_paths`, `move_pair` (`quarantine.py:75-109`) |
| A photo and its sidecar move together or not at all | `move_pair` checks both targets first and puts the photo back if the sidecar fails (`quarantine.py:98-109`) |
| Nothing is overwritten, and a name clash is settled before any move | Id suffix in `quarantine_paths`; refusal in `move_pair` |
| A crash cannot lose track of a file | Commit per file (`quarantine.py:69`); folder-by-folder jobs |
| Your stars, albums, tags and event survive | `merge_user_data` runs before the move |
| A sidecar is not left only in quarantine | `carry_sidecar` (exact copies) |
| A cross-drive move would be verified | `move_one` copies, compares byte for byte, and only then removes (`quarantine.py:112-129`) |
| A changed file loses its verified status and stale hashes | Trigger `files_changed_unverify`; `scan.py:361` |
| An offline share pauses the job and marks nothing bad | `SourceOffline`, `offline_error` (`hashing.py:27-43`); `engine.py:318-325` |
| Edits and exports are never suggested for removal | `is_edit` in `suggest` (`similar.py:245,307`); no tick box for them in `near_view.py:199` |
| RAW files never enter near-duplicate matching | `is_raw = 0` filter (`similar.py:121`) |
| Quarantined files are not "missing" and not re-cataloged | `scan.py:33-36,327-329` |
| Restore never overwrites | `move_pair` refuses an occupied path |
| Emptying demands a present, byte-identical kept copy; snapshots; logs | `manage.empty` (`manage.py:225-288`) |
| Nothing is emptied automatically | `manage.due` only offers (`manage.py:134-151`) |

# A worked example

All names and sizes are made up. Two sources are watched: `D:\Photos` and `E:\Backup Pool`.

## The photos

- `D:\Photos\2024\6-19-2024 Air Show\IMG_0412.ARW`, about 61 MB, captured 2024-06-19 10:22:31.250. Your 3-star rating is on this copy.
- `E:\Backup Pool\Air Show 2024\IMG_0412.ARW`, a byte-identical copy.
- `D:\Photos\2024\6-19-2024 Air Show\IMG_0413.ARW`, the very same size, captured at 10:22:31.375, a different shot.
- `D:\Photos\2024\6-19-2024 Air Show\IMG_0420.JPG`, 6000 x 4000, 14 MB.
- `D:\Google Takeout 2024\Google Photos\Air Show\IMG_0420.JPG`, 2048 x 1365, 1.1 MB, whose time came back 8 hours later.
- `D:\Photos\Exports\Air Show edit\IMG_0420-edit.JPG`, a hand edit of the original.

## Exact track

1. **Find duplicates** on both sources. Processing the folder `2024/6-19-2024 Air Show`: the RAW size occurs on three other files. `IMG_0413.ARW` shares the size but not the capture time, so it is **never read**. Only the two `IMG_0412.ARW` files are candidates.
2. Each candidate gives one sample hash from three 64 KB slices, about 192 KB per file instead of 61 MB. The hashes match: a **Likely** group of two.
3. **Verify this group** reads both files completely in 1 MB pieces. The full hashes match, an `exact` group forms, and the status becomes **Verified**.
4. **Which copy stays?** The E: copy sits in a shallower folder (one level, against two on D:), so with no preference it would be the keeper. You choose "keep the copy in D:\Photos" on the page, which puts D: first in the keeper order.
5. **Set aside 1 extra copy.** A snapshot `catalog-...-before-quarantine.zip` is written. The D: copy is rated 3 stars; the E: copy is rated 4 stars and sits in an album "Air Shows". The keeper's own non-empty rating wins, so D: keeps 3 stars, and the album and any manual tags are added to D:.
6. The E: file is renamed to `E:\Backup Pool\_Lunelis Quarantine\Air Show 2024\IMG_0412.ARW`. Nothing was copied or deleted, and it took an instant.

## Near-duplicate track

1. Three JPEGs of the same shot are fingerprinted: the original and the edit differ by 2 bits, the Takeout copy by 11 (illustrative figures).
2. They share a chunk bucket (or the same-name bucket), so they are compared.
3. Original against the Takeout copy: same name `img_0420.jpg`; the times are 8 hours apart, which is more than 60 s and under 36 h, so this is a **shifted copy** with 16 bits allowed. Sizes differ. Aspect ratios 1.5000 and 1.5004 agree within 2 %. **Match.**
4. Original against the edit: names `img_0420` and `img_0420-edit` (one inside the other), 2 bits, same moment, different size. **Match.** The Takeout copy and the edit also match each other, so the three form one complete group.
5. **Keeper:** the edit ranks below the original (it is an edit). The original has the most pixels. The original stays.
6. **Suggested:** the Takeout copy (fewer pixels, Takeout source) is pre-ticked. The edit shows **Edit - kept** and has no tick box.
7. After you confirm, the Takeout copy moves to `D:\Google Takeout 2024\_Lunelis Quarantine\Google Photos\Air Show\IMG_0420.JPG`, and its ratings and albums go to the original.

::: tip Undo
On the Quarantine page, Restore puts either file back under its old name. If you never empty the quarantine, you have lost nothing.
:::

# Known limits

- **Exact copies are byte-identical only.** One changed byte makes a different file. Metadata edits or a re-saved JPEG belong to the near-duplicate track.
- **Near-duplicates need a thumbnail first** and never cover RAW files. A RAW and its JPEG are never grouped.
- **A renamed copy with an unrelated name is not matched,** and neither is a cropped copy (aspect rule).
- **A near-blank picture** (black frame, clear sky) is matched only through the same-name rule, and only within 4 bits.
- **The "edit" or "export" test is a plain substring** of the relative path, so a folder called `Credits` counts as an edit and its files are never pre-ticked.
- **The same-name rule trusts the name.** The camera model is read into memory but not compared (`similar.py:125-145`), so two cameras that both write `IMG_0001.JPG` within 36 hours, with fingerprints under 16 bits apart, could be paired.
- **The mark from "Keep this one" is not durable.** `rebuild_groups` deletes and re-inserts a group's members, which resets `is_keeper` (`detect.py:193-199`). A later verify or folder pass over the same group clears your choice.
- **Edit stacks and face data are not merged** into the keeper (`merge_user_data` covers ratings, albums, manual tags and event). Emptying the quarantine deletes the catalog row, and `edits` rows cascade with it (`schema.py:563`).
- **Near-duplicate set-aside does not carry the sidecar** (`quarantine_similar` never calls `carry_sidecar`).
- **A full verify reads every byte.** A large library takes hours, which is why it is a pausable job.
- **Only Windows paths are tested here.** Rename, Recycle Bin and share-offline codes are Windows behaviour (`hashing.py:24`, `manage.py:197`).

# Discrepancies

Where the code and its docstrings, CLAUDE.md or the CHANGELOG disagree, this document follows the code.

- **Candidate rule.** `dupes/detect.py:5-7` and the page text "same size, matching samples" (`ui/dupes_view.py:241`) say candidates share a size. The code also requires the same capture time or no capture time (`detect.py:106-142`), as CLAUDE.md says.
- **Near-duplicate rules.** The docstring `similar.py:14-29` and CLAUDE.md ("Near-duplicate rules") list "same moment" and "near-blank skipped" as absolute. The code lets same-name copies 61 s to 36 h apart match at up to 16 bits and skips the moment test (`similar.py:197-242`), and lets a near-blank picture pair through the same-name rule. Neither description mentions this.
- **Unused code.** `TIME_TOLERANCE_S = 2.0` (`similar.py:49`) and `_t()` (line 106) are defined and never used.
- **Keeper docstring.** `similar.keeper_rank` documents "not damaged, then the most pixels, ..." but the code ranks "is an edit" second (`similar.py:356`). The note on the near-duplicate tab (`near_view.py:105-108`) omits it as well.
- **Takeout test differs between the two tracks.** The exact-copy keeper treats the word "takeout" anywhere in source path plus file path as Takeout (`detect.py:243`). The near-duplicate track uses `takeout_roots`, which looks only at the source's own folder name or a `Google Photos` folder inside it (`takeout.py:220-234`). CLAUDE.md says only the source's own name counts. The Settings help ("never a Google Takeout copy") describes the exact track.
- **Suggestion by "exact name".** CLAUDE.md says a copy with "the keeper's exact name" is suggested. The code compares the file stem, lowercased and without extension (`_stem`, `similar.py:297`), so `IMG_1.png` matches `IMG_1.jpg`.
- **Quarantine location.** `move_one`'s docstring (`quarantine.py:112-117`) mentions the "Lunelis folder's Duplicates / Trash" and cross-drive copies. `quarantine()` and `quarantine_similar()` only ever build a path under the file's own source (`quarantine_paths`), so a normal set-aside never crosses drives. The cross-drive branch serves the migration engine.
- **"Network" in the emptying rules.** CLAUDE.md and the `manage.py` docstring say network files are deleted and local files recycled. `_is_network` is really "has no Recycle Bin" (`paths.has_recycle_bin`): it is also true for USB sticks, cards and FAT or exFAT volumes, and for every non-Windows system.
- **The page labels a group "Verified" by its method, not its flag.** `load_groups` passes `method == "exact"` to `Group` (`dupes_view.py:96`) and ignores the `verified` column it just read. After migration 41's trigger clears `verified` on a changed file, the page still says Verified and offers Set aside, but `quarantine()` refuses it. The verify job only handles `sampled` groups (`engine.py:75`), so nothing re-verifies it. CHANGELOG 0.37.3 states that such a group "stops counting as verified". I confirmed the trigger and the refusal on a scratch catalog; the page itself I could not run.
- **A likely group can disappear without being promoted.** The page hides a `sampled` group whose members all have a `content_hash` (comment: "already represented by its verified group", `dupes_view.py:92`). A full hash is also written by Hash everything, integrity and backup runs. If those ran first, nothing creates the exact group: `verify_duplicates` only looks for members whose `content_hash` is empty (`main_window.py:3010-3018`). Read from the code, not reproduced.
- **The verify job ignores the Settings defaults.** `verify_duplicates` calls `create_job` with no options (`main_window.py:3017`), so it runs "now" with no speed limit. The button's tooltip says it "can pause and run at night" (`dupes_view.py:265`); no dialog offers a schedule. "Verify this group" has no throttle either.
- **Snapshot folder.** Both Duplicates tabs snapshot into `paths.BACKUP_DIR` (`dupes_view.py:194,469`; `near_view.py:65`), not into the folder chosen by `catalog_backup_dir` or the Lunelis folder that `catalog.backup.backup_dir` would return.
- **Sample-collision wording.** The `_verify_folder` docstring says a fluke is "dissolved". The group row stays in the catalog; it is only hidden from the page.
- **Request checking differs.** `quarantine()` raises when a requested file is not a live member; `quarantine_similar()` silently ignores such ids (`similar.py:374`).
- **Settings comment drift.** The comment for `trash_keep_days` sits at the end of the `similar_grouped_count` line (`settings.py:88-89`), the drift CLAUDE.md warns about.
- **Page text about speed.** The Settings help for "Speed limit" says it caps "how fast jobs read"; the single-group verify and the near-duplicate steps do not read through the throttle.

# To check

- **Hash first, then duplicates.** Run Hash everything or a backup, then Find duplicates, in the real app with PySide6. Does a pair of copies appear on the Duplicates page, and by what route does it become a verified group? (`dupes_view.py:92`, `main_window.py:3010`)
- **Verified label after a file changes.** Change a file in a verified group on disk, rescan, and open the page. Confirm it says Verified and that Set aside fails with the refusal message.
- **Is "Keep this one" meant to survive verification?** Mark a keeper on a likely group, verify, and see whether the mark is gone (`detect.py:193-199`).
- **Videos in near-duplicates.** `find_groups` excludes only RAW files, and videos get poster-frame thumbnails and fingerprints. I did not test whether video pairs group, or whether that is intended (`similar.py:121`; `raw/thumbnails.py`).
- **Same-name false positives.** How often do two different cameras produce the same file name inside 36 hours with a fingerprint under 16 bits apart? Needs a run on a real catalog copy opened with `immutable=1`.
- **Sidecars of near-duplicates.** Is it intended that `quarantine_similar` skips `carry_sidecar`? CHANGELOG 0.37.5 describes the fix for "the Duplicates page" only.
- **Edit stacks.** Confirm in the app that an edit made on a set-aside copy is lost after emptying the quarantine.
- **The quoted measurements** (424 s, 17.8 GB, 91,000 files, ~12x, 13,510 groups, 689.7 GB, 4,975 groups, 18.2 GB, group sizes 3,126 / 1,291 / 885 / 6) come from CLAUDE.md and the CHANGELOG. I did not re-measure them. The 7,600 in the first chart is 91,000 divided by 12, not a measurement.
- **"Find again ... about a minute"** (`near_view.py:152`) is page text, not measured here.
- **Test suite.** `pytest` and PySide6 are not installed in this environment, so I did not run `tests/test_dupes.py` or `tests/test_similar.py`. I ran `same_photo`, `shifted_copy`, `_chunks` and `quarantine()` directly instead.
- **Windows-only paths.** Recycle Bin calls, `GetDriveTypeW` checks and the network error code list (`hashing.py:24`) were read, not run.
- **Cross-drive `move_one`.** Not exercised by the duplicates pages; confirm that no path in the Duplicates tabs reaches it with different volumes.
