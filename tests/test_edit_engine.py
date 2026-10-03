"""Editing, the engine: stacks, the pipeline, the catalog, caches and XMP."""
import numpy as np
import pytest
from PIL import Image

from lunelis.catalog.schema import open_catalog
from lunelis.edit import pipeline as P
from lunelis.edit import render, store
from lunelis.edit.stack import Geometry, Stack, dumps, effective, loads
from lunelis.importers.scan import add_root, scan_root
from lunelis.xmp.sidecar import NEW_SIDECAR, XmpFields, apply_fields, parse_fields


def grey(v=0.5, h=40, w=60):
    return np.full((h, w, 3), v, dtype=np.float32)


def photo(h=300, w=450, seed=3):
    rnd = np.random.default_rng(seed)
    base = np.linspace(0.1, 0.9, w, dtype=np.float32)[None, :, None] * np.ones((h, 1, 3), np.float32)
    base[..., 0] *= 1.0
    base[..., 1] *= 0.8
    base[..., 2] *= 0.6
    return np.clip(base + rnd.normal(0, 0.03, base.shape).astype(np.float32), 0, 1)


# --- the stack ---------------------------------------------------------------------------

def test_stack_text_round_trip():
    s = Stack("B&W Classic", 40, {"exposure": 0.35, "shadows": 20},
              Geometry(rotate=90, flip_h=True, angle=-2.5, crop=(0.1, 0.05, 0.9, 0.95)))
    assert loads(dumps(s)) == s
    assert loads("v=9;exposure=0.5;newthing=3;contrast=500") == Stack(adjust={"exposure": 0.5, "contrast": 100})
    assert loads(None) == Stack() and Stack().is_identity()
    assert Stack(adjust={}).with_adjust("contrast", 0).is_identity()


def test_filter_amount_scales_and_manual_adds():
    s = Stack("Warm", 50, {"temp": 10})
    assert effective(s, {"temp": 30, "vibrance": 10}) == {"temp": 25, "vibrance": 5}
    assert effective(Stack("Warm", 0), {"temp": 30}) == {}


# --- the pipeline ------------------------------------------------------------------------

def test_identity_is_exact():
    a = photo()
    assert np.array_equal(P.apply(a, Stack()), a)


def test_adjustments_do_what_they_say():
    g = grey()
    assert P.apply(g, Stack(adjust={"exposure": 1}))[0, 0, 0] > 0.65          # +1 EV on mid-grey
    assert P.apply(g, Stack(adjust={"exposure": -1}))[0, 0, 0] < 0.4
    warm = P.apply(g, Stack(adjust={"temp": 60}))[0, 0]
    assert warm[0] > warm[2]
    a = photo()
    bw = P.apply(a, Stack(adjust={"saturation": -100}))
    assert np.abs(bw[..., 0] - bw[..., 2]).max() < 1e-3
    assert P.apply(a, Stack(adjust={"contrast": 60})).std() > a.std()
    lifted = P.apply(a, Stack(adjust={"shadows": 80}))
    assert lifted[:, :40].mean() > a[:, :40].mean() + 0.02                    # the dark side comes up
    assert abs(lifted[:, -40:].mean() - a[:, -40:].mean()) < 0.03              # the bright side barely moves
    vig = P.apply(grey(0.6, 100, 100), Stack(adjust={"vignette": -80}))
    assert vig[0, 0, 0] < vig[50, 50, 0] - 0.1


def test_geometry():
    a = photo(100, 200)
    assert P.apply(a, Stack(geometry=Geometry(rotate=90))).shape == (200, 100, 3)
    assert np.array_equal(P.apply(a, Stack(geometry=Geometry(flip_h=True))), a[:, ::-1])
    half = P.apply(a, Stack(geometry=Geometry(crop=(0.0, 0.0, 0.5, 1.0))))
    assert half.shape == (100, 100, 3)
    straight = P.apply(np.ones((300, 450, 3), np.float32), Stack(geometry=Geometry(angle=8)))
    assert straight.min() > 0.9                                               # no empty corners
    assert abs(straight.shape[1] / straight.shape[0] - 1.5) < 0.02            # same shape


