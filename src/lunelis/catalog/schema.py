"""
Lunelis catalog schema + migration runner.

The catalog is a single SQLite file. Schema changes are numbered, ordered
migrations applied in sequence and tracked in `schema_version` - never
hand-edit the live schema, add a new migration instead. This is the
migration tool the Phase 1 build order calls for "before the first table."
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable, Union

# Each migration is (version, description, sql-or-function). A function takes
# the connection and runs inside the same transaction as its schema_version
# row - for data migrations SQL can't express (compression, backfills).
# Add new migrations to the END of this list only.
Migration = tuple[int, str, Union[str, Callable[[sqlite3.Connection], None]]]

MIGRATIONS: list[Migration] = [
    (
        1,
        "initial schema: roots, files, exif, ratings, tags, albums, people, faces, duplicate_groups",
        """
        CREATE TABLE roots (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL DEFAULT 'local',   -- 'local' | 'nas'
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE files (
            id INTEGER PRIMARY KEY,
            root_id INTEGER NOT NULL REFERENCES roots(id),
            rel_path TEXT NOT NULL,               -- path relative to the root, so a NAS remount doesn't break it
            filename TEXT NOT NULL,
            ext TEXT NOT NULL,                    -- lowercase, no dot: 'arw', 'cr3', 'jpg', 'heic'...
            size_bytes INTEGER NOT NULL,
            mtime TEXT NOT NULL,                  -- filesystem mtime, ISO 8601
            content_hash TEXT,                    -- exact-duplicate signal (sha256), filled by the scanner
            perceptual_hash TEXT,                 -- near-duplicate signal, filled in Phase 2's robust dedupe
            thumbnail_path TEXT,                  -- path into cache/thumbnails, filled by the proxy pipeline
            is_raw INTEGER NOT NULL DEFAULT 0,
            imported_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (root_id, rel_path)
        );
        CREATE INDEX idx_files_content_hash ON files(content_hash);
        CREATE INDEX idx_files_root ON files(root_id);

        CREATE TABLE exif (
            file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
            captured_at TEXT,                     -- from EXIF DateTimeOriginal, ISO 8601
            camera_make TEXT,
            camera_model TEXT,
            lens TEXT,
            focal_length_mm REAL,
            aperture REAL,
            shutter_speed TEXT,
            iso INTEGER,
            gps_lat REAL,
            gps_lon REAL,
            width_px INTEGER,
            height_px INTEGER,
            raw_json TEXT                         -- full extracted EXIF, for fields not modeled above
        );

        CREATE TABLE ratings (
            file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
            stars INTEGER NOT NULL DEFAULT 0 CHECK (stars BETWEEN 0 AND 5),
            flag TEXT,                            -- 'pick' | 'reject' | NULL
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE tags (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE
        );
        CREATE TABLE file_tags (
            file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            confidence REAL,                      -- NULL = manual tag, 0..1 = model-suggested (Phase 2)
            PRIMARY KEY (file_id, tag_id)
        );

        CREATE TABLE albums (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            is_smart INTEGER NOT NULL DEFAULT 0,
            smart_rule_json TEXT,                 -- filter definition, only when is_smart = 1
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE album_files (
            album_id INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
            file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            position INTEGER,
            PRIMARY KEY (album_id, file_id)
        );

        CREATE TABLE people (
            id INTEGER PRIMARY KEY,
            name TEXT,                            -- NULL until named in Recognition Review
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE faces (
            id INTEGER PRIMARY KEY,
            file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            person_id INTEGER REFERENCES people(id),
            bbox_json TEXT NOT NULL,              -- [x, y, w, h] in the source image
            embedding BLOB,                       -- Phase 2, once recognition runs
            confidence REAL,
            confirmed INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE duplicate_groups (
            id INTEGER PRIMARY KEY,
            method TEXT NOT NULL,                 -- 'exact_hash' (Phase 1) | 'perceptual_hash' | 'exif' (Phase 2)
            resolved INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE duplicate_group_files (
            group_id INTEGER NOT NULL REFERENCES duplicate_groups(id) ON DELETE CASCADE,
            file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            is_keeper INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (group_id, file_id)
        );
        """,
    ),
    (
        2,
        "scan bookkeeping: files.missing_since, roots.last_scanned_at",
        """
        -- A file not found on rescan is flagged, never deleted: a dropped NAS
        -- share or a moved folder must not wipe ratings/albums/faces.
        ALTER TABLE files ADD COLUMN missing_since TEXT;
        ALTER TABLE roots ADD COLUMN last_scanned_at TEXT;
        """,
    ),
    (
        3,
        "exif: orientation, exposure comp, flash, utc offset, extraction bookkeeping; files.format",
        """
        ALTER TABLE exif ADD COLUMN orientation INTEGER;       -- EXIF 1-8; 5-8 = width/height swap on display
        ALTER TABLE exif ADD COLUMN exposure_comp REAL;        -- EV
        ALTER TABLE exif ADD COLUMN flash_fired INTEGER;       -- 0/1, NULL = not recorded
        ALTER TABLE exif ADD COLUMN captured_offset TEXT;      -- '-05:00' from OffsetTimeOriginal; captured_at stays camera wall-clock
        -- The files.mtime this row was read from. A row whose value no longer
        -- matches files.mtime is stale and gets re-read; a row with read_error
        -- set is a completed attempt, so unreadable files aren't retried forever.
        ALTER TABLE exif ADD COLUMN extracted_mtime TEXT;
        ALTER TABLE exif ADD COLUMN read_error TEXT;
        CREATE INDEX idx_exif_captured_at ON exif(captured_at);
        -- What the bytes actually are ('jpeg', 'tiff', 'raf', 'cr3', 'heic', ...),
        -- sniffed by the metadata pass; is_raw is corrected from it. Decoders
        -- dispatch on this, never on ext: Takeout ships JPEGs named .ARW.
        ALTER TABLE files ADD COLUMN format TEXT;
        """,
    ),
    (
        4,
        "files.thumb_error: thumbnail attempts that failed, so they aren't retried every pass",
        """
        -- Set when no thumbnail could be made ("preview unavailable"). Cleared
        -- by the scanner when the file changes, like thumbnail_path.
        ALTER TABLE files ADD COLUMN thumb_error TEXT;
        """,
    ),
    (
        5,
        "covering index for the library grid's date lookup",
        """
        -- The grid joins every file to exif for captured_at. Through the
        -- primary key that touches each exif row - and rows are ~2 KB with
        -- raw_json - so the join cost ~230ms at 159k files. This index holds
        -- just (file_id, captured_at) and makes it index-only.
        CREATE INDEX idx_exif_file_captured ON exif(file_id, captured_at);
        """,
    ),
    (
        6,
        "XMP sidecar sync: files.sidecar*, ratings.color_label / xmp_pending / xmp_error",
        """
        -- The sidecar the scanner found next to the file (a name in the same
        -- folder), its mtime then, and the mtime we last imported from or
        -- wrote to - a sidecar is (re)imported when those two differ.
        ALTER TABLE files ADD COLUMN sidecar TEXT;
        ALTER TABLE files ADD COLUMN sidecar_mtime TEXT;
        ALTER TABLE files ADD COLUMN sidecar_synced_mtime TEXT;

        ALTER TABLE ratings ADD COLUMN color_label TEXT;       -- Red/Yellow/Green/Blue/Purple
        -- 1 = changed in Lunelis, not yet written to the sidecar. Pending
        -- local changes win over the sidecar on import.
        ALTER TABLE ratings ADD COLUMN xmp_pending INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE ratings ADD COLUMN xmp_error TEXT;
        CREATE INDEX idx_ratings_pending ON ratings(xmp_pending) WHERE xmp_pending = 1;
        """,
    ),
    (
        7,
        "settings: key/value store for library settings",
        """
        -- Values are JSON. Defaults live in lunelis/settings.py, so a missing
        -- key means "default" and new settings need no migration.
        CREATE TABLE settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """,
    ),
]


def _compress_raw_exif(conn: sqlite3.Connection) -> None:
    from lunelis.catalog.exifblob import pack

    conn.execute("ALTER TABLE exif ADD COLUMN raw_exif BLOB")
    rows = conn.execute("SELECT file_id, raw_json FROM exif WHERE raw_json IS NOT NULL").fetchall()
    conn.executemany("UPDATE exif SET raw_exif = ? WHERE file_id = ?",
                     [(pack(text), fid) for fid, text in rows])
    conn.execute("ALTER TABLE exif DROP COLUMN raw_json")


MIGRATIONS.append((
    8,
    "exif.raw_json (text) -> exif.raw_exif (zlib-compressed); was 2/3 of the catalog",
    _compress_raw_exif,
))

MIGRATIONS.append((
    9,
    "jobs engine + duplicate detection: jobs, job_folders, sample_hash, quarantine",
    """
    -- sha256 of the size plus three 64 KB slices (start/middle/end): the cheap
    -- "likely identical" key. content_hash (migration 1) is the full sha256.
    -- Both are cleared by the scanner when the file changes.
    ALTER TABLE files ADD COLUMN sample_hash TEXT;
    -- Set when a duplicate was moved to the quarantine folder (never deleted).
    ALTER TABLE files ADD COLUMN quarantined_at TEXT;
    ALTER TABLE files ADD COLUMN quarantine_path TEXT;
    CREATE INDEX idx_files_size ON files(size_bytes);
    CREATE INDEX idx_files_sample ON files(sample_hash);

    CREATE TABLE jobs (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL,                 -- 'duplicates' | 'full_hash' | ...
        title TEXT NOT NULL,
        options TEXT NOT NULL DEFAULT '{}', -- JSON: scope, schedule, throttle
        state TEXT NOT NULL DEFAULT 'queued',
            -- queued | running | waiting (schedule / offline) | paused | done | cancelled | failed
        status TEXT,                        -- human-readable line for the Jobs panel
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT,
        finished_at TEXT,
        files_done INTEGER NOT NULL DEFAULT 0,
        files_total INTEGER NOT NULL DEFAULT 0,
        bytes_read INTEGER NOT NULL DEFAULT 0
    );
    -- A job is a list of folders; each is marked done as it finishes, so a
    -- job resumes after a power-off at the first unfinished folder.
    CREATE TABLE job_folders (
        job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        root_id INTEGER NOT NULL,
        folder TEXT NOT NULL,               -- rel dir inside the root, '' = top level
        files INTEGER NOT NULL DEFAULT 0,
        state TEXT NOT NULL DEFAULT 'pending',   -- pending | done
        done_at TEXT,
        PRIMARY KEY (job_id, root_id, folder)
    );

    ALTER TABLE duplicate_groups ADD COLUMN hash_key TEXT;
    ALTER TABLE duplicate_groups ADD COLUMN verified INTEGER NOT NULL DEFAULT 0;
    CREATE UNIQUE INDEX idx_dupgroups_key ON duplicate_groups(method, hash_key);
    """,
))

MIGRATIONS.append((
    10,
    "moved-file re-linking log + damaged-file check",
    """
    -- Every time a missing file was recognised at a new location and its
    -- catalog entry (ratings, EXIF, thumbnail...) moved with it.
    CREATE TABLE file_moves (
        id INTEGER PRIMARY KEY,
        file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
        from_root INTEGER NOT NULL,
        from_path TEXT NOT NULL,
        to_root INTEGER NOT NULL,
        to_path TEXT NOT NULL,
        method TEXT NOT NULL,              -- 'size+mtime' | 'exif' | 'hash'
        moved_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX idx_file_moves_file ON file_moves(file_id);

    -- Files that can't be what they claim. Rebuilt by damage.check; a row the
    -- user dismissed keeps dismissed = 1 while the problem persists.
    CREATE TABLE damaged (
        file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
        problem TEXT NOT NULL,     -- zero_bytes | zero_filled | unrecognised | truncated | corrupt | changed_on_disk
        detail TEXT,
        detected_at TEXT NOT NULL DEFAULT (datetime('now')),
        dismissed INTEGER NOT NULL DEFAULT 0
    );
    """,
))

MIGRATIONS.append((
    11,
    "case-insensitive filename index (surviving-copy lookups)",
    """
    -- damage.check.survivors() looks copies up by name; without this each
    -- lookup scanned every file (127 lookups: 7.2 s on 159k files).
    CREATE INDEX idx_files_filename_nocase ON files(filename COLLATE NOCASE);
    """,
))

MIGRATIONS.append((
    12,
    "video metadata + thumbnails, Google Takeout JSON dates/GPS, date source",
    """
    -- Where captured_at came from: 'exif' (the file's own), 'video' (the
    -- container's creation time) or 'takeout' (Google's JSON sidecar, only
    -- ever used when the file itself has no date).
    ALTER TABLE exif ADD COLUMN date_source TEXT;
    ALTER TABLE exif ADD COLUMN duration_s REAL;
    UPDATE exif SET date_source = 'exif' WHERE captured_at IS NOT NULL;
    -- The grid reads duration too: widen its covering index (migration 5) so
    -- the join still never touches the ~2 KB exif rows.
    DROP INDEX idx_exif_file_captured;
    CREATE INDEX idx_exif_file_captured ON exif(file_id, captured_at, duration_s);

    -- What a Google Takeout JSON sidecar said about a file, kept as-is so a
    -- later feature (captions, Google's people tags) can use it.
    CREATE TABLE takeout_meta (
        file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
        json_path TEXT NOT NULL,
        title TEXT,
        taken_utc INTEGER,
        lat REAL,
        lon REAL,
        description TEXT,
        people TEXT                  -- JSON list of names
    );

    -- Videos now have a metadata reader and thumbnails: forget the
    -- "not built yet" results so the next pass reads them.
    DELETE FROM exif WHERE read_error LIKE 'NotImplementedError: video%';
    UPDATE files SET thumb_error = NULL WHERE thumb_error LIKE 'NotImplementedError: video%';
    """,
))

MIGRATIONS.append((
    13,
    "card import: imports + import_items (staging, verification, placement)",
    """
    CREATE TABLE imports (
        id INTEGER PRIMARY KEY,
        source TEXT NOT NULL,            -- card or folder imported from, e.g. 'F:\\'
        volume_serial TEXT,              -- identifies the card if it's re-inserted
        volume_label TEXT,
        name TEXT,                       -- the import name typed by the user (optional)
        template TEXT NOT NULL,
        destination TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'staging',
            -- staging | staged (card can be removed) | placing | done (safe to format)
            -- | waiting (card or share missing) | cancelled
        status TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        finished_at TEXT
    );
    CREATE TABLE import_items (
        id INTEGER PRIMARY KEY,
        import_id INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
        source_rel TEXT NOT NULL,        -- path on the card, '/'-separated
        size INTEGER NOT NULL,
        mtime REAL NOT NULL,
        sha256 TEXT,                     -- of the card copy, taken while staging
        staged_path TEXT,
        dest_path TEXT,
        state TEXT NOT NULL DEFAULT 'pending',
            -- pending | staged | placed | already_in_library | failed
        error TEXT
    );
    CREATE INDEX idx_import_items ON import_items(import_id, state);
    """,
))

# Migrations after which the file should be compacted (space freed by a
# migration isn't returned to the OS until VACUUM).
MIGRATIONS.append((
    14,
    "skipped folders: excluded_folders + files.excluded",
    """
    -- Folders inside a source that the user told Lunelis to leave alone.
    -- Scans don't walk them; files already cataloged there are set aside
    -- (excluded = 1), keeping their ratings in case the folder comes back.
    CREATE TABLE excluded_folders (
        root_id INTEGER NOT NULL REFERENCES roots(id),
        rel_path TEXT NOT NULL,          -- '/'-separated, relative to the root
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (root_id, rel_path)
    );
    ALTER TABLE files ADD COLUMN excluded INTEGER NOT NULL DEFAULT 0;
    """,
))

MIGRATIONS.append((
    15,
    "events: events + event_files + event_dismissed, imports.event_start/event_id",
    """
    -- An event is a catalog object first (a name + the capture-time range of
    -- its photos) and a folder second. A photo is in at most one event.
    CREATE TABLE events (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        start_at TEXT,                   -- earliest / latest capture time of its photos
        end_at TEXT,                     --   (camera wall clock, like exif.captured_at)
        source TEXT NOT NULL DEFAULT 'manual',   -- manual | folder | suggested | import
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE event_files (
        file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
        event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE
    );
    CREATE INDEX idx_event_files_event ON event_files(event_id);
    -- Suggestions the user said no to, so they don't come back.
    CREATE TABLE event_dismissed (
        key TEXT PRIMARY KEY,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    -- A named import is an event: its photos are filed by the event's start
    -- date (fixed once, so a resumed import files the same way) and joined
    -- to the event after the scan that catalogs them.
    ALTER TABLE imports ADD COLUMN event_start TEXT;
    ALTER TABLE imports ADD COLUMN event_id INTEGER REFERENCES events(id);
    """,
))

MIGRATIONS.append((
    16,
    "migration / consolidation: migrations + migration_items",
    """
    CREATE TABLE migrations (
        id INTEGER PRIMARY KEY,
        target TEXT NOT NULL,              -- the folder everything goes into
        target_root_id INTEGER REFERENCES roots(id),   -- set when it starts
        template TEXT NOT NULL,
        options TEXT NOT NULL,             -- JSON: sources, keep_sources, one_copy, ...
        state TEXT NOT NULL DEFAULT 'planned',
            -- planned (dry run only) | running | done | released | cancelled
        job_id INTEGER REFERENCES jobs(id),
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        finished_at TEXT
    );
    CREATE TABLE migration_items (
        id INTEGER PRIMARY KEY,
        migration_id INTEGER NOT NULL REFERENCES migrations(id) ON DELETE CASCADE,
        file_id INTEGER NOT NULL REFERENCES files(id),
        src_root INTEGER NOT NULL,
        src_rel TEXT NOT NULL,             -- where it was, '/'-separated
        dest_rel TEXT,                     -- where it goes, relative to the target
        size INTEGER NOT NULL,
        action TEXT NOT NULL,              -- move | skip_duplicate | skip_damaged
        keeper_id INTEGER,                 -- skip_duplicate: the copy that moves instead
        note TEXT,                         -- sibling folder used, damaged only copy, ...
        state TEXT NOT NULL DEFAULT 'planned',
            -- planned | copied (library copy verified + catalog repointed)
            -- | done (original in quarantine) | kept (original left until review)
            -- | released (kept original moved to quarantine later) | skipped | failed
        sha256 TEXT,
        quarantine_path TEXT,
        error TEXT
    );
    CREATE INDEX idx_migration_items ON migration_items(migration_id, src_root, state);
    CREATE INDEX idx_migration_items_src ON migration_items(src_root, src_rel);
    """,
))

MIGRATIONS.append((
    17,
    "backups: backup_sets + backup_files",
    """
    -- A backup set: some sources, mirrored into a folder on a USB drive, stick
    -- or network share. Removable drives are found by volume serial, so a
    -- changed drive letter doesn't matter.
    CREATE TABLE backup_sets (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        dest_path TEXT NOT NULL,
        volume_serial TEXT,
        volume_label TEXT,
        sources TEXT NOT NULL,             -- JSON list of root ids
        options TEXT NOT NULL DEFAULT '{}',   -- JSON: auto_on_connect, schedule, mb_per_s
        status TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        last_run_at TEXT,
        last_verified_at TEXT
    );
    -- What each backup holds: one row per library file, keyed by the catalog
    -- entry (so a file moved or migrated in the library is renamed inside
    -- the backup, not copied again).
    CREATE TABLE backup_files (
        set_id INTEGER NOT NULL REFERENCES backup_sets(id) ON DELETE CASCADE,
        file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
        rel TEXT NOT NULL,                 -- path inside the backup folder, '/'-separated
        size INTEGER NOT NULL,
        mtime TEXT NOT NULL,               -- the library file's mtime when copied
        sha256 TEXT NOT NULL,
        backed_up_at TEXT NOT NULL DEFAULT (datetime('now')),
        verified_at TEXT,
        problem TEXT,                      -- set by a verify pass that found the copy bad
        PRIMARY KEY (set_id, file_id)
    );
    CREATE INDEX idx_backup_files_rel ON backup_files(set_id, rel);
    """,
))

MIGRATIONS.append((
    18,
    "albums: cover photo, updated_at, lookups by photo",
    """
    ALTER TABLE albums ADD COLUMN cover_file_id INTEGER REFERENCES files(id) ON DELETE SET NULL;
    ALTER TABLE albums ADD COLUMN updated_at TEXT;
    CREATE INDEX IF NOT EXISTS idx_album_files_file ON album_files(file_id);
    """,
))

MIGRATIONS.append((
    19,
    "burst stacks",
    """
    -- A stack shows as one tile (its cover) in the grid. kind 'burst' is
    -- rebuilt from capture times after each metadata pass (stacks.py).
    CREATE TABLE stacks (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL DEFAULT 'burst',
        cover_file_id INTEGER REFERENCES files(id) ON DELETE SET NULL,
        cover_chosen INTEGER NOT NULL DEFAULT 0,     -- the user picked the cover: a rebuild keeps it
        size INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    -- A photo is in at most one stack.
    CREATE TABLE stack_files (
        stack_id INTEGER NOT NULL REFERENCES stacks(id) ON DELETE CASCADE,
        file_id INTEGER NOT NULL UNIQUE REFERENCES files(id) ON DELETE CASCADE,
        position INTEGER NOT NULL,
        PRIMARY KEY (stack_id, file_id)
    );
    -- Photos the user unstacked: never stacked again.
    CREATE TABLE stack_dismissed (
        file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE
    );
    """,
))

MIGRATIONS.append((
    20,
    "non-destructive edits and your own filters",
    """
    -- One row per edited photo; no row = the original. The stack is
    -- edit/stack.py's text form; rev bumps on every save.
    CREATE TABLE edits (
        file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
        stack TEXT NOT NULL,
        rev INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE edit_filters (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        params TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """,
))

MIGRATIONS.append((
    21,
    "imports: the new-import filter is applied once",
    """
    ALTER TABLE imports ADD COLUMN edits_applied INTEGER NOT NULL DEFAULT 0;
    """,
))

MIGRATIONS.append((
    22,
    "merges: HDR and panorama results and what they were made from",
    """
    CREATE TABLE merges (
        file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,                -- 'hdr' | 'panorama'
        source_ids TEXT NOT NULL,          -- comma-separated file ids, in order
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """,
))

MIGRATIONS.append((
    23,
    "tags: unique ignoring case, photos by tag",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_tags_name_nocase ON tags(name COLLATE NOCASE);
    CREATE INDEX IF NOT EXISTS idx_file_tags_tag ON file_tags(tag_id);
    """,
))

def _search_sql() -> str:
    from lunelis.search import SCHEMA
    return SCHEMA


MIGRATIONS.append((
    24,
    "search: full-text index of names, folders, cameras, lenses, tags, events, albums",
    _search_sql(),
))

MIGRATIONS.append((
    25,
    "purged: every quarantined file emptied for good, for the record",
    """
    CREATE TABLE purged (
        id INTEGER PRIMARY KEY,
        path TEXT NOT NULL,                -- where it was in quarantine
        original TEXT NOT NULL,            -- where it lived before
        size INTEGER NOT NULL,
        content_hash TEXT,
        reason TEXT NOT NULL,              -- duplicate | similar | migration | original
        kept TEXT,                         -- the copy that stayed
        how TEXT NOT NULL,                 -- recycled | deleted
        purged_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """,
))

MIGRATIONS.append((
    26,
    "archive: files.archived_at - archived photos leave the library but stay in your albums",
    """
    ALTER TABLE files ADD COLUMN archived_at TEXT;
    CREATE INDEX idx_files_archived ON files(archived_at) WHERE archived_at IS NOT NULL;
    """,
))

MIGRATIONS.append((
    27,
    "imports: camera profile, sidecars filed with their clip, notes",
    """
    ALTER TABLE imports ADD COLUMN profile TEXT;
    ALTER TABLE import_items ADD COLUMN parent_id INTEGER REFERENCES import_items(id);
    ALTER TABLE import_items ADD COLUMN note TEXT;
    """,
))

MIGRATIONS.append((
    28,
    "phones: an import item's kind (sidecar / companion); motion photos",
    """
    ALTER TABLE import_items ADD COLUMN kind TEXT;
    UPDATE import_items SET kind = 'sidecar' WHERE parent_id IS NOT NULL;
    ALTER TABLE files ADD COLUMN motion_video INTEGER;
    """,
))

MIGRATIONS.append((
    29,
    "RAW+JPEG pairs: each file of a pair points at the other",
    """
    ALTER TABLE files ADD COLUMN pair_of INTEGER;
    CREATE INDEX idx_files_pair ON files(pair_of) WHERE pair_of IS NOT NULL;
    """,
))

MIGRATIONS.append((
    30,
    "scene tags: embeddings per photo per model; rejected suggestions remembered",
    """
    CREATE TABLE IF NOT EXISTS embeddings (
        file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
        model TEXT NOT NULL,
        dim INTEGER NOT NULL,
        vector BLOB NOT NULL,                 -- float32 x dim, unit length
        made_at TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (file_id, model)
    );
    CREATE TABLE IF NOT EXISTS tag_rejections (
        file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
        tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
        PRIMARY KEY (file_id, tag_id)
    );
    CREATE INDEX IF NOT EXISTS idx_file_tags_suggested ON file_tags(tag_id) WHERE confidence IS NOT NULL;
    """,
))

MIGRATIONS.append((
    31,
    "Lunelis noticed: suggested HDR brackets, panoramas, focus stacks, timelapses, star trails",
    """
    CREATE TABLE IF NOT EXISTS suggestions (
        id INTEGER PRIMARY KEY,
        key TEXT NOT NULL UNIQUE,             -- kind + its frames: a dismissed group is never offered again
        kind TEXT NOT NULL,                   -- hdr | panorama | focus | timelapse | startrails
        file_ids TEXT NOT NULL,               -- JSON list, in shooting order
        detail TEXT,                          -- JSON (EV span, interval...)
        status TEXT NOT NULL DEFAULT 'open',  -- open | dismissed | built
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        answered_at TEXT
    );
    """,
))

MIGRATIONS.append((
    32,
    "rolling integrity checks (when each file was last re-read); backups looked up per file",
    """
    ALTER TABLE files ADD COLUMN checked_at TEXT;
    CREATE INDEX IF NOT EXISTS idx_files_checked ON files(checked_at);
    CREATE INDEX IF NOT EXISTS idx_backup_files_file ON backup_files(file_id);
    """,
))

MIGRATIONS.append((
    33,
    "Autopilot Import: each run's stages, what they made (for Undo) and what waits for review",
    """
    CREATE TABLE IF NOT EXISTS autopilot_runs (
        id INTEGER PRIMARY KEY,
        import_id INTEGER NOT NULL,
        state TEXT NOT NULL DEFAULT 'running',   -- running | review | done
        stages TEXT NOT NULL,                    -- JSON: stage -> {status, summary, data}
        file_ids TEXT,                           -- JSON: the import's photos, once catalogued
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        finished_at TEXT
    );
    """,
))

MIGRATIONS.append((
    34,
    "edit stack version 2 (retouch spots): stored stacks rewritten as v=2 - the same edit, read the same way",
    """
    UPDATE edits SET stack = 'v=2' || substr(stack, 4) WHERE stack LIKE 'v=1%';
    """,
))

MIGRATIONS.append((
    35,
    "virtual copies: more edits of one photo, each its own stack",
    """
    CREATE TABLE IF NOT EXISTS copies (
        id INTEGER PRIMARY KEY,
        file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        stack TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_copies_file ON copies(file_id);
    """,
))

MIGRATIONS.append((
    36,
    "sensor dust maps per camera, and the heals made from them (for Undo)",
    """
    CREATE TABLE IF NOT EXISTS dust_maps (
        camera TEXT PRIMARY KEY,
        map TEXT NOT NULL,                    -- JSON (dust.DustMap)
        made_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS dust_heals (
        id INTEGER PRIMARY KEY,
        camera TEXT NOT NULL,
        spots TEXT NOT NULL,                  -- JSON: file id -> [[x, y, r], ...] added
        made_at TEXT NOT NULL
    );
    """,
))

MIGRATIONS.append((
    37,
    "family gallery: albums shared on the home network, one key each",
    """
    CREATE TABLE IF NOT EXISTS shares (
        id INTEGER PRIMARY KEY,
        album_id INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
        token TEXT NOT NULL UNIQUE,           -- the long random key in the link
        pin_hash TEXT,                        -- salt:PBKDF2-SHA256, or NULL for no PIN
        originals INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        revoked_at TEXT
    );
    """,
))

def _faces_v38(conn: sqlite3.Connection) -> None:
    """Faces: suggestions, unnamed groups, ignored / stranger faces, hand-drawn
    boxes, scans and "not this person". Adds only what's missing, so running
    it again (a test, a half-restored catalog) is harmless."""
    def add(table: str, column: str, decl: str) -> None:
        if column not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    add("faces", "suggested_person_id", "INTEGER REFERENCES people(id) ON DELETE SET NULL")
    add("faces", "suggestion", "REAL")                     # how alike the suggested person is (cosine)
    add("faces", "cluster", "INTEGER")                     # an unnamed group of alike faces
    add("faces", "ignored", "INTEGER NOT NULL DEFAULT 0")  # 1 = not a face, 2 = a stranger
    add("faces", "source", "TEXT NOT NULL DEFAULT 'auto'") # auto | user (drawn by hand)
    add("faces", "model", "TEXT")
    add("faces", "created_at", "TEXT")
    add("people", "cover_face_id", "INTEGER")
    add("people", "hidden", "INTEGER NOT NULL DEFAULT 0")
    for stmt in (
        "CREATE INDEX IF NOT EXISTS faces_by_file ON faces(file_id)",
        "CREATE INDEX IF NOT EXISTS faces_by_person ON faces(person_id)",
        "CREATE INDEX IF NOT EXISTS faces_by_suggestion ON faces(suggested_person_id)",
        "CREATE INDEX IF NOT EXISTS faces_by_cluster ON faces(cluster)",
        "CREATE UNIQUE INDEX IF NOT EXISTS people_by_name ON people(name COLLATE NOCASE) WHERE name IS NOT NULL",
        """CREATE TABLE IF NOT EXISTS face_scans (
            file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
            model TEXT NOT NULL,
            faces INTEGER NOT NULL,
            scanned_at TEXT NOT NULL DEFAULT (datetime('now')))""",
        """CREATE TABLE IF NOT EXISTS face_rejections (     -- "not this person": never suggested again
            face_id INTEGER NOT NULL REFERENCES faces(id) ON DELETE CASCADE,
            person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
            PRIMARY KEY (face_id, person_id))""",
    ):
        conn.execute(stmt)


MIGRATIONS.append((
    38,
    "faces: suggestions, unnamed groups, ignored faces and strangers, hand-drawn boxes, scans and \"not this person\"",
    _faces_v38,
))

MIGRATIONS.append((
    39,
    "places: pins dropped on the map, and the place tag each photo was given (and from where)",
    """
    CREATE TABLE IF NOT EXISTS locations (      -- a pin wins over the photo's own GPS; files never change
        file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        set_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS photo_places (   -- the Places|... tag Lunelis gave, from these coordinates
        file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
        place TEXT NOT NULL,
        lat REAL,
        lon REAL
    );
    """,
))

MIGRATIONS.append((
    40,
    "clearing finished jobs and emptying quarantine never trip over a migration's records",
    """
    -- migrations.job_id and migration_items.file_id were made without ON DELETE
    -- (migration 16); SQLite can't alter a foreign key, so triggers do it.
    CREATE TRIGGER IF NOT EXISTS jobs_release_migrations BEFORE DELETE ON jobs
    BEGIN
        UPDATE migrations SET job_id = NULL WHERE job_id = OLD.id;
    END;
    CREATE TRIGGER IF NOT EXISTS files_drop_migration_items BEFORE DELETE ON files
    BEGIN
        DELETE FROM migration_items WHERE file_id = OLD.id;
    END;
    """,
))

MIGRATIONS.append((
    41,
    "a duplicate group stops counting as verified when one of its files changes on disk",
    """
    CREATE TRIGGER IF NOT EXISTS files_changed_unverify AFTER UPDATE OF size_bytes, mtime ON files
    WHEN OLD.size_bytes IS NOT NEW.size_bytes OR OLD.mtime IS NOT NEW.mtime
    BEGIN
        UPDATE duplicate_groups SET verified = 0
        WHERE id IN (SELECT group_id FROM duplicate_group_files WHERE file_id = NEW.id);
    END;
    """,
))

MIGRATIONS.append((
    42,
    "turns: a photo or video rotated for viewing, without editing it (turns.py)",
    """
    CREATE TABLE IF NOT EXISTS turns (
        file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
        quarter INTEGER NOT NULL CHECK (quarter BETWEEN 1 AND 3)    -- quarter turns clockwise
    );
    """,
))

MIGRATIONS.append((
    43,
    "timelapses found in the library, for review (timelapses.py)",
    """
    CREATE TABLE IF NOT EXISTS sequences (
        id INTEGER PRIMARY KEY,
        key TEXT NOT NULL UNIQUE,
        kind TEXT NOT NULL DEFAULT 'timelapse',      -- timelapse | burst (a short run the user called a burst)
        origin TEXT NOT NULL DEFAULT 'auto',         -- auto (found) | manual (made from a selection)
        status TEXT NOT NULL DEFAULT 'found',        -- found | confirmed | dismissed
        file_ids TEXT NOT NULL,                      -- JSON list in shooting order, one per shot (RAW for a pair)
        frames INTEGER NOT NULL,
        detail TEXT,                                 -- JSON: interval (s), pauses
        stack_id INTEGER,                            -- shown as one tile (stacks.id) when stacked
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        answered_at TEXT
    );
    -- The old "Lunelis noticed" timelapse offers are the engine's job now.
    DELETE FROM suggestions WHERE kind = 'timelapse' AND status = 'open';
    """,
))

MIGRATIONS.append((
    44,
    "Google Takeout review: which Takeout items to bring into the new Library (takeout_review.py)",
    """
    CREATE TABLE IF NOT EXISTS takeout_choices (
        file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
        include INTEGER NOT NULL
    );
    """,
))

MIGRATIONS.append((
    45,
    "Canon CR3 can be read now: CR3 files marked 'no reader' are read again (importers/metadata.py)",
    """
    DELETE FROM exif WHERE read_error LIKE 'NotImplementedError%';
    """,
))


def _pets_v46(conn: sqlite3.Connection) -> None:
    """people.kind: person | pet. Adds it only when missing (safe to run again)."""
    if "kind" not in {r[1] for r in conn.execute("PRAGMA table_info(people)")}:
        conn.execute("ALTER TABLE people ADD COLUMN kind TEXT NOT NULL DEFAULT 'person'")


MIGRATIONS.append((
    46,
    "pets: a named face can be an animal (people.kind) - recognize/faces.py",
    _pets_v46,
))


VACUUM_AFTER = {8}


def _ensure_version_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "  version INTEGER PRIMARY KEY,"
        "  description TEXT NOT NULL,"
        "  applied_at TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )


def current_version(conn: sqlite3.Connection) -> int:
    _ensure_version_table(conn)
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return row[0] or 0


def _snapshot_before_upgrade(conn: sqlite3.Connection, db_path: Path, version: int) -> None:
    """Back the catalog up before a new version changes its schema: an older
    Lunelis can't open an upgraded catalog, so this backup is the way back."""
    import json
    from lunelis.catalog import backup
    folder = db_path.parent / "backups"
    if version >= 7:                     # the settings table (migration 7) may name another folder
        row = conn.execute("SELECT value FROM settings WHERE key = 'catalog_backup_dir'").fetchone()
        if row and json.loads(row[0]):
            folder = Path(json.loads(row[0]))
    backup.snapshot(conn, folder, reason=f"before-upgrade-v{version}", keep=1_000_000)   # prune nothing


class NewerCatalog(RuntimeError):
    """The catalog was upgraded by a newer Lunelis than this one."""


def migrate(db_path: str | Path) -> int:
    """Apply every migration newer than the catalog's current version.

    Safe to call every app startup - a fresh catalog runs every migration,
    an existing one only runs what's new. Returns the resulting version.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        _ensure_version_table(conn)
        applied = current_version(conn)
        if applied > MIGRATIONS[-1][0]:
            # Its tables may mean things this version doesn't know: refuse
            # rather than read or write it wrongly.
            raise NewerCatalog(f"The catalog is from a newer Lunelis (schema {applied}; this version knows "
                               f"up to {MIGRATIONS[-1][0]}).")
        if 0 < applied < MIGRATIONS[-1][0]:
            _snapshot_before_upgrade(conn, Path(db_path), applied)
        vacuum = False
        for version, description, sql in MIGRATIONS:
            if version <= applied:
                continue
            if callable(sql):
                _apply_callable(conn, version, description, sql)
                applied = version
                vacuum |= version in VACUUM_AFTER
                continue
            # executescript() commits any open transaction and then runs in
            # autocommit, so the migration and its schema_version row must be
            # one script inside an explicit BEGIN/COMMIT - otherwise a failure
            # halfway leaves tables created but unrecorded, and every later
            # startup dies on "table already exists".
            desc = description.replace("'", "''")
            try:
                conn.executescript(
                    f"BEGIN;\n{sql}\n"
                    f"INSERT INTO schema_version (version, description) "
                    f"VALUES ({int(version)}, '{desc}');\nCOMMIT;"
                )
            except sqlite3.Error:
                if conn.in_transaction:
                    conn.rollback()
                raise
            applied = version
            vacuum |= version in VACUUM_AFTER
        if vacuum:
            conn.execute("VACUUM")
        return applied
    finally:
        conn.close()


def _apply_callable(conn: sqlite3.Connection, version: int, description: str,
                    fn: Callable[[sqlite3.Connection], None]) -> None:
    """Run a Python migration and record it, all-or-nothing."""
    old = conn.isolation_level
    conn.isolation_level = None                  # we manage the transaction ourselves
    try:
        conn.execute("BEGIN")
        try:
            fn(conn)
            conn.execute("INSERT INTO schema_version (version, description) VALUES (?, ?)",
                         (version, description))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.isolation_level = old


def open_catalog(db_path: str | Path) -> sqlite3.Connection:
    """Open the catalog, migrating it first if needed."""
    migrate(db_path)
    # 20 s (not 5) before "database is locked": background passes commit in
    # short steps, so a writer normally waits well under a second.
    conn = sqlite3.connect(str(db_path), timeout=20)
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL lets the UI thread read while a background scan writes. It's a
    # persistent property of the file, so setting it every open is a no-op.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.row_factory = sqlite3.Row
    return conn
