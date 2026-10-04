# Search

The **Search** box at the top of the library (press **Ctrl+F**) finds
photos as you type. Every word has to match, and a word matches the start
of any word in:

- the file name and its folders ("myrtle", "dsc0091", "edits");
- the camera, including friendly names: "a7r v", "a7rv", "iphone";
- the lens ("24-105", "gm");
- tags, at every level ("grandma", "places");
- events and albums ("anime expo", "road trip").

Put words in quotes to match them together: `"las vegas"`.

## Special words

| Type | Finds |
|---|---|
| `2024`, `2024-06`, `june`, `june 2024` | When it was taken |
| `raw`, `jpeg`, `heic`, `video`, `photo` | That kind of file |
| `edited`, `unedited`, `picks`, `rejects`, `unrated`, `stacked`, `untagged` | Photos in that state |
| `4 stars` or `stars:4` | At least that many stars |
| `tag:beach`, `camera:a7rv`, `lens:24-105`, `folder:vegas`, `file:dsc`, `event:expo`, `album:trip` | Only in that field |
| `-word` | Leaves out photos that match it: `sony -myrtle` |

Search works together with the filter bar (rating, label, flag, tag) and
with an album or event you've opened. **Clear all** clears it too, and so
does **Esc** in the box.

## How it stays fast

Lunelis keeps a full-text index of the library. Building it for 160,000
photos takes about 1.5 seconds, and it's updated in the background after
each scan. Anything you change (a tag, an album, a rename) is indexed the
moment you search again. A search takes a few milliseconds to find the
photos; showing the matches takes up to about a quarter of a second.

## Ask your library

**Library > Ask your library (Ctrl+Shift+F)** takes a plain sentence:

> sunset on a beach, A7R V, 2024

Lunelis reads it in parts and shows each as a chip:
*2024 · Camera: ILCE-7RM5 · Looks like: sunset on a beach*.

- **Exact parts** become rules: cameras as you'd say them ("A7R V", "a7iv"),
  lenses ("24-70"), years and months ("June 2024"), stars ("4 stars"),
  "picks", "rejects", "raw", "videos", ISO ("iso 6400", "high iso"), focal
  lengths ("85mm") and apertures ("f/1.4").
- **The rest** - "sunset on a beach" - is matched against what the photos
  *look like*, using the scene model (Settings > Library > Scene tags), so it
  finds photos nobody tagged. The best matches come first. Without the scene
  model, the words are looked for in names, folders and tags instead.

Click a chip's ✕ to ask again without that part.

## Find similar

Select a photo and choose **Photo > Find similar photos (Ctrl+Alt+F)** - or
select several for **More like these**. The library shows the photos that
look most alike, best first. It needs scene tags turned on.
