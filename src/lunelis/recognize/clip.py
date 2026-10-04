"""
The scene model: OpenAI CLIP ViT-B/32 (MIT licence), as quantized ONNX files
converted by Xenova (Hugging Face), run on this PC with onnxruntime.

Four files, ~155 MB, downloaded only when you turn scene tags on, each
checked against its pinned SHA-256 before it's used:

- vision_model_quantized.onnx  image -> 512-number embedding
- text_model_quantized.onnx    words -> 512-number embedding (same space)
- vocab.json, merges.txt       CLIP's byte-pair tokenizer

The tokenizer is CLIP's own algorithm written out here (no extra package):
lower-cased, split into words, numbers and punctuation, byte-level BPE with
a `</w>` end-of-word marker, wrapped in start / end tokens (49406 / 49407),
at most 77 tokens.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable

import numpy as np

MODEL_ID = "clip-vit-b32-q"            # embeddings.model - a different model is a different value
BASE = "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main/"
SIZE = 224
MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
START, END, CONTEXT = 49406, 49407, 77


@dataclass(frozen=True)
class File:
    name: str
    url: str
    sha256: str
    size: int


FILES = (
    File("vision_model_quantized.onnx", BASE + "onnx/vision_model_quantized.onnx",
         "583fd1110a514667812fee7d684952aaf82a99b959760c8d7dca7e0ab9839299", 89_117_001),
    File("text_model_quantized.onnx", BASE + "onnx/text_model_quantized.onnx",
         "73baab855d406190da9faa498cfedf65f15cf309f4cc7385b7b032e6d08e5c3a", 64_504_507),
    File("vocab.json", BASE + "vocab.json",
         "5047b556ce86ccaf6aa22b3ffccfc52d391ea4accdab9c2f2407da5b742d4363", 862_328),
    File("merges.txt", BASE + "merges.txt",
         "9fd691f7c8039210e0fced15865466c65820d09b63988b0174bfe25de299051a", 524_619),
)
TOTAL = sum(f.size for f in FILES)


def folder() -> Path:
    from lunelis import paths
    return paths.DATA_DIR / "models" / "clip"


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
    return d


def remove() -> None:
    for f in FILES:
        (folder() / f.name).unlink(missing_ok=True)
    _sessions.cache_clear()
    _tokenizer.cache_clear()


# --- the tokenizer ---------------------------------------------------------------------------

def _bytes_to_unicode() -> dict[int, str]:
    bs = list(range(ord("!"), ord("~") + 1)) + list(range(ord("¡"), ord("¬") + 1)) + list(range(ord("®"), ord("ÿ") + 1))
    cs = bs[:]
    n = 0
    for b in range(256):
        if b not in bs:
            bs.append(b)
            cs.append(256 + n)
            n += 1
    return dict(zip(bs, (chr(c) for c in cs)))


# CLIP's split: contractions, runs of letters, single digits, runs of other symbols.
_PAT = re.compile(r"""<\|startoftext\|>|<\|endoftext\|>|'s|'t|'re|'ve|'m|'ll|'d|[^\W\d_]+|\d|[^\s\w]+|_+""",
                  re.IGNORECASE)


class Tokenizer:
    def __init__(self, vocab_path: Path, merges_path: Path) -> None:
        self.encoder: dict[str, int] = json.loads(vocab_path.read_text(encoding="utf-8"))
        lines = merges_path.read_text(encoding="utf-8").split("\n")[1:]
        merges = [tuple(m.split()) for m in lines if m.strip()]
        self.ranks = {m: i for i, m in enumerate(merges)}
        self.byte_encoder = _bytes_to_unicode()
        self.cache: dict[str, list[str]] = {}

    def _bpe(self, token: str) -> list[str]:
        if token in self.cache:
            return self.cache[token]
        word = list(token[:-1]) + [token[-1] + "</w>"]
        while len(word) > 1:
            pairs = {(word[i], word[i + 1]) for i in range(len(word) - 1)}
            best = min(pairs, key=lambda p: self.ranks.get(p, float("inf")))
            if best not in self.ranks:
                break
            first, second = best
            out, i = [], 0
            while i < len(word):
                if i < len(word) - 1 and word[i] == first and word[i + 1] == second:
                    out.append(first + second)
                    i += 2
                else:
                    out.append(word[i])
                    i += 1
            word = out
        self.cache[token] = word
        return word

    def encode(self, text: str) -> list[int]:
        text = re.sub(r"\s+", " ", text.strip()).lower()
        ids = []
        for tok in _PAT.findall(text):
            mapped = "".join(self.byte_encoder[b] for b in tok.encode("utf-8"))
            ids.extend(self.encoder[p] for p in self._bpe(mapped) if p in self.encoder)
        return [START] + ids[:CONTEXT - 2] + [END]


@lru_cache(maxsize=1)
def _tokenizer() -> Tokenizer:
    return Tokenizer(folder() / "vocab.json", folder() / "merges.txt")


@lru_cache(maxsize=1)
def _sessions():
    import onnxruntime as ort
    opts = ort.SessionOptions()
    opts.log_severity_level = 3
    opts.intra_op_num_threads = max(1, (os.cpu_count() or 4) // 2)     # leave the PC usable
    vision = ort.InferenceSession(str(folder() / FILES[0].name), opts, providers=["CPUExecutionProvider"])
    text = ort.InferenceSession(str(folder() / FILES[1].name), opts, providers=["CPUExecutionProvider"])
    return vision, text


def _normalize(v: np.ndarray) -> np.ndarray:
    return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)


def preprocess(img) -> np.ndarray:
    """A PIL image -> CLIP's 224 x 224 input (shortest side 224, centre crop)."""
    from PIL import Image
    img = img.convert("RGB")
    w, h = img.size
    k = SIZE / min(w, h)
    img = img.resize((max(SIZE, round(w * k)), max(SIZE, round(h * k))), Image.Resampling.BICUBIC)
    w, h = img.size
    left, top = (w - SIZE) // 2, (h - SIZE) // 2
    a = np.asarray(img.crop((left, top, left + SIZE, top + SIZE)), dtype=np.float32) / 255.0
    return ((a - MEAN) / STD).transpose(2, 0, 1)


class ClipBackend:
    """The Recognizer for scene tags (recognize.base)."""

    model_id = MODEL_ID
    dim = 512

    def embed_images(self, images: list) -> np.ndarray:
        if not images:
            return np.zeros((0, self.dim), np.float32)
        vision, _ = _sessions()
        batch = np.stack([preprocess(i) for i in images])
        return _normalize(vision.run(None, {"pixel_values": batch})[0].astype(np.float32))

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), np.float32)
        _, text = _sessions()
        tok = _tokenizer()
        ids = [tok.encode(t) for t in texts]
        width = max(len(i) for i in ids)
        arr = np.array([i + [END] * (width - len(i)) for i in ids], dtype=np.int64)   # padded with the end token
        return _normalize(text.run(None, {"input_ids": arr})[0].astype(np.float32))
