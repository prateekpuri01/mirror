"""Evaluation metrics for scoring quality assessment."""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from tests.eval.fixtures import TIERS, ScoringTestCase


@dataclass
class EvalResult:
    """Parsed scores for a single test case."""

    case_id: str
    role_fit_score: int
    interest_fit_score: int
    role_fit_detail: dict
    interest_fit_detail: dict

    @property
    def composite(self) -> float:
        return 0.6 * self.role_fit_score + 0.4 * self.interest_fit_score

    @property
    def organization_fit_score(self) -> int | None:
        """Extract organization_fit sub-score from interest fit detail."""
        org = self.interest_fit_detail.get("interest_fit", {}).get("organization_fit", {})
        return org.get("score")

    @property
    def deal_breaker_detected(self) -> bool | None:
        """Check if the model flagged a deal-breaker."""
        return self.interest_fit_detail.get("interest_fit", {}).get("deal_breaker_detected")


def compute_composite(role_fit: int, interest_fit: int) -> float:
    """Compute composite relevance score."""
    return 0.6 * role_fit + 0.4 * interest_fit


# ---------------------------------------------------------------------------
# 2.1 Ranking Accuracy
# ---------------------------------------------------------------------------


def pairwise_accuracy(
    results: dict[str, EvalResult],
    test_cases: dict[str, ScoringTestCase],
) -> tuple[float, list[str]]:
    """Compute % of must_beat constraints satisfied.

    Returns (accuracy_pct, list_of_violations).
    """
    total = 0
    satisfied = 0
    violations = []

    for case_id, tc in test_cases.items():
        if case_id not in results:
            continue
        for beaten_id in tc.must_beat:
            if beaten_id not in results:
                continue
            total += 1
            if results[case_id].composite > results[beaten_id].composite:
                satisfied += 1
            else:
                violations.append(
                    f"{case_id} ({results[case_id].composite:.1f}) should beat "
                    f"{beaten_id} ({results[beaten_id].composite:.1f})"
                )

    accuracy = (satisfied / total * 100) if total > 0 else 0.0
    return accuracy, violations


def tier_separation(results: dict[str, EvalResult]) -> dict[str, float]:
    """Compute gap between adjacent tiers.

    Returns dict like {"perfect-good": 12.5, "good-mediocre": 8.3, ...}
    Positive values = clean separation. Negative = bleed-through.
    """
    tier_order = ["perfect", "good", "mediocre", "mismatch"]
    tier_composites: dict[str, list[float]] = {}

    for tier_name, cases in TIERS.items():
        composites = []
        for tc in cases:
            if tc.case_id in results:
                composites.append(results[tc.case_id].composite)
        if composites:
            tier_composites[tier_name] = composites

    gaps = {}
    for i in range(len(tier_order) - 1):
        upper = tier_order[i]
        lower = tier_order[i + 1]
        if upper in tier_composites and lower in tier_composites:
            upper_min = min(tier_composites[upper])
            lower_max = max(tier_composites[lower])
            gaps[f"{upper}-{lower}"] = upper_min - lower_max

    return gaps


# ---------------------------------------------------------------------------
# 2.2 Deal-Breaker Detection
# ---------------------------------------------------------------------------


def deal_breaker_detection_rate(
    results: dict[str, EvalResult],
    test_cases: dict[str, ScoringTestCase],
) -> tuple[float, list[str]]:
    """Check deal-breaker cases have org_fit <= 5 and interest < 25.

    Returns (detection_rate_pct, list_of_failures).
    """
    db_cases = {cid: tc for cid, tc in test_cases.items() if tc.has_deal_breaker}
    if not db_cases:
        return 100.0, []

    detected = 0
    failures = []
    for cid, tc in db_cases.items():
        if cid not in results:
            continue
        r = results[cid]
        org_score = r.organization_fit_score
        interest = r.interest_fit_score

        org_ok = org_score is not None and org_score <= 10
        interest_ok = interest < 30

        if org_ok and interest_ok:
            detected += 1
        else:
            parts = []
            if not org_ok:
                parts.append(f"org_fit={org_score} (expected <=10)")
            if not interest_ok:
                parts.append(f"interest={interest} (expected <30)")
            failures.append(f"{cid} [{tc.deal_breaker_keyword}]: {', '.join(parts)}")

    total = len([cid for cid in db_cases if cid in results])
    rate = (detected / total * 100) if total > 0 else 0.0
    return rate, failures


