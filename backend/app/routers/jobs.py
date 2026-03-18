import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import JobSource, JobStatus
from app.schemas.jobs import JobCreate, JobList, JobRead, JobUpdate
from app.services import job_service

router = APIRouter(prefix="/api", tags=["jobs"])


@router.get("/jobs", response_model=JobList)
async def list_jobs(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    status: JobStatus | None = None,
    source: JobSource | None = None,
    tag: str | None = None,
    min_relevance: float | None = None,
    min_role_fit: int | None = Query(None, ge=0, le=100),
    min_interest_fit: int | None = Query(None, ge=0, le=100),
    thumbs: int | None = None,
    sort_by: str = "scraped_at",
    sort_dir: str = "desc",
    q: str | None = None,
    remote: bool | None = None,
    company: str | None = None,
    location: str | None = None,
    work_model: str | None = None,
    min_salary: int | None = Query(None, ge=0),
    session: AsyncSession = Depends(get_session),
):
    jobs, total = await job_service.list_jobs(
        session,
        page=page,
        per_page=per_page,
        status=status,
        source=source,
        tag=tag,
        min_relevance=min_relevance,
        min_role_fit=min_role_fit,
        min_interest_fit=min_interest_fit,
        thumbs=thumbs,
        sort_by=sort_by,
        sort_dir=sort_dir,
        q=q,
        remote=remote,
        company=company,
        location=location,
        work_model=work_model,
        min_salary=min_salary,
    )
    return JobList(
        items=[JobRead.model_validate(j) for j in jobs],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/jobs/{job_id}", response_model=JobRead)
async def get_job(job_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    job = await job_service.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/jobs", response_model=JobRead, status_code=201)
async def create_job(body: JobCreate, session: AsyncSession = Depends(get_session)):
    job = await job_service.create_job(session, body.model_dump())
    return job


@router.patch("/jobs/{job_id}", response_model=JobRead)
async def update_job(
    job_id: uuid.UUID, body: JobUpdate, session: AsyncSession = Depends(get_session)
):
    data = body.model_dump(exclude_unset=True)
    job = await job_service.update_job(session, job_id, data)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.delete("/jobs/{job_id}", status_code=204)
async def delete_job(job_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    deleted = await job_service.delete_job(session, job_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Job not found")


@router.post("/jobs/{job_id}/tags", response_model=JobRead)
async def add_tags(
    job_id: uuid.UUID,
    tag_ids: list[uuid.UUID],
    session: AsyncSession = Depends(get_session),
):
    job = await job_service.add_tags_to_job(session, job_id, tag_ids)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.delete("/jobs/{job_id}/tags/{tag_id}", status_code=204)
async def remove_tag(
    job_id: uuid.UUID, tag_id: uuid.UUID, session: AsyncSession = Depends(get_session)
):
    removed = await job_service.remove_tag_from_job(session, job_id, tag_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Job or tag not found")
