"""Build a chosen set of documents and zip them, named for the release and the document revision.

    python3 docs/kit/package.py <docset.json> [out-dir]

docset.json:
{
  "product": "Unpacker V2", "version": "0.5.1", "revision": "1", "date": "2026-10-09",
  "documents": [ {"name": "User Manual", "src": "src/manual.md", "type": "manual"}, ... ],
  "include_sources": false
}

Only the documents listed are built: the person who owns the software chooses them (see DOCSET.md).
Each file is named  <Product>-<Document>-v<version>-rev<revision>.docx / .pdf  and the zip
<Product>-v<version>-documentation-rev<revision>.zip  with an index (README.txt) listing what is in it.
Paths in src are relative to the docset.json folder.
"""
import json, os, re, shutil, subprocess, sys, tempfile, zipfile
from pathlib import Path

KIT = Path(__file__).resolve().parent
spec_path = Path(sys.argv[1]).resolve()
spec = json.loads(spec_path.read_text(encoding="utf-8"))
base = spec_path.parent
out = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else base / "out"
out.mkdir(parents=True, exist_ok=True)
slug = lambda t: re.sub(r"[^A-Za-z0-9]+", "-", t).strip("-")
stem = lambda doc: f"{slug(spec['product'])}-{slug(doc)}-v{spec['version']}-rev{spec['revision']}"
env = dict(os.environ)
env["NODE_PATH"] = os.pathsep.join(filter(None, [env.get("NODE_PATH", ""), subprocess.run(["npm", "root", "-g"], capture_output=True, text=True).stdout.strip(), str(KIT / "node_modules"), "/opt/node-tools/node_modules"]))
built, failed = [], []
tmp = Path(tempfile.mkdtemp(prefix="docset-"))
for d in spec["documents"]:
    src = (base / d["src"]).read_text(encoding="utf-8")
    fm = {"type": d["type"], "product": spec["product"], "version": spec["version"], "revision": spec["revision"], "date": spec.get("date", ""), "status": spec.get("status", "Released")}
    m = re.match(r"---\n(.*?)\n---\n", src, re.S)
    if m:
        for line in m.group(1).splitlines():
            k, _, v = line.partition(":")
            if k.strip() and k.strip() not in ("version", "revision", "product", "type", "date", "status"): fm[k.strip()] = v.strip()
        src = src[m.end():]
    name = stem(d["name"])
    f = tmp / f"{name}.md"
    f.write_text("---\n" + "\n".join(f"{k}: {v}" for k, v in fm.items() if v != "") + "\n---\n" + src, encoding="utf-8")
    # figures are resolved against the source folder
    text = f.read_text(encoding="utf-8")
    text = re.sub(r"(!\[[^\]]*\]\()(?!https?:|/)([^)]+\))", lambda mm: mm.group(1) + str((base / (Path(d["src"]).parent / mm.group(2).rstrip(")"))).resolve()) + ")", text)
    f.write_text(text, encoding="utf-8")
    r = subprocess.run(["node", str(KIT / "render.js"), str(f), str(out)], env=env, capture_output=True, text=True)
    if r.returncode: failed.append((d["name"], r.stderr.strip())); continue
    fin = subprocess.run(["python3", str(KIT / "finish.py"), str(out / f"{name}.docx")], capture_output=True, text=True)
    chk = subprocess.run(["python3", str(KIT / "check.py"), str(out / f"{name}.pdf"), str(f)], capture_output=True, text=True)
    built.append((d["name"], name, chk.stdout.strip().splitlines()))
    print(f"built {name}\n   " + "\n   ".join(chk.stdout.strip().splitlines()))
for n, e in failed: print(f"FAILED {n}: {e}")
index = [f"{spec['product']} {spec['version']} - documentation, revision {spec['revision']} ({spec.get('date','')})", "", "Contents:"]
index += [f"  {name}.docx / .pdf   {title}" for title, name, _ in built]
zipname = out / f"{slug(spec['product'])}-v{spec['version']}-documentation-rev{spec['revision']}.zip"
with zipfile.ZipFile(zipname, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("README.txt", "\n".join(index) + "\n")
    for _, name, _ in built:
        for ext in ("docx", "pdf"): z.write(out / f"{name}.{ext}", f"{name}.{ext}")
    if spec.get("include_sources"):
        for d in spec["documents"]: z.write(base / d["src"], "sources/" + Path(d["src"]).name)
print("zip:", zipname)
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(1 if failed else 0)
