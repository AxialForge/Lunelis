"""
Slideshow videos: photos one after another as an MP4, with transitions and
music.

- **Timing:** each photo is on screen `seconds`; a transition takes the last
  `fade` seconds of one photo's time - so the video is photos x seconds long.
- **Transitions:** crossfade, fade through black, or a straight cut.
- **Slow zoom** (Ken Burns): each photo drifts in a little while it's on.
- **Fit:** the whole photo on black (fit) or cropped to fill the frame.
- **Music:** any audio file FFmpeg reads (mp3, m4a, wav, flac...), cut to the
  video's length and faded out over the last two seconds. AAC in the MP4.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageOps

from lunelis.create import engine

SIZES = {"720p": (1280, 720), "1080p": (1920, 1080), "square": (1080, 1080), "vertical": (1080, 1920)}
TRANSITIONS = ("crossfade", "black", "cut")
FPS = 30
AUDIO_RATE = 48000
MUSIC_FADE = 2.0


@dataclass
class SlideshowOptions:
    seconds: float = 3.0
    fade: float = 0.8
    transition: str = "crossfade"
    zoom: bool = True
    fill: bool = False
    size: str = "1080p"
    music: str | None = None
    quality: int = 78

    def check(self, n: int) -> None:
        if not 0.5 <= self.seconds <= 30:
            raise ValueError("each photo 0.5-30 seconds")
        if self.transition not in TRANSITIONS:
            raise ValueError(f"transition must be one of {TRANSITIONS}")
        if self.transition != "cut" and not 0.1 <= self.fade <= self.seconds / 2:
            raise ValueError("a transition takes 0.1 s to half a photo's time")
        if self.size not in SIZES:
            raise ValueError(f"size must be one of {tuple(SIZES)}")
        if self.music and not os.path.isfile(self.music):
            raise ValueError(f"the music file isn't there: {self.music}")
        if n < 1:
            raise ValueError("pick at least one photo")

    def frames_per_photo(self) -> int:
        return max(1, round(self.seconds * FPS))


def _slide(img: Image.Image, size: tuple[int, int], fill: bool) -> Image.Image:
    if fill:
        return ImageOps.fit(img, size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", size, "black")
    img = ImageOps.contain(img, size, Image.Resampling.LANCZOS)
    canvas.paste(img, ((size[0] - img.width) // 2, (size[1] - img.height) // 2))
    return canvas


def _zoomed(slide: Image.Image, t: float) -> Image.Image:
    """t 0..1 through the photo's time: 0 -> 8 % closer, centred."""
    k = 1.0 + 0.08 * t
    w, h = slide.size
    cw, ch = w / k, h / k
    box = ((w - cw) / 2, (h - ch) / 2, (w + cw) / 2, (h + ch) / 2)
    return slide.resize((w, h), Image.Resampling.BILINEAR, box=box)


def frames(slides: list[Image.Image], opts: SlideshowOptions):
    """Every video frame, in order (a generator - one photo's frames at a time)."""
    per = opts.frames_per_photo()
    fade_n = 0 if opts.transition == "cut" else max(1, round(opts.fade * FPS))
    for i, slide in enumerate(slides):
        nxt = slides[i + 1] if i + 1 < len(slides) else None
        for f in range(per):
            img = _zoomed(slide, f / per) if opts.zoom else slide
            into = f - (per - fade_n)
            if nxt is not None and fade_n and into >= 0:
                a = (into + 1) / (fade_n + 1)
                other = _zoomed(nxt, 0.0) if opts.zoom else nxt
                if opts.transition == "crossfade":
                    img = Image.blend(img, other, a)
                else:                                          # through black: out, then in
                    img = Image.blend(img, Image.new("RGB", img.size), min(1.0, a * 2)) if a < 0.5 \
                        else Image.blend(Image.new("RGB", img.size), other, (a - 0.5) * 2)
            yield img


