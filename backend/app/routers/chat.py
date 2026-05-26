"""Chat router with SSE streaming for the resume editing agent."""

import asyncio
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.prompts import format_job_for_scoring
from app.ai.resume_agent import get_resume_agent
from app.ai.resume_builder import _build_markdown, normalize_experience_order
from app.ai.resume_prompts import build_full_profile_for_resume
from app.database import async_session, get_session
from app.models import Company, DocType, Job, UserProfile
from app.schemas.chat import ChatMessageCreate, ChatMessageRead
from app.services import chat_service, document_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])


async def _run_writing_memory_extraction(
    old_value: Any,
    new_value: Any,
    section_path: str,
    job_id: uuid.UUID,
    job_title: str,
    job_company: str,
    user_instruction: str,
) -> None:
    """Run extract_and_learn out-of-band so the chat SSE stream can complete
    immediately. Owns its own DB session because the request-scoped one is
    closed by the time this fires.
    """
    try:
        from app.ai.writing_memory import extract_and_learn

        async with async_session() as session:
            await extract_and_learn(
                session,
                old_value,
                new_value,
                section_path,
                job_id,
                domain="resume",
                job_title=job_title,
                company=job_company,
                user_instruction=user_instruction,
            )
            await session.commit()
    except Exception:
        logger.debug("Background writing memory extraction failed", exc_info=True)


