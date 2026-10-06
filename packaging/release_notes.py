"""Write one version's section of CHANGELOG.md (the release notes the updater
shows) to a file, as UTF-8.  Usage: python packaging/release_notes.py v0.4.0 notes.md

"People|<name>" would vanish on the release page and in the updater (read as
an HTML tag), so < and > are escaped outside `code`."""
import re
import sys
from pathlib import Path

version = sys.argv[1].lstrip("vV")
text = (Path(__file__).resolve().parents[1] / "CHANGELOG.md").read_text(encoding="utf-8")
m = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)", text, re.S | re.M)
if not m or not m.group(1).strip():
    # A release without its CHANGELOG section would ship a blank "what's new":
    # fail the build instead (the section is written in the release commit).
    sys.exit(f"CHANGELOG.md has no section for {version}")
body = m.group(1).strip()
body = "".join(p if p.startswith("`") else p.replace("<", "&lt;").replace(">", "&gt;")
               for p in re.split(r"(`[^`]*`)", body))
Path(sys.argv[2]).write_text(body + "\n", encoding="utf-8")
