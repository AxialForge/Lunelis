"""
Trimming a clip to a new file - the original is only read.

The streams are copied, not re-encoded: quick, and no quality is lost. A
copied video can only start on a keyframe, so the trim starts at the last
keyframe at or before the chosen start (at most a second or two early on a
camera file) and ends at the first packet past the chosen end. Audio is cut
to match the video's span.

The new file goes to the Create folder (create.engine.output_dir) as
"<name> trim 0m12s-0m31s.mp4", never over an existing file.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import av


class TrimError(Exception):
    pass


@dataclass
class TrimResult:
    path: str
    start: float          # where the new file really starts in the original (keyframe), seconds
    end: float
    duration: float


def duration(path: str | Path) -> float:
    """The clip's length in seconds (0 when it can't be read)."""
    try:
        with av.open(str(path)) as c:
            if c.duration:
                return c.duration / 1_000_000
            v = next((s for s in c.streams if s.type == "video"), None)
            if v is not None and v.duration and v.time_base:
                return float(v.duration * v.time_base)
    except (av.error.FFmpegError, OSError):
        pass
    return 0.0


def _clock(sec: float) -> str:
    sec = max(0, int(round(sec)))
    return f"{sec // 3600}h{sec // 60 % 60:02d}m{sec % 60:02d}s" if sec >= 3600 else f"{sec // 60}m{sec % 60:02d}s"


def trim_name(src: str | Path, start: float, end: float) -> str:
    p = Path(src)
    return f"{p.stem} trim {_clock(start)}-{_clock(end)}{p.suffix.lower()}"


def trim(src: str | Path, start: float, end: float, folder: str | Path,
         progress: Callable[[float], None] | None = None, cancelled: Callable[[], bool] | None = None) -> TrimResult:
    """Copy the part of `src` between `start` and `end` seconds into a new file in `folder`."""
    from lunelis.edit.export import free_path

    if end - start < 0.1:
        raise TrimError("The end must be after the start.")
    length = duration(src)
    if length and start >= length - 0.05:
        raise TrimError("The start is past the end of the clip.")
    if length:
        end = min(end, length)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    out = free_path(str(folder), trim_name(src, start, end))
    try:
        _copy(str(src), out, start, end, progress, cancelled)
    except TrimError:
        Path(out).unlink(missing_ok=True)
        raise
    except (av.error.FFmpegError, OSError, ValueError) as e:
        Path(out).unlink(missing_ok=True)      # nothing half-made is kept
        raise TrimError(f"Couldn't trim this clip: {e}") from e
    if cancelled and cancelled():
        Path(out).unlink(missing_ok=True)
        raise TrimError("Cancelled.")
    real = duration(out)
    return TrimResult(out, start, end, real)


def _copy(src: str, out: str, start: float, end: float, progress, cancelled) -> None:
    with av.open(src) as inp:
        video = next((s for s in inp.streams if s.type == "video"), None)
        if video is None:
            raise TrimError("This file has no video stream.")
        keep = [s for s in inp.streams if s.type in ("video", "audio")]
        # The last keyframe at or before the start: a seek lands on *a* keyframe
        # before it, so look ahead from there for a later one that still fits.
        inp.seek(int(start / video.time_base), stream=video, backward=True, any_frame=False)
        from_t = None
        for pkt in inp.demux(video):
            if pkt.pts is None:
                continue
            t = float(pkt.pts * video.time_base)
            if t > start + 1e-3:
                break
            if pkt.is_keyframe:
                from_t = t
        inp.seek(int(start / video.time_base), stream=video, backward=True, any_frame=False)
        with av.open(out, "w") as dst:
            omap = {}
            for s in keep:
                o = dst.add_stream_from_template(s)
                omap[s.index] = o
            first: dict[int, int] = {}        # stream -> first kept pts (made 0 in the new file)
            v_start = None
            for pkt in inp.demux(keep):
                if cancelled and cancelled():
                    return
                if pkt.dts is None or pkt.pts is None:
                    continue
                s = pkt.stream
                t = float(pkt.pts * s.time_base)
                if s is video:
                    if v_start is None:
                        if not pkt.is_keyframe or (from_t is not None and t < from_t - 1e-3):
                            continue          # up to the keyframe chosen above
                        v_start = t
                    if t > end:
                        break
                else:
                    if v_start is None or t < v_start:
                        continue              # audio before the video starts
                    if t > end:
                        continue
                base = first.setdefault(s.index, pkt.pts)
                pkt.pts -= base
                pkt.dts -= base
                if pkt.dts < 0:
                    pkt.dts = 0
                pkt.stream = omap[s.index]
                dst.mux(pkt)
                if progress and s is video and end > start:
                    progress(min(1.0, max(0.0, (t - start) / (end - start))))
            if v_start is None:
                raise TrimError("Nothing to keep between those times.")
