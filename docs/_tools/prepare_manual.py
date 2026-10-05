"""
Merge the manual's written content (docs/_tools/manual/*.json) with the
captured inventory into one file the Word builder reads, and check that
every captured control has a description.

    .venv/Scripts/python docs/_tools/prepare_manual.py docs/release-package/0.12.0

Writes docs/_tools/build/manual_data.json and page-sized slices of tall
screenshots into docs/_tools/build/parts/. Prints the coverage gaps.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image

OUT = Path(sys.argv[1]).resolve()
TOOLS = Path(__file__).resolve().parent
BUILD = TOOLS / "build"
PARTS = BUILD / "parts"
CONTENT = ["library", "editing", "create", "newer", "organize", "settings"]      # chapter order
PAGE_W_PX, PAGE_H_PX = 864, 600   # picture area of a landscape Letter page at 96 dpi
DARK = ["main_window", "photo_view", "edit_panel", "albums", "quarantine", "settings"]


def load(name: str) -> dict:
    return json.loads((TOOLS / "manual" / f"{name}.json").read_text(encoding="utf-8"))


def slices(png: Path) -> list[dict]:
    """The image, or its page-sized parts when it's very tall."""
    img = Image.open(png)
    w, h = img.size
    fit = min(1.0, PAGE_W_PX / w)                  # scale the builder will use for the width
    if h * fit <= PAGE_H_PX * 1.25:                # fits a page (at worst slightly shrunk)
        return [{"path": str(png), "width": w, "height": h}]
    step = int(PAGE_H_PX * 1.1 / fit)
    out = []
    PARTS.mkdir(parents=True, exist_ok=True)
    for i, top in enumerate(range(0, h, step), 1):
        part = img.crop((0, max(0, top - 12 if i > 1 else 0), w, min(h, top + step)))
        p = PARTS / f"{png.stem}_part{i}.png"
        part.save(p)
        out.append({"path": str(p), "width": part.width, "height": part.height})
    return out


def main() -> None:
    cap = json.loads((OUT / "capture_graphite.json").read_text(encoding="utf-8"))
    screens = {s["id"]: s for s in cap["screens"]}
    general = load("general")
    parts_doc = load("parts")
    part_text = parts_doc["parts"]
    parts = {name: load(name) for name in CONTENT}

    # Descriptions: the screen's own entry first, then the same control name anywhere.
    own: dict[tuple[str, str], dict] = {}
    by_name: dict[str, dict] = {}
    for doc in parts.values():
        for sid, ctrls in doc.get("controls", {}).items():
            for name, d in ctrls.items():
                own[(sid, name)] = d
                by_name.setdefault(name, d)

    gaps, unverified_text, chapters, placed = [], [], [], set()
    for name in CONTENT:
        doc = parts[name]
        for ch in doc["chapters"]:
            ch_out = dict(ch, screens=[])
            for sid in ch["screens"]:
                s = screens.get(sid)
                if s is None or "error" in s:
                    gaps.append(f"screen {sid}: not captured")
                    continue
                placed.add(sid)
                rows = []
                for c in s["controls"]:
                    d = own.get((sid, c["name"])) or by_name.get(c["name"])
                    if d is None:
                        gaps.append(f"{sid} #{c['n']} {c['name']}: no description")
                        d = {"does": "UNVERIFIED: not described yet.", "inputs": "-", "default": "-", "notes": "-"}
                    inputs = d.get("inputs") or "-"
                    if inputs.strip() in ("-", "") and c.get("limits"):
                        inputs = c["limits"]
                    if c.get("shortcut") and c["shortcut"] not in inputs:
                        inputs = (inputs + "; " if inputs != "-" else "") + "Shortcut " + c["shortcut"]
                    row = {"n": c["n"], "name": c["name"], "where": c.get("where", "-"), "type": c["type"],
                           "does": d.get("does", "-"),
                           "inputs": inputs, "default": d.get("default") or "-", "notes": d.get("notes") or "-"}
                    for k in ("does", "inputs", "default", "notes"):
                        if "UNVERIFIED" in str(row[k]):
                            unverified_text.append(f"{sid} #{c['n']} {c['name']} ({k})")
                    rows.append(row)
                light = OUT / "screenshots" / s["annotated"]
                screen_parts = [{"letter": q["letter"], "name": q["name"],
                          "text": part_text.get(q["name"]) or gaps.append(f"part {q['name']}: no description") or "-"}
                         for q in s.get("parts") or []]
                show = sid in parts_doc["show_parts"] and s.get("parts_image")
                ch_out["screens"].append({
                    "id": sid, "number": s["number"], "name": s["name"], "parts": screen_parts,
                    "parts_image": slices(OUT / "screenshots" / s["parts_image"])[0] if show else None,
                    "text": doc.get("screen_text", {}).get(sid, ""),
                    "images": slices(light), "rows": rows,
                })
            chapters.append(ch_out)
    for sid in screens:
        if sid not in placed:
            gaps.append(f"screen {sid}: in no chapter")

    dark = []
    for sid in DARK:
        s = screens[sid]
        p = OUT / "screenshots" / s["annotated"].replace("_annotated", "_midnight_annotated")
        if p.exists():
            dark.append({"id": sid, "name": s["name"], "images": slices(p)})

    data = {
        "version": next(l.split("=")[1].strip().strip('"') for l in
                        (TOOLS.parents[1] / "pyproject.toml").read_text(encoding="utf-8").splitlines()
                        if l.startswith("version")),
        "general": general,
        "overview_image": str(OUT / "screenshots" / screens["main_window"]["parts_image"]),
        "overview_parts": [{"letter": q["letter"], "name": q["name"], "text": part_text[q["name"]]}
                           for q in screens["main_window"]["parts"]],
        "parts_intro": parts_doc["intro"],
        "parts_glossary": sorted(
            ({"name": n, "text": part_text.get(n, "-"),
              "screens": ", ".join(dict.fromkeys(s["name"] for s in screens.values()
                                                 if any(q["name"] == n for q in s.get("parts") or [])))}
             for n in {q["name"] for s in screens.values() for q in s.get("parts") or []}),
            key=lambda r: r["name"]),
        "cover_image": str(OUT / "screenshots" / "01_main_window_clean.png"),
        "chapters": chapters,
        "dark": dark,
        "settings_reference": parts["settings"].get("settings_reference", []),
        "shortcuts": [[k["keys"], k["command"]] for k in cap["shortcuts"]],
        "gaps": gaps,
        "unverified": unverified_text,
    }
    BUILD.mkdir(parents=True, exist_ok=True)
    (BUILD / "manual_data.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    n_rows = sum(len(s["rows"]) for ch in chapters for s in ch["screens"])
    print(f"{len(chapters)} chapters, {n_rows} callout rows, {len(gaps)} gaps, {len(unverified_text)} UNVERIFIED fields")
    for g in gaps:
        print("  GAP", g)


main()
