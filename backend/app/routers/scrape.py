import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.companies import ScrapeRun
from app.schemas.scrape import ScrapeResult, ScrapeRunList, ScrapeRunRead
from app.scrapers.runner import run_hn_scrape, run_scrape

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["scraping"])


def _summarize(runs: list[ScrapeRun]) -> ScrapeResult:
    run_reads = [ScrapeRunRead.model_validate(r) for r in runs]
    return ScrapeResult(
        runs=run_reads,
        total_found=sum(r.jobs_found for r in runs),
        total_new=sum(r.jobs_new for r in runs),
        total_updated=sum(r.jobs_updated for r in runs),
    )


@router.post("/scrape/run-now", response_model=ScrapeResult)
async def scrape_run_now(
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """Scrape all active companies with all available scrapers."""
    runs = await run_scrape(session)
    from app.routers.pipeline import _run_process

    background_tasks.add_task(_run_process)
    return _summarize(runs)


@router.post("/scrape/greenhouse", response_model=ScrapeResult)
async def scrape_greenhouse(
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """Scrape only Greenhouse companies."""
    runs = await run_scrape(session, source_filter="greenhouse")
    from app.routers.pipeline import _run_process

    background_tasks.add_task(_run_process)
    return _summarize(runs)


@router.post("/scrape/lever", response_model=ScrapeResult)
async def scrape_lever(
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """Scrape only Lever companies."""
    runs = await run_scrape(session, source_filter="lever")
    from app.routers.pipeline import _run_process

    background_tasks.add_task(_run_process)
    return _summarize(runs)


@router.post("/scrape/ashby", response_model=ScrapeResult)
async def scrape_ashby(
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """Scrape only Ashby companies."""
    runs = await run_scrape(session, source_filter="ashby")
    from app.routers.pipeline import _run_process

    background_tasks.add_task(_run_process)
    return _summarize(runs)


@router.post("/scrape/hn-who-is-hiring", response_model=ScrapeResult)
async def scrape_hn(
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """Scrape the latest HN 'Who is hiring?' thread for AI/research jobs."""
    run = await run_hn_scrape(session)
    from app.routers.pipeline import _run_process

    background_tasks.add_task(_run_process)
    return _summarize([run])


@router.post("/scrape/company/{company_id}", response_model=ScrapeResult)
async def scrape_company(
    company_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """Scrape a single company with all matching scrapers."""
    runs = await run_scrape(session, company_id=company_id)
    if not runs:
        raise HTTPException(
            status_code=404,
            detail="Company not found, not active, or has no matching scraper",
        )
    from app.routers.pipeline import _run_process

    background_tasks.add_task(_run_process)
    return _summarize(runs)


@router.get("/scrape/status", response_model=list[ScrapeRunRead])
async def scrape_status(session: AsyncSession = Depends(get_session)):
    """Latest scrape run per company (most recent first)."""
    # Get the most recent run for each company
    subq = (
        select(
            ScrapeRun.company_id,
            func.max(ScrapeRun.started_at).label("latest"),
        )
        .group_by(ScrapeRun.company_id)
        .subquery()
    )
    result = await session.execute(
        select(ScrapeRun)
        .join(
            subq,
            (ScrapeRun.company_id == subq.c.company_id) & (ScrapeRun.started_at == subq.c.latest),
        )
        .order_by(ScrapeRun.started_at.desc())
    )
    runs = list(result.scalars().all())
    return [ScrapeRunRead.model_validate(r) for r in runs]


@router.get("/scrape/history", response_model=ScrapeRunList)
async def scrape_history(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """Paginated scrape run history."""
    count_result = await session.execute(select(func.count()).select_from(ScrapeRun))
    total = count_result.scalar_one()

    offset = (page - 1) * per_page
    result = await session.execute(
        select(ScrapeRun).order_by(ScrapeRun.started_at.desc()).offset(offset).limit(per_page)
    )
    runs = list(result.scalars().all())

    return ScrapeRunList(
        items=[ScrapeRunRead.model_validate(r) for r in runs],
        total=total,
        page=page,
        per_page=per_page,
    )
