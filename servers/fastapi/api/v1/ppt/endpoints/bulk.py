"""Bulk Create: many decks from one blueprint, same pipeline as the single-deck flow.

Every deck is produced by the exact code path behind /presentation/generate/async
(_run_generate_presentation_task), so fonts, icons, images, speaker notes and the
Sources rules are identical to a deck made in the UI. This module only adds:
  * a blueprint parser (markdown / pipe lines / CSV / JSON -> deck rows)
  * a bounded-parallel batch runner that lives on the server (survives a closed browser)
  * per-deck quality checks, a cross-deck duplicate report, CSV report and ZIP download
"""

import asyncio
import csv
import io
import json
import logging
import os
import re
import time
import uuid
import zipfile
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from api.v1.ppt.endpoints.presentation import (
    ASYNC_TASK_TYPE_PRESENTATION_GENERATE,
    _requested_slide_count,
    _run_generate_presentation_task,
    build_export_cookie_header,
    check_if_api_request_is_valid,
    presentation_task_progress_data,
)
from api.v1.auth.context import (
    get_current_owner_id,
    reset_current_owner_id,
    set_current_owner_id,
)
from api.v1.ppt.endpoints.slide import edit_slide
from enums.async_task_status import AsyncTaskStatus
from models.generate_presentation_request import GeneratePresentationRequest
from models.sql.async_task import AsyncTaskModel
from models.sql.folder import FolderModel
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services import bulk_quality_gate as gate
from utils.export_utils import export_presentation
from services.bulk_blueprint import parse_blueprint
from services.database import async_session_maker

LOGGER = logging.getLogger(__name__)

BULK_ROUTER = APIRouter(prefix="/bulk", tags=["Bulk Create"])

MAX_JOBS_PER_BATCH = 60
MAX_PARALLEL = 12
MAX_BRIEF_CHARS = 8000
NOTES_MIN_CHARS = 300
PILOT_FROM_DECKS = 12
MAX_DECK_RETRIES = 2
_FIX_SEM = asyncio.Semaphore(8)  # global cap on concurrent slide repairs (LLM rate limits)

# batch_id -> batch dict (also mirrored to disk so a finished batch can be re-opened)
_BATCHES: Dict[str, Dict[str, Any]] = {}
_RUNNERS: Dict[str, asyncio.Task] = {}


def _bulk_dir() -> str:
    path = os.path.join(os.getenv("APP_DATA_DIRECTORY", "/app_data"), "bulk")
    os.makedirs(path, exist_ok=True)
    return path


def _persist(batch: Dict[str, Any]) -> None:
    try:
        slim = {k: v for k, v in batch.items() if not k.startswith("_")}
        with open(os.path.join(_bulk_dir(), f"{batch['id']}.json"), "w") as fh:
            json.dump(slim, fh, default=str)
    except Exception:  # noqa: BLE001 - persistence is best effort
        LOGGER.exception("bulk: could not persist batch %s", batch.get("id"))


def _load_batch(batch_id: str) -> Dict[str, Any]:
    if batch_id in _BATCHES:
        return _BATCHES[batch_id]
    if not re.fullmatch(r"[0-9a-f]{12}", batch_id):
        raise HTTPException(404, "Batch not found")
    path = os.path.join(_bulk_dir(), f"{batch_id}.json")
    if not os.path.exists(path):
        raise HTTPException(404, "Batch not found")
    with open(path) as fh:
        batch = json.load(fh)
    # A batch loaded from disk has no live runner: anything unfinished was interrupted.
    for job in batch["jobs"]:
        if job["status"] in ("queued", "running"):
            job["status"] = "error"
            job["error"] = "Interrupted by a server restart; use Retry failed"
    _BATCHES[batch_id] = batch
    return batch


# --------------------------------------------------------------------------- parser


class BlueprintParseRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2_000_000)
    default_slides: int = Field(default=25, ge=1, le=200)


class BulkJob(BaseModel):
    name: str = Field(..., min_length=1, max_length=160)
    brief: str = Field(..., min_length=1, max_length=MAX_BRIEF_CHARS)
    slides: int = Field(default=25, ge=1, le=200)
    template: Optional[str] = Field(default=None, max_length=80)


@BULK_ROUTER.post("/parse")
async def parse_blueprint_endpoint(body: BlueprintParseRequest):
    rows = parse_blueprint(body.text, body.default_slides)
    if not rows:
        raise HTTPException(
            422,
            "No decks found. Use '## Heading' sections, 'name | slides | brief' lines, CSV or JSON.",
        )
    return {"jobs": rows[:MAX_JOBS_PER_BATCH], "truncated": len(rows) > MAX_JOBS_PER_BATCH}


