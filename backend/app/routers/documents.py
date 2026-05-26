import logging
import os
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session, async_session
from app.schemas.documents import (
    BulletGenerationRequest,
    DocumentRead,
    DocumentRevise,
    DocumentUpdate,
    ResearchEntryRequest,
    SectionHistoryItem,
    SectionHistoryResponse,
    SectionUpdate,
)
from app.services import document_service
from app.ai.resume_builder import (
    generate_resume,
    generate_research_entry,
    generate_single_bullet,
    get_resume_status,
    revise_resume,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["documents"])


@router.get("/jobs/{job_id}/documents", response_model=list[DocumentRead])
async def list_job_documents(
    job_id: uuid.UUID, session: AsyncSession = Depends(get_session)
):
    return await document_service.list_documents_for_job(session, job_id)


@router.get("/documents/{doc_id}", response_model=DocumentRead)
async def get_document(doc_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    doc = await document_service.get_document(session, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.patch("/documents/{doc_id}", response_model=DocumentRead)
async def update_document(
    doc_id: uuid.UUID, body: DocumentUpdate, session: AsyncSession = Depends(get_session)
):
    doc = await document_service.update_document_markdown(session, doc_id, body.content_markdown)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.delete("/documents/{doc_id}", status_code=204)
async def delete_document(doc_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    deleted = await document_service.delete_document(session, doc_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found")


# ---------------------------------------------------------------------------
# Resume JSON + section endpoints
# ---------------------------------------------------------------------------


@router.get("/documents/{doc_id}/json")
async def get_document_json(doc_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    """Return the resume's content_json."""
    doc = await document_service.get_document(session, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.content_json is None:
        raise HTTPException(status_code=404, detail="No JSON content available")
    # Lazy-migrate old bullet format to per-bullet objects
    from app.services.document_service import migrate_resume_json
    return migrate_resume_json(doc.content_json)


@router.patch("/documents/{doc_id}/section", response_model=DocumentRead)
async def update_document_section(
    doc_id: uuid.UUID,
    body: SectionUpdate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """Update a single section in the resume JSON and regenerate markdown + docx."""
    # Capture old value before update for writing memory extraction
    from app.services.document_service import _get_nested
    doc_pre = await document_service.get_document(session, doc_id)
    old_value = None
    pre_job_id = None
    if doc_pre and doc_pre.content_json:
        try:
            old_value = _get_nested(doc_pre.content_json, body.path)
        except (KeyError, IndexError, TypeError):
            pass
        pre_job_id = doc_pre.job_id

    doc = await document_service.update_resume_section(session, doc_id, body.path, body.value)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found or no JSON content")

    # Regenerate markdown from updated JSON
    from app.ai.resume_builder import _build_markdown
    doc.content_markdown = _build_markdown(doc.content_json)
    await session.commit()
    await session.refresh(doc)

    # Regenerate docx in background
    background_tasks.add_task(_regenerate_docx, doc_id)

    # Fire background memory writes if we have a diff
    if old_value is not None and old_value != body.value:
        background_tasks.add_task(
            _learn_from_inline_edit,
            old_value, body.value, body.path, pre_job_id, doc_id,
        )

    return doc


# Paths whose edits feed the abstract-style writing_memory extractor.
# Content paths (research descriptions, experience bullets) are now stored
# verbatim in content_memory instead, so we don't pollute writing_memory
# with low-signal one-off content rewrites.
_WRITING_MEMORY_PATH_PREFIXES = (
    "summary",
    "tagline",
    "technical_skills.",
)


def _path_feeds_writing_memory(section_path: str) -> bool:
    return section_path == "summary" or section_path == "tagline" or any(
        section_path.startswith(p) for p in _WRITING_MEMORY_PATH_PREFIXES
    )


async def _learn_from_inline_edit(
    old_value, new_value, section_path: str, job_id, doc_id: uuid.UUID,
):
    """Background task: persist content_memory + (selectively) writing_memory."""
    from sqlalchemy import select
    from app.ai.writing_memory import extract_and_learn
    from app.models import Job, UserProfile
    from app.services import content_memory_service, document_service

    try:
        async with async_session() as session:
            doc = await document_service.get_document(session, doc_id)
            content_json = doc.content_json if doc else None

            profile_result = await session.execute(select(UserProfile).limit(1))
            profile = profile_result.scalar_one_or_none()
            profile_data = profile.data if profile else None

            # 1. Content memory — store the user's final text per entity.
            if content_json is not None:
                try:
                    await content_memory_service.upsert_from_edit(
                        session,
                        doc_id=doc_id,
                        path=section_path,
                        content_json=content_json,
                        profile_data=profile_data,
                    )
                except Exception:
                    logger.exception(
                        "content_memory upsert failed for doc=%s path=%s",
                        doc_id, section_path,
                    )

            # 2. Writing memory — only for paths that surface abstract style
            # rules (summary / tagline / skills). Per-accomplishment content
            # edits flow into content_memory instead.
            if _path_feeds_writing_memory(section_path):
                job_title, company = "", ""
                if job_id:
                    job_result = await session.execute(select(Job).where(Job.id == job_id))
                    job = job_result.scalar_one_or_none()
                    if job:
                        job_title = job.title or ""
                        company = job.company or ""
                try:
                    await extract_and_learn(
                        session, old_value, new_value, section_path, job_id,
                        domain="resume", job_title=job_title, company=company,
                    )
                except Exception:
                    logger.exception(
                        "writing_memory extraction failed for doc=%s path=%s",
                        doc_id, section_path,
                    )

            await session.commit()
    except Exception:
        logger.exception("inline-edit memory persistence failed (doc=%s, path=%s)",
                         doc_id, section_path)


@router.get("/documents/{doc_id}/section-history", response_model=SectionHistoryResponse)
async def get_section_history(
    doc_id: uuid.UUID,
    entity_type: str,
    entity_key: str,
    session: AsyncSession = Depends(get_session),
):
    """Past content_memory versions for an entity (excluding the current doc).

    Frontend uses this to populate a "past versions" dropdown next to each
    editable section so the user can swap in a previously-edited phrasing.
    """
    from app.services import content_memory_service

    rows = await content_memory_service.fetch_section_history(
        session,
        entity_type=entity_type,
        entity_key=entity_key,
        exclude_doc_id=doc_id,
    )

    items: list[SectionHistoryItem] = []
    for row in rows:
        ctx = row.job_context or {}
        if row.user_text:
            preview = row.user_text[:200]
        elif isinstance(row.user_payload_json, list):
            first_text = ""
            for b in row.user_payload_json:
                if isinstance(b, dict) and b.get("text"):
                    first_text = b["text"]
                    break
                if isinstance(b, str) and b.strip():
                    first_text = b
                    break
            n = len(row.user_payload_json)
            preview = f"{n} bullet{'s' if n != 1 else ''}: {first_text[:160]}"
        else:
            preview = ""
        items.append(SectionHistoryItem(
            id=row.id,
            job_title=ctx.get("job_title") or None,
            company_name=ctx.get("company_name") or None,
            updated_at=row.updated_at,
            preview=preview,
            user_text=row.user_text,
            user_payload_json=row.user_payload_json,
        ))

    return SectionHistoryResponse(
        entity_type=entity_type,
        entity_key=entity_key,
        items=items,
    )


@router.get("/jobs/{job_id}/company-research")
async def get_company_research(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """Return cached company research for a job, if available."""
    from app.models import Job
    result = await session.execute(
        __import__("sqlalchemy").select(Job).where(Job.id == job_id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    research = (job.extra_metadata or {}).get("company_research")
    if not research:
        return {"available": False, "research": None}
    return {"available": True, "research": research}


@router.post("/jobs/{job_id}/company-research")
async def run_company_research(
    job_id: uuid.UUID,
    force: bool = False,
    session: AsyncSession = Depends(get_session),
):
    """Run or refresh Perplexity-powered company/team research for a job."""
    from app.models import Company, Job
    from app.ai.company_research import research_company_for_job
    from sqlalchemy import select

    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    company = None
    if job.company_id:
        company_result = await session.execute(
            select(Company).where(Company.id == job.company_id)
        )
        company = company_result.scalar_one_or_none()

    research = await research_company_for_job(session, job, company, force=force)
    # Persist to job metadata
    meta = dict(job.extra_metadata or {})
    meta["company_research"] = research
    job.extra_metadata = meta
    await session.commit()
    return {"research": research}


@router.post("/documents/{doc_id}/generate-research-entry")
async def generate_research_entry_endpoint(
    doc_id: uuid.UUID,
    body: ResearchEntryRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """Generate a tailored research entry for a specific accomplishment and swap it in."""
    doc = await document_service.get_document(session, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.content_json is None:
        raise HTTPException(status_code=400, detail="Document has no JSON content")
    if doc.job_id is None:
        raise HTTPException(status_code=400, detail="Document has no associated job")

    selected_research = doc.content_json.get("selected_research", [])
    if body.index < 0 or body.index >= len(selected_research):
        raise HTTPException(status_code=400, detail=f"Invalid index {body.index}")

    # Generate the entry via LLM
    entry = await generate_research_entry(session, body.accomplishment_id, doc.job_id)

    # Save it into the document
    updated_doc = await document_service.update_resume_section(
        session, doc_id, f"selected_research.{body.index}", entry
    )
    if updated_doc is None:
        raise HTTPException(status_code=500, detail="Failed to update document")

    # Regenerate markdown
    from app.ai.resume_builder import _build_markdown
    updated_doc.content_markdown = _build_markdown(updated_doc.content_json)
    await session.commit()
    await session.refresh(updated_doc)

    # Regenerate docx in background
    background_tasks.add_task(_regenerate_docx, doc_id)

    return {"entry": entry, "document": DocumentRead.model_validate(updated_doc)}


@router.post("/documents/{doc_id}/generate-bullet")
async def generate_bullet_endpoint(
    doc_id: uuid.UUID,
    body: BulletGenerationRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """Generate one experience bullet from a specific accomplishment and
    insert it into the named employer's bullet array.

    Mirrors ``generate-research-entry``: backend handles the LLM call +
    persistence + DOCX regen so the frontend only has to wait on one
    request and immediately gets the updated document back.
    """
    doc = await document_service.get_document(session, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.content_json is None:
        raise HTTPException(status_code=400, detail="Document has no JSON content")

    experience = doc.content_json.get("experience") or {}
    emp_block = experience.get(body.employer_key)
    if emp_block is None:
        raise HTTPException(
            status_code=400,
            detail=f"Employer key {body.employer_key!r} not present in resume",
        )

    try:
        new_bullet = await generate_single_bullet(
            session,
            doc_id=doc_id,
            employer_key=body.employer_key,
            accomplishment_id=body.accomplishment_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("Bullet generation failed (doc=%s, emp=%s, acc=%s)",
                         doc_id, body.employer_key, body.accomplishment_id)
        raise HTTPException(status_code=500, detail="Bullet generation failed")

    bullets = list(emp_block.get("bullets") or [])
    insert_at = body.insert_at if body.insert_at is not None else len(bullets)
    insert_at = max(0, min(insert_at, len(bullets)))
    bullets.insert(insert_at, new_bullet)

    updated_doc = await document_service.update_resume_section(
        session, doc_id, f"experience.{body.employer_key}.bullets", bullets,
    )
    if updated_doc is None:
        raise HTTPException(status_code=500, detail="Failed to persist new bullet")

    from app.ai.resume_builder import _build_markdown
    updated_doc.content_markdown = _build_markdown(updated_doc.content_json)
    await session.commit()
    await session.refresh(updated_doc)

    background_tasks.add_task(_regenerate_docx, doc_id)

    return {
        "bullet": new_bullet,
        "insert_at": insert_at,
        "document": DocumentRead.model_validate(updated_doc),
    }


async def _regenerate_docx(doc_id: uuid.UUID):
    """Regenerate .docx from content_json in a background task."""
    from app.ai.docx_builder import build_docx
    from app.models import Job, UserProfile

    async with async_session() as session:
        try:
            doc = await document_service.get_document(session, doc_id)
            if doc is None or doc.content_json is None:
                return
            from sqlalchemy import select
            result = await session.execute(
                select(UserProfile).limit(1)
            )
            profile = result.scalar_one_or_none()
            if profile is None:
                return
            # Load job for company/title in filename
            job_id_str = str(doc.job_id) if doc.job_id else "unknown"
            company = None
            title = None
            if doc.job_id:
                job_result = await session.execute(
                    select(Job).where(Job.id == doc.job_id)
                )
                job = job_result.scalar_one_or_none()
                if job:
                    company = job.company
                    title = job.title
            docx_path = build_docx(
                doc.content_json, profile.data, job_id_str,
                company=company, title=title,
            )
            doc.content_docx_path = docx_path
            await session.commit()
        except Exception:
            logger.exception("Background docx regeneration failed for doc %s", doc_id)


# ---------------------------------------------------------------------------
# Resume generation endpoints
# ---------------------------------------------------------------------------


async def _run_resume_generation(job_id: uuid.UUID):
    """Run resume generation in a background task with its own session."""
    async with async_session() as session:
        try:
            await generate_resume(session, job_id)
        except Exception:
            logger.exception("Resume generation failed for job %s", job_id)


@router.post("/jobs/{job_id}/generate-resume")
async def generate_resume_endpoint(
    job_id: uuid.UUID,
    background_tasks: BackgroundTasks,
):
    """Start tailored resume generation for a job (runs in background)."""
    status = get_resume_status()
    if status["running"]:
        raise HTTPException(
            status_code=409,
            detail="Resume generation already in progress",
        )
    background_tasks.add_task(_run_resume_generation, job_id)
    return {"status": "generating", "job_id": str(job_id)}


@router.get("/resume-status")
async def resume_generation_status_global():
    """Check resume generation/revision progress (global)."""
    return get_resume_status()


@router.get("/jobs/{job_id}/generate-resume/status")
async def resume_generation_status(job_id: uuid.UUID):
    """Check resume generation progress."""
    return get_resume_status()


async def _run_resume_revision(doc_id: uuid.UUID, instruction: str):
    """Run resume revision in a background task with its own session."""
    async with async_session() as session:
        try:
            await revise_resume(session, doc_id, instruction)
        except Exception:
            logger.exception("Resume revision failed for doc %s", doc_id)


@router.post("/documents/{doc_id}/revise")
async def revise_document(
    doc_id: uuid.UUID,
    body: DocumentRevise,
    background_tasks: BackgroundTasks,
):
    """Revise a resume based on user instructions (runs in background)."""
    status = get_resume_status()
    if status["running"]:
        raise HTTPException(
            status_code=409,
            detail="Resume generation/revision already in progress",
        )
    background_tasks.add_task(_run_resume_revision, doc_id, body.instruction)
    return {"status": "revising", "doc_id": str(doc_id)}


@router.get("/documents/{doc_id}/download")
async def download_document(doc_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    """Download a document's .docx, re-rendered with the user's current design.

    Style (layout/color/font) is applied at request time from
    ``user_profile.data["resume_design"]`` so previously-generated resumes
    immediately pick up design changes from the profile tab. Content
    (sections, bullets) comes from the stored ``content_json`` — that's
    immutable per-document.

    Falls back to the cached file on disk only when there's no content_json
    to rebuild from (legacy docs predating the JSON storage).
    """
    from sqlalchemy import select
    from app.ai.docx_builder import build_docx
    from app.models import Job, UserProfile

    doc = await document_service.get_document(session, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    # Legacy fallback: serve the cached file if we don't have JSON to rebuild from.
    if doc.content_json is None:
        if not doc.content_docx_path or not os.path.exists(doc.content_docx_path):
            raise HTTPException(status_code=404, detail="No .docx file available for this document")
        filename = os.path.basename(doc.content_docx_path)
        return FileResponse(
            path=doc.content_docx_path,
            filename=filename,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

    profile_result = await session.execute(select(UserProfile).limit(1))
    profile = profile_result.scalar_one_or_none()
    if profile is None:
        raise HTTPException(status_code=500, detail="User profile not found")

    company = None
    title = None
    if doc.job_id:
        job_result = await session.execute(select(Job).where(Job.id == doc.job_id))
        job = job_result.scalar_one_or_none()
        if job is not None:
            company = job.company
            title = job.title

    docx_path = build_docx(
        resume_data=doc.content_json,
        profile_data=profile.data,
        job_id=str(doc.job_id) if doc.job_id else str(doc_id),
        company=company,
        title=title,
    )
    # Persist the freshly-built path so the editor's cached pointer stays valid.
    doc.content_docx_path = docx_path
    await session.commit()

    filename = os.path.basename(docx_path)
    return FileResponse(
        path=docx_path,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
