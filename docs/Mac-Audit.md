# Mac port audit (started October 2026)

An operation and usability audit of every Lunelis function on macOS, run
while the port is built. Three passes:

1. **Automated** - the whole pytest suite on `macos-latest` in CI (next to
   Windows), plus Mac-only tests in `tests/test_macos.py`. Anything that can be
   checked without a person goes here.
2. **Operation** - does each function do the same job on a Mac: files,
   drives, cards, shares, sleep, Trash, packaging. Findings that a test can
   catch become tests.
3. **Usability** - does it feel right on a Mac: menus, keys, wording,
   permissions prompts, Finder, Retina, dark mode. Needs a real Mac and a
   person; the checklist at the end is the script.

Status: ✅ done (version) · 🧪 test written · 🔍 needs a real Mac · — open.

## What the port has so far

| | Status |
|---|---|
| Platform layer `lunelis/macos.py` (volumes via statfs, `/Volumes`, volume UUID, Trash, idle time, caffeinate) | ✅ 0.59 (dev) |
| Finder for Show in folder / Open folder / open the log (`lunelis/desktop.py`) | ✅ 0.59 (dev) |
| Data folder `~/Library/Application Support/Lunelis`, location/setup files in `~/Library/Preferences/Lunelis` | ✅ 0.59 (dev) |
| Path checks with a hardcoded `\` separator (8 places) | ✅ 0.59 (dev) |
| Tests on macos-latest in CI (non-blocking until the port ships) | ✅ 0.59 (dev) |
| `Lunelis.app` + `.dmg` (packaging/build_mac.sh, CI job build-mac) | ✅ 0.59 (dev), 🔍 first build |
| Updater on the Mac: points to the releases page instead of the Windows swap | ✅ 0.59 (dev) |
| Start at login (LaunchAgent) | — |
| In-place updates on the Mac | — |

## Pass 1 - automated

| Check | Status |
|---|---|
| Full suite green on Linux with the port's changes (proxy for Unix) | 🧪 |
| Full suite on macos-latest - first run, triage every failure | 🔍 |
| `test_macos.py`: statfs on the boot volume, `/Volumes`, split_mount, idle time, caffeinate, data-folder location | 🧪 (Mac only) |
| Static guard: Explorer / Notepad / os.startfile only in desktop.py | 🧪 |
| Static guard: every kernel32/shell32/winreg caller checks sys.platform | 🧪 |
| Packaged-app self-test (`Lunelis.app/Contents/MacOS/Lunelis --self-test`) in CI | 🧪 (build-mac) |
| Real-RAW tests (`LUNELIS_SAMPLE_ARW`) on the Mac runner | — add the download step to the Mac test job |
| Card import end to end from a disk image mounted under /Volumes (hdiutil attach of a DCIM-layout image) | — to write |
| Backup to a second volume that's ejected and remounted under another name | — to write |
| Migration across two volumes (copy + verify, never a rename) | — to write |
| Filenames in NFD vs NFC (see Operation, O1) | — to write |

## Pass 2 - operation (each area of the wiki)

| # | Area | Mac risk | How it's checked | Status |
|---|---|---|---|---|
| O1 | Sources & scanning | **Unicode normal form.** HFS+ and some SMB servers hand back names decomposed (NFD: `e` + combining accent); the catalog, sidecar lookups and Takeout JSON matching compare strings as stored. A folder renamed on a Mac can look like a new file. No `unicodedata.normalize` anywhere today. | test with NFD/NFC twins; real NAS share from a Mac | — |
| O2 | Sources & scanning | Case: APFS is case-insensitive by default but can be case-sensitive; code matches `COLLATE NOCASE` in places | test on a case-sensitive disk image | — |
| O3 | Sources & scanning | `.DS_Store`, `._*` AppleDouble files on shares and cards must never be cataloged | test | — |
| O4 | Importing (cards) | Card detection = removable volume in /Volumes with DCIM/PRIVATE; volume identity = diskutil VolumeUUID (FAT cards have one) | hdiutil test image; real SD card | 🔍 |
| O5 | Importing | "Eject the card" - Windows users pull the card; on a Mac it must be ejected first or macOS complains | real card; wording | 🔍 |
| O6 | Backups | Drive found again after a rename (/Volumes/<new name>) by UUID | test with two images | — |
| O7 | Quarantine / emptying | Trash via QFile.moveToTrash; FAT/exFAT and shares delete for good (has_trash) | test on an exFAT image | — |
| O8 | Migration | Same-drive check = same mount point (rename) else copy+verify | test with two images | — |
| O9 | Jobs | Keep awake = caffeinate -i while a job runs; idle schedule = HIDIdleTime | Mac tests | 🧪 |
| O10 | Network sources | smb:// shares mount under /Volumes; reachable/offline detection when the share sleeps | real NAS | 🔍 |
| O11 | Data folder | Catalog refused on a network volume (is_network); moving the data folder between disks | test + real | — |
| O12 | darktable | darktable on the Mac keeps its config in `~/.config/darktable` - verify, and the Lua script in a real darktable for Mac | real darktable | 🔍 |
| O13 | Videos | PyAV wheels on arm64; QtMultimedia playback (native backend) | real clips | 🔍 |
| O14 | AI (scenes, faces, masks) | onnxruntime / OpenCV arm64 wheels; speed on Apple silicon | real models | 🔍 |
| O15 | Family gallery | macOS firewall prompt on first start of the web server | real Mac | 🔍 |
| O16 | Single instance | QLockFile in the data folder; restart with `--after <pid>` waits with kill(0) | test | — |
| O17 | Packaging | Gatekeeper: unsigned app, first open = right-click > Open; App Translocation if run from the .dmg (read-only, random path) - say "drag to Applications" | real Mac | 🔍 |
| O18 | Permissions (TCC) | First access to removable / network volumes, Desktop, Documents, Downloads asks the user; the .app carries the reasons (Info.plist). Photos library package is out of scope. | real Mac | 🔍 |
| O19 | Start at login / tray | Settings tick box is disabled off Windows today; Mac needs a LaunchAgent and the menu-bar icon behaviour | — | — |
| O20 | Updater | Disabled on Mac (status points at the .dmg); in-place update later | test | ✅ |

## Pass 3 - usability

| # | Finding to check | Notes | Status |
|---|---|---|---|
| U1 | Shortcuts: Qt maps Ctrl to Cmd in QKeySequence, but 35 UI strings say "Ctrl" literally (hints, tooltips, menus) and the wiki says Ctrl everywhere | show ⌘ on the Mac (one helper for key names) | — |
| U2 | Menu bar: Qt moves the menus to the Mac's top bar; About / Settings / Quit need menu roles to land in the Lunelis menu (none set today) | setMenuRole | — |
| U3 | Wording: "Recycle Bin" (14 lines), "Explorer" (11), "Windows" (63), "%LOCALAPPDATA%" in UI text | Trash / Finder / macOS per platform | — |
| U4 | Theme: Fusion + token palette is pinned app-wide (on purpose, Windows dark mode leak); check it on macOS light/dark | keep or follow the system | 🔍 |
| U5 | Fonts: one hardcoded Segoe UI; Retina scaling of the grid and thumbnails (devicePixelRatio) | | 🔍 |
| U6 | Trackpad: pinch to zoom the grid / photo, two-finger scroll speed in the grid and filmstrip | | 🔍 |
| U7 | Drag a folder from Finder onto the library to add it | | 🔍 |
| U8 | Window: full screen (green button), the Dock icon, closing the window vs quitting (Cmd+Q) and the tray | | 🔍 |
| U9 | The wiki and Getting started need a Mac section (install from .dmg, Gatekeeper, permissions) | | — |

## Manual checklist on a real Mac

Use a throwaway library (Settings > data folder on a test disk image), never
the real catalog.

1. Download the test `.dmg` (Actions > Test & Release > Run workflow >
   artifact Lunelis-macos). Open it, drag Lunelis to Applications, open it
   with right-click > Open. Note every prompt macOS shows.
2. Welcome window: add a folder of photos on the Mac's disk. Scan,
   thumbnails, EXIF - compare counts with the same folder on Windows.
3. Rate, label, tag; check the sidecar beside / central; open in darktable.
4. Plug in an SD card with DCIM: does the Import page see it? Import with a
   template; eject; plug in again - "already imported"?
5. Add a NAS folder (Finder > Go > Connect to Server, smb://...). Scan; let
   the NAS sleep; check the offline message.
6. Duplicates: verify a group, quarantine, empty - does the file land in
   the Trash with Put Back?
7. Backups: a set on a USB disk; rename the disk in Finder; plug in again.
8. Start a long job, leave the Mac alone - it must not sleep (`pmset -g
   assertions` shows caffeinate).
9. Edit a photo, export, make a collage and an MP4.
10. Every page once: note anything that says Windows, Ctrl, Recycle Bin or
    Explorer, any clipped text, any shortcut that doesn't work.
