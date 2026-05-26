"""Layer 3: LLM-based cleaning for jobs that couldn't be auto-cleaned.

Only runs on jobs flagged by Layer 2 (~20-50 jobs per dataset).
Sends minimal context (first 300 chars of description + raw header)
and top fuzzy company candidates to minimize cost.
"""

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import EXTRACTION_MODEL, SCORING_MODEL, get_openai_client
from app.ai.enrichment import _levenshtein, _normalize_company_name
from app.models import Company, Job

logger = logging.getLogger(__name__)

_LLM_CLEANING_SYSTEM = """\
You are a data cleaning assistant. Given a job posting header and description snippet, \
extract the correct job title and company name.

Rules:
- The job title should be a standard professional title (e.g. "Senior Software Engineer", \
"Machine Learning Researcher", "Product Manager")
- The company name should be the actual legal/trade name, without URLs, funding info, \
or batch tags
- If you're given candidate company names from the database, prefer matching one of those \
if it's clearly the same company
- If you cannot confidently determine a field, set it to null

Respond with ONLY valid JSON (no markdown fences):
{"clean_title": "...", "clean_company": "...", "confidence": 0.0}

confidence should be 0.0-1.0 reflecting how certain you are about the extraction.
"""


async def llm_clean_job(
    session: AsyncSession,
    job: Job,
    fuzzy_candidates: list[Company],
) -> dict | None:
    """LLM-clean a single flagged job. Returns cleaned data or None."""
    # Build minimal context
    header = job.title
    desc_snippet = (job.description or "")[:300]
    raw_company = job.company

    candidates_text = ""
    if fuzzy_candidates:
        candidates_text = "\n\nKnown companies that might match:\n" + "\n".join(
            f"- {c.name}" for c in fuzzy_candidates[:10]
        )

    prompt = (
        f"Raw header/title: {header}\n"
        f"Raw company: {raw_company}\n"
        f"Description snippet: {desc_snippet}"
        f"{candidates_text}"
    )

    client = get_openai_client()
    try:
        response = await client.chat.completions.create(
            model=EXTRACTION_MODEL,
            max_completion_tokens=200,
            temperature=0,
            messages=[
                {"role": "system", "content": _LLM_CLEANING_SYSTEM},
                {"role": "user", "content": prompt},
            ],
        )
        text = response.choices[0].message.content.strip()
        result = json.loads(text)
    except (json.JSONDecodeError, IndexError, KeyError) as exc:
        logger.warning("LLM cleaning failed for job %s: %s", job.id, exc)
        return None
    except Exception as exc:
        logger.warning("LLM cleaning API error for job %s: %s", job.id, exc)
        return None

    confidence = float(result.get("confidence", 0))
    clean_title = result.get("clean_title")
    clean_company = result.get("clean_company")

    if confidence < 0.7:
        logger.info(
            "LLM low confidence (%.2f) for job %s: title=%s, company=%s",
            confidence,
            job.id,
            clean_title,
            clean_company,
        )
        # Still record the attempt
        meta = dict(job.extra_metadata or {})
        meta["llm_cleaning"] = {
            "method": "llm",
            "model": SCORING_MODEL,
            "confidence": confidence,
            "result": result,
            "status": "low_confidence",
        }
        job.extra_metadata = meta
        return {"status": "low_confidence", "confidence": confidence}

    # Apply results
    if clean_title and clean_title != job.title:
        job.clean_title = clean_title
    if clean_company and clean_company != job.company:
        job.clean_company = clean_company

    meta = dict(job.extra_metadata or {})
    meta["llm_cleaning"] = {
        "method": "llm",
        "model": SCORING_MODEL,
        "confidence": confidence,
        "status": "applied",
    }
    # Remove the flag
    meta.pop("needs_llm_cleaning", None)
    meta.pop("llm_cleaning_reason", None)
    job.extra_metadata = meta

    return {
        "status": "applied",
        "confidence": confidence,
        "clean_title": clean_title,
        "clean_company": clean_company,
    }


async def llm_clean_batch(session: AsyncSession) -> dict:
    """Run LLM on all Layer 2-flagged jobs.

    Returns {"cleaned": N, "low_confidence": N, "failed": N}
    """
    stats = {"cleaned": 0, "low_confidence": 0, "failed": 0}

    # Find flagged jobs
    result = await session.execute(select(Job))
    all_jobs = list(result.scalars().all())

    flagged = [j for j in all_jobs if (j.extra_metadata or {}).get("needs_llm_cleaning")]

    if not flagged:
        logger.info("No jobs flagged for LLM cleaning")
        return stats

    logger.info("LLM cleaning %d flagged jobs", len(flagged))

    # Pre-fetch all companies for fuzzy candidate lookup
    companies_result = await session.execute(select(Company))
    all_companies = list(companies_result.scalars().all())

    for job in flagged:
        # Find fuzzy candidates for this job's company name
        norm = _normalize_company_name(job.company)
        candidates = []
        for company in all_companies:
            comp_norm = _normalize_company_name(company.name)
            if not comp_norm or not norm:
                continue
            dist = _levenshtein(norm, comp_norm)
            if dist <= 3:
                candidates.append((dist, company))
        candidates.sort(key=lambda x: x[0])
        fuzzy_companies = [c for _, c in candidates[:10]]

        result = await llm_clean_job(session, job, fuzzy_companies)
        if result is None:
            stats["failed"] += 1
        elif result["status"] == "low_confidence":
            stats["low_confidence"] += 1
        else:
            stats["cleaned"] += 1

    await session.commit()
    logger.info(
        "LLM cleaning complete: %d cleaned, %d low_confidence, %d failed",
        stats["cleaned"],
        stats["low_confidence"],
        stats["failed"],
    )
    return stats
