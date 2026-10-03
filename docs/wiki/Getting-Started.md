# Getting started

## Install

### The app (recommended)

1. Open the public [Lunelis releases](https://github.com/AxialForge/Lunelis/releases)
   page and download `Lunelis-v<version>-windows.zip` from the newest release.
2. Unzip it into a folder of your own, e.g. `%LOCALAPPDATA%\Programs` or your
   Documents. Avoid `C:\Program Files`: the built-in updater can't replace
   files there without administrator rights.
3. Run `Lunelis\Lunelis.exe`.

The first time, Windows SmartScreen may say *"Windows protected your PC"*.
Lunelis isn't code-signed (signing certificates cost money every year), so
Windows doesn't know the publisher yet. Click **More info > Run anyway**.
You only need to do this once per version.

**Updating:** from v0.3.1, Lunelis updates itself (Settings > Updates). Before
that, unzip the new version over the old one. Your library isn't in the
program folder: it lives in the data folder (below), so every version, and
the from-source version, uses the same catalog, thumbnails and settings.

### From source (for development)

Lunelis needs Windows 10/11 and Python 3.13.

```bash
git clone https://github.com/AxialForge/Lunelis.git
cd Lunelis
py -3.13 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt -e .
python -m lunelis
```

## First run

The window opens on an empty library. Choose **Library > Add folder...**
(`Ctrl+O`) and pick a folder of photos - a local drive, a mapped drive or a
network path like `\\nas\photos` all work.

Lunelis then works through the folder in the background:

1. **Scan** - lists every photo and video (seconds, even for tens of thousands).
2. **Sidecars** - reads ratings and labels from existing XMP files (darktable,
   Lightroom, culling tools).
3. **Metadata** - camera, lens, exposure, date, GPS for every file.
4. **Takeout** - if the folder is a Google Takeout export, dates from Google's JSON.
5. **Thumbnails** - a 512 px preview per file, taken from the preview your
   camera already embedded, so even 120 MB RAWs are quick.
6. **Damage check** - flags empty, zero-filled or corrupt files.

The status bar shows progress. You can browse while it runs; thumbnails fill
in as they're made. Add more folders the same way, and press `F5` to rescan
everything later.

## Where Lunelis keeps its data

Lunelis never writes its own files into your photo folders. Everything it
needs lives in one data folder:

```
%LOCALAPPDATA%\Lunelis\
  catalog.db          the catalog (ratings, metadata, jobs...)
  cache\thumbnails\   thumbnails - safe to delete, they're rebuilt
  backups\            automatic catalog backups
  sidecars\           ratings/labels as XMP files (the central store)
```

To keep it on another drive, use **Settings > Data folder > Move...**; see
[Settings](Settings.md#data-folder).

## Next

- [Importing from a memory card](Importing.md)
- [Settings](Settings.md)
- [Browsing the library](Browsing.md)
- [Ratings, labels and sidecars](Ratings-Labels-and-Sidecars.md)
