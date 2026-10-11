"""0.55.1: copies landed hidden on a NAS (the dot-named temp file's mark outlived the rename)."""
import ctypes
import os
import sys

import pytest

from lunelis import paths

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows file attributes")


def _hide(p):
    ctypes.windll.kernel32.SetFileAttributesW(str(p), os.stat(p).st_file_attributes | paths.HIDDEN)


def _hidden(p) -> bool:
    return bool(os.stat(p).st_file_attributes & paths.HIDDEN)


def test_unhide_clears_the_mark_but_respects_a_hidden_original(tmp_path):
    src, dst = tmp_path / "a.jpg", tmp_path / "b.jpg"
    src.write_bytes(b"x")
    dst.write_bytes(b"x")
    assert paths.unhide(dst) is False                     # nothing to clear
    _hide(dst)
    assert paths.unhide(dst, like=src) is True and not _hidden(dst)
    _hide(dst)
    _hide(src)
    assert paths.unhide(dst, like=src) is False and _hidden(dst)
    assert paths.unhide(tmp_path / "missing.jpg") is False


def test_both_copiers_unhide_what_they_wrote(tmp_path, monkeypatch):
    from lunelis.importing import ingest
    from lunelis.jobs.engine import Throttle
    from lunelis.migrate import execute
    src = tmp_path / "a.jpg"
    src.write_bytes(b"picture")
    real = os.replace

    def replace_like_a_nas(a, b):                         # the share marks the dot-file; the mark survives
        _hide(a)
        real(a, b)
    monkeypatch.setattr(os, "replace", replace_like_a_nas)
    assert execute._copy_hashed(str(src), str(tmp_path / "m" / "a.jpg"), Throttle(0), None)
    assert ingest._copy_hashed(str(src), str(tmp_path / "i" / "a.jpg"))
    assert not _hidden(tmp_path / "m" / "a.jpg") and not _hidden(tmp_path / "i" / "a.jpg")
