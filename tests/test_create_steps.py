"""0.44: every Create tool can go step by step; Create & Tools holds Sensor dust."""
from PySide6.QtWidgets import QApplication


def test_every_tool_goes_step_by_step(tmp_path):
    QApplication.instance() or QApplication([])
    from lunelis.catalog.schema import open_catalog
    from lunelis.settings import Settings
    from lunelis.ui.create_page import TOOLS
    conn = open_catalog(tmp_path / "c.db")
    for key, name, _blurb, cls in TOOLS:
        tool = cls(conn)
        tool.steps_cb.setChecked(True)
        assert tool.step == 0 and not tool.picker.isHidden() and tool.body_w.isHidden(), name
        assert tool.make_b.isHidden() and not tool.next_b.isEnabled(), name     # no photos picked yet
        tool.go_step(1)
        assert tool.picker.isHidden() and not tool.body_w.isHidden(), name
        tool.go_step(2)
        assert name in tool.check_view.text() and "at least" in tool.check_view.text(), name
        tool.go_step(3)
        assert not tool.make_b.isHidden() and tool.next_b.isHidden(), name
        tool.steps_cb.setChecked(False)
        assert tool.step == -1 and not tool.picker.isHidden() and not tool.body_w.isHidden(), name
        tool.deleteLater()
    assert Settings(conn).get("create_step_by_step") is False
    conn.close()


def test_create_and_tools_holds_sensor_dust():
    from lunelis.ui.main_window import NAV
    sections = dict(NAV)
    assert sections["Create & Tools"] == ["Create", "Sensor dust"]
    assert "Sensor dust" not in sections["Keep safe"]
