"""
Write RELEASE_HISTORY.md for the release package from CHANGELOG.md: a summary
table, then every released version's notes, newest first.

    .venv/Scripts/python docs/_tools/build_release_history.py docs/release-package/0.33.0

Then md2docx.js and to_pdf.ps1 turn it into RELEASE_HISTORY.docx / .pdf.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = Path(sys.argv[1]).resolve()
HEAD = re.compile(r"^## \[(\d+\.\d+\.\d+)\] - (\d{4}-\d{2}-\d{2})")


def versions() -> list[tuple[str, str, list[str]]]:
    out, cur = [], None
    for line in (REPO / "CHANGELOG.md").read_text(encoding="utf-8").splitlines():
        m = HEAD.match(line)
        if m:
            cur = (m[1], m[2], [])
            out.append(cur)
        elif line.startswith("## "):
            cur = None                       # [Unreleased]
        elif cur is not None:
            cur[2].append(line)
    return out


# One line per version for the table; versions not listed use their first sentence.
HEADLINES = {
    "0.33.1": "Fixes: bursts not offered as focus stacks; video Info while editing; the 0.33 release documents",
    "0.33.0": "Hardening after the October audit: security, feature gaps, ease of use",
    "0.32.0": "Create round three: focus stack, star trails, median stack, panorama, HDR",
    "0.31.0": "Family gallery: share an album on the home network",
    "0.30.0": "Sensor dust map per camera, with heals and Undo",
    "0.29.0": "Editing suite: retouch, virtual copies, colour profiles, export presets",
    "0.28.0": "Autopilot Import and Review your shoot",
    "0.27.0": "My look: edits suggested in your own style",
    "0.26.0": "Offline sources stay browsable; protection marker; Map; On this day",
    "0.25.0": "Lunelis noticed: HDR, panorama, stack and timelapse suggestions",
    "0.24.0": "S-Log3 clips shown with a built-in look; your own LUTs",
    "0.23.0": "Video and GIF playback; trim to a new file",
    "0.22.0": "Contact sheets, timelapses, slideshow videos, before-and-after, prints",
    "0.21.0": "Ask your library; shooting stats",
    "0.20.0": "Scene suggestions from a model on this PC",
    "0.19.0": "Culling, smart albums, RAW+JPEG pairs",
    "0.18.0": "Phone and USB-stick import; Live Photos and motion photos",
    "0.17.0": "Create page: animations, collages, batch copies",
    "0.16.2": "The rest of the interface audit: nothing slow on the interface thread",
    "0.16.1": "One public repository for source and releases",
    "0.16.0": "Edit page; camera profiles; Sony sidecars; clear the card",
    "0.15.0": "Edit page (tagged, never built; shipped in 0.16.0)",
    "0.14.0": "Library status page; Archive",
    "0.13.0": "Icon sidebar; visible scan progress; small and scaled screens",
    "0.12.2": "Tilt the wheel for next / previous photo; every screen part named",
    "0.12.1": "No built-in import folders; a backup before every schema upgrade",
    "0.12.0": "Zoom to 400 %, all metadata, slider number boxes, new noise reduction",
    "0.11.1": "Fixes: Edit panel Reset crash; full-screen window",
    "0.11.0": "Quarantine page: restore, empty safely",
    "0.10.0": "Search box with a full-text index",
    "0.9.0": "Tags: nested keywords, Tags page, XMP both ways",
    "0.8.0": "Editing B: tone curve, masks with AI, lens corrections, HDR, panorama",
    "0.7.0": "Editing A: light, colour, crop, filters, export",
    "0.6.0": "Near-duplicates with keeper rules; burst stacks",
    "0.5.0": "Albums page; import from any drive",
    "0.4.0": "Photo view, filmstrip, hover info, timeline",
    "0.3.1": "Built-in updater with SHA-256 checks",
    "0.3.0": "Themes, sidebar sections, Settings tabs, the log",
    "0.2.0": "First Windows build",
}


def summary(version: str, body: list[str]) -> str:
    """The table line: a curated headline, else the first sentence of the notes."""
    if version in HEADLINES:
        return HEADLINES[version]
    for line in body:
        t = line.strip()
        if not t or t.startswith("#"):
            continue
        text = [t.lstrip("-* ").strip()]
        for nxt in body[body.index(line) + 1:]:
            if not nxt.strip() or re.match(r"^\s*([-*#]|\d+\.)\s", nxt):
                break
            text.append(nxt.strip())
        s = " ".join(text).replace("**", "")
        s = re.split(r"(?<=[.:])\s", s, maxsplit=1)[0].rstrip(":")
        return s.replace("|", "/")[:160]
    return ""


def main() -> None:
    vs = versions()
    lines = [f"# Lunelis {vs[0][0]} - Release History", "",
             "Every released version of Lunelis, newest first, with its full notes from `CHANGELOG.md`.", "",
             f"{len(vs)} releases from {vs[-1][1]} to {vs[0][1]}. Each is a GitHub Release on AxialForge/Lunelis "
             "with a Windows zip, a SHA-256 file and these notes.", "",
             "## At a glance", "", "| Version | Date | Headline |", "|---|---|---|"]
    lines += [f"| {v} | {d} | {summary(v, b)} |" for v, d, b in vs]
    lines.append("")
    for v, d, body in vs:
        lines += [f"## Version {v} ({d})", ""]
        for line in body:
            lines.append(re.sub(r"^### ", "### ", line))
        while lines and not lines[-1].strip():
            lines.pop()
        lines.append("")
    (OUT / "RELEASE_HISTORY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(vs)} versions -> {OUT / 'RELEASE_HISTORY.md'}")


if __name__ == "__main__":
    main()
