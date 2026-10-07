"""0.38: an MP4 cut off before its index was written is listed as damaged."""
from lunelis.damage.check import missing_moov


def box(kind, body=b""):
    return (8 + len(body)).to_bytes(4, "big") + kind + body


def test_a_clip_with_no_moov_is_found(tmp_path):
    bad = tmp_path / "cut.mp4"
    bad.write_bytes(box(b"ftyp", b"isom") + box(b"mdat", b"\1" * 5000))
    good = tmp_path / "ok.mp4"
    good.write_bytes(box(b"ftyp", b"isom") + box(b"mdat", b"\1" * 5000) + box(b"moov", b"\0" * 20))
    assert missing_moov(str(bad))
    assert not missing_moov(str(good))