def test_preview_matches_full_size():
    big = photo(1200, 1800)
    small = np.asarray(Image.fromarray((big * 255).astype(np.uint8)).resize((450, 300), Image.Resampling.BOX),
                       dtype=np.float32) / 255
    s = Stack(adjust={"exposure": 0.4, "shadows": 40, "highlights": -30, "sharpen": 40, "denoise": 30, "vibrance": 30})
    out_big = P.to_image(P.apply(big, s)).resize((450, 300), Image.Resampling.BOX)
    out_small = P.to_image(P.apply(small, s))
    diff = np.abs(np.asarray(out_big, np.float32) - np.asarray(out_small, np.float32)).mean()
    assert diff < 6                                                           # of 255


def test_auto_brightens_a_dark_photo():
    assert P.auto(photo() * 0.3)["exposure"] > 0.5


# --- the catalog, caches, thumbnails ----------------------------------------------------------

@pytest.fixture
def lib(tmp_path):
    root = tmp_path / "Photos"
    root.mkdir()
    img = Image.new("RGB", (600, 400), (90, 90, 90))
    exif = img.getexif()
    exif[0x0112] = 6                                                          # rotate 90 CW to display
    img.save(root / "a.jpg", exif=exif)
    Image.new("RGB", (300, 200), (200, 50, 50)).save(root / "b.jpg")
    conn = open_catalog(tmp_path / "cat.db")
    scan_root(conn, add_root(conn, root))
    yield conn, root, tmp_path
    conn.close()


def fid(conn, name):
    return conn.execute("SELECT id FROM files WHERE filename = ?", (name,)).fetchone()[0]


def test_save_bumps_rev_and_queues_the_sidecar(lib):
    conn, *_ = lib
    a = fid(conn, "a.jpg")
    assert store.save(conn, a, Stack(adjust={"exposure": 1}))
    assert not store.save(conn, a, Stack(adjust={"exposure": 1}))           # unchanged: nothing to do
    store.save(conn, a, Stack(adjust={"exposure": 0.5}))
    assert store.rev(conn, a) == 2 and store.get(conn, a).adjust == {"exposure": 0.5}
    assert conn.execute("SELECT xmp_pending FROM ratings WHERE file_id = ?", (a,)).fetchone()[0] == 1
    assert store.save(conn, a, Stack()) and store.rev(conn, a) == 0          # back to the original
    assert store.edited_ids(conn, [a]) == set()


def test_filters(lib):
    conn, *_ = lib
    names = [n for n, _, _ in store.filters(conn)]
    assert "Vivid" in names and "B&W Classic" in names
    with pytest.raises(ValueError):
        store.save_filter(conn, "Vivid", {"exposure": 1})
    store.save_filter(conn, "My look", {"exposure": 0.3, "fade": 20, "bogus": 3})
    assert store.filter_params(conn, "my look") == {"exposure": 0.3, "fade": 20}
    assert store.flatten(conn, Stack("My look", 50, {"fade": 5})) == {"exposure": 0.15, "fade": 15}
    store.delete_filter(conn, "My look")
    assert store.filter_params(conn, "My look") is None


def test_source_is_upright_and_outputs_follow_the_edit(lib):
    conn, root, tmp = lib
    src = render.load_source(str(root / "a.jpg"), False, 2560)
    assert src.shape[:2] == (600, 400)                                        # EXIF orientation applied
    thumbs, edits = tmp / "cache" / "thumbnails", tmp / "cache" / "edits"
    a = fid(conn, "a.jpg")
    rel = render.render_outputs(str(root / "a.jpg"), False, a, Stack(adjust={"exposure": 2}), None, thumbs, edits)
    assert render.proxy_path(edits, a).exists()
    assert np.asarray(Image.open(thumbs / rel)).mean() > 150                  # the edited look
    render.clear_outputs(str(root / "a.jpg"), a, 6, thumbs, edits)
    assert not render.proxy_path(edits, a).exists()
    assert abs(np.asarray(Image.open(thumbs / rel)).mean() - 90) < 10


