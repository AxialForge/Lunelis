# Migrate and consolidate

**Migrate** brings your photos and videos from all their sources (local
drives, NAS pools, a Google Takeout export) onto **one** drive or share, as a
single tidy library with one good copy of each photo.

Every file is copied and **checked against the original** before anything
else happens to it. File names never change. Nothing is ever deleted: once
its copy is verified, an original is moved aside into a Trash folder you
empty yourself. By default, originals stay exactly where they are until you
say you're happy with the new library.

There are two ways in:

- **Migration wizard...** (recommended) on the **Migrate** page: nine steps,
  one choice at a time. This page describes it.
- The **Migrate** page itself has the same choices on one screen, for a
  quick or partial migration (for example [only the Archive](#moving-only-the-archive)).

## What you end up with

Inside the target, Lunelis makes two folders:

```
<target>\Library\Photos and Videos\2024\6-19-2024 Air Show\Photos\...
                                                          \Videos\...
                                                          \Timelapse\18-00 (786 frames)\...
<target>\Library\Undated\Photos\...   \Videos\...
<target>\Lunelis\Trash\           originals set aside after their copy was verified
                \Duplicates\      extra copies that weren't kept
                \Damaged\         damaged files that were the only copy
                \Migration logs\  before / after inventories, manifest, report
                \Exports\, Backups\, Create folders ...
```

- **Library** holds only photos and videos.
- **Day folders** are named by date, plus the event's name when the day has
  one (`6-19-2024 Air Show`). A day with several events takes the first
  one's name. A photo in an event that runs past midnight is filed by the
  event's start.
- **Photos, Videos, Timelapse:** videos have their own folder; the frames of
  each timelapse get one folder, named by its start time and frame count.
- **RAW and JPEG** of one shot always land in the same folder, even when only
  one of them has a readable date.
- **Sidecars** (darktable or Lightroom XMP) travel beside their photo.
- **No date:** a photo whose name holds its time (phones and Google name
  files `20170808_174715.jpg`) is filed by that. Anything else goes to
  `Library\Undated`, unless you choose to file it by a believable modified
  date (step 3).
- **Damaged files:** when an intact copy of the same shot exists elsewhere,
  that copy moves and the damaged one is set aside. When the damaged file is
  the only copy, it is kept, but in `Lunelis\Damaged\<its source folder>`
  rather than in the Library.

## The wizard, step by step

Nothing on disk changes before step 8.

### 1. Sources

Tick every folder whose photos and videos go into the new library. For a
**Google Takeout** export, tick it here and then use **Open the Google Takeout
page...** to choose which Takeout items come. Items already in your library,
edited versions and numbered copies are listed there so you can leave them
out.

### 2. Target

Choose the empty drive, NAS dataset or folder for the new library. It must
be outside every source. Lunelis sets its Lunelis folder to `<target>\Lunelis`.

### 3. Layout

- **File a photo with no date by its modified date, when that date is
  believable:** off by default. "Believable" means after 1995 and in line
  with the other files in its folder; camera clocks that were never set are
  ignored.
- **Inside Photos:** one folder for the day (default), a folder per camera,
  or the folder each photo came from.

Below that you see where some of your own photos would go.

### 4. Duplicates

**Move one copy of each verified identical group** (on by default). Only
groups proven identical byte for byte count. The copy kept is the one with
your work on it (ratings, edits, tags, a sidecar), else the older file, and
never a Google Takeout copy. The other copies go to `Lunelis\Duplicates`,
after their ratings, labels, albums, tags and events are merged into the kept
copy. When only a set-aside copy had an XMP sidecar, a copy of that sidecar
goes beside the kept copy.

The step also says how many likely groups are still **unverified**. Verify
them first on the [Duplicates](Duplicates.md) page if you can. Unverified
copies are still handled safely: files that look like copies (same name,
size and date) are planned to the same place, and when the second one
arrives its bytes are compared. If they're identical, it isn't copied twice.
If they're different, it goes to a sibling folder (`6-19-2024 (2)`).

### 5. Leftovers

The files in the sources that aren't photos or videos, by type: documents,
`Thumbs.db`, camera `.XML` / `.THM` files, iPhone `.AAE` edits, Google
Takeout `.json` files. They stay where they are and are listed in the
migration logs. Sidecars are not leftovers; they travel with their photos.

### 6. Safety

- **Keep the originals until I've reviewed the new library** (recommended):
  the copy runs, the originals stay put, and you release them in step 9.
- **Move each original aside as soon as its copy is verified:** tidier
  sooner, but there's no review step.
- **The Trash keeps set-aside files:** forever, 30 days, 90 days or a year.
  After that they're *offered* for removal on the
  [Quarantine](Quarantine.md) page. Nothing goes without you saying so.

### 7. Dry run

The plan, worked out from your real files, with nothing moved: how many
files and how much data, where they go, duplicates and damaged files left
behind, how many are undated, names already taken, Takeout items left out,
the free space on the target and roughly how many hours of copying.
**Start the copy...** stays unavailable if the target doesn't have room.

### 8. Run

**Start the copy...** checks the free space again, backs up the catalog,
writes the *before* inventory (every file in every source), and runs the
migration as a background job. Its progress is on the **Jobs** button. You
can pause it, close Lunelis or let the NAS sleep, and it carries on where it
stopped.

For each file:

1. It is copied to the target under a temporary name, hashed while it's read.
2. The copy is read back and compared. If it doesn't match, the copy is
   removed and the original is left alone.
3. Its sidecar is copied the same way and compared byte for byte. If a
   *different* sidecar is already there, that file fails and its copy is
   taken back, so nothing is left half-done.
4. The **same catalog entry** is pointed at the new copy, so ratings,
   labels, picks, events, faces, places, edits and the thumbnail all stay.
5. Only then is the original handled: kept (review mode), or moved to
   `Lunelis\Trash\Migration <n>\<source folder>\...` with its original path.
   Where it is going is recorded *before* it moves, so a power cut can't lose
   track of it. Two sources with the same folder name get separate folders
   there.

If something goes wrong part-way:

- **The NAS sleeps or the network drops:** the job shows *Waiting for ... to
  come back online* and carries on by itself.
- **The target is full:** the job shows *Waiting - the target is full*. Free
  some space and it carries on; files aren't failed one by one.
- **Power cut or crash:** start Lunelis again and the job resumes. A
  half-written copy is never trusted; it is redone. Half-written temp files
  are cleaned up at the end.
- **One file can't be copied** (a path that's too long, a file in use): only
  that file fails and the rest carry on. Its duplicates are **not** set
  aside, because their kept copy never arrived.
- **An original that can't be moved to the Trash** (a busy share, a name
  clash) is tried again at the end of the run.
