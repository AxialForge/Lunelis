"""AI masks: download checks, computing + caching, geometry, the pipeline."""
import hashlib

import numpy as np
import pytest

from lunelis.edit import ai, masks as M
from lunelis.edit import pipeline as P
from lunelis.edit.stack import Geometry, Stack


class FakeSession:
    """Stands in for the ONNX model: 'finds' the left half of the picture."""

    def get_inputs(self):
        return [type("I", (), {"name": "input.1"})()]

    def run(self, _outputs, feeds):
        x = feeds["input.1"]
        assert x.shape == (1, 3, 320, 320) and x.dtype == np.float32
        y = np.zeros((1, 1, 320, 320), np.float32)
        y[..., :160] = 0.97
        return [y]


@pytest.fixture
def fake_model(monkeypatch):
    monkeypatch.setitem(ai._SESSIONS, "subject", FakeSession())
    monkeypatch.setattr(ai, "available", lambda kind: True)


def photo(h=300, w=450):
    a = np.full((h, w, 3), 0.4, np.float32)
    a[:, : w // 2] = 0.7                                          # the "subject" is brighter: a real edge
    return a


def test_compute_follows_the_edge(fake_model):
    m = ai.compute("subject", photo())
    assert m.shape == (300, 450)
    assert m[150, 50] > 0.9 and m[150, 400] < 0.1


def test_maps_are_cached(fake_model, tmp_path, monkeypatch):
    monkeypatch.setattr(ai, "masks_dir", lambda: tmp_path / "masks")
    s = Stack(masks=(M.Mask("subject", adjust={"exposure": 1}),))
    maps = ai.maps_for(7, s, photo())
    assert set(maps) == {"subject"} and ai.cache_path(7, "subject").exists()
    monkeypatch.setitem(ai._SESSIONS, "subject", None)            # the cache is used from now on
    assert np.allclose(ai.maps_for(7, s)["subject"], maps["subject"], atol=1 / 255)
    assert ai.maps_for(8, s) == {}                                # not computed, no source: no effect


def test_ai_mask_in_the_pipeline_turns_with_the_photo(fake_model):
    a = photo(200, 300)
    mask = ai.compute("subject", a)
    s = Stack(masks=(M.Mask("subject", adjust={"exposure": -2}),))
    out = P.apply(a, s, ai_maps={"subject": mask})
    assert out[100, 30, 0] < a[100, 30, 0] - 0.2 and out[100, 270, 0] == pytest.approx(0.4, abs=0.01)
    turned = P.apply(a, Stack(masks=s.masks, geometry=Geometry(rotate=90)), ai_maps={"subject": mask})
    assert turned.shape == (300, 200, 3)
    assert turned[30, 100, 0] < 0.5 and turned[270, 100, 0] == pytest.approx(0.4, abs=0.01)   # left went to top
    tiled = P.apply_tiled(a, s, ai_maps={"subject": mask}, rows=64)
    assert np.abs(tiled.astype(int) - (out * 255 + 0.5).astype(np.uint8).astype(int)).max() <= 1
    # No map (model not installed): the mask simply has no effect.
    assert np.allclose(P.apply(a, s), a, atol=1e-6)


def test_download_checks_the_checksum(tmp_path, monkeypatch):
    src = tmp_path / "model.onnx"
    src.write_bytes(b"x" * 5000)
    good = ai.Model("subject", "m.onnx", src.as_uri(), hashlib.sha256(src.read_bytes()).hexdigest(), 5000, "test")
    monkeypatch.setitem(ai.MODELS, "subject", good)
    monkeypatch.setattr(ai, "models_dir", lambda: tmp_path / "models")
    seen = []
    ai.download("subject", lambda d, t: seen.append(d))
    assert ai.available("subject") and seen[-1] == 5000
    bad = ai.Model("sky", "s.onnx", src.as_uri(), "0" * 64, 5000, "test")
    monkeypatch.setitem(ai.MODELS, "sky", bad)
    with pytest.raises(RuntimeError, match="checksum"):
        ai.download("sky")
    assert not (tmp_path / "models" / "s.onnx").exists() and not ai.available("sky")
