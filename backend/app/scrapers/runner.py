"""Scrape orchestrator — iterate companies, run scrapers, dedupe, persist."""

import logging
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.companies import Company, ScrapeRun
from app.models.jobs import Job, JobSource, JobStatus
from app.scrapers import SCRAPERS_BY_ATS
from app.scrapers.base import ScrapedJob
from app.scrapers.hn_who_is_hiring import scrape_hn_who_is_hiring

logger = logging.getLogger(__name__)

# Iteration order matches SCRAPERS_BY_ATS insertion order (Greenhouse →
# Lever → Ashby → Eightfold). Each scraper self-checks `can_handle()`
# against the company's per-ATS slug field.
SCRAPERS = list(SCRAPERS_BY_ATS.values())


async def run_scrape(
    session: AsyncSession,
    *,
    company_id: uuid.UUID | None = None,
    source_filter: str | None = None,
) -> list[ScrapeRun]:
    """Run scrapers for active companies.

    Args:
        company_id: Limit to a single company.
        source_filter: Limit to scrapers whose ``source_name`` matches.

    Returns:
        List of ScrapeRun records created during this invocation.
    """
    query = select(Company).where(Company.monitoring_active.is_(True))
    if company_id:
        query = query.where(Company.id == company_id)

    result = await session.execute(query)
    companies = list(result.scalars().all())

    runs: list[ScrapeRun] = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        for company in companies:
            for scraper in SCRAPERS:
                if not scraper.can_handle(company):
                    continue
                if source_filter and scraper.source_name != source_filter:
                    continue

                run = ScrapeRun(
                    source=JobSource(scraper.source_name),
                    company_id=company.id,
                    started_at=datetime.now(timezone.utc),
                    status="running",
                )
                session.add(run)
                await session.flush()

                try:
                    scraped_jobs = await scraper.scrape_company(company, client)
                    filtered_jobs = _apply_filters(scraped_jobs, company)
                    new_count, updated_count = await _persist_jobs(
                        session, filtered_jobs, company
                    )

                    # Detect expired jobs (listings missing from current scrape)
                    scraped_urls = {sj.url for sj in scraped_jobs if sj.url}
                    expired_count = await _detect_expired_jobs(
                        session, company, JobSource(scraper.source_name), scraped_urls
                    )

                    run.jobs_found = len(scraped_jobs)
                    run.jobs_new = new_count
                    run.jobs_updated = updated_count
                    run.jobs_expired = expired_count
                    run.status = "completed"
                    run.finished_at = datetime.now(timezone.utc)

                    company.last_scraped_at = datetime.now(timezone.utc)
                except Exception as exc:
                    logger.exception("Scrape failed for %s", company.name)
                    run.status = "failed"
                    run.error_message = str(exc)[:2000]
                    run.finished_at = datetime.now(timezone.utc)

                runs.append(run)

    await session.commit()
    return runs


def _apply_filters(
    scraped_jobs: list[ScrapedJob], company: Company
) -> list[ScrapedJob]:
    """Filter scraped jobs using company include/exclude patterns.

    Matching is case-insensitive substring against title + department/team.
    If include_patterns is set, at least one must match.
    If an include pattern matches, it overrides any exclude match (include wins).
    If only exclude matches (no include match), the job is dropped.
    """
    include = company.include_patterns or []
    exclude = company.exclude_patterns or []

    if not include and not exclude:
        return scraped_jobs

    kept: list[ScrapedJob] = []
    for sj in scraped_jobs:
        # Build searchable text from title + department/team metadata
        parts = [sj.title]
        meta = sj.metadata or {}
        for key in ("departments", "department", "team"):
            val = meta.get(key)
            if isinstance(val, list):
                parts.extend(val)
            elif isinstance(val, str) and val:
                parts.append(val)
        search_text = " ".join(parts).lower()

        has_include = any(pat.lower() in search_text for pat in include)
        has_exclude = any(pat.lower() in search_text for pat in exclude)

        # Include match overrides exclude (e.g. "Engineer" overrides "Sales" dept)
        if has_exclude and not has_include:
            continue

        # Include check (skip if no include patterns — accept all)
        if include and not has_include:
            continue

        kept.append(sj)

    dropped = len(scraped_jobs) - len(kept)
    if dropped:
        logger.info(
            "Filtered %d → %d jobs for %s (%d dropped)",
            len(scraped_jobs), len(kept), company.name, dropped,
        )
    return kept


