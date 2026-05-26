"""AI-powered tailored resume generation service."""

import asyncio
import json
import logging
import os
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import RESUME_MODEL, get_openai_client
from app.ai.docx_builder import build_docx
from app.ai.prompts import format_job_for_scoring
from app.ai.resume_prompts import (
    RESEARCH_ENTRY_SYSTEM,
    RESUME_REVISION_SYSTEM,
    # V3 single-shot pipeline prompts
    build_full_profile_for_resume,
    build_research_entry_prompt,
    build_revision_prompt,
)
from app.ai.utils import employer_key
from app.models import Company, DocType, Document, Job, UserProfile
from app.services.document_service import create_document

logger = logging.getLogger(__name__)


def _build_employer_label_map(profile_data: dict) -> dict[str, str]:
    """Build a mapping from employer_key → display name from profile work_history."""
    label_map = {}
    for wh in profile_data.get("work_history", []):
        key = employer_key(wh["employer"])
        label_map[key] = wh["employer"]
    return label_map


def normalize_experience_order(resume_data: dict, profile_data: dict) -> dict:
    """Reorder the experience dict to match work_history order from profile.

    Python dicts are insertion-ordered (3.7+), so rebuilding with the right
    key order ensures JSON serialization, markdown, and UI all stay consistent.
    """
    experience = resume_data.get("experience")
    if not experience or not profile_data:
        return resume_data

    work_history = profile_data.get("work_history", [])
    if not work_history:
        return resume_data

    ordered = {}
    for wh in work_history:
        key = employer_key(wh["employer"])
        if key in experience:
            ordered[key] = experience[key]
    # Append any keys not in work_history (shouldn't happen, but safe)
    for key, val in experience.items():
        if key not in ordered:
            ordered[key] = val

    resume_data["experience"] = ordered
    return resume_data


# Concurrency limiter — one resume at a time
_semaphore = asyncio.Semaphore(1)

# In-memory status tracking
_resume_status: dict = {
    "running": False,
    "job_id": None,
    "started_at": None,
    "error": None,
    "phase": None,
    "step": None,
    "step_detail": None,
    "step_number": 0,
    "total_steps": 14,
}


def get_resume_status() -> dict:
    """Return current resume generation status."""
    return dict(_resume_status)


async def _call_llm(
    system: str,
    messages: list[dict],
    temperature: float = 0.3,
    max_tokens: int = 4000,
) -> dict:
    """Call OpenAI for resume generation and parse JSON response."""
    client = get_openai_client()
    # Convert from Anthropic message format to OpenAI format
    openai_messages = [{"role": "system", "content": system}]
    for msg in messages:
        openai_messages.append({"role": msg["role"], "content": msg["content"]})

    response = await client.chat.completions.create(
        model=RESUME_MODEL,
        max_completion_tokens=max_tokens,
        temperature=temperature,
        messages=openai_messages,
    )

    raw_content = response.choices[0].message.content
    finish_reason = response.choices[0].finish_reason
    logger.info(
        "LLM response: finish_reason=%s, content_len=%d, first_100=%r",
        finish_reason,
        len(raw_content or ""),
        (raw_content or "")[:100],
    )
    if not raw_content:
        logger.error(
            "LLM returned None/empty content. finish_reason=%s, full response: %s",
            finish_reason,
            response,
        )
        raise ValueError(f"LLM returned empty content (finish_reason={finish_reason})")
    text = raw_content.strip()
    # Strip markdown fences if present (handles ```json, ```, etc.)
    if text.startswith("```"):
        # Remove opening fence line
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        # Remove closing fence
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
        text = text.strip()

    # If still not valid JSON, try to extract JSON from the response
    if text and not text.startswith(("{", "[")):
        # Look for first { or [ in the response
        for i, ch in enumerate(text):
            if ch in ("{", "["):
                text = text[i:]
                break

    if not text:
        logger.error("LLM returned empty response after stripping fences")
        raise ValueError("LLM returned empty response")

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.error("Failed to parse LLM JSON response (first 500 chars): %s", text[:500])
        raise


async def _call_llm_text(
    system: str,
    user_content: str,
    temperature: float = 0.3,
    max_tokens: int = 500,
) -> str:
    """Call OpenAI and return raw text (for strategy planning, not JSON)."""
    client = get_openai_client()
    response = await client.chat.completions.create(
        model=RESUME_MODEL,
        max_completion_tokens=max_tokens,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
    )
    return response.choices[0].message.content.strip()


