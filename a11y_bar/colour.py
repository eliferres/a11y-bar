"""Colour parsing and the WCAG contrast arithmetic."""
from __future__ import annotations

import re
from typing import Dict, Optional, Tuple


def _channel(c: float) -> float:
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb: Tuple[int, int, int]) -> float:
    """WCAG relative luminance."""
    r, g, b = (_channel(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: Tuple[int, int, int], bg: Tuple[int, int, int]) -> float:
    hi, lo = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


NAMED_COLOURS = {"white": (255, 255, 255, 1.0), "black": (0, 0, 0, 1.0)}


def parse_color(value: Optional[str], variables: Dict[str, str]) -> Optional[Tuple[int, int, int, float]]:
    """(r, g, b, alpha) for a hex, rgb() or rgba() literal, following var()
    references and their fallbacks; None for anything else."""
    if value is None:
        return None
    v = value.strip().lower()
    hops = 0
    while v.startswith("var(") and hops < 5:
        inner = v[4:v.rfind(")")]
        name, _, fallback = inner.partition(",")
        v = (variables.get(name.strip()) or fallback).strip().lower()
        hops += 1
    m = re.fullmatch(r"#([0-9a-f]{3,8})", v)
    if m:
        h = m.group(1)
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        if len(h) not in (6, 8):
            return None
        alpha = int(h[6:8], 16) / 255.0 if len(h) == 8 else 1.0
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)
    m = re.fullmatch(r"rgba?\(([^)]*)\)", v)
    if m:
        parts = [p for p in re.split(r"[,\s/]+", m.group(1).strip()) if p]
        if len(parts) < 3:
            return None
        try:
            rgb = [round(float(p.rstrip("%")) * (2.55 if p.endswith("%") else 1)) for p in parts[:3]]
            alpha = 1.0
            if len(parts) > 3:
                alpha = float(parts[3].rstrip("%")) / (100.0 if parts[3].endswith("%") else 1.0)
        except ValueError:
            return None
        r, g, b = (max(0, min(255, c)) for c in rgb)
        return (r, g, b, alpha)
    return NAMED_COLOURS.get(v)


def over(fg: Tuple[int, int, int, float], bg: Tuple[int, int, int]) -> Tuple[int, int, int]:
    """A translucent foreground composited onto an opaque background."""
    a = fg[3]
    return (round(fg[0] * a + bg[0] * (1 - a)),
            round(fg[1] * a + bg[1] * (1 - a)),
            round(fg[2] * a + bg[2] * (1 - a)))
