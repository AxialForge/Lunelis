# Repo conventions

How AxialForge projects are laid out and shipped. Distilled from
[jdot-utilities](https://github.com/AxialForge/jdot-utilities), which is the
reference implementation — when this document is ambiguous, go look at that repo.

## Attribution

Commits are authored by `AxialForge <37514365+AxialForge@users.noreply.github.com>`
and carry **no** AI attribution — no `Co-Authored-By: Claude` trailer, no
"Generated with Claude Code" PR footer. Both are switched off globally in
`~/.claude/settings.json`:

```json
{ "attribution": { "commit": "", "pr": "" } }
```

The `ID+LOGIN@users.noreply.github.com` address is GitHub's own: it links commits
to the account **and** keeps a real email address out of public history. Prefer
it over a personal address in anything public.

Two failure modes to watch for, both of which put the wrong name in the repo's
Contributors list:

- **A stale `--local` override.** `git config --local --get-regexp "^user\."`
  should print nothing. A leftover local identity silently wins over a perfectly
  good global.
- **A commit *authored* by Claude.** Distinct from the co-author trailer and not
  fixed by the settings change — it means the author field itself was wrong when
  the commit was made. `git log --author=noreply@anthropic.com` finds them.

Both are cheap to prevent and expensive to fix: correcting either one after the
fact rewrites history, changing every SHA from that commit forward. That's fine
on an unpublished branch and painful on a repo with published tags and releases.
Check before the first commit, not after the first release.

## Required files

| File | Why |
| --- | --- |
| `README.md` | What it is, what it needs, how to run it, how to build it. Opens with one sentence a stranger understands. |
| `CLAUDE.md` | The project guide (see below). The single highest-leverage file in the repo. |
| `CHANGELOG.md` | [Keep a Changelog](https://keepachangelog.com) format. Updated in the same commit that bumps the version. |
| `LICENSE` | MIT unless there's a reason. |
| `.gitignore` | Build output, virtualenvs, `node_modules`, secrets. Never commit a binary CI can rebuild. |
| `.editorconfig` | So editors stop fighting over line endings on Windows. |

## `CLAUDE.md` — the project guide

Not a README for robots. It carries what the code can't tell you:

1. **What this is** — one paragraph, including what it deliberately *isn't*.
2. **Non-negotiables** — the invariants that must not regress ("fully offline",
   "no build step in the renderer", "ships as a single `.exe`"). State them as
   constraints, not aspirations.
3. **Commands** — install, run, test, build. Copy-pasteable, with the version
   requirements that actually matter.
4. **Architecture** — the module boundaries and a directory map with a one-line
   purpose per file. This is what stops a change landing in the wrong layer.
5. **The extension point** — if there's a "how to add a thing" path, document it
   as the *only* supported path.
6. **Gotchas** — the payload. Every bug that cost real debugging time, written
   as symptom → cause → why the obvious fix is wrong. Include the ones that look
   like someone else's bug ("every Office conversion failed and looked like a
   LibreOffice problem — it was a malformed `file://` URL"). Add to this section
   the moment something bites; it's the part that stops the same day being lost
   twice.
7. **Roadmap** — what's deliberately unbuilt, and why.

## Commit messages

Imperative, lower-case after any prefix, no trailing period, describing the
behaviour change:

```
Recover from GPU freeze so the window can't open dead
Fix Office conversions by building the profile path with pathToFileURL
Release 0.7.1: GPU-freeze recovery
```

No Conventional Commits (`feat:` / `fix:` / `chore:`). Body paragraphs are for
*why*, when the why isn't obvious — the diff already shows the what.

## Branching

`main` is the default branch and stays releasable. Small work commits straight to
`main`; anything risky or long-running gets a branch and a PR. Tags are cut from
`main`.

## Versioning and releases

Semantic versioning. The version lives in exactly one file per stack —
`package.json`, `pyproject.toml`, or `platformio.ini` — and everything else reads
it from there.

To release:

```bash
# bump the version + update CHANGELOG.md in one commit, then:
git tag v0.3.0
git push origin v0.3.0
```

The tag push triggers the workflow, which builds the artifact and attaches it to
a GitHub Release with generated notes. **Never hand-upload a binary CI can
build** — a hand-built artifact has no reproducible provenance, and the next
person (you, in four months) can't tell which commit it came from.

## CI layout

`.github/workflows/` holds only the workflows that should run. The per-stack
starters live in `.github/workflows-available/`, which GitHub ignores; move one
in and delete the rest. Every release workflow follows the same shape:

1. trigger on `push: tags: v*` plus `workflow_dispatch` for manual runs
2. run the test suite as a gate before building
3. build the artifact
4. upload it as a build artifact (so `workflow_dispatch` runs are useful too)
5. attach to the Release only when the ref is actually a tag

## Auto-update (desktop apps)

Electron apps self-update from GitHub Releases. The default is **silent**: check
on launch, download in the background, install on the next quit — no prompt, no
installer wizard. The drop-in is `templates/node-electron/updater.js`;
`INTEGRATION.md` beside it has the four wiring points, and `/new-repo` applies
them automatically for a Node project.

Two things are load-bearing and easy to miss:

- **The NSIS installer must be one-click** (`oneClick: true`, `perMachine:
  false`, `allowToChangeInstallationDirectory: false`). A one-click installer has
  no wizard, so the silent update has none either. An assisted installer brings
  the wizard back on every update.
- **The release must contain `latest.yml` and the `.blockmap`**, not just the
  `.exe`. The updater reads `latest.yml` to find a new version; without it the
  app checks, finds nothing, and silently never updates. The template workflow
  uploads all three — don't narrow it back to `dist/*.exe`.

Choose **manual** update instead (a "Check for updates" button, nothing
automatic) when the app's premise is being fully offline / no-telemetry — a
silent background version-check is indistinguishable from telemetry to that
app's users. jdot-utilities is the reference for the manual variant.

Python and ESP32 apps have no drop-in equivalent — a Python app can self-update
by polling the Releases API and swapping its `.exe`, and ESP32 uses OTA from a
URL, but both are per-project rather than a shared module.

## Repo metadata

Description and topics are part of the deliverable — they're how the repo is
found:

```bash
gh repo edit --description "..." --add-topic esp32 --add-topic platformio
```

A landing page goes in `docs/` and is published via Settings → Pages →
`main` / `docs`.
