import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session, get_session
from app.schemas.application_requirements import AppReqCreate, AppReqRead, ExtractionStatusRead
from app.services import app_req_service
from app.services.app_req_extraction_service import (
    extract_application_requirements,
    get_req_extraction_status,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["requirements"])


class DraftAnswersRequest(BaseModel):
    field_label: str | None = None  # If set, draft only this field
    instructions: str | None = None  # Optional user instructions (single-field only)


class UpdateDraftRequest(BaseModel):
    field_label: str
    draft_response: str


@router.get("/jobs/{job_id}/requirements", response_model=AppReqRead)
async def get_requirements(
    job_id: uuid.UUID, session: AsyncSession = Depends(get_session)
):
    req = await app_req_service.get_for_job(session, job_id)
    if req is None:
        raise HTTPException(status_code=404, detail="No requirements found for this job")
    return req


@router.put("/jobs/{job_id}/requirements", response_model=AppReqRead)
async def upsert_requirements(
    job_id: uuid.UUID, body: AppReqCreate, session: AsyncSession = Depends(get_session)
):
    return await app_req_service.upsert(session, job_id, body.model_dump())


async def _run_extraction(job_id: uuid.UUID) -> None:
    """Background task wrapper — creates its own DB session."""
    async with async_session() as session:
        await extract_application_requirements(session, job_id)


@router.post("/jobs/{job_id}/requirements/extract")
async def trigger_extraction(
    job_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    # Verify job exists
    from app.models.jobs import Job
    from sqlalchemy import select

    result = await session.execute(select(Job.id).where(Job.id == job_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Check if already running
    status = get_req_extraction_status(str(job_id))
    if status and status.get("running"):
        return {"status": "already_running", "job_id": str(job_id)}

    background_tasks.add_task(_run_extraction, job_id)
    return {"status": "started", "job_id": str(job_id)}


@router.get("/jobs/{job_id}/requirements/extract/status")
async def extraction_status(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    # Check in-memory status first
    mem_status = get_req_extraction_status(str(job_id))

    # Also check DB for persisted status
    req = await app_req_service.get_for_job(session, job_id)

    if req is None and mem_status is None:
        raise HTTPException(status_code=404, detail="No extraction found for this job")

    return {
        "job_id": str(job_id),
        "extraction_status": req.extraction_status if req else None,
        "extraction_error": req.extraction_error if req else (mem_status or {}).get("error"),
        "extracted_at": req.extracted_at if req else None,
        "extraction_source_url": req.extraction_source_url if req else None,
        "in_progress": bool(mem_status and mem_status.get("running")),
    }


@router.post("/jobs/{job_id}/requirements/draft-answers")
async def draft_answers(
    job_id: uuid.UUID,
    body: DraftAnswersRequest,
    session: AsyncSession = Depends(get_session),
):
    """Draft AI-generated responses for short-answer application fields."""
    from app.ai.answer_drafter import draft_all_answers, draft_single_answer
    from app.models import Company, Job, UserProfile
    from app.models.documents import ApplicationRequirements

    # 1. Load job
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # 2. Load requirements
    result = await session.execute(
        select(ApplicationRequirements).where(ApplicationRequirements.job_id == job_id)
    )
    app_req = result.scalar_one_or_none()
    if app_req is None or not app_req.application_fields:
        raise HTTPException(status_code=404, detail="No extracted fields found")

    # 3. Load user profile
    result = await session.execute(select(UserProfile).limit(1))
    profile = result.scalar_one_or_none()
    if profile is None:
        raise HTTPException(status_code=500, detail="User profile not synced")

    # 4. Load company notes
    company_notes = None
    if job.company_id:
        result = await session.execute(
            select(Company).where(Company.id == job.company_id)
        )
        company = result.scalar_one_or_none()
        if company and company.notes:
            company_notes = company.notes

    # 5. Get short answer fields
    fields = app_req.application_fields
    short_answers = [
        f for f in fields
        if f.get("response_type") in ("short_answer", "free_text")
    ]
    if not short_answers:
        raise HTTPException(status_code=400, detail="No short answer fields found")

    if body.field_label:
        # --- Single field draft ---
        field = next(
            (f for f in short_answers if f["label"] == body.field_label), None
        )
        if field is None:
            raise HTTPException(
                status_code=404,
                detail=f"Field '{body.field_label}' not found",
            )

        logger.info("Drafting single answer for '%s' on job %s", body.field_label, job_id)
        response_text = await draft_single_answer(
            job=job,
            profile_data=profile.data,
            field=field,
            instructions=body.instructions,
            company_notes=company_notes,
        )

        # Update the field's draft_response in the JSONB
        for f in fields:
            if f["label"] == body.field_label:
                f["draft_response"] = response_text
                break

        app_req.application_fields = fields
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(app_req, "application_fields")
        await session.commit()

        return {"field_label": body.field_label, "draft_response": response_text}

    else:
        # --- Draft all short answers ---
        logger.info("Drafting all short answers for job %s", job_id)
        drafts = await draft_all_answers(
            job=job,
            profile_data=profile.data,
            short_answer_fields=short_answers,
            company_notes=company_notes,
        )

        # Update each field's draft_response in the JSONB
        for f in fields:
            if f["label"] in drafts:
                f["draft_response"] = drafts[f["label"]]

        app_req.application_fields = fields
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(app_req, "application_fields")
        await session.commit()

        return {"drafts": drafts}


@router.patch("/jobs/{job_id}/requirements/draft")
async def update_draft(
    job_id: uuid.UUID,
    body: UpdateDraftRequest,
    session: AsyncSession = Depends(get_session),
):
    """Save a manual edit to a draft response."""
    from app.models.documents import ApplicationRequirements
    from sqlalchemy.orm.attributes import flag_modified

    result = await session.execute(
        select(ApplicationRequirements).where(ApplicationRequirements.job_id == job_id)
    )
    app_req = result.scalar_one_or_none()
    if app_req is None or not app_req.application_fields:
        raise HTTPException(status_code=404, detail="No extracted fields found")

    fields = app_req.application_fields
    found = False
    for f in fields:
        if f["label"] == body.field_label:
            f["draft_response"] = body.draft_response
            found = True
            break

    if not found:
        raise HTTPException(status_code=404, detail=f"Field '{body.field_label}' not found")

    app_req.application_fields = fields
    flag_modified(app_req, "application_fields")
    await session.commit()

    return {"field_label": body.field_label, "draft_response": body.draft_response}
