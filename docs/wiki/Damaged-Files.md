# Damaged files

The **Damaged files** page lists files that can't be what they claim - and,
for each, where an intact copy survives in your library.

## What counts as damaged

| Problem | Meaning |
|---|---|
| Empty file | 0 bytes - the data never arrived |
| Filled with zeros | The file has its size but the image data is gone (usually a failed copy or disk problem) |
| Not a recognisable photo or video | The start of the file isn't any known format |
| Cut short | The end of the file is missing |
| Corrupt image data | The picture can't be decoded |
| Changed on disk | Found by *Check integrity*: the contents changed although the file was never edited (silent corruption) |

The check runs after every scan and reads almost nothing: problems are
already known from earlier passes, and only files whose start was
unrecognisable are opened to tell zero-filled from unknown. **Check now**
re-runs it.

Files with **no intact copy anywhere** are listed first - they're the ones
at risk.

## Surviving copies

Select a file to see its intact copies, best first:

1. **Same file, another location** - identical name and size elsewhere
2. **The RAW original** - for a damaged JPEG, its RAW in the same folder
3. **Google Takeout copy** - Google's re-encoded version (lower quality, but
   the picture survives)

Use **Show in Explorer** to get to it. Lunelis never repairs or replaces a
file by itself.

**Dismiss** hides a file you've dealt with (tick *Show dismissed* to see it
again). If the problem is fixed - you replace the file with a good copy - it
drops off the list on the next check.

## Catching silent corruption

1. **Library > Hash everything (integrity baseline)...** fingerprints every
   file once.
2. Later, **Library > Check integrity against the baseline...** re-reads
   them. A file whose fingerprint changed while its size and date didn't has
   been corrupted without anyone editing it - it shows up as *Changed on disk*.

Run the baseline while your files are known-good, and the check every few
months (the job can be scheduled overnight).
