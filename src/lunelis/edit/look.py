"""
Learn My Look: a small model of how you edit, trained on this PC from the
edits you've already made, that suggests a starting edit in your style.

- **What it looks at:** each photo as shot (its original, not the edit),
  boiled down to a few numbers - how bright, how contrasty, where the
  shadows and highlights sit, how much is clipped, the colour cast and how
  colourful it is.
- **What it learns:** for each Light and Color slider you actually use
  (in at least a quarter of your edits), how you set it given those numbers -
  a ridge regression per slider, plain numpy. A slider you rarely touch is
  left alone. Filters count as the adjustments they make.
- **When:** only with MIN_EDITS edits or more ("too few: no suggestion").
  The model lives in the data folder (look_model.json) and is retrained when
  your edits have grown by a tenth.
- **Never applied by itself:** `suggest()` returns adjustments; the Edit
  panel shows them and only Apply puts them on the photo (undoable).
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np

from lunelis.edit.stack import BY_KEY, clamp

LEARNED = ("exposure", "contrast", "highlights", "shadows", "whites", "blacks",
           "temp", "tint", "vibrance", "saturation")
MIN_EDITS = 15
MIN_USE = 0.25              # a slider used in fewer edits than this isn't suggested
RIDGE = 1.0                 # regularisation, per feature
FEATURE_EDGE = 384
MODEL_FILE = "look_model.json"
LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)


def features(rgb: np.ndarray) -> np.ndarray:
    """A photo as shot -> 10 numbers. rgb: (h, w, 3) floats 0-1 (or uint8)."""
    a = np.asarray(rgb)
    if a.dtype == np.uint8:
        a = a.astype(np.float32) / 255.0
    step = max(1, max(a.shape[:2]) // 256)
    a = a[::step, ::step, :3].astype(np.float32)
    y = a @ LUMA
    p5, p50, p95 = np.percentile(y, (5, 50, 95))
    mx, mn = a.max(axis=2), a.min(axis=2)
    mean = a.reshape(-1, 3).mean(axis=0)
    return np.array([
        float(y.mean()), float(y.std()), float(p5), float(p50), float(p95),
        float((y > 0.97).mean()), float((y < 0.03).mean()),
        float(mean[0] - mean[1]), float(mean[2] - mean[1]),
        float((mx - mn).mean()),
    ], np.float32)


def features_of_file(path: str, orientation: int | None = None) -> np.ndarray:
    from lunelis.raw.thumbnails import render
    img = render(path, orientation, edge=FEATURE_EDGE)
    return features(np.asarray(img.convert("RGB")))


@dataclass
class Model:
    n: int = 0                                     # edits learned from
    mu: list = field(default_factory=list)          # feature means / spreads (standardising)
    sd: list = field(default_factory=list)
    sliders: dict = field(default_factory=dict)     # key -> {"mean": .., "w": [..], "use": ..}
    made_at: str = ""

    def predict(self, x: np.ndarray) -> dict:
        z = (np.asarray(x, np.float64) - np.asarray(self.mu)) / np.asarray(self.sd)
        out = {}
        for key, s in self.sliders.items():
            v = s["mean"] + float(z @ np.asarray(s["w"]))
            p = BY_KEY[key]
            v = clamp(key, round(v / p.step) * p.step)
            if abs(v) >= p.step * (1 if p.step < 1 else 2):
                out[key] = round(float(v), 2)
        return out

    def save(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / MODEL_FILE).write_text(json.dumps(self.__dict__), encoding="utf-8")

    @classmethod
    def load(cls, folder: Path) -> "Model | None":
        try:
            return cls(**json.loads((folder / MODEL_FILE).read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return None


def fit(X: np.ndarray, targets: dict[str, np.ndarray]) -> Model:
    X = np.asarray(X, np.float64)
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd[sd < 1e-6] = 1.0
    Z = (X - mu) / sd
    n, k = Z.shape
    model = Model(n=n, mu=mu.tolist(), sd=sd.tolist(), made_at=datetime.now().isoformat(timespec="seconds"))
    gram = Z.T @ Z + RIDGE * np.eye(k)
    for key, y in targets.items():
        y = np.asarray(y, np.float64)
        use = float((np.abs(y) > 1e-9).mean())
        if use < MIN_USE:
            continue
        mean = float(y.mean())
        w = np.linalg.solve(gram, Z.T @ (y - mean))
        model.sliders[key] = {"mean": mean, "w": w.tolist(), "use": round(use, 3)}
    return model


# --- the catalog side ---------------------------------------------------------------------------

def edited(conn: sqlite3.Connection) -> list[tuple[int, str, int | None, dict]]:
    """(file id, path, orientation, the edit's adjustments incl. its filter) for photos with
    Light / Color edits."""
    from pathlib import Path as P
    from lunelis.edit import store
    from lunelis.edit.stack import effective, loads
    out = []
    for fid, text, root, rel, orient in conn.execute(
            "SELECT ed.file_id, ed.stack, r.path, f.rel_path, e.orientation FROM edits ed"
            " JOIN files f ON f.id = ed.file_id JOIN roots r ON r.id = f.root_id"
            " LEFT JOIN exif e ON e.file_id = f.id WHERE f.missing_since IS NULL AND f.quarantined_at IS NULL"):
        st = loads(text)
        adj = effective(st, store.filter_params(conn, st.filter))
        adj = {k: v for k, v in adj.items() if k in LEARNED and v}
        if adj:
            out.append((fid, str(P(root, *rel.split("/"))), orient, adj))
    return out


def stale(conn: sqlite3.Connection, folder: Path) -> bool:
    m = Model.load(folder)
    n = len(edited(conn))
    if n < MIN_EDITS:
        return False
    return m is None or abs(n - m.n) >= max(3, m.n // 10)


def train(conn: sqlite3.Connection, folder: Path, progress: Callable[[int, int], None] | None = None,
          stop: Callable[[], bool] | None = None) -> Model | None:
    """Learn from your edits (None when there are too few)."""
    rows = edited(conn)
    if len(rows) < MIN_EDITS:
        return None
    X, kept = [], []
    for i, (fid, path, orient, adj) in enumerate(rows):
        if stop and stop():
            return None
        if progress:
            progress(i, len(rows))
        try:
            X.append(features_of_file(path, orient))
            kept.append(adj)
        except Exception:
            continue                          # an unreadable original just isn't learned from
    if len(kept) < MIN_EDITS:
        return None
    targets = {k: np.array([a.get(k, 0.0) for a in kept]) for k in LEARNED}
    model = fit(np.array(X), targets)
    model.save(folder)
    return model


def describe(adjust: dict) -> str:
    parts = []
    for key in LEARNED:
        if key in adjust:
            v = adjust[key]
            parts.append(f"{BY_KEY[key].label} {'+' if v > 0 else ''}{v:g}")
    return ", ".join(parts)