def _build_markdown(resume_data: dict, profile_data: dict | None = None) -> str:
    """Build a markdown representation of the resume for DB storage / UI viewing."""
    lines = []

    lines.append(f"# {resume_data.get('tagline', '')}")
    lines.append("")

    summary = resume_data.get("summary", "")
    if summary:
        lines.append(f"## Summary\n\n{summary}")
        lines.append("")

    # Selected Research
    research = resume_data.get("selected_research", [])
    if research:
        lines.append("## Selected Research\n")
        for entry in research:
            label = entry.get("category_label", "")
            title = entry.get("title", "")
            desc = entry.get("description", "")
            lines.append(f"**{label}** — {title}")
            if desc:
                lines.append(f"\n{desc}")
            lines.append("")

    # Experience — use work_history order from profile (like docx_builder does)
    experience = resume_data.get("experience", {})
    if experience:
        lines.append("## Professional Experience\n")
        label_map = _build_employer_label_map(profile_data) if profile_data else {}
        work_history = (profile_data or {}).get("work_history", [])

        if work_history:
            # Iterate in work_history order (most recent first)
            rendered_keys = set()
            for wh in work_history:
                key = employer_key(wh["employer"])
                employer_data = experience.get(key, {})
                bullets = employer_data.get("bullets", [])
                if not bullets:
                    continue
                rendered_keys.add(key)
                label = label_map.get(key, key)
                lines.append(f"### {label}\n")
                for bullet in bullets:
                    text = bullet["text"] if isinstance(bullet, dict) else bullet
                    lines.append(f"- {text}")
                lines.append("")
            # Render any experience keys not in work_history (fallback)
            for emp_key, employer_data in experience.items():
                if emp_key in rendered_keys:
                    continue
                label = label_map.get(emp_key, emp_key)
                lines.append(f"### {label}\n")
                for bullet in employer_data.get("bullets", []):
                    text = bullet["text"] if isinstance(bullet, dict) else bullet
                    lines.append(f"- {text}")
                lines.append("")
        else:
            # No profile data — fall back to dict order
            for emp_key, employer_data in experience.items():
                label = label_map.get(emp_key, emp_key)
                lines.append(f"### {label}\n")
                for bullet in employer_data.get("bullets", []):
                    text = bullet["text"] if isinstance(bullet, dict) else bullet
                    lines.append(f"- {text}")
                lines.append("")

    # Publications
    publications = resume_data.get("publications", [])
    if publications:
        lines.append("## Selected Publications\n")
        for pub in publications:
            lines.append(f"- {pub.get('citation', '')}")
        lines.append("")

    # Skills
    skills = resume_data.get("technical_skills", {})
    if skills:
        lines.append("## Technical Skills\n")
        labels = {
            "ai_systems": "AI Systems",
            "data_science": "Data Science",
            "engineering": "Engineering",
        }
        for key, label in labels.items():
            value = skills.get(key, "")
            if value:
                lines.append(f"**{label}**: {value}")
        lines.append("")

    # Education (from profile data, not LLM-generated)
    if profile_data:
        education = profile_data.get("education", [])
        if education:
            lines.append("## Education\n")
            for edu in education:
                honors = f" — {edu['honors']}" if edu.get("honors") else ""
                lines.append(
                    f"**{edu.get('degree', '')} {edu.get('field', '')}**, "
                    f"{edu.get('institution', '')} ({edu.get('year', '')}){honors}"
                )
            lines.append("")

    # Awards
    awards = resume_data.get("awards", "")
    if awards:
        lines.append(f"## Awards & Honors\n\n{awards}")
        lines.append("")

    # Tailoring rationale (useful for review)
    rationale = resume_data.get("tailoring_rationale", "")
    if rationale:
        lines.append(f"---\n\n*Tailoring rationale: {rationale}*")

    return "\n".join(lines)


def _update_status(phase: str, step: str = "", detail: str = "", step_num: int = 0):
    """Update the global resume generation status."""
    _resume_status["phase"] = phase
    _resume_status["step"] = step
    _resume_status["step_detail"] = detail
    _resume_status["step_number"] = step_num


# ===========================================================================
# Main orchestrator (v3 single-shot pipeline)
# ===========================================================================