# ----------------------------------------------------------------------- batch runner


class BulkStartRequest(BaseModel):
    course_name: str = Field(default="", max_length=160)
    audience: str = Field(default="", max_length=300)
    jobs: List[BulkJob] = Field(..., min_length=1, max_length=MAX_JOBS_PER_BATCH)
    template: str = "momentum"
    verbosity: Literal["concise", "standard", "text-heavy"] = "standard"
    parallel: int = Field(default=10, ge=1, le=MAX_PARALLEL)
    export_as: Literal["pptx", "pdf"] = "pptx"
    folder_id: Optional[uuid.UUID] = None
    theme_preset: Optional[str] = None
    language: Optional[str] = None
    include_title_slide: bool = True
    instructions: str = Field(default="", max_length=2000)
    clinical_review: bool = True


def _consistency_instructions(req: BulkStartRequest) -> str:
    parts = []
    if req.course_name:
        parts.append(f"This deck belongs to the course '{req.course_name}'.")
    if req.audience:
        parts.append(f"Audience: {req.audience}.")
    parts.append(
        "Use the same terminology, abbreviations and Sources format as every other deck "
        "of this course; do not repeat the same slide title or the same example twice."
    )
    if req.instructions:
        parts.append(req.instructions)
    return " ".join(parts)


def _walk_urls(obj: Any, out: List[str]) -> None:
    if isinstance(obj, dict):
        for key, val in obj.items():
            if key in ("__image_url__", "image_url", "url") and isinstance(val, str):
                out.append(val)
            else:
                _walk_urls(val, out)
    elif isinstance(obj, list):
        for val in obj:
            _walk_urls(val, out)


def _slide_title(content: Any) -> str:
    if isinstance(content, dict):
        for key in ("title", "heading", "headline"):
            val = content.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip().lower()
    return ""


async def _quality(presentation_id: uuid.UUID) -> Dict[str, Any]:
    async with async_session_maker() as session:
        result = await session.scalars(
            select(SlideModel)
            .where(SlideModel.presentation == presentation_id)
            .order_by(SlideModel.index)
        )
        slides = list(result)
    notes = [len(str(s.speaker_note or "")) for s in slides]
    urls: List[str] = []
    titles: List[str] = []
    layouts: List[str] = []
    for s in slides:
        _walk_urls(s.content, urls)
        titles.append(_slide_title(s.content))
        layouts.append(s.layout)
    real = [u for u in urls if "transparent" not in u and "/static/" not in u]
    max_layout_run = 0
    run = 0
    prev = None
    for lay in layouts:
        run = run + 1 if lay == prev else 1
        prev = lay
        max_layout_run = max(max_layout_run, run)
    return {
        "slides": len(slides),
        "notes_min": min(notes) if notes else 0,
        "notes_short": sum(1 for n in notes if n < NOTES_MIN_CHARS),
        "images": len(real),
        "duplicate_images": len(real) - len(set(real)),
        "layout_repeat_max": max_layout_run,
        "distinct_layouts": len(set(layouts)),
        "_image_urls": sorted(set(real)),
        "_titles": [t for t in titles if t],
    }


def _walk_strings(obj: Any, out: List[str]) -> None:
    if isinstance(obj, dict):
        for key, val in obj.items():
            if str(key).startswith("__") or key in ("url", "image_url"):
                continue
            _walk_strings(val, out)
    elif isinstance(obj, list):
        for val in obj:
            _walk_strings(val, out)
    elif isinstance(obj, str):
        out.append(obj)


async def _load_slides(presentation_id: uuid.UUID) -> List[SlideModel]:
    async with async_session_maker() as session:
        result = await session.scalars(
            select(SlideModel)
            .where(SlideModel.presentation == presentation_id)
            .order_by(SlideModel.index)
        )
        return list(result)


def _slide_records(slides: List[SlideModel]) -> List[Dict[str, Any]]:
    records = []
    for s in slides:
        urls: List[str] = []
        _walk_urls(s.content, urls)
        strings: List[str] = []
        _walk_strings(s.content, strings)
        records.append(
            {
                "index": s.index,
                "layout": s.layout,
                "notes": s.speaker_note or "",
                "urls": [u for u in urls if "transparent" not in u and "/static/" not in u],
                "text": " ".join(strings),
                "title": _slide_title(s.content),
            }
        )
    return records


