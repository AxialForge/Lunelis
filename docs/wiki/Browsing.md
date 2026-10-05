# Browsing the library

The **Library** page shows every photo and video from all your sources in
one grid.

## The sidebar and the status bar

- **Sidebar:** every page, in three foldable groups. **Collapse** (above
  Settings), **Library > Sidebar: icons only** or `Ctrl+B` folds it to a
  narrow strip of icons - rest the mouse on one for its name - and gives the
  photos more room. Lunelis remembers the choice.
- **Status bar:** while Lunelis updates the library it shows the step (for
  example *Step 3 of 9 · Reading metadata*) and a progress bar. On the right
  it always shows the library's state - *Last scanned Oct 1, 8:01 PM* or
  *Library up to date* - and a link to the Damaged files page when there are
  any.
- **Small or scaled screens:** the window goes down to about 900 x 350; pages
  that don't fit scroll. Below 1100 px wide the sidebar folds to icons by
  itself (Settings > Appearance can turn that off); the window's size and
  place are remembered.
- **Keyboard shortcuts:** press `?` for every shortcut, the current screen's
  first.
- **Right-click** a photo for the Photo menu; it selects that photo first. The keyboard's
  menu key (or Shift+F10) opens it too.
- **Space** puts the photo under the cursor in or out of the selection.
- **Drag photos out** of the grid into Explorer (or any program) to copy them.

## The grid

- Square thumbnails with the file type in the top-right corner (`ARW`, `JPG`,
  `MP4`...).
- Bottom-left: the star rating, or the length for videos (`▶ 1:53`).
- Bottom-right: `PICK` / `REJECT` badges and a colour-label dot.
  Rejected photos are also dimmed.
- Grey tiles: **Preview pending** (still being made), **Video** or
  **Preview unavailable** (the file couldn't be read - see
  [Damaged files](Damaged-Files.md)).

The grid only ever draws what's on screen, so scrolling is smooth even with
hundreds of thousands of photos.

**Hover info:** rest the mouse on a photo for half a second to see its date,
camera, lens, exposure, size, rating and event. You can turn this off in
Settings > Appearance.

## The timeline

When the library is sorted by date, a timeline replaces the scroll bar on
the right, with years and a tick for each month. Hover over it to see which
month is there, and click or drag to jump; it snaps to the start of each
month. A small bar shows where you are. Photos without a date are at the end,
under "Undated".

## Looking at one photo

Double-click a photo, or select it and press Enter, to open it large:

- **The photo** fills the space. RAWs show their full-size embedded
  preview, so a 60 MP ARW appears in well under a second. It shows the
  thumbnail first and sharpens a moment later, and the next and previous
  photos are loaded ahead so stepping through is instant.
- **Zoom:** the **mouse wheel** zooms in and out around the pointer, from
  fitting the window up to 400 %. Double-click or **Z** jumps between fit
  and 100 %. Drag to look around (the left button, or the middle button
  anywhere). Past the preview's own resolution, the full-resolution photo
  loads in the background (a 60 MP RAW takes about 2.5 seconds). The zoom
  level shows at the bottom left. **Settings > General** can make the
  wheel change photo instead; then **Ctrl + wheel** zooms.
- **Filmstrip** along the bottom: click a thumbnail to jump, or scroll it.
  Drag the divider above it to make it (and its thumbnails) bigger or
  smaller. The size is remembered.
- **Info panel** on the right: name, type and size, clickable stars, colour
  labels and Pick/Reject, then date, camera, lens, exposure, dimensions,
  event (click to see the whole event), location (with a map link),
  condition if the file is damaged, where the file is, and its sidecar.
  **Shooting details** lists what the camera recorded: mode, metering,
  focus and drive mode, flash, white balance, picture style,
  stabilization, DRO/HDR, 35 mm focal length, time zone, serial number,
  firmware, artist and copyright. **All metadata** unfolds every tag in
  the file.
  Below that are **Show in folder** and **Open with default app**.
- **Videos and animated GIFs play** in the photo view - see
  [Videos](Videos-and-Google-Takeout) for the player keys and trimming.
- **Previous / next:** Left/Right, the filmstrip, or tilt the mouse wheel
  left/right (on mice that have a tilting wheel).
- **Keys:** Home/End,
  0-5 stars, 6-9 labels, P/X/U, Z to zoom, Esc to go back to the grid.

Stepping follows the library exactly: the same order and the same filters.

## Map and On this day

