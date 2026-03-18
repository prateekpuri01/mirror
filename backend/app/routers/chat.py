"""Chat router with SSE streaming for the resume editing agent."""

import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.prompts import format_job_for_scoring
from app.ai.resume_agent import get_resume_agent
from app.ai.resume_builder import _build_markdown
from app.ai.resume_prompts import build_full_profile_for_resume
from app.database import get_session, async_session
from app.models import Company, Document, DocType, Job, UserProfile
from app.schemas.chat import ChatMessageCreate, ChatMessageRead
from app.services import chat_service, document_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])


@router.get("/jobs/{job_id}/chat", response_model=list[ChatMessageRead])
async def list_chat_messages(
    job_id: uuid.UUID, session: AsyncSession = Depends(get_session)
):
    """List all chat messages for a job."""
    return await chat_service.list_messages(session, job_id)


@router.delete("/jobs/{job_id}/chat")
async def clear_chat(
    job_id: uuid.UUID, session: AsyncSession = Depends(get_session)
):
    """Clear all chat messages for a job."""
    count = await chat_service.clear_chat(session, job_id)
    return {"deleted": count}


@router.post("/jobs/{job_id}/chat")
async def send_chat_message(
    job_id: uuid.UUID,
    body: ChatMessageCreate,
    session: AsyncSession = Depends(get_session),
):
    """Send a chat message. Runs the agent and returns an SSE stream."""
    # 1. Validate job exists
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # 2. Find the resume document
    docs = await document_service.list_documents_for_job(session, job_id)
    resume_doc = next((d for d in docs if d.doc_type == DocType.resume and d.content_json), None)
    if resume_doc is None:
        raise HTTPException(
            status_code=400,
            detail="No resume with JSON content found. Generate a resume first.",
        )

    # 3. Persist the user message
    user_msg = await chat_service.add_message(
        session, job_id, "user", body.content, body.section_context
    )

    # 4. Load context
    # Company notes
    company_notes = None
    if job.company_id:
        company_result = await session.execute(
            select(Company).where(Company.id == job.company_id)
        )
        company = company_result.scalar_one_or_none()
        if company and company.notes:
            company_notes = company.notes

    # Profile
    profile_result = await session.execute(select(UserProfile).limit(1))
    profile = profile_result.scalar_one_or_none()
    if profile is None:
        raise HTTPException(status_code=500, detail="User profile not synced")

    profile_text = build_full_profile_for_resume(profile.data)
    job_text = format_job_for_scoring(job, company_notes=company_notes)

    # 5. Load chat history (last 10 messages)
    all_messages = await chat_service.list_messages(session, job_id)
    chat_history = [
        {"role": m.role, "content": m.content}
        for m in all_messages[-10:]
    ]

    # 6. Build agent state
    agent_state = {
        "user_message": body.content,
        "section_context": body.section_context,
        "resume_json": resume_doc.content_json,
        "job_context": job_text,
        "profile_text": profile_text,
        "chat_history": chat_history,
        "intent": None,
        "target_section_path": None,
        "target_section_value": None,
        "response_text": "",
        "updated_json": None,
        "updated_section_path": None,
    }

    # Capture IDs for the streaming generator (session will be closed)
    resume_doc_id = resume_doc.id
    profile_data = profile.data

    async def event_stream():
        """Run the agent and yield SSE events."""
        try:
            # Run the LangGraph agent
            agent = get_resume_agent()
            logger.info(
                "Running chat agent for job %s: message=%r, section_context=%s",
                job_id, body.content[:80], body.section_context,
            )
            final_state = await agent.ainvoke(agent_state)

            response_text = final_state.get("response_text", "")
            updated_json = final_state.get("updated_json")
            updated_path = final_state.get("updated_section_path")
            intent = final_state.get("intent", "unknown")
            logger.info(
                "Agent completed: intent=%s, has_update=%s, response_len=%d",
                intent, updated_json is not None, len(response_text),
            )

            # Stream the response text as tokens (simulate streaming for now)
            # In a real implementation, we'd stream from the LLM directly
            yield f"event: token\ndata: {json.dumps({'text': response_text})}\n\n"

            resume_updated = False

            # If the agent made edits, persist them
            if updated_json is not None:
                async with async_session() as bg_session:
                    if updated_path:
                        # Section-level update
                        from app.services.document_service import _get_nested
                        new_value = _get_nested(updated_json, updated_path)
                        await document_service.update_resume_section(
                            bg_session, resume_doc_id, updated_path, new_value
                        )
                        yield f"event: section_update\ndata: {json.dumps({'path': updated_path, 'value': new_value})}\n\n"
                    else:
                        # Full JSON replacement (broad rewrite)
                        await document_service.update_resume_json(
                            bg_session, resume_doc_id, updated_json
                        )
                        yield f"event: section_update\ndata: {json.dumps({'path': None, 'value': updated_json})}\n\n"

                    # Regenerate markdown
                    doc = await document_service.get_document(bg_session, resume_doc_id)
                    if doc and doc.content_json:
                        doc.content_markdown = _build_markdown(doc.content_json)
                        await bg_session.commit()

                    # Regenerate docx in background (fire and forget)
                    try:
                        from app.ai.docx_builder import build_docx
                        if doc and doc.content_json and doc.job_id:
                            docx_path = build_docx(doc.content_json, profile_data, str(doc.job_id))
                            doc.content_docx_path = docx_path
                            await bg_session.commit()
                    except Exception:
                        logger.exception("Background docx regen failed")

                    resume_updated = True

            # Persist the assistant response
            async with async_session() as bg_session:
                assistant_msg = await chat_service.add_message(
                    bg_session, job_id, "assistant", response_text
                )
                msg_id = str(assistant_msg.id)

            yield f"event: done\ndata: {json.dumps({'message_id': msg_id, 'resume_updated': resume_updated})}\n\n"

        except Exception as e:
            logger.exception("Chat agent error for job %s", job_id)
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
