"""create/animation.py: GIF, WebP and MP4 from photos."""
import av
import pytest
from PIL import Image

from lunelis.catalog.schema import open_catalog
from lunelis.create.animation import AnimOptions, Cancelled, make, sequence
from lunelis.importers.scan import add_root, scan_root


@pytest.fixture
def photos(tmp_path):
    conn = open_catalog(tmp_path / "c.db")
    root = tmp_path / "Photos"
    root.mkdir()
    for i in range(4):
        Image.new("RGB", (161, 121), (60 * i, 40, 200 - 40 * i)).save(root / f"B{i}.jpg", quality=95)
    Image.new("RGB", (100, 160), "white").save(root / "Z_portrait.jpg")      # another shape
    scan_root(conn, add_root(conn, root))
    ids = [r[0] for r in conn.execute("SELECT id FROM files ORDER BY filename")]
    return conn, ids, tmp_path / "out"


def test_bounce_goes_there_and_back_without_doubling_the_ends():
    assert sequence([1, 2, 3, 4], True) == [1, 2, 3, 4, 3, 2]
    assert sequence([1, 2], True) == [1, 2] and sequence([1, 2, 3], False) == [1, 2, 3]


def test_gif_and_webp_have_every_frame_and_the_speed(photos):
    conn, ids, out = photos
    gif = make(conn, ids[:4], AnimOptions("gif", frame_ms=120, bounce=True), out, "Burst")
    with Image.open(gif) as im:
        assert im.n_frames == 6 and im.info["duration"] == 120 and im.info.get("loop") == 0
    webp = make(conn, ids[:4], AnimOptions("webp", frame_ms=200, loops=3), out, "Burst")
    with Image.open(webp) as im:
        assert im.n_frames == 4 and im.info.get("loop") == 3


def test_mp4_frames_are_even_sized_and_repeated_for_loops(photos):
    conn, ids, out = photos
    mp4 = make(conn, ids, AnimOptions("mp4", frame_ms=100, loops=2), out, "Burst")
    with av.open(mp4) as c:
        v = c.streams.video[0]
        frames = sum(1 for _ in c.decode(v))
        assert (v.width % 2, v.height % 2) == (0, 0) and (v.width, v.height) == (160, 120)
        assert frames == 10 and abs(float(v.average_rate) - 10) < 0.01


def test_never_overwrites_needs_two_photos_and_can_be_cancelled(photos):
    conn, ids, out = photos
    a = make(conn, ids[:2], AnimOptions("gif"), out, "Same")
    b = make(conn, ids[:2], AnimOptions("gif"), out, "Same")
    assert a != b and b.endswith("Same (2).gif")
    with pytest.raises(ValueError):
        make(conn, ids[:1], AnimOptions("gif"), out, "One")
    with pytest.raises(Cancelled):
        make(conn, ids, AnimOptions("gif"), out, "Stop", cancelled=lambda: True)
