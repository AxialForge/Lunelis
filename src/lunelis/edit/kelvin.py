"""
The Temperature slider in kelvin (0.53).

The slider is a pair of channel gains (pipeline.tone_lut): red x 2^(0.45 t),
blue x 2^(-0.45 t), so red / blue changes by 2^(0.9 t) for t = slider / 100.
A white balance of T kelvin makes a T-kelvin light neutral; going from the
shot's T0 to T1 multiplies red / blue by q(T0) / q(T1), where q(T) is the
red / blue ratio of a black body at T. So

    q(T1) = q(T0) / 2^(0.9 t)

and a higher slider is a higher kelvin setting - a warmer picture, as in
Lightroom. q is worked out in linear sRGB (Kim et al.'s fit to the Planckian
locus); a camera's own primaries differ a little, so the figure is a good
guide, not a measurement.

T0, the shot's white balance: a RAW's camera multipliers against LibRaw's
daylight (D65) ones. Other files don't record it: they are shown from
DAYLIGHT with an "about" mark.
"""
from __future__ import annotations

import math

DAYLIGHT = 5500.0                 # the starting point when the file doesn't say
D65 = 6504.0                      # what LibRaw's daylight multipliers are for
LOW, HIGH = 1667.0, 25000.0       # where the fit holds
SLOPE = 0.9                       # log2(red / blue) per unit of slider / 100


def _xy(t: float) -> tuple[float, float]:
    """CIE xy of a black body at t kelvin (Kim et al. 2002)."""
    t = min(HIGH, max(LOW, t))
    if t < 4000:
        x = -0.2661239e9 / t**3 - 0.2343589e6 / t**2 + 0.8776956e3 / t + 0.179910
    else:
        x = -3.0258469e9 / t**3 + 2.1070379e6 / t**2 + 0.2226347e3 / t + 0.240390
    if t < 2222:
        y = -1.1063814 * x**3 - 1.34811020 * x**2 + 2.18555832 * x - 0.20219683
    elif t < 4000:
        y = -0.9549476 * x**3 - 1.37418593 * x**2 + 2.09137015 * x - 0.16748867
    else:
        y = 3.0817580 * x**3 - 5.87338670 * x**2 + 3.75112997 * x - 0.37001483
    return x, y


def q(t: float) -> float:
    """Red / blue of a black body at t kelvin, in linear sRGB."""
    x, y = _xy(t)
    X, Y, Z = x / y, 1.0, (1 - x - y) / y
    r = 3.2406 * X - 1.5372 * Y - 0.4986 * Z
    b = 0.0557 * X - 0.2040 * Y + 1.0570 * Z
    return max(1e-6, r) / max(1e-6, b)


def _invert(target: float) -> float:
    """The kelvin whose q is `target` (q falls as kelvin rises)."""
    lo, hi = LOW, HIGH
    if target >= q(lo):
        return lo
    if target <= q(hi):
        return hi
    for _ in range(40):
        mid = (lo + hi) / 2
        if q(mid) > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def kelvin(as_shot: float, slider: float) -> float:
    """The white balance, in kelvin, that this Temperature slider value amounts to."""
    return _invert(q(as_shot) / 2 ** (SLOPE * slider / 100))


def slider_for(as_shot: float, target: float) -> float:
    """The slider value that sets `target` kelvin (clipped to the slider's range)."""
    t = math.log2(q(as_shot) / q(target)) / SLOPE * 100
    return max(-100.0, min(100.0, t))


def as_shot(path: str, is_raw: bool) -> float | None:
    """The white balance the camera used, in kelvin - from a RAW's multipliers;
    None for a file that doesn't record it (or can't be read)."""
    if not is_raw:
        return None
    try:
        import rawpy
        with rawpy.imread(path) as raw:
            cam, day = list(raw.camera_whitebalance), list(raw.daylight_whitebalance)
    except Exception:                              # noqa: BLE001 - no figure, nothing else
        return None
    if len(cam) < 3 or len(day) < 3 or min(cam[0], cam[2], day[0], day[2]) <= 0:
        return None
    # A light with more red needs less red gain: q is the inverse of the multipliers' ratio.
    ratio = (day[0] / day[2]) / (cam[0] / cam[2])
    return _invert(q(D65) * ratio)


def label(as_shot_k: float | None, slider: float) -> str:
    """'5,250 K' for a RAW; '≈ 5,500 K' when the start is assumed."""
    k = kelvin(as_shot_k or DAYLIGHT, slider)
    text = f"{int(round(k / 50.0) * 50):,} K"
    return text if as_shot_k else "≈ " + text
