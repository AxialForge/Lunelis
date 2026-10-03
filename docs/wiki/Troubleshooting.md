# Troubleshooting and FAQ

## Something went wrong

Choose **Help > Report a problem...** and press **Copy**, then send it along
with what you were doing. It holds versions, your folders and the recent log,
but no photos. The log itself is in `%LOCALAPPDATA%\Lunelis\logs\` (Help >
Open the log folder). If Lunelis hits an unexpected error, it says so once and
keeps running. The details are always in the log.

**A tile says "Preview pending".**
Its thumbnail hasn't been made yet. It fills in by itself while a scan runs;
otherwise press `F5`.

**A tile says "Preview unavailable".**
The file couldn't be read as an image. Check the
[Damaged files](Damaged-Files.md) page - it may be empty, zero-filled or corrupt.

**Photos sort in the wrong place / at the top of "newest".**
They have no capture date, so they sort by file date. In a Google Takeout
export Lunelis uses Google's JSON dates instead; undated Takeout files sort
last. See [Videos and Google Takeout](Videos-and-Google-Takeout.md).

**A network folder shows as unavailable.**
The NAS may be asleep or the share disconnected. Open it once in Explorer and
press `F5`. Lunelis never marks photos missing because a share was offline.

**A job says "Waiting for ... to come back online".**
Its network folder dropped out. It retries every minute and carries on by
itself; nothing is lost.

**I moved photos in Explorer. Did I lose their ratings?**
No - the next rescan recognises moved and renamed files and keeps their
ratings. See [Sources and scanning](Sources-and-Scanning.md#moved-and-renamed-files).

**darktable doesn't show the ratings I set in Lunelis.**
In the default central mode, Lunelis only updates sidecars darktable already
has. See [Ratings, labels and sidecars](Ratings-Labels-and-Sidecars.md#why-darktable-cant-see-the-central-folder).

**Where did quarantined duplicates go?**
Into a `_Lunelis Quarantine` folder at the top of the same source, keeping
their folder structure. The [Quarantine](Quarantine.md) page lists them,
puts them back with **Restore**, and empties the quarantine once you're
sure (local files go to the Recycle Bin).

**I made a mistake - can I undo it?**
- A quarantined file: move it back from `_Lunelis Quarantine`, or restore it
  from the catalog.
- The catalog: restore an automatic backup - see
  [Safety and backups](Safety-and-Backups.md#automatic-catalog-backups).

**Canon CR3 files have no metadata.**
A CR3 reader isn't built yet; the files are cataloged and will be read once it is.
