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
    hide_expired: bool = False,
    pin_ids: list[str] | None = None,
) -> tuple[list[Job], int]:
    query = select(Job)
    count_query = select(func.count()).select_from(Job)

    if hide_expired:
        query = query.where(Job.expired_at.is_(None))
        count_query = count_query.where(Job.expired_at.is_(None))

    # Filters
    if status is not None:
        # Honor explicit status filter — including status=archived, so
        # the user can recover soft-deleted rows by surfacing them
        # intentionally.
        query = query.where(Job.status == status)
        count_query = count_query.where(Job.status == status)
    else:
        # Default-exclude soft-deleted (archived) jobs from the active
        # list. They're kept in the DB for scoring calibration (see
        # delete_job() docstring) but the user expects "delete" to
        # remove them from view.
        query = query.where(Job.status != JobStatus.archived)
        count_query = count_query.where(Job.status != JobStatus.archived)
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
        # Location display_names contain commas (e.g. "Boston, MA") so we
        # use pipe as the multi-value separator from the frontend.
        # Support both pipe and legacy comma (only if no pipes present).
        if "|" in location:
            loc_names = [l.strip() for l in location.split("|") if l.strip()]
        else:
            # Single location with possible comma inside (e.g. "Boston, MA")
            loc_names = [location.strip()] if location.strip() else []
        if len(loc_names) == 1:
            loc_filter = Job.id.in_(
                select(JobLocation.job_id)
                .join(Location)
                .where(Location.display_name == loc_names[0])
            )
        elif len(loc_names) > 1:
            loc_filter = Job.id.in_(
                select(JobLocation.job_id)
                .join(Location)
                .where(Location.display_name.in_(loc_names))
            )
        else:
            loc_filter = None
        if loc_filter is not None:
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

    # Pin-IDs handling: when the frontend asks us to keep specific job
    # IDs at the top regardless of sort (e.g. recently-added jobs whose
    # relevance_score=null sorts them last), fetch those rows separately
    # and prepend. They're EXCLUDED from the main query so pagination
    # math stays consistent: pin_ids slots take from the first page's
    # per_page budget, then the sorted remainder fills the rest. Pinned
    # rows are not double-counted in `total`.
    pinned_jobs: list[Job] = []
    if pin_ids:
        # Strip duplicates while preserving caller order
        seen = set()
        deduped_ids = []
        for pid in pin_ids:
            if pid not in seen:
                seen.add(pid)
                deduped_ids.append(pid)
        if deduped_ids:
            query = query.where(~Job.id.in_(deduped_ids))
            count_query = count_query.where(~Job.id.in_(deduped_ids))
            pin_query = select(Job).where(Job.id.in_(deduped_ids))
            pin_result = await session.execute(pin_query)
            pinned_by_id = {
                str(j.id): j for j in pin_result.scalars().unique().all()
            }
            # Preserve the caller's pin order
            pinned_jobs = [
                pinned_by_id[pid] for pid in deduped_ids if pid in pinned_by_id
            ]

    # Pagination — only applies to the un-pinned remainder. On page 1 the
    # pinned rows occupy the leading slots; subsequent pages skip them.
    pin_count = len(pinned_jobs)
    if page == 1:
        remainder = max(0, per_page - pin_count)
        query = query.offset(0).limit(remainder)
    else:
        offset = (page - 1) * per_page - pin_count
        offset = max(0, offset)
        query = query.offset(offset).limit(per_page)

    result = await session.execute(query)
    remainder_rows = list(result.scalars().unique().all())
    jobs = pinned_jobs + remainder_rows if page == 1 else remainder_rows

    total_result = await session.execute(count_query)
    total = total_result.scalar_one() + pin_count

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
    """Soft-delete: dismiss a job by archiving it with thumbs-down.

    The job stays in the DB for scoring calibration but disappears
    from the active job list (which filters by status).
    """
    job = await get_job(session, job_id)
    if job is None:
        return False
    job.status = JobStatus.archived
    job.thumbs = -1
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
