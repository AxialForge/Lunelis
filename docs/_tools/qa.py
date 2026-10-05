"""
Quality checks for the release documentation package.

    .venv/Scripts/python docs/_tools/qa.py docs/release-package/0.12.0

1. Every control in ui_inventory.json appears in the user manual, in its screen's table.
2. Callout numbers on each screenshot match the table rows (1..N, same count, same order).
3. Every annotated and clean screenshot exists, in each theme captured for the package.
4. The PDFs open, have pages, and their table of contents lists every chapter
   with a page number.
Writes qa_report.json and prints a summary.
"""
from __future__ import annotations

import json
import re
import sys
import zipfile
from pathlib import Path

import pypdfium2 as pdfium

PKG = Path(sys.argv[1]).resolve()
TOOLS = Path(__file__).resolve().parent


def docx_text(p: Path) -> str:
    with zipfile.ZipFile(p) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    xml = re.sub(r"</w:p>", "\n", xml)
    return re.sub(r"<[^>]+>", "", xml).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">") \
        .replace("&quot;", '"').replace("&apos;", "'")


def pdf_check(p: Path, chapters: list[str]) -> dict:
    doc = pdfium.PdfDocument(str(p))
    toc_text = ""
    for i in range(min(14, len(doc))):
        t = doc[i].get_textpage().get_text_range()
        toc_text += t
        if i and "...." not in t:
            break
    toc_lines = [l for l in toc_text.splitlines() if re.search(r"\.{4,}\s*\d+\s*$", l)]
    missing = [c for c in chapters if not any(l.strip().startswith(c) for l in toc_lines)]
    return {"pages": len(doc), "toc_entries": len(toc_lines), "chapters_missing_from_toc": missing}


def main() -> None:
    inv = json.loads((PKG / "ui_inventory.json").read_text(encoding="utf-8"))
    data = json.loads((TOOLS / "build" / "manual_data.json").read_text(encoding="utf-8"))
    text = docx_text(PKG / "USER_MANUAL.docx")
    report: dict = {"controls_missing_from_manual": [], "callout_mismatches": [], "screenshots_missing": [],
                    "pdfs": {}, "files": {}}

    # Themes captured for this package: capture_graphite.json -> light, capture_midnight.json -> dark.
    themes = {t for t, f in (("light", "graphite"), ("dark", "midnight")) if (PKG / f"capture_{f}.json").exists()}
    report["themes"] = sorted(themes)
    rows_by_screen = {s["id"]: s["rows"] for ch in data["chapters"] for s in ch["screens"]}
    for w in inv["windows"]:
        ctrls = w.get("controls", [])
        rows = rows_by_screen.get(w["id"])
        if rows is None:
            report["controls_missing_from_manual"].append(f"{w['id']}: whole screen")
            continue
        nums = [r["n"] for r in rows]
        if nums != list(range(1, len(ctrls) + 1)) or [r["name"] for r in rows] != [c["name"] for c in ctrls]:
            report["callout_mismatches"].append(w["id"])
        for c in ctrls:
            if c["name"] not in text:
                report["controls_missing_from_manual"].append(f"{w['id']} #{c['callout']} {c['name']}")
        for key, rel in w["screenshots"].items():
            if key.split("_")[0] not in themes:          # a theme this package didn't capture
                continue
            if rel is None or not (PKG / rel).exists():
                report["screenshots_missing"].append(f"{w['id']} {key}")

    chapters = [ch["title"] for ch in data["chapters"]]
    front = ["About this manual", "Installing Lunelis", "The interface at a glance"]
    for name, chs in (("USER_MANUAL.pdf", front + chapters), ("RELEASE_OVERVIEW.pdf",
                      ["Simple: Lunelis in one page", "Medium: what it does and how you use it",
                       "Advanced: how it is built", "How a release is made",
                       "Version history", "Known limitations", "Roadmap"]),
                      ("INSTALL_GUIDE.pdf", ["Before you start", "Install", "First run", "Where Lunelis keeps things",
                       "Updating", "Moving to a new PC", "Uninstalling", "Troubleshooting the install"]),
                      ("RELEASE_HISTORY.pdf", ["At a glance"]),
                      ("DEVELOPER_GUIDE.pdf", ["Rebuilding on a clean machine", "Repository layout",
                       "The catalog: schema and migrations", "Configuration", "Tests and coverage",
                       "Regenerating the documentation and screenshots", "Extension points", "Releasing"])):
        if (PKG / name).exists():
            report["pdfs"][name] = pdf_check(PKG / name, chs)
        else:
            report["pdfs"][name] = "MISSING"

    for p in sorted(PKG.iterdir()):
        if p.is_file():
            report["files"][p.name] = p.stat().st_size
        elif p.is_dir():
            files = [f for f in p.rglob("*") if f.is_file()]
            report["files"][p.name + "/"] = {"files": len(files), "bytes": sum(f.stat().st_size for f in files)}
    report["totals"] = {"screens": len(inv["windows"]), "callouts": inv["totals"]["callouts"],
                        "source_lines_unverified": inv["totals"]["unverified"],
                        "manual_gaps": data["gaps"], "manual_unverified_fields": data["unverified"]}
    (PKG / "qa_report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("controls_missing_from_manual", "callout_mismatches",
                                             "themes", "screenshots_missing", "pdfs", "totals")}, indent=1, ensure_ascii=False))


main()
