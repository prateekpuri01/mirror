"""Perturbation experiments for scoring sensitivity analysis.

Tests that targeted changes to job descriptions produce expected score shifts.
Each test modifies a base case and verifies the scoring responds appropriately.
"""

from __future__ import annotations

import asyncio
import copy

import pytest

from tests.eval.conftest import _score_one_case
from tests.eval.fixtures import TEST_CASES_BY_ID
from tests.eval.metrics import EvalResult

pytestmark = pytest.mark.eval


def _run_async(coro):
    """Helper to run async scoring in sync test context."""
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(coro)


# ---------------------------------------------------------------------------
# Perturbation 1: Seniority swap
# ---------------------------------------------------------------------------


def test_seniority_downgrade_drops_experience_score(all_scores):
    """Changing 'Senior' to 'Junior' in A1 should drop experience_level score."""
    base = TEST_CASES_BY_ID["A1"]
    perturbed_job = copy.deepcopy(base.job)
    perturbed_job["title"] = perturbed_job["title"].replace("Senior", "Junior")

    result = _run_async(_score_one_case("A1_junior", perturbed_job))
    base_result = all_scores.get("A1")

    if base_result is None:
        pytest.skip("Base case A1 not scored")

    base_exp = (
        base_result.role_fit_detail.get("role_fit", {}).get("experience_level", {}).get("score", 0)
    )
    pert_exp = (
        result.role_fit_detail.get("role_fit", {}).get("experience_level", {}).get("score", 0)
    )

    assert pert_exp < base_exp, (
        f"Seniority downgrade: experience_level did not drop (base={base_exp}, junior={pert_exp})"
    )

    # Composite should also drop
    assert result.composite < base_result.composite, (
        f"Seniority downgrade: composite did not drop "
        f"(base={base_result.composite:.1f}, junior={result.composite:.1f})"
    )


# ---------------------------------------------------------------------------
# Perturbation 2: Domain swap
# ---------------------------------------------------------------------------


def test_domain_swap_drops_domain_score():
    """Replacing AI/LLM domain with pharmaceutical manufacturing should drop domain scores."""
    base = TEST_CASES_BY_ID["A1"]
    perturbed_job = copy.deepcopy(base.job)
    perturbed_job["title"] = "Senior Research Scientist, Pharmaceutical Manufacturing"
    perturbed_job["company"] = "Pfizer Manufacturing"
    perturbed_job["description"] = (
        perturbed_job["description"]
        .replace("LLM", "pharmaceutical process")
        .replace("production LLM tools", "pharmaceutical manufacturing automation")
        .replace("LLM optimization", "process optimization")
        .replace("LLM efficiency", "manufacturing efficiency")
        .replace("ML venues (NeurIPS, ICML, ACL)", "pharmaceutical engineering journals")
        .replace("NLP", "chemical engineering")
    )

    result = _run_async(_score_one_case("A1_pharma", perturbed_job))

    # Domain relevance should be substantially lower
    domain_score = (
        result.role_fit_detail.get("role_fit", {}).get("domain_relevance", {}).get("score", 30)
    )
    assert domain_score <= 20, f"Domain swap: domain_relevance={domain_score}, expected <=20"

    # Interest domain_excitement should be low
    domain_exc = (
        result.interest_fit_detail.get("interest_fit", {})
        .get("domain_excitement", {})
        .get("score", 30)
    )
    assert domain_exc <= 20, f"Domain swap: domain_excitement={domain_exc}, expected <=20"


# ---------------------------------------------------------------------------
# Perturbation 3: Deal-breaker injection
# ---------------------------------------------------------------------------


def test_deal_breaker_injection_crashes_org_fit(all_scores):
    """Adding 'ad optimization' to a perfect match should crash org_fit to near-zero."""
    base = TEST_CASES_BY_ID["A1"]
    perturbed_job = copy.deepcopy(base.job)
    perturbed_job["description"] += (
        "\n\nNote: This role focuses on optimizing our ad delivery platform. "
        "You'll work on ad optimization and engagement maximization for our "
        "programmatic advertising customers."
    )

    result = _run_async(_score_one_case("A1_adtech", perturbed_job))

    org_score = result.organization_fit_score
    assert org_score is not None, "organization_fit sub-score not found"
    assert org_score <= 5, f"Deal-breaker injection: org_fit={org_score}, expected <=5"

    # Interest score should drop significantly vs base
    base_result = all_scores.get("A1")
    if base_result:
        assert result.interest_fit_score < base_result.interest_fit_score - 20, (
            f"Deal-breaker injection: interest didn't drop enough "
            f"(base={base_result.interest_fit_score}, injected={result.interest_fit_score})"
        )


# ---------------------------------------------------------------------------
# Perturbation 4: Sparse description
# ---------------------------------------------------------------------------


def test_sparse_description_no_extreme_scores():
    """A 3-sentence job description should not produce extreme scores."""
    sparse_job = {
        "title": "ML Researcher",
        "company": "Stealth AI Startup",
        "description": (
            "We're an early-stage AI startup working on language models. "
            "Looking for an ML researcher to join our small team. "
            "Competitive salary and equity."
        ),
        "salary_min": None,
        "salary_max": None,
        "user_notes": None,
    }

    result = _run_async(_score_one_case("sparse", sparse_job))

    # Should not be at extremes — moderate scores expected
    assert 20 <= result.role_fit_score <= 65, (
        f"Sparse description: role_fit={result.role_fit_score} is extreme "
        f"(expected 20-65 for sparse info)"
    )
    assert 15 <= result.interest_fit_score <= 60, (
        f"Sparse description: interest_fit={result.interest_fit_score} is extreme "
        f"(expected 15-60 for sparse info)"
    )