async def _persist_jobs(
    session: AsyncSession,
    scraped_jobs: list[ScrapedJob],
    company: Company,
) -> tuple[int, int]:
    """Deduplicate and persist scraped jobs. Returns (new, updated) counts."""
    new_count = 0
    updated_count = 0

    for sj in scraped_jobs:
        if not sj.url:
            continue

        # Layer 1: exact URL match → update metadata
        existing = (
            await session.execute(select(Job).where(Job.url == sj.url))
        ).scalar_one_or_none()

        if existing:
            existing.description = sj.description
            existing.description_html = sj.description_html
            existing.extra_metadata = sj.metadata
            existing.last_seen_at = datetime.now(timezone.utc)
            if sj.salary_min:
                existing.salary_min = sj.salary_min
            if sj.salary_max:
                existing.salary_max = sj.salary_max
            updated_count += 1
            continue

        # Layer 2: fuzzy dedup — same company + title (case-insensitive)
        fuzzy = (
            await session.execute(
                select(Job).where(
                    func.lower(Job.company) == company.name.lower(),
                    func.lower(Job.title) == sj.title.lower(),
                )
            )
        ).scalar_one_or_none()

        if fuzzy:
            fuzzy.last_seen_at = datetime.now(timezone.utc)
            logger.debug("Fuzzy duplicate skipped: %s at %s", sj.title, company.name)
            continue

        # New job
        now = datetime.now(timezone.utc)
        job = Job(
            title=sj.title,
            company=company.name,
            company_id=company.id,
            location=sj.location,
            remote=sj.remote,
            salary_min=sj.salary_min,
            salary_max=sj.salary_max,
            description=sj.description,
            description_html=sj.description_html,
            url=sj.url,
            application_url=sj.application_url,
            source=JobSource(sj.source),
            posted_at=sj.posted_at,
            status=JobStatus.new,
            extra_metadata=sj.metadata,
            last_seen_at=now,
        )
        session.add(job)
        new_count += 1

    await session.flush()
    return new_count, updated_count


async def _detect_expired_jobs(
    session: AsyncSession,
    company: Company,
    source: JobSource,
    scraped_urls: set[str],
) -> int:
    """Mark jobs as expired if they're no longer listed by the source.

    ATS APIs (Greenhouse/Lever/Ashby) return the complete active job list,
    so any previously-scraped URL missing from the current results is expired.
    """
    if not scraped_urls:
        return 0

    # Find active jobs for this company+source that aren't in the current scrape
    result = await session.execute(
        select(Job).where(
            Job.company_id == company.id,
            Job.source == source,
            Job.expired_at.is_(None),
            Job.url.notin_(scraped_urls),
        )
    )
    stale_jobs = list(result.scalars().all())

    now = datetime.now(timezone.utc)
    for job in stale_jobs:
        job.expired_at = now

    if stale_jobs:
        logger.info(
            "Expired %d jobs for %s (%s) — no longer listed",
            len(stale_jobs), company.name, source.value,
        )

    return len(stale_jobs)


async def expire_stale_hn_jobs(session: AsyncSession, max_age_days: int = 45) -> int:
    """Expire HN jobs older than max_age_days that are still status='new'.

    HN threads don't have a persistent listing — jobs just age out.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    result = await session.execute(
        select(Job).where(
            Job.source == JobSource.hn_who_is_hiring,
            Job.expired_at.is_(None),
            Job.status == JobStatus.new,
            Job.scraped_at < cutoff,
        )
    )
    stale = list(result.scalars().all())
    now = datetime.now(timezone.utc)
    for job in stale:
        job.expired_at = now

    if stale:
        await session.commit()
        logger.info("Expired %d stale HN jobs (older than %d days)", len(stale), max_age_days)

    return len(stale)


async def run_hn_scrape(session: AsyncSession) -> ScrapeRun:
    """Scrape latest HN 'Who is hiring?' thread. Returns a ScrapeRun record."""
    run = ScrapeRun(
        source=JobSource.hn_who_is_hiring,
        started_at=datetime.now(timezone.utc),
        status="running",
    )
    session.add(run)
    await session.flush()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            scraped_jobs = await scrape_hn_who_is_hiring(client)

        new_count, updated_count = await _persist_hn_jobs(session, scraped_jobs)

        run.jobs_found = len(scraped_jobs)
        run.jobs_new = new_count
        run.jobs_updated = updated_count
        run.status = "completed"
        run.finished_at = datetime.now(timezone.utc)
    except Exception as exc:
        logger.exception("HN scrape failed")
        run.status = "failed"
        run.error_message = str(exc)[:2000]
        run.finished_at = datetime.now(timezone.utc)

    await session.commit()
    return run


async def _persist_hn_jobs(
    session: AsyncSession, scraped_jobs: list[ScrapedJob]
) -> tuple[int, int]:
    """Persist HN jobs (no company_id, dedup by URL only)."""
    new_count = 0
    updated_count = 0

    for sj in scraped_jobs:
        if not sj.url:
            continue

        existing = (
            await session.execute(select(Job).where(Job.url == sj.url))
        ).scalar_one_or_none()

        if existing:
            existing.description = sj.description
            existing.description_html = sj.description_html
            existing.extra_metadata = sj.metadata
            existing.last_seen_at = datetime.now(timezone.utc)
            updated_count += 1
            continue

        now = datetime.now(timezone.utc)
        job = Job(
            title=sj.title,
            company=sj.company_name,
            location=sj.location,
            remote=sj.remote,
            salary_min=sj.salary_min,
            salary_max=sj.salary_max,
            description=sj.description,
            description_html=sj.description_html,
            url=sj.url,
            application_url=sj.application_url,
            source=JobSource.hn_who_is_hiring,
            posted_at=sj.posted_at,
            status=JobStatus.new,
            extra_metadata=sj.metadata,
            last_seen_at=now,
        )
        session.add(job)
        new_count += 1

    await session.flush()
    return new_count, updated_count
