"""0.38: a 24-hour clock setting, and a photo's location opening Lunelis's own Map."""
from datetime import datetime

from lunelis.ui import photoinfo


def test_the_clock_setting_changes_every_time_shown():
    t = datetime(2026, 6, 19, 14, 3)
    try:
        photoinfo.set_date_format("long")
        assert photoinfo.format_date(t).endswith("2:03 PM")
        photoinfo.set_clock_24h(True)
        assert photoinfo.format_date(t).endswith("14:03")
        assert photoinfo.clock(t) == "14:03"
    finally:
        photoinfo.set_clock_24h(False)


def test_centre_on_puts_the_spot_in_the_middle(tmp_path):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from lunelis.ui.map_view import MapCanvas, lonlat_to_world
    c = MapCanvas(tmp_path)
    c.centre_on(48.85, 2.35)
    assert c.z == 15 and (c.cx, c.cy) == lonlat_to_world(2.35, 48.85, 15)
