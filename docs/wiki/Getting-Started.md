# Getting started

## Install

### The installer (recommended)

1. Open the public [Lunelis releases](https://github.com/AxialForge/Lunelis/releases)
   page and download `Lunelis-v<version>-setup.exe` from the newest release.
2. Run it. It needs no administrator rights: Lunelis installs for your Windows
   account into `%LOCALAPPDATA%\Programs\Lunelis`, with a Start menu entry and
   an entry in Settings > Apps (where you can also uninstall it).
3. Answer the setup pages - each can be changed later in Settings:
   - **Your photos:** folders Lunelis found on this PC (Pictures, OneDrive's
     Pictures, Google Takeout exports in Downloads) are ticked; add any other
     folder, drive or network path (`\\nas\photos`).
   - **Where Lunelis keeps its catalog:** `%LOCALAPPDATA%\Lunelis` unless you
     pick another drive in this PC. Pick a folder holding a catalog from another
     PC to carry on with that library.
   - **Start-up and the tray:** keep Lunelis in the tray (it offers to import
     when a card, phone or stick goes in) and start it with Windows.
   - **Optional downloads:** four small AI models that run only on this PC -
     **Scene tags** (155 MB), **Faces** (39 MB), **Subject masks** (44 MB) and
     **Sky masks** (176 MB). Lunelis fetches the ones you tick in the
     background after it starts, each checked against its fingerprint.
4. Finish with **Start Lunelis now**. It reads your folders in the background
   and downloads the chosen models; you can browse straight away.

**Updating:** Lunelis updates itself (Settings > Updates) - you never run the
installer again. Running a newer installer over an existing install only
replaces the program files; nothing is asked twice and your library stays.

**Uninstalling:** Settings > Apps > Lunelis > Uninstall. It removes the
program and the Start-with-Windows entry, then asks whether to remove your
library data too (it goes to the Recycle Bin; *No* keeps it for a later
install). Your photos are never touched.

### The zip (no installer)

1. Download `Lunelis-v<version>-windows.zip` from the same page.
2. Unzip it into a folder of its own, e.g. `%LOCALAPPDATA%\Programs`. Avoid
   `C:\Program Files`: the built-in updater can't replace files there
   without administrator rights.
3. Run `Lunelis\Lunelis.exe`. A **Welcome** window asks the same questions
   as the installer (Help > Welcome... opens it again).

The first time, Windows SmartScreen may say *"Windows protected your PC"*.
Lunelis isn't code-signed (signing certificates cost money every year), so
Windows doesn't know the publisher yet. Click **More info > Run anyway**.
You only need to do this once per version.

Your library isn't in the program folder: it lives in the data folder (below),
so every version, and the from-source version, uses the same catalog,
thumbnails and settings.

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

If the installer or the Welcome window added your folders, the scan has
already started. Otherwise choose **Library > Add folder...**
(`Ctrl+O`) and pick a folder of photos - a local drive, a mapped drive or a
network path like `\\nas\photos` all work.

Lunelis then works through the folder in the background:

1. **Scan** - lists every photo and video (seconds, even for tens of thousands).
2. **Sidecars** - reads ratings and labels from existing XMP files (darktable,
   Lightroom, culling tools).
3. **Metadata** - camera, lens, exposure, date, GPS for every file.
4. **Takeout** - if the folder is a Google Takeout export, dates from Google's JSON.
5. **Bursts** - shots fired in a quick burst are stacked into one tile.
6. **Search index** - so the search box answers at once.
7. **Thumbnails** - a 512 px preview per file, taken from the preview your
   camera already embedded, so even 120 MB RAWs are quick.
8. **Comparing photos** - near-duplicates, scene tags (when turned on) and
   what [Lunelis noticed](Lunelis-Noticed.md).
9. **Damage check** - flags empty, zero-filled or corrupt files.

The status bar shows the step ("Step 3 of 9") and its progress. You can browse while it runs; thumbnails fill
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

To keep it on another drive, use **Settings > Advanced > Data folder > Move...**; see
[Settings](Settings.md#data-folder).

## Next

- [Importing from a memory card](Importing.md)
- [Settings](Settings.md)
- [Browsing the library](Browsing.md)
- [Ratings, labels and sidecars](Ratings-Labels-and-Sidecars.md)
