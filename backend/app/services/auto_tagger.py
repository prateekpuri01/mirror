"""Keyword-based auto-tagging of jobs.

Scans each tag name against job title/company/description and applies
the tag if there's a match. Zero LLM cost — purely deterministic.
"""

import logging
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job, JobTag, Tag

logger = logging.getLogger(__name__)


def _build_search_text(job: Job) -> str:
    """Build a searchable text blob for a job."""
    parts = [
        job.clean_title or job.title or "",
        job.clean_company or job.company or "",
        (job.description or "")[:1500],
    ]
    return " ".join(parts).lower()


def _tag_matches(tag_name: str, search_text: str) -> bool:
    """Check if a tag's name matches anywhere in the search text.

    Uses word-boundary matching so "AI" doesn't match "email".
    Multi-word tags like "machine learning" match as a phrase.
    """
    name = tag_name.lower().strip()
    if not name:
        return False

    # Escape regex special chars, then match at word boundaries
    pattern = r"\b" + re.escape(name) + r"\b"
    return bool(re.search(pattern, search_text))


async def auto_tag_jobs(
    session: AsyncSession,
    jobs: list[Job],
    tags: list[Tag] | None = None,
) -> dict:
    """Apply user-defined tags to jobs via keyword matching.

    Args:
        session: DB session.
        jobs: Jobs to tag.
        tags: Optional pre-loaded tags. If None, loads all tags from DB.

    Returns:
        {"tagged_jobs": int, "tags_applied": int}
    """
    if not jobs:
        return {"tagged_jobs": 0, "tags_applied": 0}

    if tags is None:
        result = await session.execute(select(Tag))
        tags = list(result.scalars().all())

    if not tags:
        return {"tagged_jobs": 0, "tags_applied": 0}

    # Load existing job-tag associations to avoid duplicates
    job_ids = [j.id for j in jobs]
    existing_result = await session.execute(
        select(JobTag).where(JobTag.job_id.in_(job_ids))
    )
    existing_pairs: set[tuple] = {(jt.job_id, jt.tag_id) for jt in existing_result.scalars().all()}

    tagged_jobs = 0
    tags_applied = 0

    for job in jobs:
        search_text = _build_search_text(job)
        job_tagged = False
        for tag in tags:
            if (job.id, tag.id) in existing_pairs:
                continue
            if _tag_matches(tag.name, search_text):
                session.add(JobTag(job_id=job.id, tag_id=tag.id))
                tags_applied += 1
                job_tagged = True
        if job_tagged:
            tagged_jobs += 1

    if tags_applied > 0:
        await session.commit()
        logger.info(
            "Auto-tagged %d jobs with %d tag applications",
            tagged_jobs,
            tags_applied,
        )

    return {"tagged_jobs": tagged_jobs, "tags_applied": tags_applied}


async def apply_tag_to_all_jobs(session: AsyncSession, tag: Tag) -> dict:
    """Apply a single tag to all existing jobs that match it.

    Used when a user creates a new tag — backfills it across the job pool.
    """
    result = await session.execute(select(Job).where(Job.expired_at.is_(None)))
    jobs = list(result.scalars().all())
    return await auto_tag_jobs(session, jobs, tags=[tag])
