"""v0.21: shooting stats - keeper rate by gear and exposure, focal use, months, habits, recap."""
import pytest
from PIL import Image

from lunelis import stats
from lunelis.catalog.schema import open_catalog
from lunelis.importers.scan import add_root, scan_root


@pytest.fixture
def lib(tmp_path):
    conn = open_catalog(tmp_path / "c.db")
    root = tmp_path / "P"
    root.mkdir()
    shots = [  # lens, focal, aperture, iso, shutter, when, pick
        ("24-70", 24, 2.8, 100, "1/500", "2024-06-19T10:00:00", True),
        ("24-70", 70, 2.8, 400, "1/250", "2024-06-19T11:00:00", False),
        ("24-70", 70, 4.0, 3200, "1/60", "2024-06-20T19:00:00", True),
        ("85", 85, 1.4, 100, "1/1000", "2024-07-04T18:00:00", True),
        ("85", 85, 1.4, 6400, "1/30", "2024-07-04T21:00:00", False),
        ("85", 85, 1.8, 12800, "2", "2025-01-01T23:00:00", False),
    ]
    for n, (lens, fl, ap, iso, sh, when, pick) in enumerate(shots):
        Image.new("RGB", (20, 20)).save(root / f"S{n}.jpg")
    rid = add_root(conn, root)
    scan_root(conn, rid)
    ids = [r[0] for r in conn.execute("SELECT id FROM files ORDER BY filename")]
    for fid, (lens, fl, ap, iso, sh, when, pick) in zip(ids, shots):
        conn.execute("INSERT OR REPLACE INTO exif (file_id, lens, focal_length_mm, aperture, iso, shutter_speed,"
                     " captured_at, camera_model) VALUES (?, ?, ?, ?, ?, ?, ?, 'ILCE-7RM5')",
                     (fid, f"FE {lens}mm", fl, ap, iso, sh, when))
        if pick:
            conn.execute("INSERT OR REPLACE INTO ratings (file_id, flag) VALUES (?, 'pick')", (fid,))
    conn.commit()
    return conn


def table(rows):
    return [(r.bucket, r.photos, r.keepers) for r in rows]


def test_keeper_rate_by_lens_and_exposure(lib):
    assert table(stats.keeper_rate(lib, "lens")) == [("FE 24-70mm", 3, 2), ("FE 85mm", 3, 1)]
    assert table(stats.keeper_rate(lib, "focal")) == [("24 mm", 1, 1), ("70 mm", 2, 1), ("85 mm", 3, 1)]
    assert table(stats.keeper_rate(lib, "aperture")) == [("f/1.4", 2, 1), ("f/1.8", 1, 0), ("f/2.8", 2, 1),
                                                          ("f/4", 1, 1)]
    assert table(stats.keeper_rate(lib, "iso")) == [("ISO 100", 2, 2), ("ISO 400", 1, 0), ("ISO 3,200", 1, 1),
                                                     ("ISO 6,400", 1, 0), ("ISO 12,800", 1, 0)]
    assert [b for b, _, _ in table(stats.keeper_rate(lib, "shutter"))] == [
        "1/1000 s", "1/250 s", "1/60 s", "1/15 s", "1 s or longer"]
    assert round(stats.keeper_rate(lib, "lens")[0].rate, 3) == 0.667


def test_focal_use_months_habits(lib):
    assert table(stats.focal_use(lib, "FE 24-70mm")) == [("24 mm", 1, 1), ("70 mm", 2, 1)]
    assert stats.per_month(lib) == [("2024-06", 3, 2), ("2024-07", 2, 1), ("2025-01", 1, 1)]
    h = stats.habits(lib)
    assert (h.photos, h.keepers, h.days) == (6, 3, 4) and h.camera == "ILCE-7RM5" and h.focal == "85 mm"
    h24 = stats.habits(lib, 2024)
    assert h24.photos == 5 and h24.weekday in stats.WEEKDAYS
    assert stats.years(lib) == [2025, 2024]


def test_the_recap_is_a_picture_of_the_year(lib, tmp_path):
    img = stats.recap_image(lib, 2024)
    assert img.size == (1080, 1350)
    from lunelis.create import engine
    p = engine.save(img, engine.Preset("Recap", format="png"), tmp_path, "Recap 2024")
    assert p.endswith("Recap 2024.png")


def test_the_stats_page_shows_the_figures_and_makes_a_recap(lib, tmp_path):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from lunelis.settings import Settings
    from lunelis.ui.stats_view import StatsView
    Settings(lib).set("create_output_dir", str(tmp_path / "made"))
    page = StatsView(lib)
    page.refresh()
    page.bg.wait()
    values = [page.glance.itemAt(i).widget().text() for i in range(page.glance.count())]
    assert "6" in values and "ILCE-7RM5" in values
    assert [r.bucket for r in page.by_chart.rows] == ["FE 24-70mm", "FE 85mm"]
    page.by.setCurrentIndex(page.by.findData("iso"))
    page.bg.wait()
    assert page.by_chart.rows[0].bucket == "ISO 100"
    assert page.lens.count() == 2 and page.focal_chart.rows
    assert [r.bucket for r in page.month_chart.rows] == ["2024-06", "2024-07", "2025-01"]
    page.year.setCurrentText("2024")
    page.make_recap()
    page.bg.wait()
    assert (tmp_path / "made" / "Lunelis 2024 recap.png").exists()
    page.resize(800, 600)
    page.by_chart.grab()                                      # paints without error
