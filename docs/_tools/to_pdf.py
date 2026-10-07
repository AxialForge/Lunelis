"""
Linux/CI counterpart of to_pdf.ps1: DOCX -> PDF with LibreOffice, no Word needed.

    python docs/_tools/to_pdf.py <file.docx> [<file.docx> ...]

Writes <file>.pdf next to each .docx, with heading bookmarks. The table of
contents and page numbers are the ones saved in the .docx (Word wrote them);
run to_pdf.ps1 on the Windows PC when the content changed and they must be
refreshed. Needs `soffice` (LibreOffice) on PATH. Fonts: Segoe UI is replaced
by whatever is installed, so pagination can differ slightly from Word's.
"""
import subprocess
import sys
from pathlib import Path

FILTER = 'pdf:writer_pdf_Export:{"ExportBookmarks":{"type":"boolean","value":"true"}}'

for name in sys.argv[1:]:
    docx = Path(name).resolve()
    subprocess.run(["soffice", "--headless", "--convert-to", FILTER, "--outdir", str(docx.parent), str(docx)],
                   check=True, capture_output=True, timeout=600)
    print(docx.with_suffix(".pdf"))
