import logging
import os
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session, async_session
from app.schemas.documents import DocumentRead, DocumentRevise, DocumentUpdate, SectionUpdate
from app.services import document_service
from app.ai.resume_builder import generate_resume, get_resume_status, revise_resume

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
    return doc.content_json


@router.patch("/documents/{doc_id}/section", response_model=DocumentRead)
async def update_document_section(
    doc_id: uuid.UUID,
    body: SectionUpdate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """Update a single section in the resume JSON and regenerate markdown + docx."""
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
    return doc


async def _regenerate_docx(doc_id: uuid.UUID):
    """Regenerate .docx from content_json in a background task."""
    from app.ai.docx_builder import build_docx
    from app.models import UserProfile

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
            job_id_str = str(doc.job_id) if doc.job_id else "unknown"
            docx_path = build_docx(doc.content_json, profile.data, job_id_str)
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
    """Download a document's .docx file."""
    doc = await document_service.get_document(session, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if not doc.content_docx_path:
        raise HTTPException(status_code=404, detail="No .docx file available for this document")
    if not os.path.exists(doc.content_docx_path):
        raise HTTPException(status_code=404, detail="File not found on disk")

    filename = os.path.basename(doc.content_docx_path)
    return FileResponse(
        path=doc.content_docx_path,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
