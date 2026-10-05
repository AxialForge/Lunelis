# Background jobs

Work that can take hours on a big library - finding duplicates, verifying
them, hashing everything, checking integrity - runs as a **job**.

Open the **Jobs** panel with **Library > Jobs...** (`Ctrl+J`) or the *Jobs*
button in the status bar.

## What jobs can do

- **Pause, resume, cancel** from the panel.
- **Survive a restart or power cut.** A job works folder by folder and saves
  its progress as each folder finishes; everything computed inside a folder
  is saved immediately. Turn the PC off mid-job and it carries on from the
  next unfinished folder when Lunelis starts again.
- **Wait for a sleeping NAS.** If a network folder disappears, the job pauses
  itself ("Waiting for ... to come back online") and retries every minute,
  instead of failing thousands of files.
- **Run when it suits you.** When you start a job you choose:
  - **Now**
  - **Only while the PC is idle** (no keyboard/mouse for 5 minutes)
  - **Overnight** (22:00-06:00)
- **Speed limit** in MB/s, so a job doesn't saturate your network while
  you're using it.

## Kinds of job

| Job | Started from | What it does |
|---|---|---|
| Find duplicates | Library > Find duplicates... | Quick sampled hash of possible copies, folder by folder - see [Duplicates](Duplicates.md) |
| Verify duplicates | Duplicates page > Verify likely groups... | Compares every byte of likely copies |
| Hash everything | Library > Hash everything (integrity baseline)... | A full fingerprint of every file, for later integrity checks |
| Check integrity | Library > Check integrity against the baseline... | Re-fingerprints files and flags any that changed silently - see [Damaged files](Damaged-Files.md) |

## Choosing where a job runs

The dialog opens with the defaults from
[Settings > Duplicates & jobs](Settings.md#duplicates-and-background-jobs).
These cover when to run, the idle time, the hours and the speed limit.

The job dialog lists your sources with tick boxes. Tick whole sources, or
use **Choose folder...** to run on just one folder - handy for trying things
out, or for very large (20 TB+) libraries you'd rather take a piece at a time.