def test_thumbnail_pass_shows_the_edit(lib):
    from lunelis.raw.thumbnails import generate_pending
    conn, root, tmp = lib
    b = fid(conn, "b.jpg")
    store.save(conn, b, Stack(adjust={"saturation": -100}))
    thumbs = tmp / "cache" / "thumbnails"
    generate_pending(conn, thumbs, workers=1)
    rel = conn.execute("SELECT thumbnail_path FROM files WHERE id = ?", (b,)).fetchone()[0]
    r, g, bl = np.asarray(Image.open(thumbs / rel), np.float32).mean(axis=(0, 1))
    assert abs(r - bl) < 8                                                    # grey, not red


def test_library_marks_edited_photos(lib):
    from lunelis.ui.library import LibraryIndex
    conn, *_ = lib
    store.save(conn, fid(conn, "b.jpg"), Stack(adjust={"exposure": 1}))
    idx = LibraryIndex()
    idx.load(conn)
    marks = {idx.file_id(i): idx.tile(i).edited for i in range(len(idx))}
    assert marks == {fid(conn, "a.jpg"): False, fid(conn, "b.jpg"): True}


# --- XMP ----------------------------------------------------------------------------------

def test_edit_stack_in_the_sidecar():
    text = dumps(Stack("B&W Classic", 70, {"exposure": 0.5}))
    out = apply_fields(NEW_SIDECAR, XmpFields(stars=3, edit=text))
    assert 'xmlns:lunelis="' in out and "B&amp;W Classic" in out
    assert parse_fields(out) == XmpFields(stars=3, edit=text)
    cleared = apply_fields(out, XmpFields(stars=3))
    assert "lunelis:EditStack" not in cleared and parse_fields(cleared) == XmpFields(stars=3)
    darktable = NEW_SIDECAR.replace('xmlns:xmp="http://ns.adobe.com/xap/1.0/"',
                                    'xmlns:xmp="http://ns.adobe.com/xap/1.0/"\n    xmlns:darktable="http://darktable.sf.net/"\n'
                                    '    darktable:history_end="4"')
    kept = apply_fields(darktable, XmpFields(edit=text))
    assert 'darktable:history_end="4"' in kept


def test_export_writes_the_stack(lib, tmp_path):
    from lunelis.xmp.sync import central_path, export_pending
    conn, root, _ = lib
    b = fid(conn, "b.jpg")
    store.save(conn, b, Stack(adjust={"contrast": 30}))
    store_dir = tmp_path / "sidecars"
    export_pending(conn, mode="central", update_existing=False, store_dir=store_dir)
    rid, rpath = conn.execute("SELECT id, path FROM roots").fetchone()
    xmp = central_path(store_dir, rid, rpath, "b.jpg", "b.jpg")
    assert "lunelis:EditStack=\"v=1;contrast=30\"" in open(xmp, encoding="utf-8").read()


# --- tone curve (Phase B) ---------------------------------------------------------------------

def test_curves_are_monotone_and_round_trip():
    xs = np.linspace(0, 1, 501)
    ys = P.curve_values(((0, 0), (0.3, 0.6), (0.4, 0.62), (1, 1)), xs)
    assert np.all(np.diff(ys) >= -1e-9)                                      # never reverses
    assert abs(P.curve_values(((0, 0), (1, 1)), xs) - xs).max() < 1e-9
    s = Stack().with_curve("rgb", [(0.25, 0.2), (0.75, 0.85)]).with_curve("b", [(0, 0.1), (1, 0.9)])
    assert loads(dumps(s)) == s
    assert Stack().with_curve("g", [(0, 0), (1, 1)]).is_identity()