async def _run_gate(batch: Dict[str, Any], job: Dict[str, Any], presentation_id: uuid.UUID) -> bool:
    """Check every slide; repair ONLY failing slides (max 2 rounds). Returns True if anything changed."""
    fixed = 0
    rounds = 0
    slides = await _load_slides(presentation_id)
    issues = gate.check_slides(_slide_records(slides))
    token = set_current_owner_id(batch["_owner_id"])
    try:
        while issues and rounds < gate.MAX_FIX_ROUNDS:
            rounds += 1
            by_index = {s.index: s for s in slides}

            async def _fix(idx: int, codes: List[str]) -> int:
                target = by_index.get(idx)
                if target is None:
                    return 0
                try:
                    async with _FIX_SEM, async_session_maker() as session:
                        await edit_slide(
                            id=target.id,
                            prompt=gate.fix_prompt(codes),
                            sql_session=session,
                        )
                    return 1
                except Exception:  # noqa: BLE001 - a failed repair leaves the slide flagged
                    LOGGER.exception("bulk gate: repair failed slide %s of %s", idx + 1, job["name"])
                    return 0

            results = await asyncio.gather(*[_fix(i, c) for i, c in sorted(issues.items())])
            fixed += sum(results)
            slides = await _load_slides(presentation_id)
            issues = gate.check_slides(_slide_records(slides))
    finally:
        reset_current_owner_id(token)
    sc = gate.score(len(slides), issues)
    job["gate"] = {
        "score": sc,
        "status": "PASS" if (not issues and sc >= gate.SCORE_PASS) else "NEEDS FIX",
        "fix_rounds": rounds,
        "slides_fixed": fixed,
        "remaining": gate.describe(issues),
    }
    return fixed > 0


async def _run_one(batch: Dict[str, Any], job: Dict[str, Any], sem: asyncio.Semaphore) -> None:
    """Run one deck; transient provider errors (429/400 under load) are retried with backoff."""
    while True:
        await _run_one_attempt(batch, job, sem)
        if not job.pop("_retry", False):
            return
        job["attempts"] = job.get("attempts", 0) + 1
        job["status"] = "queued"
        job["error"] = f"Retrying ({job['attempts']}/{MAX_DECK_RETRIES}) after a provider error"
        _persist(batch)
        await asyncio.sleep(20 * job["attempts"])


async def _run_one_attempt(batch: Dict[str, Any], job: Dict[str, Any], sem: asyncio.Semaphore) -> None:
    async with sem:
        if batch.get("cancelled"):
            job["status"] = "cancelled"
            return
        job["status"] = "running"
        job["started_at"] = time.time()
        _persist(batch)
        req: BulkStartRequest = batch["_request"]
        try:
            gen = GeneratePresentationRequest(
                content=job["brief"],
                instructions=batch["_instructions"],
                verbosity=req.verbosity,
                web_search=False,
                n_slides=job["slides"],
                language=req.language,
                template=job.get("template") or req.template,
                include_title_slide=req.include_title_slide,
                export_as=req.export_as,
                folder_id=req.folder_id,
                theme_preset=req.theme_preset,
            )
            async with async_session_maker() as session:
                (presentation_id,) = await check_if_api_request_is_valid(gen, session)
                task = AsyncTaskModel(
                    type=ASYNC_TASK_TYPE_PRESENTATION_GENERATE,
                    status=AsyncTaskStatus.PENDING,
                    message="Queued by Bulk Create",
                    data=presentation_task_progress_data(
                        created_slides=0,
                        remaining_slides=_requested_slide_count(gen),
                        presentation_id=presentation_id,
                    ),
                )
                session.add(task)
                await session.commit()
                await session.refresh(task)
                task_id = task.id
            job["presentation_id"] = str(presentation_id)
            await _run_generate_presentation_task(
                gen, presentation_id, task_id, batch["_cookie"], batch["_owner_id"]
            )
            async with async_session_maker() as session:
                final = await session.get(AsyncTaskModel, task_id)
            if final is None or final.status != AsyncTaskStatus.COMPLETED:
                detail = (final.error or {}).get("detail") if final and final.error else None
                raise RuntimeError(detail or (final.message if final else "task missing"))
            data = final.data or {}
            job["export_path"] = data.get("path")
            job["edit_path"] = data.get("edit_path")
            changed = await _run_gate(batch, job, presentation_id)
            async with async_session_maker() as session:
                pres = await session.get(PresentationModel, presentation_id)
                if pres is not None and pres.title != job["name"]:
                    pres.title = job["name"][:200]
                    session.add(pres)
                    await session.commit()
                    changed = True
            if changed:
                exported = await export_presentation(
                    presentation_id, job["name"], req.export_as, cookie_header=batch["_cookie"]
                )
                job["export_path"] = exported.path
            q = await _quality(presentation_id)
            images = q.pop("_image_urls")
            titles = q.pop("_titles")
            job["quality"] = q
            job["_images"] = images
            job["_titles"] = titles
            job["status"] = "done"
            job["error"] = None
        except Exception as exc:  # noqa: BLE001 - one bad deck must not stop the batch
            LOGGER.exception("bulk: job %s failed (attempt %s)", job["name"], job.get("attempts", 0) + 1)
            job["status"] = "error"
            job["error"] = (getattr(exc, "detail", None) or str(exc))[:300]
            job["_retry"] = job.get("attempts", 0) < MAX_DECK_RETRIES and not batch.get("cancelled")
        finally:
            job["seconds"] = round(time.time() - job.get("started_at", time.time()), 1)
            _recompute_cross_deck(batch)
            _persist(batch)