async def generate_resume(session: AsyncSession, job_id) -> Document:
    """Generate a tailored resume for a specific job posting.

    V3 pipeline: company research → strategic plan → single-shot generation.
    """
    global _resume_status
    _resume_status = {
        "running": True,
        "job_id": str(job_id),
        "started_at": datetime.now(UTC).isoformat(),
        "error": None,
        "phase": "generating",
        "step": None,
        "step_detail": None,
        "step_number": 0,
        "total_steps": 7,
    }

    try:
        async with _semaphore:
            return await _generate_resume_v3(session, job_id)
    except Exception as e:
        _resume_status["running"] = False
        _resume_status["error"] = str(e)
        _resume_status["phase"] = None
        logger.exception("Failed to generate resume for job %s", job_id)
        raise


async def _generate_resume_v3(session: AsyncSession, job_id) -> Document:
    """V4 staged pipeline: plan → selection → parallel(research+skills) →
    parallel(bullets) → summary+tagline. Wrapped here for company research,
    profile loading, and persistence.
    """

    # --- Load context -----------------------------------------------------------
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise ValueError(f"Job {job_id} not found")

    company = None
    if job.company_id:
        company_result = await session.execute(select(Company).where(Company.id == job.company_id))
        company = company_result.scalar_one_or_none()

    # Phase 0: Company research (Perplexity)
    company_research = None
    try:
        from app.ai.company_research import research_company_for_job
        from app.database import async_session as session_factory

        _update_status("researching", "company_research", "Researching company & team...", 0)
        logger.info("Phase 0: Researching company/team for job %s", job.id)
        company_research = await research_company_for_job(session, job, company)
        if company_research:
            async with session_factory() as persist_session:
                from sqlalchemy import update

                meta = dict(job.extra_metadata or {})
                meta["company_research"] = company_research
                await persist_session.execute(
                    update(Job).where(Job.id == job.id).values(extra_metadata=meta)
                )
                await persist_session.commit()
            job.extra_metadata = meta
    except Exception:
        logger.warning(
            "Company research failed for job %s, continuing without", job.id, exc_info=True
        )

    # Load profile
    from app.database import async_session as session_factory2

    async with session_factory2() as fresh_session:
        result = await fresh_session.execute(select(UserProfile).limit(1))
        profile = result.scalar_one_or_none()
    if profile is None:
        raise RuntimeError("User profile not synced — run profile sync first")

    # --- Staged pipeline (plan → selection → parallel sections → summary) ----
    from app.ai.resume_pipeline import run_pipeline

    resume_data = await run_pipeline(
        session=session,
        job=job,
        company=company,
        company_research=company_research,
        profile_data=profile.data,
        call_llm=_call_llm,
        update_status=_update_status,
    )
    resume_data = normalize_experience_order(resume_data, profile.data)

    # --- Save ------------------------------------------------------------------
    _update_status("saving", "save", "Saving resume...", 7)
    content_markdown = _build_markdown(resume_data, profile_data=profile.data)
    docx_path = build_docx(
        resume_data, profile.data, str(job.id), company=job.company, title=job.title
    )
    json_path = docx_path.replace(".docx", ".json")
    with open(json_path, "w") as f:
        json.dump(resume_data, f, indent=2)

    from app.database import async_session as _session_factory

    doc_name = f"Resume - {job.company} - {job.title}"
    async with _session_factory() as save_session:
        doc = await create_document(
            save_session,
            job_id=job.id,
            doc_type=DocType.resume,
            name=doc_name,
            content_markdown=content_markdown,
            content_docx_path=docx_path,
            content_json=resume_data,
        )

    logger.info(
        "Resume generated for job %s: doc_id=%s, docx=%s",
        job.id,
        doc.id,
        docx_path,
    )

    _resume_status["running"] = False
    _resume_status["phase"] = None
    return doc


