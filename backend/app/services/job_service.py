import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job, JobStatus, JobSource, JobTag, Tag
from app.models.locations import JobLocation, Location


async def list_jobs(
    session: AsyncSession,
    *,
    page: int = 1,
    per_page: int = 20,
    status: JobStatus | None = None,
    source: JobSource | None = None,
    tag: str | None = None,
    min_relevance: float | None = None,
    min_role_fit: int | None = None,
    min_interest_fit: int | None = None,
    thumbs: int | None = None,
    sort_by: str = "scraped_at",
    sort_dir: str = "desc",
    q: str | None = None,
    remote: bool | None = None,
    company: str | None = None,
    location: str | None = None,
    work_model: str | None = None,
    min_salary: int | None = None,
) -> tuple[list[Job], int]:
    query = select(Job)
    count_query = select(func.count()).select_from(Job)

    # Filters
    if status is not None:
        query = query.where(Job.status == status)
        count_query = count_query.where(Job.status == status)
    if source is not None:
        query = query.where(Job.source == source)
        count_query = count_query.where(Job.source == source)
    if min_relevance is not None:
        query = query.where(Job.relevance_score >= min_relevance)
        count_query = count_query.where(Job.relevance_score >= min_relevance)
    if min_role_fit is not None:
        query = query.where(Job.role_fit_score >= min_role_fit)
        count_query = count_query.where(Job.role_fit_score >= min_role_fit)
    if min_interest_fit is not None:
        query = query.where(Job.interest_fit_score >= min_interest_fit)
        count_query = count_query.where(Job.interest_fit_score >= min_interest_fit)
    if thumbs is not None:
        query = query.where(Job.thumbs == thumbs)
        count_query = count_query.where(Job.thumbs == thumbs)
    if tag is not None:
        query = query.join(JobTag).join(Tag).where(Tag.name == tag)
        count_query = count_query.join(JobTag).join(Tag).where(Tag.name == tag)
    if remote is not None:
        query = query.where(Job.remote == remote)
        count_query = count_query.where(Job.remote == remote)
    if company is not None:
        company_pattern = f"%{company}%"
        query = query.where(Job.company.ilike(company_pattern))
        count_query = count_query.where(Job.company.ilike(company_pattern))
    if location is not None:
        loc_names = [l.strip() for l in location.split(",") if l.strip()]
        if len(loc_names) == 1:
            loc_filter = Job.id.in_(
                select(JobLocation.job_id)
                .join(Location)
                .where(Location.display_name == loc_names[0])
            )
        else:
            loc_filter = Job.id.in_(
                select(JobLocation.job_id)
                .join(Location)
                .where(Location.display_name.in_(loc_names))
            )
        query = query.where(loc_filter)
        count_query = count_query.where(loc_filter)
    if work_model is not None:
        models = [m.strip() for m in work_model.split(",") if m.strip()]
        if len(models) == 1:
            query = query.where(Job.work_model == models[0])
            count_query = count_query.where(Job.work_model == models[0])
        elif len(models) > 1:
            query = query.where(Job.work_model.in_(models))
            count_query = count_query.where(Job.work_model.in_(models))
    if min_salary is not None:
        sal_filter = func.coalesce(
            func.nullif(Job.salary_max, -1),
            func.nullif(Job.salary_min, -1),
        ) >= min_salary
        query = query.where(sal_filter)
        count_query = count_query.where(sal_filter)
    if q is not None:
        pattern = f"%{q}%"
        text_filter = or_(
            Job.title.ilike(pattern),
            Job.company.ilike(pattern),
            Job.description.ilike(pattern),
        )
        query = query.where(text_filter)
        count_query = count_query.where(text_filter)

    # Sorting
    if sort_by == "salary_avg":
        # Mean of salary_min and salary_max (treat -1 sentinel as NULL for sorting)
        sort_column = func.nullif(
            (func.nullif(Job.salary_min, -1) + func.coalesce(func.nullif(Job.salary_max, -1), func.nullif(Job.salary_min, -1))) / 2,
            None,
        )
    else:
        sort_column = getattr(Job, sort_by, Job.scraped_at)
    if sort_dir == "asc":
        query = query.order_by(sort_column.asc().nulls_last())
    else:
        query = query.order_by(sort_column.desc().nulls_last())

    # Pagination
    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page)

    result = await session.execute(query)
    jobs = list(result.scalars().unique().all())

    total_result = await session.execute(count_query)
    total = total_result.scalar_one()

    return jobs, total


async def get_job(session: AsyncSession, job_id: uuid.UUID) -> Job | None:
    result = await session.execute(select(Job).where(Job.id == job_id))
    return result.scalar_one_or_none()


async def create_job(session: AsyncSession, data: dict) -> Job:
    job = Job(**data)
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


async def update_job(session: AsyncSession, job_id: uuid.UUID, data: dict) -> Job | None:
    job = await get_job(session, job_id)
    if job is None:
        return None
    for key, value in data.items():
        setattr(job, key, value)
    await session.commit()
    await session.refresh(job)
    return job


async def delete_job(session: AsyncSession, job_id: uuid.UUID) -> bool:
    job = await get_job(session, job_id)
    if job is None:
        return False
    await session.delete(job)
    await session.commit()
    return True


async def add_tags_to_job(
    session: AsyncSession, job_id: uuid.UUID, tag_ids: list[uuid.UUID]
) -> Job | None:
    job = await get_job(session, job_id)
    if job is None:
        return None
    for tag_id in tag_ids:
        result = await session.execute(select(Tag).where(Tag.id == tag_id))
        tag = result.scalar_one_or_none()
        if tag and tag not in job.tags:
            job.tags.append(tag)
    await session.commit()
    await session.refresh(job)
    return job


async def remove_tag_from_job(
    session: AsyncSession, job_id: uuid.UUID, tag_id: uuid.UUID
) -> bool:
    job = await get_job(session, job_id)
    if job is None:
        return False
    result = await session.execute(select(Tag).where(Tag.id == tag_id))
    tag = result.scalar_one_or_none()
    if tag and tag in job.tags:
        job.tags.remove(tag)
        await session.commit()
        return True
    return False
