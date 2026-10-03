# Duplicates

The Duplicates page has two tabs:

- **Exact copies** - byte-identical files, the same file saved in two
  places such as an old backup pool and a new one.
- **Near-duplicates** - the same photo saved as a *different* file: resized,
  re-compressed, re-encoded by Google Takeout, or exported
  ([below](#near-duplicates)).

Lunelis never deletes anything: extra copies are moved to a quarantine
folder that you empty yourself.

## Finding them

1. **Library > Find duplicates...**
2. Tick the sources to check, or **Choose folder...** for just one folder.
3. Pick when to run (now / when idle / overnight) and an optional speed limit.

The job works one folder at a time, and **each folder is compared against
your whole library** as it finishes - so results start appearing on the
**Duplicates** page long before the job is done.

### How it stays fast

Reading every byte of a multi-terabyte library would take days. Instead:

- Two files can only be identical if they have the **same size and the same
  capture time** (identical files have identical metadata). Everything else is
  never read.
- Possible copies get a **sampled fingerprint**: three small slices of the
  file (start, middle, end), about 200 KB instead of 20-40 MB.
- Matching fingerprints form a **Likely** group.

## Verifying

A likely group is almost always a real duplicate, but nothing is ever moved
on a sample alone. **Verify likely groups...** runs a job that compares every
byte. Groups that really are identical become **Verified**; a rare lookalike
is dissolved.

## Choosing what to keep

**Keep copies in:** chooses which source the kept copy should come from
(e.g. your main pool over an old backup). Without a choice, Lunelis keeps
the copy that isn't in a Google Takeout export and sits in the least-nested
folder.

The lower panel lists every copy of the selected group with
**Show in Explorer**.

## Quarantine

**Move extra copies to quarantine...** handles every *verified* group:

- one copy of each photo stays exactly where it is;
- the others are **renamed** into a `_Lunelis Quarantine` folder on the same
  drive (instant - nothing is copied or deleted);
- the catalog is backed up first;
- Lunelis refuses to quarantine a group that isn't verified, or its last copy.

Quarantined files disappear from the grid, and scans ignore the folder.
The [Quarantine](Quarantine.md) page lists them, and there you can restore
them or empty them for good.

## Near-duplicates

Every photo gets a small fingerprint of what it looks like, made from its
thumbnail (on this PC, so it costs no network reads). It happens by itself
after thumbnails are made; the first time takes about a minute for 160,000
photos. **Find again** on the tab re-runs it.

Two photos are the same photo only when nothing says otherwise:

- the fingerprints are nearly equal;
- they were taken at the **same moment** - to the fraction of a second when
  the camera records it, so burst frames never match;
- their **names are related**: equal, or one inside the other
  (`20191012-_DSC0040.jpg` is an export of `_DSC0040.JPG`). Copies keep
  their name; unrelated names at the same moment are different shots;
- the shape matches (a crop is a different picture);
- they aren't RAW files, and aren't the same size (that's the exact tab's job);
- the picture isn't nearly blank (black frames and flat skies all look alike).

Every photo in a group matches every other one directly, so groups stay
small.

### Which copy is kept

Not damaged, then the most pixels, then not a Google Takeout re-encode, then
your preferred source, then the biggest file. A copy is **suggested** to set
aside only when it's plainly lesser - fewer pixels, a Takeout re-encode, or
exactly the kept copy's name. Edited exports (a folder or name with "edit" or
"export") are never suggested, and neither is a same-size copy under another
name (`DSC00913.jpeg` beside a bigger `DSC00913 2.jpeg` may be the original
of an edit). You can still tick any copy yourself.

### Setting copies aside

Pick a group to see every copy side by side. Tick or untick **Set aside**,
then **Set aside ticked copies**, or **Set aside all suggested...** for every
group at once. Either way you confirm first, the catalog is backed up, the
copies are renamed into the quarantine folder like exact copies, and their
stars, labels, albums and event move to the kept copy.

## Bursts

Frames from a burst are separate files with different contents and capture
times a fraction of a second apart, so they're never duplicates. The library
shows them as [stacks](Browsing.md#burst-stacks) instead.
