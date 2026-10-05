# Lunelis 0.34 - Install Guide

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

There are two ways to install Lunelis. Use the installer unless you have a reason not to.

### With the installer (recommended)

1. Open the releases page at `https://github.com/AxialForge/Lunelis/releases`.
2. Under the newest release, download `Lunelis-vX.Y.Z-setup.exe`.
3. **Optional, recommended:** check the download. Download the matching `.sha256` file too, then in PowerShell run `Get-FileHash Lunelis-vX.Y.Z-setup.exe`. The hash it prints must match the one in the `.sha256` file.
4. Run the setup. It installs for your Windows account only, so there is no administrator prompt.
5. Work through the pages:

| Page | What to choose |
|---|---|
| License | The MIT license. |
| Destination | `%LOCALAPPDATA%\Programs\Lunelis` is the usual place for programs installed per user. The folder must be empty or already hold Lunelis: updates replace it and uninstalling removes it. |
| Your photos | Folders Lunelis found on this PC are ticked: Pictures, OneDrive's Pictures, and Google Takeout exports in Downloads. **Add a folder...** adds any folder, drive or network folder (such as `\\nas\photos`). |
| Where Lunelis keeps its catalog | `%LOCALAPPDATA%\Lunelis`, or a folder on another drive in this PC. It cannot be a network folder. Choose a folder that holds a catalog from another PC to carry on with that library. |
| Start-up and the tray | Keep Lunelis in the tray (it offers to import when a card, phone or stick goes in), and start it with Windows. |
| Optional downloads | The scene model (155 MB), and the subject (44 MB) and sky (176 MB) mask models. |
| Ready to install | A summary of your answers, and the optional desktop shortcut. |

6. Leave **Start Lunelis now** ticked and click **Finish**.

Lunelis applies your answers at its first start. It reads your folders and downloads the chosen models in the background, so you can browse straight away. Each answer can be changed later in Settings.

### With the zip

1. Download `Lunelis-vX.Y.Z-windows.zip` from the same page, and check it the same way.
2. Right-click the zip, choose **Extract All...**, and extract it to a folder of its own, such as `%LOCALAPPDATA%\Programs`.
3. Double-click `Lunelis.exe`. A **Welcome** window asks the same questions as the installer, except the data folder (Settings > Advanced > Data folder moves it).

**Where not to unpack it:**

- **Not in `C:\Program Files`.** The built-in updater could not replace the files there without administrator rights.
- **Not in a folder that holds other things.** An update replaces the whole program folder, so keep Lunelis in a folder of its own.

### The SmartScreen message

The first time, Windows may show *"Windows protected your PC"* for the setup or for `Lunelis.exe`. Lunelis is not code-signed, because signing certificates cost money every year, so Windows does not know the publisher. Click **More info**, then **Run anyway**. Windows asks once for each new version.

### The firewall message

Windows Defender Firewall may ask whether Lunelis can use the network the first time you share an album on the home network (the family gallery). Allow it on **Private networks** only. If you never use the family gallery, you can cancel the message; nothing else needs inbound access.

## First run

If the installer or the Welcome window added your folders, the scan has already started. Otherwise:

1. **Add your photos.** Click **Add a folder** (or **Library > Add folder...**, `Ctrl+O`) and pick a folder of photos. Add as many folders as you like, one at a time. **Help > Welcome...** opens the setup questions again.
2. **Let the scan run.** Lunelis works through the folders in nine steps: scan, sidecars, metadata, Takeout dates, bursts, search index, thumbnails, comparing photos and the damage check. The status bar shows the step and its progress. You can browse while it runs. A large library on a NAS can take hours the first time; later scans only look at what changed.
3. **Choose where imports go.** Before the first memory-card import, Lunelis asks for a destination folder. You can also set it in **Settings > Import**.
4. **Set a photo backup.** Go to **Backups** (sidebar > Keep safe) and add a backup to a USB drive or network folder. Catalog backups are already automatic.

If part of the setup could not be done (a folder that was not reachable, a model that did not download), Lunelis says so once and carries on with the rest.

## Where Lunelis keeps things

