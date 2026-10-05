"""0.30 sensor dust map: spots found at the same place across a camera's
frames, a cleaning noticed, and healed as edits (originals untouched)."""
import os
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PIL import Image  # noqa: E402

from lunelis import dust  # noqa: E402
from lunelis.edit import store  # noqa: E402

T0 = datetime(2026, 3, 1, 12)
SPOTS = [(0.30, 0.25), (0.72, 0.60)]                  # sensor fractions of the dust


def sky(seed, w=512, h=341, spots=SPOTS, strength=0.12):
    """A plain bright sky with soft dark blots where the dust sits."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[:h, :w]
    a = 0.75 + 0.05 * (yy / h) + rng.normal(0, 0.004, (h, w))
    for sx, sy in spots:
        a -= strength * np.exp(-(((xx - sx * w) ** 2 + (yy - sy * h) ** 2) / (2 * 4.0 ** 2)))
    return (np.clip(a, 0, 1) * 255).astype(np.uint8)


@pytest.fixture
def cam(tmp_path):
    from lunelis.catalog.schema import open_catalog
    from lunelis.importers.scan import add_root, scan_root
    root = tmp_path / "Photos"
    root.mkdir()
    thumbs = tmp_path / "thumbs"
    thumbs.mkdir()
    conn = open_catalog(tmp_path / "cat.db")
    plan = []
    for k in range(12):                               # 12 frames with dust ...
        plan.append((f"A{k:02d}.jpg", sky(k), T0 + timedelta(days=k), 1, 11.0))
    for k in range(8):                                # ... then a cleaning: 8 clean frames
        plan.append((f"B{k:02d}.jpg", sky(100 + k, spots=[]), T0 + timedelta(days=40 + k), 1, 11.0))
    plan.append(("C00.jpg", np.rot90(sky(200), -1).copy(), T0 + timedelta(days=5, hours=1), 6, 11.0))   # portrait
    plan.append(("D00.jpg", sky(300), T0 + timedelta(days=6, hours=1), 1, 2.8))   # wide open: not looked at
    for name, a, *_ in plan:
        Image.fromarray(a).convert("RGB").save(root / name, quality=95)
    scan_root(conn, add_root(conn, root))
    ids = dict(conn.execute("SELECT filename, id FROM files").fetchall())
    for name, a, when, orient, ap in plan:
        fid = ids[name]
        Image.fromarray(a).convert("RGB").save(thumbs / f"{fid}.jpg", quality=95)
        conn.execute("UPDATE files SET thumbnail_path = ? WHERE id = ?", (f"{fid}.jpg", fid))
        conn.execute("INSERT OR REPLACE INTO exif (file_id, captured_at, camera_model, aperture, orientation)"
                     " VALUES (?, ?, 'ILCE-7RM5', ?, ?)", (fid, when.isoformat(), ap, orient))
    conn.commit()
    yield conn, ids, thumbs
    conn.close()


def test_the_dust_is_found_where_it_is(cam):
    conn, ids, thumbs = cam
    assert dust.cameras(conn) == [("ILCE-7RM5", 21)]
    m = dust.build(conn, "ILCE-7RM5", thumbs)
    assert m.frames == 21 and len(m.spots) == 2
    found = sorted((s.x, s.y) for s in m.spots)
    for (fx, fy), (tx, ty) in zip(found, sorted(SPOTS)):
        assert abs(fx - tx) < 0.04 and abs(fy - ty) < 0.05
    assert all(s.confidence >= 0.5 for s in m.spots)


def test_noise_is_not_dust(tmp_path):
    rng = np.random.default_rng(9)
    hits = np.zeros(dust.GRID[::-1], int)
    for k in range(12):
        a = np.clip(0.75 + rng.normal(0, 0.01, (341, 512)), 0, 1).astype(np.float32)
        _c, b = dust.blots(a)
        hits += dust._cells(b)
    assert hits.max() < 6                                    # nothing in the same place again and again


def test_a_cleaning_is_noticed(cam):
    conn, ids, thumbs = cam
    m = dust.build(conn, "ILCE-7RM5", thumbs)
    assert all(s.state == "cleaned" for s in m.spots)
    assert m.cleanings == [(T0 + timedelta(days=11)).date().isoformat()]
    assert "cleaned around" in dust.describe(m) and "none left now" in dust.describe(m)
    dust.save(conn, m)
    assert dust.load(conn, "ILCE-7RM5").spots == m.spots


def test_healing_is_an_edit_and_can_be_undone(cam, tmp_path):
    conn, ids, thumbs = cam
    m = dust.build(conn, "ILCE-7RM5", thumbs)
    before = {p.name: p.read_bytes() for p in (tmp_path / "Photos").iterdir()}
    plan = dust.affected(conn, m)
    assert ids["A03.jpg"] in plan and ids["B03.jpg"] not in plan           # only while the dust was there
    assert ids["D00.jpg"] in plan                                          # wide open frames get healed too
    portrait = plan[ids["C00.jpg"]]
    assert {(round(s.x, 1), round(s.y, 1)) for s in portrait} == {(0.8, 0.3), (0.4, 0.7)}   # turned upright
    done, skipped = dust.heal(conn, m)
    assert done == len(plan) and skipped == 0
    assert len(store.get(conn, ids["A03.jpg"]).retouch) == 2
    assert {p.name: p.read_bytes() for p in (tmp_path / "Photos").iterdir()} == before   # files untouched
    from lunelis.edit import pipeline
    a = np.asarray(Image.open(tmp_path / "Photos" / "A03.jpg").convert("RGB"), np.float32) / 255
    out = pipeline.apply(a, store.get(conn, ids["A03.jpg"]))
    y, x = int(0.25 * a.shape[0]), int(0.30 * a.shape[1])
    assert out[y, x].mean() > a[y, x].mean() + 0.04                         # the blot is lifted
    assert dust.undo(conn, "ILCE-7RM5") == done
    assert store.get(conn, ids["A03.jpg"]).retouch == ()


def test_photos_turned_in_lunelis_are_skipped(cam):
    from lunelis.edit.stack import Geometry, Stack
    conn, ids, thumbs = cam
    store.save(conn, ids["A01.jpg"], Stack(geometry=Geometry(rotate=90)))
    done, skipped = dust.heal(conn, dust.build(conn, "ILCE-7RM5", thumbs))
    assert skipped == 1 and store.get(conn, ids["A01.jpg"]).retouch == ()


def test_the_sensor_dust_page(cam, monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from lunelis import paths
    from lunelis.ui.dust_view import DustView
    conn, ids, thumbs = cam
    monkeypatch.setattr(paths, "THUMBNAIL_CACHE", thumbs)
    view = DustView(conn)
    try:
        view.refresh()
        assert view.camera.currentData() == "ILCE-7RM5" and not view.heal_b.isEnabled()
        view.look()
        view.bg.wait()
        assert view.map is not None and len(view.map.spots) == 2
        assert view.heal_b.text().startswith("Heal on ") and "cleaned around" in view.text.text()
        shown = []
        view.show_ids.connect(lambda i, n: shown.append(n))
        view.show_b.click()
        assert shown == ["Sensor dust"]
        from PySide6.QtWidgets import QMessageBox
        monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
        view.heal_b.click()
        view.bg.wait()
        assert "Healed" in view.text.text() and view.undo_b.isEnabled()
        view.undo_b.click()
        assert "Took the dust spots off" in view.text.text() and not view.undo_b.isEnabled()
        view.refresh()                                             # the saved map comes back
        assert view.map is not None and len(view.map.spots) == 2
    finally:
        view.deleteLater()
