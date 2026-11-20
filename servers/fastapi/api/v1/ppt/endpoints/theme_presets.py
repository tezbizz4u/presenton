from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from templates.theme_presets import all_theme_presets, get_theme_preset


THEME_PRESETS_ROUTER = APIRouter(prefix="/theme-presets", tags=["Theme presets"])


def _summary(preset: dict) -> dict:
    colors = preset["theme"]["colors"]
    return {
        "id": preset["id"],
        "name": preset["name"],
        "family": preset["family"],
        "mode": preset["mode"],
        "trend": preset["trend"],
        "font": preset["font"],
        "swatches": [
            colors["background"],
            colors["card"],
            colors["primary"],
            colors["graph_1"],
            colors["background_text"],
        ],
    }


@THEME_PRESETS_ROUTER.get("")
async def list_theme_presets(
    q: Optional[str] = Query(default=None, description="Search in name"),
    mode: Optional[str] = Query(default=None, description="Light, Soft, Dark or Vivid"),
    font: Optional[str] = None,
    trend: Optional[str] = Query(default=None, description="e.g. Pantone 2026, Natural green"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=60, ge=1, le=500),
):
    presets = all_theme_presets()
    if q:
        needle = q.casefold()
        presets = tuple(p for p in presets if needle in p["name"].casefold())
    if mode:
        presets = tuple(p for p in presets if p["mode"].casefold() == mode.casefold())
    if font:
        presets = tuple(p for p in presets if p["font"].casefold() == font.casefold())
    if trend:
        presets = tuple(p for p in presets if trend.casefold() in p["trend"].casefold())
    return {
        "total": len(presets),
        "items": [_summary(p) for p in presets[offset : offset + limit]],
    }


@THEME_PRESETS_ROUTER.get("/{preset_id}")
async def get_theme_preset_detail(preset_id: str):
    preset = get_theme_preset(preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail="Theme preset not found")
    return {**_summary(preset), "theme": preset["theme"]}
