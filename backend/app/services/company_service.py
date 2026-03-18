import uuid

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.companies import Company
from app.models.jobs import Job


async def list_companies(session: AsyncSession) -> list[Company]:
    result = await session.execute(
        select(Company).order_by(Company.name.asc())
    )
    return list(result.scalars().all())


async def list_companies_with_counts(session: AsyncSession) -> list[dict]:
    """List all companies with job_count and derived sources."""
    job_count_sub = (
        select(
            Job.company_id,
            func.count(Job.id).label("job_count"),
        )
        .where(Job.company_id.isnot(None))
        .group_by(Job.company_id)
        .subquery()
    )

    query = (
        select(Company, func.coalesce(job_count_sub.c.job_count, 0).label("job_count"))
        .outerjoin(job_count_sub, Company.id == job_count_sub.c.company_id)
        .order_by(Company.name.asc())
    )

    result = await session.execute(query)
    rows = result.all()

    companies = []
    for company, job_count in rows:
        # Derive sources from which slug fields are set
        sources = []
        if company.greenhouse_slug:
            sources.append("greenhouse")
        if company.lever_slug:
            sources.append("lever")
        if company.ashby_slug:
            sources.append("ashby")
        if company.careers_url:
            sources.append("website")

        companies.append({
            "company": company,
            "job_count": job_count,
            "sources": sources,
        })

    return companies


async def list_companies_paginated(
    session: AsyncSession,
    page: int = 1,
    per_page: int = 50,
    q: str | None = None,
    sort_by: str = "name",
    sort_dir: str = "asc",
) -> tuple[list[dict], int]:
    """List companies with job_count/sources, supporting search, sort, and pagination."""
    job_count_sub = (
        select(
            Job.company_id,
            func.count(Job.id).label("job_count"),
        )
        .where(Job.company_id.isnot(None))
        .group_by(Job.company_id)
        .subquery()
    )

    base = select(Company, func.coalesce(job_count_sub.c.job_count, 0).label("job_count")).outerjoin(
        job_count_sub, Company.id == job_count_sub.c.company_id
    )

    if q:
        pattern = f"%{q}%"
        base = base.where(or_(Company.name.ilike(pattern), Company.notes.ilike(pattern)))

    # Sorting
    sort_map = {
        "name": Company.name,
        "job_count": func.coalesce(job_count_sub.c.job_count, 0),
        "last_scraped_at": Company.last_scraped_at,
        "created_at": Company.created_at,
    }
    sort_col = sort_map.get(sort_by, Company.name)
    order = sort_col.desc() if sort_dir == "desc" else sort_col.asc()
    base = base.order_by(order)

    # Count
    count_query = select(func.count()).select_from(base.subquery())
    total = (await session.execute(count_query)).scalar() or 0

    # Paginate
    query = base.offset((page - 1) * per_page).limit(per_page)
    result = await session.execute(query)
    rows = result.all()

    companies = []
    for company, job_count in rows:
        sources = []
        if company.greenhouse_slug:
            sources.append("greenhouse")
        if company.lever_slug:
            sources.append("lever")
        if company.ashby_slug:
            sources.append("ashby")
        if company.careers_url:
            sources.append("website")
        companies.append({
            "company": company,
            "job_count": job_count,
            "sources": sources,
        })

    return companies, total


async def delete_company_with_jobs(session: AsyncSession, company_id: uuid.UUID) -> bool:
    """Delete a company and all its associated jobs (cascades to job children)."""
    company = await get_company(session, company_id)
    if not company:
        return False
    await session.execute(delete(Job).where(Job.company_id == company_id))
    await session.delete(company)
    await session.commit()
    return True


async def get_company(session: AsyncSession, company_id: uuid.UUID) -> Company | None:
    result = await session.execute(select(Company).where(Company.id == company_id))
    return result.scalar_one_or_none()


async def create_company(session: AsyncSession, data: dict) -> Company:
    company = Company(**data)
    session.add(company)
    await session.commit()
    await session.refresh(company)
    return company


async def update_company(
    session: AsyncSession, company_id: uuid.UUID, data: dict
) -> Company | None:
    company = await get_company(session, company_id)
    if company is None:
        return None
    for key, value in data.items():
        setattr(company, key, value)
    await session.commit()
    await session.refresh(company)
    return company


async def delete_company(session: AsyncSession, company_id: uuid.UUID) -> bool:
    company = await get_company(session, company_id)
    if company is None:
        return False
    await session.delete(company)
    await session.commit()
    return True
