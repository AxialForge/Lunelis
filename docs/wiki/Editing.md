# Editing

Every edit in Lunelis is **non-destructive**. An edit is a short list of
instructions kept in the catalog and in the photo's XMP sidecar. The photo
file itself is never written, so the original is always one click away
(**Reset**).

## Starting

Open a photo (double-click it) and press **E**, or click **Edit** in the
top bar. The Info panel on the right becomes the **Edit** panel, and the
big picture shows your changes as you make them. Edit mode stays on while
you step to the next photo with the arrow keys or the filmstrip, so you can
work through a shoot in one go. **Done** (or **E**, or **Esc**) goes back
to the Info panel.

RAW files are decoded properly for editing (camera white balance, 16-bit),
not from their small embedded JPEG. The first open of a 60 MP RAW takes
about a second.

There's no Save button. Each change is saved a moment after you make it,
and the rest is saved when you leave the photo.

## The Edit page

**Photos > Edit** in the sidebar (or **Photo > Edit > Open in the Edit
page**) is a workspace for editing many photos in a row: the photo large,
the Edit panel full height, and the photos to work through in the filmstrip
underneath. It is always in edit mode; the arrow keys and the filmstrip move
to the next photo, saving as you go.

**Photos:** which photos are in the filmstrip -

| Choice | What it holds |
|---|---|
| Selected in the library | The photos selected in the Library (the default when there is a selection) |
| What the library shows now | Everything the Library grid shows, with its search and filters |
| Edited photos | Every photo that has an edit |
| Picks | Every picked photo |
| 4 and 5 stars | Your best photos |
| Recently imported | The last 30 days of imports |

Videos are left out: they can't be edited yet.

**Batch tools** along the top:

- **Copy this edit:** copies the photo's edit settings (as Ctrl+Shift+C).
- **Paste to all:** pastes them onto every photo in the filmstrip. Each keeps
  its own crop and rotation.
- **Reset all...:** takes every edit off them (asks first).
- **Export all...:** exports them all with the usual Export dialog.

Ratings, labels and flags (keys 0-9, P, X, U) act on the photo being
edited.

## The panel

