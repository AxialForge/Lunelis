---
type: audit
title: Documentation and Code Consistency Audit
subtitle: Does what Lunelis says about itself match what the code does? Version 0.46.0
audience: Owners and maintainers
sources: Lunelis 0.46.0 (commit d3bada9). CLAUDE.md, CHANGELOG.md, module docstrings, Settings and UI strings, docs/wiki, installer text, checked against src/lunelis
status: Draft
---

# Verdict

Lunelis does what its safety rules say it does. The rules that matter most (never touch originals, verify before removing, migrations only, no telemetry) are enforced by named functions, and 15 such claims are confirmed in this report. The written material around the code has drifted. We verified 42 findings: **2 high, 16 medium and 24 low**. Nothing is fixed yet.

The two high findings are about trust, not lost files. The installer promises that Lunelis "never writes into your photo folders", and it does write there when you ask it to and, by default, into sidecars that already exist. The migration wizard also puts the Lunelis folder inside the new library, so the next rescan catalogs the Trash and Duplicates files as photos.

**Do first:** reword the installer sentence (one line) and stop the scanner from walking the Lunelis folder. Both are small.

::: stats
42 | verified findings
2 | high
16 | medium
24 | low
0 | fixed so far
:::

::: note How to read this report
Every finding was re-checked against the code at 0.46.0. A finding names the file and line, says who is affected, and says which line to change and to what. The "Checked and sound" chapter lists claims that were tested and are true, so the report is not only bad news.
:::

# Scope and method

This audit compares what Lunelis says about itself with what the code does. It does not judge the code's design or hunt for bugs, except where a bug makes a written promise false.

| In scope | Out of scope | How it was reviewed |
|:--|:--|:--|
| CLAUDE.md rules and gotchas | Code quality and style | Opened the cited file and line |
| CHANGELOG.md entries | Performance figures | Searched for every writer of the data |
| Module docstrings and comments | Windows-only behaviour | Ran one scratch scan on made-up folders |
| Settings help text and UI strings | The darktable plugin inside darktable | Read the enforcing function |
| docs/wiki pages | Tests (not run) | Counted and merged duplicates |
| Installer text (lunelis.iss) | Screenshots and layout | Rated with the rubric below |

## Severity rubric

| Level | Meaning | Typical example |
|:--|:--|:--|
| [high] | Could make a user lose data or trust: text promises a guarantee the code does not give, or a safety rule is documented wrongly | "We never write there", when we do |
| [medium] | Misleads a user about what the program does, or where a setting or file is | A setting named in the wrong tab |
| [low] | Stale comment or docstring with no user effect | A docstring that counts three tools when there are thirteen |

## How the raw material was handled

Seven discrepancy lists were written during a documentation pass. They held 70 items, each with file and line evidence, plus matching lists of things "to check".

| Step | Count |
|:--|:--|
| Raw discrepancy items read | 70 |
| Dropped after checking (not a real mismatch) | 7 |
| Merged into another item (same fact in several lists) | 25 |
| Verified findings that came from the raw lists | 38 |
| New findings from the "to check" lists and from this review | 4 |
| Verified findings in this report | 42 |

Items dropped: the "no File menu" remark (a wish, not a mismatch), the Ctrl+F menu remark (works as designed), the stats recap folder (a point about a guide, not about the repository), the installer licence file's line endings (same words as LICENSE), the move_one docstring (accurate, because migration calls it), the updater's ".old-" folder name (documented correctly) and the CHANGELOG "K only" play key (a later entry adds Space).

Duplicates merged: the video thumbnail claim appeared in four lists, the place count in two, the quarantine location in four, the `trash_keep_days` comment in four, the daily-backup claim in three, and the "no Lunelis folder" claim in three.

# Findings at a glance

```chart Findings by area and severity (42 verified findings)
{"type":"stacked","labels":["Safety and files","Settings and UI text","Network and privacy","Docs and wiki","Code comments"],"series":[{"name":"High","values":[2,0,0,0,0],"color":"#A3203A"},{"name":"Medium","values":[7,4,1,4,0],"color":"#E0A030"},{"name":"Low","values":[0,4,1,10,9],"color":"#6C8EBF"}]}
```

Safety and files has the fewest findings but the heaviest ones. Docs and wiki has the most, nearly all low: old statements that nobody went back to fix after a release.

