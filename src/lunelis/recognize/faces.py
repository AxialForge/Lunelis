"""
Faces: found, recognised and named on this PC.

Two small free models from OpenCV's model zoo, run with OpenCV itself (no
extra package), downloaded only when you turn faces on and checked against
their pinned SHA-256:

- YuNet (MIT licence, 0.2 MB): finds faces and five landmarks.
- SFace (Apache 2.0, 37 MB): a 128-number fingerprint per face, aligned on
  the landmarks. Two fingerprints of one person have a cosine similarity
  above ~0.36 (OpenCV's own threshold); we suggest from 0.40.

How a face moves through the catalog (faces table):

    found     person_id NULL, confirmed 0          (maybe in an unnamed `cluster`)
    suggested suggested_person_id + suggestion      ("Ann?" - waiting for a yes)
    named     person_id + confirmed 1               -> the photo gets People|Ann
    ignored   ignored 1                             ("not a face")
    stranger  ignored 2                             (someone you don't know)  -> People|Unknown

Only confirmed faces tag their photo, so a wrong guess never reaches a
sidecar. "Not this person" is remembered (face_rejections) and that person
is never suggested for that face again. With Settings faces_auto_confirm on,
a suggestion at least faces_auto_threshold alike is confirmed by itself.

Boxes are stored as fractions of the upright photo ([x, y, w, h], 0..1), so
the overlay draws them at any size. A 160 px crop of each face is cached in
<data>/cache/faces for the People page.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable

import numpy as np

from lunelis.tags import model as tags

MODEL_ID = "yunet-2023mar+sface-2021dec"
ROOT = "People"                       # person tags: People|Ann
UNKNOWN = "Unknown"                   # People|Unknown: a photo with a stranger in it
NOT_A_FACE, STRANGER = 1, 2           # faces.ignored
BASE = "https://github.com/opencv/opencv_zoo/raw/47534e27c9851bb1128ccc0102f1145e27f23f98/models/"
DETECT_EDGE = 1600                    # faces are found in a 1600 px copy of the photo
MIN_FACE = 0.025                      # smaller than 2.5 % of the long side: too small to know
MIN_SCORE = 0.8
SUGGEST = 0.40                        # cosine similarity for "Ann?"
GROUP = 0.45                          # stricter, for unnamed groups of the same face
CROP = 160
LIVE = "f.missing_since IS NULL AND f.excluded = 0 AND f.quarantined_at IS NULL"
VIDEO = "COALESCE(f.format, '') NOT IN ('mp4', 'mov', 'mpeg-ts')"


@dataclass(frozen=True)
class File:
    name: str
    url: str
    sha256: str
    size: int


FILES = (
    File("face_detection_yunet_2023mar.onnx", BASE + "face_detection_yunet/face_detection_yunet_2023mar.onnx",
         "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4", 232_589),
    File("face_recognition_sface_2021dec.onnx", BASE + "face_recognition_sface/face_recognition_sface_2021dec.onnx",
         "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79", 38_696_353),
)
TOTAL = sum(f.size for f in FILES)


# --- the models ---------------------------------------------------------------------------------

def folder() -> Path:
    from lunelis import paths
    return paths.DATA_DIR / "models" / "faces"


def crops_dir() -> Path:
    from lunelis import paths
    return paths.DATA_DIR / "cache" / "faces"


def available() -> bool:
    return all((folder() / f.name).is_file() and (folder() / f.name).stat().st_size == f.size for f in FILES)


def download(on_progress: Callable[[int, int], None] | None = None,
             should_cancel: Callable[[], bool] | None = None) -> Path:
    """Fetch every missing file, checking each one's SHA-256 before it's kept."""
    d = folder()
    d.mkdir(parents=True, exist_ok=True)
    done = sum(f.size for f in FILES if (d / f.name).is_file() and (d / f.name).stat().st_size == f.size)
    for f in FILES:
        dest = d / f.name
        if dest.is_file() and dest.stat().st_size == f.size:
            continue
        tmp = dest.with_suffix(dest.suffix + ".part")
        h = hashlib.sha256()
        req = urllib.request.Request(f.url, headers={"User-Agent": "Lunelis"})
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
                    on_progress(done, TOTAL)
        if h.hexdigest() != f.sha256:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"{f.name} didn't match its checksum - nothing was installed")
        os.replace(tmp, dest)
    backend.cache_clear()
    return d


def remove() -> None:
    for f in FILES:
        (folder() / f.name).unlink(missing_ok=True)
    backend.cache_clear()


class Backend:
    """YuNet + SFace through OpenCV. detect() takes an RGB uint8 array."""
    model_id = MODEL_ID

    def __init__(self, d: Path) -> None:
        import cv2
        self.cv2 = cv2
        try:
            cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)   # "targets not supported" noise
        except AttributeError:
            pass
        self.detector = cv2.FaceDetectorYN.create(str(d / FILES[0].name), "", (320, 320), MIN_SCORE, 0.3, 200)
        self.recognizer = cv2.FaceRecognizerSF.create(str(d / FILES[1].name), "")

    def detect(self, rgb: np.ndarray) -> list[tuple[list[float], float, np.ndarray]]:
        """[(box as fractions [x, y, w, h], score, fingerprint)] for each face."""
        bgr = self.cv2.cvtColor(rgb, self.cv2.COLOR_RGB2BGR)
        h, w = bgr.shape[:2]
        self.detector.setInputSize((w, h))
        _, found = self.detector.detect(bgr)
        out = []
        if found is None:
            return out
        long_side = max(w, h)
        for row in found:
            x, y, fw, fh = (float(v) for v in row[:4])
            if max(fw, fh) < MIN_FACE * long_side:
                continue
            aligned = self.recognizer.alignCrop(bgr, row)
            vec = self.recognizer.feature(aligned).astype(np.float32).ravel()
            vec /= max(1e-6, float(np.linalg.norm(vec)))
            x0, y0 = max(0.0, x), max(0.0, y)
            box = [x0 / w, y0 / h, min(fw, w - x0) / w, min(fh, h - y0) / h]
            out.append((box, float(row[-1]), vec))
        return out


@lru_cache(maxsize=1)
def backend() -> Backend | None:
    if not available():
        return None
    return Backend(folder())


# --- finding faces --------------------------------------------------------------------------------

def _vec(blob: bytes | None) -> np.ndarray | None:
    return None if blob is None else np.frombuffer(blob, dtype=np.float32)


def _image(conn: sqlite3.Connection, file_id: int):
    """The photo upright, at most DETECT_EDGE px (a RAW's own embedded preview)."""
    from lunelis.raw.thumbnails import render
    row = conn.execute(
        "SELECT r.path, f.rel_path, e.orientation FROM files f JOIN roots r ON r.id = f.root_id"
        " LEFT JOIN exif e ON e.file_id = f.id WHERE f.id = ?", (file_id,)).fetchone()
    if row is None:
        raise FileNotFoundError(file_id)
    return render(os.path.join(row[0], *row[1].split("/")), row[2], DETECT_EDGE, log_preview="off")


def crop_path(face_id: int) -> Path:
    return crops_dir() / str(face_id // 1000) / f"{face_id}.jpg"


def save_crop(img, face_id: int, box: list[float]) -> None:
    """A square crop around the face (with some room) for the People page."""
    w, h = img.size
    cx, cy = (box[0] + box[2] / 2) * w, (box[1] + box[3] / 2) * h
    half = max(box[2] * w, box[3] * h) * 0.8
    c = img.crop((int(cx - half), int(cy - half), int(cx + half), int(cy + half)))
    c.thumbnail((CROP, CROP))
    p = crop_path(face_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    c.save(p, "JPEG", quality=88)


def scan_files(conn: sqlite3.Connection, file_ids: list[int], be: Backend | None = None,
               should_cancel=None) -> tuple[int, int]:
    """Find faces in these photos (once per model), then suggest and group.
    Returns (photos looked at, faces found)."""
    be = be or backend()
    if be is None:
        raise RuntimeError("the face models aren't installed (Settings > Library > Faces)")
    looked = found = 0
    new_ids: list[int] = []
    for fid in file_ids:
        if should_cancel and should_cancel():
            break
        try:
            img = _image(conn, fid)
        except Exception:                            # unreadable or offline: the next run tries again
            continue
        hits = be.detect(np.asarray(img.convert("RGB")))
        # A re-scan with a new model replaces the faces it found itself; named and hand-drawn ones stay.
        conn.execute("DELETE FROM faces WHERE file_id = ? AND source = 'auto' AND confirmed = 0", (fid,))
        for box, score, vec in hits:
            if _overlaps_kept(conn, fid, box):
                continue
            face_id = conn.execute(
                "INSERT INTO faces (file_id, bbox_json, embedding, confidence, model, created_at)"
                " VALUES (?, ?, ?, ?, ?, datetime('now'))",
                (fid, json.dumps([round(v, 5) for v in box]), vec.tobytes(), score, be.model_id)).lastrowid
            new_ids.append(face_id)
            try:
                save_crop(img, face_id, box)
            except OSError:
                pass
        conn.execute("INSERT INTO face_scans (file_id, model, faces) VALUES (?, ?, ?)"
                     " ON CONFLICT(file_id) DO UPDATE SET model = excluded.model, faces = excluded.faces,"
                     " scanned_at = datetime('now')", (fid, be.model_id, len(hits)))
        looked += 1
        found += len(hits)
        if looked % 25 == 0:
            conn.commit()
    conn.commit()
    if new_ids:
        suggest(conn, new_ids)
        group(conn, new_ids)
    return looked, found


def _overlaps_kept(conn: sqlite3.Connection, file_id: int, box: list[float]) -> bool:
    """A face already named or drawn by hand at this spot: don't add it twice."""
    for (b,) in conn.execute("SELECT bbox_json FROM faces WHERE file_id = ?", (file_id,)):
        if iou(json.loads(b), box) > 0.4:
            return True
    return False


def iou(a: list[float], b: list[float]) -> float:
    ix = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def pending(conn: sqlite3.Connection, model: str = MODEL_ID, root_id: int | None = None) -> list[int]:
    """Photos not yet looked at by this model."""
    sql = (f"SELECT f.id, f.rel_path FROM files f LEFT JOIN face_scans s ON s.file_id = f.id AND s.model = ?"
           f" WHERE s.file_id IS NULL AND {LIVE} AND {VIDEO} AND f.thumbnail_path IS NOT NULL")
    args: list = [model]
    if root_id is not None:
        sql += " AND f.root_id = ?"
        args.append(root_id)
    return [r[0] for r in conn.execute(sql + " ORDER BY f.id", args)]


def job_folder(conn, root_id, folder, *, throttle=None, should_cancel=None, workers=1, **_):
    """The jobs engine's "faces" kind: one folder."""
    from lunelis.dupes import detect
    result = detect.FolderResult()
    be = backend()
    if be is None:
        result.cancelled = True                       # nothing to run with: pause, don't fail
        return result
    todo = [fid for fid, rel in conn.execute(
        f"SELECT f.id, f.rel_path FROM files f LEFT JOIN face_scans s ON s.file_id = f.id AND s.model = ?"
        f" WHERE f.root_id = ? AND s.file_id IS NULL AND {LIVE} AND {VIDEO} AND f.thumbnail_path IS NOT NULL",
        (be.model_id, root_id)) if detect._dir_of(rel) == folder]
    done, _ = scan_files(conn, todo, be, should_cancel)
    if done < len(todo) and should_cancel and should_cancel():
        result.cancelled = True
    return result


# --- people ------------------------------------------------------------------------------------

def tag_for(name: str) -> str:
    return f"{ROOT}{tags.SEP}{name}"


def person_id(conn: sqlite3.Connection, name: str, create: bool = True) -> int | None:
    name = " ".join((name or "").replace(tags.SEP, " ").split())
    if not name:
        raise ValueError("A person needs a name.")
    if name.casefold() == UNKNOWN.casefold():
        raise ValueError("\"Unknown\" is kept for strangers - mark the face as a stranger instead.")
    row = conn.execute("SELECT id FROM people WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    if row:
        return row[0]
    if not create:
        return None
    return conn.execute("INSERT INTO people (name) VALUES (?)", (name,)).lastrowid


def name_of(conn: sqlite3.Connection, pid: int) -> str | None:
    row = conn.execute("SELECT name FROM people WHERE id = ?", (pid,)).fetchone()
    return row[0] if row else None


def _centroids(conn: sqlite3.Connection) -> tuple[list[int], np.ndarray, dict[int, np.ndarray]]:
    """(person ids, their mean fingerprints, every confirmed fingerprint per person)."""
    by: dict[int, list[np.ndarray]] = {}
    for pid, blob in conn.execute(
            "SELECT person_id, embedding FROM faces WHERE confirmed = 1 AND person_id IS NOT NULL"
            " AND embedding IS NOT NULL AND ignored = 0"):
        by.setdefault(pid, []).append(_vec(blob))
    ids = sorted(by)
    if not ids:
        return [], np.zeros((0, 128), np.float32), {}
    means = np.stack([np.mean(by[p], axis=0) for p in ids]).astype(np.float32)
    means /= np.maximum(1e-6, np.linalg.norm(means, axis=1, keepdims=True))
    return ids, means, {p: np.stack(v) for p, v in by.items()}


def suggest(conn: sqlite3.Connection, face_ids: list[int] | None = None) -> int:
    """Suggest a known person for unnamed faces (all of them when face_ids is None).
    Returns how many got a suggestion; confirms the surest ones when that's turned on."""
    from lunelis.settings import Settings
    ids, means, samples = _centroids(conn)
    if not ids:
        return 0
    s = Settings(conn)
    auto = bool(s.get("faces_auto_confirm"))
    threshold = float(s.get("faces_auto_threshold"))
    sql = ("SELECT id, embedding FROM faces WHERE confirmed = 0 AND ignored = 0 AND embedding IS NOT NULL")
    rows = conn.execute(sql).fetchall() if face_ids is None else [
        r for chunk in _chunks(face_ids) for r in conn.execute(
            sql + f" AND id IN ({','.join('?' * len(chunk))})", chunk)]
    rejected = {}
    for fid, pid in conn.execute("SELECT face_id, person_id FROM face_rejections"):
        rejected.setdefault(fid, set()).add(pid)
    made = 0
    confirm_now: dict[int, list[int]] = {}
    for face_id, blob in rows:
        v = _vec(blob)
        sims = means @ v
        best_i, best = -1, -1.0
        for i in np.argsort(-sims)[:5]:
            pid = ids[i]
            if pid in rejected.get(face_id, ()):
                continue
            sim = max(float(sims[i]), float(np.max(samples[pid] @ v)) * 0.95)
            if sim > best:
                best_i, best = i, sim
        if best_i >= 0 and best >= SUGGEST:
            conn.execute("UPDATE faces SET suggested_person_id = ?, suggestion = ? WHERE id = ?",
                         (ids[best_i], round(best, 4), face_id))
            made += 1
            if auto and best >= threshold:
                confirm_now.setdefault(ids[best_i], []).append(face_id)
        else:
            conn.execute("UPDATE faces SET suggested_person_id = NULL, suggestion = NULL WHERE id = ?", (face_id,))
    conn.commit()
    for pid, fids in confirm_now.items():
        confirm(conn, fids, pid)
    return made


def _chunks(items: list[int], n: int = 500):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def group(conn: sqlite3.Connection, face_ids: list[int] | None = None) -> int:
    """Put unnamed, unsuggested faces into groups of alike faces (each joins
    the nearest group within GROUP, or starts its own). Returns faces grouped."""
    rows = conn.execute(
        "SELECT id, embedding, cluster FROM faces WHERE confirmed = 0 AND ignored = 0"
        " AND suggested_person_id IS NULL AND embedding IS NOT NULL ORDER BY id").fetchall()
    if not rows:
        return 0
    want = None if face_ids is None else set(face_ids)
    members: dict[int, list[np.ndarray]] = {}
    for fid, blob, cl in rows:
        if cl is not None and (want is None or fid not in want):
            members.setdefault(cl, []).append(_vec(blob))
    keys = list(members)
    cents = (np.stack([np.mean(members[k], axis=0) for k in keys]).astype(np.float32)
             if keys else np.zeros((0, 128), np.float32))
    if len(keys):
        cents /= np.maximum(1e-6, np.linalg.norm(cents, axis=1, keepdims=True))
    nxt = (conn.execute("SELECT COALESCE(MAX(cluster), 0) FROM faces").fetchone()[0] or 0) + 1
    n = 0
    for fid, blob, cl in rows:
        if want is not None and fid not in want:
            continue
        if want is None and cl is not None:
            continue
        v = _vec(blob)
        if len(keys):
            sims = cents @ v
            i = int(np.argmax(sims))
            if sims[i] >= GROUP:
                conn.execute("UPDATE faces SET cluster = ? WHERE id = ?", (keys[i], fid))
                members[keys[i]].append(v)
                c = np.mean(members[keys[i]], axis=0)
                cents[i] = c / max(1e-6, float(np.linalg.norm(c)))
                n += 1
                continue
        conn.execute("UPDATE faces SET cluster = ? WHERE id = ?", (nxt, fid))
        members[nxt] = [v]
        keys.append(nxt)
        cents = np.vstack([cents, v[None, :]])
        nxt += 1
        n += 1
    conn.commit()
    return n


def _sync_tags(conn: sqlite3.Connection, file_ids, pid: int) -> None:
    """A photo carries People|<name> exactly when it has a confirmed face of that person."""
    name = name_of(conn, pid)
    if not name:
        return
    have, lose = [], []
    for fid in set(file_ids):
        named = conn.execute("SELECT 1 FROM faces WHERE file_id = ? AND person_id = ? AND confirmed = 1"
                             " AND ignored = 0 LIMIT 1", (fid, pid)).fetchone()
        (have if named else lose).append(fid)
    if have:
        tags.add(conn, have, [tag_for(name)], commit=False)
    if lose:
        tags.remove(conn, lose, tag_for(name), commit=False)


def unknown_tag() -> str:
    return f"{ROOT}{tags.SEP}{UNKNOWN}"


def _sync_unknown(conn: sqlite3.Connection, file_ids) -> None:
    """A photo carries People|Unknown exactly when it has a face marked as a stranger."""
    have, lose = [], []
    for fid in set(file_ids):
        s = conn.execute("SELECT 1 FROM faces WHERE file_id = ? AND ignored = ? LIMIT 1", (fid, STRANGER)).fetchone()
        (have if s else lose).append(fid)
    if have:
        tags.add(conn, have, [unknown_tag()], commit=False)
    if lose and tags.tag_id(conn, unknown_tag(), create=False) is not None:
        tags.remove(conn, lose, unknown_tag(), commit=False)


def _files_of(conn: sqlite3.Connection, face_ids) -> dict[int, list[int]]:
    """{previous person (or None): [file ids]} for these faces."""
    out: dict = {}
    for chunk in _chunks(list(face_ids)):
        for pid, fid in conn.execute(
                f"SELECT person_id, file_id FROM faces WHERE id IN ({','.join('?' * len(chunk))})", chunk):
            out.setdefault(pid, []).append(fid)
    return out


def confirm(conn: sqlite3.Connection, face_ids, pid: int) -> int:
    """These faces are this person: named, ungrouped, and the photos tagged."""
    face_ids = list(face_ids)
    before = _files_of(conn, face_ids)
    for chunk in _chunks(face_ids):
        conn.execute(f"UPDATE faces SET person_id = ?, confirmed = 1, suggested_person_id = NULL,"
                     f" suggestion = NULL, cluster = NULL, ignored = 0"
                     f" WHERE id IN ({','.join('?' * len(chunk))})", [pid, *chunk])
        conn.execute(f"DELETE FROM face_rejections WHERE person_id = ? AND face_id IN"
                     f" ({','.join('?' * len(chunk))})", [pid, *chunk])
    files = [f for fs in before.values() for f in fs]
    _sync_tags(conn, files, pid)
    _sync_unknown(conn, files)
    for old, fs in before.items():
        if old is not None and old != pid:
            _sync_tags(conn, fs, old)
    if conn.execute("SELECT cover_face_id FROM people WHERE id = ?", (pid,)).fetchone()[0] is None and face_ids:
        conn.execute("UPDATE people SET cover_face_id = ? WHERE id = ?", (face_ids[0], pid))
    conn.commit()
    return len(face_ids)


def name_faces(conn: sqlite3.Connection, face_ids, name: str) -> int:
    """Name faces (making the person if needed). Returns the person's id."""
    pid = person_id(conn, name)
    confirm(conn, face_ids, pid)
    suggest(conn)                                   # a new known face may match more unnamed ones
    return pid


def name_group(conn: sqlite3.Connection, cluster: int, name: str) -> int:
    ids = [r[0] for r in conn.execute("SELECT id FROM faces WHERE cluster = ? AND confirmed = 0 AND ignored = 0",
                                      (cluster,))]
    return name_faces(conn, ids, name)


def reject(conn: sqlite3.Connection, face_ids) -> int:
    """"Not this person": unnamed again (and its tag gone), and that person is
    never suggested for these faces again."""
    face_ids = list(face_ids)
    before = _files_of(conn, face_ids)
    for chunk in _chunks(face_ids):
        q = ",".join("?" * len(chunk))
        conn.execute(f"INSERT OR IGNORE INTO face_rejections (face_id, person_id)"
                     f" SELECT id, COALESCE(person_id, suggested_person_id) FROM faces WHERE id IN ({q})"
                     f" AND COALESCE(person_id, suggested_person_id) IS NOT NULL", chunk)
        conn.execute(f"UPDATE faces SET person_id = NULL, confirmed = 0, suggested_person_id = NULL,"
                     f" suggestion = NULL WHERE id IN ({q})", chunk)
    for old, fs in before.items():
        if old is not None:
            _sync_tags(conn, fs, old)
    conn.commit()
    suggest(conn, face_ids)                         # the next-best person, if any
    group(conn, face_ids)
    return len(face_ids)


def ignore(conn: sqlite3.Connection, face_ids, ignored: bool = True, stranger: bool = False) -> int:
    """"Not a face" (ignored), or a stranger (People|Unknown on the photo): either
    way kept out of suggestions and groups. ignored=False brings faces back."""
    face_ids = list(face_ids)
    before = _files_of(conn, face_ids)
    state = (STRANGER if stranger else NOT_A_FACE) if ignored else 0
    for chunk in _chunks(face_ids):
        conn.execute(f"UPDATE faces SET ignored = ?, person_id = NULL, confirmed = 0, suggested_person_id = NULL,"
                     f" suggestion = NULL, cluster = NULL WHERE id IN ({','.join('?' * len(chunk))})",
                     [state, *chunk])
    for old, fs in before.items():
        if old is not None:
            _sync_tags(conn, fs, old)
    _sync_unknown(conn, [f for fs in before.values() for f in fs])
    conn.commit()
    if not ignored:
        suggest(conn, face_ids)
        group(conn, face_ids)
    return len(face_ids)


def mark_strangers(conn: sqlite3.Connection, face_ids) -> int:
    """People you don't know: their photos get People|Unknown."""
    return ignore(conn, face_ids, stranger=True)


def unnamed_in(conn: sqlite3.Connection, file_ids) -> list[int]:
    """Faces in these photos that nobody has named or set aside."""
    ids = list(file_ids)
    out: list[int] = []
    for chunk in _chunks(ids):
        out += [r[0] for r in conn.execute(
            f"SELECT id FROM faces WHERE confirmed = 0 AND ignored = 0 AND file_id IN ({','.join('?' * len(chunk))})",
            chunk)]
    return out


def rest_are_strangers(conn: sqlite3.Connection, file_ids) -> int:
    """A shoot in a public place: everyone not named in these photos is a stranger."""
    return mark_strangers(conn, unnamed_in(conn, file_ids))


def ungroup(conn: sqlite3.Connection, face_ids) -> int:
    """Take faces out of an unnamed group (they start groups of their own later)."""
    face_ids = list(face_ids)
    for chunk in _chunks(face_ids):
        conn.execute(f"UPDATE faces SET cluster = NULL WHERE id IN ({','.join('?' * len(chunk))})", chunk)
    nxt = (conn.execute("SELECT COALESCE(MAX(cluster), 0) FROM faces").fetchone()[0] or 0) + 1
    for i, fid in enumerate(face_ids):
        conn.execute("UPDATE faces SET cluster = ? WHERE id = ?", (nxt + i, fid))
    conn.commit()
    return len(face_ids)


def rename_person(conn: sqlite3.Connection, pid: int, new: str) -> int:
    """Rename; a name already in use merges the two people. Returns the surviving id."""
    old = name_of(conn, pid)
    new = " ".join((new or "").replace(tags.SEP, " ").split())
    if not new:
        raise ValueError("A person needs a name.")
    if new.casefold() == UNKNOWN.casefold():
        raise ValueError("\"Unknown\" is kept for strangers.")
    other = person_id(conn, new, create=False)
    if other is not None and other != pid:
        merge_people(conn, pid, other)
        return other
    conn.execute("UPDATE people SET name = ? WHERE id = ?", (new, pid))
    if old and tags.tag_id(conn, tag_for(old), create=False) is not None:
        tags.rename(conn, tag_for(old), tag_for(new))
    conn.commit()
    return pid


def merge_people(conn: sqlite3.Connection, source: int, into: int) -> None:
    faces = [r[0] for r in conn.execute("SELECT id FROM faces WHERE person_id = ?", (source,))]
    files = [r[0] for r in conn.execute("SELECT file_id FROM faces WHERE person_id = ?", (source,))]
    name = name_of(conn, source)
    conn.execute("UPDATE faces SET person_id = NULL, confirmed = 0 WHERE person_id = ?", (source,))
    if name:
        tags.remove(conn, files, tag_for(name), commit=False)
    conn.execute("UPDATE faces SET suggested_person_id = ? WHERE suggested_person_id = ?", (into, source))
    conn.execute("DELETE FROM people WHERE id = ?", (source,))
    if name and tags.tag_id(conn, tag_for(name), create=False) is not None:
        tags.delete(conn, tag_for(name))
    confirm(conn, faces, into)


def delete_person(conn: sqlite3.Connection, pid: int) -> int:
    """Forget a person: their faces become unnamed again; the People tag goes."""
    name = name_of(conn, pid)
    faces = [r[0] for r in conn.execute("SELECT id FROM faces WHERE person_id = ?", (pid,))]
    conn.execute("UPDATE faces SET person_id = NULL, confirmed = 0 WHERE person_id = ?", (pid,))
    conn.execute("UPDATE faces SET suggested_person_id = NULL, suggestion = NULL WHERE suggested_person_id = ?",
                 (pid,))
    conn.execute("DELETE FROM people WHERE id = ?", (pid,))
    if name and tags.tag_id(conn, tag_for(name), create=False) is not None:
        tags.delete(conn, tag_for(name))
    conn.commit()
    group(conn, faces)
    return len(faces)


def add_face(conn: sqlite3.Connection, file_id: int, box: list[float], name: str | None = None) -> int:
    """A face drawn by hand (one the model missed). Fingerprinted when the models are in."""
    box = [max(0.0, min(1.0, float(v))) for v in box]
    vec = None
    be = backend()
    img = None
    try:
        img = _image(conn, file_id)
    except Exception:
        pass
    if be is not None and img is not None:
        w, h = img.size
        x0, y0 = int(box[0] * w), int(box[1] * h)
        x1, y1 = int((box[0] + box[2]) * w), int((box[1] + box[3]) * h)
        pad = int(max(x1 - x0, y1 - y0) * 0.4)
        sub = np.asarray(img.convert("RGB").crop((max(0, x0 - pad), max(0, y0 - pad), min(w, x1 + pad),
                                                  min(h, y1 + pad))))
        hits = be.detect(sub) if sub.size else []
        if hits:
            vec = max(hits, key=lambda t: t[1])[2]
    face_id = conn.execute(
        "INSERT INTO faces (file_id, bbox_json, embedding, confidence, source, model, created_at)"
        " VALUES (?, ?, ?, NULL, 'user', ?, datetime('now'))",
        (file_id, json.dumps([round(v, 5) for v in box]), None if vec is None else vec.tobytes(),
         MODEL_ID if vec is not None else None)).lastrowid
    conn.commit()
    if img is not None:
        try:
            save_crop(img, face_id, box)
        except OSError:
            pass
    if name:
        name_faces(conn, [face_id], name)
    elif vec is not None:
        suggest(conn, [face_id])
    return face_id


def delete_face(conn: sqlite3.Connection, face_id: int) -> None:
    """Remove a hand-drawn box (found faces are ignored instead, so they don't come back)."""
    before = _files_of(conn, [face_id])
    conn.execute("DELETE FROM faces WHERE id = ? AND source = 'user'", (face_id,))
    for old, fs in before.items():
        if old is not None:
            _sync_tags(conn, fs, old)
    conn.commit()
    crop_path(face_id).unlink(missing_ok=True)


# --- reading -------------------------------------------------------------------------------------------

@dataclass
class Face:
    id: int
    file_id: int
    box: list[float]
    person_id: int | None
    name: str | None
    suggested_id: int | None
    suggested: str | None
    suggestion: float | None
    cluster: int | None
    ignored: bool                     # set aside: not a face, or a stranger
    source: str
    stranger: bool = False


FACE_SQL = ("SELECT fa.id, fa.file_id, fa.bbox_json, fa.person_id, p.name, fa.suggested_person_id, s.name,"
            " fa.suggestion, fa.cluster, fa.ignored, fa.source FROM faces fa"
            " LEFT JOIN people p ON p.id = fa.person_id AND fa.confirmed = 1"
            " LEFT JOIN people s ON s.id = fa.suggested_person_id")


def _face(r) -> Face:
    return Face(r[0], r[1], json.loads(r[2]), r[3] if r[4] is not None else None, r[4], r[5], r[6], r[7], r[8],
                bool(r[9]), r[10], r[9] == STRANGER)


def faces_of(conn: sqlite3.Connection, file_id: int, with_ignored: bool = False,
             with_strangers: bool = False) -> list[Face]:
    sql = FACE_SQL + " WHERE fa.file_id = ?" + (
        "" if with_ignored else f" AND fa.ignored IN (0, {STRANGER})" if with_strangers else " AND fa.ignored = 0")
    return [_face(r) for r in conn.execute(sql + " ORDER BY json_extract(fa.bbox_json, '$[0]')", (file_id,))]


def faces_where(conn: sqlite3.Connection, where: str, args=(), limit: int | None = None) -> list[Face]:
    sql = FACE_SQL + f" JOIN files f ON f.id = fa.file_id WHERE {LIVE} AND " + where + " ORDER BY fa.id DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [_face(r) for r in conn.execute(sql, args)]


def faces_of_person(conn, pid: int, limit: int | None = None) -> list[Face]:
    return faces_where(conn, "fa.person_id = ? AND fa.confirmed = 1 AND fa.ignored = 0", (pid,), limit)


def suggested_for(conn, pid: int, limit: int | None = None) -> list[Face]:
    return faces_where(conn, "fa.suggested_person_id = ? AND fa.confirmed = 0 AND fa.ignored = 0", (pid,), limit)


def faces_in_group(conn, cluster: int, limit: int | None = None) -> list[Face]:
    return faces_where(conn, "fa.cluster = ? AND fa.confirmed = 0 AND fa.ignored = 0", (cluster,), limit)


def ignored_faces(conn, limit: int | None = None) -> list[Face]:
    """Strangers and "not a face", newest first."""
    return faces_where(conn, "fa.ignored IN (1, 2)", (), limit)


@dataclass
class Person:
    id: int
    name: str
    photos: int
    waiting: int
    cover_face_id: int | None


def people(conn: sqlite3.Connection) -> list[Person]:
    out = []
    for pid, name, cover in conn.execute("SELECT id, name, cover_face_id FROM people WHERE name IS NOT NULL"
                                         " AND hidden = 0 ORDER BY name COLLATE NOCASE"):
        photos = conn.execute(
            f"SELECT COUNT(DISTINCT fa.file_id) FROM faces fa JOIN files f ON f.id = fa.file_id"
            f" WHERE fa.person_id = ? AND fa.confirmed = 1 AND fa.ignored = 0 AND {LIVE}", (pid,)).fetchone()[0]
        waiting = conn.execute(
            f"SELECT COUNT(*) FROM faces fa JOIN files f ON f.id = fa.file_id WHERE fa.suggested_person_id = ?"
            f" AND fa.confirmed = 0 AND fa.ignored = 0 AND {LIVE}", (pid,)).fetchone()[0]
        if cover is None or not conn.execute("SELECT 1 FROM faces WHERE id = ? AND person_id = ?",
                                             (cover, pid)).fetchone():
            row = conn.execute("SELECT id FROM faces WHERE person_id = ? AND confirmed = 1 ORDER BY id LIMIT 1",
                               (pid,)).fetchone()
            cover = row[0] if row else None
        out.append(Person(pid, name, photos, waiting, cover))
    return out


def groups(conn: sqlite3.Connection, min_faces: int = 2) -> list[tuple[int, int, int]]:
    """Unnamed groups: [(cluster, faces, first face id)], biggest first."""
    return [tuple(r) for r in conn.execute(
        f"SELECT fa.cluster, COUNT(*), MIN(fa.id) FROM faces fa JOIN files f ON f.id = fa.file_id"
        f" WHERE fa.cluster IS NOT NULL AND fa.confirmed = 0 AND fa.ignored = 0"
        f" AND fa.suggested_person_id IS NULL AND {LIVE}"
        f" GROUP BY fa.cluster HAVING COUNT(*) >= ? ORDER BY COUNT(*) DESC", (min_faces,))]


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    """For the People page header and Library status."""
    one = lambda q: conn.execute(q).fetchone()[0]
    return {
        "scanned": one("SELECT COUNT(*) FROM face_scans"),
        "faces": one("SELECT COUNT(*) FROM faces WHERE ignored = 0"),
        "named": one("SELECT COUNT(*) FROM faces WHERE confirmed = 1 AND ignored = 0"),
        "waiting": one("SELECT COUNT(*) FROM faces WHERE suggested_person_id IS NOT NULL AND confirmed = 0"
                       " AND ignored = 0"),
        "people": one("SELECT COUNT(*) FROM people WHERE name IS NOT NULL"),
        "strangers": one(f"SELECT COUNT(*) FROM faces WHERE ignored = {STRANGER}"),
    }


def photos_of(conn: sqlite3.Connection, pid: int) -> list[int]:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT file_id FROM faces WHERE person_id = ? AND confirmed = 1 AND ignored = 0", (pid,))]
