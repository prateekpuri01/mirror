"""AI-powered job scoring service."""

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import RESUME_MODEL, get_openai_client
from app.ai.prompts import (
    PROMPT_VERSION,
    build_interest_fit_prompt,
    build_interest_fit_system,
    build_role_fit_prompt,
    build_role_fit_system,
    format_example_jobs,
    format_job_for_scoring,
)
from app.models import Company, Job, UserProfile

logger = logging.getLogger(__name__)

# Model for scoring — GPT-5.4 for speed + quality
SCORING_MODEL = RESUME_MODEL  # "gpt-5.4"

# Concurrency — GPT-5.4 handles high parallelism
_CONCURRENCY = 10

# In-memory batch progress tracking
_batch_status: dict = {
    "running": False,
    "total": 0,
    "completed": 0,
    "failed": 0,
    "started_at": None,
}


def _build_compact_profile(data: dict) -> str:
    """Build a compact profile string (~2k tokens) from the full profile data.

    Sends only what the LLM needs for scoring: skills, experience summary,
    key accomplishments, education, and preferences.
    """
    lines = []

    # Base profile fields (from profile.yaml)
    personal = data.get("personal", {})
    lines.append(f"Name: {personal.get('name', 'Unknown')}")
    lines.append(f"Location: {personal.get('location', 'Unknown')}")
    lines.append(f"Remote preference: {personal.get('remote_preference', 'flexible')}")
    lines.append(f"Willing to relocate: {personal.get('willing_to_relocate', False)}")
    lines.append(f"Experience: {data.get('experience_years', 'N/A')} years post-PhD")
    lines.append("")

    # Target roles
    target_roles = data.get("target_roles", [])
    if target_roles:
        roles = [f"{r['title']} ({r.get('seniority', '')})".strip() for r in target_roles]
        lines.append(f"Target roles: {', '.join(roles)}")

    # Domains
    domains = data.get("domains", [])
    if domains:
        lines.append(f"Domains: {', '.join(domains)}")
    lines.append("")

    # Skills (dynamic categories)
    skills = data.get("skills", {})
    from app.ai.skill_utils import format_skills_for_prompt

    skills_text = format_skills_for_prompt(skills)
    if skills_text:
        lines.append(skills_text)
    lines.append("")

    # Work history
    lines.append("Work history:")
    for job in data.get("work_history", []):
        end = job.get("end") or "present"
        lines.append(f"  - {job['title']} at {job['employer']} ({job['start']} to {end})")
    lines.append("")

    # Education
    lines.append("Education:")
    for edu in data.get("education", []):
        honors = f" — {edu['honors']}" if edu.get("honors") else ""
        lines.append(
            f"  - {edu['degree']} {edu['field']}, {edu['institution']} ({edu['year']}){honors}"
        )
    lines.append("")

    # Awards (compact)
    awards = data.get("awards", [])
    if awards:
        lines.append(f"Awards: {'; '.join(awards)}")
        lines.append("")

    # Key accomplishments from complete profile (top entries by relevance)
    complete = data.get("complete_profile", {})
    accomplishments = complete.get("accomplishments", [])
    # Sort by relevance_weight descending, take top 12
    top_acc = sorted(accomplishments, key=lambda a: a.get("relevance_weight", 0), reverse=True)[:12]
    if top_acc:
        lines.append("Key accomplishments:")
        for a in top_acc:
            position = f" ({a['work_history_key']})" if a.get("work_history_key") else ""
            lines.append(f"  - [{a.get('employer', '')}] {a.get('title', '')}{position}")
            if a.get("impact_summary"):
                summary = a["impact_summary"].strip().replace("\n", " ")[:200]
                lines.append(f"    {summary}")
            quants = a.get("quantitative_specifics", [])
            if quants:
                lines.append(f"    Metrics: {'; '.join(quants[:3])}")
            skills_demo = a.get("skills_demonstrated", [])
            if skills_demo:
                lines.append(f"    Skills: {', '.join(skills_demo[:6])}")
            related_pubs = a.get("related_publication_ids", [])
            if related_pubs:
                lines.append(f"    Related pubs: {', '.join(related_pubs)}")
        lines.append("")

    # Key publications (top 6)
    publications = complete.get("publications", [])
    top_pubs = sorted(publications, key=lambda p: p.get("relevance_weight", 0), reverse=True)[:6]
    if top_pubs:
        lines.append("Key publications:")
        for p in top_pubs:
            fa = " [first author]" if p.get("first_author") else ""
            lines.append(
                f'  - "{p.get("title", "")}" ({p.get("venue", "")}, {p.get("year", "")}){fa}'
            )
        lines.append("")

    # Search preferences — prefer new free-text fields, fall back to legacy
    prefs = data.get("search_preferences", {})
    if prefs:
        if prefs.get("looking_for"):
            lines.append(f"Looking for: {prefs['looking_for']}")
            if prefs.get("positive_signals"):
                lines.append(f"Positive signals: {', '.join(prefs['positive_signals'])}")
        elif prefs.get("industries_ranked"):
            lines.append("Preferred org types (ranked):")
            for ind in prefs["industries_ranked"]:
                lines.append(f"  - {ind}")
            if prefs.get("nice_to_haves"):
                lines.append(f"Nice-to-haves: {', '.join(prefs['nice_to_haves'])}")

        if prefs.get("not_looking_for"):
            lines.append(f"Not looking for: {prefs['not_looking_for']}")
            if prefs.get("exclusions"):
                lines.append("Exclusions:")
                for ex in prefs["exclusions"]:
                    lines.append(f"  - {ex}")
        elif prefs.get("deal_breakers"):
            lines.append("Deal-breakers:")
            for db in prefs["deal_breakers"]:
                lines.append(f"  - {db}")
            if prefs.get("org_anti_patterns"):
                lines.append("Anti-patterns:")
                for ap in prefs["org_anti_patterns"]:
                    lines.append(f"  - {ap}")

    return "\n".join(lines)