| Area | High | Medium | Low | Total |
|:--|:--|:--|:--|:--|
| Safety and files | 2 | 7 | 0 | 9 |
| Settings and UI text | 0 | 4 | 4 | 8 |
| Network and privacy | 0 | 1 | 1 | 2 |
| Docs and wiki | 0 | 4 | 10 | 14 |
| Code comments | 0 | 0 | 9 | 9 |
| Total | 2 | 16 | 24 | 42 |

# Findings

All findings are open. Every high and medium finding has a detail block in the next chapter. Low findings are explained in the table.

## Safety and files

| ID | Finding | Severity | Status |
|:--|:--|:--|:--|
| D1 | Installer says Lunelis never writes into photo folders | [high] | [open] |
| D2 | Lunelis folder sits inside the migration target, so a rescan catalogs Trash and Duplicates | [high] | [open] |
| D3 | Text says originals go to a quarantine folder on their own drive; with a Lunelis folder they go to Trash | [medium] | [open] |
| D4 | Restoring a migrated original uses a plain rename and fails across drives | [medium] | [open] |
| D5 | Snapshot pruning treats all backup kinds alike; the "prune nothing" comment is half true | [medium] | [open] |
| D6 | Catalog backup runs only at start-up, though Settings says "every 24 hours" | [medium] | [open] |
| D7 | The start-up "Restore the newest backup" button ignores a moved backup folder | [medium] | [open] |
| D8 | CLAUDE.md says empty() is the only hard removal; Clear the card also deletes | [medium] | [open] |
| D9 | Keeper ranking finds "takeout" anywhere in the path, against the documented rule | [medium] | [open] |

## Settings and UI text

| ID | Finding | Severity | Status |
|:--|:--|:--|:--|
| D10 | "Only in the Lunelis catalog" says no sidecar files at all, but existing ones are still updated | [medium] | [open] |
| D11 | Settings and wiki say a darktable plugin is planned; it exists | [medium] | [open] |
| D12 | Migration wizard describes the kept copy in a different order than the code ranks it | [medium] | [open] |
| D13 | Scene model error points to "Settings > AI", which does not exist | [medium] | [open] |
| D14 | Edit setting says "half size while a slider moves"; the code caps at 960 px | [low] | [open] |
| D15 | Shortcut sheet leaves out the Up, Down, Page Up and Page Down keys | [low] | [open] |
| D16 | Lens guide names "Develop > Lens > Use the lens profile" | [low] | [open] |
| D17 | Place count is given as 32,000 and ~34,000; the list has 31,735 | [low] | [open] |

## Network and privacy

| ID | Finding | Severity | Status |
|:--|:--|:--|:--|
| D18 | Wiki network table gets the model host, the map setting and "Open map" wrong | [medium] | [open] |
| D19 | CHANGELOG says the daily update check is the only network use | [low] | [open] |

## Docs and wiki

| ID | Finding | Severity | Status |
|:--|:--|:--|:--|
| D20 | Wiki says writing to an existing sidecar changes "only the rating and label" | [medium] | [open] |
| D21 | Wiki puts the S-Log3 setting in Appearance; it is on the Library tab | [medium] | [open] |
| D22 | Edit stacks are written to sidecars but never read back; no text says so | [medium] | [open] |
| D23 | Licence is stated as MIT in the app and README, GPL for the Windows build elsewhere | [medium] | [open] |
| D24 | CLAUDE.md says video has no thumbnails and no metadata reader | [low] | [open] |
| D25 | CLAUDE.md lists three columns cleared on change; the code clears five | [low] | [open] |
| D26 | CLAUDE.md gives the thumbnail folder without the four-digit padding | [low] | [open] |
| D27 | CLAUDE.md says face recognition stays deferred; it is built | [low] | [open] |
| D28 | "Library menu = dev trigger until Step 9" is still in CLAUDE.md | [low] | [open] |
| D29 | CLAUDE.md says free collage cells have no UI; they do | [low] | [open] |
| D30 | Noise reduction is documented at the end of the pipeline; it runs first | [low] | [open] |
| D31 | Old CHANGELOG entries name the wrong Settings tab for three settings | [low] | [open] |
| D32 | Wiki gives the idle and overnight job times as fixed values | [low] | [open] |
| D33 | CLAUDE.md does not say the update log is deleted after a good update | [low] | [open] |

