"""v0.19: smart albums - saved rules that pick photos and stay current."""
import json

import pytest
from PIL import Image

from lunelis.albums import smart
from lunelis.catalog.schema import open_catalog
from lunelis.importers.scan import add_root, scan_root
from lunelis.ui.library import Filter, LibraryIndex


@pytest.fixture
def lib(tmp_path):
    conn = open_catalog(tmp_path / "c.db")
    root = tmp_path / "Photos"
    root.mkdir()
    for i in range(6):
        Image.new("RGB", (40, 30), (40 * i, 80, 120)).save(root / f"P{i}.jpg")
    rid = add_root(conn, root)
    scan_root(conn, rid)
    ids = [r[0] for r in conn.execute("SELECT id FROM files ORDER BY filename")]
    exif = [  # iso, lens, model, aperture, focal, date, shutter
        (6400, "FE 24-70mm F2.8 GM II", "ILCE-7RM5", 2.8, 35, "2024-06-19T10:00:00", "1/250"),
        (3200, "FE 24-70mm F2.8 GM II", "ILCE-7RM5", 4.0, 50, "2024-07-01T10:00:00", "1/60"),
        (100, "FE 85mm F1.4 GM", "ILCE-7RM5", 1.4, 85, "2025-01-01T10:00:00", "1/1000"),
        (12800, "FE 85mm F1.4 GM", "ILCE-7M4", 1.4, 85, "2025-02-01T10:00:00", "2s"),
        (400, None, "iPhone 15 Pro", 1.8, 6, "2026-01-01T10:00:00", "1/120"),
        (6400, "FE 24-70mm F2.8 GM II", "ILCE-7M4", 8.0, 24, "2026-03-01T10:00:00", "1/30"),
    ]
    for fid, (iso, lens, model, ap, fl, when, sh) in zip(ids, exif):
        conn.execute("INSERT OR REPLACE INTO exif (file_id, iso, lens, camera_model, aperture, focal_length_mm,"
                     " captured_at, shutter_speed) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (fid, iso, lens, model, ap, fl, when, sh))
    stars = [5, 5, 3, 5, 0, 2]
    for fid, s in zip(ids, stars):
        conn.execute("INSERT OR REPLACE INTO ratings (file_id, stars, flag) VALUES (?, ?, ?)",
                     (fid, s, "pick" if s == 5 else None))
    conn.commit()
    return conn, ids


def found(conn, rules, match="all"):
    idx = LibraryIndex()
    idx.load(conn, "name", Filter(smart=json.dumps({"match": match, "rules": rules})))
    return sorted(idx.file_id(i) for i in range(len(idx)))


def test_the_example_rule_finds_exactly_those_photos(lib):
    conn, ids = lib
    rules = [{"field": "iso", "op": ">", "value": 3200}, {"field": "stars", "op": "=", "value": 5},
             {"field": "lens", "op": "contains", "value": "24-70"}]
    assert found(conn, rules) == [ids[0]]


def test_any_numbers_text_dates_kinds_and_flags(lib):
    conn, ids = lib
    assert found(conn, [{"field": "camera", "op": "is", "value": "ilce-7m4"},
                        {"field": "aperture", "op": "<=", "value": 1.8}], match="any") == sorted([ids[2], ids[3], ids[4], ids[5]])
    assert found(conn, [{"field": "date", "op": "on or after", "value": "2025-01-01"},
                        {"field": "date", "op": "on or before", "value": "2025-12-31"}]) == [ids[2], ids[3]]
    assert found(conn, [{"field": "flag", "op": "is", "value": "pick"}]) == [ids[0], ids[1], ids[3]]
    assert found(conn, [{"field": "shutter_s", "op": ">=", "value": 1}]) == [ids[3]]
    assert found(conn, [{"field": "lens", "op": "is not", "value": "FE 85mm F1.4 GM"},
                        {"field": "focal", "op": ">=", "value": 24}]) == [ids[0], ids[1], ids[5]]
    assert len(found(conn, [{"field": "kind", "op": "is", "value": "photo"}])) == 6


def test_a_smart_album_stays_current(lib):
    conn, ids = lib
    aid = smart.create(conn, "Five stars", {"match": "all", "rules": [{"field": "stars", "op": "=", "value": 5}]})
    def count():
        return next(a.count for a in smart.smart_albums(conn) if a.key == str(aid))
    assert count() == 3
    conn.execute("UPDATE ratings SET stars = 5 WHERE file_id = ?", (ids[2],))
    conn.commit()
    assert count() == 4                                     # nothing re-saved: the rule finds it
    smart.update(conn, aid, {"match": "all", "rules": [{"field": "stars", "op": ">=", "value": 3}]}, name="Good")
    album = next(a for a in smart.smart_albums(conn) if a.key == str(aid))
    assert album.name == "Good" and album.count == 4 and album.blurb == "Stars >= 3"


def test_smart_albums_are_not_your_albums_and_bad_rules_are_refused(lib):
    conn, ids = lib
    from lunelis.albums import model
    smart.create(conn, "Night", {"match": "all", "rules": [{"field": "iso", "op": ">=", "value": 6400}]})
    assert [a.name for a in model.your_albums(conn)] == []
    for bad in ({"rules": []}, {"rules": [{"field": "nope", "op": "=", "value": 1}]},
                {"rules": [{"field": "iso", "op": "contains", "value": 1}]},
                {"rules": [{"field": "iso", "op": ">=", "value": "lots"}]},
                {"rules": [{"field": "date", "op": "is", "value": "June"}]},
                {"match": "some", "rules": [{"field": "iso", "op": ">=", "value": 1}]}):
        with pytest.raises(smart.RuleError):
            smart.create(conn, "Bad", bad)


def test_the_rule_editor_builds_and_checks_rules():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from lunelis.ui.smart_dialog import SmartAlbumDialog
    dlg = SmartAlbumDialog()
    row = dlg.rows[0]
    row.field.setCurrentIndex(row.field.findData("iso"))
    row.op.setCurrentIndex(row.op.findText(">="))
    row.num.setValue(3200)
    lens = dlg.add_row({"field": "lens", "op": "contains", "value": "24-70"})
    assert lens.text.text() == "24-70" and lens.text.isVisibleTo(dlg) is not None
    dlg._accept()
    assert dlg.result_value is None and "name" in dlg.error.text()          # needs a name
    dlg.name.setText("Night lens")
    dlg._accept()
    name, rules = dlg.result_value
    assert name == "Night lens" and rules == {"match": "all", "rules": [
        {"field": "iso", "op": ">=", "value": 3200.0}, {"field": "lens", "op": "contains", "value": "24-70"}]}
