"""ASF Quality Gate for Bulk Create.

Pure checks (no database, no network) so they can be unit tested. The batch runner
feeds in one record per slide and gets back the slides that fail, with reasons. Only
those slides are repaired, never the whole deck.

Slide record keys: index, layout, notes (str), urls (list[str], real images only),
text (all visible text joined), title (str).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

NOTES_MIN_CHARS = 350
MAX_SAME_LAYOUT_IN_A_ROW = 2
SCORE_PASS = 85
MAX_FIX_ROUNDS = 2

PLACEHOLDER_RE = re.compile(
    r"(lorem ipsum|\bTBD\b|\bTODO\b|\bXXX+\b|\[(?:insert|add|your)[^\]]*\]|\{\{|\}\}|"
    r"\bundefined\b|\bplaceholder\b|\bsample text\b)",
    re.I,
)

ISSUE_LABELS = {
    "notes_short": "speaker note shorter than %d characters" % NOTES_MIN_CHARS,
    "dup_image": "same image as an earlier slide of this deck",
    "placeholder": "placeholder or empty text",
    "layout_run": "same layout used more than %d times in a row" % MAX_SAME_LAYOUT_IN_A_ROW,
}

FIX_PROMPTS = {
    "notes_short": (
        "Keep every word of the slide text and every image exactly as it is. Rewrite ONLY the "
        "speaker note: plain text, at least 500 characters, teaching the why behind the points, "
        "adding one practical clinical or classroom example, and never repeating the slide text."
    ),
    "dup_image": (
        "Keep the slide text and layout. Replace the picture with a clearly different, relevant "
        "subject for this slide's topic: write a new image prompt describing a different scene "
        "or object than before."
    ),
    "placeholder": (
        "Replace any placeholder, empty or meaningless text on this slide with real, specific, "
        "accurate content for this slide's topic. Keep the layout and the speaker note quality."
    ),
    "layout_run": (
        "Keep all the content of this slide but present it with a different layout than the "
        "slides next to it."
    ),
}


def check_slides(slides: List[Dict[str, Any]]) -> Dict[int, List[str]]:
    """Return {slide index: [issue codes]} for every slide that fails a rule."""
    issues: Dict[int, List[str]] = {}
    seen_urls: set[str] = set()
    run = 0
    prev_layout = None
    ordered = sorted(slides, key=lambda s: s["index"])
    for pos, s in enumerate(ordered):
        found: List[str] = []
        if len(str(s.get("notes") or "").strip()) < NOTES_MIN_CHARS:
            found.append("notes_short")
        urls = [u for u in s.get("urls", []) if u]
        if any(u in seen_urls for u in urls):
            found.append("dup_image")
        seen_urls.update(urls)
        text = str(s.get("text") or "")
        if PLACEHOLDER_RE.search(text) or (pos > 0 and not str(s.get("title") or "").strip()):
            found.append("placeholder")
        run = run + 1 if s.get("layout") == prev_layout else 1
        prev_layout = s.get("layout")
        if run > MAX_SAME_LAYOUT_IN_A_ROW:
            found.append("layout_run")
        if found:
            issues[s["index"]] = found
    return issues


def score(total_slides: int, issues: Dict[int, List[str]]) -> int:
    """100 minus the share of slides that still fail (each failing slide counts once)."""
    if total_slides <= 0:
        return 0
    return max(0, round(100 * (1 - len(issues) / total_slides)))


def fix_prompt(codes: List[str]) -> str:
    return " ".join(FIX_PROMPTS[c] for c in codes if c in FIX_PROMPTS)


def describe(issues: Dict[int, List[str]]) -> List[str]:
    return [
        "slide %d: %s" % (i + 1, ", ".join(ISSUE_LABELS[c] for c in codes))
        for i, codes in sorted(issues.items())
    ]