## Code comments

| ID | Finding | Severity | Status |
|:--|:--|:--|:--|
| D34 | Three comments in settings.py are on the wrong line or say the wrong thing | [low] | [open] |
| D35 | Duplicate-detection docstring says candidates share a size; they also share a capture time | [low] | [open] |
| D36 | Near-duplicate docstring lists absolute rules that have exceptions; one constant is unused | [low] | [open] |
| D37 | Export docstring says an sRGB profile is embedded; the profile is the user's choice | [low] | [open] |
| D38 | Masks comment says hue and fade are global only; they are in LOCAL_KEYS | [low] | [open] |
| D39 | Four page docstrings give the wrong count or name | [low] | [open] |
| D40 | Video and timelapse leftovers: trim docstring, button names, dead extensions, dead entries | [low] | [open] |
| D41 | AVIF is sniffed and listed in a docstring, but .avif files are never cataloged | [low] | [open] |
| D42 | Three small docstring slips: tag separator, SFace size, first-run model list | [low] | [open] |

# Finding details

Detail blocks for every high and medium finding. Each one gives the evidence, who is affected and the change to make.

## Safety and files

### D1: Installer promises Lunelis never writes into photo folders [high]

**Evidence:** `packaging/installer/lunelis.iss:276` says "Lunelis never writes into your photo folders." The code writes there in at least five ways: `update_existing_sidecars` defaults to on (`settings.py:24`) and `export_pending` rewrites a sidecar that already sits beside a photo (`xmp/sync.py:248`); the "Next to each photo" mode creates new `.xmp` files; a card import places files in the library; a migration copies into a target; Clear the card and quarantine move files in source folders.

**Impact:** A person who reads this on the first setup page may point Lunelis at a read-only archive or a NAS they want left alone. The first rating on a photo with a darktable sidecar rewrites that sidecar. CLAUDE.md states the rule correctly ("Photo folders only ever gain files the user asked for"), so only the installer is wrong.

**Recommendation:** In `lunelis.iss:276` replace the sentence with: "Lunelis does not change your photos. It only writes in your photo folders when you ask: a sidecar next to a photo, a card import, or a migration." Add the same wording to the Welcome window if it repeats the claim.

### D2: Lunelis folder is created inside the migration target [high]

**Evidence:** The wizard makes the Lunelis folder `<target>\Lunelis` (`ui/migration_wizard.py:331-332` and `:417`). `migrate/execute.py:235` then adds the whole `<target>` as a source with `add_root`. The scanner skips only the names in `SKIP_DIR_NAMES` (`importers/scan.py:33-36`), and "lunelis" is not one. Settings refuses a Lunelis folder inside a source (`ui/settings_view.py:1431-1434`), but the wizard does not go through that check. A scratch scan of a made-up folder holding `Library\2024\a.jpg` and `Lunelis\Trash\Migration 1\src\b.jpg` cataloged both files.

**Impact:** After the first rescan, the migrated originals in `Trash` and the copies in `Duplicates` appear in the grid as new photos, and the duplicate finder flags them against the library copy. Settings text says "Your Library then holds only photos and videos", which is not true. No file is lost: the keeper rule usually prefers the library path, and the copies are byte-identical. It does erode trust in a migration.

**Recommendation:** In `importers/scan.py`, skip the Lunelis folder when walking a source (compare against `lunelis_folder.root(settings)`), or add `Staging`, `Trash`, `Duplicates` and `Migration logs` to the walk's skip list. Also run the Settings overlap check in the wizard. Not done here: the full wizard was not run, only the scan (see To check).

### D3: Text puts set-aside originals on "their own drive" [medium]

**Evidence:** With a Lunelis folder set, `migrate/execute.py:112-124` (`set_aside_base`) sends migration originals to `<Lunelis folder>\Trash\Migration N\...` and unkept copies to `Duplicates\...`. Only without a Lunelis folder does it use `_Lunelis Quarantine\migration-N` on the file's own drive. The wizard always sets the folder (`ui/migration_wizard.py:417`). Text that still says "own drive": `ui/quarantine_view.py:191-195`, `ui/migrate_view.py:537`, `docs/wiki/Migration.md:10, 66, 105`, and CLAUDE.md lines 30 and 331. The wiki never mentions the Lunelis folder at all.

