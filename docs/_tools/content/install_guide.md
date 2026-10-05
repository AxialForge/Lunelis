# Lunelis 0.33 - Install Guide

How to install Lunelis on a Windows PC, set it up the first time, keep it updated, move it and remove it.

This guide is for the person setting Lunelis up. Using it day to day is covered in the *User Manual*.

## Before you start

| You need | Details |
|---|---|
| Windows | Windows 10 or 11, 64-bit. |
| Disk space | About 600 MB for the program. Plan for about 25 KB per photo for thumbnails: 4 GB for 159,000 photos. Optional AI models add up to 375 MB. |
| Memory | 8 GB works; 16 GB is better for large RAW files, panoramas and stacks. |
| Screen | 1280 x 720 or larger. Scaled displays (125 %, 150 %) are supported. |
| Internet | Only to download Lunelis and, if you want them, updates, AI models and map pictures. Everything else works offline. |
| Rights | A normal user account. No administrator rights are needed. |

Your photos can be anywhere: a folder on this PC, an external drive, a USB stick or a network share (NAS), such as `\\nas\photos` or a mapped drive letter.

## Install

Lunelis comes as a zip file. Unpacking it is the whole install.

1. Open the releases page at `https://github.com/AxialForge/Lunelis/releases`.
2. Under the newest release, download `Lunelis-v0.33.0-windows.zip` (about 184 MB).
3. **Optional, recommended:** check the download. Download the matching `.sha256` file too, then in PowerShell run `Get-FileHash Lunelis-v0.33.0-windows.zip`. The hash it prints must match the one in the `.sha256` file.
4. Right-click the zip, choose **Extract All...**, and extract it to a folder of your own. A good place is `%LOCALAPPDATA%\Programs`, which gives `%LOCALAPPDATA%\Programs\Lunelis\Lunelis.exe`.
5. Double-click `Lunelis.exe`.
6. **Optional:** right-click `Lunelis.exe` and choose **Pin to Start**, or **Show more options > Send to > Desktop (create shortcut)**.

**Where not to unpack it:**

- **Not in `C:\Program Files`.** The built-in updater could not replace the files there without administrator rights.
- **Not in a folder that holds other things.** An update replaces the whole program folder, so keep Lunelis in a folder of its own. Since 0.33, an update refuses to run if the folder also holds the data folder or other programs.

### The SmartScreen message

The first time, Windows may show *"Windows protected your PC"*. Lunelis is not code-signed, because signing certificates cost money every year, so Windows does not know the publisher. Click **More info**, then **Run anyway**. Windows asks once for each new version.

### The firewall message

Windows Defender Firewall may ask whether Lunelis can use the network the first time you share an album on the home network (the family gallery). Allow it on **Private networks** only. If you never use the family gallery, you can cancel the message; nothing else needs inbound access.

## First run

The window opens on an empty library.

1. **Add your photos.** Click **Add a folder** (or **Library > Add folder...**, `Ctrl+O`) and pick a folder of photos. Add as many folders as you like, one at a time.
2. **Let the scan run.** Lunelis works through the folders in nine steps: scan, sidecars, metadata, Takeout dates, bursts, search index, thumbnails, comparing photos and the damage check. The status bar shows the step and its progress. You can browse while it runs. A large library on a NAS can take hours the first time; later scans only look at what changed.
3. **Choose where imports go.** Before the first memory-card import, Lunelis asks for a destination folder. You can also set it in **Settings > Import**.
4. **Set a photo backup.** Go to **Backups** (sidebar > Keep safe) and add a backup to a USB drive or network folder. Catalog backups are already automatic.

### Settings worth a look on the first day

| Setting | Where | What it does |
|---|---|---|
| Keep Lunelis in the tray | Settings > General > Tray and start-up | When the window closes, Lunelis stays in the tray and offers imports when a card, phone or stick is plugged in. |
| Start Lunelis with Windows | Settings > General > Tray and start-up | Starts Lunelis in the tray when you sign in. Off unless you tick it. |
| Theme | Settings > Appearance | Graphite (light), Midnight (dark), High contrast, or Follow Windows. |
| When background jobs run | Settings > Duplicates & jobs | Any time, only while the PC is idle, or only at night; plus a read-speed limit for NAS-friendly work. |
| Sidecar mode | Settings > Ratings & sidecars | Keep XMP sidecars in one central folder (the default) or next to the photos for darktable and Lightroom. |
| Autopilot | Settings > Library > Shoots, videos and the autopilot | Which steps run after an import. Everything stays reviewable and undoable. |

