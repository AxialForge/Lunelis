"""0.56.2: S in the photo view opens the burst in the filmstrip, not only in the Library behind it."""
from lunelis import stacks
from test_issues_ui_054 import _shut, _window


def test_s_on_a_burst_cover_puts_its_frames_in_the_filmstrip(tmp_path):
    w, ids, _ = _window(tmp_path, 5)
    try:
        stacks.make_burst(w.conn, ids[:3])
        w.reload()
        cover = next(f for f in ids[:3] if w.index.position(f) >= 0)
        w.open_detail(ids[4])                                  # a plain photo; the burst stays one tile
        # (counted from what is there: the test catalog also holds other tests' photos)
        n = len(w.detail.index)
        assert sum(w.detail.index.position(f) >= 0 for f in ids) == 3     # the burst tile plus two photos
        w.detail.go(w.detail.index.position(cover))            # arrow keys / the filmstrip land on the burst
        assert w.detail.info.file_id == cover
        w.toggle_stack()                                       # S
        assert len(w.detail.index) == n + 2                    # its frames are in the photo view's own list
        assert all(w.detail.index.position(f) >= 0 for f in ids[:3])
        assert w.detail.info.file_id == cover                  # still on the same photo
        assert w.detail.pos == w.detail.index.position(cover)
        assert "3 frames" in w.status.text()
        around = {w.detail.index.file_id(p) for p in (w.detail.pos - 1, w.detail.pos + 1)
                  if 0 <= p < len(w.detail.index)}
        assert around & (set(ids[:3]) - {cover})               # an arrow key now lands on a frame of the burst
        w.toggle_stack()                                       # S again: one tile once more
        assert len(w.detail.index) == n and w.detail.info.file_id == cover
        w.close_detail()
    finally:
        _shut(w)