**Impact:** A person looking for a migrated original looks next to the source and does not find it. If the Lunelis folder is on another drive, "on the same drive" is false, and the non-negotiable "Removals go to a quarantine folder on the same drive" does not hold for migrations.

**Recommendation:** Change the Quarantine page hint (`quarantine_view.py:191`) to "Each sits in a _Lunelis Quarantine folder on its own drive, or in the Lunelis folder's Trash or Duplicates." Make the same change at `migrate_view.py:537` and in `Migration.md` lines 10, 66 and 105. In CLAUDE.md line 30 add "(or the Lunelis folder's Trash/Duplicates)". Add a short Lunelis folder section to the wiki.

### D4: Restoring a migrated original cannot cross drives [medium]

**Evidence:** `dupes/manage.py:181` (`restore_entry`, entries whose key starts with "m") calls `os.rename(e.now, e.original)`. Everything that sets files aside goes through `move_one`, which falls back to copy, compare and remove across drives (`dupes/quarantine.py:119-129`). Restore does not. It also moves the photo only, not its sidecar.

**Impact:** When the Lunelis folder is on another drive from the old source, "Restore" on the Quarantine page fails with an error. The file stays in Trash, so nothing is lost, but the one-click undo the page promises does not work. Not run on Windows (see To check).

**Recommendation:** In `restore_entry`, replace the `os.rename` with `move_pair(e.now, e.original, ...)` from `dupes/quarantine.py`, passing the sidecar when the migration item recorded one.

### D5: Snapshot pruning treats every backup kind alike [medium]

**Evidence:** `catalog/backup.py:65-68` deletes every snapshot beyond `catalog_backups_keep` (default 10, `settings.py:29`) in the folder, whatever its kind. `catalog/schema.py:959` passes `keep=1_000_000` and comments "prune nothing", which is true only for that one call. The next daily, manual or before-job snapshot prunes the before-upgrade file along with the rest.

**Impact:** The before-upgrade snapshot exists because an older Lunelis cannot open an upgraded catalog ("the way back", `schema.py:951`). Ten later snapshots push it out. (With a Lunelis folder set, daily snapshots go to a different folder, so the clash applies mainly to the default setup.) A user who wants to go back to the previous version after a few busy days has no snapshot from before the upgrade.

**Recommendation:** In `backup.snapshot`, prune only files of the same reason prefix, or never prune names that start with `before-upgrade`. Change the `schema.py:959` comment to "this call prunes nothing; later snapshots keep these". Document the retention rule in `Safety-and-Backups.md`.

### D6: Catalog backup runs once per start-up [medium]

**Evidence:** `ui/main_window.py:788` schedules `_daily_backup` once, 3 seconds after start. The worker calls `snapshot_if_due` (`:339`), which compares the newest snapshot with `catalog_backup_every_hours` (`catalog/backup.py:78-85`). No timer repeats it. Settings labels the control "Back up every [24] hours" (`ui/settings_view.py:1398`) and CLAUDE.md line 23 says "snapshotted daily". The wiki is closer: "once a day when it starts".

**Impact:** A PC that is left on for a week with Lunelis open gets one backup at start and then only the "before a job" snapshots. A shorter interval changes nothing while Lunelis stays open. Picks and albums live only in the catalog, so this is the weakest point of the safety net.

**Recommendation:** Either add a repeating timer that calls `_daily_backup` every `catalog_backup_every_hours`, or change the Settings label to "Back up at start-up if the newest backup is older than [24] hours" and edit CLAUDE.md line 23 to "daily, checked at start-up". The timer is the better fix.

### D7: Start-up restore offer ignores a moved backup folder [medium]

**Evidence:** `main.py:151` looks for snapshots only in `paths.DATA_DIR / "backups"`. `catalog/backup.py:33-36` (`backup_dir`) honours the `catalog_backup_dir` setting and, failing that, `<Lunelis folder>\Backups\Catalog`. The migration wizard sets a Lunelis folder, so after a migration the daily snapshots land outside `data\backups`. CLAUDE.md also says backups "live in the data folder".