async def _get_profile(session: AsyncSession) -> UserProfile:
    """Load the user profile from the DB."""
    result = await session.execute(select(UserProfile).limit(1))
    profile = result.scalar_one_or_none()
    if profile is None:
        raise RuntimeError("User profile not synced — run profile sync first")
    return profile


async def _get_profile_text(session: AsyncSession) -> str:
    """Load a compact profile string from the DB for scoring prompts."""
    profile = await _get_profile(session)
    return _build_compact_profile(profile.data)


async def _get_example_jobs(session: AsyncSession) -> tuple[list[Job], list[Job]]:
    """Fetch thumbed-up and thumbed-down jobs for few-shot examples.

    Limits: 10 positive, 5 negative (most recent first).
    Keeps prompt size bounded as users dismiss more jobs over time.
    """
    pos_result = await session.execute(
        select(Job).where(Job.thumbs == 1).order_by(Job.updated_at.desc()).limit(10)
    )
    positive = list(pos_result.scalars().unique().all())

    neg_result = await session.execute(
        select(Job).where(Job.thumbs == -1).order_by(Job.updated_at.desc()).limit(5)
    )
    negative = list(neg_result.scalars().unique().all())

    return positive, negative


def _parse_json_response(text: str) -> dict:
    """Parse JSON from LLM response, stripping markdown fences if present."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    return json.loads(text)


async def _call_llm(system: str, messages: list[dict]) -> dict:
    """Call GPT-5.4 and parse JSON response."""
    client = get_openai_client()
    # Convert from Anthropic message format to OpenAI format
    oai_messages = [{"role": "system", "content": system}]
    for msg in messages:
        oai_messages.append({"role": msg["role"], "content": msg["content"]})

    response = await client.chat.completions.create(
        model=SCORING_MODEL,
        messages=oai_messages,
        max_completion_tokens=2000,
    )

    return _parse_json_response(response.choices[0].message.content)


async def score_job(
    session: AsyncSession,
    job_id: uuid.UUID,
    *,
    profile_text: str | None = None,
    profile_data: dict | None = None,
    positive_jobs: list[Job] | None = None,
    negative_jobs: list[Job] | None = None,
) -> Job | None:
    """Score a single job for role fit and interest fit."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        return None

    # Load profile if not cached
    if profile_text is None or profile_data is None:
        profile = await _get_profile(session)
        if profile_text is None:
            profile_text = _build_compact_profile(profile.data)
        if profile_data is None:
            profile_data = profile.data

    # Load examples if not cached
    if positive_jobs is None:
        positive_jobs, negative_jobs = await _get_example_jobs(session)

    # Load company notes if the job is linked to a company
    company_notes = None
    if job.company_id:
        company_result = await session.execute(select(Company).where(Company.id == job.company_id))
        company = company_result.scalar_one_or_none()
        if company and company.notes:
            company_notes = company.notes

    job_text = format_job_for_scoring(job, company_notes=company_notes)

    # Build dynamic system prompts from profile data
    role_fit_system = build_role_fit_system(profile_data)
    interest_fit_system = build_interest_fit_system(profile_data)

    # Score role fit and interest fit in parallel
    try:
        role_task = _call_llm(
            role_fit_system,
            build_role_fit_prompt(profile_text, job_text),
        )
        interest_task = _call_llm(
            interest_fit_system,
            build_interest_fit_prompt(
                profile_text,
                job_text,
                format_example_jobs(positive_jobs),
                format_example_jobs(negative_jobs) if negative_jobs else None,
            ),
        )
        role_result, interest_result = await asyncio.gather(role_task, interest_task)
    except Exception:
        logger.exception("Failed to score job %s (%s at %s)", job.id, job.title, job.company)
        raise

    # Post-hoc arithmetic correction: recompute totals from sub-scores
    rf = role_result.get("role_fit", {})
    rf_computed = sum(
        [
            rf.get("hard_skills", {}).get("score", 0),
            rf.get("experience_level", {}).get("score", 0),
            rf.get("domain_relevance", {}).get("score", 0),
            rf.get("education_fit", {}).get("score", 0),
        ]
    )
    rf_reported = rf.get("score", 0)
    if rf_computed > 0 and rf_computed != rf_reported:
        logger.warning(
            "Role fit arithmetic mismatch for %s: reported=%d computed=%d",
            job.id,
            rf_reported,
            rf_computed,
        )
    role_score = rf_computed if rf_computed > 0 else rf_reported

    intf = interest_result.get("interest_fit", {})
    if_computed = sum(
        [
            intf.get("role_alignment", {}).get("score", 0),
            intf.get("domain_excitement", {}).get("score", 0),
            intf.get("organization_fit", {}).get("score", 0),
            intf.get("practical_factors", {}).get("score", 0),
        ]
    )
    if_reported = intf.get("score", 0)
    if if_computed > 0 and if_computed != if_reported:
        logger.warning(
            "Interest fit arithmetic mismatch for %s: reported=%d computed=%d",
            job.id,
            if_reported,
            if_computed,
        )
    interest_score = if_computed if if_computed > 0 else if_reported

    # Build rationale
    rationale = {
        **role_result,
        **interest_result,
        "scored_at": datetime.now(UTC).isoformat(),
        "model": SCORING_MODEL,
        "prompt_version": PROMPT_VERSION,
    }

    # Update job
    job.role_fit_score = role_score
    job.interest_fit_score = interest_score
    job.score_rationale = rationale
    # Composite relevance score
    job.relevance_score = round((0.6 * role_score + 0.4 * interest_score) / 100.0, 3)
    # Pipeline tracking
    job.pipeline_stage = "scored"
    job.scored_at = datetime.now(UTC)
    job.score_prompt_version = PROMPT_VERSION

    await session.commit()
    await session.refresh(job)
    logger.info(
        "Scored job %s: role=%d interest=%d relevance=%.3f",
        job.id,
        role_score,
        interest_score,
        job.relevance_score,
    )
    return job


