# Settings

Open **Settings** at the bottom of the sidebar, or press `Ctrl+,`. Changes
are saved as you make them: there is no Save button to forget. A value
Lunelis can't use (such as a folder template with an unknown token) is shown
in red and not saved.

Settings are arranged in tabs across the top: **General · Appearance ·
Library · Import · Edit · Ratings & sidecars · Duplicates & jobs · Backups ·
darktable · Updates · Advanced**.

## General

- **Open Lunelis on:** the library, Albums, or wherever you left off.
- **In the photo view, the mouse wheel:** zooms in and out (default), or
  goes to the next/previous photo. Whichever it isn't: the arrow keys, the
  filmstrip and tilting the wheel always change photo; Z and double-click
  always zoom.
- **Dates look like:** `Jun 19, 2026 · 2:03 PM`, `2026-06-19 14:03`,
  `19 Jun 2026 · 14:03` or `6/19/2026 2:03 PM`.
- **Ask before quitting while an export, merge, import or job is still
  running** (on by default).
- **Tray and start-up:** below.

## Edit

- **Start new photos with:** the filter new imports start with (the same
  setting as on the Import tab).
- **While dragging a slider:** Fast (half-size preview while it moves) or
  Sharp.
- **Unfold every section** of the Edit panel.
- **AI models:** whether the Subject and Sky models are installed, with
  Download / Remove.
- **Caches:** the rendered edits and the subject/sky masks, with their
  sizes and a **Clear** button. They're made again whenever needed.

## Appearance

- **Theme:**
  - **Graphite:** light, the mockups' look.
  - **Midnight:** dark.
  - **High contrast:** black and white with yellow highlights.
  - **Follow Windows:** switches between Graphite and Midnight with Windows'
    own light/dark setting, even while Lunelis is open.

  Themes apply at once, with no restart.
- **Library view:** the default sort and thumbnail size (Small 120 px,
  Medium 180, Large 260, Extra large 360, or Custom), and whether videos
  show in the library. Changing the sort or the Grid size slider in the
  library also updates these.
- **Hover info, sidebar folding, RAW+JPEG pairs:** show photo info when the
  pointer rests on a photo; fold the sidebar to icons on a narrow window; show
  a RAW+JPEG shot as one photo.