def _recompute_cross_deck(batch: Dict[str, Any]) -> None:
    """Report images / slide titles that repeat across decks of the same batch."""
    img_count: Dict[str, int] = {}
    title_count: Dict[str, int] = {}
    for job in batch["jobs"]:
        for u in job.get("_images", []):
            img_count[u] = img_count.get(u, 0) + 1
        for t in set(job.get("_titles", [])):
            title_count[t] = title_count.get(t, 0) + 1
    for job in batch["jobs"]:
        if job["status"] != "done":
            continue
        job["cross_deck_duplicate_images"] = sum(
            1 for u in job.get("_images", []) if img_count.get(u, 0) > 1
        )
        job["cross_deck_duplicate_titles"] = sum(
            1 for t in set(job.get("_titles", [])) if title_count.get(t, 0) > 1
        )


async def _run_batch(batch: Dict[str, Any], only_ids: Optional[List[int]] = None) -> None:
    sem = asyncio.Semaphore(batch["_request"].parallel)
    jobs = [j for j in batch["jobs"] if only_ids is None or j["idx"] in only_ids]
    if only_ids is None and len(jobs) > PILOT_FROM_DECKS:
        # Pilot deck first: the rest only starts if it passes the quality gate.
        pilot, jobs = jobs[0], jobs[1:]
        await _run_one(batch, pilot, asyncio.Semaphore(1))
        if pilot["status"] != "done" or pilot.get("gate", {}).get("status") != "PASS":
            for j in jobs:
                j["status"] = "cancelled"
                j["error"] = "Stopped: the pilot deck did not pass the quality gate. Check it, then Retry."
            jobs = []
    await asyncio.gather(*[_run_one(batch, j, sem) for j in jobs])
    batch["finished_at"] = time.time()
    _persist(batch)


def _speed(batch: Dict[str, Any], jobs: List[Dict[str, Any]]) -> Dict[str, Any]:
    done = [j for j in jobs if j["status"] == "done" and j.get("seconds")]
    slides_done = sum(j["slides"] for j in done)
    if not done or not slides_done:
        return {"seconds_per_slide": None, "eta_seconds": None}
    per_slide = sum(j["seconds"] for j in done) / slides_done
    parallel = max(1, int(batch.get("parallel") or 1))
    left = sum(j["slides"] for j in jobs if j["status"] in ("queued", "running"))
    return {
        "seconds_per_slide": round(per_slide, 1),
        "eta_seconds": round(left * per_slide / parallel),
    }


def _public(batch: Dict[str, Any]) -> Dict[str, Any]:
    jobs = []
    for j in batch["jobs"]:
        jobs.append({k: v for k, v in j.items() if not k.startswith("_")})
    done = sum(1 for j in jobs if j["status"] == "done")
    failed = sum(1 for j in jobs if j["status"] == "error")
    return {
        "id": batch["id"],
        "course_name": batch.get("course_name", ""),
        "template": batch.get("template"),
        "parallel": batch.get("parallel"),
        "created_at": batch["created_at"],
        "finished_at": batch.get("finished_at"),
        "total": len(jobs),
        "done": done,
        "failed": failed,
        "running": sum(1 for j in jobs if j["status"] == "running"),
        "clinical_review": batch.get("clinical_review", True),
        "jobs": jobs,
        **_speed(batch, jobs),
    }


