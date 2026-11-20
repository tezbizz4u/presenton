"""Recolour template slides to an ASF (Color Hunt) theme preset.

Template slides (``slide.ui``) carry literal hex colours in ``font.color``,
``fill.color``, ``stroke.color``, border ``*.color`` and chart ``*_color`` /
``colors`` fields; the editor draws them on a canvas, so the colours have to be
changed in the data itself (this also makes PPTX/PDF export follow).

Mapping rules (same idea for every template):
* neutrals (white/grey/black) follow a ramp text -> card, so light stays light and
  dark stays dark; with a Dark preset the theme text is light and the card dark,
  which inverts the whole slide consistently;
* the slide-sized background fill becomes the theme background;
* chromatic colours are grouped by hue and assigned to the palette accents, keeping
  lightness relationships, then pushed until they still contrast with the colour
  their usual partner (white or black text) maps to;
* chart series colours use the theme graph colours;
* shadows, images and photo content are untouched.

The original value of every changed colour is stored in
``slide.properties["asf_recolor_orig"]`` so a theme can be reverted.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from typing import Any, Iterable

from templates.theme_presets import _from_hls, _hls, _lum, _mix, _to_hex, _to_rgb, contrast

_HEX = re.compile(r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")
_SNAPSHOT_KEY = "asf_recolor_orig"
_SLIDE_W, _SLIDE_H = 1280, 720


_ART_PREFIX = "/app_data/templates/"
_ART_DIR = "_recolored"


def _walk_art(node: Any, path: tuple, out: list[tuple[tuple, str]]) -> None:
    """Template-shipped decorative pictures (waves, textured backdrops)."""
    if isinstance(node, dict):
        data = node.get("data")
        if (
            node.get("type") == "image"
            and isinstance(data, str)
            and data.startswith(_ART_PREFIX)
            and f"/{_ART_DIR}/" not in data
            and ".." not in data
        ):
            out.append(((*path, "data"), data))
        for key, value in node.items():
            if isinstance(value, (dict, list)):
                _walk_art(value, (*path, key), out)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            if isinstance(value, (dict, list)):
                _walk_art(value, (*path, i), out)


_SVG_HEX = re.compile(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})(?![0-9a-zA-Z])")
_SVG_RGB = re.compile(r"rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)")
_SVG_NAMED = {"white": "#FFFFFF", "black": "#000000"}


_CACHE_LIMIT = 800 * 1024 * 1024


def _prune_cache(out_dir: str) -> None:
    """Keep the recoloured-picture cache bounded (oldest files go first)."""
    try:
        files = [
            (e.stat().st_mtime, e.stat().st_size, e.path)
            for e in os.scandir(out_dir)
            if e.is_file() and not e.name.endswith(".tmp")
        ]
        total = sum(size for _, size, _ in files)
        if total <= _CACHE_LIMIT:
            return
        for _, size, path in sorted(files):
            os.remove(path)
            total -= size
            if total <= _CACHE_LIMIT * 0.75:
                break
    except OSError:
        pass


def _recolor_svg(source: str, base: str, out_dir: str, mapper: "_Mapper") -> str | None:
    try:
        with open(source, "r", encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError):
        return None
    if len(text) > 2_000_000:
        return None

    def hex_sub(match: re.Match) -> str:
        raw = match.group(1)
        if len(raw) == 3:
            raw = "".join(ch * 2 for ch in raw)
        return mapper.map("#" + raw.upper())

    def rgb_sub(match: re.Match) -> str:
        r, g, b = (min(255, int(match.group(i))) for i in (1, 2, 3))
        return mapper.map(f"#{r:02X}{g:02X}{b:02X}")

    def attr_sub(match: re.Match) -> str:
        key, quote, value = match.group(1), match.group(2), match.group(3)
        return f"{key}={quote}{mapper.map(_SVG_NAMED[value.lower()])}{quote}"

    out = _SVG_HEX.sub(hex_sub, text)
    out = _SVG_RGB.sub(rgb_sub, out)
    out = re.sub(
        r"""\b(fill|stroke|stop-color)=(["'])(white|black)\2""", attr_sub, out, flags=re.IGNORECASE
    )
    # fill/stroke omitted on a shape means black in SVG; leave those alone (no visible change risk)
    if out == text:
        return None
    name = hashlib.sha1(out.encode("utf-8")).hexdigest()[:20] + ".svg"
    path = os.path.join(out_dir, name)
    if not os.path.isfile(path):
        os.makedirs(out_dir, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(out)
        os.replace(tmp, path)
    return f"/app_data/templates/{_ART_DIR}/{name}"


def _recolor_art(src_url: str, mapper: "_Mapper") -> str | None:
    """Hue-rotate a template picture towards `target_hex`; returns the new /app_data url.

    Greys/blacks stay as they are, only coloured pixels move, lightness and alpha are kept.
    Results are cached by (source, target hue) so every preset reuses the same file.
    """
    root = os.getenv("APP_DATA_DIRECTORY")
    if not root:
        return None
    rel = src_url[len("/app_data/") :]
    source = os.path.realpath(os.path.join(root, rel))
    base = os.path.realpath(os.path.join(root, "templates"))
    if not source.startswith(base + os.sep) or not os.path.isfile(source):
        return None
    out_dir = os.path.join(base, _ART_DIR)
    if source.lower().endswith(".svg"):
        return _recolor_svg(source, base, out_dir, mapper)
    target_hue = round(_hue(mapper.primary) / 4) * 4  # 90 buckets keep the cache small
    name = hashlib.sha1(f"{rel}|{target_hue}".encode()).hexdigest()[:20]
    for ext in (".png", ".jpg"):
        if os.path.isfile(os.path.join(out_dir, name + ext)):
            return f"/app_data/templates/{_ART_DIR}/{name}{ext}"
    try:
        import numpy as np
        from PIL import Image

        image = Image.open(source)
        rgba = image.convert("RGBA")
        alpha = np.asarray(rgba.getchannel("A"), dtype=np.float32) / 255.0
        hsv = np.asarray(rgba.convert("RGB").convert("HSV"), dtype=np.float32).copy()
        weight = (hsv[..., 1] / 255.0) * (hsv[..., 2] / 255.0) * alpha
        coloured = weight > 0.15
        if coloured.mean() < 0.02:
            return None  # essentially grey art: nothing to recolour
        angles = hsv[..., 0][coloured] / 255.0 * 2 * np.pi
        w = weight[coloured]
        dominant = (np.arctan2((np.sin(angles) * w).sum(), (np.cos(angles) * w).sum()) % (2 * np.pi)) / (2 * np.pi) * 360
        delta = (target_hue - dominant) / 360.0 * 255.0
        hsv[..., 0] = (hsv[..., 0] + delta) % 255.0
        rgb = Image.fromarray(hsv.astype("uint8"), "HSV").convert("RGB")
        result = rgb.convert("RGBA")
        result.putalpha(rgba.getchannel("A"))
        os.makedirs(out_dir, exist_ok=True)
        opaque = bool(alpha.min() >= 0.999)
        ext = ".jpg" if opaque else ".png"
        out_path = os.path.join(out_dir, name + ext)
        tmp = out_path + ".tmp"
        if opaque:
            rgb.save(tmp, format="JPEG", quality=90)
        else:
            result.save(tmp, format="PNG")
        os.replace(tmp, out_path)
        _prune_cache(out_dir)
        return f"/app_data/templates/{_ART_DIR}/{name}{ext}"
    except Exception:
        return None


def _is_color_key(key: str) -> bool:
    return (key == "color" or key.endswith("_color")) and "shadow" not in key


def _chroma(color: str) -> float:
    r, g, b = _to_rgb(color)
    return (max(r, g, b) - min(r, g, b)) / 255


def _lstar(color: str) -> float:
    y = _lum(color)
    return (903.3 * y if y <= 0.008856 else 116 * (y ** (1 / 3)) - 16) / 100


def _hue(color: str) -> float:
    return _hls(color)[0] * 360


def _hue_distance(a: float, b: float) -> float:
    d = abs(a - b) % 360
    return 360 - d if d > 180 else d


def _ensure_contrast(color: str, other: str, minimum: float) -> str:
    if contrast(color, other) >= minimum:
        return color
    h, l, s = _hls(color)
    direction = -1 if _lum(other) > 0.35 else 1
    for i in range(1, 41):
        candidate = _from_hls(h, l + direction * i * 0.025, s)
        if contrast(candidate, other) >= minimum:
            return candidate
    return "#111111" if direction < 0 else "#F5F5F5"


def _walk(
    node: Any,
    path: tuple,
    out: list[tuple[tuple, str, bool, bool]],
    bg_fill: bool = False,
) -> None:
    """Collect (path, hex, is_background, is_series_color) for every colour leaf."""
    if isinstance(node, dict):
        size = node.get("size")
        is_bg_element = (
            isinstance(size, dict)
            and isinstance(node.get("fill"), dict)
            and (size.get("width") or 0) >= _SLIDE_W * 0.92
            and (size.get("height") or 0) >= _SLIDE_H * 0.92
        )
        for key, value in node.items():
            if isinstance(value, str) and _HEX.match(value) and _is_color_key(str(key)):
                out.append(((*path, key), value, bool(bg_fill and key == "color"), False))
            elif key == "colors" and isinstance(value, list):
                for i, item in enumerate(value):
                    if isinstance(item, str) and _HEX.match(item):
                        out.append(((*path, key, i), item, False, True))
            elif isinstance(value, (dict, list)):
                _walk(value, (*path, key), out, bg_fill=bool(key == "fill" and is_bg_element))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            if isinstance(value, (dict, list)):
                _walk(value, (*path, i), out)


def _get(root: Any, path: Iterable):
    node = root
    for key in path:
        node = node[key]
    return node


def _set(root: Any, path: tuple, value: str) -> None:
    parent = _get(root, path[:-1])
    parent[path[-1]] = value


def _split_alpha(color: str) -> tuple[str, str]:
    return color[:7], color[7:]


class _Mapper:
    def __init__(
        self,
        theme: dict[str, Any],
        usage: dict[str, int],
        native_dark: bool = False,
        freeze_neutrals: bool = False,
    ):
        self.native_dark = native_dark
        self.freeze_neutrals = freeze_neutrals
        colors = theme["colors"]
        self.bg = colors["background"].upper()
        self.card = colors["card"].upper()
        self.text = colors["background_text"].upper()
        self.primary = colors["primary"].upper()
        self.graph = [colors[f"graph_{i}"].upper() for i in range(10) if f"graph_{i}" in colors]
        palette = [c.upper() for c in theme.get("palette", [])]

        accents: list[str] = []

        def push(c: str) -> None:
            if _chroma(c) < 0.1:
                return
            r1 = _to_rgb(c)
            for x in accents:
                r2 = _to_rgb(x)
                if abs(r1[0] - r2[0]) + abs(r1[1] - r2[1]) + abs(r1[2] - r2[2]) < 24:
                    return
            accents.append(c)

        push(self.primary)
        for c in sorted(palette, key=lambda x: _hls(x)[2], reverse=True):
            push(c)
        self.accents = accents or [self.primary]

        # hue clusters of chromatic source colours, weighted by usage
        clusters: list[dict[str, Any]] = []
        for color, weight in sorted(usage.items(), key=lambda kv: kv[1], reverse=True):
            if _chroma(color) < 0.12:
                continue
            h = _hue(color)
            found = next((c for c in clusters if _hue_distance(c["hue"], h) <= 35), None)
            if found:
                found["weight"] += weight
            else:
                clusters.append({"hue": h, "weight": weight, "repl": _hls(color)[1]})
        clusters.sort(key=lambda c: c["weight"], reverse=True)
        for i, cluster in enumerate(clusters):
            cluster["accent"] = self.accents[i % len(self.accents)]
        self.clusters = clusters
        self._cache: dict[tuple[str, bool], str] = {}
        self.white_partner = self.neutral("#FFFFFF")
        self.black_partner = self.neutral("#000000")

    def neutral(self, color: str) -> str:
        if self.freeze_neutrals:
            return color
        t = max(0.0, min(1.0, _lstar(color)))
        # light-native slide: white -> card, black -> text; dark-native: the reverse,
        # so the polarity of the slide is kept whatever the preset mode is
        if self.native_dark:
            return _mix(self.card, self.text, t)
        return _mix(self.text, self.card, t)

    def map(self, hex_color: str, is_background: bool = False) -> str:
        base, alpha = _split_alpha(hex_color)
        key = (base.upper(), is_background)
        cached = self._cache.get(key)
        if cached is None:
            cached = self._compute(base.upper(), is_background)
            self._cache[key] = cached
        return cached + alpha

    def _compute(self, color: str, is_background: bool) -> str:
        if is_background:
            return self.bg
        if _chroma(color) < 0.12:
            return self.neutral(color)
        h, l, _ = _hls(color)
        cluster = next(
            (c for c in self.clusters if _hue_distance(c["hue"], h * 360) <= 35),
            self.clusters[0] if self.clusters else None,
        )
        if cluster is None:
            return self.primary
        ah, al, as_ = _hls(cluster["accent"])
        out = _from_hls(ah, max(0.04, min(0.96, al + (l - cluster["repl"]))), as_)
        star = _lstar(color)
        if star < 0.5:
            out = _ensure_contrast(out, self.white_partner, 4.5)
        elif star > 0.7:
            out = _ensure_contrast(out, self.black_partner, 4.5)
        return out


def revert_slide(slide: Any) -> None:
    """Undo a previous recolour of this slide (if one was recorded)."""
    props = slide.properties if isinstance(slide.properties, dict) else None
    snapshot = props.get(_SNAPSHOT_KEY) if props else None
    if props is not None and _SNAPSHOT_KEY in props and not snapshot:
        slide.properties = {k: v for k, v in props.items() if k != _SNAPSHOT_KEY}
        return
    if not snapshot or not isinstance(slide.ui, dict):
        return
    ui = copy.deepcopy(slide.ui)
    for raw_path, original in snapshot.items():
        try:
            path = tuple(json.loads(raw_path))
            parent = _get(ui, path[:-1])
            if isinstance(parent, (dict, list)):
                _set(ui, path, original)
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    slide.ui = ui
    new_props = {k: v for k, v in props.items() if k != _SNAPSHOT_KEY}
    slide.properties = new_props


def recolor_slide(slide: Any, theme: dict[str, Any]) -> int:
    """Recolour one slide's ui to `theme` ({'colors':..., 'palette':...}); returns changed count."""
    revert_slide(slide)
    if not isinstance(slide.ui, dict) or not theme.get("palette"):
        return 0
    ui = copy.deepcopy(slide.ui)
    entries: list[tuple[tuple, str, bool, bool]] = []
    _walk(ui, (), entries)

    usage: dict[str, int] = {}
    for _, value, _, is_series in entries:
        if not is_series:
            base = value[:7].upper()
            usage[base] = usage.get(base, 0) + 1
    has_bg_fill = any(is_bg for _, _, is_bg, _ in entries)
    if has_bg_fill:
        bg_values = [v for _, v, is_bg, _ in entries if is_bg]
        native_dark = _lstar(bg_values[0]) < 0.5
        freeze = False
    else:
        fonts = [
            _lstar(v)
            for path, v, _, series in entries
            if not series and "font" in path and _chroma(v) < 0.12
        ]
        native_dark = bool(fonts) and sum(fonts) / len(fonts) > 0.6
        # background is a picture/gradient we cannot recolour: keep text polarity
        freeze = native_dark
    mapper = _Mapper(theme, usage, native_dark, freeze)

    snapshot: dict[str, str] = {}
    for path, value, is_background, is_series in entries:
        if is_series and mapper.graph:
            index = path[-1] if isinstance(path[-1], int) else 0
            new_value = mapper.graph[index % len(mapper.graph)] + value[7:]
        else:
            new_value = mapper.map(value, is_background)
        if new_value.upper() != value.upper():
            snapshot[json.dumps(list(path))] = value
            _set(ui, path, new_value)

    art: list[tuple[tuple, str]] = []
    _walk_art(ui, (), art)
    for path, src in art:
        new_src = _recolor_art(src, mapper)
        if new_src and new_src != src:
            snapshot[json.dumps(list(path))] = src
            _set(ui, path, new_src)

    if not snapshot:
        return 0
    slide.ui = ui
    props = dict(slide.properties) if isinstance(slide.properties, dict) else {}
    props[_SNAPSHOT_KEY] = snapshot
    slide.properties = props
    return len(snapshot)


def apply_theme_to_slides(slides: Iterable[Any], theme: dict[str, Any] | None) -> None:
    """`theme` is a stored presentation theme dict; non-preset themes revert the slides."""
    data = None
    if isinstance(theme, dict) and theme.get("source") == "preset":
        candidate = theme.get("data")
        if isinstance(candidate, dict) and candidate.get("palette") and candidate.get("colors"):
            data = candidate
    for slide in slides:
        if data is not None:
            recolor_slide(slide, data)
        else:
            revert_slide(slide)


def _preset_data(theme: dict[str, Any] | None) -> dict[str, Any] | None:
    if isinstance(theme, dict) and theme.get("source") == "preset":
        candidate = theme.get("data")
        if isinstance(candidate, dict) and candidate.get("palette") and candidate.get("colors"):
            return candidate
    return None


def ensure_theme_on_new_slides(slides: Iterable[Any], theme: dict[str, Any] | None) -> int:
    """Recolour only slides that carry no recolour snapshot yet (new / template-swapped
    slides added in the editor). Already recoloured slides are left alone."""
    data = _preset_data(theme)
    if data is None:
        return 0
    count = 0
    for slide in slides:
        props = slide.properties if isinstance(slide.properties, dict) else {}
        if _SNAPSHOT_KEY in props:
            continue
        count += recolor_slide(slide, data)
    return count


def forget_snapshot(slide: Any) -> None:
    """Drop the snapshot without touching ui (used when ui was replaced by a fresh layout)."""
    props = slide.properties if isinstance(slide.properties, dict) else None
    if props and _SNAPSHOT_KEY in props:
        slide.properties = {k: v for k, v in props.items() if k != _SNAPSHOT_KEY}
