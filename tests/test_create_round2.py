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


def test_deflicker_evens_brightness_but_follows_a_trend():
    from lunelis.create.timelapse import gains
    flicker = [100, 140, 100, 140, 100, 140, 100, 140]
    out = np.array(flicker) * gains(flicker, 5)
    assert np.std(out[2:-2]) < np.std(flicker) / 4
    fade = list(np.linspace(200, 50, 30))                         # a sunset getting darker: kept
    kept = np.array(fade) * gains(fade, 5)
    assert kept[0] > kept[-1] + 100


def test_stabilise_measures_a_known_drift():
    from lunelis.create.timelapse import shifts
    rng = np.random.default_rng(1)
    base = (rng.random((288, 512)) * 255).astype(np.uint8)
    frames = [Image.fromarray(np.roll(base, (0, 3 * i), axis=(0, 1))).convert("RGB") for i in range(5)]
    got = shifts(frames)
    assert np.allclose(got[:, 0], [0, 3, 6, 9, 12], atol=0.6) and np.allclose(got[:, 1], 0, atol=0.6)


def test_a_timelapse_is_an_mp4_of_every_frame(photos):
    from lunelis.create.timelapse import TimelapseOptions, make
    conn, ids, out = photos
    path = make(conn, ids[:8], TimelapseOptions(fps=12, size="720p", stabilize=True), out, "Lapse")
    with av.open(path) as c:
        v = c.streams.video[0]
        assert (v.width, v.height) == (1280, 720) and sum(1 for _ in c.decode(v)) == 8
        assert abs(float(v.average_rate) - 12) < 0.01


def _tone(path, seconds=10, rate=44100):
    import wave
    t = np.arange(int(seconds * rate)) / rate
    data = (np.sin(2 * np.pi * 440 * t) * 12000).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data.tobytes())


def test_slideshow_length_transitions_and_music(photos, tmp_path):
    from lunelis.create.slideshow import FPS, SlideshowOptions, make
    conn, ids, out = photos
    song = tmp_path / "song.wav"
    _tone(song)
    path = make(conn, ids[:4], SlideshowOptions(seconds=1.0, fade=0.3, size="720p", music=str(song)), out, "Show")
    with av.open(path) as c:
        v = c.streams.video[0]
        n = sum(1 for _ in c.decode(v))
        assert n == 4 * FPS and (v.width, v.height) == (1280, 720)
    with av.open(path) as c:
        a = c.streams.audio[0]
        samples = sum(f.samples for f in c.decode(a))
        assert abs(samples / a.rate - 4.0) < 0.15                 # cut to the video's length
    silent = make(conn, ids[:2], SlideshowOptions(seconds=0.5, transition="cut", zoom=False, size="square"), out, "Cut")
    with av.open(silent) as c:
        assert not c.streams.audio and sum(1 for _ in c.decode(c.streams.video[0])) == 2 * round(0.5 * FPS)


def test_crossfade_and_through_black_frames():
    from lunelis.create.slideshow import SlideshowOptions, frames
    a, b = Image.new("RGB", (40, 20), (200, 0, 0)), Image.new("RGB", (40, 20), (0, 0, 200))
    cross = list(frames([a, b], SlideshowOptions(seconds=1.0, fade=0.5, zoom=False)))
    mid = cross[30 - 8].getpixel((20, 10))                        # halfway through the fade
    assert mid[0] > 40 and mid[2] > 40                            # a mix of both
    dark = list(frames([a, b], SlideshowOptions(seconds=1.0, fade=0.5, transition="black", zoom=False)))
    assert min(sum(f.getpixel((20, 10))) for f in dark[15:30]) < 30   # passes through black
