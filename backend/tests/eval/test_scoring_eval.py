"""Main scoring evaluation suite.

Runs real LLM calls against 16 synthetic test cases and checks:
- Score ranges per case
- Pairwise ordering constraints
- Deal-breaker detection
- Sub-score arithmetic consistency
- Score calibration and range utilization
"""

from __future__ import annotations

import pytest

from tests.eval.fixtures import TEST_CASES, TEST_CASES_BY_ID
from tests.eval.metrics import (
    EvalResult,
    calibration_stats,
    check_arithmetic,
    check_range_validity,
    deal_breaker_detection_rate,
    generate_report,
    pairwise_accuracy,
    tier_separation,
)

pytestmark = pytest.mark.eval


# ---------------------------------------------------------------------------
# Score range tests (parametrized over all 16 cases)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", TEST_CASES, ids=[tc.case_id for tc in TEST_CASES])
def test_role_fit_in_range(all_scores: dict[str, EvalResult], case):
    """Role fit score falls within expected range."""
    if case.case_id not in all_scores:
        pytest.skip(f"No score for {case.case_id}")
    r = all_scores[case.case_id]
    lo, hi = case.role_fit_range
    assert lo <= r.role_fit_score <= hi, (
        f"{case.case_id} role_fit={r.role_fit_score}, expected [{lo}, {hi}]"
    )


@pytest.mark.parametrize("case", TEST_CASES, ids=[tc.case_id for tc in TEST_CASES])
def test_interest_fit_in_range(all_scores: dict[str, EvalResult], case):
    """Interest fit score falls within expected range."""
    if case.case_id not in all_scores:
        pytest.skip(f"No score for {case.case_id}")
    r = all_scores[case.case_id]
    lo, hi = case.interest_fit_range
    assert lo <= r.interest_fit_score <= hi, (
        f"{case.case_id} interest_fit={r.interest_fit_score}, expected [{lo}, {hi}]"
    )


# ---------------------------------------------------------------------------
# Pairwise ordering
# ---------------------------------------------------------------------------


def test_pairwise_ordering(all_scores, test_cases):
    """All must_beat constraints are satisfied."""
    acc, violations = pairwise_accuracy(all_scores, test_cases)
    # Allow up to 10% violation rate
    assert acc >= 90.0, (
        f"Pairwise accuracy {acc:.1f}% < 90%. Violations:\n"
        + "\n".join(violations[:10])
    )


# ---------------------------------------------------------------------------
# Deal-breaker detection
# ---------------------------------------------------------------------------


DEAL_BREAKER_CASES = [tc for tc in TEST_CASES if tc.has_deal_breaker]


@pytest.mark.parametrize(
    "case", DEAL_BREAKER_CASES, ids=[tc.case_id for tc in DEAL_BREAKER_CASES]
)
def test_deal_breaker_interest_low(all_scores: dict[str, EvalResult], case):
    """Deal-breaker jobs get interest_fit < 30."""
    if case.case_id not in all_scores:
        pytest.skip(f"No score for {case.case_id}")
    r = all_scores[case.case_id]
    assert r.interest_fit_score < 30, (
        f"{case.case_id} [{case.deal_breaker_keyword}] "
        f"interest_fit={r.interest_fit_score}, expected <30"
    )


@pytest.mark.parametrize(
    "case", DEAL_BREAKER_CASES, ids=[tc.case_id for tc in DEAL_BREAKER_CASES]
)
def test_deal_breaker_org_fit_low(all_scores: dict[str, EvalResult], case):
    """Deal-breaker jobs get organization_fit <= 10."""
    if case.case_id not in all_scores:
        pytest.skip(f"No score for {case.case_id}")
    r = all_scores[case.case_id]
    org_score = r.organization_fit_score
    if org_score is None:
        pytest.skip("organization_fit sub-score not found in response")
    assert org_score <= 10, (
        f"{case.case_id} [{case.deal_breaker_keyword}] "
        f"org_fit={org_score}, expected <=10"
    )


def test_deal_breaker_detection_rate_overall(all_scores, test_cases):
    """Overall deal-breaker detection rate is 100%."""
    rate, failures = deal_breaker_detection_rate(all_scores, test_cases)
    assert rate == 100.0, (
        f"Deal-breaker detection {rate:.0f}%. Failures:\n"
        + "\n".join(failures)
    )


# ---------------------------------------------------------------------------
# Tier separation
# ---------------------------------------------------------------------------


def test_tier_separation_no_bleed(all_scores):
    """Adjacent tiers have positive separation (no bleed-through)."""
    gaps = tier_separation(all_scores)
    bleed = {k: v for k, v in gaps.items() if v < 0}
    assert not bleed, (
        f"Tier bleed-through detected: {bleed}"
    )


# ---------------------------------------------------------------------------
# Sub-score consistency
# ---------------------------------------------------------------------------


def test_arithmetic_consistency(all_scores):
    """Reported totals equal sum of sub-scores."""
    discrepancies = check_arithmetic(all_scores)
    assert not discrepancies, (
        f"Arithmetic errors ({len(discrepancies)}):\n"
        + "\n".join(discrepancies[:10])
    )


def test_range_validity(all_scores):
    """All sub-scores within their max range."""
    violations = check_range_validity(all_scores)
    assert not violations, (
        f"Range violations ({len(violations)}):\n"
        + "\n".join(violations[:10])
    )


# ---------------------------------------------------------------------------
# Score calibration
# ---------------------------------------------------------------------------


def test_score_range_utilization(all_scores):
    """Score standard deviation >= 15 (no compression)."""
    cal = calibration_stats(all_scores)
    assert cal.std_composite >= 15, (
        f"Score compression: std={cal.std_composite:.1f} < 15. "
        f"Range: {cal.min_composite:.1f}-{cal.max_composite:.1f}"
    )


def test_tier_centroids_reasonable(all_scores):
    """Tier centroids are within +-20 of expected values."""
    cal = calibration_stats(all_scores)
    expected = {
        "perfect": 88.0,
        "good": 65.0,
        "mediocre": 42.0,
        "mismatch": 15.0,
    }
    for tier_name, exp in expected.items():
        actual = cal.tier_centroids.get(tier_name)
        if actual is None:
            continue
        assert abs(actual - exp) <= 20, (
            f"Tier '{tier_name}' centroid {actual:.1f} too far from expected {exp:.0f}"
        )


# ---------------------------------------------------------------------------
# Full report (always runs last, prints summary)
# ---------------------------------------------------------------------------


def test_generate_full_report(all_scores, test_cases, capsys):
    """Generate and print the full evaluation report."""
    report = generate_report(all_scores, test_cases)
    print(f"\n{report}")
    # This test always passes — it's for visibility
    assert len(all_scores) > 0, "No scores generated"
