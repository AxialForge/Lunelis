"""
Animal faces, sorted out by the scene model (0.52).

The face finder (YuNet) is made for people, but a dog's or a cat's face is
close enough that it finds those too. When the scene model (CLIP, clip.py)
is on this PC, each new face crop is compared with a few short descriptions
- people on one side, animals on the other - and a face that is clearly an
animal is marked as one (faces.mark_animal). Without the scene model nothing
happens; animals can always be marked by hand, and a wrong call undone with
"Not an animal".

Only unnamed faces are looked at: a face you named stays what you said.
"""
from __future__ import annotations

import sqlite3
from functools import lru_cache

import numpy as np

PEOPLE = ("a photo of a person's face", "a close-up of a human face", "a portrait of a person",
          "a baby's face", "a face in a crowd of people")
ANIMALS = ("a photo of a dog", "a photo of a cat", "a close-up of a dog's face", "a close-up of a cat's face",
           "a photo of a horse", "a photo of a bird", "a photo of a pet", "a photo of a wild animal")
SURE = 0.90              # the animal side's share before a face is marked (0.80 caught people in costume)
TEMPERATURE = 100.0      # CLIP's own logit scale
BATCH = 32


@lru_cache(maxsize=1)
def _texts() -> np.ndarray:
    from lunelis.recognize.clip import ClipBackend
    return ClipBackend().embed_texts(list(PEOPLE + ANIMALS))


def animal_share(images: list) -> np.ndarray:
    """For each face crop (PIL image), how sure the model is it's an animal (0-1)."""
    from lunelis.recognize.clip import ClipBackend
    if not images:
        return np.zeros(0, np.float32)
    v = ClipBackend().embed_images(images)
    logits = TEMPERATURE * (v @ _texts().T)
    logits -= logits.max(axis=1, keepdims=True)
    p = np.exp(logits)
    p /= p.sum(axis=1, keepdims=True)
    return p[:, len(PEOPLE):].sum(axis=1)


def available() -> bool:
    try:
        from lunelis.recognize import clip
        return clip.available()
    except Exception:                               # noqa: BLE001 - no model, no sorting
        return False


def sort_out(conn: sqlite3.Connection, face_ids: list[int] | None = None, should_cancel=None) -> int:
    """Mark the animal faces among these unnamed faces (every unnamed face when
    None). Returns how many were marked. Does nothing without the scene model."""
    if not available():
        return 0
    from PIL import Image
    from lunelis.recognize import faces
    sql = "SELECT id FROM faces WHERE confirmed = 0 AND ignored = 0"
    if face_ids is None:
        ids = [r[0] for r in conn.execute(sql)]
    else:
        want = set(face_ids)
        ids = [r[0] for r in conn.execute(sql) if r[0] in want]
    marked = 0
    for i in range(0, len(ids), BATCH):
        if should_cancel and should_cancel():
            break
        chunk, imgs = [], []
        for fid in ids[i:i + BATCH]:
            try:
                with Image.open(faces.crop_path(fid)) as im:
                    imgs.append(im.convert("RGB"))
                chunk.append(fid)
            except OSError:                         # no crop cached: left for the next run
                continue
        if not chunk:
            continue
        share = animal_share(imgs)
        hits = [fid for fid, s in zip(chunk, share) if s >= SURE]
        if hits:
            marked += faces.mark_animal(conn, hits)
    return marked
