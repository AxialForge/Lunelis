"""
The built-in filters: a curated starting set. Each is just adjustments
(never geometry), scaled by the filter's amount - so every one of them is
fine-tunable afterwards, and "B&W Classic at 50 %" is a half-desaturated
photo, not a special mode.
"""
from __future__ import annotations

BUILTIN: dict[str, dict[str, float]] = {
    "Vivid": {"vibrance": 35, "saturation": 10, "contrast": 20, "shadows": 10},
    "B&W Classic": {"saturation": -100, "contrast": 25, "blacks": -10},
    "Filmic": {"contrast": 15, "fade": 30, "saturation": -15, "temp": 8, "highlights": -20},
    "Warm": {"temp": 30, "tint": 5, "vibrance": 10},
    "Cool": {"temp": -30, "tint": -3, "vibrance": 5},
    "Matte": {"fade": 45, "contrast": -15, "saturation": -10, "shadows": 10},
    "Punch": {"contrast": 35, "whites": 15, "blacks": -20, "vibrance": 20, "sharpen": 25},
    "Soft": {"contrast": -20, "highlights": -20, "shadows": 15, "fade": 10},
}
