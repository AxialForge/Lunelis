"""Copy a release (zip + .sha256 + notes) from the private repo's GitHub
Copy a release to the archived AxialForge/Lunelis-releases - only needed
so installs older than 0.16.1 (which only look there) can update. Uses this PC's `gh`
login. CI does this itself once a RELEASES_TOKEN secret exists; this is the
fallback. Usage:  python packaging/publish_public.py v0.4.0"""
import subprocess
import sys
import tempfile
from pathlib import Path

PRIVATE, PUBLIC = "AxialForge/Lunelis", "AxialForge/Lunelis-releases"


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def main(tag: str) -> int:
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp:
        gh("release", "download", tag, "--repo", PRIVATE, "--pattern", "Lunelis-*-windows.zip*", "--dir", tmp)
        files = sorted(str(p) for p in Path(tmp).iterdir())
        if not any(f.endswith(".sha256") for f in files):
            print("The private release has no .sha256 - the updater won't install it. Rebuild with the new workflow.")
            return 1
        notes = Path(tmp) / "notes.md"
        subprocess.run([sys.executable, str(root / "packaging" / "release_notes.py"), tag, str(notes)], check=True)
        gh("release", "create", tag, *files, "--repo", PUBLIC, "--title", f"Lunelis {tag}",
           "--notes-file", str(notes))
    print(f"Published {tag} to https://github.com/{PUBLIC}/releases/tag/{tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