# ---------------------------------------------------------------------------
# 2.3 Score Calibration
# ---------------------------------------------------------------------------


@dataclass
class CalibrationStats:
    min_composite: float
    max_composite: float
    mean_composite: float
    std_composite: float
    range_utilization: float  # max - min
    tier_centroids: dict[str, float]  # tier -> mean composite

    # Expected tier centroids for comparison
    EXPECTED_CENTROIDS = {
        "perfect": 88.0,
        "good": 65.0,
        "mediocre": 42.0,
        "mismatch": 15.0,
    }


def calibration_stats(results: dict[str, EvalResult]) -> CalibrationStats:
    """Compute calibration statistics across all scored cases."""
    composites = [r.composite for r in results.values()]

    tier_centroids = {}
    for tier_name, cases in TIERS.items():
        tier_scores = [results[tc.case_id].composite for tc in cases if tc.case_id in results]
        if tier_scores:
            tier_centroids[tier_name] = statistics.mean(tier_scores)

    return CalibrationStats(
        min_composite=min(composites),
        max_composite=max(composites),
        mean_composite=statistics.mean(composites),
        std_composite=statistics.stdev(composites) if len(composites) > 1 else 0.0,
        range_utilization=max(composites) - min(composites),
        tier_centroids=tier_centroids,
    )


# ---------------------------------------------------------------------------
# 2.4 Sub-Score Consistency
# ---------------------------------------------------------------------------


def check_arithmetic(results: dict[str, EvalResult]) -> list[str]:
    """Verify reported total = sum of sub-scores for each case.

    Returns list of discrepancy descriptions.
    """
    discrepancies = []

    for cid, r in results.items():
        # Check role fit arithmetic
        rf = r.role_fit_detail.get("role_fit", {})
        rf_sub_scores = [
            rf.get("hard_skills", {}).get("score", 0),
            rf.get("experience_level", {}).get("score", 0),
            rf.get("domain_relevance", {}).get("score", 0),
            rf.get("education_fit", {}).get("score", 0),
        ]
        rf_sum = sum(rf_sub_scores)
        rf_reported = rf.get("score", 0)
        if rf_sum != rf_reported:
            discrepancies.append(
                f"{cid} role_fit: reported={rf_reported}, sum={rf_sum} "
                f"(sub-scores: {rf_sub_scores})"
            )

        # Check interest fit arithmetic
        intf = r.interest_fit_detail.get("interest_fit", {})
        if_sub_scores = [
            intf.get("role_alignment", {}).get("score", 0),
            intf.get("domain_excitement", {}).get("score", 0),
            intf.get("organization_fit", {}).get("score", 0),
            intf.get("practical_factors", {}).get("score", 0),
        ]
        if_sum = sum(if_sub_scores)
        if_reported = intf.get("score", 0)
        if if_sum != if_reported:
            discrepancies.append(
                f"{cid} interest_fit: reported={if_reported}, sum={if_sum} "
                f"(sub-scores: {if_sub_scores})"
            )

    return discrepancies


