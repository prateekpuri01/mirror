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
from app.services import chat_service, document_service, edit_exemplars_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])


async def _run_exemplar_upsert(
    *,
    job_id: uuid.UUID,
    doc_id: uuid.UUID,
    section_path: str,
    before_value: Any,
    after_value: Any,
    instruction: str,
    source: str,
) -> None:
    """Background helper: one section's exemplar upsert. Owns its own DB
    session because the request-scoped one is closed by the time this fires."""
    try:
        async with async_session() as session:
            await edit_exemplars_service.upsert_from_edit(
                session,
                job_id=job_id,
                doc_id=doc_id,
                section_path=section_path,
                before_value=before_value,
                after_value=after_value,
                instruction=instruction,
                source=source,
            )
    except Exception:
        logger.exception("edit_exemplars upsert failed for path=%s", section_path)


def _enumerate_resume_paths(resume_json: dict) -> list[str]:
    """Yield every section path we treat as an editable atom.

    Mirrors the paths the chat router actually persists edits to: summary,
    tagline, awards, technical_skills.<bucket>, selected_research.N (whole
    entries), experience.<emp>.bullets (whole arrays). Sub-fields beneath
    those (e.g. selected_research.N.description) are addressed by direct
    edit_section calls, not broad_rewrite, so we don't need to enumerate
    them here.
    """
    paths: list[str] = []
    for key in ("summary", "tagline", "awards"):
        if key in (resume_json or {}):
            paths.append(key)
    skills = (resume_json or {}).get("technical_skills") or {}
    if isinstance(skills, dict):
        for bucket in skills:
            paths.append(f"technical_skills.{bucket}")
    research = (resume_json or {}).get("selected_research") or []
    for i, _ in enumerate(research):
        paths.append(f"selected_research.{i}")
    experience = (resume_json or {}).get("experience") or {}
    for emp_key in experience:
        paths.append(f"experience.{emp_key}.bullets")
    return paths


