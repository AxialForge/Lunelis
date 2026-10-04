"""
Scene tags: a local model suggests what's in each photo (Scene > Beach,
Scene > Food...), and you accept or reject the suggestions.

- **Reads the cached 512 px thumbnails** - never a RAW, never the NAS.
- **One embedding per photo** (embeddings table, per model): made once, so
  a changed label list only re-scores, it doesn't re-read photos. The same
  vectors serve "find similar" later (0.21).
- **Suggestions** are file_tags rows with a confidence (locked format,
  docs/Schemas.md §2): kept out of the normal tag lists, the Tag filter and
  XMP until accepted. Accepting sets confidence NULL (a user tag); rejecting
  deletes the row and remembers it (tag_rejections) so it isn't suggested
  again. A tag you gave yourself is never touched.
- **Scoring:** each photo against every label prompt, softmax with CLIP's
  temperature (x100); labels at or above MIN_CONFIDENCE suggested, at most
  MAX_PER_PHOTO, best first.
- **The job** (jobs engine kind "scene_tags") works folder by folder:
  resumable, pausable, idle-only if you like, and incremental - a second run
  only does photos without an embedding.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = "Scene"
MIN_CONFIDENCE = 0.25
MAX_PER_PHOTO = 3
BATCH = 16
BUILTIN = Path(__file__).with_name("scene_labels.json")
USER_FILE = "scene_labels.json"
LIVE = "f.missing_since IS NULL AND f.excluded = 0 AND f.quarantined_at IS NULL"


@dataclass(frozen=True)
class Label:
    name: str
    prompt: str

    @property
    def tag(self) -> str:
        from lunelis.tags.model import SEP
        return f"{ROOT}{SEP}{self.name}"


def labels(data_dir: str | Path | None = None) -> list[Label]:
    found = {d["name"]: Label(d["name"], d["prompt"])
             for d in json.loads(BUILTIN.read_text(encoding="utf-8"))["labels"]}
    if data_dir is not None and (Path(data_dir) / USER_FILE).is_file():
        try:
            extra = json.loads((Path(data_dir) / USER_FILE).read_text(encoding="utf-8")).get("labels", [])
        except (OSError, ValueError):
            extra = []                               # a broken file never stops tagging
        for d in extra:
            if not isinstance(d, dict) or not d.get("name"):
                continue
            if d.get("off"):
                found.pop(d["name"], None)
            elif d.get("prompt"):
                found[d["name"]] = Label(d["name"], d["prompt"])
    return list(found.values())


# --- the backend -----------------------------------------------------------------------------

def backend():
    """The installed scene model, or None (not downloaded / turned on)."""
    from lunelis.recognize import clip
    return clip.ClipBackend() if clip.available() else None


_text_cache: dict[tuple, np.ndarray] = {}


def label_vectors(rec, labs: list[Label]) -> np.ndarray:
    key = (rec.model_id, tuple(lab.prompt for lab in labs))
    if key not in _text_cache:
        _text_cache[key] = rec.embed_texts([lab.prompt for lab in labs])
    return _text_cache[key]


def score(image_vecs: np.ndarray, label_vecs: np.ndarray) -> np.ndarray:
    """Softmax over labels (CLIP's x100 temperature): rows = photos."""
    logits = 100.0 * image_vecs @ label_vecs.T
    logits -= logits.max(axis=1, keepdims=True)
    p = np.exp(logits)
    return p / p.sum(axis=1, keepdims=True)


def pick(probs: np.ndarray, labs: list[Label], min_conf: float = MIN_CONFIDENCE,
         most: int = MAX_PER_PHOTO) -> list[list[tuple[Label, float]]]:
    out = []
    for row in probs:
        order = np.argsort(-row)[:most]
        out.append([(labs[i], float(row[i])) for i in order if row[i] >= min_conf])
    return out


# --- storing -------------------------------------------------------------------------------------

def store_embeddings(conn: sqlite3.Connection, model: str, vecs: dict[int, np.ndarray]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO embeddings (file_id, model, dim, vector, made_at) VALUES (?, ?, ?, ?, datetime('now'))",
        [(fid, model, int(v.shape[0]), v.astype(np.float32).tobytes()) for fid, v in vecs.items()])


def embeddings(conn: sqlite3.Connection, model: str, file_ids=None) -> tuple[list[int], np.ndarray]:
    if file_ids is None:
        rows = conn.execute("SELECT file_id, vector FROM embeddings WHERE model = ?", (model,)).fetchall()
    else:
        ids = list(file_ids)
        rows = []
        for start in range(0, len(ids), 900):
            chunk = ids[start:start + 900]
            rows += conn.execute(f"SELECT file_id, vector FROM embeddings WHERE model = ? AND file_id IN"
                                 f" ({','.join('?' * len(chunk))})", (model, *chunk)).fetchall()
    if not rows:
        return [], np.zeros((0, 0), np.float32)
    return [r[0] for r in rows], np.stack([np.frombuffer(r[1], dtype=np.float32) for r in rows])


def suggest(conn: sqlite3.Connection, picks: dict[int, list[tuple[Label, float]]]) -> int:
    """Store suggestions; never over a user tag, never a rejected one. Returns how many."""
    from lunelis.tags.model import tag_id
    made = 0
    for fid, found in picks.items():
        for lab, conf in found:
            tid = tag_id(conn, lab.tag)
            if conn.execute("SELECT 1 FROM tag_rejections WHERE file_id = ? AND tag_id = ?", (fid, tid)).fetchone():
                continue
            before = conn.total_changes
            # An existing row (yours, or an earlier suggestion) is left as it is.
            conn.execute("INSERT OR IGNORE INTO file_tags (file_id, tag_id, confidence) VALUES (?, ?, ?)",
                         (fid, tid, round(conf, 4)))
            made += conn.total_changes - before
    return made


def rescore(conn: sqlite3.Connection, rec=None, data_dir=None) -> int:
    """Suggest again from the stored embeddings (after the labels changed)."""
    rec = rec or backend()
    if rec is None:
        return 0
    ids, vecs = embeddings(conn, rec.model_id)
    if not ids:
        return 0
    labs = labels(data_dir)
    picks = pick(score(vecs, label_vectors(rec, labs)), labs)
    n = suggest(conn, dict(zip(ids, picks)))
    conn.commit()
    return n


def thumbnail_image(conn: sqlite3.Connection, file_id: int):
    from PIL import Image
    from lunelis import paths
    from lunelis.raw.thumbnails import cache_rel_path
    row = conn.execute("SELECT thumbnail_path FROM files WHERE id = ?", (file_id,)).fetchone()
    p = paths.THUMBNAIL_CACHE / ((row[0] if row else None) or cache_rel_path(file_id))
    with Image.open(p) as im:
        return im.convert("RGB")


def tag_files(conn: sqlite3.Connection, file_ids: list[int], rec=None, data_dir=None,
              should_cancel=None) -> tuple[int, int]:
    """Embed (from thumbnails) and score these photos. (embedded, suggestions)."""
    rec = rec or backend()
    if rec is None:
        raise RuntimeError("the scene model isn't installed (Settings > AI)")
    labs = labels(data_dir)
    lv = label_vectors(rec, labs)
    embedded = suggested = 0
    for start in range(0, len(file_ids), BATCH):
        if should_cancel and should_cancel():
            break
        chunk = file_ids[start:start + BATCH]
        images, ok = [], []
        for fid in chunk:
            try:
                images.append(thumbnail_image(conn, fid))
                ok.append(fid)
            except OSError:
                continue                              # no thumbnail yet: the next run picks it up
        if not ok:
            continue
        vecs = rec.embed_images(images)
        store_embeddings(conn, rec.model_id, dict(zip(ok, vecs)))
        suggested += suggest(conn, dict(zip(ok, pick(score(vecs, lv), labs))))
        embedded += len(ok)
        conn.commit()
    return embedded, suggested


def pending_in_folder(conn: sqlite3.Connection, root_id: int, folder: str, model: str) -> list[int]:
    from lunelis.dupes import detect
    return [fid for fid, rel in conn.execute(
        f"SELECT f.id, f.rel_path FROM files f LEFT JOIN embeddings em ON em.file_id = f.id AND em.model = ?"
        f" WHERE f.root_id = ? AND {LIVE} AND em.file_id IS NULL AND f.thumbnail_path IS NOT NULL"
        f" AND COALESCE(f.format, '') NOT IN ('mp4', 'mov', 'mpeg-ts')", (model, root_id))
            if detect._dir_of(rel) == folder]


def job_folder(conn, root_id, folder, *, throttle=None, should_cancel=None, workers=1, **_):
    """The jobs engine's "scene_tags" kind: one folder."""
    from lunelis import paths
    from lunelis.dupes import detect
    rec = backend()
    result = detect.FolderResult()
    if rec is None:
        result.cancelled = True                       # nothing to run with: pause, don't fail
        return result
    todo = pending_in_folder(conn, root_id, folder, rec.model_id)
    done, _ = tag_files(conn, todo, rec, paths.DATA_DIR, should_cancel)
    if done < len(todo) and should_cancel and should_cancel():
        result.cancelled = True
    return result


# --- reviewing -------------------------------------------------------------------------------------

def queue(conn: sqlite3.Connection, min_conf: float = 0.0) -> list[tuple[str, int, float]]:
    """[(tag, photos, average confidence)] of suggestions waiting, most first."""
    return [tuple(r) for r in conn.execute(
        f"SELECT t.name, COUNT(*), AVG(ft.confidence) FROM file_tags ft JOIN tags t ON t.id = ft.tag_id"
        f" JOIN files f ON f.id = ft.file_id WHERE ft.confidence IS NOT NULL AND ft.confidence >= ? AND {LIVE}"
        f" GROUP BY t.id ORDER BY COUNT(*) DESC", (min_conf,))]


def suggested_photos(conn: sqlite3.Connection, tag: str, min_conf: float = 0.0) -> list[tuple[int, float]]:
    return [tuple(r) for r in conn.execute(
        f"SELECT ft.file_id, ft.confidence FROM file_tags ft JOIN tags t ON t.id = ft.tag_id"
        f" JOIN files f ON f.id = ft.file_id WHERE t.name = ? AND ft.confidence IS NOT NULL"
        f" AND ft.confidence >= ? AND {LIVE} ORDER BY ft.confidence DESC", (tag, min_conf))]


def accept(conn: sqlite3.Connection, tag: str, file_ids) -> int:
    """Suggestions become your tags (and go to sidecars)."""
    from lunelis.tags import model as tags
    tid = tags.tag_id(conn, tag, create=False)
    ids = list(file_ids)
    if tid is None or not ids:
        return 0
    before = conn.total_changes
    conn.executemany("UPDATE file_tags SET confidence = NULL WHERE file_id = ? AND tag_id = ?"
                     " AND confidence IS NOT NULL", [(f, tid) for f in ids])
    n = conn.total_changes - before
    tags._queue_sidecars(conn, set(ids))
    conn.commit()
    return n


def reject(conn: sqlite3.Connection, tag: str, file_ids) -> int:
    """Suggestions removed, and never suggested again for these photos."""
    from lunelis.tags import model as tags
    tid = tags.tag_id(conn, tag, create=False)
    ids = list(file_ids)
    if tid is None or not ids:
        return 0
    before = conn.total_changes
    conn.executemany("DELETE FROM file_tags WHERE file_id = ? AND tag_id = ? AND confidence IS NOT NULL",
                     [(f, tid) for f in ids])
    n = conn.total_changes - before
    conn.executemany("INSERT OR IGNORE INTO tag_rejections (file_id, tag_id) VALUES (?, ?)", [(f, tid) for f in ids])
    conn.commit()
    return n


def accept_above(conn: sqlite3.Connection, tag: str, threshold: float) -> int:
    return accept(conn, tag, [f for f, c in suggested_photos(conn, tag) if c >= threshold])


def suggestions_of(conn: sqlite3.Connection, file_id: int) -> list[tuple[str, float]]:
    return [tuple(r) for r in conn.execute(
        "SELECT t.name, ft.confidence FROM file_tags ft JOIN tags t ON t.id = ft.tag_id"
        " WHERE ft.file_id = ? AND ft.confidence IS NOT NULL ORDER BY ft.confidence DESC", (file_id,))]
