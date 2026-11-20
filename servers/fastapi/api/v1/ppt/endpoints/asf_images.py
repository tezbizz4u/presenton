"""ASF image sources: several stock/web/AI image sources side by side.

Presenton's own settings hold a single active image provider. ASF wants Pexels,
Pixabay and Unsplash keys saved together, a licence-aware web search and AI image
generation with styles/formats, all available from one picker. Keys are stored on
the server (APP_DATA_DIRECTORY/asf_image_keys.json, mode 600) and never returned
in full.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import socket
import tempfile
import uuid
from io import BytesIO
from typing import Any, Literal, Optional
from urllib.parse import urlparse

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Query
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel
from yarl import URL
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.image_asset import ImageAsset
from services.database import get_async_session
from utils.asset_directory_utils import (
    filesystem_image_path_to_app_data_url,
    get_images_directory,
)
from utils.get_env import get_app_data_directory_env

LOGGER = logging.getLogger(__name__)
ASF_IMAGES_ROUTER = APIRouter(prefix="/images-plus", tags=["Images (ASF)"])

SOURCES = ("pexels", "pixabay", "unsplash")
ENV_NAMES = {
    "pexels": "PEXELS_API_KEY",
    "pixabay": "PIXABAY_API_KEY",
    "unsplash": "UNSPLASH_ACCESS_KEY",
}
UTM = "utm_source=asf_presenton&utm_medium=referral"
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=40)

# ---------------------------------------------------------------- key storage

_KEYS_LOCK = asyncio.Lock()


def _keys_path() -> str:
    base = get_app_data_directory_env() or "/app_data"
    return os.path.join(base, "asf_image_keys.json")


def _read_keys() -> dict[str, str]:
    try:
        with open(_keys_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return {k: str(v) for k, v in data.items() if k in SOURCES and v}
    except (OSError, ValueError):
        return {}


def _write_keys(keys: dict[str, str]) -> None:
    path = _keys_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".asf_keys_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(keys, handle)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _key_for(source: str) -> str:
    stored = _read_keys().get(source, "").strip()
    return stored or (os.getenv(ENV_NAMES[source]) or "").strip()


def _mask(value: str) -> str:
    return "" if not value else "•" * 8 + value[-4:]


class KeysUpdate(BaseModel):
    pexels: Optional[str] = None  # None = leave unchanged, "" = remove
    pixabay: Optional[str] = None
    unsplash: Optional[str] = None


@ASF_IMAGES_ROUTER.get("/keys")
async def get_keys():
    return {s: {"configured": bool(_key_for(s)), "hint": _mask(_key_for(s))} for s in SOURCES}


@ASF_IMAGES_ROUTER.put("/keys")
async def put_keys(body: KeysUpdate):
    async with _KEYS_LOCK:
        keys = _read_keys()
        for source in SOURCES:
            value = getattr(body, source)
            if value is None:
                continue
            value = value.strip()
            if value and (len(value) < 8 or len(value) > 200 or any(c.isspace() for c in value)):
                raise HTTPException(status_code=422, detail=f"{source}: key looks invalid")
            if value:
                keys[source] = value
            else:
                keys.pop(source, None)
        _write_keys(keys)
    return await get_keys()


# --------------------------------------------------------------- stock search


def _credit(source: str, author: str, author_url: str, page_url: str, licence: str) -> dict:
    return {
        "source": source,
        "author": author,
        "author_url": author_url,
        "page_url": page_url,
        "license": licence,
    }


async def _get_json(session: aiohttp.ClientSession, url: str, retries: int = 0, **kwargs) -> Any:
    last: HTTPException | None = None
    for attempt in range(retries + 1):
        try:
            async with session.get(url, timeout=HTTP_TIMEOUT, **kwargs) as response:
                if response.status in (401, 403):
                    raise HTTPException(status_code=401, detail="The key was rejected (invalid or no access)")
                if response.status == 429:
                    raise HTTPException(status_code=429, detail="Rate limit reached, try again later")
                if response.status in (502, 503, 504):
                    last = HTTPException(status_code=504, detail="The image source is busy, please try again")
                    continue
                if response.status != 200:
                    raise HTTPException(status_code=502, detail=f"Source answered {response.status}")
                return await response.json(content_type=None)
        except (asyncio.TimeoutError, aiohttp.ClientError):
            last = HTTPException(status_code=504, detail="The image source is slow right now, please try again")
    raise last or HTTPException(status_code=502, detail="Search failed")


def _orientation(value: str, source: str) -> Optional[str]:
    table = {
        "pexels": {"landscape": "landscape", "portrait": "portrait", "square": "square"},
        "pixabay": {"landscape": "horizontal", "portrait": "vertical", "square": None},
        "unsplash": {"landscape": "landscape", "portrait": "portrait", "square": "squarish"},
    }
    return table[source].get(value)


async def _search_pexels(session, query: str, limit: int, orientation: str, page: int, key_override: str | None = None):
    key = key_override or _key_for("pexels")
    if not key:
        raise HTTPException(status_code=400, detail="Pexels key is not set")
    params: dict[str, Any] = {"query": query, "per_page": limit, "page": page}
    if _orientation(orientation, "pexels"):
        params["orientation"] = _orientation(orientation, "pexels")
    data = await _get_json(session, "https://api.pexels.com/v1/search", params=params, headers={"Authorization": key})
    out = []
    for photo in data.get("photos", []):
        src = photo.get("src", {})
        full = src.get("large2x") or src.get("large") or src.get("original")
        if not full:
            continue
        out.append(
            {
                "id": f"pexels-{photo.get('id')}",
                "source": "pexels",
                "thumb": src.get("medium") or full,
                "url": full,
                "width": photo.get("width"),
                "height": photo.get("height"),
                "alt": photo.get("alt") or "",
                "credit": _credit(
                    "pexels",
                    photo.get("photographer", ""),
                    photo.get("photographer_url", ""),
                    photo.get("url", ""),
                    "Pexels License",
                ),
            }
        )
    return out


async def _search_pixabay(session, query: str, limit: int, orientation: str, page: int, key_override: str | None = None):
    key = key_override or _key_for("pixabay")
    if not key:
        raise HTTPException(status_code=400, detail="Pixabay key is not set")
    params: dict[str, Any] = {
        "key": key,
        "q": query[:99],
        "image_type": "photo",
        "per_page": max(3, limit),
        "page": page,
        "safesearch": "true",
    }
    if _orientation(orientation, "pixabay"):
        params["orientation"] = _orientation(orientation, "pixabay")
    data = await _get_json(session, "https://pixabay.com/api/", params=params)
    out = []
    for hit in data.get("hits", [])[:limit]:
        full = hit.get("largeImageURL") or hit.get("webformatURL")
        if not full:
            continue
        out.append(
            {
                "id": f"pixabay-{hit.get('id')}",
                "source": "pixabay",
                "thumb": hit.get("webformatURL") or full,
                "url": full,
                "width": hit.get("imageWidth"),
                "height": hit.get("imageHeight"),
                "alt": hit.get("tags") or "",
                "credit": _credit(
                    "pixabay",
                    hit.get("user", ""),
                    f"https://pixabay.com/users/{hit.get('user', '')}-{hit.get('user_id', '')}/",
                    hit.get("pageURL", ""),
                    "Pixabay Content License",
                ),
            }
        )
    return out


async def _search_unsplash(session, query: str, limit: int, orientation: str, page: int, key_override: str | None = None):
    key = key_override or _key_for("unsplash")
    if not key:
        raise HTTPException(status_code=400, detail="Unsplash key is not set")
    params: dict[str, Any] = {"query": query, "per_page": min(limit, 30), "page": page}
    if _orientation(orientation, "unsplash"):
        params["orientation"] = _orientation(orientation, "unsplash")
    data = await _get_json(
        session,
        "https://api.unsplash.com/search/photos",
        params=params,
        headers={"Authorization": f"Client-ID {key}", "Accept-Version": "v1"},
    )
    out = []
    for photo in data.get("results", []):
        urls = photo.get("urls", {})
        full = urls.get("regular") or urls.get("full")
        if not full:
            continue
        user = photo.get("user", {})
        out.append(
            {
                "id": f"unsplash-{photo.get('id')}",
                "source": "unsplash",
                "thumb": urls.get("small") or full,
                "url": full,
                "width": photo.get("width"),
                "height": photo.get("height"),
                "alt": photo.get("alt_description") or "",
                "download_location": photo.get("links", {}).get("download_location"),
                "credit": _credit(
                    "unsplash",
                    user.get("name", ""),
                    f"{user.get('links', {}).get('html', 'https://unsplash.com')}?{UTM}",
                    f"{photo.get('links', {}).get('html', 'https://unsplash.com')}?{UTM}",
                    "Unsplash License",
                ),
            }
        )
    return out


_SEARCHERS = {"pexels": _search_pexels, "pixabay": _search_pixabay, "unsplash": _search_unsplash}


@ASF_IMAGES_ROUTER.get("/stock")
async def search_stock(
    query: str = Query(min_length=1, max_length=200),
    sources: str = "pexels,pixabay,unsplash",
    orientation: Literal["any", "landscape", "portrait", "square"] = "any",
    per_source: int = Query(default=8, ge=1, le=24),
    page: int = Query(default=1, ge=1, le=20),
):
    wanted = [s for s in (x.strip() for x in sources.split(",")) if s in SOURCES]
    if not wanted:
        raise HTTPException(status_code=422, detail="No valid source selected")
    async with aiohttp.ClientSession(trust_env=True) as session:
        outcomes = await asyncio.gather(
            *(_SEARCHERS[s](session, query.strip(), per_source, orientation, page) for s in wanted),
            return_exceptions=True,
        )
    results: list[dict] = []
    errors: dict[str, str] = {}
    for source, outcome in zip(wanted, outcomes):
        if isinstance(outcome, HTTPException):
            errors[source] = str(outcome.detail)
        elif isinstance(outcome, Exception):
            LOGGER.warning("asf stock search %s failed: %s", source, type(outcome).__name__)
            errors[source] = "Search failed"
        else:
            results.append(outcome)
    # interleave so that every source shows up near the top
    merged: list[dict] = []
    for i in range(max((len(r) for r in results), default=0)):
        for r in results:
            if i < len(r):
                merged.append(r[i])
    return {"results": merged, "errors": errors}


class KeyTest(BaseModel):
    source: Literal["pexels", "pixabay", "unsplash"]
    key: Optional[str] = None  # test a key before saving it


@ASF_IMAGES_ROUTER.post("/keys/test")
async def test_key(body: KeyTest):
    override = body.key.strip() if body.key and body.key.strip() else None
    async with aiohttp.ClientSession(trust_env=True) as session:
        found = await _SEARCHERS[body.source](session, "nature", 3, "any", 1, override)
    return {"ok": True, "found": len(found)}


# ------------------------------------------------------- licence-safe web search

OPENVERSE_URL = "https://api.openverse.org/v1/images/"


@ASF_IMAGES_ROUTER.get("/web")
async def search_web(
    query: str = Query(min_length=1, max_length=200),
    orientation: Literal["any", "landscape", "portrait", "square"] = "any",
    limit: int = Query(default=24, ge=1, le=40),
    page: int = Query(default=1, ge=1, le=20),
):
    """Internet search restricted to openly licensed images (Openverse: Wikimedia
    Commons, Flickr CC, museums, ...). Random web images are not offered because
    their reuse rights are unknown."""
    params: dict[str, Any] = {
        "q": query.strip(),
        "page_size": limit,
        "page": page,
        "license_type": "commercial,modification",
        "mature": "false",
    }
    aspect = {"landscape": "wide", "portrait": "tall", "square": "square"}.get(orientation)
    if aspect:
        params["aspect_ratio"] = aspect
    async with aiohttp.ClientSession(trust_env=True) as session:
        data = await _get_json(session, OPENVERSE_URL, retries=1, params=params, headers={"User-Agent": "asf-presenton/1.0"})
    results = []
    for item in data.get("results", []):
        url = item.get("url")
        if not url or (item.get("filetype") or "").lower() not in ("jpg", "jpeg", "png", "webp", ""):
            continue
        licence = (item.get("license") or "").upper()
        version = item.get("license_version") or ""
        label = f"CC {licence} {version}".strip() if licence not in ("", "CC0", "PDM") else (licence or "Open licence")
        results.append(
            {
                "id": f"web-{item.get('id')}",
                "source": "web",
                "thumb": item.get("thumbnail") or url,
                "url": url,
                "width": item.get("width"),
                "height": item.get("height"),
                "alt": item.get("title") or "",
                "credit": _credit(
                    item.get("source") or "web",
                    item.get("creator") or "Unknown",
                    item.get("creator_url") or "",
                    item.get("foreign_landing_url") or "",
                    label,
                ),
                "attribution": item.get("attribution") or "",
            }
        )
    return {"results": results, "errors": {}}


# ------------------------------------------------------------------ importing


async def _assert_public_host(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise HTTPException(status_code=422, detail="Only https image links are allowed")
    host = parsed.hostname
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, parsed.port or 443, type=socket.SOCK_STREAM)
    except OSError:
        raise HTTPException(status_code=422, detail="Image host could not be resolved")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise HTTPException(status_code=422, detail="Image host is not a public address")


async def _download_image(url: str) -> bytes:
    current = url
    async with aiohttp.ClientSession(trust_env=True) as session:
        for _ in range(4):
            await _assert_public_host(current)
            async with session.get(
                current,
                timeout=HTTP_TIMEOUT,
                allow_redirects=False,
                headers={"User-Agent": "asf-presenton/1.0"},
            ) as response:
                if response.status in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    if not location:
                        break
                    current = str(response.url.join(URL(location)))
                    continue
                if response.status != 200:
                    raise HTTPException(status_code=502, detail=f"Image download failed ({response.status})")
                declared = response.headers.get("Content-Type", "")
                if not declared.lower().startswith("image/"):
                    raise HTTPException(status_code=422, detail="The link is not an image")
                chunks, size = [], 0
                async for chunk in response.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > MAX_DOWNLOAD_BYTES:
                        raise HTTPException(status_code=413, detail="Image is larger than 20 MB")
                    chunks.append(chunk)
                return b"".join(chunks)
    raise HTTPException(status_code=502, detail="Too many redirects")


def _store_image_bytes(data: bytes) -> str:
    try:
        with Image.open(BytesIO(data)) as probe:
            fmt = (probe.format or "").upper()
            probe.verify()
    except (UnidentifiedImageError, OSError, ValueError):
        raise HTTPException(status_code=422, detail="The file is not a valid image")
    ext = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif"}.get(fmt)
    if not ext:
        raise HTTPException(status_code=422, detail=f"Image format {fmt or 'unknown'} is not supported")
    path = os.path.join(get_images_directory(), f"{uuid.uuid4()}.{ext}")
    with open(path, "wb") as handle:
        handle.write(data)
    return path


class ImportBody(BaseModel):
    url: str
    source: str = "web"
    credit: Optional[dict] = None
    download_location: Optional[str] = None
    prompt: Optional[str] = None


@ASF_IMAGES_ROUTER.post("/import")
async def import_image(body: ImportBody, sql_session: AsyncSession = Depends(get_async_session)):
    """Copy a chosen stock/web image onto the server so slides and exports keep working."""
    data = await _download_image(body.url)
    path = _store_image_bytes(data)
    credit = body.credit if isinstance(body.credit, dict) else {}
    asset = ImageAsset(
        path=path,
        is_uploaded=False,
        extras={"prompt": body.prompt or "", "source": body.source, "credit": credit},
    )
    sql_session.add(asset)
    await sql_session.commit()
    if body.source == "unsplash" and body.download_location and _key_for("unsplash"):
        # Unsplash API rule: report the download.
        try:
            await _assert_public_host(body.download_location)
            async with aiohttp.ClientSession(trust_env=True) as session:
                await session.get(
                    body.download_location,
                    headers={"Authorization": f"Client-ID {_key_for('unsplash')}"},
                    timeout=HTTP_TIMEOUT,
                )
        except Exception:  # noqa: BLE001 - best effort only
            LOGGER.warning("unsplash download ping failed")
    return {"id": str(asset.id), "url": filesystem_image_path_to_app_data_url(path), "credit": credit}


# ------------------------------------------------------------ AI generation

AI_MODELS = {
    "quality": {"id": "gemini-3-pro-image", "label": "Nano Banana Pro (best quality, slower)"},
    "fast": {"id": "gemini-3.1-flash-image", "label": "Gemini Flash Image (fast, cheaper)"},
}
AI_STYLES = {
    "photo": ("Photorealistic", "photorealistic photograph, natural light, sharp focus, high detail"),
    "illustration": ("Illustration", "clean modern digital illustration, soft shading, friendly colours"),
    "flat": ("Flat vector", "flat vector illustration, simple geometric shapes, limited colour palette"),
    "3d": ("3D render", "polished 3D render, soft studio lighting, smooth materials"),
    "watercolor": ("Watercolor", "delicate watercolor painting, soft washes, paper texture"),
    "line": ("Line art", "minimal black line art on a plain background, uniform stroke"),
    "diagram": ("Diagram look", "clear educational diagram style, simple labelled-free shapes, high contrast, plain background"),
    "minimal": ("Minimal", "minimalist composition, lots of empty space, calm muted colours"),
}
AI_ASPECTS = {"16:9": (16, 9), "4:3": (4, 3), "1:1": (1, 1), "3:4": (3, 4), "9:16": (9, 16)}


def _genai_client():
    from google import genai  # imported lazily: heavy and only needed here

    api_key = (os.getenv("GOOGLE_API_KEY") or "").strip()
    if api_key:
        return genai.Client(api_key=api_key)
    project = (os.getenv("VERTEX_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT") or "").strip() or None
    location = (os.getenv("VERTEX_LOCATION") or "global").strip()
    return genai.Client(vertexai=True, project=project, location=location)


def _generate_one_sync(prompt: str, model: str, aspect: str) -> bytes:
    from google.genai import types

    client = _genai_client()
    config = types.GenerateContentConfig(
        response_modalities=["IMAGE"],
        image_config=types.ImageConfig(aspect_ratio=aspect),
    )
    response = client.models.generate_content(model=model, contents=prompt, config=config)
    parts = getattr(response, "parts", None)
    if not parts and getattr(response, "candidates", None):
        content = getattr(response.candidates[0], "content", None)
        parts = getattr(content, "parts", None) if content else None
    for part in parts or []:
        inline = getattr(part, "inline_data", None)
        data = getattr(inline, "data", None) if inline else None
        if data:
            if isinstance(data, str):
                import base64

                data = base64.b64decode(data)
            return data
    raise RuntimeError("The model returned no image")


class GenerateBody(BaseModel):
    prompt: str
    style: str = "photo"
    aspect: str = "16:9"
    model: Literal["quality", "fast"] = "fast"
    count: int = 2
    no_text: bool = True


@ASF_IMAGES_ROUTER.get("/ai-options")
async def ai_options():
    return {
        "models": [{"key": k, **v} for k, v in AI_MODELS.items()],
        "styles": [{"key": k, "label": v[0]} for k, v in AI_STYLES.items()],
        "aspects": list(AI_ASPECTS.keys()),
    }


@ASF_IMAGES_ROUTER.post("/generate")
async def generate_ai_images(body: GenerateBody, sql_session: AsyncSession = Depends(get_async_session)):
    prompt = body.prompt.strip()
    if not prompt or len(prompt) > 1500:
        raise HTTPException(status_code=422, detail="Describe the image in 1-1500 characters")
    if body.style not in AI_STYLES or body.aspect not in AI_ASPECTS:
        raise HTTPException(status_code=422, detail="Unknown style or aspect ratio")
    count = max(1, min(body.count, 4))
    model = AI_MODELS[body.model]
    full_prompt = f"{prompt}. Style: {AI_STYLES[body.style][1]}."
    if body.no_text:
        full_prompt += " No text, captions, logos or watermarks in the image."

    semaphore = asyncio.Semaphore(2)

    async def one() -> bytes:
        async with semaphore:
            return await asyncio.wait_for(
                asyncio.to_thread(_generate_one_sync, full_prompt, model["id"], body.aspect), timeout=150
            )

    outcomes = await asyncio.gather(*(one() for _ in range(count)), return_exceptions=True)
    images, failures = [], []
    for outcome in outcomes:
        if isinstance(outcome, Exception):
            failures.append(outcome)
            continue
        path = _store_image_bytes(outcome)
        credit = {
            "source": "ai",
            "author": model["label"].split(" (")[0],
            "license": "AI-generated image",
            "model": model["id"],
        }
        asset = ImageAsset(
            path=path,
            is_uploaded=False,
            extras={"prompt": prompt, "source": "ai", "style": body.style, "aspect": body.aspect, "credit": credit},
        )
        sql_session.add(asset)
        images.append((asset, path, credit))
    if not images:
        LOGGER.warning("asf ai generate failed: %s", [type(f).__name__ for f in failures])
        detail = str(failures[0])[:300] if failures else "Image generation failed"
        raise HTTPException(status_code=502, detail=f"Image generation failed: {detail}")
    await sql_session.commit()
    return {
        "results": [
            {"id": str(a.id), "url": filesystem_image_path_to_app_data_url(p), "credit": c, "source": "ai"}
            for a, p, c in images
        ],
        "failed": len(failures),
    }
