"""
The edit stack: what an edit is, independent of any pixels.

A stack has three parts, all optional:

- filter:   a named filter (built-in or yours) at an amount 0-100 %. The
            filter's adjustments are scaled by the amount, so "Vivid at 40 %"
            is simple mode's one slider;
- adjust:   manual adjustments, added on top of the filter's (applying a
            filter never locks out fine-tuning);
- geometry: quarter turns, flips, straighten angle, crop;
- masks:    local adjustments (edit/masks.py), applied in order;
- lens:     lens corrections (edit/lens.py): profile on/off + manual sliders;
- curves:   tone curves - "rgb" (all channels) and "r"/"g"/"b" - as control
            points (x, y) in 0..1, through a monotone spline.

Every adjustment is 0 when untouched, so scaling and adding stacks is plain
arithmetic and an empty stack means "the original".

Stored as compact text - `v=1;f=Vivid@40;exposure=0.3;crop=0.1,0.05,0.9,0.95`
- which is readable in the catalog and safe inside an XMP attribute.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

VERSION = 1


@dataclass(frozen=True)
class Param:
    key: str
    label: str
    lo: float
    hi: float
    group: str
    step: float = 1.0


# Order = the order of the Develop panel's sliders.
PARAMS: tuple[Param, ...] = (
    Param("exposure", "Exposure", -5, 5, "Light", 0.05),
    Param("contrast", "Contrast", -100, 100, "Light"),
    Param("highlights", "Highlights", -100, 100, "Light"),
    Param("shadows", "Shadows", -100, 100, "Light"),
    Param("whites", "Whites", -100, 100, "Light"),
    Param("blacks", "Blacks", -100, 100, "Light"),
    Param("temp", "Temperature", -100, 100, "Color"),
    Param("tint", "Tint", -100, 100, "Color"),
    Param("vibrance", "Vibrance", -100, 100, "Color"),
    Param("saturation", "Saturation", -100, 100, "Color"),
    Param("hue", "Hue", -180, 180, "Color"),
    Param("fade", "Fade", 0, 100, "Effects"),
    Param("vignette", "Vignette", -100, 100, "Effects"),
    Param("sharpen", "Sharpening", 0, 100, "Detail"),
    Param("denoise", "Noise reduction", 0, 100, "Detail"),
    Param("denoise_detail", "Noise detail", -100, 100, "Detail"),
    Param("denoise_color", "Color noise", 0, 100, "Detail"),
)
BY_KEY = {p.key: p for p in PARAMS}
GROUPS = ("Light", "Color", "Effects", "Detail")
CURVE_CHANNELS = ("rgb", "r", "g", "b")
LINEAR = ((0.0, 0.0), (1.0, 1.0))


def clean_curve(points) -> tuple:
    """Sorted, clamped, deduplicated control points with both ends present
    (an end's y can move: that's how you lift blacks or dim whites)."""
    pts = sorted((max(0.0, min(1.0, float(x))), max(0.0, min(1.0, float(y)))) for x, y in points)
    out: list[tuple[float, float]] = []
    for x, y in pts:
        if out and abs(x - out[-1][0]) < 0.01:
            out[-1] = (out[-1][0], y)
        else:
            out.append((round(x, 4), round(y, 4)))
    if not out or out[0][0] > 0:
        out.insert(0, (0.0, 0.0))
    if out[-1][0] < 1:
        out.append((1.0, 1.0))
    return tuple(out)


@dataclass(frozen=True)
class Geometry:
    rotate: int = 0                                  # quarter turns clockwise: 0, 90, 180, 270
    flip_h: bool = False
    flip_v: bool = False
    angle: float = 0.0                               # straighten, degrees (-45..45), clockwise
    crop: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)   # fractions of the straightened image

    def is_identity(self) -> bool:
        return self == Geometry()


@dataclass(frozen=True)
class Stack:
    filter: str | None = None
    amount: int = 100                                # filter strength, %
    adjust: dict = field(default_factory=dict)       # key -> value, only non-zero ones
    geometry: Geometry = Geometry()
    curves: dict = field(default_factory=dict)       # channel -> points, only non-linear ones
    masks: tuple = ()                                # edit/masks.Mask, applied in order
    lens: dict = field(default_factory=dict)         # edit/lens.py settings, only non-default ones

    def is_identity(self) -> bool:
        return ((self.filter is None or self.amount == 0) and not self.adjust
                and self.geometry.is_identity() and not self.curves and not self.masks and not self.lens)

    def with_lens(self, key: str, value) -> "Stack":
        lens = dict(self.lens)
        if key == "profile":
            value = bool(value)
        else:
            value = float(round(max(-100.0, min(100.0, float(value)))))
        if value:
            lens[key] = value
        else:
            lens.pop(key, None)
        return replace(self, lens=lens)

    def with_curve(self, channel: str, points) -> "Stack":
        curves = dict(self.curves)
        pts = clean_curve(points)
        if pts == LINEAR:
            curves.pop(channel, None)
        else:
            curves[channel] = pts
        return replace(self, curves=curves)

    def with_adjust(self, key: str, value: float) -> "Stack":
        adj = dict(self.adjust)
        value = clamp(key, value)
        if value:
            adj[key] = value
        else:
            adj.pop(key, None)
        return replace(self, adjust=adj)

    def without_geometry(self) -> "Stack":
        return replace(self, geometry=Geometry())


def clamp(key: str, value: float) -> float:
    p = BY_KEY[key]
    v = max(p.lo, min(p.hi, float(value)))
    return round(v, 2) if p.step < 1 else float(round(v))


def effective(stack: Stack, filter_params: dict | None) -> dict:
    """The adjustments the pipeline applies: the filter's, scaled by its
    amount, plus the manual ones - clamped to each slider's range."""
    out: dict[str, float] = {}
    if stack.filter and filter_params and stack.amount:
        k = stack.amount / 100
        for key, v in filter_params.items():
            if key in BY_KEY:
                out[key] = v * k
    for key, v in stack.adjust.items():
        out[key] = out.get(key, 0.0) + v
    p = {k: max(BY_KEY[k].lo, min(BY_KEY[k].hi, v)) for k, v in out.items() if v}
    if stack.curves:
        p["_curves"] = stack.curves                   # not a slider: carried through for the tone LUT
    return p


