"""AI-powered tailored resume generation service."""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import get_openai_client, RESUME_MODEL
from app.ai.prompts import format_job_for_scoring
from app.ai.resume_prompts import (
    RESUME_PROMPT_VERSION,
    RESUME_SYSTEM,
    RESUME_REVISION_SYSTEM,
    build_full_profile_for_resume,
    build_resume_prompt,
    build_revision_prompt,
)
from app.ai.docx_builder import build_docx
from app.models import Company, Document, DocType, Job, UserProfile
from app.services.document_service import create_document

logger = logging.getLogger(__name__)

# Concurrency limiter — one resume at a time
_semaphore = asyncio.Semaphore(1)

# In-memory status tracking
_resume_status: dict = {
    "running": False,
    "job_id": None,
    "started_at": None,
    "error": None,
}


def get_resume_status() -> dict:
    """Return current resume generation status."""
    return dict(_resume_status)


async def _call_llm(system: str, messages: list[dict]) -> dict:
    """Call OpenAI for resume generation and parse JSON response."""
    client = get_openai_client()
    # Convert from Anthropic message format to OpenAI format
    openai_messages = [{"role": "system", "content": system}]
    for msg in messages:
        openai_messages.append({"role": msg["role"], "content": msg["content"]})

    async with _semaphore:
        response = await client.chat.completions.create(
            model=RESUME_MODEL,
            max_completion_tokens=4000,
            temperature=0.3,
            messages=openai_messages,
        )

    text = response.choices[0].message.content.strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    return json.loads(text)


def _build_markdown(resume_data: dict) -> str:
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

    # Experience
    experience = resume_data.get("experience", {})
    if experience:
        lines.append("## Professional Experience\n")
        for employer_key, employer_data in experience.items():
            label = {"rand": "RAND Corporation", "finra": "FINRA", "ucla": "UCLA Physics"}.get(
                employer_key, employer_key
            )
            lines.append(f"### {label}\n")
            for bullet in employer_data.get("bullets", []):
                lines.append(f"- {bullet}")
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
            "communication": "Communication",
        }
        for key, label in labels.items():
            value = skills.get(key, "")
            if value:
                lines.append(f"**{label}**: {value}")
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


async def generate_resume(session: AsyncSession, job_id) -> Document:
    """Generate a tailored resume for a specific job posting.

    Orchestrates: load context -> LLM call -> markdown + docx generation -> DB storage.
    """
    global _resume_status
    _resume_status = {
        "running": True,
        "job_id": str(job_id),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "error": None,
    }

    try:
        # 1. Load job
        result = await session.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one_or_none()
        if job is None:
            raise ValueError(f"Job {job_id} not found")

        # 2. Load company notes
        company_notes = None
        if job.company_id:
            company_result = await session.execute(
                select(Company).where(Company.id == job.company_id)
            )
            company = company_result.scalar_one_or_none()
            if company and company.notes:
                company_notes = company.notes

        # 3. Load user profile
        result = await session.execute(select(UserProfile).limit(1))
        profile = result.scalar_one_or_none()
        if profile is None:
            raise RuntimeError("User profile not synced — run profile sync first")

        # 4. Build full profile text (ALL accomplishments)
        profile_text = build_full_profile_for_resume(profile.data)

        # 5. Format job text (reuse from scoring)
        job_text = format_job_for_scoring(job, company_notes=company_notes)

        # 6. Get score rationale if available
        score_rationale = job.score_rationale

        # 7. Call LLM
        logger.info("Generating tailored resume for job %s (%s at %s)", job.id, job.title, job.company)
        messages = build_resume_prompt(profile_text, job_text, score_rationale, company_notes)
        resume_data = await _call_llm(RESUME_SYSTEM, messages)

        # 8. Generate markdown
        content_markdown = _build_markdown(resume_data)

        # 9. Generate .docx and save JSON for revisions
        docx_path = build_docx(resume_data, profile.data, str(job.id))
        json_path = docx_path.replace(".docx", ".json")
        with open(json_path, "w") as f:
            json.dump(resume_data, f, indent=2)

        # 10. Store in DB (including content_json for section-level editing)
        doc_name = f"Resume - {job.company} - {job.title}"
        doc = await create_document(
            session,
            job_id=job.id,
            doc_type=DocType.resume,
            name=doc_name,
            content_markdown=content_markdown,
            content_docx_path=docx_path,
            content_json=resume_data,
        )

        logger.info(
            "Resume generated for job %s: doc_id=%s, docx=%s",
            job.id, doc.id, docx_path,
        )

        _resume_status["running"] = False
        return doc

    except Exception as e:
        _resume_status["running"] = False
        _resume_status["error"] = str(e)
        logger.exception("Failed to generate resume for job %s", job_id)
        raise


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
        "started_at": datetime.now(timezone.utc).isoformat(),
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

        # Call LLM with revision prompt
        logger.info("Revising resume for doc %s: %s", doc_id, instruction[:100])
        messages = build_revision_prompt(
            current_resume_json=json.dumps(current_resume, indent=2),
            instruction=instruction,
            profile_text=profile_text,
            job_text=job_text,
        )
        resume_data = await _call_llm(RESUME_REVISION_SYSTEM, messages)

        # Regenerate markdown
        content_markdown = _build_markdown(resume_data)

        # Regenerate docx
        docx_path = build_docx(resume_data, profile.data, str(job.id))
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
