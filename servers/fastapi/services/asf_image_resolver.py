"""ASF smart image resolver used while slides are generated.

The slide model writes a long, descriptive image prompt for every image slot. Using
that text verbatim as a stock-search query gives random or duplicate pictures, and
decorative "badge icon" slots get filled with unrelated photos. This resolver:

1. returns a transparent picture for purely decorative slots (icons, badges, rings),
2. condenses the prompt into a short keyword query,
3. looks for clinical images on Wikimedia Commons first (licence-safe, real scans),
4. then searches Unsplash, Pexels and Pixabay together,
5. never uses the same picture twice in one presentation,
6. draws concepts/diagrams/anatomy with AI (capped per deck) and falls back to AI when no
   stock picture is found.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from typing import Any, Optional

import aiohttp
from PIL import Image

from models.sql.image_asset import ImageAsset

LOGGER = logging.getLogger(__name__)

DECOR_RE = re.compile(
    r"\b(icons?|badges?|rings?|emblems?|glyphs?|pictograms?|bullets?|dividers?|"
    r"circular outline|concentric)\b",
    re.I,
)
MEDICAL_IMAGING_RE = re.compile(
    r"\b(ultrasound|sonograph\w*|sonogram|x-?ray|radiograph\w*|mri|ct scan|"
    r"ecg|ekg|histolog\w*|microscop\w*|biopsy|endoscop\w*|fundus|retina\w*|"
    r"dermoscop\w*|echocardiogra\w*|mammogra\w*)\b",
    re.I,
)
STOPWORDS = set(
    """a an the of in on with and or for to from by at as is are was were be being been
    showing shows show displaying displays featuring features depicting depicts illustrating
    illustrates presenting visualization visualisation image images photo photos photograph
    picture pictures shot view views scene high resolution quality professional modern clean
    close closeup up detailed realistic photorealistic cinematic stylized stylised minimalist
    minimal vibrant soft subtle background backdrop style styled shallow depth field lighting
    light bright warm cool tone tones color colour colors colours deep rich beautiful
    stunning sharp focus bokeh composition standing sitting holding using next beside
    against over under into onto within across between near around while that this these
    those its their his her it who which where when well also both each other such
    grayscale monochrome crisp elegant sleek contemporary state art central peripheral
    clearly distinct distinctive prominently using""".split()
)
ANATOMY_RE = re.compile(
    r"\b(ovary|ovaries|ovarian|follic\w*|polycystic|pcos|uterus|uterine|endometri\w*|"
    r"liver|hepatic|steatosis|fatty|kidney|renal|heart|cardiac|thyroid|pancrea\w*|"
    r"adrenal|bladder|lung|pulmonary|brain|bone|breast|carotid|aorta|coronary|"
    r"cyst\w*|fibrosis|cirrhosis|plaque|retina\w*|skin|acanthosis|hirsutism|acne)\b",
    re.I,
)
PEOPLE_RE = re.compile(r"\b(patient|patients|case|woman|women|female|portrait|person|people)\b", re.I)
ILLUS_RE = re.compile(
    r"\b(illustrations?|diagrams?|infographics?|flow ?charts?|pathways?|mechanisms?|"
    r"cycles?|schematics?|conceptual|concepts?|cross-?sections?|anatomical|framework|"
    r"model of|process|timeline|metaphor|abstract)\b",
    re.I,
)
INDIAN_SCENE_RE = re.compile(
    r"\b(patient|patients|woman|women|female|girl|girls|mother|mothers|family|families|"
    r"people|person|persons|community|village\w*|health ?workers?|asha|anm|anganwadi|"
    r"doctor|doctors|nurse|nurses|counsel\w*|consultation|clinic|teacher|student|students|"
    r"couple|household|kitchen|meal|diet|food|thali|yoga|exercise|walking|classroom|"
    r"training|workshop|camp|screening)\b",
    re.I,
)
INDIAN_DIRECTION = (
    " Set in India: the people are Indian (South Asian features, natural skin tones, "
    "authentic everyday Indian clothing such as saree, salwar kameez or kurta for women), "
    "in a realistic Indian setting (Indian home, community health centre, clinic or village). "
    "Respectful, candid documentary look; no Western stock-photo look."
)
CLINIC_QUERY = "indian doctor patient consultation clinic"
AI_MAX_PER_DECK = int(os.getenv("ASF_AI_MAX_PER_DECK", "10"))
_AI_SEM = asyncio.Semaphore(int(os.getenv("ASF_AI_CONCURRENCY", "4")))
AI_ENABLED = os.getenv("ASF_AI_IMAGES", "1") != "0"
KEEP_ALWAYS = {"india", "indian", "ayush", "ayurveda", "yoga"}
GENERIC_FOR_COMMONS = {"medical", "monitor", "screen", "machine", "device", "clinical"}

_TRANSPARENT_PATH: Optional[str] = None


def is_decorative(prompt: str) -> bool:
    """True for slots that only want a small decorative shape, not a picture."""
    return bool(DECOR_RE.search(prompt or ""))


def condense_query(prompt: str, max_words: int = 6) -> str:
    words = re.findall(r"[A-Za-z][A-Za-z\-]+", prompt or "")
    picked: list[str] = []
    for word in words:
        low = word.lower().strip("-")
        if len(low) < 3 or (low in STOPWORDS and low not in KEEP_ALWAYS):
            continue
        if low not in (p.lower() for p in picked):
            picked.append(low)
        if len(picked) >= max_words:
            break
    return " ".join(picked)


def _orientation(target_size: tuple[float, float] | None) -> str:
    if not target_size:
        return "landscape"
    ratio = target_size[0] / target_size[1]
    if ratio >= 1.2:
        return "landscape"
    if ratio <= 0.83:
        return "portrait"
    return "square"


def _transparent_asset(images_directory: str) -> ImageAsset:
    global _TRANSPARENT_PATH
    if not _TRANSPARENT_PATH or not os.path.exists(_TRANSPARENT_PATH):
        path = os.path.join(images_directory, f"transparent-{uuid.uuid4()}.png")
        Image.new("RGBA", (16, 16), (0, 0, 0, 0)).save(path)
        _TRANSPARENT_PATH = path
    return ImageAsset(
        path=_TRANSPARENT_PATH,
        is_uploaded=False,
        extras={"source": "decorative", "note": "transparent placeholder for a decorative slot"},
    )


class AsfImageResolver:
    def __init__(self, images_directory: str):
        self.images_directory = images_directory
        self.used: set[str] = set()
        self.ai_count = 0

    async def resolve(
        self, prompt: str, target_size: tuple[float, float] | None
    ) -> Optional[ImageAsset]:
        """Return an ImageAsset, or None when the caller should use its own provider."""
        if is_decorative(prompt):
            return _transparent_asset(self.images_directory)

        # Imported here: asf_images pulls in the database layer.
        from api.v1.ppt.endpoints import asf_images as asf

        orientation = _orientation(target_size)
        query = condense_query(prompt)
        if not query:
            return None
        if (
            AI_ENABLED
            and ILLUS_RE.search(prompt)
            and not MEDICAL_IMAGING_RE.search(prompt)
        ):
            # Concepts, diagrams and mechanisms have no honest stock photo: draw them.
            ai = await self._ai_fallback(asf, prompt, orientation, kind="diagram")
            if ai is not None:
                return ai
        indian_scene = bool(INDIAN_SCENE_RE.search(prompt)) and not MEDICAL_IMAGING_RE.search(prompt)
        if indian_scene and AI_ENABLED:
            # Stock sites rarely have Indian subjects: draw the scene with Indian people.
            ai = await self._ai_fallback(asf, prompt, orientation, kind="indian")
            if ai is not None:
                return ai
        if indian_scene and "india" not in query.lower():
            query = f"indian {query}"
        short = " ".join(query.split()[:3])

        candidate = None
        try:
            if MEDICAL_IMAGING_RE.search(prompt):
                imaging = MEDICAL_IMAGING_RE.search(prompt).group(0).lower()
                body = [m.lower() for m in ANATOMY_RE.findall(prompt)]
                commons_query = " ".join(dict.fromkeys([imaging, *body][:4]))
                if not body:
                    commons_query = " ".join(
                        w for w in query.split() if w not in GENERIC_FOR_COMMONS
                    ) or query
                # Only accept a Commons file whose title names the scan type or the
                # organ; otherwise unrelated drawings (e.g. a knee) get picked.
                terms = {imaging, *body}
                stems = [t[:5] for t in terms if t]
                found = [
                    r
                    for r in await self._commons(asf, commons_query)
                    if any(st in (r.get("alt") or "").lower() for st in stems)
                ]
                candidate = await self._first_unused(found)
                if candidate is None:
                    # No licence-safe scan found: a neutral clinic photo beats a wrong one.
                    scan_q = f"{imaging} scan doctor"
                    candidate = await self._first_unused(
                        await self._stock(asf, scan_q, orientation)
                    )
            if candidate is None and PEOPLE_RE.search(prompt):
                # Case / patient slides: a consultation scene, not a random stock portrait.
                candidate = await self._first_unused(
                    await self._stock(asf, CLINIC_QUERY, orientation)
                )
            if candidate is None:
                for q in (query, short):
                    candidate = await self._first_unused(await self._stock(asf, q, orientation))
                    if candidate:
                        break
            if candidate is None and ANATOMY_RE.search(prompt):
                # AI-drawn anatomy as a photo is often wrong; draw a clean educational
                # illustration with the quality model instead, then fall back to a neutral
                # clinic photo, or no picture at all.
                ai = await self._ai_fallback(asf, prompt, orientation, kind="anatomy")
                if ai is not None:
                    return ai
                candidate = await self._first_unused(
                    await self._stock(asf, CLINIC_QUERY, orientation)
                )
                if candidate is None:
                    return _transparent_asset(self.images_directory)
            if candidate is not None:
                return await self._import(asf, candidate, prompt)
        except Exception as exc:  # noqa: BLE001 - never block slide generation
            LOGGER.warning("asf image resolver failed: %s", type(exc).__name__)

        return await self._ai_fallback(asf, prompt, orientation, kind="photo")

    async def _first_unused(self, results: list[dict]) -> Optional[dict]:
        for item in results:
            if item["id"] not in self.used and (item.get("width") or 1200) >= 800:
                self.used.add(item["id"])
                return item
        return None

    async def _commons(self, asf: Any, query: str) -> list[dict]:
        try:
            data = await asf.search_web(query=query, orientation="any", limit=10, page=1)
            return data.get("results", [])
        except Exception:  # noqa: BLE001
            return []

    async def _stock(self, asf: Any, query: str, orientation: str) -> list[dict]:
        sources = [s for s in ("unsplash", "pexels", "pixabay") if asf._key_for(s)]
        if not sources:
            return []
        async with aiohttp.ClientSession(trust_env=True) as session:
            outcomes = await asyncio.gather(
                *(asf._SEARCHERS[s](session, query, 8, orientation, 1) for s in sources),
                return_exceptions=True,
            )
        lists = [o for o in outcomes if isinstance(o, list)]
        merged: list[dict] = []
        for i in range(max((len(x) for x in lists), default=0)):
            for lst in lists:
                if i < len(lst):
                    merged.append(lst[i])
        return merged

    async def _import(self, asf: Any, item: dict, prompt: str) -> ImageAsset:
        data = await asf._download_image(item["url"])
        path = asf._store_image_bytes(data)
        return ImageAsset(
            path=path,
            is_uploaded=False,
            extras={
                "prompt": prompt,
                "source": item.get("source", "stock"),
                "credit": item.get("credit") or {},
                "stock_id": item["id"],
            },
        )

    async def _ai_fallback(
        self, asf: Any, prompt: str, orientation: str, kind: str = "photo"
    ) -> Optional[ImageAsset]:
        if not AI_ENABLED or self.ai_count >= AI_MAX_PER_DECK:
            return None
        aspect = {"landscape": "16:9", "portrait": "3:4", "square": "1:1"}[orientation]
        if kind == "anatomy":
            style = asf.AI_STYLES["diagram"][1]
            model = asf.AI_MODELS["quality"]
            extra = (
                " Anatomically accurate educational medical illustration of exactly the organ "
                "or structure described, centred, on a clean light background."
            )
        elif kind == "diagram":
            style = asf.AI_STYLES["flat"][1]
            model = asf.AI_MODELS["fast"]
            extra = " Simple, clear teaching visual with no labels."
        elif kind == "indian":
            style = asf.AI_STYLES["photo"][1]
            model = asf.AI_MODELS["fast"]
            extra = INDIAN_DIRECTION
        else:
            style = asf.AI_STYLES["photo"][1]
            model = asf.AI_MODELS["fast"]
            extra = ""
        full_prompt = (
            f"{prompt}.{extra} Style: {style}. "
            "No text, letters, numbers, captions, logos or watermarks in the image."
        )
        self.ai_count += 1
        data = None
        for attempt in range(3):  # 429 Too Many Requests: back off and retry
            try:
                async with _AI_SEM:
                    data = await asyncio.wait_for(
                        asyncio.to_thread(asf._generate_one_sync, full_prompt, model["id"], aspect),
                        timeout=150,
                    )
                break
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("asf ai image failed (%s, try %s): %s", kind, attempt + 1, type(exc).__name__)
                await asyncio.sleep(4 * (attempt + 1))
        if data is None:
            return None
        try:
            path = asf._store_image_bytes(data)
        except Exception:  # noqa: BLE001
            return None
        return ImageAsset(
            path=path,
            is_uploaded=False,
            extras={
                "prompt": prompt,
                "source": "ai",
                "credit": {
                    "source": "ai",
                    "author": "Gemini image (Vertex AI)",
                    "license": "AI-generated image",
                },
            },
        )
