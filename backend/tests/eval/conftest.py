"""Fixtures for scoring evaluation tests.

Uses real LLM calls — not mocked. All tests require @pytest.mark.eval.
"""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace

import pytest

from tests.eval.fixtures import (
    EVAL_PROFILE_DATA,
    NEGATIVE_EXAMPLES_TEXT,
    POSITIVE_EXAMPLES_TEXT,
    TEST_CASES,
    TEST_CASES_BY_ID,
)
from tests.eval.metrics import EvalResult

logger = logging.getLogger(__name__)


def _make_fake_job(job_dict: dict) -> SimpleNamespace:
    """Create a minimal object matching the interface of format_job_for_scoring."""
    return SimpleNamespace(
        title=job_dict["title"],
        company=job_dict["company"],
        description=job_dict.get("description", ""),
        salary_min=job_dict.get("salary_min"),
        salary_max=job_dict.get("salary_max"),
        display_title=job_dict["title"],
        display_company=job_dict["company"],
        extra_metadata=None,
        user_notes=job_dict.get("user_notes"),
    )


async def _score_one_case(case_id: str, job_dict: dict) -> EvalResult:
    """Score a single test case by calling the real LLM pipeline."""
    from app.ai.client import RESUME_MODEL, get_openai_client
    from app.ai.prompts import (
        build_interest_fit_prompt,
        build_interest_fit_system,
        build_role_fit_prompt,
        build_role_fit_system,
        format_job_for_scoring,
    )
    from app.ai.scoring import _build_compact_profile, _parse_json_response

    profile_text = _build_compact_profile(EVAL_PROFILE_DATA)
    fake_job = _make_fake_job(job_dict)
    job_text = format_job_for_scoring(fake_job)

    role_fit_system = build_role_fit_system(EVAL_PROFILE_DATA)
    interest_fit_system = build_interest_fit_system(EVAL_PROFILE_DATA)

    role_msgs = build_role_fit_prompt(profile_text, job_text)
    interest_msgs = build_interest_fit_prompt(
        profile_text, job_text, POSITIVE_EXAMPLES_TEXT, NEGATIVE_EXAMPLES_TEXT
    )

    client = get_openai_client()

    async def call_llm(system: str, messages: list[dict]) -> dict:
        oai_messages = [{"role": "system", "content": system}]
        for msg in messages:
            oai_messages.append({"role": msg["role"], "content": msg["content"]})
        response = await client.chat.completions.create(
            model=RESUME_MODEL,
            messages=oai_messages,
            max_completion_tokens=2000,
        )
        return _parse_json_response(response.choices[0].message.content)

    role_result, interest_result = await asyncio.gather(
        call_llm(role_fit_system, role_msgs),
        call_llm(interest_fit_system, interest_msgs),
    )

    # Apply arithmetic correction (same as scoring.py post-hoc fix)
    rf = role_result.get("role_fit", {})
    rf_computed = sum(
        [
            rf.get("hard_skills", {}).get("score", 0),
            rf.get("experience_level", {}).get("score", 0),
            rf.get("domain_relevance", {}).get("score", 0),
            rf.get("education_fit", {}).get("score", 0),
        ]
    )
    role_score = rf_computed if rf_computed > 0 else rf.get("score", 0)

    intf = interest_result.get("interest_fit", {})
    if_computed = sum(
        [
            intf.get("role_alignment", {}).get("score", 0),
            intf.get("domain_excitement", {}).get("score", 0),
            intf.get("organization_fit", {}).get("score", 0),
            intf.get("practical_factors", {}).get("score", 0),
        ]
    )
    interest_score = if_computed if if_computed > 0 else intf.get("score", 0)

    return EvalResult(
        case_id=case_id,
        role_fit_score=role_score,
        interest_fit_score=interest_score,
        role_fit_detail=role_result,
        interest_fit_detail=interest_result,
    )


@pytest.fixture(scope="session")
def profile_data() -> dict:
    """Return the eval profile data (fixed, not from DB)."""
    return EVAL_PROFILE_DATA


@pytest.fixture(scope="session")
def all_scores() -> dict[str, EvalResult]:
    """Score all 16 test cases once (session-scoped).

    Makes real LLM calls. Results cached for all test functions.
    """

    async def _run():
        tasks = {tc.case_id: _score_one_case(tc.case_id, tc.job) for tc in TEST_CASES}
        results = {}
        # Run with concurrency limit of 10
        sem = asyncio.Semaphore(10)

        async def bounded(cid, coro):
            async with sem:
                return cid, await coro

        gathered = await asyncio.gather(
            *[bounded(cid, coro) for cid, coro in tasks.items()],
            return_exceptions=True,
        )
        for item in gathered:
            if isinstance(item, Exception):
                logger.error("Scoring failed: %s", item)
                continue
            cid, result = item
            results[cid] = result
            logger.info(
                "Scored %s: role=%d interest=%d composite=%.1f",
                cid,
                result.role_fit_score,
                result.interest_fit_score,
                result.composite,
            )
        return results

    return asyncio.get_event_loop().run_until_complete(_run())


@pytest.fixture
def test_cases() -> dict[str, ScoringTestCase]:
    """Return test cases lookup dict."""
    return TEST_CASES_BY_ID
