# Documentation style guide

How Lunelis documents are written. The kit (`render.js`) turns one Markdown file into a
.docx and a .pdf with the same look every time. You write content; the kit does layout.

## The twelve document types

| Type (`type:`) | Answers | Must contain |
|:--|:--|:--|
| `manual` | How do I do X? | Task finder, tasks by goal, settings, troubleshooting, glossary |
| `quickstart` | What do I need on day one? | Five steps, ten keys, three surprises. Two to four pages |
| `ui` | What does every control do? | A chapter per page: controls, right-click, keys. Shortcut table |
| `engine` | How does this part work? | Problem, stages diagram, steps with real thresholds, settings, writes, safety, limits |
| `dataflow` | Where does data go? | Context diagram, trust boundaries, one diagram per flow, data inventory, network use |
| `schema` | What is stored where? | ER diagram, every table, migrations, invariants |
| `compat` | What works with what? | Support matrix with chips, how it was tested |
| `audit` | What is wrong, how bad, what next? | Verdict, severity chart, findings with evidence, what was checked and sound |
| `security` | What leaves the PC? | Assets, boundaries diagram, controls, network use, residual risk |
| `performance` | What was measured? | Method and machine, charts, targets, how to reproduce |
| `operations` | How do I run and recover it? | Data folder map, runbooks, decision tree, health checks |
| `release` | What changed and why? | Change table, upgrade notes, decision log |

Start from `docs/kit/templates/<type>.md`. Delete the sections that do not apply; do not
leave placeholder text behind.

## Rules that apply to every document

- Ask the owner which document types they want (`DOCSET.md`). Do not build all twelve by default.
- Screenshots are light theme only. The build refuses dark ones.
- The manual lists every error with cause and fix, in the chapter where it happens, plus an index.
- The quick start opens with what you need: system requirements, install, permissions.
- Files and covers carry the release version and the document revision; the build zips the set.

## Rules of writing

- Plain English for a photographer. Short sentences. No marketing words.
- **Every fact comes from the code or `CHANGELOG.md`.** Name the file (and line where it
  helps) for numbers, thresholds and defaults. Do not invent behaviour.
- When the code and the changelog or `CLAUDE.md` disagree, write what the code does and
  list the conflict under **Discrepancies**.
- What you could not confirm is not written as fact. It goes under **To check**, with where
  you looked.
- No personal data: no names, emails, addresses, share names or user folder paths. Use made-up
  examples such as `D:\Photos\2024\6-19-2024 Air Show`.
- Say "cumulative" in full where it applies. Say "source" for what the code calls a root,
  after the first mention.
- Never hard-code a chapter number ("see chapter 4"). Name the chapter instead. `check.py` fails on this.
- Every `##` sits under a `#`, and `##` titles are unique in the document (the contents list shows them).
- Long text in one table column squeezes the others. Put `<br>` into long cells, and keep `file:line` cells in a column of their own.
- Portrait US Letter only. For wide tables, shorten the cells; three or four columns at most.

## Visuals: use one when it saves a paragraph

| Need | Use |
|:--|:--|
| A process, pipeline or decision | ```` ```mermaid Caption ```` flowchart |
| Messages between parts over time | `sequenceDiagram` |
| Tables and keys | `erDiagram` |
| Numbers compared | ```` ```chart Caption ```` with JSON (bar, hbar, stacked, line) |
| Headline numbers | `::: stats` tiles |
| A screenshot | `![Caption](path.png)` (PNG, about 1200 px wide) |

Diagram rules: one idea per diagram, eight to twelve boxes at most, labels in plain words,
arrows left to right or top to bottom, a caption that says what the reader should take away.
Diagrams are scaled to fit the page width, so every box you add makes the text smaller:

- Draw rows of at most four or five boxes. Stack rows instead of making one long row.
- Keep a label to two short lines. Move detail into a table beside the diagram.
- Decision diamonds shrink text most. Prefer a table of questions for more than three.
- `direction LR` inside a subgraph is ignored when inner nodes link outside it. Link subgraph
  ids to each other instead (`A --> B`), and use `A ~~~ B` to stack two groups.
- A tall diagram is fine (up to most of a page); a wide one is not.

Diagram source stays in the Markdown, so it changes when the text does.

## Blocks the kit understands

| Write | You get |
|:--|:--|
| `# Title` | A chapter. New page, "CHAPTER n" label, coloured rule |
| `# Title {nobreak}` | A chapter that continues on the same page. Use for any chapter under about half a page |
| `## Section`, `### Part` | Headings (the first two levels appear in the contents) |
| `::: strip` with `Where:`, `For:`, `Answers:` lines | Three-cell strip under a chapter title |
| `::: stats` with `value \| label` lines | Headline number tiles |
| `::: note`, `tip`, `warn`, `important`, `check` (+ optional title) | Coloured callout. Holds lists |
| `::: cards` with `#### Title` sections | Two or three option cards side by side |
| Pipe table | Banded table with a coloured header |
| `[ok]` `[partial]` `[no]` `[na]` in a cell | Support chip |
| `[critical]` `[high]` `[medium]` `[low]` `[info]` in a cell | Severity chip |
| `[fixed]` `[open]` `[later]` `[verified]` `[check]` `[pass]` `[fail]` | Status chip |
| `<br>` in a cell | A line break inside the cell |
| Fenced block with a caption after the language | Code block with a label |
| `> quote` | Indented quotation (licence text) |
| `<!-- comment -->` | Ignored |

Front matter (between `---` lines): `type`, `title`, `subtitle`, `audience`, `sources`,
`status`, `version` (default: from `pyproject.toml`), `date` (default: today).

## Colour

One colour per document type, set in `types.json`. Chips use fixed meanings across all types:
green means supported or fixed, amber partial or later, red none or open.

## Build and check

```text Build one document
docs/kit/build.sh docs/src/my-document.md        # writes docs/manuals/my-document.docx and .pdf
```

`build.sh` renders the .docx, lets LibreOffice fill the contents list and page numbers, writes
the PDF, then runs `check.py` (page size, near-empty pages, contents, stale references).
Then look at the pages: `pdftoppm -jpeg -r 50 file.pdf page`, and check for clipped images,
orphan table rows and overlapping headings.

## Before you publish

1. Every number has a source in the code or changelog.
2. **To check** and **Discrepancies** are filled in, or say "none".
3. `check.py` passes and you have looked at every page.
4. The document names the version it describes.
