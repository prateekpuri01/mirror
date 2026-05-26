import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session, get_session
from app.schemas.application_requirements import AppReqCreate, AppReqRead
from app.services import app_req_service
from app.services.app_req_extraction_service import (
    extract_application_requirements,
    get_req_extraction_status,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["requirements"])


async def _learn_from_answer_edit(
    old_value: str,
    new_value: str,
    field_label: str,
    job_id: uuid.UUID,
    job_title: str,
    company: str,
) -> None:
    """Background task: extract writing memory rules from a manual answer
    edit. Owns its own DB session — the request-scoped one is closed by the
    time this fires.
    """
    try:
        from app.ai.writing_memory import extract_and_learn

        async with async_session() as bg_session:
            await extract_and_learn(
                bg_session,
                old_value,
                new_value,
                f"answer.{field_label}",
                job_id,
                domain="answer",
                job_title=job_title,
                company=company,
            )
            await bg_session.commit()
    except Exception:
        logger.debug("Background writing memory extraction (answer) failed", exc_info=True)


class DraftAnswersRequest(BaseModel):
    field_label: str | None = None  # If set, draft only this field
    instructions: str | None = None  # Optional user instructions (single-field only)


class UpdateDraftRequest(BaseModel):
    field_label: str
    draft_response: str


@router.get("/jobs/{job_id}/requirements", response_model=AppReqRead)
async def get_requirements(job_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
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
    from sqlalchemy import select

    from app.models.jobs import Job

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
    background_tasks: BackgroundTasks,
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

    # 4. Load company notes + research
    company_notes = None
    if job.company_id:
        result = await session.execute(select(Company).where(Company.id == job.company_id))
        company = result.scalar_one_or_none()
        if company and company.notes:
            company_notes = company.notes
    company_research = (job.extra_metadata or {}).get("company_research")

    # 4b. Load writing memory for answer drafting
    from app.ai.writing_memory import format_writing_memory

    answer_memory_text = await format_writing_memory(session, "answer")

    # 5. Get short answer fields
    fields = app_req.application_fields
    short_answers = [f for f in fields if f.get("response_type") in ("short_answer", "free_text")]
    if not short_answers:
        raise HTTPException(status_code=400, detail="No short answer fields found")

    if body.field_label:
        # --- Single field draft ---
        from app.ai.answer_drafter import _prune_history

        field = next((f for f in short_answers if f["label"] == body.field_label), None)
        if field is None:
            raise HTTPException(
                status_code=404,
                detail=f"Field '{body.field_label}' not found",
            )

        now = datetime.now(UTC).isoformat()
        history = field.get("draft_history") or []

        # Detect manual edit: if draft_response differs from last ai_draft in history
        current_draft = field.get("draft_response")
        if current_draft and history:
            last_ai = next((e for e in reversed(history) if e["type"] == "ai_draft"), None)
            if last_ai and last_ai["content"] != current_draft:
                history.append({"type": "manual_edit", "content": current_draft, "timestamp": now})

                # Fire writing memory extraction in the background — used to
                # be `await`-ed inline, which kept the redraft request open
                # for the 3–10s the extraction LLM takes. Now the redraft
                # response goes out as soon as the new draft is saved.
                background_tasks.add_task(
                    _learn_from_answer_edit,
                    last_ai["content"],
                    current_draft,
                    body.field_label,
                    job_id,
                    job.title or "",
                    job.company or "",
                )

        # Append instruction if provided
        if body.instructions:
            history.append({"type": "instruction", "content": body.instructions, "timestamp": now})

        logger.info("Drafting single answer for '%s' on job %s", body.field_label, job_id)
        response_text = await draft_single_answer(
            job=job,
            profile_data=profile.data,
            field=field,
            instructions=body.instructions,
            company_notes=company_notes,
            draft_history=history if history else None,
            company_research=company_research,
            memory_text=answer_memory_text,
        )

        # Append AI draft to history and prune
        history.append({"type": "ai_draft", "content": response_text, "timestamp": now})
        history = _prune_history(history)

        # Update the field's draft_response and draft_history in the JSONB
        for f in fields:
            if f["label"] == body.field_label:
                f["draft_response"] = response_text
                f["draft_history"] = history
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
            company_research=company_research,
            memory_text=answer_memory_text,
        )

        now = datetime.now(UTC).isoformat()

        # Update each field's draft_response and reset draft_history
        for f in fields:
            if f["label"] in drafts:
                f["draft_response"] = drafts[f["label"]]
                f["draft_history"] = [
                    {"type": "ai_draft", "content": drafts[f["label"]], "timestamp": now}
                ]

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
    from sqlalchemy.orm.attributes import flag_modified

    from app.models.documents import ApplicationRequirements

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
