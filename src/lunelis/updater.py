"""
The built-in updater (Settings > Updates).

Releases are read from the public source repository, AxialForge/Lunelis
(until 0.16.0 they came from AxialForge/Lunelis-releases). Each release carries
`Lunelis-vX.Y.Z-windows.zip` and `Lunelis-vX.Y.Z-windows.zip.sha256`.

  check()     the newest release (GitHub API, no login - 60 checks/hour per IP)
  download()  the zip into <data>/updates/, verified against its .sha256
  stage()     unzipped next to it, checked to contain Lunelis.exe
  apply()     a small PowerShell script waits for this Lunelis to exit, renames
              the install folder to `<folder>.old-<time>`, moves the new one in
              its place and starts it. If the rename fails (a file in use, or a
              folder that needs admin rights) it starts the OLD version again,
              so an update can't leave you without a working Lunelis.
  cleanup()   at the next start: remove the `.old-*` folders.

Only a packaged build updates itself; from source, `git pull` is the update.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

RELEASES_REPO = "AxialForge/Lunelis"
LATEST_API = f"https://api.github.com/repos/{RELEASES_REPO}/releases/latest"
PAGE = f"https://github.com/{RELEASES_REPO}/releases"
USER_AGENT = "Lunelis-updater"


class UpdateError(Exception):
    pass


@dataclass
class Release:
    version: str
    tag: str
    notes: str
    page_url: str
    zip_url: str
    zip_name: str
    size: int
    sha_url: str | None


def parse_version(v: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums[:3]) + (0,) * (3 - min(3, len(nums)))


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


def _get(url: str, timeout: float = 15):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


def release_from_json(data: dict) -> Release:
    assets = {a["name"]: a for a in data.get("assets", [])}
    zips = [a for n, a in assets.items() if n.lower().endswith("-windows.zip")]
    if not zips:
        raise UpdateError("the newest release has no Windows download")
    z = zips[0]
    sha = assets.get(z["name"] + ".sha256")
    tag = data.get("tag_name", "")
    return Release(version=tag.lstrip("vV"), tag=tag, notes=data.get("body") or "",
                   page_url=data.get("html_url") or PAGE, zip_url=z["browser_download_url"],
                   zip_name=z["name"], size=int(z.get("size") or 0),
                   sha_url=sha["browser_download_url"] if sha else None)


def check(timeout: float = 15) -> Release:
    """The newest published release. Raises UpdateError when offline etc."""
    try:
        with _get(LATEST_API, timeout) as r:
            return release_from_json(json.loads(r.read().decode("utf-8")))
    except UpdateError:
        raise
    except Exception as e:                        # no network, rate limit, no releases yet...
        raise UpdateError(f"couldn't check for updates: {e}") from e


def updates_dir() -> Path:
    from lunelis import paths
    return Path(paths.DATA_DIR) / "updates"


def download(rel: Release, on_progress: Callable[[int, int], None] | None = None,
             should_cancel: Callable[[], bool] | None = None) -> Path:
    """Fetch the zip and check it against the published .sha256."""
    if not rel.sha_url:
        raise UpdateError("the release has no checksum file - not installing an unverifiable download")
    folder = updates_dir()
    folder.mkdir(parents=True, exist_ok=True)
    with _get(rel.sha_url) as r:
        expected = r.read().decode("utf-8").split()[0].strip().lower()
    target = folder / rel.zip_name
    part = target.with_suffix(".zip.part")
    h = hashlib.sha256()
    done = 0
    with _get(rel.zip_url, timeout=60) as r, open(part, "wb") as out:
        total = int(r.headers.get("Content-Length") or rel.size or 0)
        while chunk := r.read(1 << 20):
            if should_cancel and should_cancel():
                out.close()
                part.unlink(missing_ok=True)
                raise UpdateError("cancelled")
            out.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if on_progress:
                on_progress(done, total)
    if h.hexdigest() != expected:
        part.unlink(missing_ok=True)
        raise UpdateError("the download doesn't match its checksum - not installing it")
    os.replace(part, target)
    return target


def stage(zip_path: Path) -> Path:
    """Unzip next to the download; return the folder that holds Lunelis.exe."""
    dest = zip_path.with_suffix("")
    if dest.exists():
        shutil.rmtree(dest)
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():                 # refuse paths that climb out of the folder
            if name.startswith(("/", "\\")) or ".." in Path(name).parts:
                raise UpdateError(f"unsafe path in the download: {name}")
        z.extractall(dest)
    exes = list(dest.rglob("Lunelis.exe"))
    if not exes:
        raise UpdateError("the download doesn't contain Lunelis.exe")
    return exes[0].parent


def install_dir() -> Path | None:
    """The folder the running Lunelis.exe is in (None from source)."""
    from lunelis import paths
    return Path(sys.executable).parent if paths.FROZEN else None


APPLY_PS1 = r"""
param([int]$ProcessId, [string]$Install, [string]$New)
$ErrorActionPreference = 'Stop'
try { Wait-Process -Id $ProcessId -Timeout 90 -ErrorAction SilentlyContinue } catch {}
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$oldName = (Split-Path $Install -Leaf) + '.old-' + $stamp
$renamed = $false
for ($i = 0; $i -lt 40 -and -not $renamed; $i++) {
    try { Rename-Item -LiteralPath $Install -NewName $oldName; $renamed = $true }
    catch { Start-Sleep -Milliseconds 500 }
}
if (-not $renamed) {
    # Couldn't swap (a file in use, or a protected folder): keep the old version running.
    Start-Process -FilePath (Join-Path $Install 'Lunelis.exe') -ArgumentList '--update-failed'
    exit 1
}
try {
    Move-Item -LiteralPath $New -Destination $Install
    Start-Process -FilePath (Join-Path $Install 'Lunelis.exe') -ArgumentList '--updated'
} catch {
    # Put the old version back.
    if (Test-Path -LiteralPath $Install) { Remove-Item -LiteralPath $Install -Recurse -Force }
    Rename-Item -LiteralPath (Join-Path (Split-Path $Install -Parent) $oldName) -NewName (Split-Path $Install -Leaf)
    Start-Process -FilePath (Join-Path $Install 'Lunelis.exe') -ArgumentList '--update-failed'
    exit 1
}
"""


def check_install_folder(target: Path, staged: Path) -> None:
    """The swap replaces the whole install folder (and later deletes the old
    one), so it must hold Lunelis and nothing else: refuse when the data folder
    sits inside it, or when it holds things the new version doesn't have
    (other programs, a portable data folder). Raises UpdateError with what to do."""
    from lunelis import paths
    if paths._inside(paths.DATA_DIR, target):
        raise UpdateError(f"Lunelis's data folder is inside its program folder ({target}). Move the data folder "
                          "(Settings > Advanced > Data folder) before updating, so the update can't touch it.")
    ours = {p.name.lower() for p in Path(staged).iterdir()}
    extra = sorted(p.name for p in Path(target).iterdir()
                   if p.name.lower() not in ours and not p.name.lower().endswith((".log", ".tmp")))
    if extra:
        shown = ", ".join(extra[:5]) + (f" and {len(extra) - 5} more" if len(extra) > 5 else "")
        raise UpdateError(f"The program folder {target} also holds {shown}. An update replaces the whole folder, "
                          "so put Lunelis in a folder of its own first (unzip it into an empty folder).")


def apply(staged: Path) -> None:
    """Start the swap script; the caller then quits Lunelis."""
    target = install_dir()
    if target is None:
        raise UpdateError("running from source - update with git pull instead")
    check_install_folder(target, staged)
    # Move-Item can't move a folder to another drive: put the new version next
    # to the install folder first (same drive), then the swap is two renames.
    if os.path.splitdrive(str(staged))[0].lower() != os.path.splitdrive(str(target))[0].lower():
        near = target.parent / f"{target.name}.new"
        if near.exists():
            shutil.rmtree(near)
        shutil.copytree(staged, near)
        staged = near
    script = updates_dir() / "apply-update.ps1"
    script.write_text(APPLY_PS1, encoding="utf-8")
    flags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0   # DETACHED_PROCESS | NEW_PROCESS_GROUP
    subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                      "-File", str(script), "-ProcessId", str(os.getpid()), "-Install", str(target),
                      "-New", str(staged)], creationflags=flags, close_fds=True)


def cleanup() -> int:
    """After an update: remove the previous version's folder and the download.
    Returns folders removed."""
    target = install_dir()
    n = 0
    if target is not None:
        for old in target.parent.glob(target.name + ".old-*"):
            shutil.rmtree(old, ignore_errors=True)
            n += 1
    shutil.rmtree(updates_dir(), ignore_errors=True)
    return n