### Optional downloads

Lunelis works fully without these. Each one is downloaded only when you ask for it, checked against a fixed SHA-256 fingerprint, and stored in the data folder's `models` folder.

| Download | Size | Turn on in | Used for |
|---|---|---|---|
| Subject mask model | 44 MB | Settings > Edit > AI models, or the first Subject mask | One-click subject masks. |
| Sky mask model | 176 MB | Settings > Edit > AI models, or the first Sky mask | One-click sky masks. |
| Scene model (CLIP) | about 155 MB | Settings > Library > Scene tags > Download and turn on | Scene tag suggestions, Find similar, and the picture part of Ask your library. |
| Map pictures | small, as you browse | The Map page | OpenStreetMap tiles. Only tile numbers are sent; nothing is fetched until you turn the map on. |

## Where Lunelis keeps things

| What | Where | Notes |
|---|---|---|
| The program | The folder you unpacked, such as `%LOCALAPPDATA%\Programs\Lunelis` | Replaced by each update. Holds nothing of yours. |
| The data folder | `%LOCALAPPDATA%\Lunelis` | The catalog, thumbnails, catalog backups, central sidecars, logs, models. |
| Moved data folder | Recorded in `%APPDATA%\Lunelis\location.json` | Set with Settings > Advanced > Data folder > Move.... |
| Set-aside files | `_Lunelis Quarantine` at the top of each photo source | Restored or emptied from the Quarantine page. |
| Your creations | `Pictures\Lunelis creations` by default | Exports and Create tools write here; nothing is overwritten. |
| Start-with-Windows entry | `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` | Only when you tick the setting. |

**The data folder must be on a local drive.** The catalog is a SQLite database, and SQLite's locking is not reliable on network shares, so Lunelis refuses to move it onto one. Your photos can be anywhere.

## Updating

You install Lunelis once. After that it updates itself.

1. Lunelis checks for a new version once a day, or when you click **Settings > Updates > Check for updates**.
2. Click **Download and install**. The download is checked against its SHA-256 before anything is unpacked.
3. Lunelis restarts in the new version. The previous version is kept until the new one has started.

Your catalog, settings and thumbnails are in the data folder, so they carry over. If a new version changes the catalog's layout, a catalog backup is made first. An older version cannot open a catalog that a newer one has upgraded; to go back, restore that backup from **Settings > Backups**.

## Moving to a new PC

1. On the old PC, make sure a catalog backup is recent (**Settings > Backups > Back up now**), then close Lunelis and also quit it from the tray.
2. Copy the whole data folder (`%LOCALAPPDATA%\Lunelis`) to the new PC, to the same place.
3. Install Lunelis on the new PC as above and start it.
4. Keep the same drive letters and network paths for your photos as on the old PC. Lunelis can't yet point an existing source at a new path: a source added again at a new path is scanned as new photos, without the old ratings and edits. Ratings in XMP sidecars next to the photos are read back.

## Uninstalling

1. In Lunelis, untick **Start Lunelis with Windows** (Settings > General), then quit from the tray icon.
2. Delete the program folder, such as `%LOCALAPPDATA%\Programs\Lunelis`.
3. **Only if you want to remove your catalog too:** delete `%LOCALAPPDATA%\Lunelis` and `%APPDATA%\Lunelis`. This removes ratings that live only in the catalog. Ratings, labels and tags in XMP sidecars next to your photos stay.

Your photos are never touched by uninstalling. Check each source for a `_Lunelis Quarantine` folder and empty or restore it from the Quarantine page before you uninstall.

## Troubleshooting the install

| Problem | What to do |
|---|---|
| Nothing happens when you double-click `Lunelis.exe` | Check that the whole zip was extracted (not opened in place). Look for `%LOCALAPPDATA%\Lunelis\logs\lunelis.log`. |
| "The update didn't download" | Check the internet connection and try again later; GitHub may have limited the number of checks. Your current version keeps working. |
| An update says the folder holds other things | Move Lunelis into a folder of its own and start it from there. |
| A network folder shows as unavailable | Open it once in Explorer (to wake the NAS or sign in), then press `F5`. Photos are never marked missing because a share was offline. |
| Text or windows look too large or cut off | Lunelis follows Windows' display scaling. Settings > Appearance has the grid and text sizes. |
| Something else | **Help > Report a problem...** collects versions, folders and the recent log (no photos) for you to copy. |