async def _score_one(
    job_id: uuid.UUID,
    semaphore: asyncio.Semaphore,
    session: AsyncSession,
    profile_text: str,
    profile_data: dict,
    positive_jobs: list[Job],
    negative_jobs: list[Job],
) -> bool:
    """Score a single job under the semaphore. Returns True on success."""
    async with semaphore:
        try:
            await score_job(
                session,
                job_id,
                profile_text=profile_text,
                profile_data=profile_data,
                positive_jobs=positive_jobs,
                negative_jobs=negative_jobs,
            )
            return True
        except Exception:
            logger.exception("Failed to score job %s", job_id)
            return False


async def score_jobs_batch(
    session: AsyncSession,
    *,
    only_unscored: bool = True,
    only_thumbed: bool = False,
    only_enriched: bool = False,
) -> None:
    """Score multiple jobs with high concurrency. Commits per-job for resume safety."""
    global _batch_status

    if _batch_status["running"]:
        raise RuntimeError("A batch scoring job is already running")

    # Build query
    query = select(Job)
    if only_enriched:
        query = query.join(Company, Job.company_id == Company.id).where(
            Company.enriched_at.isnot(None)
        )
    if only_thumbed:
        query = query.where(Job.thumbs.isnot(None))
    elif only_unscored:
        query = query.where(Job.role_fit_score.is_(None))

    result = await session.execute(query)
    jobs = list(result.scalars().unique().all())

    if not jobs:
        logger.info("No jobs to score")
        return

    # Cache profile and examples
    profile = await _get_profile(session)
    profile_text = _build_compact_profile(profile.data)
    profile_data = profile.data
    positive_jobs, negative_jobs = await _get_example_jobs(session)

    _batch_status = {
        "running": True,
        "total": len(jobs),
        "completed": 0,
        "failed": 0,
        "started_at": datetime.now(UTC).isoformat(),
    }

    logger.info("Starting batch scoring of %d jobs (concurrency=%d)", len(jobs), _CONCURRENCY)

    semaphore = asyncio.Semaphore(_CONCURRENCY)

    # Process in batches of 20 for manageable gather groups
    batch_size = 20
    for batch_start in range(0, len(jobs), batch_size):
        batch = jobs[batch_start : batch_start + batch_size]
        tasks = [
            _score_one(
                job.id, semaphore, session, profile_text, profile_data, positive_jobs, negative_jobs
            )
            for job in batch
        ]
        results = await asyncio.gather(*tasks)

        for success in results:
            if success:
                _batch_status["completed"] += 1
            else:
                _batch_status["failed"] += 1

    _batch_status["running"] = False
    logger.info(
        "Batch scoring complete: %d/%d succeeded, %d failed",
        _batch_status["completed"],
        _batch_status["total"],
        _batch_status["failed"],
    )


def get_batch_status() -> dict:
    """Return current batch scoring status."""
    return dict(_batch_status)