**Impact:** If the catalog is damaged at start, the dialog offers "Restore the newest backup" only when the old folder holds a snapshot. For anyone with a Lunelis folder or a moved backup folder, no button appears, and the person has to know the command line `--restore`.

**Recommendation:** In `main.py:151` read the setting the same way `backup_dir` does (open the catalog read-only, or read `location.json`), or search both `data\backups` and the Lunelis folder. Update the non-negotiable wording in CLAUDE.md to "the data folder, or the Lunelis folder when one is set".

### D8: "The only hard removal" is not the only one [medium]

**Evidence:** CLAUDE.md line 443: "`empty()` is the only hard removal in Lunelis". `importing/ingest.py:715` (`clear_card`) calls `os.remove(path)` on the memory card file. It runs only after the whole import is verified, re-compares size, time and full SHA-256 against the library copy (`:707-712`), and is the user's explicit action after a warning (`:692-694`). `dupes/manage.py` `empty()` also deletes on network and removable volumes with `os.remove` (`:253`).

**Impact:** The rule is a safety summary used when adding features. Anyone who trusts it will not look for the card delete, and it hides that two features remove data for good. The card delete itself is well guarded.

**Recommendation:** Reword CLAUDE.md line 443 to "`empty()` and `ingest.clear_card()` are the only hard removals; both are user-triggered, verified against a kept copy, and never run by a job."

### D9: Keeper ranking treats any "takeout" in the path as Takeout [medium]

**Evidence:** CLAUDE.md line 850 says "takeout" in the full path is not a Takeout export; only the root's own folder name counts. `dupes/detect.py:243` (`keeper_rank`) and `migrate/plan.py:115` (`migration_keeper_rank`) both test `"takeout" in (root + "/" + rel).lower()`. `dupes/similar.py:346-357` uses `takeout_roots()` correctly. The Duplicates page and Settings say "never a Google Takeout copy" (`ui/settings_view.py:1350`).

**Impact:** A folder called `Takeout trip 2022` that holds ordinary photos ranks below its duplicates, and so is the copy chosen for Duplicates or Trash. Nothing is lost (copies are verified identical, user data is merged), but the wrong copy is kept and the written rule says it cannot happen.

**Recommendation:** In both functions, replace the string test with membership in `set(takeout_roots(conn))`, as `similar.keeper_rank` does. Add a test with a root whose path contains "takeout" in a parent folder.

## Settings and UI text

### D10: "Only in the Lunelis catalog" says no sidecar files at all [medium]

**Evidence:** `ui/settings_view.py:39-40` describes the mode: "No sidecar files at all." `xmp/sync.py:248` still writes a sidecar that exists beside the photo when `update_existing_sidecars` is on, in every mode, and that setting defaults to on (`settings.py:24`). The checkbox below the radio buttons says so (`settings_view.py:1325-1327`), but the radio text claims otherwise.

**Impact:** A person who picks this mode to keep a NAS read-only still has existing darktable sidecars rewritten after each rating.

**Recommendation:** Change the text to "Lunelis creates no sidecar files. Sidecars that already exist are still kept up to date unless you untick the box below." Better still, grey out the radio while the checkbox is on.

### D11: A darktable plugin is "planned", but it exists [medium]

**Evidence:** `ui/settings_view.py:36` says of the central folder: "darktable and Lightroom don't look there (a darktable plugin is planned)." `docs/wiki/Ratings-Labels-and-Sidecars.md:80-81` says the same, as a roadmap item. The plugin ships as `darktable/lunelis.lua`, installs from the Settings darktable tab, and has its own wiki page `darktable.md`.

**Impact:** People who use darktable will not look for the plugin and will pick "Next to each photo" to get their ratings across.

**Recommendation:** Change both texts to "darktable and Lightroom don't look there. For darktable, install the Lunelis plugin (Settings > darktable)." Remove the roadmap sentence from the wiki heading "Why darktable can't see the central folder".

### D12: The migration wizard describes the kept copy in the wrong order [medium]

**Evidence:** `ui/migration_wizard.py:204-207` says the kept copy is the one "with your work on it ... else the older file; not a Google Takeout copy". `migrate/plan.py:93-117` ranks: preferred source first, then not a Takeout export, then your work, then older, then shortest path. CHANGELOG 0.41.0 matches the code. The Duplicates page uses a third order (`dupes/detect.py:234-246`).