async def _run_broad_rewrite_exemplars(
    *,
    job_id: uuid.UUID,
    doc_id: uuid.UUID,
    before_json: dict,
    after_json: dict,
    instruction: str,
) -> None:
    """Background helper: diff each section after a broad rewrite and upsert
    one exemplar per changed section."""
    try:
        from app.services.document_service import _get_nested

        async with async_session() as session:
            for path in _enumerate_resume_paths(after_json):
                try:
                    before = _get_nested(before_json, path)
                except (KeyError, IndexError, TypeError):
                    before = None
                try:
                    after = _get_nested(after_json, path)
                except (KeyError, IndexError, TypeError):
                    after = None
                if before == after:
                    continue
                await edit_exemplars_service.upsert_from_edit(
                    session,
                    job_id=job_id,
                    doc_id=doc_id,
                    section_path=path,
                    before_value=before,
                    after_value=after,
                    instruction=instruction,
                    source="broad_rewrite",
                )
    except Exception:
        logger.exception("broad_rewrite exemplar upsert failed")


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

    # Pre-route override: deterministic UI affordances skip the classifier.
    # Live: proofread (read-only), quick_edit (commit-direct, bypasses card).
    # Legacy intents accepted for backward compatibility — route_intent
    # remaps them to the new set.
    valid_intent_overrides = {
        "scoped_edit",
        "quick_edit",
        "brainstorm",
        "broad_rewrite",
        "remember_preference",
        "proofread",
        # Legacy aliases — remapped in route_intent.
        "make_edit",
        "ask_question",
        "multiple_changes",
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
        "_job_id": job_id,
        "_doc_id": resume_doc.id,
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
                            # Exemplar capture — convergence record per
                            # (doc, section_path). Background to avoid
                            # blocking the SSE stream on embedding latency.
                            asyncio.create_task(
                                _run_exemplar_upsert(
                                    job_id=job_id,
                                    doc_id=resume_doc_id,
                                    section_path=updated_path,
                                    before_value=old_section_value,
                                    after_value=new_section_value,
                                    instruction=body.content,
                                    source="edit_section",
                                )
                            )
                    elif updated_json is not None:
                        # broad_rewrite: diff per section and capture each
                        # changed section as its own exemplar.
                        asyncio.create_task(
                            _run_broad_rewrite_exemplars(
                                job_id=job_id,
                                doc_id=resume_doc_id,
                                before_json=agent_state["resume_json"],
                                after_json=updated_json,
                                instruction=body.content,
                            )
                        )

            # Persist the assistant response (and any brainstorm action cards
            # attached to it) in a single session.
            action_cards_raw = final_state.get("_action_cards") or []
            async with async_session() as bg_session:
                assistant_msg = await chat_service.add_message(
                    bg_session, job_id, "assistant", response_text
                )
                msg_id = str(assistant_msg.id)

                if action_cards_raw:
                    persisted = await chat_service.persist_action_cards(
                        bg_session, job_id, assistant_msg.id, action_cards_raw
                    )
                    for row in persisted:
                        yield (
                            "event: action_card\n"
                            "data: "
                            + json.dumps(
                                {
                                    "id": str(row.id),
                                    "message_id": msg_id,
                                    "card_index": row.card_index,
                                    "kind": row.kind,
                                    "section_path": row.section_path,
                                    "rationale": row.rationale,
                                    "proposed_value": row.proposed_value,
                                    "status": row.status,
                                }
                            )
                            + "\n\n"
                        )

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


# ---------------------------------------------------------------------------
# Action cards (brainstorm-emitted edit suggestions)
# ---------------------------------------------------------------------------


@router.get("/jobs/{job_id}/chat/action_cards")
async def list_action_cards(
    job_id: uuid.UUID, session: AsyncSession = Depends(get_session)
):
    """List action cards for a job (used by the frontend on reload so the
    inline cards re-appear next to their messages with current status)."""
    cards = await chat_service.list_action_cards_for_job(session, job_id)
    return [
        {
            "id": str(c.id),
            "message_id": str(c.message_id),
            "card_index": c.card_index,
            "kind": c.kind,
            "section_path": c.section_path,
            "rationale": c.rationale,
            "proposed_value": c.proposed_value,
            "status": c.status,
            "created_at": c.created_at.isoformat(),
            "resolved_at": c.resolved_at.isoformat() if c.resolved_at else None,
        }
        for c in cards
    ]


@router.post("/chat/action_cards/{card_id}/dismiss")
async def dismiss_action_card(
    card_id: uuid.UUID, session: AsyncSession = Depends(get_session)
):
    """Mark an action card as dismissed. No model call."""
    card = await chat_service.get_action_card(session, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Action card not found")
    if card.status != "pending":
        return {"id": str(card.id), "status": card.status}
    updated = await chat_service.mark_action_card(session, card_id, "dismissed")
    return {"id": str(updated.id), "status": updated.status}


def _resolve_card_value(
    kind: str, proposed_value: str, current_value: object
) -> object:
    """Convert a card's `proposed_value` (always a string) into the value that
    will be written to the resume JSON, verbatim — no LLM polish.

    Strategy: try to JSON-parse `proposed_value`. If it parses and matches the
    current section's type, use the parsed value (with a soft merge for dicts
    so `accomplishment_id`-style metadata isn't dropped when the brainstorm
    omits it). Otherwise treat `proposed_value` as a plain string and adapt
    it to the section shape.
    """
    parsed: object | None = None
    try:
        parsed = json.loads(proposed_value)
    except (json.JSONDecodeError, TypeError):
        parsed = None

    # add_bullet appends to a bullets array regardless of what was parsed.
    if kind == "add_bullet":
        bullet_obj: dict
        if isinstance(parsed, dict) and "text" in parsed:
            bullet_obj = {
                "text": str(parsed.get("text") or "").strip(),
                "accomplishment_ids": list(parsed.get("accomplishment_ids") or []),
            }
        else:
            bullet_obj = {"text": proposed_value.strip(), "accomplishment_ids": []}
        if isinstance(current_value, list):
            return [*current_value, bullet_obj]
        # Defensive: if current is None / not a list, return a single-element list.
        return [bullet_obj]

    # remove_section: destructive; not supported via verbatim apply. The
    # brainstorm prompt is told not to emit these.
    if kind == "remove_section":
        raise HTTPException(
            status_code=400,
            detail=(
                "remove_section cards are not supported via apply. Use a "
                "follow-up chat message to remove content explicitly."
            ),
        )

    # Default path: rewrite_section / replace_selected_research / etc.
    # If the parsed JSON matches the current type, use it.
    if isinstance(current_value, dict) and isinstance(parsed, dict):
        merged = dict(current_value)
        merged.update(parsed)
        return merged
    if isinstance(current_value, list) and isinstance(parsed, list):
        return parsed
    if isinstance(current_value, str) and isinstance(parsed, str):
        return parsed

    # Mismatched types or unparsed: fall back to plain string handling.
    if isinstance(current_value, dict):
        # Brainstorm sent free text for a dict-shaped section. Update the
        # most prose-like field (description for selected_research entries,
        # text for bullets). Preserve everything else.
        merged = dict(current_value)
        if "description" in merged:
            merged["description"] = proposed_value
        elif "text" in merged:
            merged["text"] = proposed_value
        else:
            # Unknown shape; refuse rather than corrupt the section.
            raise HTTPException(
                status_code=400,
                detail=(
                    "proposed_value is a plain string but the target section "
                    "is a structured object with no obvious prose field. "
                    "Cannot apply verbatim."
                ),
            )
        return merged
    if isinstance(current_value, list):
        raise HTTPException(
            status_code=400,
            detail=(
                "proposed_value is a plain string but the target section is "
                "an array. Use add_bullet, or have the brainstorm emit a "
                "JSON array."
            ),
        )
    # Plain string section (or current_value is None / new section).
    return proposed_value


@router.post("/chat/action_cards/{card_id}/apply")
async def apply_action_card(
    card_id: uuid.UUID, session: AsyncSession = Depends(get_session)
):
    """Apply an action card verbatim. No second LLM pass — what the user saw
    on the card is what lands in the resume."""
    card = await chat_service.get_action_card(session, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Action card not found")
    if card.status != "pending":
        raise HTTPException(
            status_code=400, detail=f"Card is already {card.status}"
        )
    if not card.section_path:
        raise HTTPException(
            status_code=400, detail="Card has no section_path; cannot apply"
        )

    # Load job + resume doc.
    job_result = await session.execute(select(Job).where(Job.id == card.job_id))
    job = job_result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    docs = await document_service.list_documents_for_job(session, card.job_id)
    resume_doc = next(
        (d for d in docs if d.doc_type == DocType.resume and d.content_json), None
    )
    if resume_doc is None:
        raise HTTPException(
            status_code=400,
            detail="No resume with JSON content found for this job.",
        )

    profile_result = await session.execute(select(UserProfile).limit(1))
    profile = profile_result.scalar_one_or_none()
    if profile is None:
        raise HTTPException(status_code=500, detail="User profile not synced")

    # Pull the current value at the section path. None is fine — the section
    # may be a brand-new key (e.g. a previously-empty field).
    try:
        current_value = document_service._get_nested(  # noqa: SLF001
            resume_doc.content_json, card.section_path
        )
    except (KeyError, IndexError, TypeError):
        current_value = None

    new_value = _resolve_card_value(card.kind, card.proposed_value, current_value)

    # Write the new value into the resume JSON, regen markdown + docx, and
    # mark the card applied. No LLM, no normalization beyond preserving the
    # resume_json structure.
    async with async_session() as bg_session:
        await document_service.update_resume_section(
            bg_session, resume_doc.id, card.section_path, new_value
        )
        doc = await document_service.get_document(bg_session, resume_doc.id)
        if doc and doc.content_json:
            doc.content_markdown = _build_markdown(doc.content_json, profile_data=profile.data)
            await bg_session.commit()
        # Regenerate docx in the background (fire-and-forget).
        try:
            from app.ai.docx_builder import build_docx

            if doc and doc.content_json and doc.job_id:
                docx_path = build_docx(
                    doc.content_json,
                    profile.data,
                    str(doc.job_id),
                    company=job.company,
                    title=job.title,
                )
                doc.content_docx_path = docx_path
                await bg_session.commit()
        except Exception:
            logger.exception("apply_action_card: docx regen failed")

    updated_card = await chat_service.mark_action_card(session, card_id, "applied")

    # Record the exemplar: original_llm_value = whatever was in the section
    # before this card was applied (resume_builder output or a prior edit),
    # final_user_value = the card's proposed_value. The instruction is the
    # most recent user message in the chat thread (which prompted the
    # brainstorm to produce this card), with the card rationale as a fallback.
    try:
        msgs = await chat_service.list_messages(session, card.job_id)
        last_user = next(
            (m.content for m in reversed(msgs) if m.role == "user"), None
        )
        instruction = last_user or (card.rationale or "")
        async with async_session() as exemplar_session:
            await edit_exemplars_service.upsert_from_edit(
                exemplar_session,
                job_id=card.job_id,
                doc_id=resume_doc.id,
                section_path=card.section_path,
                before_value=current_value,
                after_value=new_value,
                instruction=instruction,
                source="action_card",
            )
    except Exception:
        logger.exception("apply_action_card: exemplar upsert failed (non-fatal)")

    return {
        "id": str(updated_card.id),
        "status": updated_card.status,
        "section_path": card.section_path,
        "new_value": new_value,
    }
