# Videos and Google Takeout

## Videos

MP4, MOV and AVCHD videos are cataloged like photos:

- **Thumbnail** - a frame from near the start (skipping the often-black first
  frame), turned upright for portrait clips.
- **Length** on the tile (`▶ 1:53`).
- **Recording date** from the video file, converted to local time so videos
  sort among the photos from the same shoot.
- **Size and GPS** where the camera or phone records them.

Videos can be rated and labelled like photos. Playback and editing aren't
part of Lunelis yet.

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
