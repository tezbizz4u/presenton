"""ASF theme-preset library: 1000+ deterministic colour/font themes.

A preset is a complete Presenton theme (16 colours + text font) that can be
applied on top of any bundled layout family. Presets are generated, not
stored, so the library costs no database space and is identical on every run.
"""
from __future__ import annotations

import colorsys
import re
from functools import lru_cache
from typing import Any
from urllib.parse import quote_plus

# (display name, hue 0-360). 72 hues → every ~5 degrees, named by nearest family.
_HUE_FAMILIES = [
    (0, "Crimson"), (8, "Scarlet"), (16, "Vermilion"), (24, "Coral"), (32, "Tangerine"),
    (40, "Amber"), (48, "Honey"), (56, "Gold"), (64, "Lemon"), (72, "Chartreuse"),
    (84, "Lime"), (96, "Fern"), (110, "Leaf"), (124, "Emerald"), (138, "Jade"),
    (150, "Mint"), (160, "Sea Green"), (170, "Teal"), (180, "Cyan"), (190, "Lagoon"),
    (200, "Sky"), (210, "Azure"), (220, "Cobalt"), (230, "Royal"), (240, "Indigo"),
    (250, "Iris"), (260, "Violet"), (270, "Amethyst"), (282, "Orchid"), (294, "Magenta"),
    (306, "Fuchsia"), (318, "Rose"), (330, "Raspberry"), (342, "Ruby"), (352, "Cherry"),
]

# 2 variants per family hue (hue shift) gives 70 hues; plus ASF clinical set below.
_FONTS = [
    "Lato", "Poppins", "Inter", "Merriweather", "Nunito", "Source Sans 3",
    "Playfair Display", "Roboto",
]

# mode: (bg_l, bg_s, card, text_l, primary_l, primary_s)
_MODES = {
    "Light": dict(bg_l=0.97, bg_s=0.30, card="#FFFFFF", dark=False, p_l=0.38, p_s=0.65),
    "Soft": dict(bg_l=0.92, bg_s=0.35, card=None, dark=False, p_l=0.42, p_s=0.55),
    "Dark": dict(bg_l=0.11, bg_s=0.35, card=None, dark=True, p_l=0.62, p_s=0.70),
    "Vivid": dict(bg_l=0.96, bg_s=0.55, card="#FFFFFF", dark=False, p_l=0.44, p_s=0.85),
}


def _hex(h: float, s: float, l: float) -> str:
    r, g, b = colorsys.hls_to_rgb((h % 360) / 360.0, min(max(l, 0), 1), min(max(s, 0), 1))
    return "#{:02X}{:02X}{:02X}".format(round(r * 255), round(g * 255), round(b * 255))


def _lum(color: str) -> float:
    vals = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in vals]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _readable_on(background: str, h: float, minimum: float) -> str:
    """Near-black / near-white text tinted with the hue that meets `minimum`."""
    for l in (0.12, 0.08, 0.04, 0.0) if _lum(background) > 0.4 else (0.92, 0.96, 1.0):
        candidate = _hex(h, 0.25 if 0 < l < 1 else 0, l)
        if contrast(candidate, background) >= minimum:
            return candidate
    return "#000000" if _lum(background) > 0.4 else "#FFFFFF"


def _build_colors(hue: float, mode: dict[str, Any]) -> dict[str, str]:
    background = _hex(hue, mode["bg_s"], mode["bg_l"])
    card = mode["card"] or (
        _hex(hue, mode["bg_s"], 0.17) if mode["dark"] else _hex(hue, mode["bg_s"], 0.98)
    )
    primary = _hex(hue, mode["p_s"], mode["p_l"])
    # Keep the brand colour readable against the background (>= 3:1 for UI shapes).
    step = 0.02
    p_l = mode["p_l"]
    while contrast(primary, background) < 3.0 and 0.05 < p_l < 0.95:
        p_l += step if mode["dark"] else -step
        primary = _hex(hue, mode["p_s"], p_l)
    def _best_text(color: str) -> str:
        dark_text, light_text = _hex(hue, 0.25, 0.08), "#FFFFFF"
        return dark_text if contrast(dark_text, color) >= contrast(light_text, color) else light_text

    primary_text = _best_text(primary)
    guard = 0
    while contrast(primary_text, primary) < 4.5 and guard < 40:
        guard += 1
        p_l += -0.02 if primary_text == "#FFFFFF" else 0.02
        p_l = min(max(p_l, 0.05), 0.95)
        primary = _hex(hue, mode["p_s"], p_l)
        primary_text = _best_text(primary)
    background_text = _readable_on(background, hue, 7.0)
    stroke = _hex(hue, mode["p_s"], max(0.2, min(0.8, p_l + (0.08 if mode["dark"] else -0.08))))
    # Chart palette: analogous + complementary accents around the brand hue.
    offsets = [0, 30, -30, 150, 180, 210, 60, -60, 120, 240]
    lightness = [p_l, p_l + 0.05, p_l - 0.05, p_l, p_l + 0.08, p_l - 0.08, p_l, p_l + 0.1, p_l - 0.1, p_l]
    graphs = {
        f"graph_{i}": _hex(hue + offsets[i], 0.65, min(0.8, max(0.28, lightness[i])))
        for i in range(10)
    }
    return {
        "primary": primary,
        "background": background,
        "card": card,
        "stroke": stroke,
        "primary_text": primary_text,
        "background_text": background_text,
        **graphs,
    }


def _font(name: str) -> dict[str, str]:
    return {
        "name": name,
        "url": f"https://fonts.googleapis.com/css2?family={quote_plus(name)}:wght@400;500;700&display=swap",
    }


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


@lru_cache(maxsize=1)
def all_theme_presets() -> tuple[dict[str, Any], ...]:
    presets: list[dict[str, Any]] = []
    for base_hue, family in _HUE_FAMILIES:
        for shift_index, shift in enumerate((0, 4)):
            hue = base_hue + shift
            label = family if shift == 0 else f"{family} Alt"
            for mode_name, mode in _MODES.items():
                colors = _build_colors(hue, mode)
                # Fonts rotate per (hue, mode) so a family shows several type styles.
                offset = base_hue // 8 + shift_index
                for font_name in (_FONTS[(offset + step) % len(_FONTS)] for step in (0, 2, 4, 6)):
                    name = f"{label} {mode_name} · {font_name}"
                    presets.append(
                        {
                            "id": _slug(name),
                            "name": name,
                            "family": label,
                            "mode": mode_name,
                            "font": font_name,
                            "theme": {"colors": colors, "fonts": {"textFont": _font(font_name)}},
                        }
                    )
    return tuple(presets)


def get_theme_preset(preset_id: str) -> dict[str, Any] | None:
    for preset in all_theme_presets():
        if preset["id"] == preset_id:
            return preset
    return None