- **An original that changed since the plan** (or was edited since it was
  cataloged) isn't copied. Rescan and plan again for those.
- **Cancel** on the Jobs page stops the migration. What has moved stays
  moved and is recorded, and you can plan again.

### 9. Release

When the copy is done, use the new library for a while. Then **Check now**
runs the **accounted-for report**: every file in the *before* inventory is
checked against what happened to it (copied to the Library, an identical
copy in the Library, a sidecar that travelled, left in place as a leftover,
unticked on the Takeout page, and so on).

Anything **unaccounted for** blocks the release, including a source folder
that couldn't be read or a planned file that never arrived. The report says
what and why.

**Release the originals...** moves the kept originals into `Lunelis\Trash`.
Before each one goes, its Library copy is read back once more and compared
with the hash taken while copying. If it no longer matches, that original
stays where it is.

## The migration logs

`Lunelis\Migration logs\Migration <n>\` holds, for each migration:

| File | What's in it |
|---|---|
| `before.csv` | every file in the sources before anything moved |
| `unreadable.csv` | source folders that couldn't be read (should be empty) |
| `manifest.csv` | every planned file: where from, where to, its SHA-256, what happened, where its original went |
| `after.csv` | every file in the target afterwards |
| `report.csv`, `summary.txt` | the accounted-for report |

## Moving only the Archive

Tick **Only photos in the Archive** on the Migrate page (or use **Library >
Move the Archive to a drive...**) to move just your archived photos - see
[Albums](Albums.md#the-archive). Everything else stays where it is.

## Good to know

- **Try it small first.** Migrate one folder-sized source into a test folder,
  look at the result, release, and restore one file from the Quarantine page,
  before moving everything.
- **Check the dates first.** Day folders come from capture dates, so look at
  the dry run's *undated* count before you start. Canon CR3 files are dated
  from 0.51 on; a catalog made earlier re-reads them by itself.
- **Space.** The target needs room for everything that moves (the dry run's
  figure, plus 2 % and 1 GB). If the target is on the same NAS volume as the
  sources, the originals still take their space until you empty the Trash.
- **Time.** Each file is read once, written once and read back once. From one
  NAS share to another through this PC on gigabit Ethernet, expect roughly 6-8
  hours per terabyte, so a few days for several terabytes. Pause it, or
  schedule it for nights in
  [Settings](Settings.md#duplicates-and-background-jobs).
- **Without a Lunelis folder** (the Migrate page with none set), set-aside
  originals go to `_Lunelis Quarantine\migration-<n>\` on their own drive
  instead of `Lunelis\Trash`.
- **Nothing is ever deleted by a migration.** Only emptying the Trash on the
  [Quarantine](Quarantine.md) page removes files, and it asks first.
- **Leftovers** stay in the old folders, untouched. Look through those
  folders before you delete them yourself.
