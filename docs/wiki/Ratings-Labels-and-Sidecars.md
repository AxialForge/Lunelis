# Ratings, labels and sidecars

## Rating photos

Select one or more photos and press:

| Keys | Sets |
|---|---|
| `0` - `5` | Star rating (0 clears it) |
| `6` / `7` / `8` / `9` | Red / Yellow / Green / Blue label - press again to remove it |
| `P` | Pick |
| `X` | Reject |
| `U` | Unflag |

The same options are in the **Photo** menu and on right-click. Purple is in
the menu (it has no key, as in Lightroom and darktable).

**Undo:** `Ctrl+Z` takes back the last rating, label or flag change (up to 20
steps). Changing more than 500 photos at once asks first.

## Where ratings are stored

Every rating is saved instantly in the catalog, and about a second after you
stop rating it's also written to an **XMP sidecar** - a small standard file
that other programs understand. That way your ratings are never locked inside
Lunelis.

The **sidecar location** setting decides where:

| Setting | Your photo folders | darktable/Lightroom see Lunelis ratings? |
|---|---|---|
| **Central Lunelis folder** (default) | Stay clean - nothing is added | Only where a sidecar already exists (see below) |
| Next to photos | Gets `.xmp` files | Yes |
| Catalog only | Stay clean | No |

In central mode the sidecars are real XMP files in a mirror of your folders
under `%LOCALAPPDATA%\Lunelis\sidecars\` - one folder to back up.

**Update sidecars that already exist next to photos** (on by default) keeps
darktable's and Lightroom's existing sidecars in step, without ever creating
new files in your folders.

## Tags

[Tags](Tags.md) go into the same sidecar as `dc:subject` and
`lr:hierarchicalSubject`, which darktable and Lightroom both read. Tags from
a sidecar are only ever added in Lunelis, never removed.

## Edits

A photo's edit is written to the same sidecar as `lunelis:EditStack`, in
Lunelis's own namespace, and nothing else in the file changes. Adobe's
`crs:` develop settings are never written, so a Camera Raw sidecar keeps
its own edit. See [Editing](Editing.md#where-edits-are-stored).

## darktable, Lightroom and culling tools

When a folder is scanned, Lunelis **reads** the ratings and colour labels in
sidecars that are already there:

- darktable's `photo.ARW.xmp`
- Adobe-style `photo.xmp` (Lightroom, Bridge, many culling tools) - for a
  RAW+JPG pair it belongs to the RAW, as in Adobe's convention

If you change a rating in darktable later, the next rescan picks it up. If
you changed the same photo in Lunelis and that change hasn't been written out
yet, your Lunelis change wins.

When Lunelis writes to an existing sidecar it changes **only the rating and
label** - darktable's edit history and everything else in the file stay
byte-for-byte as they were.

- A reject is stored as rating `-1` (darktable and Adobe agree on this).
- **Picks** have no standard XMP field, so they live in the catalog only.

## Why darktable can't see the central folder

Programs find a sidecar purely by name and location: *same folder, same
name, `.xmp`*. Nothing inside a photo points anywhere else, and Lunelis never
modifies photos. A darktable plugin that reads the central folder is on the
[roadmap](Roadmap.md).
