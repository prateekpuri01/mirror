"""Unit tests for the pure-Python metrics in metrics_external.py.

These tests don't make any LLM or network calls — they validate the math.
Run with: docker compose exec api python -m pytest tests/eval/external/test_metrics_external.py -v
"""

from __future__ import annotations

import math

import pytest

from tests.eval.external.metrics_external import (
    classification_accuracy,
    classify_score,
    confusion_matrix,
    format_confusion_matrix,
    kendall_tau,
    mean_score_by_class,
    ndcg_at_k,
    pairwise_accuracy_by_class,
    pairwise_accuracy_from_ranking,
    pearson_r,
    spearman_rho,
    top_k_precision,
)


# ---------------------------------------------------------------------------
# Spearman ρ
# ---------------------------------------------------------------------------


class TestSpearmanRho:
    def test_perfect_positive(self):
        assert spearman_rho([1, 2, 3, 4, 5], [10, 20, 30, 40, 50]) == pytest.approx(1.0)

    def test_perfect_negative(self):
        assert spearman_rho([1, 2, 3, 4, 5], [50, 40, 30, 20, 10]) == pytest.approx(-1.0)

    def test_partial_agreement(self):
        # One swap in the middle of an otherwise perfect sequence
        rho = spearman_rho([1, 2, 3, 4, 5], [2, 1, 4, 3, 5])
        assert 0.7 < rho < 0.9

    def test_no_correlation(self):
        rho = spearman_rho([1, 2, 3, 4], [3, 1, 4, 2])
        assert -0.5 < rho < 0.5

    def test_handles_ties(self):
        # All ties → 0 (no variance)
        assert spearman_rho([1, 1, 1, 1], [2, 2, 2, 2]) == 0.0

    def test_degenerate_inputs(self):
        assert spearman_rho([], []) == 0.0
        assert spearman_rho([1], [2]) == 0.0
        assert spearman_rho([1, 2], [1]) == 0.0  # mismatched lengths


# ---------------------------------------------------------------------------
# Kendall τ
# ---------------------------------------------------------------------------


class TestKendallTau:
    def test_perfect_positive(self):
        assert kendall_tau([1, 2, 3, 4, 5], [10, 20, 30, 40, 50]) == pytest.approx(1.0)

    def test_perfect_negative(self):
        assert kendall_tau([1, 2, 3, 4, 5], [50, 40, 30, 20, 10]) == pytest.approx(-1.0)

    def test_partial(self):
        tau = kendall_tau([1, 2, 3, 4, 5], [2, 1, 4, 3, 5])
        assert 0.4 < tau < 0.7

    def test_degenerate(self):
        assert kendall_tau([], []) == 0.0
        assert kendall_tau([1], [2]) == 0.0


# ---------------------------------------------------------------------------
# nDCG@k
# ---------------------------------------------------------------------------


class TestNdcgAtK:
    def test_perfect_ordering(self):
        # Predicted A,B,C with grades A=3, B=2, C=1 → DCG == ideal DCG → 1.0
        ndcg = ndcg_at_k(["a", "b", "c"], {"a": 3, "b": 2, "c": 1}, k=5)
        assert ndcg == pytest.approx(1.0)

    def test_reversed_ordering(self):
        # Predicted C,B,A but A is most relevant → low score
        ndcg = ndcg_at_k(["c", "b", "a"], {"a": 3, "b": 2, "c": 1}, k=5)
        assert ndcg < 0.85  # Reversal penalty, but log-scale damps it

    def test_top_k_cutoff(self):
        # Only the top k items count
        ndcg = ndcg_at_k(["a", "b", "z"], {"a": 3, "b": 2, "z": 0, "c": 1}, k=2)
        assert ndcg == pytest.approx(1.0)

    def test_empty_inputs(self):
        assert ndcg_at_k([], {"a": 1}, k=5) == 0.0
        assert ndcg_at_k(["a"], {}, k=5) == 0.0
        assert ndcg_at_k(["a"], {"a": 1}, k=0) == 0.0


