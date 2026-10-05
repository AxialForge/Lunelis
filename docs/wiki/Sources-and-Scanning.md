# Sources and scanning

A **source** is a folder you added with *Add folder*. Lunelis catalogs every
photo and video underneath it and never moves, renames or edits them.

## What gets cataloged

| Kind | Formats |
|---|---|
| RAW | Sony ARW/SR2/ARQ, Canon CR2 (CR3 metadata coming), Nikon NEF/NRW, Fuji RAF, DNG, Panasonic RW2, Olympus ORF, Pentax PEF, Samsung SRW |
| Images | JPEG, HEIC/HEIF (incl. Sony `.HIF`), PNG, TIFF, WebP, BMP |
| Video | MP4, MOV, AVCHD (MTS/M2TS) |

Hidden and system folders (`$RECYCLE.BIN`, `@eaDir`, `#recycle`...) and
Lunelis's own `_Lunelis Quarantine` folders are skipped.

**Files are identified by their contents, not their name.** A JPEG named
`.ARW` (Google Takeout does this) is treated as the JPEG it is.

## Network folders (NAS)

Network paths work like local ones, and Lunelis is built for them: folder
listings are read in one round trip per folder, and metadata/thumbnails read
several files at once. On a real 159,000-file library over gigabit, a full
rescan takes under a minute.

If a source can't be reached (NAS asleep, drive unplugged), Lunelis says so
and **does not** treat it as empty - nothing is marked missing because a share
was offline.

### While a source is offline

Lunelis checks every source about once a minute. While one isn't answering:

- Its photos **stay in the library** - the thumbnails are kept on this PC -
  with an **OFFLINE** mark in the top-left corner of each tile.
- Opening one shows its thumbnail and says the folder isn't answering; the
  photo is safe where it is.
- The status bar says when a source goes offline and when it's back.
- If a drive or NAS stops answering in the middle of an export, merge, trim
  or Create tool, the message says so in words (and that nothing was changed)
  instead of a Windows error number.

## Rescanning

`F5` (**Library > Rescan all folders**) checks every source for new, changed
and removed files. Only what changed is read again.

- **New files** are cataloged and get metadata and thumbnails.
- **Changed files** (different size or date) are re-read.
- **Files that disappeared** are *flagged missing*, never deleted from the
  catalog - their ratings wait for them to come back.

## Turning sources off and skipping folders

In [Settings](Settings.md#sources) you can turn a whole source off, or have
Lunelis skip one folder inside a source, such as an exports folder. Either
way the photos disappear from the library while their ratings stay in the
catalog. Nothing on disk is touched, and switching back brings everything
back. A skipped folder isn't scanned, and its photos are never reported as
missing.

## Moved and renamed files

If you move or rename photos in Explorer, the next rescan recognises them at
their new location and **keeps their ratings, labels, metadata and thumbnail**.
Lunelis matches the missing file to the new one by:

1. same size and modified date (moves in Explorer keep the date),
2. then same size, capture time and name,
3. then the same content hash, if the file had been hashed.

Only a clear one-to-one match is linked; if two identical candidates appear,
Lunelis leaves both alone rather than guess.
