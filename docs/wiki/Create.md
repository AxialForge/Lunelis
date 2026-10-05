# Create

**Sidebar > Create** makes new files from your photos: animations, collages
and batch copies. Your photos are only read. Everything Create makes is a
**new file** in one folder, and nothing there is ever overwritten - a second
file with the same name gets " (2)".

## Where things are saved

The Create page shows the folder at the top. By default it's
`Pictures\Lunelis creations` (Windows' own Pictures folder, even if it's on
OneDrive). **Change...** picks another; **Open the folder** shows it in
Explorer. When a tool finishes, its message links to the new file and the
folder.

## Choosing the photos

Every tool starts with the same picker:

| Photos: | What it takes |
|---|---|
| Selected in the library | What you selected before opening Create |
| What the library shows now | The library's current search and filters |
| An album or event... | Pick one from the list |
| A folder... | A folder inside one of your sources, and the folders in it |
| Picks | Photos flagged Pick |
| 4 and 5 stars | Your favourites |
| Recently imported | The latest imports |

The photos appear as a strip. **Drag** to change the order (an animation
plays in this order); **untick** a photo to leave it out. Videos aren't
offered. Each photo is used **with its edits**.

## Animation

A burst or a few photos as a short animation.

| Setting | What it does |
|---|---|
| Make | **MP4 video** - the best quality and the smallest file. **WebP animation** - good quality, plays in browsers. **GIF** - plays everywhere, but only 256 colours a frame, so it's kept to 1080 px. |
| Speed | How long each photo is shown (20 ms to 5 s) |
| Loop | GIF / WebP: forever, or a number of times. MP4: how many times the photos are played in the video |
| Play forward, then back | 1 2 3 4 3 2 1... (a "boomerang") |
| Size | The long side: 720, 1080, 1440 or 2160 px |
| Quality | Higher is better and bigger (MP4 and WebP) |

The preview on the right plays at the chosen speed. All frames take the
first photo's shape; a photo of another shape is cropped to fill it (never
stretched). Up to 300 photos.

## Collage

Several photos on one picture.

- **Layout:** 2 side by side, 2 stacked, 3 in a row, 1 big + 2, 1 on top + 2,
  2 x 2, 1 big + 3, 1 on top + 3, 2 x 3, 3 x 2 or 3 x 3. The first photos in
  the strip fill the cells in order.
- **Shape:** square 1:1, portrait 4:5, story 9:16, wide 16:9, landscape 3:2,
  tall 2:3. **Size:** the long side, 1080 to 6000 px.
- **Spacing, Border, Corners:** the gap between photos, the margin round
  them and rounded corners - all scale with the picture, so the saved file
  looks like the preview. **Background colour...** for the gaps.
- **On the preview:** drag a photo onto another cell to **swap** them; drag
  inside a cell to **move** the photo in it; the **mouse wheel** zooms the
  photo under the pointer (up to 4x). Photos always fill their cell -
  cropped, never stretched.
- **Save as** JPEG, PNG or WebP.
- **Place the photos yourself:** tick it and the frames are yours to arrange -
  click a frame to select it, **Shift+drag** to move it, drag a **corner**
  to resize it, **Add a frame** for the next photo of the strip, **Remove
  frame** for the selected one. Frames may overlap; later ones sit on top.

The preview uses the thumbnails; the saved collage uses the full photos.

## Batch copies

Copies of many photos at once - always new files, in a new folder called
`Batch <date> <time>` inside the Create folder.

| Setting | What it does |
|---|---|
| Size and format | A preset: Original size, Large, Web, Email, Instagram square / portrait, Story, Facebook, Widescreen, Lossless PNG, Small WebP |
| Names | `{name}` the original's name, `{n}` 001 002..., `{date}` the capture date, and your own text - e.g. `Trip {n}` |
| Metadata | Keep all of it, keep it without the location, or strip it all |
| Watermark | Your text in a corner or the centre, its size, opacity and colour (it has a soft shadow so it reads on any photo) |

"Instagram" and "Story" presets **crop to fill** their exact size; the others
only shrink a photo to fit and never enlarge it.

A photo that can't be made (its file is missing, say) is skipped; the rest
are still made and the message says which ones failed.

### Your own presets

**Edit the presets...** opens `create_presets.json` in the Lunelis data folder
(made from the built-in presets the first time). Each preset has a `name`,
`width` and `height` (or `null` for the original size), `fit` (`inside` or
`fill`), `format` (`jpeg`, `png`, `webp` or `tiff`) and `quality` (1-100).
A preset with the same name as a built-in one replaces it. Save the file and
reopen Batch copies to use it.

## Contact sheet

The photos in a grid on Letter or A4 pages (portrait or landscape), 2-10
across, with any of their file name, date and stars underneath, a title and
page numbers. A **PDF** with every page, or a **PNG** per page. Made from
the thumbnails (with your edits), so even a thousand photos are quick.

## Timelapse

An interval shoot as a video: the photos in strip order, at 1-60 frames a
second, 720p, 1080p or 4K.

- **Deflicker** evens out the exposure jumps between frames over a window
  of frames you choose - a sunset still gets darker, the flicker goes.
- **Stabilise** takes out drift and knocks; the picture is enlarged just
  enough that the moved edges never show.

## Slideshow video

Photos one after another: 1-30 seconds each, with a **crossfade**, a **fade
through black** or a **cut**, an optional **slow zoom**, the whole photo on
black or cropped to fill, widescreen, square or vertical (for phones), and
**music** from any audio file (cut to the video's length and faded out).

## Before and after

Each photo as shot next to how you edited it: **side by side**, **one above
the other**, or a **slider** video or GIF where a line sweeps across. The
"before" keeps the photo's crop, so the two line up.

## Prints

Photos at real print sizes - wallet, 4 x 6, 5 x 7, 8 x 10 - at 300 dots per
inch:

- **On Letter or A4 sheets** (a PDF): as many as fit (two 4 x 6 a sheet, eight
  wallets), each turned to suit its photo, with cut marks.
- **One file per print** for a printing service: a JPEG at exactly the print
  size (4 x 6 = 1800 x 1200).

**Fill** crops the photo to the print's shape (like a lab does); untick it
for the whole photo with white borders.

## Focus stack, star trails, median stack

Three ways to turn several frames into one picture, each at 2048 px to full
size, saved as JPEG or TIFF:

- **Focus stack** - frames focused at different distances (macro, landscape
  foreground to horizon). They're lined up, and every part of the picture
  comes from the frame that's sharpest there.
- **Star trails** - night frames from a tripod; each pixel keeps its brightest
  value, so the stars draw arcs. (Not lined up: the stars are meant to move.)
- **Median stack** - the same scene shot three or more times; each pixel takes
  the middle value, so people walking through disappear.

## Panorama and HDR

**Panorama** and **HDR** on the Create page take the photos from the picker
to the same merge as **Photo > Merge** (blend settings, where to save), and
the result goes into the library.

[Lunelis noticed](Lunelis-Noticed.md)'s suggestions now build focus stacks and
star trails too.

## Stopping

Every tool shows its progress with **Cancel**. Cancelling an animation or
collage keeps nothing half-made; cancelling a batch keeps the copies already
finished.
