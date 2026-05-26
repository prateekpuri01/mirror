"""Background-task helper: pre-warm Job.extra_metadata['company_research'].

Called from the import paths (Add Job by URL, hot-search company import)
so that by the time the user clicks Generate Resume, Phase 0 research is
already cached and the resume pipeline starts in milliseconds instead of
the 2-5 minutes it takes to fire two LLM-native web searches.

Idempotent: `research_company_for_job` checks the cache first and returns
early if research is already populated, so re-firing for jobs that have
been pre-warmed (or had research run previously) is a no-op.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy import select

from app.database import async_session
from app.models import Company, Job

logger = logging.getLogger(__name__)


# Cap concurrent pre-warms. Company research fires LLM-native web searches
# which we already rate-limit downstream, but at the per-search level the
# OpenAI Responses API still parallelizes well. 3 simultaneous is a safe
# ceiling for an import burst of 5-20 jobs.
_PREWARM_CONCURRENCY = 3


async def prewarm_company_research_for_jobs(job_ids: list[str | uuid.UUID]) -> None:
    """Run `research_company_for_job` over each job ID with bounded concurrency.

    Each call gets its own session so a failure on one job doesn't poison
    the others. Persists results to `job.extra_metadata["company_research"]`
    inside that session.
    """
    if not job_ids:
        return

    # Import here so test scaffolds and import-time circular issues don't
    # block the routers that schedule this task.
    from app.ai.company_research import research_company_for_job

    sem = asyncio.Semaphore(_PREWARM_CONCURRENCY)

    async def _one(job_id: str | uuid.UUID) -> None:
        async with sem:
            try:
                async with async_session() as session:
                    job_uuid = job_id if isinstance(job_id, uuid.UUID) else uuid.UUID(str(job_id))
                    job = await session.get(Job, job_uuid)
                    if job is None:
                        return
                    # Cache check is inside research_company_for_job, so
                    # re-firing for already-warmed jobs is a no-op.
                    company = None
                    if job.company_id:
                        result = await session.execute(
                            select(Company).where(Company.id == job.company_id)
                        )
                        company = result.scalar_one_or_none()
                    research = await research_company_for_job(session, job, company)
                    if research:
                        meta = dict(job.extra_metadata or {})
                        meta["company_research"] = research
                        job.extra_metadata = meta
                        from sqlalchemy.orm.attributes import flag_modified

                        flag_modified(job, "extra_metadata")
                        await session.commit()
                        logger.info(
                            "Pre-warmed company research for job %s (%s)",
                            job.id,
                            getattr(job, "title", "?")[:60],
                        )
            except Exception:
                logger.exception("Pre-warm company research failed for job %s", job_id)

    await asyncio.gather(*[_one(jid) for jid in job_ids], return_exceptions=False)