@BULK_ROUTER.post("/start")
async def start_batch(request_http: Request, body: BulkStartRequest):
    batch_id = uuid.uuid4().hex[:12]
    if body.folder_id is None:
        async with async_session_maker() as session:
            folder = FolderModel(
                name=f"{body.course_name or 'Bulk'} {datetime.now().strftime('%Y-%m-%d %H:%M')}"[:120]
            )
            session.add(folder)
            await session.commit()
            await session.refresh(folder)
            body.folder_id = folder.id
    batch: Dict[str, Any] = {
        "id": batch_id,
        "course_name": body.course_name,
        "template": body.template,
        "parallel": body.parallel,
        "clinical_review": body.clinical_review,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "jobs": [
            {
                "idx": i,
                "name": j.name,
                "brief": j.brief,
                "slides": j.slides,
                "template": j.template,
                "status": "queued",
            }
            for i, j in enumerate(body.jobs)
        ],
        "_request": body,
        "_instructions": _consistency_instructions(body),
        "_cookie": build_export_cookie_header(request_http),
        "_owner_id": get_current_owner_id(),
    }
    _BATCHES[batch_id] = batch
    _persist(batch)
    _RUNNERS[batch_id] = asyncio.create_task(_run_batch(batch))
    return _public(batch)


@BULK_ROUTER.get("/{batch_id}")
async def get_batch(batch_id: str):
    return _public(_load_batch(batch_id))


@BULK_ROUTER.post("/{batch_id}/retry")
async def retry_failed(batch_id: str):
    batch = _load_batch(batch_id)
    if "_request" not in batch:
        raise HTTPException(409, "This batch was closed by a restart; start a new batch")
    runner = _RUNNERS.get(batch_id)
    if runner and not runner.done():
        raise HTTPException(409, "Batch is still running")
    failed = [j["idx"] for j in batch["jobs"] if j["status"] in ("error", "cancelled")]
    if not failed:
        raise HTTPException(409, "Nothing to retry")
    for j in batch["jobs"]:
        if j["idx"] in failed:
            j["status"] = "queued"
            j.pop("error", None)
    batch.pop("finished_at", None)
    batch["cancelled"] = False
    _RUNNERS[batch_id] = asyncio.create_task(_run_batch(batch, failed))
    return _public(batch)


@BULK_ROUTER.post("/{batch_id}/cancel")
async def cancel_batch(batch_id: str):
    batch = _load_batch(batch_id)
    batch["cancelled"] = True  # running decks finish; queued decks are skipped
    return _public(batch)


@BULK_ROUTER.get("/{batch_id}/report.csv")
async def batch_report(batch_id: str):
    batch = _load_batch(batch_id)
    out = io.StringIO()
    cols = [
        "deck", "status", "slides_requested", "slides", "seconds", "notes_min_chars",
        "notes_under_300", "images", "duplicate_images", "layout_repeat_max",
        "distinct_layouts", "cross_deck_duplicate_images", "cross_deck_duplicate_titles",
        "clinical_review", "error", "seconds_per_slide", "gate_score", "gate_status",
        "slides_fixed", "fix_rounds", "remaining_issues",
    ]
    writer = csv.writer(out)
    writer.writerow(cols)
    for j in batch["jobs"]:
        q = j.get("quality", {})
        writer.writerow([
            j["name"], j["status"], j["slides"], q.get("slides", ""), j.get("seconds", ""),
            q.get("notes_min", ""), q.get("notes_short", ""), q.get("images", ""),
            q.get("duplicate_images", ""), q.get("layout_repeat_max", ""),
            q.get("distinct_layouts", ""), j.get("cross_deck_duplicate_images", ""),
            j.get("cross_deck_duplicate_titles", ""),
            "NEEDS ASF CLINICAL REVIEW" if batch.get("clinical_review", True) else "",
            j.get("error", ""),
            round(j["seconds"] / max(1, j["slides"]), 1) if j.get("seconds") else "",
            j.get("gate", {}).get("score", ""), j.get("gate", {}).get("status", ""),
            j.get("gate", {}).get("slides_fixed", ""), j.get("gate", {}).get("fix_rounds", ""),
            "; ".join(j.get("gate", {}).get("remaining", [])),
        ])
    return Response(
        out.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="bulk-{batch_id}-report.csv"'},
    )


@BULK_ROUTER.get("/{batch_id}/zip")
async def batch_zip(batch_id: str):
    batch = _load_batch(batch_id)
    files = []
    for j in batch["jobs"]:
        path = j.get("export_path")
        if j["status"] == "done" and path and os.path.isfile(path):
            files.append((j["name"], path))
    if not files:
        raise HTTPException(404, "No exported files yet")
    zip_path = os.path.join(_bulk_dir(), f"{batch_id}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
        for i, (name, path) in enumerate(files, 1):
            safe = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip()[:80] or "deck"
            ext = os.path.splitext(path)[1] or ".pptx"
            zf.write(path, f"{i:02d}-{safe}{ext}")
    return FileResponse(zip_path, media_type="application/zip", filename=f"bulk-{batch_id}.zip")
