# Lunelis 0.33 - Developer Guide

How to rebuild Lunelis from nothing, how the code is laid out, and where to extend it.

The deep engineering notes are in `CLAUDE.md` at the repository root: every subsystem's rules and every bug that cost real debugging time. This guide is the map; `CLAUDE.md` is the territory.

## Rebuilding on a clean machine

These steps were run against the `v0.33.0` tag in a new folder with a new virtual environment. The results are under *What the clean build showed*.

### What you need

| Tool | Version used | Notes |
|---|---|---|
| Windows | 10 or 11, 64-bit | The program and its build are Windows-only. |
| Python | 3.13.x (3.13.1 used) | From python.org; the `py` launcher is assumed. 3.13 is the floor: the pinned PySide6 and rawpy wheels need it. |
| Git | any recent | Only to get the source. |
| PowerShell | 5.1 (built in) | Used by the self-test and the updater's swap script. |
| GitHub CLI `gh` | any recent, logged in | Only to look at releases and CI runs. |

No compiler, Node.js or Visual Studio is needed: every dependency installs from a binary wheel. Node.js and Word are needed only to rebuild this documentation.

### Steps

```
git clone https://github.com/AxialForge/Lunelis.git
cd Lunelis
git checkout v0.33.0
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pip install -e . pytest lupa "pyinstaller>=6.11"
.venv\Scripts\python -m pytest -q
.venv\Scripts\python packaging\make_version_info.py
.venv\Scripts\pyinstaller --noconfirm Lunelis.spec
dist\Lunelis\Lunelis.exe --self-test selftest.txt
type selftest.txt
```

