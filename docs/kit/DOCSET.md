# Choosing the document set

Not every product needs all twelve document types. **Ask the owner which ones they want, and
build only those.** Do not assume the full set, and do not drop a type without asking.

## Ask

Offer the twelve types from `STYLE.md` and let the owner pick. A good default question:
"Which of these do you want for this release? (manual, quick start, interface reference,
engine explanations, data flow, data model, compatibility, audit, security and privacy,
performance and test, operations, release and decisions)". Record the answer in a `docset.json`.

## Build

```text docset.json
{
  "product": "Product Name", "version": "1.2.3", "revision": "1", "date": "2026-10-09",
  "documents": [
    { "name": "User Manual", "src": "src/manual.md", "type": "manual" }
  ]
}
```

`python3 docs/kit/package.py docset.json out/` builds each document and writes one zip.

## Naming and revisions

- Every file carries the release version and the document revision:
  `Product-User-Manual-v1.2.3-rev1.docx`, `.pdf`.
- The zip is `Product-v1.2.3-documentation-rev1.zip`, with a `README.txt` index inside.
- **Revision** counts rebuilds of the documentation for that release. Start at 1. Raise it when
  any document changes without a new release. A new release restarts at 1.
- Version, revision and date are printed on every cover and in every footer.

## Rules that apply to every document

- **Screenshots are light theme.** Documents are printed; dark screenshots print as black
  blocks. The build refuses a dark screenshot. Capture in the light theme.
- **The manual lists every error** a user can meet: the message, the cause and the fix. Each
  one sits in the chapter where it happens, with an index table in Troubleshooting.
- **The quick start states what you need first:** operating system, memory, disk space,
  permissions, optional extras, and anything to download or install.