- **Burst stacks:** whether bursts show as one tile, how far apart frames
  can be (default 1 s), and how many shots make a burst (default 3). See
  [Browsing](Browsing.md#burst-stacks).

## Scene tags (Library tab)

Download and turn on the scene model (once, checked against its fingerprint),
tag the whole library as a background job, choose whether new photos are
tagged after each scan, and open the suggestions to review. See
[Tags](Tags.md#scene-tags).

## Shoots, videos and the autopilot (Library tab)

- **After each scan, look for brackets, panoramas, focus stacks and
  timelapses** - see [Lunelis noticed](Lunelis-Noticed.md).
- **S-Log3 videos** - the built-in look, your own `.cube` LUT, or off. See
  [Videos](Videos-and-Google-Takeout.md#s-log3-footage).
- **Autopilot after an import** - which stages run. See
  [Importing](Importing.md#autopilot).

## Colour (Edit tab)

**Monitor profile** (off, or Windows' display profile) and a **Soft-proof
profile** (a printer or paper .icc) - see [Editing](Editing.md#colour).

## Thumbnails (Library tab)

This shows how many thumbnails there are and how many are still to make.
**Rebuild all thumbnails...** deletes Lunelis's thumbnail cache (never a
photo) and makes the thumbnails again.

## Sources

The folders Lunelis catalogs, with how many photos and videos each holds and
when it was last scanned.

- **On / off**: a source that's off is hidden and never scanned. Nothing on
  disk or in the catalog is deleted, so turning it back on brings everything
  back, ratings included.
- **Add a folder...**: the same as **Library > Add folder...** (`Ctrl+O`).
- **Skip a folder inside a source...**: Lunelis leaves that folder alone,
  for example an exports folder or an editing cache. Photos already cataloged
  there disappear from the library straight away, but their ratings are kept.
  **Stop skipping** brings them back at once and scans the folder for anything
  new.

## Importing from memory cards

Where card imports go and how they get there. See
[Importing from a memory card](Importing.md).

| Setting | What it does |
|---|---|
| Import into | The library folder imports are filed under. |
| Folders | The storage template, e.g. `{YYYY}\{M}-{D}-{YYYY}[ {import_name}]`. The example underneath updates as you type. |
| Staging folder on this PC | Where the card is copied first. The default is the `staging` folder in the data folder. |
| Keep this much free | Staging never takes the local drive below this. The rest of the card goes to the network staging folder instead. |
| Network staging folder | Where staging spills over to. It must be outside every source, so that half-imported files are never cataloged. Set it to **None** to pause the import instead. |

**File names are never changed.** The template only picks the folder.

## Ratings and sidecars

Where stars, labels and rejects are written as XMP sidecars (they are always
kept in the catalog too):

- **In Lunelis's own sidecar folder** (the default): photo folders stay clean.
- **Next to each photo**: darktable, Lightroom and culling tools see them.
- **Only in the Lunelis catalog**: no sidecar files at all.

When you switch, Lunelis offers to write your existing ratings to the new
place straight away. Otherwise only ratings you change from then on go there.

**Also keep sidecars that already exist next to photos up to date** keeps
darktable's and Lightroom's own sidecars current, without ever creating new
ones beside your photos.

**Sidecar folder**: changing it moves the existing sidecars along with it.
Lunelis refuses a folder inside one of your sources.

## Duplicates and background jobs

- **Keep the copy in**: when copies are identical, the one in this source is
  kept. Otherwise it's the copy in the shallowest folder, and never a Google
  Takeout copy if there's another.
- **New jobs start with**: the defaults the job dialog opens with. These are
  when to run (straight away, only while the PC is idle, or only between set
  hours) and a speed limit for reading from the NAS. Each job keeps its own
  choice. See [Background jobs](Jobs.md).

## Catalog backups

The backup folder, how often to back up (every 24 hours by default) and how
many backups to keep (10). The page also offers:

- **Back up now**.
- **Restore a backup...**: choose a backup and Lunelis restarts to put it in
  place. The catalog it replaces is kept alongside as
  `catalog.db.before-restore-...`.
- **Open folder**.

Keeping backups on a different drive, or on a network folder, protects them
if this PC's drive fails. Changing the folder moves the existing backups there.

## Data folder

Where the catalog, thumbnails, backups and sidecars live. **Move...** picks a
new place and restarts Lunelis to move everything:

- On the same drive, it's instant.
- On another drive, every file is copied and checked first, and the old
  folder is removed only when all of them have arrived. If the copy is
  interrupted, Lunelis keeps using the old folder and finishes the move at the
  next start.

The data folder has to be on a drive in this PC. The catalog can't live on a
network share, because the database needs features that network shares
don't provide reliably. Moving isn't possible while a card import is
unfinished, because its staged copies live in the data folder.

## Updates

- **Check for updates** looks at the public
  [Lunelis releases](https://github.com/AxialForge/Lunelis/releases) page. When
  a new version is there, you see what's new and **Download and install**.
- **Installing:** the download is checked against its published checksum.
  Lunelis then closes, swaps in the new version and opens again.
- **Your data is untouched:** the library, settings and thumbnails live in
  the data folder, so an update never touches them.
- **Nothing is overwritten:** the previous version is kept until the new one
  has started. If the swap can't happen (a file in use, or a program folder
  that needs administrator rights), the old version simply starts again.
- **Automatic check:** once a day at start-up (untick it to turn it off). A
  button in the status bar says when an update is waiting.
- **Skip this version:** stays quiet until the next one comes out.

## Log and problem reports (Advanced tab)

Lunelis writes what it does, and every error, to
`%LOCALAPPDATA%\Lunelis\logs\lunelis.log`. **Report a problem...** (also in
the Help menu) gathers versions, folders, library size and the recent log,
ready to copy. It contains no photos.

## Tray and start-up (General tab)

- **Keep Lunelis in the tray**: closing the window keeps Lunelis running and
  watching for memory cards. When this is off, closing the window quits.
- **Start Lunelis with Windows**: Lunelis opens in the tray when you sign in.
  This is off until you tick it.
