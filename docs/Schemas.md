# Locked formats

Formats other work builds on. Locked in 0.16 (2026-10-03): a change to any of
them needs a version bump inside the format (or a catalog migration), a
loader that still reads the old form, and an update to this page and to
`tests/test_schemas_locked.py`, whose golden values pin them.

## 1. The edit stack

Stored per photo in `edits.stack` (catalog) and in XMP as `lunelis:EditStack`.
Never written into the photo itself. Code: `edit/stack.py`, `edit/masks.py`.

```
v=1;f=Vivid@40;exposure=0.3;contrast=12;rotate=90;flip_h=1;angle=1.5;
crop=0.1,0.05,0.9,0.95;curve=0,0 0.5,0.6 1,1;lens=1;lens_distortion=10;
mask=radial|0.5,0.5,0.22,0.28,0.5||exposure:0.6|
```

(one line in practice). Rules:

- `;`-separated `key=value` fields; `v=1` first. Order is fixed (equal stacks
  give equal text), and only non-default values are written - an empty
  stack is `v=1` and means "the original".
- `f=<filter>@<amount 0-100>`: a built-in or user filter, scaled by amount.
- Adjustments: one field per `edit/stack.PARAMS` key, in that order.
- Geometry: `rotate` (0/90/180/270), `flip_h`, `flip_v`, `angle` (straighten,
  degrees), `crop` (left, top, right, bottom as fractions).
- Curves: `curve` (RGB), `curve_r` / `curve_g` / `curve_b` - control points
  `x,y` in 0..1, space-separated.
- Lens: `lens=1` (use the profile) and `lens_<distortion|vignette|ca_red|ca_blue>`.
- Masks: one `mask=` per mask, in order:
  `kind|shape numbers|inv flag|key:value,...|strokes`.
- **Unknown keys are ignored** when reading, so a newer Lunelis's stack opens
  in an older one (minus what the older one can't do).

New tools (healing, presets, virtual copies) add new keys; they never change
the meaning of an existing one.

## 2. Tags: source and confidence

Tags are full-path names (`Places|Ohio`, shown as Places > Ohio) in `tags`;
`file_tags (file_id, tag_id, confidence)` links them to photos.

| `confidence` | Source | Meaning |
|---|---|---|
| NULL | user | A tag you gave the photo, or accepted from a suggestion. Authoritative. |
| 0.0 - 1.0 | auto | A model's suggestion (scene tagging, 0.20). Filterable, removable, never overwrites a user tag. |

- Only user tags are written to XMP (`dc:subject`, `lr:hierarchicalSubject`),
  so unreviewed suggestions never reach sidecars or other programs.
- Accepting a suggestion sets its confidence to NULL; rejecting deletes the row
  and remembers the rejection so it isn't suggested again.
- Model suggestions live under a top-level `Scene` tag (`Scene|Beach`).
- **Embeddings** (0.20): a new table `embeddings (file_id PRIMARY KEY, model TEXT,
  dim INTEGER, vector BLOB float32, made_at TEXT)`, one row per photo per model,
  made from the cached 512 px thumbnail. A different model is a different
  `model` value, never a re-interpretation of old vectors.

## 3. Camera profiles

`importing/camera_profiles.json` (built in) plus `<data folder>/camera_profiles.json`
(yours; the same `id` replaces a built-in). Code: `importing/profiles.py`.

```json
{"profiles": [{
  "id": "sony", "name": "Sony", "makes": ["SONY"],
  "markers": ["PRIVATE/M4ROOT", "DCIM/*MSDCF"],
  "media_dirs": ["DCIM", "PRIVATE/M4ROOT/CLIP", "PRIVATE/AVCHD"],
  "skip_dirs": ["PRIVATE/M4ROOT/SUB", "PRIVATE/M4ROOT/THMBNL"],
  "sidecars": [{"for": ["mp4", "mxf"], "names": ["{stem}M01.XML", "{stem}.XMP"]}],
  "companions": [],
  "note": "..."
}]}
```

- `markers` (paths, `*` wildcards) identify the card; the first matching
  profile wins; `generic` is always last.
- `media_dirs` are read recursively; a source with none of them is read whole.
- `skip_dirs` are never read.
- `sidecars`: for files with these extensions (`*` = any), these neighbours
  (`{stem}` = name without extension, `{name}` = full name) are copied,
  verified and filed **with** their file, never on their own, never renamed.
- `companions` (0.18, optional, same shape as `sidecars`): neighbours that are
  photos or videos themselves - an iPhone Live Photo's `{stem}.MOV` beside its
  `.HEIC` / `.JPG`. They're cataloged like any photo, but filed in the same
  folder as their photo, always: if either name is taken there, both go to
  the sibling folder. A different file already beside the photo keeps the
  companion on the card (never "safe to format").
- `makes` match EXIF Make: a folder with no card layout (copied off a phone)
  gets the profile of its photos' Make.

## 4. Keeper

A **keeper** is a photo flagged **Pick** (`ratings.flag = 'pick'`). Stars don't
count. Used by Your shooting stats (0.21) and Learn My Look / Autopilot
(0.27-0.28), and shown on any page that uses it.
