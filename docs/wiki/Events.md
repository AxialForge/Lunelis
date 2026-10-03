# Events

An event is a trip, a shoot or a day out: a name plus the photos that belong
to it, such as *Las Vegas Vaca*, *Air Show* or *Myrtle Beach*. Its dates
are always the capture dates of its photos. A photo is in at most one event.

Events live in the catalog. Making, renaming or removing one never touches
your photos or folders, and removing an event leaves its photos exactly as
they are.

Events are one of the sections on the [Albums](Albums.md) page. **Event
suggestions...** there opens the page described below.

## Making events

There are three ways:

- **From photos you pick.** Select photos in the library, then choose
  **Photo > Event > New event from selection...** (`Ctrl+E`). **Add to an
  event...** and **Remove from its event** are in the same menu. Adding a
  photo that's already in another event moves it, after asking.
- **When you import a card.** Type an **event name** on the Import page (see
  below).
- **By accepting suggestions.** The Events page suggests events, and you
  tick the ones you want, fix any names, then press **Create ticked events**.

## Suggestions

Lunelis looks in two places. Nothing changes until you press Create.

**Your folder names.** Folders you already named after events are found:
`6-19-2026 Air Show`, `9-6-2021_Air_Show`, `2019-07-04 Fireworks`,
`Memories\Aquarium` and so on.

- **Trips kept together.** The outermost named folder is the event, so a
  trip's day folders (`Las Vegas Vaca\Aug 17 grand canyon tour`) and working
  folders (`Clips`, `JPEG Files`) belong to it.
- **One event, not several.** The same name on consecutive days
  (`6-17-2024 Myrtle Beach` to `6-20-2024 Myrtle Beach`), or the same folder
  in both NAS pools, becomes a single suggestion.
- **Workflow folders ignored.** `Timelaps`, `TL1`, `2024 Edits`,
  `2024 Album`, `Set_1`, `original`, `ARW` and similar aren't events.
- **Dates taken out of names.** `8-23-2025 -- 8-25-2025 summer vaca 2025`
  is suggested as *summer vaca 2025*.
- **Strays left out.** Photos from another trip that ended up in a subfolder
  aren't included.

**Gaps in capture time.** Photos that aren't in a named folder or an event
are split wherever more than **18 hours** pass between shots. A run of at
least **30 photos** over no more than three weeks is suggested, named by its
dates (for example *Jun 19 - 21, 2026*). Rename it as you create it. Both
numbers can be changed on the Events page.

**Dismiss ticked** means those suggestions won't come back.

## Seeing an event's photos

Double-click an event, or select it and press **Show photos**. The library
shows only that event, with an **Event: name** filter chip. Click the chip's
x to see everything again.

## Events and importing

The Import page's **Event name** field makes the import an event:

- **One folder.** Every photo is filed by the event's **start date**, so a
  night shoot or a week-long trip lands in one folder
  (`2026\6-17-2026 Myrtle Beach\`) instead of splitting at midnight. That
  includes the later days and any clips without a date.
- **Joined automatically.** Once the imported photos are cataloged, they're
  added to the event.

Storage templates also have an `{event}` token, with a fallback:
`{YYYY}\{event|date}` files a photo under its event's name, or its date when
it has none. See [Importing](Importing.md#where-the-photos-go).

## Coming later

- Renaming an event's folder on disk will be an optional move job, built
  with migration.
- [Migration](Migration.md) already files events under their start date,
  in folders named after them.
