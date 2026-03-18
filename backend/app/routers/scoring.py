import uuid
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session, async_session
from app.ai.scoring import score_job, score_jobs_batch, get_batch_status
from app.ai.enrichment import (
    enrich_single,
    get_enrichment_status,
    run_enrichment_pipeline,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scoring", tags=["scoring"])


# ---------------------------------------------------------------------------
# Scoring endpoints (existing)
# ---------------------------------------------------------------------------


@router.post("/score/{job_id}")
async def score_single_job(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """Score a single job for role fit and interest fit."""
    try:
        job = await score_job(session, job_id)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id": str(job.id),
        "role_fit_score": job.role_fit_score,
        "interest_fit_score": job.interest_fit_score,
        "relevance_score": job.relevance_score,
        "score_rationale": job.score_rationale,
    }


async def _run_batch(
    only_unscored: bool = True,
    only_thumbed: bool = False,
    only_enriched: bool = False,
):
    """Run batch scoring in a background task with its own session."""
    async with async_session() as session:
        try:
            await score_jobs_batch(
                session,
                only_unscored=only_unscored,
                only_thumbed=only_thumbed,
                only_enriched=only_enriched,
            )
        except Exception:
            logger.exception("Batch scoring failed")


@router.post("/batch")
async def batch_score(
    background_tasks: BackgroundTasks,
    only_enriched: bool = False,
):
    """Score all unscored jobs (runs in background).

    Pass ?only_enriched=true to score only jobs from enriched companies.
    """
    status = get_batch_status()
    if status["running"]:
        raise HTTPException(status_code=409, detail="Batch scoring already in progress")
    background_tasks.add_task(_run_batch, only_unscored=True, only_enriched=only_enriched)
    return {"message": "Batch scoring started", "status": "running"}


@router.post("/calibrate")
async def calibrate_score(background_tasks: BackgroundTasks):
    """Score only thumbed jobs for prompt tuning."""
    status = get_batch_status()
    if status["running"]:
        raise HTTPException(status_code=409, detail="Batch scoring already in progress")
    background_tasks.add_task(_run_batch, only_unscored=False, only_thumbed=True)
    return {"message": "Calibration scoring started", "status": "running"}


@router.get("/status")
async def scoring_status():
    """Get current batch scoring progress."""
    return get_batch_status()


# ---------------------------------------------------------------------------
# Enrichment endpoints
# ---------------------------------------------------------------------------


async def _run_enrichment(force: bool = False, limit: int = 0):
    """Run enrichment pipeline in a background task with its own session."""
    async with async_session() as session:
        try:
            await run_enrichment_pipeline(
                session, brave_api_key=settings.brave_api_key, force=force, limit=limit
            )
        except Exception:
            logger.exception("Enrichment pipeline failed")


@router.post("/enrich")
async def enrich_all(
    background_tasks: BackgroundTasks,
    force: bool = False,
    limit: int = 0,
):
    """Run full company enrichment pipeline (resolve, enrich, extract teams).

    Runs in background. Check progress with GET /api/scoring/enrich/status.
    Pass ?force=true to re-enrich already-enriched companies.
    Pass ?limit=N to enrich only the first N unenriched companies.
    """
    status = get_enrichment_status()
    if status["running"]:
        raise HTTPException(
            status_code=409, detail="Enrichment pipeline already in progress"
        )
    background_tasks.add_task(_run_enrichment, force, limit)
    return {"message": "Enrichment pipeline started", "status": "running"}


@router.get("/enrich/status")
async def enrichment_status():
    """Get current enrichment pipeline progress."""
    return get_enrichment_status()


@router.post("/enrich/{company_id}")
async def enrich_company(
    company_id: uuid.UUID,
    force: bool = False,
    session: AsyncSession = Depends(get_session),
):
    """Enrich a single company with AI-generated context."""
    try:
        company = await enrich_single(
            session, company_id, brave_api_key=settings.brave_api_key, force=force
        )
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return {
        "company_id": str(company.id),
        "name": company.name,
        "website": company.website,
        "notes": company.notes,
        "enriched_at": company.enriched_at.isoformat() if company.enriched_at else None,
    }
