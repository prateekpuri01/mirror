"""In-memory scoring runner for external evaluation.

Reuses the LLM-call pattern from tests/eval/conftest.py::_score_one_case so that
arbitrary (job_dict, profile_dict) pairs can be scored without touching the DB.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ScoredPair:
    """Result of scoring one (job, profile) pair."""

    role_fit_score: int
    interest_fit_score: int
    composite: float  # 0.6 * role + 0.4 * interest, on 0-100 scale
    role_fit_detail: dict
    interest_fit_detail: dict
    error: str | None = None


def _make_fake_job(job_dict: dict) -> SimpleNamespace:
    """Build a SimpleNamespace mirroring the Job ORM interface used by format_job_for_scoring."""
    return SimpleNamespace(
        title=job_dict.get("title", ""),
        company=job_dict.get("company", ""),
        description=job_dict.get("description", ""),
        salary_min=job_dict.get("salary_min"),
        salary_max=job_dict.get("salary_max"),
        display_title=job_dict.get("title", ""),
        display_company=job_dict.get("company", ""),
        extra_metadata=job_dict.get("extra_metadata"),
        user_notes=job_dict.get("user_notes"),
    )


async def score_pair(
    job_dict: dict,
    profile_data: dict,
    positive_examples_text: str = "",
    negative_examples_text: str | None = None,
) -> ScoredPair:
    """Score one (job, profile) pair via the live LLM scoring pipeline.

    No DB access, no persistence. Returns a ScoredPair with sub-score details.
    """
    from app.ai.client import get_openai_client, RESUME_MODEL
    from app.ai.prompts import (
        build_role_fit_system,
        build_interest_fit_system,
        build_role_fit_prompt,
        build_interest_fit_prompt,
        format_job_for_scoring,
    )
    from app.ai.scoring import _build_compact_profile, _parse_json_response

    profile_text = _build_compact_profile(profile_data)
    fake_job = _make_fake_job(job_dict)
    job_text = format_job_for_scoring(fake_job)

    role_system = build_role_fit_system(profile_data)
    interest_system = build_interest_fit_system(profile_data)
    role_msgs = build_role_fit_prompt(profile_text, job_text)
    interest_msgs = build_interest_fit_prompt(
        profile_text, job_text, positive_examples_text, negative_examples_text
    )

    client = get_openai_client()

    async def _call_llm(system: str, messages: list[dict]) -> dict:
        oai_messages = [{"role": "system", "content": system}]
        for msg in messages:
            oai_messages.append({"role": msg["role"], "content": msg["content"]})
        response = await client.chat.completions.create(
            model=RESUME_MODEL,
            messages=oai_messages,
            max_completion_tokens=2000,
        )
        return _parse_json_response(response.choices[0].message.content)

    try:
        role_result, interest_result = await asyncio.gather(
            _call_llm(role_system, role_msgs),
            _call_llm(interest_system, interest_msgs),
        )
    except Exception as e:
        logger.warning("score_pair failed for %s: %s", job_dict.get("title", "?"), e)
        return ScoredPair(
            role_fit_score=0,
            interest_fit_score=0,
            composite=0.0,
            role_fit_detail={},
            interest_fit_detail={},
            error=str(e),
        )

    # Arithmetic correction (mirrors the post-hoc fix in scoring.py)
    rf = role_result.get("role_fit", {})
    rf_computed = sum(
        rf.get(k, {}).get("score", 0)
        for k in ("hard_skills", "experience_level", "domain_relevance", "education_fit")
    )
    role_score = rf_computed if rf_computed > 0 else rf.get("score", 0)

    intf = interest_result.get("interest_fit", {})
    if_computed = sum(
        intf.get(k, {}).get("score", 0)
        for k in ("role_alignment", "domain_excitement", "organization_fit", "practical_factors")
    )
    interest_score = if_computed if if_computed > 0 else intf.get("score", 0)

    composite = 0.6 * role_score + 0.4 * interest_score

    return ScoredPair(
        role_fit_score=role_score,
        interest_fit_score=interest_score,
        composite=composite,
        role_fit_detail=role_result,
        interest_fit_detail=interest_result,
    )


async def score_pairs_concurrent(
    pairs: list[tuple[Any, dict, dict]],
    concurrency: int = 5,
    progress_callback=None,
) -> list[tuple[Any, ScoredPair]]:
    """Score many (key, job_dict, profile_dict) tuples in parallel.

    Returns list of (key, ScoredPair) preserving input order.
    `key` is whatever identifier the caller wants to track each pair by.
    """
    sem = asyncio.Semaphore(concurrency)
    completed = [0]
    total = len(pairs)

    async def _bounded(key, job, profile):
        async with sem:
            result = await score_pair(job, profile)
            completed[0] += 1
            if progress_callback:
                progress_callback(completed[0], total, key)
            return key, result

    tasks = [_bounded(k, j, p) for (k, j, p) in pairs]
    return await asyncio.gather(*tasks)
