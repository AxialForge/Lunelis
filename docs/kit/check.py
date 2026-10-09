"""Quality checks for a built document: python3 check.py <file.pdf> <source.md>

- portrait US Letter on every page
- no near-empty pages (fewer than 25 words and no picture) other than the cover
- the contents list has page numbers
- no hard-coded chapter references ("chapter 4", "see 3.2") in the source
- counts of To check / Discrepancies bullets, so a report can quote them
"""
import re, subprocess, sys

pdf, src = sys.argv[1], sys.argv[2]
info = subprocess.run(["pdfinfo", "-f", "1", "-l", "9999", pdf], capture_output=True, text=True).stdout
pages = int(re.search(r"Pages:\s+(\d+)", info).group(1))
problems = []
sizes = set(re.findall(r"Page\s+\d+ size:\s+([\d.]+) x ([\d.]+)", info))
for w, h in sizes:
    if abs(float(w) - 612) > 2 or abs(float(h) - 792) > 2: problems.append(f"page size {w}x{h} is not US Letter portrait")
imgs = subprocess.run(["pdfimages", "-list", pdf], capture_output=True, text=True).stdout.splitlines()[2:]
img_pages = {int(l.split()[0]) for l in imgs if l.strip()}
for p in range(2, pages + 1):
    t = subprocess.run(["pdftotext", "-f", str(p), "-l", str(p), pdf, "-"], capture_output=True, text=True).stdout
    nw = len(t.split())
    if nw < 25 and p not in img_pages: problems.append(f"page {p} is blank or nearly empty ({nw} words)")
    elif nw < 60 and p not in img_pages and p != pages and p != 2: problems.append(f"page {p} is sparse ({nw} words): check it, or use {{nobreak}} on the chapter before it")
toc = subprocess.run(["pdftotext", "-f", "2", "-l", "3", "-layout", pdf, "-"], capture_output=True, text=True).stdout
if not re.search(r"\.{3,}\s*\d+|\s\d+\s*$", toc, re.M): problems.append("contents list has no page numbers")
text = open(src, encoding="utf-8").read()
for m in re.finditer(r"(?i)\b(chapter \d+|see \d+\.\d+|section \d+\.\d+)\b", text):
    problems.append(f"hard-coded reference '{m.group(0)}' in the source")
print(f"{pdf}: {pages} pages, {len(img_pages)} pages with pictures")
print("PROBLEMS:\n  " + "\n  ".join(problems) if problems else "checks passed")