def test_curves_change_pixels():
    g = grey(0.5)
    assert P.apply(g, Stack().with_curve("rgb", [(0.5, 0.7)]))[0, 0, 0] == pytest.approx(0.7, abs=0.01)
    blue_down = P.apply(g, Stack().with_curve("b", [(0.5, 0.3)]))[0, 0]
    assert blue_down[2] < 0.35 and abs(blue_down[0] - 0.5) < 0.01
    lifted = P.apply(grey(0.0), Stack().with_curve("rgb", [(0, 0.1), (1, 1)]))
    assert lifted[0, 0, 0] == pytest.approx(0.1, abs=0.01)                   # the black point lifts


# --- masks (Phase B) --------------------------------------------------------------------------

def test_masks_round_trip_and_blend():
    from lunelis.edit import masks as M
    lin = M.Mask("linear", (0.5, 0.0, 0.5, 0.5), {"exposure": -1})
    rad = M.Mask("radial", (0.5, 0.5, 0.1, 0.1, 0.3), {"exposure": 1}, invert=True)
    br = M.Mask("brush", (), {"saturation": -100},
                strokes=((0.05, 0.5, 1.0, False, ((0.2, 0.8), (0.4, 0.8))), (0.05, 0.0, 1.0, True, ((0.4, 0.8),))))
    s = Stack(masks=(lin, rad, br))
    assert loads(dumps(s)) == s and not s.is_identity()
    g = grey(0.5, 200, 200)
    top, bottom = P.apply(g, Stack(masks=(lin,)))[[2, 150], 100, 0]
    assert top < 0.4 and bottom == pytest.approx(0.5, abs=1e-3)
    out = P.apply(g, Stack(masks=(rad,)))
    assert out[100, 100, 0] == pytest.approx(0.5, abs=1e-3) and out[5, 5, 0] > 0.6     # inverted: outside


def test_masks_follow_the_crop_not_the_frame():
    from lunelis.edit import masks as M
    rad = M.Mask("radial", (0.25, 0.5, 0.1, 0.1, 0.1), {"exposure": 1})
    g = grey(0.5, 200, 400)
    whole = P.apply(g, Stack(masks=(rad,)))
    cropped = P.apply(g, Stack(masks=(rad,), geometry=Geometry(crop=(0.0, 0.0, 0.5, 1.0))))
    assert whole[100, 100, 0] > 0.6 and cropped[100, 100, 0] > 0.6                   # same spot of the photo
    assert cropped[100, 199, 0] == pytest.approx(0.5, abs=1e-3)


