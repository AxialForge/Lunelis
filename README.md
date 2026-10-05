# Lunelis

A photo library for Windows that runs entirely on your own PC. Lunelis
catalogues the photos and videos you already have - on local drives, USB drives
and network storage - without moving, renaming or changing them, and gives you
what you need to look after a large collection for the long term.

No accounts, no cloud, no subscriptions. Originals are never touched, and
nothing is deleted behind your back.

## Install

1. Open [Releases](https://github.com/AxialForge/Lunelis/releases) and download
   `Lunelis-vX.Y.Z-setup.exe` from the newest release.
2. Run it. It installs for your Windows account only (no administrator rights)
   into `%LOCALAPPDATA%\Programs\Lunelis`, adds Lunelis to the Start menu and to
   Settings > Apps, and asks a few setup questions: your photo folders, where
   the catalog lives, the tray and start-with-Windows, and optional AI models.
3. Lunelis keeps itself up to date from **Settings > Updates**. You never run the
   installer again.

Prefer no installer? Download `Lunelis-vX.Y.Z-windows.zip` instead, unzip it into
a folder of its own and run `Lunelis.exe`; a Welcome window asks the same
questions. Lunelis isn't code-signed, so Windows SmartScreen asks once per
version: **More info > Run anyway**.

Requirements: Windows 10 or 11, 64-bit. The full walkthrough is in
[Getting started](docs/wiki/Getting-Started.md).

## What it does

| Area | |
|---|---|
| Browse and find | A fast grid with a timeline, filters, full-text search, smart albums, a map, On this day, and Ask your library ("sunset on a beach, 2024"). |
| Rate, cull, organise | Stars, labels, flags, full-screen culling with compare, albums, events, nested tags, scene tag suggestions from a model on this PC. Everything is written to standard XMP sidecars. |
| Edit | Non-destructive: light, colour, tone curve, crop, lens corrections, noise reduction, masks (including AI subject and sky), retouch, virtual copies, My look, colour-managed export with presets. |
| Create | 13 tools: animations, collages, batch copies, contact sheets, timelapses, slideshow videos, before-and-after, prints, focus stacks, star trails, median stacks, panoramas, HDR. |
| Videos | Playback with trim to a new file; Sony S-Log3 clips shown with a built-in look or your own LUTs. |
| Bring photos in | Memory-card, phone and USB-stick import, verified twice, with Autopilot (burst covers, scene tags, event name, draft album) and Review your shoot to undo any step. |
| Clean up | Exact and near-duplicates with keeper rules, damaged-file checks, migration of a scattered library onto one drive. |
| Keep safe | Catalog backups, verified photo backups, quarantine instead of deletion, rolling integrity checks, a sensor dust map per camera. |
| Share at home | A family gallery: an album on phones and TVs on your home network, never the internet. |

How to use each part is in the [wiki](docs/wiki/Home.md).

## Run from source

```bash
py -3.13 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt -e .
python -m pytest
python -m lunelis
```

Python 3.13 and PySide6 (Qt 6), a SQLite catalog, rawpy (LibRaw), PyAV,
OpenCV and onnxruntime. Tests never touch real data. Building `Lunelis.exe` and
the installer, the repository layout and the extension points are in
[For developers](docs/wiki/For-Developers.md); the engineering notes and the
gotchas are in [CLAUDE.md](CLAUDE.md).

## Where things live

- The program: `%LOCALAPPDATA%\Programs\Lunelis` (or wherever you unzipped it).
- Your library data (catalog, thumbnails, backups, central sidecars):
  `%LOCALAPPDATA%\Lunelis`, or the folder you chose. Never inside your photo
  folders and never inside the program folder.

## License

MIT - see [LICENSE](LICENSE).
