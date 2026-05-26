"""API endpoints for the job title / company name cleaning pipeline."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import Job

router = APIRouter(prefix="/api", tags=["cleaning"])


class CleaningResult(BaseModel):
    layer2: dict
    layer3: dict


class FlaggedJob(BaseModel):
    id: str
    title: str
    company: str
    clean_title: str | None = None
    clean_company: str | None = None
    reason: str | None = None


@router.post("/cleaning/run", response_model=CleaningResult)
async def run_cleaning(session: AsyncSession = Depends(get_session)):
    """Run the full cleaning pipeline (Layer 2 deterministic + Layer 3 LLM)."""
    from app.ai.cleaning import llm_clean_batch
    from app.services.cleaning import clean_jobs_batch

    layer2_stats = await clean_jobs_batch(session)
    layer3_stats = await llm_clean_batch(session)

    return CleaningResult(layer2=layer2_stats, layer3=layer3_stats)


@router.post("/cleaning/layer2")
async def run_layer2_only(session: AsyncSession = Depends(get_session)):
    """Run only Layer 2 (deterministic) cleaning."""
    from app.services.cleaning import clean_jobs_batch

    return await clean_jobs_batch(session)


@router.get("/cleaning/flagged", response_model=list[FlaggedJob])
async def list_flagged(session: AsyncSession = Depends(get_session)):
    """List jobs that couldn't be auto-cleaned (flagged for LLM or low-confidence)."""
    result = await session.execute(select(Job))
    all_jobs = list(result.scalars().all())

    flagged = []
    for job in all_jobs:
        meta = job.extra_metadata or {}
        reason = None
        if meta.get("needs_llm_cleaning"):
            reason = meta.get("llm_cleaning_reason", "unknown")
        elif (meta.get("llm_cleaning") or {}).get("status") == "low_confidence":
            reason = "low_confidence"
        else:
            continue

        flagged.append(
            FlaggedJob(
                id=str(job.id),
                title=job.title,
                company=job.company,
                clean_title=job.clean_title,
                clean_company=job.clean_company,
                reason=reason,
            )
        )

    return flagged