**Impact:** A person who has work on a Takeout copy expects it to be kept. The code keeps the non-Takeout copy and merges the work into it. The result is safe, but not what the text predicts.

**Recommendation:** Rewrite the wizard text as: "Lunelis keeps the copy from your preferred source, else one that is not a Google Takeout copy, else the one with your work on it, else the older file. Ratings, tags and albums from the other copies move to it."

### D13: The scene error points to a tab that does not exist [medium]

**Evidence:** `recognize/scenes.py:173` raises "the scene model isn't installed (Settings > AI)". The settings tabs are General, Edit, Appearance, Library, Import, Ratings and sidecars, Duplicates and jobs, Backups, darktable, Updates and Advanced (`ui/settings_view.py:172-184`). The Scene tags card is on the Library tab.

**Impact:** The person sees this message when they ask for scene tags before downloading the model, then searches Settings for an "AI" tab.

**Recommendation:** Change the message to "the scene model isn't installed (Settings > Library > Scene tags)".

## Network and privacy

### D18: The wiki network table has three errors [medium]

**Evidence:** `docs/wiki/Safety-and-Backups.md:22-25`.

- Row "When you click Download" says models come "from GitHub". The scene model (`recognize/clip.py:32`) and the sky model (`edit/ai.py:50`) come from huggingface.co. The face models (`recognize/faces.py:50`) and the subject model (`edit/ai.py:46`) come from github.com.
- Row "online map" gives the setting as "Settings > Places". The checkbox is on the Library tab, in the Places card (`ui/settings_view.py:172-174`, `:566`).
- Row "Open map" says the link opens openstreetmap.org in your browser. It opens Lunelis's own Map unless the setting "A photo's location in Info opens" is set to "OpenStreetMap in the browser" (`ui/main_window.py:2410-2415`; default "map", `settings.py:115`).

**Impact:** This table is the one place a privacy-minded reader looks to see which hosts Lunelis may contact. A missing host (Hugging Face) is the most serious part. The other two make the text more cautious than the code, which is harmless, but wrong.

**Recommendation:** Edit the table: models "from GitHub or Hugging Face (huggingface.co), checked against a SHA-256 stored in the program"; map setting "Settings > Library > Places > Show map pictures on the Map"; "Open map" row "Only if you set photo locations to open in the browser". Also change the sentence above the table that says only "Open map" passes coordinates.

## Docs and wiki

### D20: The wiki says an existing sidecar only gets rating and label [medium]

**Evidence:** `docs/wiki/Ratings-Labels-and-Sidecars.md:69-70`: "it changes only the rating and label". `xmp/sidecar.py:331-356` (`apply_fields`) also writes tags (`dc:subject`, `lr:hierarchicalSubject`) and `lunelis:EditStack`. The same wiki page documents both further up (lines 45-58).

**Impact:** The page contradicts itself. The false line is the one a worried darktable user reads before allowing Lunelis to update their sidecars.

**Recommendation:** Replace the sentence with: "Lunelis changes the rating, label, tags and its own `lunelis:EditStack` entry. darktable's edit history and everything else in the file stay byte-for-byte as they were."

### D21: The wiki puts the S-Log3 setting in the wrong tab [medium]

**Evidence:** `docs/wiki/Videos-and-Google-Takeout.md:57` says "Settings > Appearance > S-Log3 videos". The setting is in the card "Shoots, videos and the autopilot" on the Library tab (`ui/settings_view.py:172-177`, `:433-462`). `docs/wiki/Settings.md:99` has it right.

**Impact:** A person who wants to change the S-Log3 look opens Appearance and does not find it.

**Recommendation:** Change line 57 to "Settings > Library > Shoots, videos and the autopilot". CHANGELOG 0.24.0 has the same old wording (see D31).

### D22: Edits are written to sidecars but never read back [medium]

**Evidence:** The edit stack goes out as `lunelis:EditStack` (`xmp/sync.py:228`, `xmp/sidecar.py:347-351`). `xmp/sidecar.py` parses it into `XmpFields.edit`, but `import_sidecars` in `xmp/sync.py` reads only stars, flag, label and keywords (`:127-140`), and a search of `src/` finds no other caller that applies it. `docs/wiki/Editing.md:318-321` and `Ratings-Labels-and-Sidecars.md:51` say edits are "stored" in the sidecar. CLAUDE.md says "Edits are an instruction stack in XMP".