- **Map** (sidebar > Photos) places every photo with a location. Crowded
  places become numbered circles; click one to see those photos in the
  library. Drag to move, mouse wheel or + / - to zoom, **Fit** shows
  everything. Without the internet the dots sit on a plain grid of latitude
  and longitude; **Show map tiles (online)** brings in OpenStreetMap's map
  pictures (only tile numbers are asked for - nothing about your photos is
  sent - and they're kept in the data folder after the first time).
- **On this day** shows what you shot on today's date in other years, one row
  per year with a strip of photos and **Show all**. Step a day with ‹ / ›,
  pick any date, or tick **Within 3 days** for the week around it.

## Sorting

| Sort | Order |
|---|---|
| Date (newest / oldest) | Capture date. Files without one use their file date - except inside a Google Takeout export, where file dates are meaningless; those go to the end as undated. |
| File name | A-Z |
| File size | Largest first |
| Recently imported | Newest additions first |

**Grid size** (toolbar slider, or **Ctrl + mouse wheel** over the grid) makes
tiles bigger or smaller. Tiles resize smoothly while you drag, and sharpen
once you let go. The library remembers the size and the sort.

## Search

The box at the top finds photos by name, folder, tag, camera, lens,
event, album and date. See [Search](Search.md).

## Filters

The filter bar narrows the grid:

- **Rating** - ★1+ to ★5, or *Unrated*
- **Label** - Red, Yellow, Green, Blue, Purple
- **Flag** - Picks or Rejects
- **Tag** - one tag and everything inside it (see [Tags](Tags.md))

Active filters show as chips; click a chip's ✕ to remove it or **Clear all**.
The count on the right always says how many photos match.

## Edited photos

Photos you've [edited](Editing.md) show an **EDITED** badge next to the
format badge, and their thumbnails show the edit.

## Burst stacks

Frames your camera fired in quick succession show as **one tile** - the
stack's cover - with a ❐ frame count and card edges peeking out under it.

- **Double-click** a stack to open it: the photo view's filmstrip steps
  through every frame.
- **S** (or **Photo > Stack > Open / close stack**) opens or closes the stack
  in the grid. Open frames have a coloured bar along the top.
- **Photo > Stack > Make this the stack cover** picks the frame that stands
  for the stack. The best-rated frame is the cover until you choose.
- **Photo > Stack > Unstack** shows the frames one by one for good.
- **Stack bursts** in the filter bar turns stacking off and on.

A burst is at least three shots from one camera in one folder, each within
a second of the last (measured to the fraction of a second when the camera
records it, otherwise in the same second). A RAW+JPEG pair counts as one
shot. Change both in **Settings > Library**. Stacks only change the grid:
albums, events, duplicates and backups still see every frame.

If a filter leaves the cover out (say, only 5-star photos), the stack shows
its first frame that's still in.

## RAW+JPEG pairs

A camera set to RAW+JPEG saves two files per shot (`DSC01234.ARW` and
`DSC01234.JPG`). Lunelis shows them as **one photo**, badged *ARW+JPG*, and
stars, labels, picks, albums, tags, events and the archive go to both.
Settings > Appearance turns this off. Only an exact pair (the same name in
the same folder, one RAW and one JPEG) is paired - never a guess.

## Culling

**Photo > Cull full screen (Ctrl+K)** goes through your selection (or
everything the library shows) full screen, from the keyboard: **P** pick,
**X** reject, **U** unflag, **0-5** stars, **6-9** labels, **← →** to move.
With auto-advance on (**A**), each pick or rating moves to the next photo.
**C** compares 2, 3 or 4 shots side by side - zoom (wheel or **Z**) and drag
move them all together, **Tab** chooses which one the keys act on. **Esc**
goes back to the library.

## Undo

**Ctrl+Z** takes back the last change - stars, labels, flags, tags, albums
(adding, removing, a new album), events, archiving, unstacking, and edits
pasted or reset on many photos - up to 30 steps. **Ctrl+Shift+Z** or
**Ctrl+Y** redoes it. The Photo menu says what Undo will undo.

## Selecting

| Action | How |
|---|---|
| Select one | Click |
| Add / remove one | `Ctrl`+click |
| Select a range | `Shift`+click, or `Shift`+arrow keys |
| Select all / none | `Ctrl+A` / `Esc` |
| Move | Arrow keys, `Page Up/Down`, `Home/End` |

Right-click the grid for the **Photo** menu (rating, label, flag).
