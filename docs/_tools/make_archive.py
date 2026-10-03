"""
Build the reconstruction archive Lunelis_v<version>_<YYYYMMDD>.zip: everything
needed to rebuild, run and understand this release without GitHub.

    .venv/Scripts/python docs/_tools/make_archive.py docs/release-package/0.12.0 <clean build folder>

Layout inside the zip (one top folder):
    README_FIRST.txt   what is where, how to rebuild
    MANIFEST.txt       every file with its size and SHA-256
    source/            `git archive` of the release tag - exactly what was tagged
    bin/               the CI-built release zip + .sha256, as published
    build/             how the binary is made: spec, CI workflow, packaging scripts,
                       and the logs of the clean rebuild (tests, PyInstaller, self-test)
    deps/              requirements, the exact versions of the clean build, and
                       every wheel needed to rebuild offline
    assets/            icons and logo; where the optional AI models come from
    docs/              user manual, release overview, developer guide, inventory,
                       screenshots, architecture, wiki and changelog

Then: a secret scan of every text file, and a verification pass that unzips the
archive and checks every file against MANIFEST.txt. Prints a JSON summary.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

PKG = Path(sys.argv[1]).resolve()
CLEAN = Path(sys.argv[2]).resolve()
REPO = Path(__file__).resolve().parents[2]
VERSION = next(l.split("=")[1].strip().strip('"') for l in (REPO / "pyproject.toml").read_text(encoding="utf-8").splitlines()
               if l.startswith("version"))
TAG = f"v{VERSION}"
STAMP = datetime.date.today().strftime("%Y%m%d")
NAME = f"Lunelis_v{VERSION}_{STAMP}"
EXCLUDE_DIRS = {".venv", "venv", "node_modules", "__pycache__", ".git", ".pytest_cache", "build_tmp"}
EXCLUDE_FILES = re.compile(r"(^\.env$|\.db$|\.db-wal$|\.db-shm$|\.pyc$|credentials|\.pem$|\.key$)", re.I)

SECRET_PATTERNS = {
    "GitHub token": r"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}",
    "AWS key": r"AKIA[0-9A-Z]{16}",
    "private key": r"-----BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY-----",
    "password or token assignment": r"(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?token)\s*[:=]\s*['\"][^'\"\s]{8,}['\"]",
    "Slack/Discord webhook": r"hooks\.slack\.com/services/|discord(app)?\.com/api/webhooks/",
}
PRIVATE_INFO = {
    "private network address": r"\b192\.168\.\d{1,3}\.\d{1,3}\b",
    "Windows user folder": r"(?i)C:\\\\?Users\\\\?(?!Demo\b|Public\b|<|%)[A-Za-z][^\\\\\s\"']*",
}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def copy_tree(src: Path, dst: Path) -> None:
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for f in files:
            if EXCLUDE_FILES.search(f):
                continue
            s = Path(root) / f
            t = dst / s.relative_to(src)
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, t)


def run(*cmd: str, cwd: Path | None = None) -> str:
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout


def stage(top: Path) -> None:
    # source: exactly the tag
    src_zip = top.parent / "source.zip"
    run("git", "archive", "--format=zip", "-o", str(src_zip), TAG, cwd=REPO)
    with zipfile.ZipFile(src_zip) as z:
        z.extractall(top / "source")
    src_zip.unlink()

    # bin: the published CI build
    (top / "bin").mkdir(parents=True)
    run("gh", "release", "download", TAG, "--repo", "AxialForge/Lunelis",
        "--pattern", "Lunelis-*-windows.zip*", "--dir", str(top / "bin"))
    z = next((top / "bin").glob("*.zip"))
    want = (top / "bin" / (z.name + ".sha256")).read_text(encoding="ascii").split()[0].lower()
    if sha256(z) != want:
        raise SystemExit(f"{z.name}: checksum does not match its .sha256")

    # build: recipe + clean-build evidence
    b = top / "build"
    (b / "packaging").mkdir(parents=True)
    for f in ("Lunelis.spec", "pyproject.toml"):
        shutil.copy2(REPO / f, b / f)
    shutil.copy2(REPO / ".github" / "workflows" / "python-release.yml", b / "python-release.yml")
    for f in (REPO / "packaging").glob("*.py"):
        shutil.copy2(f, b / "packaging" / f.name)
    home = re.compile(re.escape(str(Path.home())).replace(r"\\", r"[\\/]+") + r"|C:[\\/]+Users[\\/]+[^\\/\s]*~\d", re.I)
    for f in ("clean-build.log", "pyinstaller.log", "selftest.txt", "pytest.log", "coverage.txt"):
        if (CLEAN / f).exists():
            text = (CLEAN / f).read_text(encoding="utf-8", errors="replace")
            # Logs carry this PC's home folder (pip and PyInstaller caches): mask the user name.
            text = home.sub("%USERPROFILE%", text)
            for word in re.findall(r"[A-Za-z]{3,}", Path.home().name):    # paths wrapped across lines
                text = re.sub(rf"\b{word}\b", "USER", text, flags=re.I)
            (b / (f if f.startswith("clean-") else f"clean-{f}")).write_text(text, encoding="utf-8")

    # deps: versions + wheels
    d = top / "deps"
    d.mkdir()
    shutil.copy2(REPO / "requirements.txt", d / "requirements.txt")
    if (CLEAN / "pip-freeze.txt").exists():
        shutil.copy2(CLEAN / "pip-freeze.txt", d / "pip-freeze-clean-build.txt")
    shutil.copy2(REPO / "docs" / "_tools" / "package.json", d / "docs-tools-package.json")
    if (REPO / "docs" / "_tools" / "package-lock.json").exists():
        shutil.copy2(REPO / "docs" / "_tools" / "package-lock.json", d / "docs-tools-package-lock.json")
    py = CLEAN / ".venv" / "Scripts" / "python.exe"
    run(str(py), "-m", "pip", "download", "-q", "-r", str(REPO / "requirements.txt"),
        "pytest", "lupa", "pyinstaller>=6.11", "setuptools", "wheel", "-d", str(d / "wheels"),
        "--only-binary=:all:", "--python-version", "3.13", "--platform", "win_amd64")

    # assets
    copy_tree(REPO / "assets", top / "assets")
    ai = (REPO / "src" / "lunelis" / "edit" / "ai.py").read_text(encoding="utf-8")
    models = re.findall(r"https?://[^\s\"']+\.onnx", ai)
    shas = re.findall(r"\b[0-9a-f]{64}\b", ai)
    (top / "assets" / "AI_MODELS.txt").write_text(
        "The optional AI mask models are NOT included (they are third-party files that\n"
        "Lunelis downloads only when the user asks). Their sources and pinned SHA-256:\n\n"
        + "\n".join(models) + "\n\n" + "\n".join(shas) + "\n\nSee src/lunelis/edit/ai.py.\n", encoding="utf-8")

    # docs
    dd = top / "docs"
    dd.mkdir()
    for f in ("USER_MANUAL.docx", "USER_MANUAL.pdf", "RELEASE_OVERVIEW.docx", "RELEASE_OVERVIEW.pdf",
              "DEVELOPER_GUIDE.md", "DEVELOPER_GUIDE.pdf", "ui_inventory.json"):
        if (PKG / f).exists():
            shutil.copy2(PKG / f, dd / f)
    copy_tree(PKG / "screenshots", dd / "screenshots")
    copy_tree(PKG / "architecture", dd / "architecture")
    copy_tree(REPO / "docs" / "wiki", dd / "wiki")
    shutil.copy2(REPO / "CHANGELOG.md", dd / "CHANGELOG.md")
    tools = dd / "documentation-tools"
    for f in REPO.joinpath("docs", "_tools").rglob("*"):
        rel = f.relative_to(REPO / "docs" / "_tools")
        if f.is_file() and not set(rel.parts) & (EXCLUDE_DIRS | {"build"}):
            (tools / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, tools / rel)

    (top / "README_FIRST.txt").write_text(README.format(v=VERSION, tag=TAG, name=NAME, date=STAMP), encoding="utf-8")


README = """Lunelis {v} - reconstruction archive ({name})
=====================================================================

