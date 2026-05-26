import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session, get_session
from app.services.pipeline import process_new_jobs
from app.schemas.companies import (
    CompanyCreate,
    CompanyList,
    CompanyListRead,
    CompanyRead,
    CompanyUpdate,
    DiscoverRequest,
    DiscoverResponse,
    ImportRequest,
    ImportResponse,
    RefreshResponse,
)
from app.services import company_service
from app.services.company_discovery import discover_company, import_company, refresh_company_jobs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["companies"])


@router.get("/companies", response_model=CompanyList)
async def list_companies(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    q: str | None = Query(None),
    sort_by: str = Query("name"),
    sort_dir: str = Query("asc"),
    session: AsyncSession = Depends(get_session),
):
    rows, total = await company_service.list_companies_paginated(
        session, page=page, per_page=per_page, q=q, sort_by=sort_by, sort_dir=sort_dir
    )
    items = []
    for row in rows:
        company = row["company"]
        data = CompanyListRead.model_validate(company)
        data.job_count = row["job_count"]
        data.sources = row["sources"]
        items.append(data)
    return CompanyList(items=items, total=total, page=page, per_page=per_page)


@router.post("/companies", response_model=CompanyRead, status_code=201)
async def create_company(body: CompanyCreate, session: AsyncSession = Depends(get_session)):
    company = await company_service.create_company(session, body.model_dump())
    return company


@router.patch("/companies/{company_id}", response_model=CompanyRead)
async def update_company(
    company_id: uuid.UUID,
    body: CompanyUpdate,
    session: AsyncSession = Depends(get_session),
):
    data = body.model_dump(exclude_unset=True)
    company = await company_service.update_company(session, company_id, data)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


@router.delete("/companies/{company_id}", status_code=204)
async def delete_company(
    company_id: uuid.UUID, session: AsyncSession = Depends(get_session)
):
    deleted = await company_service.delete_company_with_jobs(session, company_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Company not found")


@router.post("/companies/{company_id}/refresh", response_model=RefreshResponse)
async def refresh_company_endpoint(
    company_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    try:
        result = await refresh_company_jobs(session, company_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    # Run extraction + scoring pipeline on new jobs (same as import endpoint)
    if result.get("total_jobs", 0) > 0:

        async def _run_process():
            async with async_session() as bg_session:
                try:
                    await process_new_jobs(bg_session)
                except Exception:
                    logger.exception("Pipeline processing after refresh failed")

        background_tasks.add_task(_run_process)

    return RefreshResponse(**result)


@router.get("/companies/scrape-progress")
async def scrape_progress():
    """Lightweight polling endpoint for real-time scrape progress."""
    from app.services.scrape_progress import progress
    return progress.to_dict()


@router.post("/companies/discover", response_model=DiscoverResponse)
async def discover(body: DiscoverRequest, session: AsyncSession = Depends(get_session)):
    # Quick URL resolution first (no scraping) to check for duplicates
    from app.services.company_discovery import resolve_job_url, _find_company_by_slug
    resolution = await resolve_job_url(body.job_url)
    if resolution.ats and resolution.slug:
        existing = await _find_company_by_slug(session, resolution.ats, resolution.slug)
        if existing:
            return DiscoverResponse(
                ats=resolution.ats,
                slug=resolution.slug,
                company_name=existing.name,
                error=f'"{existing.name}" has already been added. '
                      f"Use the Refresh button on the Companies page to check for new jobs.",
            )

    # Not a duplicate — proceed with full discover (scrape + score)
    result = await discover_company(session, body.job_url)
    return DiscoverResponse(**result)


@router.post("/companies/import", response_model=ImportResponse)
async def import_company_endpoint(
    body: ImportRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    result = await import_company(
        session,
        name=body.name,
        website=body.website,
        ats=body.ats,
        slug=body.slug,
        selected_urls=body.selected_urls,
        monitoring_active=body.monitoring_active,
    )

    if result.get("jobs_imported", 0) > 0:

        async def _run_process():
            async with async_session() as bg_session:
                try:
                    await process_new_jobs(bg_session)
                except Exception:
                    logger.exception("Pipeline processing after import failed")

        background_tasks.add_task(_run_process)

        # Pre-warm company research for the freshly imported jobs. By the
        # time the user clicks Generate Resume, Phase 0 will hit cache
        # instead of paying the 2-5 min LLM-native web search penalty.
        job_ids = result.get("job_ids") or []
        if job_ids:
            from app.services.company_research_prewarm import (
                prewarm_company_research_for_jobs,
            )
            background_tasks.add_task(
                prewarm_company_research_for_jobs, job_ids,
            )

    return ImportResponse(**result)
