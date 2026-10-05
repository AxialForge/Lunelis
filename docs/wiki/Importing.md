# Importing from a memory card

Insert a card, check where the photos will go, press **Import** - Lunelis
copies everything off the card, checks every copy, and files the photos
into your library in the folder layout you choose. **File names are never
changed.**

## Starting an import

- **With the tray on** (the default when you run Lunelis normally): insert a
  card and a notification appears - *"Memory card inserted: 312 photos and
  videos. Click to import."* Click it.
- **Or** open the **Import** page (sidebar, or `Ctrl+I`) and pick the card
  under *Memory cards*, or **Import from a folder...** for photos already on a
  drive.

You can also import from any other drive or folder. The Import page lists:

- **Memory cards**.
- **Drives:** a USB drive imports everything on it. An internal or network
  drive opens a folder picker on that drive, since importing all of `C:\`
  is never what you want.
- **Recent folders:** the last six folders you imported from, one click
  away.

Lunelis reads the card and shows **exactly which library folders** the
photos will be filed into, with counts and sizes, before anything is copied.
Type an **event name** (optional) and the import becomes an
[event](Events.md): the name is added to the folder, and the whole card is
filed by the event's start date, so a multi-day trip stays in one folder.

## The two moments that matter

1. **"Everything is copied and verified - you can remove the card."**
   Every file has been copied into a staging folder and read back to check it
   matches the card exactly.
2. **"All in the library and verified. It's safe to format the card."**
   Every file has been filed into the library and checked again against the
   card's original fingerprint. Only then are the staging copies removed.

Don't format the card before the second message.

## Autopilot

Tick **Autopilot** beside the Import button and, once the photos are
imported and catalogued, Lunelis sorts out the shoot for you to review:

| Stage | What it does | In the review |
|---|---|---|
| Best frame of each burst | The sharpest frame becomes the burst's cover | Undo puts the old cover back |
| Scene tags | Suggestions for the shoot (when scene tags are on in Settings) | Undo removes the suggestions |
| An event | Named from the shoot - e.g. *Beach · Sep 12, 2026* (skipped if you gave the import a name) | Undo removes it; rename it on the Albums page |
| Edits in your style | A suggested edit per photo from [My look](Editing.md#my-look) - **not applied** | **Apply** or **Skip** |
| A draft album | The best frames: no rejects, one per burst | Undo removes it |
| A highlight reel | A slideshow of up to 30 best frames - **not made** | **Make it** (saved to the Create folder) or **Skip** |

When it's done, the status bar (and the tray) says *your shoot is ready* -
click **review it**. **Show the photos** shows the shoot in the library;
**Done reviewing** keeps what's left and skips what's still waiting.

Nothing is set aside, deleted, renamed or edited before you've reviewed it.
A stage that can't run says why (no scene model yet, fewer than 15 edits to
learn your look from). If Lunelis is closed half-way, the autopilot carries
on where it stopped the next time. Turn stages on and off in **Settings >
Library > Shoots, videos and the autopilot**. While a shoot waits, the Import
page shows a **Review it** banner; the tray message opens the review too.

## Camera profiles

Lunelis recognises the card from its layout and reads it the way that
camera writes it:

| Camera | What's read | What's left on the card |
|---|---|---|
| Sony | DCIM (every 100MSDCF, 101MSDCF... folder), XAVC clips in PRIVATE/M4ROOT/CLIP, AVCHD | SUB proxies, THMBNL thumbnails |
| Canon, Nikon, Fujifilm | DCIM | Canon's CANONMSC and MISC folders |
| GoPro | DCIM | LRV proxies and THM thumbnails |
| iPhone / iPad | DCIM (100APPLE, 101APPLE...) | - |
| Android phone | DCIM and Pictures | .thumbnails folders |
| Anything else | DCIM, or the whole folder | - |

A folder copied off a phone (no DCIM above it) is recognised from its
photos' camera maker instead.

**Sidecars travel with their file.** A Sony clip's metadata file
(`C0001M01.XML` beside `C0001.MP4`) and any `.XMP` beside a photo are
copied, verified and filed into the same folder as their photo or clip -
never on their own, never renamed, and a different file already there is
kept. Lunelis looks once more for sidecars a camera writes a moment late
before it says the card can be removed.

## Phones

- **Live Photos:** an iPhone Live Photo is a photo (`IMG_0001.HEIC` or `.JPG`)
  and a short video (`IMG_0001.MOV`). They're always filed into the same
  folder - if either name is taken there, both go to the sibling folder
  together - and both appear in your library.
- **Edits made on the iPhone** (`IMG_0001.AAE`) travel with their photo.
- **Android motion photos** keep their video inside the JPEG: they import as
  one file, and the photo's Info panel says *Motion photo*.
- **Dates come from the photos**, not from the files - copying off a phone
  gives every file today's date, so that would file everything under today.

## USB sticks and drives

A USB stick or drive that's plugged in while Lunelis runs gets the same offer
as a memory card - *"USB drive inserted: 240 photos and videos. Click to
import."* - when it has photos on it. It's also listed under **Drives** on the
Import page.

## Already in your library

Before copying anything, each file is checked against your library: a photo
you already have - even under another name - is recognised (same size, same
capture time, then byte for byte) and isn't copied again.

**A card you insert again** isn't copied again: files that card already gave
Lunelis are recognised and skipped.

**More cameras** are a config file, not a program change: a
`camera_profiles.json` in the data folder adds or replaces profiles (the
format is in the developer docs, `docs/Schemas.md`).

## Clearing the card

When everything on the card is verified in your library, the Import page
offers **Clear the card...**. It deletes only the files Lunelis imported and
verified (and leaves any that changed on the card since), asks first, and is
never offered for a folder you imported from - only for a memory card.
Formatting the card in your camera does the same and more.

## Where the photos go

**Destination** is the library folder imports go into, for example
`\\nas\photos`. There is no default: the first import asks for it, and
**Change...** picks another.

**Folders** is the storage template. The default matches your existing
layout:

| Template | A photo taken 19 June 2026 goes to |
|---|---|
| `{YYYY}\{M}-{D}-{YYYY}[ {import_name}]` (default) | `2026\6-19-2026\` - or `2026\6-19-2026 Air Show\` with an event name |
| Year | `2026\` |
| Year \ Month | `2026\06\` |
| Year \ Month \ Day | `2026\06\19\` |
| Year \ Date | `2026\2026-06-19\` |
| Import date \ name | `Imports\2026-09-27 Air Show\` |
| Camera \ Year | `ILCE-7RM5\2026\` |
| Year \ Event (else the date) | `2026\Air Show\` - or `2026\2026-06-19\` with no event |

Without an event name, a card spanning several days is split by each
photo's own capture date, and photos with no date go to `Undated\`. With an
event name, everything goes under the event's start date.

You can type your own template. Tokens: `{YYYY}` `{YY}` `{M}` `{MM}` `{D}`
`{DD}` `{month_name}` `{date}` `{camera}` `{event}` `{import_name}`
`{import_date}` `{original_folder}`. Anything in `[square brackets]` is only
included when every token inside has a value, and `{event|date}` uses the
first of the two that has a value.

## Names are never changed

- If an **identical** file is already in the target folder, or anywhere in
  your library, it's skipped - re-inserting a card never creates duplicates.
- If a **different** file already has the same name (camera counters roll
  over, especially at high burst rates), the new one goes into a sibling
  folder - `6-19-2026 (2)\` - instead of being renamed or overwriting anything.

## Staging

Copying off the card first into a **local staging folder** frees the card
quickly. When the local disk would drop below **50 GB free**, staging
spills over to a network staging folder, if you set one in **Settings >
Import** (for example `\\nas\staging\Lunelis`; it must be outside your photo
sources, so half-finished imports are never cataloged). If neither has room,
or no network folder is set, the import pauses and says so.

## If something is interrupted

Every file's progress is recorded, so an import survives closing Lunelis, a
crash or a power cut:

- Filing into the library (after the card was copied) carries on by itself
  when Lunelis starts.
- Copying off the card carries on when **that same card** is inserted again.
- If the NAS is asleep, the import waits for it.

## Starting with Windows

Right-click the tray icon > **Start with Windows** to have Lunelis start
hidden in the tray when you sign in, ready for cards. Closing the window
keeps it in the tray; use **Quit Lunelis** from the tray menu to exit.
