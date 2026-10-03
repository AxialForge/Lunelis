# Safety and backups

## What Lunelis will and won't touch

| | |
|---|---|
| **Never** | Modifies, renames or deletes your photos and videos |
| **Never** | Puts its own files (catalog, thumbnails, backups) in your photo folders |
| **Never** | Sends anything over the internet - no accounts, no telemetry |
| **Only when you ask** | Moves duplicate copies into `_Lunelis Quarantine` (a rename on the same drive) |
| **Only if you choose it** | Writes `.xmp` sidecars next to photos (the default keeps them in a central folder) |
| **Only existing ones** | Updates rating/label in sidecars darktable or Lightroom already made (can be turned off) |

## The Library status page

**Keep safe > Library status** shows what Lunelis is doing to the library
and how the library is:

- **Updating the library:** the nine steps of every scan, with a tick when
  done, the running step's progress, and **Rescan everything** / **Stop**.
- **At a glance:** photos and videos, archived, missing, damaged, waiting
  for thumbnails or metadata, duplicates to review, quarantine, the last
  catalog backup and photo backups - each a link to the page that deals
  with it.
- **Sources:** every folder Lunelis catalogs, whether it is reachable right
  now, its size, missing files and last scan, with **Rescan** for just that
  folder.

The status bar's library state and scan step link here.

## Automatic catalog backups

Picks, flags, job history and (later) albums and faces live only in the
catalog, so Lunelis backs it up:

- **once a day** when it starts, and
- **before any job that moves files** (e.g. quarantine).

Backups are zipped - a 159,000-photo catalog is about 20 MB - and the newest
10 are kept in `%LOCALAPPDATA%\Lunelis\backups\`. The folder, how often and how
many are all in [Settings](Settings.md#catalog-backups), along with **Back up
now** and **Restore a backup...**.

### From the command line

```bash
python -m lunelis.catalog.backup                 # list backups
python -m lunelis.catalog.backup --now           # take one now
python -m lunelis.catalog.backup --restore catalog-20260927-081500-daily.zip
```

Close Lunelis before restoring. The catalog being replaced is kept next to it
as `catalog.db.before-restore-<time>`, so a restore can itself be undone.

## Moving the data folder

The data folder defaults to `%LOCALAPPDATA%\Lunelis`. To use another drive,
choose **Settings > Data folder > Move...**. Lunelis restarts and moves
everything, copying and checking every file before the old folder is removed
(see [Settings](Settings.md#data-folder)). It has to be a drive in this PC,
not a network share.

The choice is recorded in `%APPDATA%\Lunelis\location.json`.

## Backing up your photos

Catalog backups protect your ratings and events, not your photos. To back up
the photos too, onto a USB drive, stick or network folder, see
[Backups](Backups.md).

## What to back up yourself

Your photos are your existing backup system's job. For Lunelis, back up
`%LOCALAPPDATA%\Lunelis\backups\` and `sidecars\`; the thumbnail cache can
always be rebuilt.
