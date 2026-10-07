"""0.38: a photo that can't be opened says why."""
from lunelis.ui.detail_view import _damage_reason


def test_a_zero_filled_photo_is_called_damaged(tmp_path):
    p = tmp_path / "x.jpg"
    p.write_bytes(b"\0" * 10000)
    assert "all zeros" in _damage_reason(str(p), OSError("cannot identify image file"))
    e = tmp_path / "e.jpg"
    e.write_bytes(b"")
    assert "empty" in _damage_reason(str(e), OSError("x"))
    assert "isn't there" in _damage_reason(str(tmp_path / "gone.jpg"), OSError("x"))
