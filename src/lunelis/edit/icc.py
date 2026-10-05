"""
Colour profiles made here, and colour management for the photo view.

**Making profiles.** Lunelis writes its own small ICC v2 matrix profiles from
published colour-space numbers (primaries, white point, tone curve), so
exports can be tagged Display P3 or an Adobe RGB (1998)-compatible space
without shipping anyone's profile files:

| name               | primaries      | curve                       |
|--------------------|----------------|-----------------------------|
| Display P3         | DCI-P3, D65    | the sRGB curve              |
| Adobe RGB-compatible | Adobe (1998), D65 | gamma 563/256 (≈2.2)  |

Primaries are adapted to the profile connection space's D50 white with the
Bradford transform, as ICC requires.

**The photo view.** `display_transform()` maps sRGB pixels to the monitor's
own profile (Windows' display profile) when that's turned on; `proof()`
shows how a photo will come out in another profile (a printer / paper
.icc) and, with the gamut warning, paints the colours it can't reproduce
magenta. Neither ever changes an edit or a file.
"""
from __future__ import annotations

import struct
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms

D65 = (0.3127, 0.3290)
D50_XYZ = (0.9642, 1.0, 0.8249)
SPACES = {
    "display-p3": ("Display P3 (Lunelis)", ((0.680, 0.320), (0.265, 0.690), (0.150, 0.060)), D65, "srgb"),
    "adobe-rgb": ("Adobe RGB (1998) compatible (Lunelis)", ((0.640, 0.330), (0.210, 0.710), (0.150, 0.060)),
                  D65, 563 / 256),
}
GAMUT_WARNING = (255, 0, 255)


def _xyz(xy) -> np.ndarray:
    x, y = xy
    return np.array([x / y, 1.0, (1 - x - y) / y])


def _bradford(src_white: np.ndarray, dst_white: np.ndarray) -> np.ndarray:
    m = np.array([[0.8951, 0.2664, -0.1614], [-0.7502, 1.7135, 0.0367], [0.0389, -0.0685, 1.0296]])
    s, d = m @ src_white, m @ dst_white
    return np.linalg.inv(m) @ np.diag(d / s) @ m


def rgb_to_xyz_d50(primaries, white) -> np.ndarray:
    """Columns: the red, green and blue colorants in D50 XYZ."""
    p = np.stack([_xyz(c) for c in primaries], axis=1)
    w = _xyz(white)
    s = np.linalg.solve(p, w)
    m = p * s
    return _bradford(w, np.array(D50_XYZ)) @ m


def _s15(v: float) -> bytes:
    return struct.pack(">i", int(round(v * 65536)))


def _tag_xyz(xyz) -> bytes:
    return b"XYZ " + b"\0" * 4 + b"".join(_s15(v) for v in xyz)


def _tag_curve(curve) -> bytes:
    if curve == "srgb":
        x = np.linspace(0, 1, 1024)
        y = np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)
        return b"curv" + b"\0" * 4 + struct.pack(">I", 1024) + b"".join(
            struct.pack(">H", int(round(v * 65535))) for v in y)
    return b"curv" + b"\0" * 4 + struct.pack(">I", 1) + struct.pack(">H", int(round(curve * 256)))


def _tag_desc(text: str) -> bytes:
    a = text.encode("ascii", "replace") + b"\0"
    return (b"desc" + b"\0" * 4 + struct.pack(">I", len(a)) + a + struct.pack(">II", 0, 0)
            + struct.pack(">HB", 0, 0) + b"\0" * 67)


def _tag_text(text: str) -> bytes:
    return b"text" + b"\0" * 4 + text.encode("ascii", "replace") + b"\0"