**Impact:** A reader will assume the sidecar is a backup of the edit. If the catalog is lost and not restored from a backup zip, the edits are gone, even though the sidecars hold them. Nothing in the text warns about this.

**Recommendation:** Add to both wiki pages: "Lunelis writes the edit to the sidecar so it travels with the photo, but it does not read it back. To keep your edits safe, rely on the catalog backups." Or build the import (a larger change) and change the text then.

### D23: Two licence statements for the Windows build [medium]

**Evidence:** `LICENSE`, `README.md:71` and Help > About (`ui/main_window.py:1514`) say "MIT licence". `THIRD-PARTY-LICENSES.md:8-10` says the bundled FFmpeg and pillow-heif include GPL-2.0 parts (x264, x265), so "the Windows build as a whole is distributed under the GPL-2.0-or-later". Lunelis's own source stays MIT.

**Impact:** A person reading About believes the download is MIT. If the build is GPL, the about box and README understate what the licence asks of those who redistribute it. This is a legal point, so it needs an owner's decision, not just a text edit.

**Recommendation:** Decide the intended wording, then use it everywhere. For example, About: "Lunelis source code: MIT licence. The Windows download also contains GPL-licensed parts; see Third-party licences." Make `README.md:71` say the same and link to `THIRD-PARTY-LICENSES.md`.

# Checked and sound

These claims were tested against the code and are true. Each names the function that enforces it.

| Claim | Enforced by | Result |
|:--|:--|:--|
| The scanner never deletes a files row, and an unreachable source is not scanned as empty | `scan.scan_root` (raises RootUnavailable, `scan.py:317`; missing marked, not removed). The only deletes are `remove_root` (user action, refused while a job or migration depends on the source), relink merge, and Empty | [verified] |
| Sources cannot nest or overlap | `scan.add_root` raises RootOverlap (`scan.py:119`) | [verified] |
| A move removes its source only after the copy is verified | `quarantine.move_one` compares bytes (`_same_bytes`) before `os.remove`; `ingest` deletes the staged copy only after the library copy verifies (`ingest.py:625`) | [verified] |
| Clear the card needs a full SHA-256 match and a finished import | `ingest.clearable` and `clear_card` re-check size, time and `_sha256` (`ingest.py:680-715`) | [verified] |
| A sampled hash is never proof of identity | `ingest._already_in_library` takes sampled candidates, then requires an equal full SHA-256 (`ingest.py:471-500`) | [verified] |
| Schema changes are migrations only, all-or-nothing, with a snapshot first | `schema.migrate`: one script in BEGIN/COMMIT with the version row inside (`schema.py:995-1003`); `_snapshot_before_upgrade` (`:949`) | [verified] |
| The catalog cannot live on a network share | `paths.check_new_data_dir` refuses UNC and mapped network drives (`paths.py:156-164`) | [verified] |
| Updates need a checksum and refuse unsafe zips | `updater.download` raises without a `.sha256` (`updater.py:114-115`); entries with `..` or an absolute path are refused (`:149-150`) | [verified] |
| Emptying quarantine snapshots first and checks the kept copy byte by byte | `manage.empty` (`manage.py:232`, `:246-248`); local files go to the Recycle Bin | [verified] |
| Quarantined files are not "missing" and the scanner skips the quarantine folder | `SKIP_DIR_NAMES` (`scan.py:35`) and the `quarantined` set (`scan.py:328`) | [verified] |
| Outputs never overwrite | `export.free_path` (`export.py:110-118`); `merge.save` refuses an existing path (`merge.py:118`) | [verified] |
| The grid never decodes a RAW | `grid` imports only `cache_rel_path`; `thumbcache.load_image` decodes cached JPEGs with Pillow (`thumbcache.py:68`) | [verified] |
| XMP is edited as text, not re-serialized | `sidecar.apply_fields` changes attributes in place; ElementTree only validates the result (`sidecar.py:353`) | [verified] |
| Recognition sits behind one interface | `recognize.Recognizer` (`recognize/__init__.py:8`); `clip.py` is the one implementation | [verified] |
| No telemetry; the network is used only by the updater, three model downloaders, the opt-in map tiles, the OpenStreetMap link and the family gallery on the home network | Search for `urlopen`, `QNetworkAccessManager` and `http` in `src/` finds only those | [verified] |

