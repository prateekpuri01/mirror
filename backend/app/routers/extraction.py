import asyncio
import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session, get_session
from app.models.jobs import Job
from app.models.locations import JobLocation, Location
from app.schemas.locations import LocationRead
from app.services.extraction import (
    _browser_salary_fallback,
    _extract_one,
    _make_display_name,
    get_extraction_status,
    run_extraction_pipeline,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/extraction", tags=["extraction"])


async def _run_extraction():
    """Run extraction pipeline in a background task with its own session."""
    async with async_session() as session:
        try:
            await run_extraction_pipeline(session)
        except Exception:
            logger.exception("Extraction pipeline failed")


@router.post("/run")
async def run_extraction(background_tasks: BackgroundTasks):
    """Run the field extraction pipeline (background task).

    Extracts salary, location, and other fields from job descriptions using LLM.
    Check progress with GET /api/extraction/status.
    """
    status = get_extraction_status()
    if status["running"]:
        raise HTTPException(status_code=409, detail="Extraction pipeline already in progress")
    background_tasks.add_task(_run_extraction)
    return {"message": "Extraction pipeline started", "status": "running"}


@router.get("/status")
async def extraction_status():
    """Get current extraction pipeline progress."""
    return get_extraction_status()


class ExtractionPreview(BaseModel):
    salary_min: int | None = None
    salary_max: int | None = None
    salary_confidence: str | None = None
    work_model: str | None = None
    locations: list[dict] = []


class ExtractionApply(BaseModel):
    salary_min: int | None = None
    salary_max: int | None = None
    work_model: str | None = None
    locations: list[dict] = []


@router.post("/preview/{job_id}", response_model=ExtractionPreview)
async def extract_preview(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """Run extraction on a single job and return results without saving."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    semaphore = asyncio.Semaphore(1)
    # Retry up to 2 times
    extracted = None
    for _attempt in range(2):
        _, extracted = await _extract_one(job, semaphore)
        if extracted is not None:
            break
    if extracted is None:
        raise HTTPException(status_code=500, detail="Extraction failed after retries")

    # Browser fallback: if no salary found, render the page and try again
    if extracted.get("salary_confidence", "none") == "none" or not extracted.get("salary_min"):
        browser_result = await _browser_salary_fallback(job)
        if browser_result:
            extracted["salary_min"] = browser_result["salary_min"]
            extracted["salary_max"] = browser_result.get("salary_max")
            extracted["salary_confidence"] = browser_result["salary_confidence"]

    # Build location display names for the preview
    locs = []
    for loc in extracted.get("locations", []):
        dn = _make_display_name(loc)
        if dn:
            locs.append({**loc, "display_name": dn})

    return ExtractionPreview(
        salary_min=extracted.get("salary_min"),
        salary_max=extracted.get("salary_max"),
        salary_confidence=extracted.get("salary_confidence"),
        work_model=extracted.get("work_model"),
        locations=locs,
    )


@router.post("/apply/{job_id}")
async def extract_apply(
    job_id: uuid.UUID,
    body: ExtractionApply,
    session: AsyncSession = Depends(get_session),
):
    """Apply user-reviewed extraction results to a job."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Update salary
    job.salary_min = body.salary_min if body.salary_min is not None else -1
    job.salary_max = body.salary_max

    # Update work model
    if body.work_model in ("remote", "hybrid", "onsite"):
        job.work_model = body.work_model
        job.remote = body.work_model == "remote"

    # Update locations — clear existing links and rebuild
    await session.execute(JobLocation.__table__.delete().where(JobLocation.job_id == job_id))

    if body.locations:
        for loc_dict in body.locations:
            dn = loc_dict.get("display_name") or _make_display_name(loc_dict)
            if not dn:
                continue

            # Find or create location
            existing = await session.execute(select(Location).where(Location.display_name == dn))
            location = existing.scalar_one_or_none()
            if location is None:
                location = Location(
                    display_name=dn,
                    city=loc_dict.get("city") or dn,
                    state=(loc_dict.get("state") or "").strip() or None,
                    country=(loc_dict.get("country") or "US").strip(),
                    is_remote=bool(loc_dict.get("is_remote")),
                )
                session.add(location)
                await session.flush()

            # Link job to location
            stmt = (
                pg_insert(JobLocation)
                .values(job_id=job_id, location_id=location.id)
                .on_conflict_do_nothing()
            )
            await session.execute(stmt)

    # Mark as extracted
    meta = dict(job.extra_metadata or {})
    meta["extracted"] = True
    meta["salary_source"] = "extraction"
    job.extra_metadata = meta

    await session.commit()
    await session.refresh(job)

    return {"status": "ok", "job_id": str(job_id)}


@router.get("/locations", response_model=list[LocationRead])
async def list_locations(session: AsyncSession = Depends(get_session)):
    """List all normalized locations, ordered by job count descending."""
    stmt = (
        select(Location)
        .outerjoin(JobLocation)
        .group_by(Location.id)
        .order_by(func.count(JobLocation.job_id).desc())
    )
    result = await session.execute(stmt)
    return result.scalars().all()
