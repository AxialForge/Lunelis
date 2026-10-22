# Documentation kit

Builds the Lunelis document set (manual, audit report, data flows, engine explanations and
more) from Markdown into styled `.docx` and `.pdf` files.

```text Layout
docs/kit/        the renderer, templates and style guide
docs/src/        document sources (Markdown)
docs/manuals/    built .docx and .pdf
```

## Set up once

```text Tools needed
node (with the docx package), python 3 with matplotlib, LibreOffice + python uno, Poppler
npm install --prefix docs/kit        # docx and the Mermaid command-line tool
```

Mermaid draws diagrams in a headless Chrome. The kit finds Playwright's Chromium under
`/opt/pw-browsers` or Chrome on Windows; set `CHROME_PATH` to override.

## Write a document

1. Copy `templates/<type>.md` to `docs/src/<name>.md`.
2. Fill it in. Read `STYLE.md` first.
3. `docs/kit/build.sh docs/src/<name>.md`.

## Files

| File | Job |
|:--|:--|
| `render.js` | Markdown to .docx: cover, contents, chapters, tables, callouts, diagrams, charts |
| `finish.py` | LibreOffice fills the contents list and page numbers and writes the PDF |
| `check.py` | Page size, empty pages, contents, stale chapter references |
| `chart.py` | Draws bar, stacked, horizontal-bar and line charts in the kit's look |
| `types.json` | Name and colour of each document type |
| `templates/` | One starter file per document type |
