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
