"""Job liveness: expiration stats, URL checks, garbage collection."""

import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job, JobStatus

logger = logging.getLogger(__name__)


async def get_liveness_stats(session: AsyncSession) -> dict:
    """Get expiration statistics."""
    now = datetime.now(timezone.utc)

    total_expired = await session.execute(
        select(func.count(Job.id)).where(Job.expired_at.isnot(None))
    )
    expired_7d = await session.execute(
        select(func.count(Job.id)).where(
            Job.expired_at.isnot(None),
            Job.expired_at >= now - timedelta(days=7),
        )
    )
    expired_30d = await session.execute(
        select(func.count(Job.id)).where(
            Job.expired_at.isnot(None),
            Job.expired_at >= now - timedelta(days=30),
        )
    )
    total_active = await session.execute(
        select(func.count(Job.id)).where(Job.expired_at.is_(None))
    )

    return {
        "total_expired": total_expired.scalar() or 0,
        "expired_last_7d": expired_7d.scalar() or 0,
        "expired_last_30d": expired_30d.scalar() or 0,
        "total_active": total_active.scalar() or 0,
    }


async def check_urls_batch(session: AsyncSession, max_jobs: int = 50) -> dict:
    """HTTP HEAD check on jobs not seen recently to detect dead listings.

    Targets jobs with no last_seen_at or last_seen_at > 7 days ago.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    result = await session.execute(
        select(Job)
        .where(
            Job.expired_at.is_(None),
            (Job.last_seen_at.is_(None)) | (Job.last_seen_at < cutoff),
        )
        .limit(max_jobs)
    )
    jobs = list(result.scalars().all())

    stats = {"checked": 0, "alive": 0, "dead": 0, "errors": 0}

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        for job in jobs:
            stats["checked"] += 1
            try:
                resp = await client.head(job.url)
                if resp.status_code == 404 or resp.status_code == 410:
                    job.expired_at = datetime.now(timezone.utc)
                    stats["dead"] += 1
                else:
                    job.last_seen_at = datetime.now(timezone.utc)
                    stats["alive"] += 1
            except Exception:
                stats["errors"] += 1

    await session.commit()
    logger.info(
        "URL check: %d checked, %d alive, %d dead, %d errors",
        stats["checked"], stats["alive"], stats["dead"], stats["errors"],
    )
    return stats


async def garbage_collect(session: AsyncSession, expired_days: int = 90) -> dict:
    """Archive jobs expired > N days with status='new' (never interacted with)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=expired_days)

    result = await session.execute(
        select(Job).where(
            Job.expired_at.isnot(None),
            Job.expired_at < cutoff,
            Job.status == JobStatus.new,
        )
    )
    jobs = list(result.scalars().all())

    for job in jobs:
        job.status = JobStatus.archived

    await session.commit()
    logger.info("GC: archived %d jobs expired > %d days", len(jobs), expired_days)
    return {"archived": len(jobs)}