# --- text form ---------------------------------------------------------------------

def _num(v: float) -> str:
    return f"{v:g}"


def dumps(stack: Stack) -> str:
    parts = [f"v={VERSION}"]
    if stack.filter:
        parts.append(f"f={stack.filter.replace(';', ',').replace('=', '-').replace('@', ' ')}@{stack.amount}")
    for p in PARAMS:                                  # fixed order: equal stacks, equal text
        if stack.adjust.get(p.key):
            parts.append(f"{p.key}={_num(stack.adjust[p.key])}")
    g = stack.geometry
    if g.rotate:
        parts.append(f"rotate={g.rotate}")
    if g.flip_h:
        parts.append("flip_h=1")
    if g.flip_v:
        parts.append("flip_v=1")
    if g.angle:
        parts.append(f"angle={_num(round(g.angle, 2))}")
    if g.crop != (0.0, 0.0, 1.0, 1.0):
        parts.append("crop=" + ",".join(_num(round(c, 5)) for c in g.crop))
    for ch in CURVE_CHANNELS:
        if ch in stack.curves:
            key = "curve" if ch == "rgb" else f"curve_{ch}"
            parts.append(f"{key}=" + " ".join(f"{_num(x)},{_num(y)}" for x, y in stack.curves[ch]))
    if stack.lens.get("profile"):
        parts.append("lens=1")
    for k in ("distortion", "vignette", "ca_red", "ca_blue"):
        if stack.lens.get(k):
            parts.append(f"lens_{k}={_num(stack.lens[k])}")
    from lunelis.edit import masks
    for m in stack.masks:
        parts.append("mask=" + masks.dumps(m))
    return ";".join(parts)


def loads(text: str | None) -> Stack:
    """Parse a stack; unknown keys (from a newer Lunelis) are ignored."""
    if not text:
        return Stack()
    pairs = [part.split("=", 1) for part in text.split(";") if "=" in part]
    kv = dict(pairs)                                  # "mask" repeats: read from `pairs` below
    filt, amount = None, 100
    if "f" in kv:
        name, _, amt = kv["f"].rpartition("@")
        filt = name or kv["f"]
        try:
            amount = max(0, min(100, int(amt))) if name else 100
        except ValueError:
            amount = 100
    adjust = {}
    for key, v in kv.items():
        if key in BY_KEY:
            try:
                val = clamp(key, float(v))
            except ValueError:
                continue
            if val:
                adjust[key] = val
    geo = Geometry()
    try:
        rot = int(kv.get("rotate", 0)) % 360
        geo = Geometry(
            rotate=rot if rot in (0, 90, 180, 270) else 0,
            flip_h=kv.get("flip_h") == "1",
            flip_v=kv.get("flip_v") == "1",
            angle=max(-45.0, min(45.0, float(kv.get("angle", 0)))),
            crop=_crop(kv.get("crop")),
        )
    except ValueError:
        pass
    curves = {}
    for ch in CURVE_CHANNELS:
        text_pts = kv.get("curve" if ch == "rgb" else f"curve_{ch}")
        if text_pts:
            try:
                pts = clean_curve(tuple(float(c) for c in pair.split(",")) for pair in text_pts.split())
            except ValueError:
                continue
            if pts != LINEAR:
                curves[ch] = pts
    from lunelis.edit import masks
    mask_list = tuple(m for k, v in pairs if k == "mask" for m in [masks.loads(v)] if m is not None)
    lens: dict = {}
    if kv.get("lens") == "1":
        lens["profile"] = True
    for k in ("distortion", "vignette", "ca_red", "ca_blue"):
        try:
            v = float(round(max(-100.0, min(100.0, float(kv.get(f"lens_{k}", 0))))))
        except ValueError:
            v = 0.0
        if v:
            lens[k] = v
    return Stack(filt, amount, adjust, geo, curves, mask_list, lens)


def _crop(text: str | None) -> tuple[float, float, float, float]:
    if not text:
        return (0.0, 0.0, 1.0, 1.0)
    x0, y0, x1, y1 = (max(0.0, min(1.0, float(c))) for c in text.split(","))
    if x1 - x0 < 0.01 or y1 - y0 < 0.01:
        return (0.0, 0.0, 1.0, 1.0)
    return (x0, y0, x1, y1)