@router.get("/jobs/{job_id}/chat", response_model=list[ChatMessageRead])
async def list_chat_messages(job_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    """List all chat messages for a job."""
    return await chat_service.list_messages(session, job_id)


@router.delete("/jobs/{job_id}/chat")
async def clear_chat(job_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
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

    # 2. Find the resume document (use specific doc_id if provided, else latest)
    if body.doc_id:
        resume_doc = await document_service.get_document(session, body.doc_id)
        if (
            resume_doc is None
            or resume_doc.doc_type != DocType.resume
            or not resume_doc.content_json
        ):
            raise HTTPException(status_code=400, detail="Document not found or not a resume.")
    else:
        docs = await document_service.list_documents_for_job(session, job_id)
        resume_doc = next(
            (d for d in docs if d.doc_type == DocType.resume and d.content_json), None
        )
    if resume_doc is None:
        raise HTTPException(
            status_code=400,
            detail="No resume with JSON content found. Generate a resume first.",
        )

    # 3. Persist the user message
    await chat_service.add_message(session, job_id, "user", body.content, body.section_context)

    # 4. Load context
    # Company notes
    company_notes = None
    if job.company_id:
        company_result = await session.execute(select(Company).where(Company.id == job.company_id))
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
    chat_history = [{"role": m.role, "content": m.content} for m in all_messages[-10:]]

    # 5b. Load company research from document or job
    company_research = None
    if resume_doc.content_json:
        company_research = resume_doc.content_json.get("_research")
    if not company_research and job.extra_metadata:
        company_research = (job.extra_metadata or {}).get("company_research")

    # 5c. Load generation log and strategic plan from resume document
    generation_log = None
    strategic_plan = None
    if resume_doc.content_json:
        generation_log = resume_doc.content_json.get("_generation_log")
        strategic_plan = resume_doc.content_json.get("_strategic_plan")

    # 5d. Load writing memory preferences
    from app.ai.writing_memory import format_writing_memory

    writing_memory_text = await format_writing_memory(session, "resume")

    # Pre-route override: deterministic buttons (e.g. "Proofread") send the
    # exact intent so the agent skips classification.
    valid_intent_overrides = {
        "make_edit",
        "ask_question",
        "broad_rewrite",
        "multiple_changes",
        "remember_preference",
        "proofread",
    }
    initial_intent = (
        body.intent_override if body.intent_override in valid_intent_overrides else None
    )

    # 6. Build agent state
    agent_state = {
        "user_message": body.content,
        "section_context": body.section_context,
        "resume_json": resume_doc.content_json,
        "job_context": job_text,
        "profile_text": profile_text,
        "company_research": company_research,
        "chat_history": chat_history,
        "generation_log": generation_log,
        "strategic_plan": strategic_plan,
        "writing_memory_text": writing_memory_text,
        "intent": initial_intent,
        "target_section_path": None,
        "target_section_value": None,
        "response_text": "",
        "updated_json": None,
        "updated_section_path": None,
        "_new_preference": None,
        # Lets edit_section open its own DB session for content_memory
        # grounding fetches, and use a focused profile slice rather than
        # the full ~5–7k-token profile_text dump.
        "_session_factory": async_session,
        "_profile_data": profile.data,
    }

    # Capture IDs for the streaming generator (session will be closed)
    resume_doc_id = resume_doc.id
    profile_data = profile.data
    job_company = job.company
    job_title = job.title

    async def event_stream():
        """Run the agent and yield SSE events."""
        try:
            # Run the LangGraph agent
            agent = get_resume_agent()
            logger.info(
                "Running chat agent for job %s: message=%r, section_context=%s",
                job_id,
                body.content[:80],
                body.section_context,
            )
            final_state = await agent.ainvoke(agent_state)

            response_text = final_state.get("response_text", "")
            updated_json = final_state.get("updated_json")
            updated_path = final_state.get("updated_section_path")
            intent = final_state.get("intent", "unknown")
            logger.info(
                "Agent completed: intent=%s, has_update=%s, response_len=%d",
                intent,
                updated_json is not None,
                len(response_text),
            )

            # Stream the response text as tokens (simulate streaming for now)
            # In a real implementation, we'd stream from the LLM directly
            yield f"event: token\ndata: {json.dumps({'text': response_text})}\n\n"

            resume_updated = False

            # If the agent saved a new writing preference, persist it
            new_pref = final_state.get("_new_preference")
            if new_pref and isinstance(new_pref, dict):
                try:
                    from app.services import writing_memory_service

                    async with async_session() as pref_session:
                        await writing_memory_service.add_rule(
                            pref_session,
                            domain="resume",
                            rule_text=new_pref["rule_text"],
                            category=new_pref.get("category", "content"),
                            scope="universal",
                            source_type="explicit_user",
                            source_job_id=job_id,
                        )
                        await pref_session.commit()
                    logger.info("Saved explicit writing preference: %s", new_pref["rule_text"][:80])
                except Exception:
                    logger.exception("Failed to save writing preference")

            # If the agent made edits, persist them
            if updated_json is not None:
                # Normalize experience ordering to match work_history
                updated_json = normalize_experience_order(updated_json, profile_data)

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

                    # Regenerate markdown (pass profile_data for correct ordering)
                    doc = await document_service.get_document(bg_session, resume_doc_id)
                    if doc and doc.content_json:
                        doc.content_markdown = _build_markdown(
                            doc.content_json, profile_data=profile_data
                        )
                        await bg_session.commit()

                    # Regenerate docx in background (fire and forget)
                    try:
                        from app.ai.docx_builder import build_docx

                        if doc and doc.content_json and doc.job_id:
                            docx_path = build_docx(
                                doc.content_json,
                                profile_data,
                                str(doc.job_id),
                                company=job_company,
                                title=job_title,
                            )
                            doc.content_docx_path = docx_path
                            await bg_session.commit()
                    except Exception:
                        logger.exception("Background docx regen failed")

                    resume_updated = True

                    # Fire writing memory extraction in the background. This
                    # used to be `await`-ed inline, which kept the SSE stream
                    # open (and the chat input disabled) for the 3–10s the
                    # LLM extraction takes. Now it runs in its own task with
                    # its own DB session — the chat `done` event fires as
                    # soon as the assistant response is persisted.
                    if updated_path:
                        from app.services.document_service import _get_nested

                        old_section_value = _get_nested(agent_state["resume_json"], updated_path)
                        new_section_value = _get_nested(updated_json, updated_path)
                        if old_section_value != new_section_value:
                            asyncio.create_task(
                                _run_writing_memory_extraction(
                                    old_section_value,
                                    new_section_value,
                                    updated_path,
                                    job_id,
                                    job_title,
                                    job_company,
                                    body.content,
                                )
                            )

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
