"""File sizes in words, one way everywhere (0.48: small files showed "0 MB")."""
from __future__ import annotations


def human(n: int | float | None) -> str:
    """1 -> '1 byte', 2_300 -> '2 KB', 4_500_000 -> '4.5 MB', 2.3e9 -> '2.3 GB', 1.2e12 -> '1.20 TB'."""
    n = int(n or 0)
    if n < 1000:
        return f"{n:,} byte{'s' if n != 1 else ''}"
    if n < 1_000_000:
        return f"{n / 1e3:,.0f} KB"
    if n < 100_000_000:
        return f"{n / 1e6:,.1f} MB"
    if n < 1_000_000_000:
        return f"{n / 1e6:,.0f} MB"
    if n < 1_000_000_000_000:
        return f"{n / 1e9:,.1f} GB"
    return f"{n / 1e12:,.2f} TB"
