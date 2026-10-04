"""
Ask your library: a plain sentence -> photos, best first.

    "sunset on a beach, A7R V, 2024"
        -> Camera: ILCE-7RM5 · 2024 · looks like "sunset on a beach"

The sentence is read in two parts:

- **What it can match exactly** becomes smart-album rules (albums/smart.py):
  a camera or lens named as you'd say it ("A7R V" finds ILCE-7RM5 through
  the catalog's own model names), years and months ("2024", "June 2024"),
  stars ("4 stars", "5 star"), "picks", "rejects", "raw", "videos", ISO
  ("iso 6400", "high iso"), focal lengths ("85mm") and apertures ("f/1.4").
- **The rest** ("sunset on a beach") is matched against what the photos look
  like: the scene model's picture embeddings (recognize/), so it finds a
  sunset nobody tagged. Without the model it falls back to the search box's
  word search (names, folders, tags).

Every part read is shown as a chip, so a wrong reading is easy to see - and
removing a chip asks again without it.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field

import numpy as np

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
     "december"], 1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})
SIMILAR_LIMIT = 500             # photos shown for "find similar" / a "looks like"
LOOKS_FLOOR = 0.20              # cosine below this isn't a match for a sentence


@dataclass
class Chip:
    kind: str                   # camera | lens | date | stars | flag | kind | iso | focal | aperture | looks
    text: str                   # what the chip says
    rules: list = field(default_factory=list)   # its smart-album rules (none for "looks like")


def _chip(kind: str, text: str, *rules: dict) -> Chip:
    return Chip(kind, text, list(rules))


@dataclass
class Asked:
    sentence: str
    chips: list[Chip] = field(default_factory=list)
    looks: str = ""             # what's left for the picture model

    def rules(self) -> dict | None:
        rs = [r for c in self.chips for r in c.rules]
        return {"match": "all", "rules": rs} if rs else None

    def without(self, i: int) -> "Asked":
        chips = [c for n, c in enumerate(self.chips) if n != i]
        looks = "" if self.chips[i].kind == "looks" else self.looks
        return Asked(self.sentence, chips, looks)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _cameras(conn) -> list[tuple[str, str]]:
    """(model, everything it can be called): ILCE-7RM5 is also "A7R V", "a7r5"."""
    out = []
    for model, make in conn.execute("SELECT DISTINCT camera_model, camera_make FROM exif WHERE camera_model IS NOT NULL"):
        names = {_norm(model)}
        m = re.match(r"ILCE-(\d)([A-Z]*)M(\d+)", model or "")          # Sony: ILCE-7RM5 -> a7rv / a7r5
        if m:
            roman = {"2": "ii", "3": "iii", "4": "iv", "5": "v", "6": "vi"}.get(m.group(3), m.group(3))
            names |= {f"a{m.group(1)}{m.group(2).lower()}{roman}", f"a{m.group(1)}{m.group(2).lower()}{m.group(3)}"}
        else:
            m = re.match(r"ILCE-(\d+)([A-Z]*)$", model or "")
            if m:
                names.add(f"a{m.group(1)}{m.group(2).lower()}")
        out.append((model, names))
    return out


def _lenses(conn) -> list[str]:
    return [r[0] for r in conn.execute("SELECT DISTINCT lens FROM exif WHERE lens IS NOT NULL AND lens != ''")]


def parse(conn: sqlite3.Connection, sentence: str) -> Asked:
    asked = Asked(sentence)
    rest = " " + re.sub(r"\s+", " ", sentence.strip()) + " "

    def take(pattern: str, fn) -> None:
        nonlocal rest
        while True:
            m = re.search(pattern, rest, re.IGNORECASE)
            if not m:
                return
            chip = fn(m)
            if chip is None:
                return
            asked.chips.append(chip)
            rest = rest[:m.start()] + " " + rest[m.end():]

    # Stars, flags, kinds.
    take(r"\b([0-5])\s*stars?\b", lambda m: _chip("stars", f"{m.group(1)}+ stars",
                                                  {"field": "stars", "op": ">=", "value": int(m.group(1))}))
    take(r"\bpick(?:s|ed)?\b", lambda m: _chip("flag", "Picks", {"field": "flag", "op": "is", "value": "pick"}))
    take(r"\breject(?:s|ed)?\b", lambda m: _chip("flag", "Rejects", {"field": "flag", "op": "is", "value": "reject"}))
    take(r"\b(raws?|raw files)\b", lambda m: _chip("kind", "RAW", {"field": "kind", "op": "is", "value": "raw"}))
    take(r"\b(videos?|clips?)\b", lambda m: _chip("kind", "Videos", {"field": "kind", "op": "is", "value": "video"}))
    # Exposure.
    take(r"\bhigh iso\b", lambda m: _chip("iso", "ISO 3200 and up", {"field": "iso", "op": ">=", "value": 3200}))
    take(r"\biso\s*(\d{2,6})\b", lambda m: _chip("iso", f"ISO {m.group(1)}",
                                                 {"field": "iso", "op": "=", "value": int(m.group(1))}))
    take(r"\b(\d{1,4})\s*mm\b", lambda m: _chip("focal", f"{m.group(1)} mm",
                                               {"field": "focal", "op": "=", "value": int(m.group(1))}))
    take(r"\bf\s*/\s*(\d+(?:\.\d)?)\b", lambda m: _chip("aperture", f"f/{m.group(1)}",
                                                      {"field": "aperture", "op": "=", "value": float(m.group(1))}))
    # Dates: "June 2024", "2024".

    def between(text: str, start: str, end: str) -> Chip:
        return _chip("date", text, {"field": "date", "op": "on or after", "value": start},
                     {"field": "date", "op": "on or before", "value": end})

    def month_year(m) -> Chip:
        mo, y = MONTHS[m.group(1).lower()], int(m.group(2))
        last = 31 if mo in (1, 3, 5, 7, 8, 10, 12) else 30 if mo != 2 else 29
        return between(f"{m.group(1).title()} {y}", f"{y}-{mo:02d}-01", f"{y}-{mo:02d}-{last:02d}")

    month_names = "|".join(sorted(MONTHS, key=len, reverse=True))
    take(rf"\b(?:in\s+)?({month_names})\s+((?:19|20)\d\d)\b", month_year)
    take(r"\b(?:in\s+)?((?:19|20)\d\d)\b", lambda m: between(m.group(1), f"{m.group(1)}-01-01", f"{m.group(1)}-12-31"))

    # Cameras and lenses, as the catalog knows them.
    words = [w for w in re.split(r"[\s,;]+", rest) if w]
    used: set[int] = set()
    for model, names in _cameras(conn):
        found = False
        for i in range(len(words)):
            for j in range(min(len(words), i + 3), i, -1):
                said = _norm("".join(words[i:j]))
                if set(range(i, j)) & used or len(said) < 3:
                    continue
                if said in names:
                    asked.chips.append(_chip("camera", f"Camera: {model}",
                                             {"field": "camera", "op": "is", "value": model}))
                    used |= set(range(i, j))
                    found = True
                    break
            if found:
                break
    for lens in _lenses(conn):
        nl = _norm(lens)
        for i, w in enumerate(words):
            if i in used or len(_norm(w)) < 4 or not re.search(r"\d", w):
                continue
            if _norm(w) in nl:
                asked.chips.append(_chip("lens", f"Lens: {w}", {"field": "lens", "op": "contains", "value": w}))
                used.add(i)
                break

    # What's left is what the photo should look like.
    text = re.sub(r"\s+", " ", " ".join(w for i, w in enumerate(words) if i not in used)).strip(" ,.;")
    while True:                                  # "show me photos of ..." -> "..."
        t = re.sub(r"^(show me|show|find|photos? of|pictures? of|shots? of|my|me|some|all)\s+", "", text,
                   flags=re.IGNORECASE)
        if t == text:
            break
        text = t
    filler = {"on", "a", "an", "the", "of", "in", "at", "with", "and", "photo", "photos", "picture", "pictures",
              "from", "by", "for", "to", "my", "me", "taken", "shot", "shots", "some", "all", "show", "find", "using"}
    if text and any(w.lower() not in filler for w in text.split()):
        asked.looks = text
        asked.chips.append(_chip("looks", f"Looks like: {text}"))
    return asked


def chips_text(asked: Asked) -> list[str]:
    return [c.text for c in asked.chips if c.text]


# --- answering ---------------------------------------------------------------------------------

def _candidates(conn, rules: dict | None) -> list[int]:
    from lunelis.albums.model import LIVE
    sql = (f"SELECT f.id FROM files f JOIN roots r ON r.id = f.root_id LEFT JOIN exif e ON e.file_id = f.id"
           f" LEFT JOIN ratings rt ON rt.file_id = f.id WHERE {LIVE} AND r.enabled = 1 AND f.archived_at IS NULL")
    params: list = []
    if rules:
        from lunelis.albums.smart import condition
        cond, params = condition(rules)
        sql += f" AND {cond}"
    return [r[0] for r in conn.execute(sql, params)]


_cache: dict = {}


def _matrix(conn, model_id: str) -> tuple[np.ndarray, np.ndarray]:
    """Every photo's embedding, kept in memory (half precision: ~160 MB for a
    159k library) and re-read only when the set of embeddings changed."""
    stamp = conn.execute("SELECT COUNT(*), MAX(made_at) FROM embeddings WHERE model = ?", (model_id,)).fetchone()
    key = (model_id, tuple(stamp))
    if _cache.get("key") != key:
        from lunelis.recognize.scenes import embeddings
        ids, vecs = embeddings(conn, model_id)
        _cache.clear()
        _cache.update(key=key, ids=np.array(ids, dtype=np.int64),
                      vecs=vecs.astype(np.float16) if len(ids) else np.zeros((0, 1), np.float16))
    return _cache["ids"], _cache["vecs"]


def rank_by_vector(conn, model_id: str, query: np.ndarray, among: list[int] | None = None,
                   limit: int = SIMILAR_LIMIT, floor: float | None = None) -> list[int]:
    """Photos (with an embedding) closest to `query`, best first."""
    ids, vecs = _matrix(conn, model_id)
    if not len(ids):
        return []
    if among is not None:
        keep = np.isin(ids, np.array(list(among), dtype=np.int64))
        ids, vecs = ids[keep], vecs[keep]
        if not len(ids):
            return []
    q = (query / max(np.linalg.norm(query), 1e-12)).astype(np.float32)
    sims = vecs.astype(np.float32) @ q if len(ids) < 20000 else (vecs @ q.astype(np.float16)).astype(np.float32)
    top = np.argsort(-sims)[:limit]
    return [int(ids[i]) for i in top if floor is None or sims[i] >= floor]


def answer(conn: sqlite3.Connection, asked: Asked, rec=None) -> tuple[list[int] | None, str | None]:
    """(ranked ids or None, search words or None). Ranked ids: the picture model
    ordered them; search words: no model, fall back to the word search. Both
    None: the rules alone are the answer."""
    rules = asked.rules()
    if not asked.looks:
        return None, None
    if rec is None:
        from lunelis.recognize.scenes import backend
        rec = backend()
    if rec is None:
        return None, asked.looks
    among = _candidates(conn, rules) if rules else None
    q = rec.embed_texts([f"a photo of {asked.looks}"])[0]
    return rank_by_vector(conn, rec.model_id, q, among, floor=LOOKS_FLOOR), None


def similar_to(conn: sqlite3.Connection, file_ids: list[int], rec=None, limit: int = SIMILAR_LIMIT) -> list[int]:
    """Find similar (one photo) / More like these (several): the average of their pictures."""
    if rec is None:
        from lunelis.recognize.scenes import backend
        rec = backend()
    if rec is None:
        return []
    from lunelis.recognize.scenes import embeddings
    have, vecs = embeddings(conn, rec.model_id, file_ids)        # just these few, from the catalog
    if not have:
        return []
    ranked = rank_by_vector(conn, rec.model_id, vecs.mean(axis=0), None, limit + len(have))
    return [f for f in ranked if f not in set(have)][:limit]


def filter_json(asked: Asked) -> str | None:
    r = asked.rules()
    return json.dumps(r) if r else None