The claim "snapshot before every job that moves or removes files" also holds: snapshots are taken before quarantine, similar-copy quarantine, emptying, migration, release and removing a source (`quarantine.py:53`, `similar.py:380`, `manage.py:232`, `execute.py:232, 482`, `main_window.py:3382`).

# Recommended order of work

Work in batches. Each batch can ship alone.

## Batch 1: the two high findings

1. **D1** Reword the installer sentence (`lunelis.iss:276`). One line, and it removes the biggest false promise.
2. **D2** Make the scanner skip the Lunelis folder, and run the Settings overlap check in the wizard. Add a test that scans a source containing `Lunelis\Trash`.

## Batch 2: quick text fixes (one-line edits)

3. **D13** `scenes.py:173`: "Settings > AI" becomes "Settings > Library > Scene tags".
4. **D11** Settings text and wiki: replace "a darktable plugin is planned".
5. **D10** Settings text for "Only in the Lunelis catalog".
6. **D21** Wiki S-Log3 location. **D20** wiki "only rating and label". **D18** wiki network table.
7. **D17, D14, D16** Place count, live-quality label, lens guide path.

## Batch 3: UI strings that need a decision

8. **D3** Quarantine page hint, migration start dialog, Migration wiki page, plus a wiki section on the Lunelis folder.
9. **D12** Migration wizard keeper text.
10. **D6** Settings label for the backup interval (if the timer is not built).
11. **D15** Shortcut sheet keys.
12. **D23** Licence wording. Needs the owner first.

## Batch 4: small code changes that make the text true

13. **D9** Use `takeout_roots()` in both keeper ranks.
14. **D6** Add the repeating backup timer.
15. **D7** Look for backups where `backup_dir` puts them.
16. **D5** Prune by backup kind.
17. **D4** Use `move_pair` in `restore_entry`.

## Batch 5: documentation sweep

18. **D8, D22** CLAUDE.md line 443 and the edit-stack note in the wiki.
19. **D24 to D33** CLAUDE.md and CHANGELOG corrections, in one commit.

## Batch 6: comment sweep

20. **D34 to D42** Correct the nine docstring and comment groups. No user effect; do it with the nearest code change.

# To check

Open questions. Each says where we looked.

- **Windows.** The app is Windows-first and this audit ran on Linux. Everything was read from source. No dialog was seen on screen, and no test was run.
- **D2 end to end.** We ran only the scanner on a made-up folder. We did not run the migration wizard to see the files appear in the grid. Looked at: `importers/scan.py`, `migrate/execute.py:235`, `ui/migration_wizard.py`.
- **D4 on Windows.** Whether `os.rename` raises when the Trash is on another drive from the old source is standard Windows behaviour, but we did not try it. Looked at: `dupes/manage.py:181`.
- **Space key in tick lists.** The `row_toggles` docstring says Space or Enter (`ui/widgets.py:9`), but the code connects only click and activate. We did not test the key. Looked at: `ui/widgets.py:18-19`.
- **Settings with no control.** `burst_max_frames` and `timelapse_max_pause_minutes` exist in `settings.py` and are validated, but no Settings control changes them. Confirm this is intended.
- **Measured figures in CLAUDE.md.** Timing and size numbers (NAS scan times, thumbnail sizes, hash volumes) are quoted from CLAUDE.md and were not re-measured.
- **Installer behaviour.** We did not confirm that a silent install writes `setup.json` with the page defaults (`lunelis.iss:422-426`), or where `THIRD-PARTY-LICENSES.md` lands in an installed folder.
- **darktable plugin.** CLAUDE.md says it was tested with a stand-in `darktable` module only. We did not check it inside a real darktable.
- **Hugging Face and GitHub redirects.** Model URLs name github.com and huggingface.co. Both redirect to storage hosts we did not inspect, so the D18 fix should name the URL hosts only.
- **Canon CR3.** Still reported as "reader not built yet" (`importers/metadata.py:43-45`). We did not check whether a later step fills CR3 details another way.