# ---------------------------------------------------------------------------
# top_k_precision
# ---------------------------------------------------------------------------


class TestTopKPrecision:
    def test_full_overlap(self):
        assert top_k_precision(["a", "b", "c"], ["a", "b", "c"]) == 1.0

    def test_partial_overlap(self):
        assert top_k_precision(["a", "b", "c"], ["a", "b", "d"]) == pytest.approx(2 / 3)

    def test_no_overlap(self):
        assert top_k_precision(["a", "b", "c"], ["x", "y", "z"]) == 0.0

    def test_order_doesnt_matter(self):
        # We only care about set membership
        assert top_k_precision(["c", "a", "b"], ["a", "b", "d"]) == pytest.approx(2 / 3)

    def test_empty(self):
        assert top_k_precision([], ["a"]) == 0.0


# ---------------------------------------------------------------------------
# Pairwise accuracy by class (HF-style)
# ---------------------------------------------------------------------------


class TestPairwiseAccuracyByClass:
    def test_well_ordered_classes(self):
        scores = {
            "low": [10, 20, 30],
            "mid": [40, 50, 60],
            "high": [70, 80, 90],
        }
        result = pairwise_accuracy_by_class(scores, ["low", "mid", "high"])
        assert result["high"]["low"] == 1.0
        assert result["high"]["mid"] == 1.0
        assert result["mid"]["low"] == 1.0

    def test_reversed_classes(self):
        # Higher class has LOWER scores — pairwise should be 0
        scores = {
            "low": [70, 80, 90],
            "mid": [40, 50, 60],
            "high": [10, 20, 30],
        }
        result = pairwise_accuracy_by_class(scores, ["low", "mid", "high"])
        assert result["high"]["low"] == 0.0
        assert result["mid"]["low"] == 0.0

    def test_overlap(self):
        # Realistic overlap — high>low usually but high vs mid is ambiguous
        scores = {
            "low": [10, 20, 30],
            "mid": [40, 50, 60],
            "high": [35, 55, 75],  # one below mid's max, one above
        }
        result = pairwise_accuracy_by_class(scores, ["low", "mid", "high"])
        assert result["high"]["low"] == 1.0  # all 9 pairs: high > low
        assert 0.4 < result["high"]["mid"] < 0.7  # noisy

    def test_ties_count_as_half(self):
        # All values equal between two classes — pairwise should be 0.5
        scores = {"low": [50, 50], "high": [50, 50]}
        result = pairwise_accuracy_by_class(scores, ["low", "high"])
        assert result["high"]["low"] == 0.5

    def test_missing_class_returns_zero(self):
        scores = {"low": [10, 20], "high": []}
        result = pairwise_accuracy_by_class(scores, ["low", "high"])
        assert result["high"]["low"] == 0.0


# ---------------------------------------------------------------------------
# Pairwise accuracy from ranking (Vanetik-style)
# ---------------------------------------------------------------------------


class TestPairwiseAccuracyFromRanking:
    def test_perfect_match(self):
        assert pairwise_accuracy_from_ranking(
            ["a", "b", "c", "d", "e"], ["a", "b", "c", "d", "e"]
        ) == 1.0

    def test_one_swap(self):
        # 1 inverted pair out of 10 total
        acc = pairwise_accuracy_from_ranking(
            ["a", "c", "b", "d", "e"], ["a", "b", "c", "d", "e"]
        )
        assert acc == pytest.approx(0.9)

    def test_fully_reversed(self):
        assert pairwise_accuracy_from_ranking(
            ["e", "d", "c", "b", "a"], ["a", "b", "c", "d", "e"]
        ) == 0.0

    def test_three_items(self):
        # 3 items = 3 pairs
        acc = pairwise_accuracy_from_ranking(["b", "a", "c"], ["a", "b", "c"])
        assert acc == pytest.approx(2 / 3)

    def test_mismatched_lengths(self):
        assert pairwise_accuracy_from_ranking(["a", "b"], ["a", "b", "c"]) == 0.0

    def test_too_short(self):
        assert pairwise_accuracy_from_ranking(["a"], ["a"]) == 0.0