| What | Where | Notes |
|---|---|---|
| The program | `%LOCALAPPDATA%\Programs\Lunelis`, or the folder you chose or unpacked | Replaced by each update. Holds nothing of yours. The installer's uninstaller is in its `uninstall` folder. |
| The data folder | `%LOCALAPPDATA%\Lunelis` | The catalog, thumbnails, catalog backups, central sidecars, logs, models. |
| Moved data folder | Recorded in `%APPDATA%\Lunelis\location.json` | Set by the installer, or with Settings > Advanced > Data folder > Move.... |
| Setup answers | `%APPDATA%\Lunelis\setup.applied.json` | What the installer asked, kept as a record once applied. |
| Set-aside files | `_Lunelis Quarantine` at the top of each photo source | Restored or emptied from the Quarantine page. |
| Your creations | `Pictures\Lunelis creations` by default | Exports and Create tools write here; nothing is overwritten. |
| Start-with-Windows entry | `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` | Only when you tick the setting. |

**The data folder must be on a local drive.** The catalog is a SQLite database, and SQLite's locking is not reliable on network shares, so Lunelis refuses to move it onto one. Your photos can be anywhere.

## Updating

You install Lunelis once. After that it updates itself.

1. Lunelis checks for a new version once a day, or when you click **Settings > Updates > Check for updates**.
2. Click **Download and install**. The download is checked against its SHA-256 before anything is unpacked.
3. Lunelis restarts in the new version. The previous version is kept until the new one has started.

An installed Lunelis keeps its uninstaller through updates, and Settings > Apps shows the new version. Running a newer setup over an existing install also works: it only replaces the program files and asks nothing.

Your catalog, settings and thumbnails are in the data folder, so they carry over. If a new version changes the catalog's layout, a catalog backup is made first. An older version cannot open a catalog that a newer one has upgraded; to go back, restore that backup from **Settings > Backups**.

## Moving to a new PC

1. On the old PC, make sure a catalog backup is recent (**Settings > Backups > Back up now**), then close Lunelis and also quit it from the tray.
2. Copy the whole data folder (`%LOCALAPPDATA%\Lunelis`, or the folder you chose) to a drive in the new PC.
3. Run the setup on the new PC. On the **Where Lunelis keeps its catalog** page, choose the copied folder: Lunelis carries on with that library.
4. Keep the same drive letters and network paths for your photos as on the old PC. Lunelis can't yet point an existing source at a new path: a source added again at a new path is scanned as new photos, without the old ratings and edits. Ratings in XMP sidecars next to the photos are read back.

## Uninstalling

**Installed with the setup:** open **Settings > Apps > Installed apps**, find **Lunelis**, and choose **Uninstall**.

1. Quit Lunelis from its tray icon first.
2. The uninstaller removes the program folder and the Start-with-Windows entry.
3. It then asks whether to remove your library data too: the catalog (ratings, edits, albums, tags), thumbnails and catalog backups. **Yes** sends the data folder to the Recycle Bin; **No** keeps it, so a later install picks up where you left off.

**Unpacked from the zip:** untick **Start Lunelis with Windows** (Settings > General), quit from the tray icon, and delete the program folder. Delete `%LOCALAPPDATA%\Lunelis` and `%APPDATA%\Lunelis` only if you want your library data gone too.

Your photos are never touched by uninstalling. Ratings, labels and tags in XMP sidecars next to your photos stay. Check each source for a `_Lunelis Quarantine` folder and empty or restore it from the Quarantine page before you uninstall.

## Troubleshooting the install

| Problem | What to do |
|---|---|
| Nothing happens when you double-click `Lunelis.exe` | Check that the whole zip was extracted (not opened in place). Look for `%LOCALAPPDATA%\Lunelis\logs\lunelis.log`. |
| "The update didn't download" | Check the internet connection and try again later; GitHub may have limited the number of checks. Your current version keeps working. |
| An update says the folder holds other things | Move Lunelis into a folder of its own and start it from there. |
| A network folder shows as unavailable | Open it once in Explorer (to wake the NAS or sign in), then press `F5`. Photos are never marked missing because a share was offline. |
| Text or windows look too large or cut off | Lunelis follows Windows' display scaling. Settings > Appearance has the grid and text sizes. |
| Something else | **Help > Report a problem...** collects versions, folders and the recent log (no photos) for you to copy. |