def _audio_stream(out, quality: int):
    stream = out.add_stream("aac", rate=AUDIO_RATE)
    stream.layout = "stereo"
    stream.time_base = Fraction(1, AUDIO_RATE)
    stream.bit_rate = 128_000 if quality < 85 else 192_000
    return stream


def _music(out, stream, path: str, seconds: float):
    """The AAC track: the file's audio, cut to `seconds`, faded at the end.
    (Its stream is added before anything is written - see make.)"""
    import av
    resampler = av.AudioResampler(format="fltp", layout="stereo", rate=AUDIO_RATE)
    total = int(seconds * AUDIO_RATE)
    fade_from = total - int(MUSIC_FADE * AUDIO_RATE)
    done = 0
    with av.open(path) as src:
        for frame in src.decode(audio=0):
            for rf in resampler.resample(frame):
                a = rf.to_ndarray().astype(np.float32)           # (channels, samples)
                n = a.shape[1]
                if done + n > total:
                    a = a[:, :total - done]
                    n = a.shape[1]
                if n <= 0:
                    break
                pos = np.arange(done, done + n)
                gain = np.clip((total - pos) / max(1, total - fade_from), 0, 1)
                a *= np.where(pos >= fade_from, gain, 1.0)
                nf = av.AudioFrame.from_ndarray(np.ascontiguousarray(a), format="fltp", layout="stereo")
                nf.sample_rate = AUDIO_RATE
                nf.time_base = Fraction(1, AUDIO_RATE)
                nf.pts = done
                for pkt in stream.encode(nf):
                    out.mux(pkt)
                done += n
            if done >= total:
                break
    for pkt in stream.encode(None):
        out.mux(pkt)


def make(conn: sqlite3.Connection, file_ids: list[int], opts: SlideshowOptions, folder: str | Path, name: str,
         progress: Callable[[int, int], None] | None = None,
         cancelled: Callable[[], bool] | None = None) -> str:
    import av
    from lunelis.create.animation import Cancelled
    from lunelis.edit.export import _BAD, free_path
    opts.check(len(file_ids))
    size = SIZES[opts.size]
    os.makedirs(folder, exist_ok=True)
    dest = free_path(str(folder), (_BAD.sub("_", name).strip(" .") or "Slideshow") + ".mp4")
    crf = round(51 - opts.quality * 0.4)
    per = opts.frames_per_photo()
    try:
        with av.open(dest, "w") as out:
            vs = out.add_stream("libx264", rate=Fraction(FPS, 1))
            vs.width, vs.height = size
            vs.pix_fmt = "yuv420p"
            vs.options = {"crf": str(crf), "preset": "medium"}
            # Every stream exists before the first packet: the file's header is
            # written then, and a stream added later crashes the muxer.
            aus = _audio_stream(out, opts.quality) if opts.music else None

            def slides():
                for i, fid in enumerate(file_ids):
                    if cancelled and cancelled():
                        raise Cancelled()
                    if progress:
                        progress(i, len(file_ids))
                    yield _slide(engine.photo(conn, fid, max(size)), size, opts.fill)

            # Two slides at a time: this one and the next (for its transition).
            it = slides()
            cur = next(it, None)
            i = 0
            while cur is not None:
                nxt = next(it, None)
                pair = [cur] + ([nxt] if nxt is not None else [])
                for k, img in enumerate(frames(pair, opts)):
                    if k >= per:
                        break                                   # the next one's own frames come next round
                    for pkt in vs.encode(av.VideoFrame.from_image(img)):
                        out.mux(pkt)
                cur, i = nxt, i + 1
            for pkt in vs.encode():
                out.mux(pkt)
            if aus is not None:
                _music(out, aus, opts.music, len(file_ids) * per / FPS)
    except Cancelled:
        Path(dest).unlink(missing_ok=True)
        raise
    if progress:
        progress(len(file_ids), len(file_ids))
    return dest
