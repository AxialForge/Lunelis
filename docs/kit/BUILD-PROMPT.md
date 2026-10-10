# Prompt: build the documentation for a release

Paste this into a new session in the product's repository. Fill in the three values in `{braces}`.

```text
Build the full documentation set for {PRODUCT} release {VERSION}, revision 1.

Use the documentation kit in docs/kit (copy it in from the Lunelis repo if this repo does not
have it). Read docs/kit/STYLE.md and DOCSET.md first and follow them exactly.

1. ASK ME which document types I want before writing anything. Offer: quick start, user manual,
   interface reference, engine explanations, data flow, data model, compatibility report,
   audit report, security and privacy report, test and performance report, operations guide,
   release and decisions. Build only the ones I choose. Record the choice in docset.json.

2. Facts. Every statement comes from the code or CHANGELOG.md, verified, with file:line for
   numbers, thresholds and defaults. What you cannot confirm goes in a To check chapter. Where the
   code disagrees with CLAUDE.md, the changelog, docstrings or UI text, write what the code does
   and list the conflict under Discrepancies. Never invent behaviour. No personal data: no real
   names, emails, addresses, share names or user folder paths; use made-up examples.

3. Screenshots: capture them from the real app with the repo's capture tooling, in the LIGHT theme
   only (documents are printed). Check mean brightness >= 200 with PIL; the kit refuses images
   under 150. Crop dark regions to light parts, never recolour. Look at every image you use and
   write captions that match what it shows. Use demo data only. List every shot in SHOTS.md.

4. User manual: list EVERY error, refusal, warning and failure message the user can meet, in a
   table | Message or symptom | Cause | Fix |, wording exact from the code, under a heading
   "<Chapter>: if something goes wrong" inside the chapter where it happens. Then build a
   Troubleshooting index of all of them (message | chapter). Say which messages were seen on screen
   and which were only matched from the code.

5. Quick start: open with "What you need": operating system, memory, disk, permissions, optional
   extras, install and download, network use. Say "not measured" where unknown.

6. Work in parallel: one agent per document or document pair, each writing Markdown into
   docs/src and building it with docs/kit/build.sh, rasterizing the PDF and LOOKING at every page
   (clipped images, orphan table rows, sparse pages, unreadable diagrams). Run the product's test
   suite for the performance report and report failures honestly, labelling platform artefacts.
   Take real measurements only; label them indicative.

7. Package: python3 docs/kit/package.py docset.json out/. Files are named
   {Product}-{Document}-v{VERSION}-rev{N}.docx and .pdf; the zip is
   {Product}-v{VERSION}-documentation-rev{N}.zip with a README.txt index. Raise the revision when
   documents change without a new release.

8. Finish: send me the zip, the page count per document, and a plain list of what is unverified
   and of any real bugs the documents uncovered. Do not change product code. Commit sources and
   docs as asked; commits carry no AI attribution.
```
