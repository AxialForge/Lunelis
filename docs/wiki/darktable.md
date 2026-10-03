# darktable

Lunelis keeps its sidecars out of your photo folders by default, so on its
own darktable can't see the ratings you give in Lunelis. A small script in
darktable fixes that. It swaps **stars, colour labels and rejects** with
Lunelis, **both ways**.

## Install

1. Install darktable and start it once, so it creates its settings folder
   (`%LOCALAPPDATA%\darktable`).
2. In Lunelis, open **Settings > darktable** and press **Install in
   darktable**.
3. Restart darktable.

Installing copies `lunelis.lua` into darktable's `lua` folder and adds one
line, `require "lunelis"`, to darktable's `luarc`. Nothing else in darktable
is changed. **Remove from darktable** takes both out again.

## How it syncs

The two programs swap small files in an **exchange folder**, by default
`%LOCALAPPDATA%\Lunelis\darktable`.

- **Into darktable:** your Lunelis ratings arrive when darktable starts,
  when you switch views (lighttable and darkroom), and when you press
  **Sync with Lunelis**. That's in the Lunelis box on the right of the
  lighttable, or give it a keyboard shortcut in darktable's shortcut settings.
- **Into Lunelis:** what you change in darktable goes to Lunelis on the same
  occasions, and when darktable closes. Lunelis picks it up within about 20
  seconds, and it also goes into Lunelis's sidecars.
- **When both changed the same photo:** the newest change wins.
- **Picks** stay in Lunelis, because darktable has no picks.
- **Colour labels:** darktable lets a photo have several; Lunelis has one.
  - If a photo has one label in darktable, a change in Lunelis replaces it.
  - If it has several, Lunelis only adds its own and never removes yours.
- **The first time** the script runs, it doesn't send darktable's existing
  ratings to Lunelis. Lunelis already read those from darktable's own XMP
  sidecars when it scanned your folders.

## darktable on another PC

Choose an **exchange folder** that both PCs can reach, such as a folder on
the NAS, in **Settings > darktable**. Then install the script from the Lunelis
that runs on the darktable PC. Both sides match photos by their full path,
so darktable must open the photos through the same path Lunelis uses, e.g.
`\\nas\photos\...`.
