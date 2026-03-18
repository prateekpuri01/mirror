"""Zero-cost pre-filter: keyword-based screening before expensive LLM scoring.

Two tiers:
  Tier A — Hard exclusion (deal-breaker keywords from profile.yaml)
  Tier B — Positive signal required (must match at least one target domain/role keyword)

No LLM calls. Runs on title + company + first 500 chars of description.
"""

import logging
import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tier A: Hard exclusion keywords (from profile.yaml deal_breakers)
# ---------------------------------------------------------------------------
EXCLUDE_KEYWORDS: list[str] = [
    # Crypto / blockchain
    "crypto", "blockchain", "web3", "defi", "decentralized finance",
    "smart contract", "solidity", "ethereum", "nft",
    # Ad-tech / growth hacking
    "ad-tech", "adtech", "growth hacking", "engagement optimization",
    "ad network", "programmatic advertising",
    # Defense contractors
    "raytheon", "lockheed martin", "northrop grumman", "general dynamics",
    "l3harris", "bae systems",
    # Enterprise SaaS with no research
    "salesforce admin", "hubspot", "zendesk",
]

# ---------------------------------------------------------------------------
# Tier B: Positive signal keywords (from target_roles + domains)
# ---------------------------------------------------------------------------
INCLUDE_KEYWORDS: list[str] = [
    # Target roles
    "research", "scientist", "researcher", "information scientist",
    # Domains
    "machine learning", "ml", "artificial intelligence", "ai",
    "data science", "data scientist", "nlp", "natural language",
    "policy", "governance", "economics", "labor",
    "national security", "technology policy",
    # Related signals
    "llm", "large language model", "deep learning",
    "causal inference", "bayesian", "statistics",
    "analyst", "quantitative",
]

# Compile patterns for efficient matching
_EXCLUDE_PATTERNS = [re.compile(re.escape(kw), re.IGNORECASE) for kw in EXCLUDE_KEYWORDS]
_INCLUDE_PATTERNS = [re.compile(re.escape(kw), re.IGNORECASE) for kw in INCLUDE_KEYWORDS]


def _build_search_text(job: Job) -> str:
    """Build text to search against: title + company + first 500 chars of description."""
    parts = [job.title, job.company]
    if job.description:
        parts.append(job.description[:500])
    return " ".join(parts)


def check_prefilter(job: Job) -> tuple[bool, str]:
    """Check if a job passes the pre-filter.

    Returns (passes: bool, reason: str).
    """
    text = _build_search_text(job)

    # Tier A: hard exclusion
    for pattern in _EXCLUDE_PATTERNS:
        if pattern.search(text):
            return False, f"exclude:{pattern.pattern}"

    # Tier B: positive signal required
    for pattern in _INCLUDE_PATTERNS:
        if pattern.search(text):
            return True, f"include:{pattern.pattern}"

    return False, "no_positive_signal"


async def run_prefilter_batch(session: AsyncSession) -> dict:
    """Run pre-filter on all 'cleaned' jobs that haven't been filtered yet.

    Returns stats: {passed, skipped, already_filtered}.
    """
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
        passes, reason = check_prefilter(job)
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
        stats["passed"], stats["skipped"], stats["total"],
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
    meta["prefilter_override_at"] = datetime.now(timezone.utc).isoformat()
    job.extra_metadata = meta

    await session.commit()
    await session.refresh(job)
    return job
