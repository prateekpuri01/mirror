"""Pipeline orchestrator — process jobs through clean → prefilter → score stages."""

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.prefilter import check_prefilter, run_prefilter_batch
from app.ai.scoring import score_job, _get_profile_text, _get_example_jobs
from app.models import Job
from app.services.cleaning import clean_jobs_batch
from app.services.extraction import extract_new_jobs

logger = logging.getLogger(__name__)


async def process_new_jobs(
    session: AsyncSession,
    *,
    source: str | None = None,
) -> dict:
    """Process all unprocessed jobs through the full pipeline.

    1. Clean (Layer 2) all scraped jobs
    2. Pre-filter cleaned jobs
    3. Score jobs that pass the pre-filter

    Returns stats: {cleaned, scored, skipped, errors, expired_excluded}.
    """
    stats = {"cleaned": 0, "extracted": 0, "scored": 0, "skipped": 0, "errors": 0, "expired_excluded": 0}

    # Step 1: Run Layer 2 cleaning (handles its own pipeline_stage updates)
    clean_stats = await clean_jobs_batch(session)
    stats["cleaned"] = clean_stats["titles_cleaned"] + clean_stats["companies_cleaned"]

    # Also advance scraped jobs that don't need cleaning to 'cleaned'
    result = await session.execute(
        select(Job).where(
            Job.pipeline_stage == "scraped",
            Job.expired_at.is_(None),
        )
    )
    uncleaned_jobs = list(result.scalars().all())
    now = datetime.now(timezone.utc)
    for job in uncleaned_jobs:
        job.pipeline_stage = "cleaned"
        job.cleaned_at = now
    if uncleaned_jobs:
        await session.commit()

    # Step 1.5: Extract salary/location/work_model via LLM for cleaned jobs
    result = await session.execute(
        select(Job).where(
            Job.pipeline_stage == "cleaned",
            Job.expired_at.is_(None),
        )
    )
    cleaned_jobs = list(result.scalars().all())
    if cleaned_jobs:
        extraction_stats = await extract_new_jobs(session, cleaned_jobs)
        stats["extracted"] = extraction_stats["extracted"]

    # Step 2: Pre-filter
    prefilter_stats = await run_prefilter_batch(session)
    stats["skipped"] = prefilter_stats["skipped"]

    # Step 3: Score jobs that passed pre-filter and are still in 'cleaned' stage
    query = select(Job).where(
        Job.pipeline_stage == "cleaned",
        Job.prefilter_pass.is_(True),
        Job.expired_at.is_(None),
    )
    if source:
        query = query.where(Job.source == source)

    result = await session.execute(query)
    to_score = list(result.scalars().all())

    if to_score:
        profile_text = await _get_profile_text(session)
        positive_jobs, negative_jobs = await _get_example_jobs(session)

        for job in to_score:
            try:
                await score_job(
                    session,
                    job.id,
                    profile_text=profile_text,
                    positive_jobs=positive_jobs,
                    negative_jobs=negative_jobs,
                )
                stats["scored"] += 1
            except Exception:
                logger.exception("Failed to score job %s", job.id)
                stats["errors"] += 1

    logger.info(
        "Pipeline complete: cleaned=%d extracted=%d scored=%d skipped=%d errors=%d",
        stats["cleaned"], stats["extracted"], stats["scored"], stats["skipped"], stats["errors"],
    )
    return stats


async def get_pipeline_summary(session: AsyncSession) -> dict:
    """Get counts per pipeline stage."""
    result = await session.execute(
        select(Job.pipeline_stage, func.count(Job.id)).group_by(Job.pipeline_stage)
    )
    counts = {row[0]: row[1] for row in result.all()}

    # Count expired jobs
    expired_result = await session.execute(
        select(func.count(Job.id)).where(Job.expired_at.isnot(None))
    )
    expired_count = expired_result.scalar() or 0

    total = sum(counts.values())
    return {
        "scraped": counts.get("scraped", 0),
        "cleaned": counts.get("cleaned", 0),
        "scored": counts.get("scored", 0),
        "skipped": counts.get("skipped", 0),
        "total": total,
        "expired": expired_count,
    }


async def rescore_outdated(session: AsyncSession, prompt_version: str) -> dict:
    """Re-score jobs whose score_prompt_version doesn't match current version."""
    from app.ai.prompts import PROMPT_VERSION

    result = await session.execute(
        select(Job).where(
            Job.pipeline_stage == "scored",
            Job.expired_at.is_(None),
            Job.score_prompt_version != PROMPT_VERSION,
        )
    )
    jobs = list(result.scalars().all())

    stats = {"rescored": 0, "failed": 0, "current_version": PROMPT_VERSION}

    if not jobs:
        return stats

    profile_text = await _get_profile_text(session)
    positive_jobs, negative_jobs = await _get_example_jobs(session)

    for job in jobs:
        try:
            await score_job(
                session,
                job.id,
                profile_text=profile_text,
                positive_jobs=positive_jobs,
                negative_jobs=negative_jobs,
            )
            stats["rescored"] += 1
        except Exception:
            logger.exception("Failed to rescore job %s", job.id)
            stats["failed"] += 1

    logger.info(
        "Rescore complete: %d rescored, %d failed (version: %s)",
        stats["rescored"], stats["failed"], PROMPT_VERSION,
    )
    return stats