async def revise_resume(session: AsyncSession, doc_id, instruction: str) -> Document:
    """Revise an existing resume based on user instructions.

    Loads the current resume JSON + job context, sends revision instruction
    to the LLM, regenerates markdown + docx, and updates the document.
    """
    global _resume_status

    # Load existing document
    result = await session.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    if doc is None:
        raise ValueError(f"Document {doc_id} not found")
    if doc.job_id is None:
        raise ValueError("Document is not linked to a job")

    _resume_status = {
        "running": True,
        "job_id": str(doc.job_id),
        "started_at": datetime.now(UTC).isoformat(),
        "error": None,
    }

    try:
        # Load the current resume JSON from file
        current_resume = None
        if doc.content_docx_path:
            json_path = doc.content_docx_path.replace(".docx", ".json")
            if os.path.exists(json_path):
                with open(json_path) as f:
                    current_resume = json.load(f)

        if current_resume is None:
            raise ValueError("No resume JSON found — regenerate the resume first")

        # Load job
        result = await session.execute(select(Job).where(Job.id == doc.job_id))
        job = result.scalar_one_or_none()
        if job is None:
            raise ValueError(f"Job {doc.job_id} not found")

        # Load company notes
        company_notes = None
        if job.company_id:
            company_result = await session.execute(
                select(Company).where(Company.id == job.company_id)
            )
            company = company_result.scalar_one_or_none()
            if company and company.notes:
                company_notes = company.notes

        # Load profile
        result = await session.execute(select(UserProfile).limit(1))
        profile = result.scalar_one_or_none()
        if profile is None:
            raise RuntimeError("User profile not synced")

        # Build context
        profile_text = build_full_profile_for_resume(profile.data)
        job_text = format_job_for_scoring(job, company_notes=company_notes)

        # Load company research from job metadata (persisted during generation)
        company_research = None
        meta = job.extra_metadata or {}
        if meta.get("company_research"):
            company_research = meta["company_research"]

        # Load writing memory preferences
        from app.ai.writing_memory import format_writing_memory

        memory_text = await format_writing_memory(session, "resume")

        # Call LLM with revision prompt
        logger.info("Revising resume for doc %s: %s", doc_id, instruction[:100])
        messages = build_revision_prompt(
            current_resume_json=json.dumps(current_resume, indent=2),
            instruction=instruction,
            profile_text=profile_text,
            job_text=job_text,
            company_notes=company_notes,
            company_research=company_research,
            memory_text=memory_text,
        )
        resume_data = await _call_llm(RESUME_REVISION_SYSTEM, messages)
        resume_data = normalize_experience_order(resume_data, profile.data)

        # Regenerate markdown
        content_markdown = _build_markdown(resume_data, profile_data=profile.data)

        # Regenerate docx
        docx_path = build_docx(
            resume_data, profile.data, str(job.id), company=job.company, title=job.title
        )
        json_path = docx_path.replace(".docx", ".json")
        with open(json_path, "w") as f:
            json.dump(resume_data, f, indent=2)

        # Update document in DB (including content_json)
        doc.content_markdown = content_markdown
        doc.content_json = resume_data
        doc.content_docx_path = docx_path
        doc.version += 1
        await session.commit()
        await session.refresh(doc)

        logger.info("Resume revised: doc_id=%s, version=%d", doc.id, doc.version)
        _resume_status["running"] = False
        return doc

    except Exception as e:
        _resume_status["running"] = False
        _resume_status["error"] = str(e)
        logger.exception("Failed to revise resume %s", doc_id)
        raise


async def generate_research_entry(session: AsyncSession, accomplishment_id: str, job_id) -> dict:
    """Generate a single selected_research entry for a given accomplishment.

    Looks up the accomplishment from the profile, calls LLM to generate a
    job-tailored description, and returns a ResearchEntry dict.
    """
    # Load job
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise ValueError(f"Job {job_id} not found")

    # Load profile
    result = await session.execute(select(UserProfile).limit(1))
    profile = result.scalar_one_or_none()
    if profile is None:
        raise RuntimeError("User profile not synced")

    # Find the accomplishment in the profile
    complete = profile.data.get("complete_profile", {})
    accomplishments = complete.get("accomplishments", [])
    accomplishment = None
    for a in accomplishments:
        if a.get("id") == accomplishment_id:
            accomplishment = a
            break

    if accomplishment is None:
        raise ValueError(f"Accomplishment '{accomplishment_id}' not found in profile")

    # Build job text
    company_notes = None
    if job.company_id:
        company_result = await session.execute(select(Company).where(Company.id == job.company_id))
        company = company_result.scalar_one_or_none()
        if company and company.notes:
            company_notes = company.notes
    job_text = format_job_for_scoring(job, company_notes=company_notes)

    # Call LLM
    logger.info(
        "Generating research entry for accomplishment '%s' targeting job %s",
        accomplishment_id,
        job.id,
    )
    messages = build_research_entry_prompt(accomplishment, job_text)
    entry = await _call_llm(RESEARCH_ENTRY_SYSTEM, messages)

    return entry


