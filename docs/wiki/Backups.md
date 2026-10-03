# Backups

A backup mirrors the sources you choose onto a **USB drive, stick or network
folder**. After the first backup, only new and changed files are copied.
Every copy is checked, nothing is ever deleted from a backup, and the
catalog (ratings, picks, events) goes with it. A new PC can be set up from
the backup alone.

Open **Backups** in the sidebar.

## Making a backup

**New backup...** asks for:

- **Where:** a folder on a USB drive, stick or network share, outside your
  library.
- **What:** tick the sources. The dialog shows how much that is and how much
  space the drive has.
- **Back up automatically whenever this drive is connected** (on by default).

A removable drive is recognised by its serial number, so it doesn't matter
if Windows gives it a different drive letter next time.

## What's on the backup drive

```
<backup folder>\
  12-Photos\2026\6-19-2026 Air Show\DSC01234.ARW   your folders, as they are
  _Lunelis\backup.json                                      what this backup is
  _Lunelis\catalog\catalog-*.zip                            catalog snapshots (newest 5)
  _Lunelis\sidecars\...                                     Lunelis's sidecar folder
  _Lunelis\previous-versions\<time>\...                     older copies of changed files
```

It's plain files and folders. You can browse it, or copy things back by
hand, without Lunelis.

## How a backup runs

It runs as a background [job](Jobs.md): pausable, resumable after a restart,
waits for the drive or the NAS, and speed-limited if you set one.

For each file:

- **New:** copied, then read back and compared, and its fingerprint (hash)
  is stored. That also gives the file its integrity baseline in the library.
- **Unchanged:** skipped.
- **Changed:** the old copy moves to `_Lunelis\previous-versions`, then the
  new one is copied.
- **Moved or renamed in the library** (including by a [migration](Migration.md)):
  renamed inside the backup too, not copied again.
- **Deleted from the library:** stays in the backup.

The drive is never filled to the last byte. If it runs out of room, the
backup pauses and says so.

## Verify

**Verify** re-reads every copy on the backup and compares it with the
fingerprint taken when it was made. It catches a failing drive or bit rot
before you need the backup. Copies that don't match are listed, and the next
**Back up now** replaces them.

## Restore

**Restore...** offers three things:

1. **Bring back missing or damaged library files, to where they were.**
   - Each file is checked against its fingerprint.
   - A damaged file still in the library is moved to quarantine first,
     never overwritten.
   - A healthy file is never touched.
2. **Copy everything in the backup into a new folder.** Use this when a
   drive has died.
3. **Restore the catalog from the backup.** Ratings, picks and events go
   back to that point. Lunelis restarts to do it and keeps the current
   catalog alongside.

## Coming later

- Encryption, so a lost USB stick doesn't expose your photos.
- Several backup drives in rotation (already possible: make one backup set
  per drive).
