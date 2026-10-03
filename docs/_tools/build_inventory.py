"""
Turn capture_graphite.json (+ capture_midnight.json) into ui_inventory.json:
every window, page, tab, dialog and menu of Lunelis, with each control's
callout number, type, source line and limits.

    .venv/Scripts/python docs/_tools/build_inventory.py docs/release-package/0.12.0
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

OUT = Path(sys.argv[1])
REPO = Path(__file__).resolve().parents[2]


def version() -> str:
    for line in (REPO / "pyproject.toml").read_text(encoding="utf-8").splitlines():
        if line.startswith("version"):
            return line.split("=")[1].strip().strip('"')
    return "UNVERIFIED"


def main() -> None:
    light = json.loads((OUT / "capture_graphite.json").read_text(encoding="utf-8"))
    dark_path = OUT / "capture_midnight.json"
    dark = {s["id"]: s for s in json.loads(dark_path.read_text(encoding="utf-8"))["screens"]} \
        if dark_path.exists() else {}
    windows = []
    for s in light["screens"]:
        if "error" in s:
            windows.append({"id": s["id"], "name": s["name"], "capture_error": s["error"]})
            continue
        d = dark.get(s["id"], {})
        windows.append({
            "id": s["id"],
            "number": s["number"],
            "name": s["name"],
            "parent": s["parent"],
            "class": s["class"],
            "defined_at": s["defined_at"],
            "size_px": s["size"],
            "screenshots": {
                "light_clean": f"screenshots/{s['clean']}",
                "light_annotated": f"screenshots/{s['annotated']}",
                "dark_clean": f"screenshots/{d['clean']}" if d else None,
                "dark_annotated": f"screenshots/{d['annotated']}" if d else None,
            },
            "controls": [{
                "callout": c["n"],
                "name": c["name"],
                "type": c["type"],
                "defined_at": c["defined_at"],
                **({"tooltip": c["tooltip"]} if c.get("tooltip") else {}),
                **({"limits": c["limits"]} if c.get("limits") else {}),
                **({"value_on_demo_library": c["shown"]} if c.get("shown") not in (None, "") else {}),
                **({"enabled": False} if c.get("enabled") is False else {}),
                **({"shortcut": c["shortcut"]} if c.get("shortcut") else {}),
                **({"note": c["note"]} if c.get("note") else {}),
                "bounds_px": dict(zip(("x", "y", "width", "height"), c["rect"])),
            } for c in s["controls"]],
        })
    inv = {
        "application": "Lunelis",
        "version": version(),
        "generated": datetime.date.today().isoformat(),
        "how_made": "docs/_tools/capture.py drives the real application offscreen on a generated demo "
                    "library; positions come from QWidget.mapTo(), source lines from the code.",
        "window_size_px": light["window"],
        "unverified_means": "the capture could not tie the control to one line of source code",
        "windows": windows,
        "keyboard_shortcuts": light["shortcuts"],
        "totals": {
            "windows": len(windows),
            "callouts": sum(len(w.get("controls", [])) for w in windows),
            "unique_controls": len({(c["name"], c["type"]) for w in windows for c in w.get("controls", [])}),
            "unverified": sum(c["defined_at"] == "UNVERIFIED" for w in windows for c in w.get("controls", [])),
        },
    }
    (OUT / "ui_inventory.json").write_text(json.dumps(inv, indent=1, ensure_ascii=False), encoding="utf-8")
    print(inv["totals"])


main()
