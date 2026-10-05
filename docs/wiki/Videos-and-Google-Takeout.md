# Videos and Google Takeout

## Videos

MP4, MOV and AVCHD videos are cataloged like photos:

- **Thumbnail** - a frame from near the start (skipping the often-black first
  frame), turned upright for portrait clips.
- **Length** on the tile (`▶ 1:53`).
- **Recording date** from the video file, converted to local time so videos
  sort among the photos from the same shoot.
- **Size and GPS** where the camera or phone records them.

Videos can be rated and labelled like photos.

### Playing a video

Open a video like a photo (double-click it). It plays in the photo view,
with a bar underneath:

| Control | Key | What it does |
|---|---|---|
| ▶ / ❚❚ | K | Play or pause |
| Slider | J / L | Move through the clip; J and L jump 5 seconds back or on |
| Sound on / Muted | | Turn the sound off and on |

Left / Right still go to the previous or next photo. A video stops when you
leave it, and pauses when you go back to the library.

### Trimming

1. Move to where the copy should start and click **Start here** (or press **I**).
2. Move to where it should end and click **End here** (or press **O**).
   Mark only one of them and the copy runs from the start or to the end.
3. Click **Save trimmed copy**.

The copy is a **new file** in the Create folder (see [Create](Create)),
named like `C0042 trim 0m12s-0m31s.mp4`; the original is never changed and a
second copy gets " (2)". The picture and sound are copied, not re-encoded, so
it's quick and nothing is lost - which means the copy starts on the
**keyframe** just before your start mark (usually within a second).

### Animated GIFs

GIFs are in the library too. An animated one plays when you open it, and
plays in its tile when you hold the pointer over it in the grid. A still GIF
is shown like any photo.

## Google Takeout exports

A Google Takeout export gives every photo a small JSON file with Google's
record of it: when it was taken, where, its description. For photos that
have no date of their own - Snapchat saves, screenshots, some re-encoded
uploads - that JSON is the only reliable date.

When a source is a Takeout export (its folder name contains "Takeout", or it
has a `Google Photos` folder inside), Lunelis reads the JSON files after the
metadata pass and:

- fills in the **date** and **location** for files that don't have one;
- **never overrides** a date or location the photo itself carries;
- keeps the description and people names for later features.

It copes with Takeout's quirks: JSON files moved into album folders, Google's
shortened file names, numbered copies (`IMG(2).jpg`), `-edited` versions, and
the same name in several month folders. If it can't tell which photo a JSON
belongs to, it leaves the date empty - a wrong date is worse than none.

Files in a Takeout export that still have no date sort at the **end** of the
date order, because the export's file dates are just the day it was unzipped.
