"""Fill the table of contents of a .docx and write its PDF, using LibreOffice.

    python3 finish.py out/Name.docx      ->  rewrites Name.docx, writes Name.pdf next to it

LibreOffice builds the contents list and the real page numbers; Word would do the same on open,
but then the file and its PDF would disagree until someone updates fields by hand.
"""
import os, subprocess, sys, time
import uno
from com.sun.star.beans import PropertyValue

def pv(name, value):
    p = PropertyValue(); p.Name = name; p.Value = value; return p

def main(paths):
    port = 2002 + os.getpid() % 500
    profile = f"/tmp/lo-profile-{os.getpid()}"
    proc = subprocess.Popen(["soffice", "--headless", "--invisible", "--norestore", f"-env:UserInstallation=file://{profile}",
                             f"--accept=socket,host=localhost,port={port};urp;"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        ctx = None
        for _ in range(90):
            try:
                local = uno.getComponentContext()
                r = local.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", local)
                ctx = r.resolve(f"uno:socket,host=localhost,port={port};urp;StarOffice.ComponentContext"); break
            except Exception:
                time.sleep(1)
        if ctx is None: raise SystemExit("LibreOffice did not start")
        desk = ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
        for f in paths:
            f = os.path.abspath(f); url = "file://" + f
            doc = desk.loadComponentFromURL(url, "_blank", 0, (pv("Hidden", True),))
            for _ in range(2):          # twice: the contents list changes the page count
                idx = doc.getDocumentIndexes()
                for i in range(idx.getCount()): idx.getByIndex(i).update()
                doc.refresh()
            doc.storeToURL(url, (pv("FilterName", "MS Word 2007 XML"),))
            doc.storeToURL("file://" + os.path.splitext(f)[0] + ".pdf", (pv("FilterName", "writer_pdf_Export"),))
            doc.close(True)
            print("finished", f)
        try: desk.terminate()
        except Exception: pass
    finally:
        try: proc.wait(timeout=20)
        except Exception: proc.kill()

main(sys.argv[1:])
