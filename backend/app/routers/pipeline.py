"""Pipeline orchestration endpoints."""

import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.prefilter import get_skipped_jobs, override_prefilter, run_prefilter_batch
from app.database import async_session, get_session
from app.models import Job
from app.schemas.pipeline import (
    GCResult,
    LivenessStats,
    PipelineProcessResult,
    PipelineStatus,
    PrefilterResult,
    RescoreResult,
)
from app.scrapers.runner import run_scrape
from app.services.liveness import check_urls_batch, garbage_collect, get_liveness_stats
from app.services.pipeline import get_pipeline_summary, process_new_jobs, rescore_outdated

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


# ---------------------------------------------------------------------------
# Pipeline processing
# ---------------------------------------------------------------------------


async def _run_process(source: str | None = None):
    """Background task: process new jobs through full pipeline."""
    async with async_session() as session:
        try:
            await process_new_jobs(session, source=source)
        except Exception:
            logger.exception("Pipeline processing failed")


@router.post("/process", response_model=PipelineProcessResult)
async def process_jobs(
    background_tasks: BackgroundTasks,
    source: str | None = None,
):
    """Clean + pre-filter + score all unprocessed jobs.

    Pass ?source=greenhouse to limit to a specific source.
    Runs in background.
    """
    background_tasks.add_task(_run_process, source)
    return PipelineProcessResult()


@router.post("/update/{source}")
async def update_source(
    source: str,
    background_tasks: BackgroundTasks,
):
    """Full pipeline: scrape → clean → pre-filter → score for a given source.

    Example: POST /api/pipeline/update/greenhouse
    """

    async def _run_full(source_name: str):
        async with async_session() as session:
            try:
                await run_scrape(session, source_filter=source_name)
                await process_new_jobs(session, source=source_name)
            except Exception:
                logger.exception("Full pipeline update failed for %s", source_name)

    background_tasks.add_task(_run_full, source)
    return {"message": f"Full pipeline started for {source}", "status": "running"}


async def _run_rescore():
    """Background task: rescore outdated jobs."""
    from app.ai.prompts import PROMPT_VERSION

    async with async_session() as session:
        try:
            await rescore_outdated(session, PROMPT_VERSION)
        except Exception:
            logger.exception("Rescore failed")


@router.post("/rescore", response_model=RescoreResult)
async def rescore(
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """Re-score jobs with outdated prompt_version. Runs in background.

    Returns the list of job IDs that will be rescored so the frontend
    can display per-row progress indicators.
    """
    from app.ai.prompts import PROMPT_VERSION
    from sqlalchemy import select as sa_select

    result = await session.execute(
        sa_select(Job.id).where(
            Job.pipeline_stage == "scored",
            Job.expired_at.is_(None),
            Job.score_prompt_version != PROMPT_VERSION,
        )
    )
    job_ids = [str(row[0]) for row in result.all()]

    background_tasks.add_task(_run_rescore)
    return RescoreResult(job_ids=job_ids, current_version=PROMPT_VERSION)


@router.get("/status", response_model=PipelineStatus)
async def pipeline_status(session: AsyncSession = Depends(get_session)):
    """Get counts per pipeline stage."""
    summary = await get_pipeline_summary(session)
    return PipelineStatus(**summary)


# ---------------------------------------------------------------------------
# Pre-filter
# ---------------------------------------------------------------------------


async def _run_prefilter():
    """Background task: run pre-filter."""
    async with async_session() as session:
        try:
            await run_prefilter_batch(session)
        except Exception:
            logger.exception("Pre-filter failed")


@router.post("/prefilter", response_model=PrefilterResult)
async def prefilter(background_tasks: BackgroundTasks):
    """Run pre-filter on all 'cleaned' jobs. Runs in background."""
    background_tasks.add_task(_run_prefilter)
    return PrefilterResult()


@router.get("/prefilter/skipped")
async def prefilter_skipped(
    limit: int = 100,
    session: AsyncSession = Depends(get_session),
):
    """List jobs auto-skipped by the pre-filter."""
    jobs = await get_skipped_jobs(session, limit=limit)
    return [
        {
            "id": str(j.id),
            "title": j.display_title,
            "company": j.display_company,
            "source": j.source.value,
            "prefilter_reason": (j.extra_metadata or {}).get("prefilter_reason"),
            "scraped_at": j.scraped_at.isoformat(),
        }
        for j in jobs
    ]


@router.post("/prefilter/override/{job_id}")
async def prefilter_override_job(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """Force a skipped job back into the pipeline for scoring."""
    job = await override_prefilter(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "id": str(job.id),
        "pipeline_stage": job.pipeline_stage,
        "prefilter_pass": job.prefilter_pass,
    }


# ---------------------------------------------------------------------------
# Liveness
# ---------------------------------------------------------------------------


@router.get("/liveness/stats", response_model=LivenessStats)
async def liveness_stats(session: AsyncSession = Depends(get_session)):
    """Get expiration statistics."""
    stats = await get_liveness_stats(session)
    return LivenessStats(**stats)


async def _run_url_check():
    """Background task: check URLs."""
    async with async_session() as session:
        try:
            await check_urls_batch(session)
        except Exception:
            logger.exception("URL check failed")


@router.post("/liveness/check-urls")
async def check_urls(background_tasks: BackgroundTasks):
    """HTTP HEAD check on stale jobs. Runs in background."""
    background_tasks.add_task(_run_url_check)
    return {"message": "URL check started", "status": "running"}


@router.post("/liveness/gc", response_model=GCResult)
async def gc(
    expired_days: int = 90,
    session: AsyncSession = Depends(get_session),
):
    """Archive jobs expired > N days with status='new'."""
    result = await garbage_collect(session, expired_days=expired_days)
    return GCResult(**result)