async def generate_single_bullet(
    session: AsyncSession,
    *,
    doc_id,
    employer_key: str,
    accomplishment_id: str,
) -> dict:
    """Generate ONE experience bullet for a specific accomplishment.

    Used by the resume editor's "Add bullet from accomplishment" dropdown.
    Reuses the staged-pipeline bullet prompt (with ``bullet_count=1``) so
    the new bullet inherits:

      - **Voice** from ``content_memory`` past versions of this employer's
        bullet sets (same grounding the pipeline uses)
      - **Cross-section dedup** from the finalized ``selected_research``
        in the current resume (the prompt sees research and is told not
        to restate it)
      - **Strategic plan tone** from the resume's ``_strategic_plan``
        attached at generation time

    Returns ``{"text": str, "accomplishment_ids": [accomplishment_id]}``.
    """
    from app.ai.content_memory_grounding import format_grounding_block
    from app.ai.content_memory_paths import EXPERIENCE_BULLETS_SET
    from app.ai.resume_prompts import build_bullet_set_prompt, build_bullet_set_system
    from app.ai.utils import employer_key as employer_key_fn
    from app.ai.writing_memory import format_writing_memory
    from app.services import content_memory_service, document_service

    doc = await document_service.get_document(session, doc_id)
    if doc is None or doc.content_json is None:
        raise ValueError(f"Document {doc_id} has no JSON content")
    if doc.job_id is None:
        raise ValueError(f"Document {doc_id} has no associated job")

    result = await session.execute(select(Job).where(Job.id == doc.job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise ValueError(f"Job {doc.job_id} not found")

    result = await session.execute(select(UserProfile).limit(1))
    profile = result.scalar_one_or_none()
    if profile is None:
        raise RuntimeError("User profile not synced")

    # Find the accomplishment + verify it's a real entity in the profile.
    accomplishments = (profile.data.get("complete_profile") or {}).get("accomplishments") or []
    accomplishment = next(
        (a for a in accomplishments if a.get("id") == accomplishment_id),
        None,
    )
    if accomplishment is None:
        raise ValueError(f"Accomplishment {accomplishment_id!r} not found in profile")

    # Map employer_key (the resume JSON's key) back to the display name so
    # the prompt reads naturally. Profile work_history is the source of
    # canonical names.
    employer_name = employer_key
    for wh in profile.data.get("work_history") or []:
        if employer_key_fn(wh.get("employer", "")) == employer_key:
            employer_name = wh.get("employer", employer_key)
            break

    # Job context for relevance (same builder the pipeline uses)
    company_notes = None
    if job.company_id:
        company_result = await session.execute(
            select(Company).where(Company.id == job.company_id),
        )
        company = company_result.scalar_one_or_none()
        if company and company.notes:
            company_notes = company.notes
    job_text = format_job_for_scoring(job, company_notes=company_notes)

    # Grounding: past hand-tuned versions of this employer's bullet sets
    grouped = await content_memory_service.fetch_grounding(
        session,
        entity_type=EXPERIENCE_BULLETS_SET,
        entity_keys=[employer_key],
    )
    grounding_text = format_grounding_block(
        grouped.get(employer_key, []),
        profile_data=profile.data,
    )
    # Plus the abstract writing-memory style rules
    writing_memory_text = await format_writing_memory(session, "resume")
    if writing_memory_text:
        grounding_text = (grounding_text + "\n\n" + writing_memory_text).strip()

    # Strategic plan context: reuse what's attached to the resume at gen time
    plan = doc.content_json.get("_strategic_plan") or {}
    plan_text = json.dumps(plan, indent=2) if plan else "(no strategic plan attached)"

    # Finalized research as cross-section anti-redundancy input
    finalized_research = doc.content_json.get("selected_research") or []

    # Single-bullet ask via the existing bullet-set prompt with bullet_count=1
    system = build_bullet_set_system(profile.data)
    messages = build_bullet_set_prompt(
        employer_key_str=employer_key,
        employer_name=employer_name,
        bullet_count=1,
        accomplishments=[accomplishment],
        plan_mapping=None,
        plan_text=plan_text,
        finalized_research=finalized_research,
        job_text=job_text,
        grounding_text=grounding_text,
        critic_notes="",
    )
    logger.info(
        "Generating bullet for accomplishment %r under employer %r (doc %s)",
        accomplishment_id,
        employer_key,
        doc_id,
    )
    result = await _call_llm(system, messages, temperature=0.45, max_tokens=800)

    bullets = result.get("bullets") or []
    if not bullets:
        raise ValueError("LLM returned no bullets")
    first = bullets[0]
    text = (first.get("text") or "").strip() if isinstance(first, dict) else str(first).strip()
    if not text:
        raise ValueError("LLM returned an empty bullet")

    # Anchor the bullet to the chosen accomplishment_id no matter what the
    # LLM said — the user asked for THIS accomplishment and the dropdown
    # binding is the contract.
    return {"text": text, "accomplishment_ids": [accomplishment_id]}
