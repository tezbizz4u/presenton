"""Blueprint text -> deck rows (dependency-free so it is easy to unit test)."""
import csv
import io
import json
import re
from typing import Any, Dict, List, Optional

MAX_BRIEF_CHARS = 8000


def _clip(text: str) -> str:
    return text.strip()[:MAX_BRIEF_CHARS]


def parse_blueprint(text: str, default_slides: int) -> List[Dict[str, Any]]:
    """Turn a pasted blueprint into deck rows.

    Accepted shapes (first match wins):
      1. JSON list of {name, brief, slides}
      2. CSV with a header containing name/title and brief/content columns
      3. Pipe lines:  name | slides | brief
      4. Markdown: each '## ' (else '# ') heading starts one deck; the text under it is the brief
    """
    stripped = text.strip()
    rows: List[Dict[str, Any]] = []

    if stripped.startswith("["):
        try:
            for item in json.loads(stripped):
                name = str(item.get("name") or item.get("title") or "").strip()
                brief = str(item.get("brief") or item.get("content") or "").strip()
                if name and brief:
                    rows.append(
                        {
                            "name": name[:160],
                            "brief": _clip(brief),
                            "slides": int(item.get("slides") or default_slides),
                        }
                    )
            if rows:
                return rows
        except (ValueError, AttributeError, TypeError):
            pass

    first_line = stripped.splitlines()[0].lower() if stripped else ""
    if "," in first_line and ("brief" in first_line or "content" in first_line):
        reader = csv.DictReader(io.StringIO(stripped))
        for rec in reader:
            low = {str(k).strip().lower(): (v or "").strip() for k, v in rec.items() if k}
            name = low.get("name") or low.get("title") or ""
            brief = low.get("brief") or low.get("content") or ""
            if name and brief:
                try:
                    n = int(low.get("slides") or default_slides)
                except ValueError:
                    n = default_slides
                rows.append({"name": name[:160], "brief": _clip(brief), "slides": n})
        if rows:
            return rows

    pipe_lines = [ln for ln in stripped.splitlines() if ln.count("|") >= 2]
    if pipe_lines and len(pipe_lines) >= max(1, len(stripped.splitlines()) // 2):
        for ln in pipe_lines:
            name, slides, brief = [p.strip() for p in ln.split("|", 2)]
            try:
                n = int(slides)
            except ValueError:
                n = default_slides
            if name and brief:
                rows.append({"name": name[:160], "brief": _clip(brief), "slides": n})
        if rows:
            return rows

    for marker in ("## ", "# "):
        sections: List[Dict[str, Any]] = []
        current: Optional[Dict[str, Any]] = None
        for ln in stripped.splitlines():
            if ln.startswith(marker) and not ln.startswith(marker + "#"):
                current = {"name": ln[len(marker):].strip()[:160], "body": []}
                sections.append(current)
            elif current is not None:
                current["body"].append(ln)
        for sec in sections:
            body = "\n".join(sec["body"]).strip()
            if sec["name"] and body:
                m = re.search(r"(\d{1,3})\s*slides?", body, re.IGNORECASE)
                n = int(m.group(1)) if m else default_slides
                rows.append(
                    {
                        "name": sec["name"],
                        "brief": _clip(f"{sec['name']}\n\n{body}"),
                        "slides": max(1, min(n, 200)),
                    }
                )
        if rows:
            return rows
    return rows
