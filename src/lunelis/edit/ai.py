"""
AI masks - Subject and Sky - from small free models run on this PC.

- Subject: "silueta" (a compact U^2-Net, 44 MB) from the rembg project's
  GitHub releases.
- Sky:     "skyseg" (a U^2-Net trained for sky, 176 MB) from Hugging Face.

Nothing is downloaded until you first add that kind of mask, and only
after you say yes; the file's SHA-256 is checked before it's used. After
that everything runs offline (onnxruntime, CPU).

A model sees the photo at 320 x 320; its answer is refined against the
photo itself (a guided filter) at up to 1024 px, so edges follow hair and
skylines. The result is cached per photo (cache/masks/), in the photo's
own orientation - pipeline.apply turns it with the photo's geometry.
Raw model output is used through a soft threshold: normalizing it (as
rembg does) turned noise into a "sky" on photos that have none.
"""
from __future__ import annotations

import hashlib
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

MASK_EDGE = 1024


@dataclass(frozen=True)
class Model:
    kind: str
    file: str
    url: str
    sha256: str
    size: int
    source: str


MODELS = {
    "subject": Model("subject", "silueta.onnx",
                     "https://github.com/danielgatis/rembg/releases/download/v0.0.0/silueta.onnx",
                     "75da6c8d2f8096ec743d071951be73b4a8bc7b3e51d9a6625d63644f90ffeedb", 44_173_029,
                     "rembg (GitHub)"),
    "sky": Model("sky", "skyseg.onnx",
                 "https://huggingface.co/JianyuanWang/skyseg/resolve/main/skyseg.onnx",
                 "ab9c34c64c3d821220a2886a4a06da4642ffa14d5b30e8d5339056a089aa1d39", 175_997_079,
                 "Hugging Face"),
}


def models_dir() -> Path:
    from lunelis import paths
    return paths.DATA_DIR / "models"


def masks_dir() -> Path:
    from lunelis import paths
    return paths.DATA_DIR / "cache" / "masks"


def model_path(kind: str) -> Path:
    return models_dir() / MODELS[kind].file


def available(kind: str) -> bool:
    p = model_path(kind)
    return p.exists() and p.stat().st_size == MODELS[kind].size


def download(kind: str, on_progress: Callable[[int, int], None] | None = None,
             should_cancel: Callable[[], bool] | None = None) -> Path:
    """Fetch a model, check its SHA-256, then move it into place."""
    m = MODELS[kind]
    dest = model_path(kind)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    h = hashlib.sha256()
    done = 0
    req = urllib.request.Request(m.url, headers={"User-Agent": "Lunelis"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as out:
        while True:
            if should_cancel and should_cancel():
                out.close()
                tmp.unlink(missing_ok=True)
                raise RuntimeError("cancelled")
            chunk = r.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if on_progress:
                on_progress(done, m.size)
    if h.hexdigest() != m.sha256:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("the download didn't match its checksum - nothing was installed")
    os.replace(tmp, dest)
    return dest


_SESSIONS: dict = {}


def _session(kind: str):
    s = _SESSIONS.get(kind)
    if s is None:
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        s = ort.InferenceSession(str(model_path(kind)), opts, providers=["CPUExecutionProvider"])
        _SESSIONS[kind] = s
    return s


def _guided(I: np.ndarray, p: np.ndarray, r: int, eps: float) -> np.ndarray:
    """He et al.'s guided filter: p's edges pulled onto the guide image I."""
    def box(a):
        # Mean over a (2r+1)^2 window, edges clamped - summed-area table.
        p = np.pad(a.astype(np.float64), ((r + 1, r), (r + 1, r)), mode="edge")
        c = p.cumsum(0).cumsum(1)
        k = 2 * r + 1
        s = c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]
        return (s / (k * k)).astype(np.float32)
    mI, mp = box(I), box(p)
    a = (box(I * p) - mI * mp) / (box(I * I) - mI * mI + eps)
    b = mp - a * mI
    return box(a) * I + box(b)


def compute(kind: str, source: np.ndarray) -> np.ndarray:
    """The mask (0..1, float32, at most MASK_EDGE px) for an upright
    float32 sRGB photo."""
    from lunelis.edit.pipeline import LUMA
    from lunelis.edit.render import _resize
    small = _resize(source, 320)
    x = np.asarray(Image.fromarray((np.clip(small, 0, 1) * 255).astype(np.uint8)).resize((320, 320),
                   Image.Resampling.BILINEAR), dtype=np.float32) / 255
    x = ((x - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32))
    sess = _session(kind)
    y = sess.run(None, {sess.get_inputs()[0].name: x.transpose(2, 0, 1)[None].astype(np.float32)})[0][0, 0]
    t = np.clip((y - 0.2) / 0.6, 0, 1)
    y = (t * t * (3 - 2 * t)).astype(np.float32)          # soft threshold
    guide = _resize(source, MASK_EDGE)
    gh, gw = guide.shape[:2]
    up = np.asarray(Image.fromarray(y, "F").resize((gw, gh), Image.Resampling.BILINEAR), dtype=np.float32)
    I = np.ascontiguousarray(guide @ LUMA, dtype=np.float32)
    refined = _guided(I, up, max(2, gw // 160), 1e-3)
    return np.clip(refined, 0, 1).astype(np.float32)


def cache_path(file_id: int, kind: str) -> Path:
    return masks_dir() / f"{file_id // 1000:04d}" / f"{file_id}-{kind}.png"


def cached(file_id: int, kind: str) -> np.ndarray | None:
    p = cache_path(file_id, kind)
    if not p.exists():
        return None
    try:
        with Image.open(p) as im:
            return np.asarray(im.convert("L"), dtype=np.float32) / 255
    except OSError:
        return None


def store(file_id: int, kind: str, mask: np.ndarray) -> None:
    p = cache_path(file_id, kind)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    Image.fromarray((np.clip(mask, 0, 1) * 255 + 0.5).astype(np.uint8), "L").save(tmp, "PNG")
    os.replace(tmp, p)


def kinds_in(stack) -> set[str]:
    return {m.kind for m in stack.masks if m.kind in MODELS}


def maps_for(file_id: int, stack, source: np.ndarray | None = None) -> dict:
    """The AI masks a stack uses, from the cache - or computed from `source`
    (and cached) when missing and the model is installed. A mask that can't
    be had is left out: it then has no effect, never a wrong one."""
    out = {}
    for kind in kinds_in(stack):
        m = cached(file_id, kind)
        if m is None and source is not None and available(kind):
            m = compute(kind, source)
            store(file_id, kind, m)
        if m is not None:
            out[kind] = m
    return out
