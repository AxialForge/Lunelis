# Family gallery

Share an album with the people at home - on their phones, tablets or the TV -
without accounts, uploads or the internet.

## Sharing an album

**Albums**, right-click an album > **Share on the home network...**:

- **PIN (optional):** 4-12 digits. Without one, anyone at home with the link
  can look; with one, they type it once per browser.
- **Allow downloading the original files:** off by default - guests see
  resized copies (1600 px, with your edits).
- **Share** shows the album's **link** (with **Copy**) and a **QR code**: point
  a phone's camera at it. **Update** changes the PIN or the originals choice;
  **Stop sharing** makes the link useless at once (sharing again makes a new
  one).

On the gallery page: the photos as a grid (tap one for it large), and
**Slideshow** - full screen, a new photo every six seconds - for the TV.

Windows may ask once whether Lunelis may accept connections on private
networks: allow it, or the phones can't reach it.

## What keeps it private

- **Home network only.** Requests from anywhere that isn't a home (private)
  address are refused, before anything else is looked at.
- **Nothing to find.** There's no list of albums: each shared album has its
  own long random key in its link, and without the key there's nothing.
- **PINs aren't stored**, only a salted hash of them; five wrong tries lock
  that device out for ten minutes.
- **Rate-limited:** a device asking too fast gets "wait a moment".
- **No location leaves:** the resized copies carry no metadata (no GPS).
- **Only that album:** asking for any other photo gets nothing.
- **Off by default.** The gallery only runs while at least one album is
  shared; stop sharing the last one and nothing is listening.

The gallery uses port 8735 (setting `gallery_port`).
