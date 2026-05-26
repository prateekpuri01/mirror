"""Pipeline orchestrator — process jobs through clean → prefilter → score stages."""

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.prefilter import run_prefilter_batch
from app.ai.scoring import _build_compact_profile, _get_example_jobs, _get_profile, score_job
from app.models import Job
from app.services.auto_tagger import auto_tag_jobs
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
    stats = {
        "cleaned": 0,
        "extracted": 0,
        "scored": 0,
        "skipped": 0,
        "errors": 0,
        "expired_excluded": 0,
        "tagged": 0,
    }

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
    now = datetime.now(UTC)
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
        profile = await _get_profile(session)
        profile_text = _build_compact_profile(profile.data)
        profile_data = profile.data
        positive_jobs, negative_jobs = await _get_example_jobs(session)

        # Score jobs in parallel, capped by detected API rate limits
        from app.services.rate_limits import max_concurrent_scoring

        scoring_semaphore = asyncio.Semaphore(max_concurrent_scoring())

        async def _score_one(job: Job) -> bool:
            async with scoring_semaphore:
                try:
                    await score_job(
                        session,
                        job.id,
                        profile_text=profile_text,
                        profile_data=profile_data,
                        positive_jobs=positive_jobs,
                        negative_jobs=negative_jobs,
                    )
                    return True
                except Exception:
                    logger.exception("Failed to score job %s", job.id)
                    return False

        results = await asyncio.gather(*[_score_one(j) for j in to_score])
        stats["scored"] = sum(1 for r in results if r)
        stats["errors"] = sum(1 for r in results if not r)

    # Step 4: Auto-tag scored jobs with user-defined tags
    if to_score:
        try:
            tag_stats = await auto_tag_jobs(session, to_score)
            stats["tagged"] = tag_stats["tagged_jobs"]
        except Exception:
            logger.exception("Auto-tagging failed")

    logger.info(
        "Pipeline complete: cleaned=%d extracted=%d scored=%d skipped=%d tagged=%d errors=%d",
        stats["cleaned"],
        stats["extracted"],
        stats["scored"],
        stats["skipped"],
        stats["tagged"],
        stats["errors"],
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

    profile = await _get_profile(session)
    profile_text = _build_compact_profile(profile.data)
    profile_data = profile.data
    positive_jobs, negative_jobs = await _get_example_jobs(session)

    # Rescore in parallel, capped by detected API rate limits
    from app.services.rate_limits import max_concurrent_scoring

    rescore_sem = asyncio.Semaphore(max_concurrent_scoring())

    async def _rescore_one(job: Job) -> bool:
        async with rescore_sem:
            try:
                await score_job(
                    session,
                    job.id,
                    profile_text=profile_text,
                    profile_data=profile_data,
                    positive_jobs=positive_jobs,
                    negative_jobs=negative_jobs,
                )
                return True
            except Exception:
                logger.exception("Failed to rescore job %s", job.id)
                return False

    results = await asyncio.gather(*[_rescore_one(j) for j in jobs])
    stats["rescored"] = sum(1 for r in results if r)
    stats["failed"] = sum(1 for r in results if not r)

    logger.info(
        "Rescore complete: %d rescored, %d failed (version: %s)",
        stats["rescored"],
        stats["failed"],
        PROMPT_VERSION,
    )
    return stats
