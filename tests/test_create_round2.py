"""v0.22: Create round two - contact sheets, timelapses, slideshows, before/after, print sheets."""
import av
import numpy as np
import pytest
from PIL import Image

from lunelis import paths
from lunelis.catalog.schema import open_catalog
from lunelis.importers.scan import add_root, scan_root
from lunelis.raw.thumbnails import generate_pending


@pytest.fixture
def photos(tmp_path):
    conn = open_catalog(tmp_path / "c.db")
    root = tmp_path / "P"
    root.mkdir()
    for i in range(12):
        Image.new("RGB", (300, 200), (20 * i, 100, 200 - 10 * i)).save(root / f"IMG_{i:02d}.jpg")
    rid = add_root(conn, root)
    scan_root(conn, rid)
    generate_pending(conn, paths.THUMBNAIL_CACHE)
    ids = [r[0] for r in conn.execute("SELECT id FROM files WHERE root_id = ? ORDER BY filename", (rid,))]
    return conn, ids, tmp_path / "out"


def test_contact_sheet_pages_grid_and_captions(photos):
    from lunelis.create import contact_sheet as cs
    conn, ids, out = photos
    opts = cs.SheetOptions(columns=4, title="Air show", format="png")
    _, _, rows, _, _ = cs.layout(opts)
    per = rows * 4
    many = (ids * 10)[:per + 3]                                   # just over one page
    assert cs.pages_needed(opts, len(many)) == 2
    files = cs.make(conn, many, opts, out, "Sheet")
    assert [f.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for f in files] == ["Sheet - page 1.png", "Sheet - page 2.png"]
    with Image.open(files[0]) as im:
        assert im.size == (1275, 1650)                            # Letter at 150 dpi
    pdf = cs.make(conn, ids, cs.SheetOptions(page="a4", landscape=True), out, "Sheet")
    assert pdf[0].endswith("Sheet.pdf")
    with open(pdf[0], "rb") as f:
        assert f.read(5) == b"%PDF-"
    with pytest.raises(ValueError):
        cs.SheetOptions(columns=12).check(3)


def test_tools_are_on_the_create_page(tmp_path):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from lunelis.ui.create_page import CreatePage
    conn = open_catalog(tmp_path / "w.db")
    page = CreatePage(conn)
    assert "contact" in page.tools
    tool = page.tools["contact"]
    tool.columns.setValue(3)
    assert tool.options().columns == 3 and tool.options().format == "pdf"
