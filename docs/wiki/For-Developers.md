# For developers

The detailed engineering guide is [`CLAUDE.md`](../../CLAUDE.md) at the root
of the repository: non-negotiables, the architecture map, the rules for each
subsystem, and a **Gotchas** section recording every bug that cost real
debugging time and why the obvious fix is wrong. Read it before changing
anything structural.

## Locked formats

The edit stack, the tag schema (user / auto tags, embeddings), camera
profiles and what counts as a keeper are written down in
[`docs/Schemas.md`](../Schemas.md) and pinned by `tests/test_schemas_locked.py`.
Change one only with a version bump and a loader for the old form.

## Stack

Python 3.13 · PySide6 (Qt) · SQLite · exifread · rawpy (LibRaw) · Pillow +
pillow-heif · PyAV (FFmpeg). No server, no network calls.

## Running

```bash
py -3.13 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt -e .
python -m pytest        # the full test suite
python -m lunelis       # the app
```

Tests always run against a throwaway data folder (`tests/conftest.py` sets
`LUNELIS_DATA_DIR`), so they can never touch a real catalog.

## How a photo gets into the grid

```
scan (importers/scan.py)          list folders, sync the `files` table
  -> relink (importers/relink.py) moved files keep their catalog entry
  -> sidecars (xmp/sync.py)       ratings/labels from existing XMP
  -> metadata (importers/metadata.py, video.py)
  -> Takeout (importers/takeout.py)
  -> thumbnails (raw/thumbnails.py, raw/previews.py)
  -> damage check (damage/check.py)
grid (ui/grid.py) <- LibraryIndex (ui/library.py) <- thumbnails via ui/thumbcache.py
```

Long work runs through the jobs engine (`jobs/engine.py`): a job is a list of
folders, each marked done as it finishes.

## Conventions

- Schema changes are **migrations only** (`catalog/schema.py`), appended to the
  list, never edited once shipped.
- Colours come only from `ui/theme.py` tokens.
- Commit messages: imperative, describing the behaviour change.
- Update `CHANGELOG.md` with every user-visible change and `CLAUDE.md` when
  something bites.

## Editing this wiki

The wiki is the Markdown files in `docs/wiki/`. Link pages with relative
links (`[Duplicates](Duplicates.md)`); GitHub renders them in the repository.