def make_profile(key: str) -> bytes:
    """An ICC v2 RGB display profile for one of SPACES."""
    name, primaries, white, curve = SPACES[key]
    m = rgb_to_xyz_d50(primaries, white)
    tags = [(b"desc", _tag_desc(name)), (b"cprt", _tag_text("No copyright, use freely")),
            (b"wtpt", _tag_xyz(D50_XYZ)),
            (b"rXYZ", _tag_xyz(m[:, 0])), (b"gXYZ", _tag_xyz(m[:, 1])), (b"bXYZ", _tag_xyz(m[:, 2]))]
    trc = _tag_curve(curve)
    tags += [(b"rTRC", trc), (b"gTRC", trc), (b"bTRC", trc)]
    table_size = 4 + 12 * len(tags)
    offset = 128 + table_size
    entries, data, seen = [], b"", {}
    for sig, body in tags:
        if body in seen:                                 # the three curves share one copy
            entries.append(struct.pack(">4sII", sig, *seen[body]))
            continue
        while (offset + len(data)) % 4:
            data += b"\0"
        at = offset + len(data)
        seen[body] = (at, len(body))
        entries.append(struct.pack(">4sII", sig, at, len(body)))
        data += body
    while len(data) % 4:
        data += b"\0"
    size = 128 + table_size + len(data)
    header = (struct.pack(">I", size) + b"lcms" + struct.pack(">I", 0x02100000) + b"mntr" + b"RGB " + b"XYZ "
              + struct.pack(">6H", 2026, 1, 1, 0, 0, 0) + b"acsp" + b"MSFT" + b"\0" * 4 + b"\0" * 8 + b"\0" * 8
              + struct.pack(">I", 0) + b"".join(_s15(v) for v in D50_XYZ) + b"LUNE" + b"\0" * 16 + b"\0" * 28)
    assert len(header) == 128
    return header + struct.pack(">I", len(tags)) + b"".join(entries) + data


def profile_path(key: str, folder: Path) -> Path:
    """The profile written once into `folder` (the data folder's 'profiles')."""
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / f"{key}.icc"
    if not p.exists():
        p.write_bytes(make_profile(key))
    return p


# --- the photo view ------------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _display_profile():
    try:
        return ImageCms.get_display_profile()
    except Exception:
        return None


@lru_cache(maxsize=4)
def display_transform(choice: str):
    """sRGB -> the monitor's profile, or None (off, or Windows has none set)."""
    if choice != "system":
        return None
    mon = _display_profile()
    if mon is None:
        return None
    return ImageCms.buildTransform(ImageCms.createProfile("sRGB"), mon, "RGB", "RGB",
                                   ImageCms.Intent.PERCEPTUAL)


def to_screen(img: Image.Image, choice: str) -> Image.Image:
    t = display_transform(choice)
    return ImageCms.applyTransform(img.convert("RGB"), t) if t is not None else img


@lru_cache(maxsize=4)
def _proof_transforms(proof_path: str, intent: str):
    srgb = ImageCms.createProfile("sRGB")
    target = ImageCms.getOpenProfile(proof_path)
    mode = target.profile.xcolor_space.strip() if hasattr(target.profile, "xcolor_space") else "RGB"
    mode = "CMYK" if mode == "CMYK" else "RGB"
    it = ImageCms.Intent.PERCEPTUAL if intent == "perceptual" else ImageCms.Intent.RELATIVE_COLORIMETRIC
    there = ImageCms.buildTransform(srgb, target, "RGB", mode, it)
    back = ImageCms.buildTransform(target, srgb, mode, "RGB", ImageCms.Intent.RELATIVE_COLORIMETRIC)
    exact = ImageCms.buildTransform(srgb, target, "RGB", mode, ImageCms.Intent.RELATIVE_COLORIMETRIC)
    return there, back, exact


def out_of_gamut(img: Image.Image, proof_path: str, tolerance: float = 6.0) -> np.ndarray:
    """True where a colour doesn't survive a trip into the profile and back."""
    _, back, exact = _proof_transforms(proof_path, "relative")
    rgb = img.convert("RGB")
    trip = ImageCms.applyTransform(ImageCms.applyTransform(rgb, exact), back)
    d = np.abs(np.asarray(rgb, np.int16) - np.asarray(trip, np.int16)).max(axis=2)
    return d > tolerance


def proof(img: Image.Image, proof_path: str, intent: str = "perceptual", gamut_warning: bool = True) -> Image.Image:
    """How `img` (sRGB) comes out in `proof_path`'s space, shown back in sRGB."""
    there, back, _ = _proof_transforms(proof_path, intent)
    out = ImageCms.applyTransform(ImageCms.applyTransform(img.convert("RGB"), there), back)
    if gamut_warning:
        a = np.array(out)
        a[out_of_gamut(img, proof_path)] = GAMUT_WARNING
        out = Image.fromarray(a, "RGB")
    return out
