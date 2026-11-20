"""ASF theme-preset library built from Color Hunt palettes.

Each Color Hunt palette is 4 colours. Every palette yields two presets (Light and
Dark), each a complete Presenton theme (16 colours) plus the original palette so
the frontend can recolour template slides (see recolorSlides.ts).

Palette source: https://colorhunt.co (palettes are public property per the
site's About page). Codes live in data/colorhunt_codes.txt, 24 hex chars each.
"""
from __future__ import annotations

import colorsys
from functools import lru_cache
from pathlib import Path
from typing import Any

_CODES_FILE = Path(__file__).parent / "data" / "colorhunt_codes.txt"

_HUE_FAMILIES = [
    (15, "Red"), (40, "Orange"), (65, "Yellow"), (150, "Green"),
    (190, "Teal"), (255, "Blue"), (290, "Purple"), (335, "Pink"), (361, "Red"),
]

_WHITE = (255, 255, 255)
_BLACK = (0, 0, 0)


def _to_rgb(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _to_hex(rgb: tuple[float, float, float]) -> str:
    r, g, b = (max(0, min(255, round(v))) for v in rgb)
    return f"#{r:02X}{g:02X}{b:02X}"


def _lum(color: str) -> float:
    lin = []
    for v in _to_rgb(color):
        s = v / 255
        lin.append(s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4)
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _hls(color: str) -> tuple[float, float, float]:
    r, g, b = (v / 255 for v in _to_rgb(color))
    return colorsys.rgb_to_hls(r, g, b)  # h, l, s in 0..1


def _from_hls(h: float, l: float, s: float) -> str:
    r, g, b = colorsys.hls_to_rgb(h, max(0.0, min(1.0, l)), max(0.0, min(1.0, s)))
    return _to_hex((r * 255, g * 255, b * 255))


def _mix(a: str, b: str, t: float) -> str:
    ra, rb = _to_rgb(a), _to_rgb(b)
    return _to_hex(tuple(ra[i] + (rb[i] - ra[i]) * t for i in range(3)))  # type: ignore[arg-type]


def _sat(color: str) -> float:
    return _hls(color)[2]


def _push(color: str, bg: str, minimum: float) -> str:
    """Move lightness away from `bg` until contrast >= minimum (or limits)."""
    if contrast(color, bg) >= minimum:
        return color
    h, l, s = _hls(color)
    direction = -1 if _lum(bg) > 0.35 else 1
    for _ in range(40):
        l += direction * 0.025
        candidate = _from_hls(h, l, s)
        if contrast(candidate, bg) >= minimum:
            return candidate
        if l <= 0 or l >= 1:
            break
    return "#111111" if direction < 0 else "#F5F5F5"


def _readable_on(bg: str, dark: str, light: str) -> str:
    best = dark if contrast(dark, bg) >= contrast(light, bg) else light
    if contrast(best, bg) >= 4.5:
        return best
    fallback = "#111111" if contrast("#111111", bg) >= contrast("#FFFFFF", bg) else "#FFFFFF"
    return fallback


def _family(color: str) -> str:
    h, l, s = _hls(color)
    if s < 0.12 or l > 0.94 or l < 0.06:
        return "Neutral"
    if 10 / 360 <= h < 50 / 360 and l < 0.5 and s < 0.8:
        return "Brown"
    deg = h * 360
    for limit, name in _HUE_FAMILIES:
        if deg < limit:
            return name
    return "Red"


def _style_tag(palette: list[str]) -> str:
    hls = [_hls(c) for c in palette]
    avg_l = sum(x[1] for x in hls) / 4
    avg_s = sum(x[2] for x in hls) / 4
    hues = [x[0] * 360 for x in hls if x[2] > 0.15]
    warm = bool(hues) and sum(1 for h in hues if h < 70 or h > 330) >= max(1, round(len(hues) * 0.75))
    cool = bool(hues) and sum(1 for h in hues if 150 <= h <= 280) >= max(1, round(len(hues) * 0.75))
    if avg_l < 0.3:
        return "Dark"
    if avg_s > 0.88 and 0.45 <= avg_l <= 0.7:
        return "Neon"
    if avg_l > 0.72:
        return "Pastel"
    if avg_s < 0.3:
        return "Muted"
    if warm and avg_l < 0.55 and avg_s < 0.6:
        return "Earthy"
    if avg_s > 0.6:
        return "Vivid"
    return "Warm" if warm else "Cool" if cool else "Balanced"


def _build_theme(palette: list[str], dark: bool) -> dict[str, Any]:
    p = sorted(palette, key=_lum)  # dark -> light
    if not dark:
        bg = p[3] if _lum(p[3]) >= 0.55 else _mix(p[3], "#FFFFFF", 0.7)
        card = _mix(bg, "#FFFFFF", 0.6)
        stroke = _mix(bg, p[0], 0.18)
        text = _push(_mix(p[0], "#000000", 0.35), bg, 7.0)
    else:
        bg = p[0] if _lum(p[0]) <= 0.12 else _mix(p[0], "#000000", 0.65)
        card = _mix(bg, "#FFFFFF", 0.08)
        stroke = _mix(bg, "#FFFFFF", 0.18)
        text = _push(_mix(p[3], "#FFFFFF", 0.8), bg, 7.0)

    by_sat = sorted(p, key=_sat, reverse=True)
    primary = _push(by_sat[0], bg, 3.0)
    primary_text = _readable_on(
        primary, _mix(p[0], "#000000", 0.5), _mix(p[3], "#FFFFFF", 0.75)
    )

    base = [_push(c, bg, 2.0) for c in by_sat]
    graph = [primary] + [c for c in base if c != primary]
    for i in range(6):
        a, b = base[i % 4], base[(i + 1) % 4]
        mixed = _mix(a, b, 0.5)
        h, l, s = _hls(mixed)
        mixed = _from_hls(h, l + (0.1 if i % 2 else -0.1), s)
        graph.append(_push(mixed, bg, 2.0))
    graph = graph[:10]

    colors = {
        "primary": primary,
        "background": bg,
        "card": card,
        "stroke": stroke,
        "primary_text": primary_text,
        "background_text": text,
    }
    for i, c in enumerate(graph):
        colors[f"graph_{i}"] = c
    return {"colors": colors, "palette": p, "mode": "dark" if dark else "light"}


@lru_cache(maxsize=1)
def _palettes() -> tuple[tuple[str, tuple[str, ...]], ...]:
    out: list[tuple[str, tuple[str, ...]]] = []
    if not _CODES_FILE.exists():
        return ()
    for line in _CODES_FILE.read_text().split():
        code = line.strip().lower()
        if len(code) != 24 or any(ch not in "0123456789abcdef" for ch in code):
            continue
        out.append((code, tuple(f"#{code[i:i + 6].upper()}" for i in range(0, 24, 6))))
    return tuple(out)


@lru_cache(maxsize=1)
def _index() -> tuple[tuple[dict[str, Any], ...], dict[str, dict[str, Any]]]:
    presets: list[dict[str, Any]] = []
    for number, (code, palette) in enumerate(_palettes(), start=1):
        palette_list = list(palette)
        tag = _style_tag(palette_list)
        family = _family(max(palette_list, key=_sat))
        for dark in (False, True):
            mode = "Dark" if dark else "Light"
            theme = _build_theme(palette_list, dark)
            presets.append(
                {
                    "id": f"ch-{code}-{mode.lower()}",
                    "name": f"{family} {tag} #{number:04d}",
                    "family": family,
                    "mode": mode,
                    "trend": tag,
                    "font": "Template font",
                    "code": code,
                    "theme": theme,
                }
            )
    return tuple(presets), {p["id"]: p for p in presets}


def all_theme_presets() -> tuple[dict[str, Any], ...]:
    return _index()[0]


def get_theme_preset(preset_id: str) -> dict[str, Any] | None:
    return _index()[1].get(preset_id)
