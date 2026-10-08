"""Near-duplicates: fingerprints, grouping rules, keeper choice, setting aside."""
import os
import random

import pytest
from PIL import Image, ImageDraw

from lunelis.catalog.schema import open_catalog
from lunelis.dupes import similar
from lunelis.dupes.quarantine import QUARANTINE_DIR, QuarantineRefused
from lunelis.importers.metadata import extract_pending
from lunelis.importers.scan import add_root, scan_root
from lunelis.raw import thumbnails


def picture(seed: int, size=(1200, 800)) -> Image.Image:
    """A 'photo' with real structure (blocks and lines), different per seed."""
    rnd = random.Random(seed)
    im = Image.new("RGB", size, (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)))
    d = ImageDraw.Draw(im)
    for _ in range(40):
        x, y = rnd.randrange(size[0]), rnd.randrange(size[1])
        d.rectangle([x, y, x + rnd.randrange(50, 400), y + rnd.randrange(50, 300)],
                    fill=(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)))
    return im


@pytest.fixture
def lib(tmp_path):
    main, old = tmp_path / "Main", tmp_path / "Old"
    (main / "2020").mkdir(parents=True)
    (main / "Edits").mkdir(parents=True)
    (old / "2020").mkdir(parents=True)
    one, two = picture(1), picture(2)
    one.save(main / "2020/IMG_1.jpg", quality=95)
    one.resize((600, 400)).save(old / "2020/IMG_1.jpg", quality=70)          # a smaller copy
    one.save(main / "Edits/20200101-IMG_1.jpg", quality=80)                 # an export, same pixels
    one.save(old / "2020/unrelated.jpg", quality=60)                        # same look, other name
    two.save(main / "2020/IMG_2.jpg", quality=95)                           # a different photo
    Image.new("RGB", (800, 600)).save(main / "2020/black.jpg")
    Image.new("RGB", (400, 300)).save(old / "2020/black.jpg")               # black frames prove nothing
    conn = open_catalog(tmp_path / "cat.db")
    for p in (main, old):
        scan_root(conn, add_root(conn, p))
    extract_pending(conn)
    cache = tmp_path / "thumbs"
    thumbnails.generate_pending(conn, cache, workers=2)
    yield conn, main, old, cache
    conn.close()


def fid(conn, rel_end, root_path=None):
    q = "SELECT f.id FROM files f JOIN roots r ON r.id = f.root_id WHERE f.rel_path = ?"
    args = [rel_end]
    if root_path is not None:
        q += " AND r.path = ?"
        args.append(str(root_path))
    return conn.execute(q, args).fetchone()[0]


def names(conn, group):
    return sorted(conn.execute(
        f"SELECT r.path || '/' || f.rel_path FROM files f JOIN roots r ON r.id = f.root_id"
        f" WHERE f.id IN ({','.join(map(str, group))})").fetchall())


def test_fingerprints_come_from_thumbnails(lib):
    conn, _, _, cache = lib
    # 0.45: thumbnails bring their fingerprint; compute_missing is only for old ones
    assert conn.execute("SELECT COUNT(perceptual_hash) FROM files").fetchone()[0] == 7
    assert similar.compute_missing(conn, cache) == 0                # nothing left to read back
    a = conn.execute("SELECT perceptual_hash FROM files WHERE rel_path = '2020/IMG_1.jpg'").fetchall()
    assert len(a) == 2 and bin(int(a[0][0], 16) ^ int(a[1][0], 16)).count("1") <= similar.MAX_DISTANCE


def test_groups_same_photo_with_related_names_only(lib):
    conn, main, old, cache = lib
    assert similar.refresh(conn, cache) == 1
    assert similar.refresh(conn, cache) is None                     # no new fingerprints: no regroup
    (g,) = similar.load(conn)
    ids = {m[0] for m in g.members}
    big, small = fid(conn, "2020/IMG_1.jpg", main), fid(conn, "2020/IMG_1.jpg", old)
    export = fid(conn, "Edits/20200101-IMG_1.jpg")
    assert ids == {big, small, export}                              # not 'unrelated', IMG_2 or the black frames
    assert g.keeper == big                                          # most pixels
    assert g.extras == [small]                                      # the export is never suggested


def test_no_chaining():
    # A~B and B~C but A is 6 bits from C: never one group of three.
    a = 0x0F0F_3C3C_5A5A_F0F0
    b, c = a ^ 0b111, a ^ 0b111111
    info = lambda h, size: (h, None, 1.5, (1, "x"), "img.jpg", None, size)  # noqa: E731
    assert similar.same_photo(info(a, 1), info(b, 2), 4)
    assert similar.same_photo(info(b, 2), info(c, 3), 4)
    assert not similar.same_photo(info(a, 1), info(c, 3), 4)


def test_same_photo_rules():
    h = 0x0F0F_3C3C_5A5A_F0F0
    base = (h, "2020-01-01T10:00:00.25", 1.5, (1, "a"), "dsc1.jpg", "A7", 100)
    other = lambda **k: tuple(k.get(n, v) for n, v in zip(  # noqa: E731
        ("h", "t", "r", "f", "n", "c", "s"), base))
    assert similar.same_photo(base, other(s=50, f=(2, "b")), 4)
    assert not similar.same_photo(base, other(s=100), 4)                          # same size: exact pass
    assert not similar.same_photo(base, other(s=50, t="2020-01-01T10:00:00.37"), 4)  # a burst frame
    assert not similar.same_photo(base, other(s=50, n="dsc2.jpg"), 4)              # unrelated name
    assert not similar.same_photo(base, other(s=50, r=1.0), 4)                     # a crop
    assert similar.same_photo(base, other(s=50, n="20200101-dsc1.jpg", t=None), 4)   # an export


def test_set_aside_keeps_one_and_merges(lib):
    conn, main, old, cache = lib
    similar.refresh(conn, cache)
    (g,) = similar.load(conn)
    small = g.extras[0]
    conn.execute("INSERT INTO ratings (file_id, stars) VALUES (?, 4)", (small,))
    conn.commit()
    with pytest.raises(QuarantineRefused):
        similar.quarantine_similar(conn, g.id, [m[0] for m in g.members])      # never the last copy
    moved = similar.quarantine_similar(conn, g.id, g.extras)
    assert moved == [os.path.join(str(old), QUARANTINE_DIR, "2020", "IMG_1.jpg")]
    assert os.path.exists(moved[0]) and not (old / "2020/IMG_1.jpg").exists()
    assert (main / "2020/IMG_1.jpg").exists()
    assert conn.execute("SELECT stars FROM ratings WHERE file_id = ?", (g.keeper,)).fetchone()[0] == 4
    assert similar.load(conn)[0].extras == []                       # only the kept copy and the export left


def test_near_tab(lib, qtbot=None):
    from PySide6.QtWidgets import QApplication
    from lunelis.ui.near_view import NearView
    QApplication.instance() or QApplication([])
    conn, _, _, cache = lib
    view = NearView(conn)
    view.refresh()
    assert "Not searched yet" in view.summary.text()
    similar.refresh(conn, cache)
    view.refresh()
    assert view.list.rowCount() == 1 and view.detail.rowCount() == 3
    assert [c.isChecked() for _, c in view._checks] == [True]          # the export has no box: always kept
    assert view.group_b.isEnabled() and view.all_b.isEnabled()
    for _, c in view._checks:
        c.setChecked(False)
    assert not view.group_b.isEnabled()