def check_range_validity(results: dict[str, EvalResult]) -> list[str]:
    """Check each sub-score is within its max range.

    Returns list of out-of-range descriptions.
    """
    violations = []

    role_fit_maxes = {
        "hard_skills": 30,
        "experience_level": 20,
        "domain_relevance": 30,
        "education_fit": 20,
    }
    interest_fit_maxes = {
        "role_alignment": 25,
        "domain_excitement": 30,
        "organization_fit": 25,
        "practical_factors": 20,
    }

    for cid, r in results.items():
        rf = r.role_fit_detail.get("role_fit", {})
        for dim, max_val in role_fit_maxes.items():
            score = rf.get(dim, {}).get("score", 0)
            if score < 0 or score > max_val:
                violations.append(f"{cid} role_fit.{dim}={score} (max={max_val})")

        intf = r.interest_fit_detail.get("interest_fit", {})
        for dim, max_val in interest_fit_maxes.items():
            score = intf.get(dim, {}).get("score", 0)
            if score < 0 or score > max_val:
                violations.append(f"{cid} interest_fit.{dim}={score} (max={max_val})")

    return violations


# ---------------------------------------------------------------------------
# Summary report
# ---------------------------------------------------------------------------


def generate_report(
    results: dict[str, EvalResult],
    test_cases: dict[str, ScoringTestCase],
) -> str:
    """Generate a human-readable evaluation report."""
    lines = ["=" * 60, "SCORING EVAL REPORT", "=" * 60, ""]

    # Individual scores
    lines.append("Individual Scores:")
    lines.append(f"{'Case':6} {'Tier':10} {'Role':5} {'Int':5} {'Comp':6}")
    lines.append("-" * 35)
    for tc in sorted(test_cases.values(), key=lambda t: t.case_id):
        if tc.case_id in results:
            r = results[tc.case_id]
            lines.append(
                f"{tc.case_id:6} {tc.tier:10} {r.role_fit_score:5d} "
                f"{r.interest_fit_score:5d} {r.composite:6.1f}"
            )
    lines.append("")

    # Pairwise accuracy
    acc, violations = pairwise_accuracy(results, test_cases)
    lines.append(f"Pairwise Accuracy: {acc:.1f}%")
    if violations:
        lines.append(f"  Violations ({len(violations)}):")
        for v in violations[:10]:
            lines.append(f"    - {v}")
    lines.append("")

    # Tier separation
    gaps = tier_separation(results)
    lines.append("Tier Separation:")
    for pair, gap in gaps.items():
        status = "OK" if gap > 0 else "BLEED"
        lines.append(f"  {pair}: {gap:+.1f} ({status})")
    lines.append("")

    # Deal-breaker detection
    db_rate, db_failures = deal_breaker_detection_rate(results, test_cases)
    lines.append(f"Deal-Breaker Detection: {db_rate:.0f}%")
    if db_failures:
        for f in db_failures:
            lines.append(f"  MISS: {f}")
    lines.append("")

    # Calibration
    cal = calibration_stats(results)
    lines.append("Calibration:")
    lines.append(f"  Range: {cal.min_composite:.1f} - {cal.max_composite:.1f}")
    lines.append(f"  Mean: {cal.mean_composite:.1f}, Std: {cal.std_composite:.1f}")
    if cal.std_composite < 15:
        lines.append("  WARNING: Score compression detected (std < 15)")
    lines.append("  Tier Centroids (actual vs expected):")
    for tier_name in ["perfect", "good", "mediocre", "mismatch"]:
        actual = cal.tier_centroids.get(tier_name, 0)
        expected = CalibrationStats.EXPECTED_CENTROIDS.get(tier_name, 0)
        lines.append(f"    {tier_name}: {actual:.1f} (expected ~{expected:.0f})")
    lines.append("")

    # Arithmetic
    arith = check_arithmetic(results)
    lines.append(f"Arithmetic Errors: {len(arith)}")
    for a in arith[:5]:
        lines.append(f"  - {a}")
    lines.append("")

    # Range validity
    range_v = check_range_validity(results)
    lines.append(f"Range Violations: {len(range_v)}")
    for rv in range_v[:5]:
        lines.append(f"  - {rv}")

    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)