Everything needed to install, rebuild and understand Lunelis {v} without GitHub.
Made {date} from the {tag} tag. Every file is listed in MANIFEST.txt with its size
and SHA-256.

WHAT IS WHERE
  bin/      Lunelis-{tag}-windows.zip: the official build, made by CI, exactly as
            published; its .sha256 beside it. To install: unzip, run Lunelis.exe.
  source/   The source code at {tag} (git archive: exactly what was tagged).
  deps/     requirements.txt (pinned), pip-freeze-clean-build.txt (every package
            of the verified clean build) and wheels/ (all of them, for an offline
            rebuild on 64-bit Windows with Python 3.13).
  build/    How the binary is made: Lunelis.spec, the CI workflow, the packaging
            scripts, and the logs of the clean rebuild (tests, PyInstaller,
            self-test).
  assets/   Icons and logo. AI_MODELS.txt: where the optional AI models come from.
  docs/     USER_MANUAL (docx, pdf), RELEASE_OVERVIEW (docx, pdf), DEVELOPER_GUIDE
            (md, pdf), ui_inventory.json, screenshots (light and dark),
            architecture (Mermaid source + PNG), the wiki, CHANGELOG, and the
            tools that generated the documents.

REBUILD OFFLINE (Windows 10/11 64-bit, Python 3.13)
  cd source
  py -3.13 -m venv .venv
  .venv\\Scripts\\pip install --no-index --find-links ..\\deps\\wheels -r requirements.txt
  .venv\\Scripts\\pip install --no-index --find-links ..\\deps\\wheels -e . pytest lupa pyinstaller
  .venv\\Scripts\\python -m pytest -q
  .venv\\Scripts\\python packaging\\make_version_info.py
  .venv\\Scripts\\pyinstaller --noconfirm Lunelis.spec
  dist\\Lunelis\\Lunelis.exe --self-test selftest.txt

  Full details: docs/DEVELOPER_GUIDE.md.