| Section | What's there |
|---|---|
| Top | **Auto** (a starting point from the photo's histogram), **Reset** (back to the original), **Before** (show the original; `\` toggles it) |
| Filters | Every filter, previewed on this photo, plus an **Amount** slider |
| Crop & rotate | Rotate left/right, flip, **Crop**, aspect ratio, **Straighten** |
| Masks | Gradient, Radial, Brush, Subject and Sky masks, each with its own sliders |
| Lens corrections | The lens profile, plus manual distortion, vignetting and fringing |
| Light | Exposure, Contrast, Highlights, Shadows, Whites, Blacks |
| Tone curve | RGB and red / green / blue curves over the photo's histogram |
| Color | Temperature, Tint, Vibrance, Saturation, Hue |
| Effects | Fade, Vignette |
| Detail | Sharpening, Noise reduction, Noise detail, Color noise |

Every slider has a number box: click it, type a value and press Enter.
Double-click a slider (or its name) to put it back to 0. While you drag,
the preview renders at half size so it keeps up, and sharpens when you let
go. **Settings > Edit** can keep it sharp all the time.

Click a section's header to fold it away. Folded sections stay folded next
time; **Settings > Edit > Unfold every section** opens them all again.

The mouse wheel zooms the picture while you edit, and so do Z and
double-click. The middle mouse button pans, even while cropping or
painting a mask.

## Noise reduction

High-ISO photos get three controls in **Detail**:

- **Noise reduction** removes grain from the brightness. Lunelis first
  measures how noisy this photo actually is, so the same setting suits an
  ISO 100 photo and an ISO 6400 one. It then averages each spot with other
  spots that look alike (non-local means), which keeps texture such as
  fabric and hair where a blur would smear it.
- **Noise detail** puts some of the removed fine texture back (+), or
  smooths further (-).
- **Color noise** removes the coloured speckles in dark areas without
  greying thin coloured details (a gold chain stays gold).

It's worked out once for the photo and reused while you move other
sliders. A 60 MP export with noise reduction takes about 17 seconds longer.

**Undo / redo:** `Ctrl+Z` / `Ctrl+Shift+Z` (or `Ctrl+Y`) step through this
photo's changes.

## Crop and straighten

Click **Crop** (or press **R**). Drag the corners or edges, or drag inside
the frame to move it. Pick an **Aspect** (Original, 1:1, 4:5, 3:2, 2:3,
4:3, 16:9) to lock the shape, or Free. **Enter** finishes.

**Straighten** turns the photo by up to 45° either way, and trims it to
the biggest rectangle of the same shape that fits, so there are no empty
corners. Rotating or flipping keeps your crop on the same part of the
picture.

## Tone curve

Click on the curve to add a point, drag points to bend it, and
double-click a point to remove it. The two end points only move up and
down: lift the left one to fade the blacks, or lower the right one to dim
the whites. **RGB** bends every channel; **R**, **G** and **B** bend one
colour each, and the other channels' curves show faintly. **Reset**
straightens the channel you're on. Curves never cross over themselves, so
a darker tone can't come out lighter than a brighter one.

## Masks: local adjustments

A mask applies its own sliders to part of the photo, on top of everything
else. Add as many as you like:

| Mask | Where it applies |
|---|---|
| **Gradient** | Fully at the solid line, fading to nothing at the dashed line. Drag either end, or the middle handle to move it. |
| **Radial** | Inside an ellipse, fading out over **Feather** towards its edge. Drag the middle to move it and the edge handles to resize. |
| **Brush** | Where you paint it. Set **Brush size**, **Brush feather** and **Flow**; hold **Alt** (or tick **Erase**) to take paint away. |
| **Subject** | The main subject (people, animals, objects), found by a small AI model on this PC. |
| **Sky** | The sky, found the same way. |

Pick a mask in the list to edit it. Its sliders (exposure, contrast,
highlights, shadows, whites, blacks, temperature, tint, vibrance,
saturation, sharpening, noise reduction) appear below. **Invert** swaps
inside and outside, so an inverted Subject mask with less exposure and
saturation makes a subject stand out. **Show mask** (`O`) tints where it
applies in red. `Esc` puts the mask down. Masks stay put on the photo when
you change the crop.

### The AI models

The first Subject or Sky mask asks to download a free model, once:

- **Subject:** "silueta", 44 MB, from the rembg project on GitHub;
- **Sky:** "skyseg", 176 MB, from Hugging Face.

Each download is checked against a known checksum before it's used. After
that the models run offline on this PC, and your photos never leave it.
Finding the subject or sky takes about half a second per photo. The result
is kept in the cache (`cache\masks`).

## Lens corrections

**Use the lens profile** corrects the lens's barrel or pincushion
distortion, its colour fringing and its vignetting, using lensfun's free
database. The camera, lens, focal length and aperture come from the photo.
The panel says which profile it found. Lenses on about 98 % of the
library's photos have one, including every Sony FE lens in it.

It's off by default because camera JPEGs are usually corrected in the
camera already, so it's mainly for RAW files. The manual sliders
(**Distortion**, **Vignetting**, **Fringing red/cyan** and **blue/yellow**)
work on any photo, with or without a profile. The picture is scaled so
corrected edges never leave empty corners.

## Filters

A filter is a set of adjustments with one **Amount** slider (0-100 %).
The built-in ones are Vivid, B&W Classic, Filmic, Warm, Cool, Matte, Punch
and Soft. Each tile shows that filter on the photo you're editing.

- Filters and sliders add up. Applying a filter never locks the sliders.
- **Adjust sliders** moves the filter's values into the sliders below, so
  you can fine-tune each one.
- **Save as filter...** keeps the current look (the filter plus your slider
  changes, but never the crop) as a filter of your own. It's then available
  for any photo.
- Right-click one of your filters to delete it. Photos that use it keep
  their look, because its values move into their own sliders first.

**Settings > Import > Start new photos with** gives every newly imported
photo a filter as a starting point. Photos you've already edited are left
alone.

## Several photos at once

| Menu | Keys | What it does |
|---|---|---|
| Photo > Edit > Copy edit settings | `Ctrl+Shift+C` | Copies the photo's filter and adjustments |
| Photo > Edit > Paste edit settings | `Ctrl+Shift+V` | Applies them to every selected photo |
| Photo > Edit > Reset edits... | | Takes all edits off the selected photos |

Crops and rotations aren't pasted, because they belong to each photo.
Pasted photos render in the background ("Rendering edits..." in the status
bar), and their thumbnails update as each one finishes.

## In the library

Edited photos show an **EDITED** badge, and their thumbnails show the
edit. Lunelis keeps a rendered 2560 px copy of each edited photo in its
cache (`cache\edits` in the data folder), so browsing never re-runs the
RAW decode. The cache is safe to delete: it's made again from the edit.

## Exporting

**Photo > Export...** (`Ctrl+Shift+E`) saves the selected photos (or the
one on screen), with their edits, as new files:

- **Format:** JPEG (with a quality setting), or TIFF or PNG, both lossless;
- **Size:** full size, or 4096 / 2048 / 1600 / 1080 px on the long edge;
- **Metadata:** keep everything, keep everything but the location (GPS), or
  remove it all. Camera, lens, exposure and date come from the catalog, so
  exports of RAW files carry them too;
- **File names:** `{name}` (the original's name), `{date}` (capture date),
  `{n}` (001, 002...) and your own text. An existing file is never
  overwritten; the new one gets " (2)".

Exports run in the background with a progress window you can cancel.
Videos are skipped. A 60 MP RAW takes about 7 seconds at full size.

## Merging: HDR and panoramas

Select the photos in the library, then **Photo > Merge**:

- **HDR...** takes 2-9 bracketed exposures of one scene and blends them
  into one photo with detail in both the highlights and the shadows. Hand-held
  brackets are lined up first. The result looks natural; edit it like any
  photo.
- **Panorama...** takes 2-30 overlapping photos (overlap them by about a
  third, at the same exposure) and stitches them into one. By default it
  works at half size, which is faster and still about 3000 px per frame,
  and crops away the empty edges.

Lunelis asks where to save each result, starting in the last folder you
used, and never overwrites a file. Save as a 16-bit TIFF (the most room for
editing) or a JPEG. If you save into one of your library's folders, the
result is added to the library at once, with the first photo's date and
camera. The source photos are only read.

## Where edits are stored

In the catalog (`edits`), and in the photo's XMP sidecar as
`lunelis:EditStack`, next to the rating, following your
[sidecar settings](Ratings-Labels-and-Sidecars.md). Lunelis doesn't write
Adobe Camera Raw's `crs:` settings: sidecars from Camera Raw already exist
in the library, and overwriting their settings with Lunelis's would destroy
those edits. darktable and Lightroom don't read Lunelis edits. To take an
edit to another program, export the photo.

## Coming later

Layers for compositing, masks that follow colour or brightness ranges, and
a DNG option for HDR merges. See the [roadmap](Roadmap.md).
