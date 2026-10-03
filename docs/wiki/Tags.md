# Tags

Tags are words you attach to photos, like "Beach", "Grandma" or
"Anime Expo 2026", so you can find them again later. A photo can have any
number of tags.

## Tagging

- **One photo:** open it (double-click). The Info panel has a **Tags**
  box: type a tag and press Enter. Commas add several at once. Click a
  tag's ✕ to take it off.
- **A selection:** select photos in the library and press **Ctrl+T**
  (Photo > Tags > Tag photos...). The dialog lists every tag the selection
  has; "(3 of 5)" means only some of them have it. Type to add a tag to all
  of them, and click ✕ to remove one from all of them. Your recently used
  tags are one click away.

Typing autocompletes from the tags you already have. Case doesn't matter:
"beach" and "Beach" are the same tag.

## Tags inside tags

Type `Places > Ohio > Cleveland` (or `Places|Ohio|Cleveland`) to file a tag
inside another. The parents are made for you. Filtering by **Places**
shows everything tagged with any place, and **Places › Ohio** shows
Cleveland, Columbus and everything else inside Ohio.

## Finding tagged photos

- The filter bar's **Tag** button lists every tag with a search box. Pick
  one to see its photos, and remove the chip to go back.
- **Photos > Tags** in the sidebar shows all your tags as a tree, with how
  many photos each has (counting the tags inside it). Double-click one to
  see its photos.

## Renaming, merging, deleting

Right-click a tag on the Tags page:

- **Rename...**: renames the tag everywhere, including the tags inside it.
  Renaming onto a tag that already exists merges the two.
- **Merge into...**: moves every photo with this tag onto another tag.
- **New tag inside...**
- **Delete...**: takes the tag, and the tags inside it, off every photo.
  The photos themselves aren't touched.

## Tags and other programs

Tags are written to each photo's XMP sidecar, following your
[sidecar settings](Ratings-Labels-and-Sidecars.md), in the two standard
places darktable and Lightroom read:

- `dc:subject`, a flat list of every level;
- `lr:hierarchicalSubject`, the nested paths (`Places|Ohio|Cleveland`).

Tags added in darktable (or another program) show up in Lunelis the next
time the folder is scanned. They're only ever added, never removed: a tool
that rewrites a sidecar without its keywords can't wipe your tags.
darktable's automatic tags (`darktable|format|arw` and the like) are kept
in the sidecar but never shown as tags.
