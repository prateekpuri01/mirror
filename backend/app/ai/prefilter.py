"""Zero-cost pre-filter: keyword-based screening before expensive LLM scoring.

Two tiers:
  Tier A — Hard exclusion (deal-breaker keywords derived from profile data)
  Tier B — Positive signal required (target roles + domains + top skills from profile)

No LLM calls. Runs on title + company + first 500 chars of description.
"""

import logging
import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job, UserProfile

logger = logging.getLogger(__name__)


def _extract_keywords(text: str) -> list[str]:
    """Extract keyword phrases from a deal-breaker or anti-pattern string.

    Splits on " / ", " - ", parentheticals, and common separators
    to get individual matchable phrases.
    """
    # Remove parenthetical content like "(Raytheon, Lockheed)"
    parens = re.findall(r"\(([^)]+)\)", text)
    # Remove parentheticals from main text
    main = re.sub(r"\([^)]*\)", "", text).strip()
    # Split main text on common separators
    parts = re.split(r"\s*/\s*|\s*-\s*|\s*,\s*", main)
    # Add parenthetical items
    for paren in parens:
        parts.extend(re.split(r"\s*,\s*", paren))
    # Clean and filter
    return [p.strip().lower() for p in parts if len(p.strip()) >= 3]


def build_keyword_lists(profile_data: dict) -> tuple[list[str], list[str]]:
    """Build exclude/include keyword lists from profile data.

    Returns (exclude_keywords, include_keywords).
    """
    prefs = profile_data.get("search_preferences", {})

    # EXCLUDE: prefer new `exclusions` field (already clean keywords),
    # fall back to legacy deal_breakers + org_anti_patterns
    exclude = []
    exclusions = prefs.get("exclusions", [])
    if exclusions:
        exclude.extend([kw.lower() for kw in exclusions])
    else:
        for text in prefs.get("deal_breakers", []) + prefs.get("org_anti_patterns", []):
            exclude.extend(_extract_keywords(text))

    # INCLUDE: target role titles + domains + top technical skills
    include = []
    for role in profile_data.get("target_roles", []):
        title = role.get("title", "")
        if title:
            include.append(title.lower())
            # Also add individual words from multi-word titles
            for word in title.lower().split():
                if len(word) >= 4:  # Skip short words like "of", "and"
                    include.append(word)
    for domain in profile_data.get("domains", []):
        include.append(domain.lower())
    # Add first 10 skills (from any category) as positive signals
    from app.ai.skill_utils import flatten_skills

    for skill in flatten_skills(profile_data.get("skills", {}))[:10]:
        include.append(skill.lower())

    return exclude, include


def _compile_patterns(keywords: list[str]) -> list[re.Pattern]:
    """Compile keyword list into regex patterns, deduplicating."""
    seen = set()
    patterns = []
    for kw in keywords:
        if kw not in seen and kw:
            seen.add(kw)
            patterns.append(re.compile(re.escape(kw), re.IGNORECASE))
    return patterns


def _build_search_text(job: Job) -> str:
    """Build text to search against: title + company + first 500 chars of description."""
    parts = [job.title, job.company]
    if job.description:
        parts.append(job.description[:500])
    return " ".join(parts)


def check_prefilter(
    job: Job,
    exclude_patterns: list[re.Pattern],
    include_patterns: list[re.Pattern],
) -> tuple[bool, str]:
    """Check if a job passes the pre-filter.

    Returns (passes: bool, reason: str).
    """
    text = _build_search_text(job)

    # Tier A: hard exclusion
    for pattern in exclude_patterns:
        if pattern.search(text):
            return False, f"exclude:{pattern.pattern}"

    # Tier B: positive signal required
    for pattern in include_patterns:
        if pattern.search(text):
            return True, f"include:{pattern.pattern}"

    return False, "no_positive_signal"


async def _load_profile_data(session: AsyncSession) -> dict:
    """Load profile data from DB for building keyword lists."""
    result = await session.execute(select(UserProfile).limit(1))
    profile = result.scalar_one_or_none()
    if profile is None:
        logger.warning("No profile found — prefilter will have empty keyword lists")
        return {}
    return profile.data or {}


async def run_prefilter_batch(session: AsyncSession) -> dict:
    """Run pre-filter on all 'cleaned' jobs that haven't been filtered yet.

    Returns stats: {passed, skipped, already_filtered}.
    """
    # Load profile and build keyword patterns once
    profile_data = await _load_profile_data(session)
    exclude_kw, include_kw = build_keyword_lists(profile_data)
    exclude_patterns = _compile_patterns(exclude_kw)
    include_patterns = _compile_patterns(include_kw)

    result = await session.execute(
        select(Job).where(
            Job.pipeline_stage == "cleaned",
            Job.prefilter_pass.is_(None),
            Job.expired_at.is_(None),
        )
    )
    jobs = list(result.scalars().all())

    stats = {"passed": 0, "skipped": 0, "already_filtered": 0, "total": len(jobs)}

    for job in jobs:
        passes, reason = check_prefilter(job, exclude_patterns, include_patterns)
        job.prefilter_pass = passes

        if not passes:
            job.pipeline_stage = "skipped"
            meta = dict(job.extra_metadata or {})
            meta["prefilter_reason"] = reason
            job.extra_metadata = meta
            stats["skipped"] += 1
        else:
            stats["passed"] += 1

    await session.commit()
    logger.info(
        "Pre-filter: %d passed, %d skipped out of %d jobs",
        stats["passed"],
        stats["skipped"],
        stats["total"],
    )
    return stats


async def get_skipped_jobs(session: AsyncSession, limit: int = 100) -> list[Job]:
    """Return jobs that were skipped by the pre-filter."""
    result = await session.execute(
        select(Job)
        .where(Job.pipeline_stage == "skipped", Job.prefilter_pass.is_(False))
        .order_by(Job.scraped_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def override_prefilter(session: AsyncSession, job_id) -> Job | None:
    """Force a skipped job back into the pipeline for scoring."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        return None

    job.prefilter_pass = True
    job.pipeline_stage = "cleaned"
    meta = dict(job.extra_metadata or {})
    meta["prefilter_override"] = True
    meta["prefilter_override_at"] = datetime.now(UTC).isoformat()
    job.extra_metadata = meta

    await session.commit()
    await session.refresh(job)
    return job
