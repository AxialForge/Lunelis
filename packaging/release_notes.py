"""Write one version's section of CHANGELOG.md (the release notes the updater
shows) to a file, as UTF-8.  Usage: python packaging/release_notes.py v0.4.0 notes.md"""
import re
import sys
from pathlib import Path

version = sys.argv[1].lstrip("vV")
text = (Path(__file__).resolve().parents[1] / "CHANGELOG.md").read_text(encoding="utf-8")
m = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)", text, re.S | re.M)
Path(sys.argv[2]).write_text((m.group(1).strip() if m else f"Lunelis {version}") + "\n", encoding="utf-8")
