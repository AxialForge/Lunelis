# Quarantine

Lunelis never deletes a photo on its own. When it removes a copy, it
**renames** it into a `_Lunelis Quarantine` folder on the same drive. That
happens when you:

- set aside exact [duplicates](Duplicates.md) or near-duplicates;
- skip duplicate copies during a [migration](Migration.md);
- move originals out of the way after a migration.

The **Quarantine** page (sidebar > Keep safe) lists all of it.

## What the page shows

For each file:

- where it was and where it is now;
- **why** it was set aside: exact copy, near-duplicate, migration copy, or
  migrated original;
- when, and its size;
- **the kept copy**: the copy that made setting this one aside safe. A ✓
  means it's there. A ⚠ means it can't be found (or is a different size),
  and then that file will **not** be emptied.

The top line totals the files and space per drive. **Show** narrows the
list to one reason.

## Restoring

Select files and click **Restore**: each goes back exactly where it came
from. If something already sits at that spot, that file stays in
quarantine and the page says why. A migrated original that comes back is a
second copy of a photo that now lives in its new place, so the next scan
lists it again.

## Emptying

**Empty selected...** or **Empty all...** removes files for good:

- files on this PC go to the **Recycle Bin**, so you can still get them
  back from there;
- files on network drives (your NAS) are **deleted**. Windows has no
  Recycle Bin for network shares, so you must tick "I understand the
  network files can't be recovered" first;
- a file whose kept copy is missing or different stays in quarantine,
  always;
- the catalog is backed up first, and every file removed is written to a
  log in the catalog (`purged`: where it was, its size and fingerprint, why,
  and the copy that was kept).

Emptied files leave the catalog. Their stars, labels, albums and events
were moved to the kept copy when they were set aside.
