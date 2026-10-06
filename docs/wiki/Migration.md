# Migrate and consolidate

**Migrate** copies your library from all its sources onto **one** drive or
share, laid out with a storage template, with one good copy of each photo.
It can bring the NAS pools and a Takeout export together into a single,
tidy library.

Every file is copied and **checked against the original** before anything
else happens. Nothing is ever deleted: originals go to a
`_Lunelis Quarantine` folder on their own drive, and only once their copy is
verified. By default, originals stay exactly where they are until you say
you're happy with the new library.

Open **Migrate** in the sidebar.

## 1. Plan: a dry run

1. **What to move:** tick the sources.
2. **Where to:** choose the target. It must be outside your current sources;
   an empty drive or network share is ideal. It becomes a source itself when
   the migration starts.
3. **Folders:** pick a storage template. The default is your usual
   `{YYYY}\{M}-{D}-{YYYY}[ {import_name}]`. In a migration, `{import_name}`
   is the photo's **event** name, so event folders get named, e.g.
   `2024\6-17-2024 Myrtle Beach\`. A whole event is filed under its start
   date.
4. **How:**
   - **One copy of each verified duplicate.** Verify duplicates first on the
     Duplicates page. Unverified copies are still handled safely, see below.
   - **Leave damaged files behind when an intact copy exists.** The good copy
     goes instead. A damaged file that is the *only* copy still moves, flagged.
   - **Include videos.**
   - **Originals:** *keep them until I've reviewed the new library*
     (default), or *move each one to quarantine as soon as its copy is
     verified*.

Press **Preview**. Nothing is copied or moved. You see:

- how many files and how much data, and where they'll go (per year folder);
- duplicates and damaged copies that stay behind;
- files that **look like copies of others**;
- files whose name was already taken, which go to a sibling folder
  (`6-19-2026 (2)`);
- undated files, which go to `Undated`;
- whether the target has enough **free space**. **Start** stays greyed out
  if it doesn't.

The **Worth a look** list shows every file with something to note.

## 2. Start

**Start migration...** backs up the catalog, then runs in the background as
a job. Like other jobs, it can be paused, it survives a restart, it waits for
a sleeping NAS, and it follows the job defaults in
[Settings](Settings.md#duplicates-and-background-jobs).

For each file:

1. It's copied to the target, hashed while it's read.
2. The copy is read back and compared. If it doesn't match, the copy is
   removed and the original is left alone.
3. Its sidecar (darktable or Lightroom XMP next to it) goes with it.
4. The **same catalog entry** is pointed at the new copy, so ratings,
   labels, picks, events, metadata and the thumbnail all stay.
5. Only then is the original handled: kept (review mode), or renamed into
   `_Lunelis Quarantine\migration-<n>\...` on its own drive.

**File names never change.** A different photo whose name is taken goes to a
sibling folder. RAW+JPEG pairs always stay together.

### Unverified duplicates

Your two NAS pools hold many of the same photos. Files that look like copies
(same name, size and date) are planned to the **same place**. When the
second one arrives, the bytes are compared:

- **Identical:** it isn't copied again. Its rating and label are merged into
  the copy that moved, and its original is set aside with the rest.
- **Different:** it goes to a sibling folder.

## 3. Review, then release

In review mode the originals stay where they were. Lunelis won't catalog
them a second time, because their entries now live in the new library.
Browse the new library, and when you're happy press **Release originals...**
to move them into quarantine. The [Quarantine](Quarantine.md) page can put
them back, or empty the quarantine once you're sure.

## Moving only the Archive

Tick **Only photos in the Archive** (or use **Library > Move the Archive to a
drive...**) to move just your archived photos - see
[Albums](Albums.md#the-archive). Everything else stays where it is.

## Good to know

- Your first migration: verify duplicates first on the Duplicates page, and
  choose a target with enough space. The preview tells you both.
- **Try it small first.** Plan a migration of one folder-sized source (or
  only the Archive) into a test folder, look at the result, release, and
  restore from the Quarantine page once, before moving everything.
- **Space.** The target needs room for everything that moves (the preview's
  figure, plus 2 % and 1 GB). If the target is on the same drive or NAS volume
  as the sources, the originals still take their space too: until you release
  them, and then in `_Lunelis Quarantine` until you empty it.
- **Time.** Each file is read once, written once and read back once. From one
  NAS share to another through this PC on gigabit Ethernet, expect roughly 6-8
  hours per terabyte, so a few days for several terabytes. Pause it, or
  schedule it for nights in
  [Settings](Settings.md#duplicates-and-background-jobs).
- **What travels with a photo:** the photo, its XMP sidecar, and everything in
  the catalog (ratings, labels, tags, albums, events, faces, places, edits). When
  a duplicate stays behind and only it had an XMP sidecar, a copy of that
  sidecar goes beside the copy that moved.
- **What stays behind:** files Lunelis doesn't catalog - documents,
  `Thumbs.db`, camera `.XML`/`.THM` files, iPhone `.AAE` edits, Google Takeout
  `.json` files - and anything in a skipped folder. They stay in the old
  folders, untouched; look through those folders before you delete them
  yourself.
- Nothing is ever deleted by a migration. Only **Empty quarantine** removes
  files, and it asks first.
- Renaming an event's folder on disk will come later, as an optional move job.