# ---------------------------------------------------------------------------
# Mean score by class
# ---------------------------------------------------------------------------


class TestMeanScoreByClass:
    def test_basic(self):
        scored = [
            (10.0, "No Fit"), (20.0, "No Fit"),
            (50.0, "Good Fit"), (60.0, "Good Fit"),
        ]
        result = mean_score_by_class(scored)
        assert result["No Fit"] == {"n": 2, "mean": 15.0, "min": 10.0, "max": 20.0}
        assert result["Good Fit"] == {"n": 2, "mean": 55.0, "min": 50.0, "max": 60.0}

    def test_single_example(self):
        result = mean_score_by_class([(42.0, "x")])
        assert result["x"] == {"n": 1, "mean": 42.0, "min": 42.0, "max": 42.0}

    def test_empty(self):
        assert mean_score_by_class([]) == {}


# ---------------------------------------------------------------------------
# classify_score and classification_accuracy
# ---------------------------------------------------------------------------


class TestClassifyScore:
    def test_no_fit(self):
        assert classify_score(0) == "No Fit"
        assert classify_score(39.99) == "No Fit"

    def test_potential_fit(self):
        assert classify_score(40) == "Potential Fit"
        assert classify_score(55) == "Potential Fit"
        assert classify_score(69.99) == "Potential Fit"

    def test_good_fit(self):
        assert classify_score(70) == "Good Fit"
        assert classify_score(100) == "Good Fit"

    def test_custom_thresholds(self):
        assert classify_score(20, low=15, high=80) == "Potential Fit"
        assert classify_score(85, low=15, high=80) == "Good Fit"


class TestClassificationAccuracy:
    def test_perfect(self):
        assert classification_accuracy(["a", "b", "c"], ["a", "b", "c"]) == 1.0

    def test_partial(self):
        assert classification_accuracy(["a", "b", "x"], ["a", "b", "c"]) == pytest.approx(2 / 3)

    def test_none_correct(self):
        assert classification_accuracy(["x", "y", "z"], ["a", "b", "c"]) == 0.0

    def test_mismatched_lengths(self):
        assert classification_accuracy(["a", "b"], ["a", "b", "c"]) == 0.0


# ---------------------------------------------------------------------------
# Confusion matrix
# ---------------------------------------------------------------------------


class TestConfusionMatrix:
    def test_basic(self):
        predicted = ["a", "b", "a", "c"]
        actual = ["a", "a", "b", "c"]
        cm = confusion_matrix(predicted, actual, labels=["a", "b", "c"])
        # actual=a, predicted=a: 1 (only the first one)
        assert cm["a"]["a"] == 1
        # actual=a, predicted=b: 1 (the second one)
        assert cm["a"]["b"] == 1
        # actual=b, predicted=a: 1 (the third)
        assert cm["b"]["a"] == 1
        # actual=c, predicted=c: 1 (the fourth)
        assert cm["c"]["c"] == 1

    def test_format_renders(self):
        cm = {"a": {"a": 1, "b": 0}, "b": {"a": 0, "b": 1}}
        out = format_confusion_matrix(cm)
        assert "a" in out
        assert "b" in out

    def test_auto_labels(self):
        # No labels provided → derived from union of inputs
        cm = confusion_matrix(["x", "y"], ["x", "x"])
        assert "x" in cm
        assert "y" in cm


# ---------------------------------------------------------------------------
# Pearson r
# ---------------------------------------------------------------------------


class TestPearson:
    def test_perfect_linear(self):
        assert pearson_r([1, 2, 3, 4, 5], [2, 4, 6, 8, 10]) == pytest.approx(1.0)

    def test_perfect_negative(self):
        assert pearson_r([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)

    def test_no_variance(self):
        # All same values → no correlation possible
        assert pearson_r([1, 1, 1], [1, 2, 3]) == 0.0

    def test_degenerate(self):
        assert pearson_r([], []) == 0.0
        assert pearson_r([1], [2]) == 0.0