- **The editable install matters.** `pip install -e .` puts the package metadata in place. The program reads its own version from that metadata, and the spec bundles it with `copy_metadata("lunelis")`.
- **`make_version_info.py` must run before PyInstaller.** It writes `packaging/version_info.txt` (the version in the exe's file properties) from `pyproject.toml`. A fresh clone cannot build without it.
- **Run the program from source** with `.venv\Scripts\python -m lunelis`.
- **Tests never touch real data.** `tests/conftest.py` points `LUNELIS_DATA_DIR` at a temporary folder for every test.

### What the clean build showed

Run on 2026-10-05, Windows 11, Python 3.13.1, from `git archive v0.33.0` into a new folder with a new virtual environment.

| Step | Result | Time |
|---|---|---|
| Install pinned requirements + `-e .`, pytest, lupa, PyInstaller 6.22.3 | OK, wheels only, no compiler | 55 s |
| `pytest -q` | **559 passed**, 1 skipped, 0 failed | 5 min 29 s |
| `make_version_info.py`, then `pyinstaller Lunelis.spec` | OK: `dist\Lunelis` is 440 MB | 55 s |
| `Lunelis.exe --self-test` | **PASS**: version 0.33.0, assets, darktable plugin, LibRaw 0.22.1, thumbnails, HEIC (libheif 1.23.4), video (PyAV 18.1.0), video playback (Qt Multimedia), catalog schema v37, editing and export with ICC, onnxruntime 1.30.0, lensfun and OpenCV 5.0.0, main window | 6 s |

The one skipped test needs the scene model, which a clean build has not downloaded. **Verdict: the release rebuilds cleanly from its tag.**

## Repository layout

| Path | What is there |
|---|---|
| `src/lunelis/` | The program: about 37,700 lines of Python. |
| `src/lunelis/ui/` | Every window, page, dialog and widget (47 modules). `main_window.py` holds the main window and the library worker thread; `create_page.py` the Create page and its 13 tools; `cull_view.py`, `video_player.py`, `map_view.py`, `calendar_view.py`, `autopilot_view.py`, `dust_view.py`, `share_dialog.py` the Phase 3 screens. |
| `src/lunelis/catalog/` | `schema.py` (all migrations and `open_catalog()`), `backup.py`, `ratings.py`, `exifblob.py`. |
| `src/lunelis/importers/`, `raw/` | Scanning, file-type sniffing, metadata, Takeout, video metadata, moved-file re-linking; previews and thumbnails. |
| `src/lunelis/importing/` | Card, phone and stick import, camera profiles, folder templates, `autopilot.py`. |
| `src/lunelis/xmp/` | XMP sidecar reading and writing. |
| `src/lunelis/jobs/`, `dupes/`, `damage/` | Jobs engine and rolling checks; exact and near duplicates, quarantine; damaged files. |
| `src/lunelis/events/`, `albums/`, `tags/` | Events and suggestions, albums and smart albums, tags. |
| `src/lunelis/recognize/` | The CLIP scene model (`clip.py`), scene labels and suggestions (`scenes.py`). |
| `src/lunelis/video/` | `trim.py` (stream copy), `slog.py` (S-Log3 detection), `lut.py` (looks and `.cube`). |
| `src/lunelis/create/` | `engine.py` (the shared export engine) and one module per tool family, including `stacking.py`. |
| `src/lunelis/migrate/`, `backups/` | Migration (plan, then execute); photo backups and `protection.py`. |
| `src/lunelis/edit/` | The editing engine (15 modules): stack, presets, pipeline, render, store, export, masks, AI, lens, merge, denoise, retouch, look, ICC. |
| `src/lunelis/darktable/` | `lunelis.lua` (the darktable plugin) and `bridge.py`. |
| `src/lunelis/` root files | `main.py` (start-up), `paths.py`, `settings.py`, `log.py`, `updater.py`, `selftest.py`, `search.py`, `ask.py`, `stacks.py`, `pairs.py`, `noticed.py`, `reach.py`, `dust.py`, `gallery.py`, `stats.py`, `history.py`. |
| `tests/` | 63 pytest files and `conftest.py` (throwaway data folder; crash dialogs become test failures). |
| `packaging/` | `make_version_info.py`, `release_notes.py`, `launch.py`, `publish_public.py` (for the archived Lunelis-releases repository only). |
| `Lunelis.spec` | The PyInstaller one-folder build. |
| `assets/` | Icons and logo. |
| `docs/wiki/` | The user wiki (Markdown), including `Feature-Plan.md` and `Roadmap.md`. |
| `docs/Audit-2026-10.md`, `docs/Interface-Audit.md`, `docs/Schemas.md` | The October audit, the interface audit, and the locked file formats. |
| `docs/_tools/` | The documentation tools that made this package. |
| `.github/workflows/python-release.yml` | CI: tests on every push; build, self-test and release on `v*` tags. |

## The catalog: schema and migrations

The catalog is one SQLite file, `catalog.db`, in the data folder. Code never opens it directly: `catalog.schema.open_catalog()` runs any new migrations and turns on foreign keys, WAL mode and `sqlite3.Row`. Before a schema upgrade it takes a catalog backup.

Every schema change is an entry appended to `MIGRATIONS` in `catalog/schema.py`. An entry is SQL or a Python function, and each runs all-or-nothing in its own transaction. Shipped migrations are never edited. Version 0.33.0 is at schema **37**:

| # | Migration |
|---|---|
| 1 | Initial schema: roots, files, exif, ratings, tags, albums, people, faces, duplicate_groups |
| 2 | Scan bookkeeping: files.missing_since, roots.last_scanned_at |
| 3 | exif: orientation, exposure compensation, flash, UTC offset, extraction bookkeeping; files.format |
| 4 | files.thumb_error: failed thumbnail attempts |
| 5 | Covering index for the library grid's date lookup |
| 6 | XMP sidecar sync: files.sidecar*, ratings.color_label / xmp_pending / xmp_error |
| 7 | settings: key/value store |
| 8 | exif.raw_json (text) to exif.raw_exif (zlib-compressed) |
| 9 | Jobs engine and duplicate detection: jobs, job_folders, sample_hash, quarantine |
| 10 | Moved-file re-linking log and damaged-file check |
| 11 | Case-insensitive file-name index |
| 12 | Video metadata and thumbnails, Google Takeout dates and GPS, date source |
| 13 | Card import: imports and import_items |
| 14 | Skipped folders |
| 15 | Events |
| 16 | Migration / consolidation |
| 17 | Backups: backup_sets and backup_files |
| 18 | Albums: cover photo, updated_at, lookups by photo |
| 19 | Burst stacks |
| 20 | Non-destructive edits and user filters |
| 21 | Imports: the new-import filter is applied once |
| 22 | Merges: HDR and panorama results and their sources |
| 23 | Tags: unique ignoring case; photos by tag |
| 24 | Search: the full-text index |
| 25 | purged: a record of every quarantined file emptied for good |
| 26 | Archive: files.archived_at |
| 27 | Imports: camera profile, sidecars filed with their clip, notes |
| 28 | Phones: import item kinds; motion photos |
| 29 | RAW+JPEG pairs |
| 30 | Scene tags: embeddings per photo per model; rejected suggestions |
| 31 | Lunelis noticed: suggestions |
| 32 | Rolling integrity checks (checked_at); backups looked up per file |
| 33 | Autopilot runs, stages, what they made, what waits for review |
| 34 | Edit stack version 2: stored v=1 stacks rewritten |
| 35 | Virtual copies |
| 36 | Sensor dust maps and heals |
| 37 | Family gallery shares |

Key shapes:

- **Paths are relative.** `files` is keyed by `(root_id, rel_path)`, so a remounted NAS doesn't orphan rows.
- **Ids are stable.** A file's catalog id never changes when the file moves; relinking and migration repoint the row.
- **"Live" files** are `missing_since IS NULL AND excluded = 0 AND quarantined_at IS NULL`. The library grid also hides archived photos (`archived_at`), which albums still show. Every new query over `files` must say which of these it means.

To add a migration: append `(next_number, "what it does", sql_or_function)`, add a test in `tests/test_schema.py`, and never rely on the live schema inside an older migration. File formats that leave the program (sidecars, edit stacks, presets, share pages) are locked in `docs/Schemas.md`.

## Configuration

### Environment variables

| Name | Used by | Purpose |
|---|---|---|
| `LUNELIS_DATA_DIR` | `paths.py`, tests, `docs/_tools` | Use this folder as the data folder. Tests and the documentation tools always set it. |
| `LOCALAPPDATA`, `APPDATA` | `paths.py`, `darktable/bridge.py` | The default data folder and darktable's config folder; `%APPDATA%\Lunelis\location.json` records a moved data folder. |
| `QT_QPA_PLATFORM`, `QT_QPA_FONTDIR` | `docs/_tools/capture.py` only | `offscreen` and the Windows font folder, for headless screenshots. |

There are no secrets: the program needs no keys or accounts. CI publishes with the workflow's own `GITHUB_TOKEN`.

### Settings

User settings are stored as JSON in the catalog's `settings` table. Each key has a default in `settings.DEFAULTS`, and `settings.validate()` checks every write, so a bad value never reaches the catalog. The user manual's *Settings reference* appendix lists every user-facing key. No default points at a particular PC or network: `import_destination` and `import_staging_network` start empty, and the first import asks.

### Runtime paths

| Path | What |
|---|---|
| `%LOCALAPPDATA%\Lunelis\` | The data folder (or wherever `location.json` / `LUNELIS_DATA_DIR` says). |
| `...\catalog.db` (+ `-wal`, `-shm`) | The catalog. Never open a live catalog from another process: copy all three files and open the copy with `immutable=1`. |
| `...\cache\thumbnails\<id // 1000>\<id>.jpg` | 512-pixel thumbnails, about 25 KB each. |
| `...\cache\edits`, `...\cache\masks` | Rendered previews of edited photos; AI masks. |
| `...\backups\` | Zipped catalog snapshots (daily, before jobs that move or remove files, and before schema upgrades). |
| `...\sidecars\<root id>-<root name>\...` | The central XMP sidecar store. |
| `...\logs\lunelis.log` | The log. |
| `...\models\` | AI models once downloaded: silueta 44 MB, skyseg 176 MB, CLIP about 155 MB; all pinned SHA-256. |
| `...\staging\import-<id>\` | Imports in progress. |
| `...\camera_profiles.json`, `create_presets.json` | Optional user additions to the built-in camera profiles and Create presets. |
| `<source>\_Lunelis Quarantine\` | Set-aside files, on the same drive as the source. |
| `Pictures\Lunelis creations` | Create and export output, unless Settings says otherwise. |
| `%APPDATA%\Lunelis\location.json` | A user-chosen data folder, and any pending move. |
| `HKCU\...\Run` | The Start-with-Windows entry, only when the user ticks it. |

## Tests and coverage

```
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m pytest tests\test_edit_engine.py -q     # one file
```

Measured in the clean build (2026-10-05): **559 tests passing, 1 skipped, in about 5 to 6 minutes**. Statement coverage with `pytest --cov=lunelis` (pytest-cov) is **80 %** overall (25,967 statements):

| Package | Statements | Covered |
|---|---|---|
| tags, events | 447 | 94-95 % |
| edit | 1,771 | 92 % |
| jobs, albums | 466 | 91 % |
| create | 1,081 | 90 % |
| dupes, video | 995 | 89 % |
| importing, xmp, catalog, damage, darktable, top-level modules | 3,951 | 88 % |
| migrate, importers | 1,378 | 86-87 % |
| raw | 313 | 81 % |
| backups | 389 | 77 % |
| ui | 14,848 | 74 % |
| recognize | 328 | 70 % (the CLIP model itself is not downloaded in tests) |

To measure it yourself: `pip install pytest-cov`, then `python -m pytest -q --cov=lunelis --cov-report=term`. (`coverage run -m pytest` recorded nothing in this setup; use pytest-cov.)

- **What is covered.** Most tests drive the core packages directly against temporary catalogs and folders. User-interface tests build real widgets offscreen and click real buttons. `conftest.py` turns the crash dialog into a test failure, so an exception can never hang the suite on a modal window.
- **What is not covered automatically.** Real network shares, real memory cards and phones, the Recycle Bin, the updater's file swap, and the family gallery on a real phone. These were checked by hand.
- **Rules for tests.** Never use the real catalog or the real photo library. Never open `MainWindow` on a copy of the real catalog, because its worker starts scanning the real sources. Run the full suite from a clean worktree before every release commit.

## Regenerating the documentation and screenshots

Everything in this package is made by the tools in `docs/_tools/` from the real program, on a generated demo library with invented photos, names and places:

```
set DEMO=C:\Users\Public\Lunelis Demo
set PKG=docs\release-package\0.33.0
.venv\Scripts\python docs\_tools\demo_library.py "%DEMO%"
.venv\Scripts\python docs\_tools\capture.py "%DEMO%" %PKG% graphite
.venv\Scripts\python docs\_tools\build_inventory.py %PKG%
.venv\Scripts\python docs\_tools\prepare_manual.py %PKG%
.venv\Scripts\python docs\_tools\build_release_history.py %PKG%
cd docs\_tools && npm install && cd ..\..
node docs\_tools\build_manual.js %PKG%
node docs\_tools\md2docx.js docs\_tools\content\release_overview.md %PKG%\RELEASE_OVERVIEW.docx "Lunelis Release Overview" landscape
node docs\_tools\md2docx.js docs\_tools\content\install_guide.md %PKG%\INSTALL_GUIDE.docx "Lunelis Install Guide"
node docs\_tools\md2docx.js docs\_tools\content\developer_guide.md %PKG%\DEVELOPER_GUIDE.docx "Lunelis Developer Guide"
set MD_NO_BREAKS=1 && node docs\_tools\md2docx.js %PKG%\RELEASE_HISTORY.md %PKG%\RELEASE_HISTORY.docx "Lunelis Release History"
powershell -File docs\_tools\to_pdf.ps1 %PKG%\USER_MANUAL.docx %PKG%\RELEASE_OVERVIEW.docx %PKG%\INSTALL_GUIDE.docx %PKG%\DEVELOPER_GUIDE.docx %PKG%\RELEASE_HISTORY.docx
.venv\Scripts\python docs\_tools\qa.py %PKG%
```

- **Demo folder.** The demo library must live in a folder whose path holds no user name, such as `C:\Users\Public\Lunelis Demo`. Screenshots show source paths. `capture.py` also sets a made-up user profile.
- **How capture works.** `capture.py` runs Qt offscreen at exactly 100 % scale and opens every page, tab, dialog, menu and Create tool. It replaces `exec()` on dialogs so they open without blocking, and every opened dialog is closed again before the next screen. It records each control's position and the source line that creates it, then draws the numbered callouts.
- **Manual text.** The manual's text is in `docs/_tools/manual/*.json` (`newer.json` and `create.json` hold the chapters added since 0.12). `prepare_manual.py` fails loudly if a control has no description.
- **Tool requirements.** Word (for the PDFs, through COM), Node.js with the `docx` and `@mermaid-js/mermaid-cli` packages, and Chrome for Mermaid (`docs/_tools/puppeteer.json`).
- **The package folder is not in git.** `docs/release-package/` is ignored; the public repository holds the tools, not the outputs.

## Extension points

| To add... | Do this |
|---|---|
| A page in the sidebar | Write a `QWidget` in `ui/`, add it to `MainWindow` (the `NAV` list near the top of `main_window.py`, and `open_page`), and give it a wiki page and a manual chapter. Colours come only from `ui/theme.py` tokens. |
| A setting | Add the key and default to `settings.DEFAULTS`, a check to `settings.validate()` if it has limits, and a control on the right card in `ui/settings_view.py` (it saves on change). |
| An edit slider | Add a `Param` to `edit/stack.py`, handle it in `edit/pipeline.py`, and test it in `tests/test_edit_engine.py`. Unknown keys in a stored stack are ignored, so older stacks keep working. |
| A filter (look) | Add an entry to `BUILTIN` in `edit/presets.py`. |
| A mask kind | Add it to `KINDS` and the shape builders in `edit/masks.py`, and a button in `ui/develop.py`. AI kinds go through `edit/ai.py`, which needs a model URL and a pinned SHA-256. |
| A Create tool | Subclass `Tool` in `ui/create_page.py`, add it to `TOOLS`, and do the work in a `create/` module that reads photos with `engine.photo()` and writes with `engine.save()` (a new file, never an overwrite). |
| A Lunelis noticed kind | Add it to `KINDS` in `noticed.py` with a detector that checks the pictures, not only the EXIF, and a Build it action. |
| An Autopilot stage | Add it to `STAGES` in `importing/autopilot.py`, record what it made so Undo can remove exactly that, and add its review row in `ui/autopilot_view.py`. |
| A background job kind | Add it to `KINDS` in `jobs/engine.py` with a per-folder function. Jobs get pause, idle-only, night-only, speed limit and offline retry for free. |
| A search field | Add a column to `search_fts` in a new migration, fill it in `search.refresh()`, and add the word to `FIELD_ALIASES`. |
| A file format | Add the extension to `importers/formats.py` and teach `sniff()` its bytes, then a metadata reader in `importers/metadata.py` and a thumbnail path in `raw/thumbnails.py`. |
| A catalog table or column | A new migration (see above); never edit a shipped one. |

## Releasing

Each version is committed, pushed, tagged and green in CI before work on the next one starts.

1. Run the full test suite from a clean worktree.
2. Update `CHANGELOG.md` and bump `version` in `pyproject.toml` in one commit (`Release X.Y.Z: <what>`).
3. Check that nothing personal is in the tree (names, home network addresses, local user paths): the repository is public.
4. Push `main`, then `git tag vX.Y.Z` and `git push origin vX.Y.Z`.
5. CI tests, runs `make_version_info.py`, builds with PyInstaller, runs `Lunelis.exe --self-test`, zips the build with a `.sha256`, and attaches it all to a GitHub Release on AxialForge/Lunelis with the CHANGELOG notes.
6. Check that the updater sees it: Settings > Updates > Check for updates on an older install.

Never upload a hand-built zip: CI builds every release.