def test_brush_paints_and_erases():
    from lunelis.edit import masks as M
    paint = (0.05, 0.0, 1.0, False, ((0.2, 0.5), (0.8, 0.5)))
    erase = (0.08, 0.0, 1.0, True, ((0.5, 0.5),))
    a = M.raster_strokes((paint, erase), 1000, 1000)
    h, w = a.shape
    assert a[h // 2, int(w * 0.25)] > 0.9 and a[h // 2, w // 2] < 0.05 and a[int(h * 0.2), w // 2] == 0


def test_tiled_matches_whole_with_masks():
    from lunelis.edit import masks as M
    big = photo(900, 1350)
    s = Stack(adjust={"shadows": 30}, masks=(M.Mask("linear", (0.5, 0, 0.5, 0.6), {"exposure": -0.8, "shadows": 20}),
                                             M.Mask("brush", (), {"exposure": 1},
                                                    strokes=((0.05, 0.5, 1, False, ((0.3, 0.3), (0.6, 0.7))),))))
    whole = (P.apply(big, s) * 255 + 0.5).astype(np.uint8)
    tiled = P.apply_tiled(big, s, rows=200)
    assert np.abs(whole.astype(int) - tiled.astype(int)).mean() < 1.0


# --- lens corrections (Phase B) ------------------------------------------------------------------

def test_lens_profile_lookup_and_round_trip():
    from lunelis.edit import lens as L
    a7 = L.LensInfo("SONY", "ILCE-7RM5", "FE 24-105mm F4 G OSS", 24.0, 4.0)
    assert "24-105" in L.profile_name(a7)
    assert L.profile_name(L.LensInfo("SONY", "ILCE-7RM3", "Sony FE 24-105mm F4 G OSS (SEL24105G)", 24, 4))
    assert L.profile_name(L.LensInfo("Apple", "iPhone X", "iPhone X back camera", 4, 1.8)) is None
    s = Stack().with_lens("profile", True).with_lens("distortion", 30).with_lens("ca_red", -20)
    assert loads(dumps(s)) == s and Stack().with_lens("vignette", 0).is_identity()


def test_lens_corrections_change_the_picture():
    from lunelis.edit import lens as L
    yy, xx = np.mgrid[0:400, 0:600]
    grid = np.repeat((((xx // 40 + yy // 40) % 2) * 0.6 + 0.2).astype(np.float32)[..., None], 3, 2)
    a7 = L.LensInfo("SONY", "ILCE-7RM5", "FE 24-105mm F4 G OSS", 24.0, 4.0)
    prof = P.apply(grid, Stack().with_lens("profile", True), lens_info=a7)
    assert prof.shape == grid.shape and np.abs(prof - grid).mean() > 0.01            # geometry moved
    assert prof[:5, :5].mean() > P.apply(grid, Stack(adjust={"contrast": 1}))[:5, :5].mean() - 0.3
    vig = P.apply(grey(0.4, 100, 150), Stack().with_lens("vignette", 80))
    assert vig[0, 0, 0] > vig[50, 75, 0] + 0.1                                        # corners brightened
    barrel = P.apply(grid, Stack().with_lens("distortion", 60))
    assert not np.isnan(barrel).any() and barrel.min() >= 0.19                        # corners stay filled
    # No profile info: the profile setting is harmless.
    assert np.allclose(P.apply(grid, Stack().with_lens("profile", True)), grid, atol=2e-3)


def test_lens_in_tiled_export_matches():
    from lunelis.edit import lens as L
    a7 = L.LensInfo("SONY", "ILCE-7RM5", "FE 24-105mm F4 G OSS", 24.0, 4.0)
    a = photo(600, 900)
    s = Stack(adjust={"exposure": 0.2}).with_lens("profile", True).with_lens("distortion", 20)
    whole = (P.apply(a, s, lens_info=a7) * 255 + 0.5).astype(np.uint8)
    tiled = P.apply_tiled(a, s, rows=128, lens_info=a7)
    assert np.abs(whole.astype(int) - tiled.astype(int)).max() <= 1


# --- noise reduction ---------------------------------------------------------------------------

def test_noise_reduction_removes_noise_and_keeps_edges():
    from lunelis.edit import denoise
    rng = np.random.default_rng(4)
    clean = np.zeros((200, 300, 3), np.float32)
    clean[:, 150:] = 0.8                                   # a hard edge
    clean[:, :150] = 0.25
    noisy = np.clip(clean + rng.normal(0, 0.04, clean.shape).astype(np.float32), 0, 1)
    sigma = denoise.estimate_sigma(noisy.mean(axis=2))
    assert 0.02 < sigma < 0.06                              # finds roughly the noise that's there
    out = denoise.apply(noisy, {"denoise": 60, "denoise_color": 60})
    assert out[:, 20:130].std() < noisy[:, 20:130].std() * 0.5
    assert abs(out[:, 145].mean() - 0.25) < 0.05 and abs(out[:, 155].mean() - 0.8) < 0.05   # edge stays sharp
    more_detail = denoise.apply(noisy, {"denoise": 60, "denoise_detail": 100})
    assert more_detail[:, 20:130].std() > out[:, 20:130].std()
    assert np.array_equal(denoise.apply(noisy, {"denoise_detail": 50}), noisy)   # no amount: untouched
    s = Stack(adjust={"denoise": 40, "denoise_color": 30})
    assert loads(dumps(s)) == s
    strips = denoise.apply_strips(noisy, {"denoise": 60}, rows=64)
    assert np.abs(strips - denoise.apply(noisy, {"denoise": 60}, denoise.estimate_sigma(
        __import__("cv2").cvtColor(noisy, __import__("cv2").COLOR_RGB2YCrCb)[..., 0]))).mean() < 0.01
