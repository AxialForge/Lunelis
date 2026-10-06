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
pillow-heif · PyAV (FFmpeg). No telemetry and no accounts. Network use is
limited to the daily update check (on by default, switchable), downloads the
user starts (updates, models), OpenStreetMap tiles when the online map is on,
and the LAN-only family gallery server while an album is shared - listed in
[Safety and backups](Safety-and-Backups#what-goes-over-the-network).

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

## Building the program and the installer

```bash
python packaging\make_version_info.py
pyinstaller --noconfirm Lunelis.spec              # dist\Lunelis\Lunelis.exe
dist\Lunelis\Lunelis.exe --self-test selftest.txt
iscc /DAppVersion=X.Y.Z packaging\installer\lunelis.iss   # dist\Lunelis-vX.Y.Z-setup.exe
```

CI does all of this on a version tag, then installs the setup.exe silently on
the runner, self-tests the installed copy and uninstalls it, before attaching
the zip and the setup.exe to the release. Inno Setup 6 is free; nothing is
code-signed.

How the installer and the program share the work:

- **The installer asks, the program applies.** The setup pages write
  `%APPDATA%\Lunelis\setup.json`; the next start applies it once
  (`firstrun.py`) and renames it `setup.applied.json`. The installer never
  opens the catalog.
- **Installed once.** Updates come from inside the program (`updater.py`
  swaps the whole program folder), carrying the uninstaller in
  `<program>\uninstall` across and updating the version in Settings > Apps.
  A setup.exe run over an existing install skips the setup pages.
- **The program folder holds only Lunelis.** The installer refuses a
  non-empty folder: updates replace that folder and uninstalling removes it.

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