NOT INCLUDED, ON PURPOSE
  Virtual environments, node_modules, caches, git history, catalogs, photos,
  credentials and tokens. The two optional AI models (220 MB, third-party) are
  downloaded by Lunelis itself when a user asks; see assets/AI_MODELS.txt.
"""


def manifest(top: Path) -> list[tuple[str, int, str]]:
    rows = []
    for p in sorted(top.rglob("*")):
        if p.is_file() and p.name != "MANIFEST.txt":
            rows.append((p.relative_to(top).as_posix(), p.stat().st_size, sha256(p)))
    lines = [f"Lunelis {VERSION} reconstruction archive - {len(rows)} files", "SHA-256  size(bytes)  path", ""]
    lines += [f"{h}  {s:>11}  {r}" for r, s, h in rows]
    (top / "MANIFEST.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return rows


def scan(top: Path) -> dict:
    found, private = [], []
    for p in top.rglob("*"):
        if not p.is_file() or p.suffix.lower() in {".png", ".jpg", ".zip", ".whl", ".pdf", ".docx", ".ico", ".icns", ".exe", ".dll", ".pyd", ".gz"}:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = p.relative_to(top).as_posix()
        for name, pat in SECRET_PATTERNS.items():
            for m in re.finditer(pat, text):
                found.append({"file": rel, "kind": name, "match": m.group(0)[:12] + "…"})
        for name, pat in PRIVATE_INFO.items():
            for m in re.finditer(pat, text):
                private.append({"file": rel, "kind": name, "match": m.group(0)[:60]})
    # Whole-archive checks for forbidden content
    bad = [p.relative_to(top).as_posix() for p in top.rglob("*")
           if (p.is_dir() and p.name in EXCLUDE_DIRS) or (p.is_file() and EXCLUDE_FILES.search(p.name))]
    return {"secrets": found, "private_info": private, "excluded_items_present": bad}


def verify(zip_path: Path, rows: list[tuple[str, int, str]]) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(zip_path) as z:
            bad = z.testzip()
            z.extractall(tmp)
        top = Path(tmp) / NAME
        mismatches = [r for r, s, h in rows if not (top / r).exists() or sha256(top / r) != h]
        # The archived source must be byte-identical to the tree the clean build used.
        src_diff = []
        for p in (top / "source").rglob("*"):
            if p.is_file():
                q = CLEAN / p.relative_to(top / "source")
                if not q.exists() or sha256(q) != sha256(p):
                    src_diff.append(p.relative_to(top / "source").as_posix())
        return {"zip_test": bad or "ok", "files_checked": len(rows), "hash_mismatches": mismatches,
                "source_differs_from_clean_build_tree": src_diff}


def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="lunelis-archive-"))
    top = work / NAME
    top.mkdir()
    stage(top)
    rows = manifest(top)
    secrets = scan(top)
    out = PKG / f"{NAME}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(top.rglob("*")):
            if p.is_file():
                z.write(p, (Path(NAME) / p.relative_to(top)).as_posix())
    result = {"archive": str(out), "size_mb": round(out.stat().st_size / 1048576, 1), "files": len(rows) + 1,
              "by_folder_mb": {}, "secret_scan": secrets, "verification": verify(out, rows)}
    for r, s, _ in rows:
        k = r.split("/")[0]
        result["by_folder_mb"][k] = round(result["by_folder_mb"].get(k, 0) + s / 1048576, 1)
    (PKG / "archive_report.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    shutil.rmtree(work, ignore_errors=True)
    print(json.dumps({k: v for k, v in result.items() if k != "secret_scan"}, indent=1))
    print("secrets:", len(secrets["secrets"]), "| private-info hits:", len(secrets["private_info"]),
          "| excluded items present:", len(secrets["excluded_items_present"]))


main()
