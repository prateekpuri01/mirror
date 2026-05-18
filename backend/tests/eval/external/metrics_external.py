"""Pure-Python ranking and classification metrics for external evaluation.

No numpy/scipy dependency — keeps the eval harness lean. All metrics handle
ties via average ranks (Spearman) or position (nDCG).
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict


# ---------------------------------------------------------------------------
# Rank correlation
# ---------------------------------------------------------------------------


def _average_ranks(values: list[float]) -> list[float]:
    """Convert values to ranks. Ties get average rank (standard Spearman convention)."""
    n = len(values)
    indexed = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[indexed[j + 1]] == values[indexed[i]]:
            j += 1
        avg_rank = (i + j) / 2 + 1  # 1-indexed average
        for k in range(i, j + 1):
            ranks[indexed[k]] = avg_rank
        i = j + 1
    return ranks


def spearman_rho(xs: list[float], ys: list[float]) -> float:
    """Spearman rank correlation coefficient.

    Returns 0.0 for degenerate inputs (length < 2 or zero variance in either).
    """
    n = len(xs)
    if n < 2 or len(ys) != n:
        return 0.0
    rx = _average_ranks(xs)
    ry = _average_ranks(ys)
    mean_x = sum(rx) / n
    mean_y = sum(ry) / n
    num = sum((rx[i] - mean_x) * (ry[i] - mean_y) for i in range(n))
    den_x = math.sqrt(sum((rx[i] - mean_x) ** 2 for i in range(n)))
    den_y = math.sqrt(sum((ry[i] - mean_y) ** 2 for i in range(n)))
    if den_x == 0 or den_y == 0:
        return 0.0
    return num / (den_x * den_y)


def kendall_tau(xs: list[float], ys: list[float]) -> float:
    """Kendall tau-b rank correlation. Robust to ties.

    Returns 0.0 for degenerate inputs.
    """
    n = len(xs)
    if n < 2 or len(ys) != n:
        return 0.0
    concordant = 0
    discordant = 0
    tx = 0  # ties only in xs
    ty = 0  # ties only in ys
    for i in range(n):
        for j in range(i + 1, n):
            dx = xs[i] - xs[j]
            dy = ys[i] - ys[j]
            if dx == 0 and dy == 0:
                continue
            if dx == 0:
                tx += 1
            elif dy == 0:
                ty += 1
            elif (dx > 0) == (dy > 0):
                concordant += 1
            else:
                discordant += 1
    denom = math.sqrt((concordant + discordant + tx) * (concordant + discordant + ty))
    if denom == 0:
        return 0.0
    return (concordant - discordant) / denom


# ---------------------------------------------------------------------------
# Top-k precision and nDCG
# ---------------------------------------------------------------------------


def top_k_precision(predicted_top_k: list, ground_truth_top_k: list) -> float:
    """Fraction of predicted top-k items that appear in ground truth top-k."""
    if not predicted_top_k:
        return 0.0
    gt_set = set(ground_truth_top_k)
    hits = sum(1 for item in predicted_top_k if item in gt_set)
    return hits / len(predicted_top_k)


def ndcg_at_k(
    predicted_order: list,
    relevance_grades: dict,
    k: int,
) -> float:
    """Normalized DCG at rank k.

    `predicted_order` — list of item ids in the order our algorithm ranked them
    `relevance_grades` — {item_id: relevance_value (0 = irrelevant, higher = more relevant)}
    `k` — cutoff for the @k computation
    """
    if not predicted_order or k <= 0:
        return 0.0

    def _dcg(items: list, grades: dict) -> float:
        return sum(
            grades.get(item, 0) / math.log2(i + 2)
            for i, item in enumerate(items[:k])
        )

    dcg = _dcg(predicted_order, relevance_grades)
    # Ideal ordering = sort items by relevance descending
    ideal_order = sorted(relevance_grades.keys(), key=lambda x: -relevance_grades[x])
    idcg = _dcg(ideal_order, relevance_grades)
    if idcg == 0:
        return 0.0
    return dcg / idcg


# ---------------------------------------------------------------------------
# Classification metrics
# ---------------------------------------------------------------------------


def classify_score(score: float, low: float = 40, high: float = 70) -> str:
    """Bin a 0-100 fit score into No Fit / Potential Fit / Good Fit."""
    if score < low:
        return "No Fit"
    if score < high:
        return "Potential Fit"
    return "Good Fit"


def classification_accuracy(predicted: list[str], actual: list[str]) -> float:
    """Fraction of predictions that match the ground-truth class."""
    if not predicted or len(predicted) != len(actual):
        return 0.0
    hits = sum(1 for p, a in zip(predicted, actual) if p == a)
    return hits / len(predicted)


def confusion_matrix(
    predicted: list[str],
    actual: list[str],
    labels: list[str] | None = None,
) -> dict[str, dict[str, int]]:
    """Build a confusion matrix as a nested dict {actual: {predicted: count}}."""
    if labels is None:
        labels = sorted(set(actual) | set(predicted))
    cm = {a: {p: 0 for p in labels} for a in labels}
    for p, a in zip(predicted, actual):
        if a in cm and p in cm[a]:
            cm[a][p] += 1
    return cm


def format_confusion_matrix(cm: dict[str, dict[str, int]]) -> str:
    """Return a human-readable string representation of a confusion matrix."""
    labels = list(cm.keys())
    col_width = max(15, max(len(l) for l in labels) + 2)
    header = "actual \\ predicted".ljust(col_width)
    for l in labels:
        header += l.ljust(col_width)
    lines = [header, "-" * len(header)]
    for actual_label in labels:
        row = actual_label.ljust(col_width)
        for pred_label in labels:
            row += str(cm[actual_label].get(pred_label, 0)).ljust(col_width)
        lines.append(row)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pairwise ordering accuracy — the right metric when absolute calibration
# doesn't matter, only relative ranking does.
# ---------------------------------------------------------------------------


def pairwise_accuracy_by_class(
    scores_by_class: dict[str, list[float]],
    class_order: list[str],
) -> dict[str, dict[str, float]]:
    """For each ordered pair (high_class, low_class) where high should outscore low,
    compute the fraction of cross-class score pairs we get right.

    Args:
      scores_by_class: {label: [list of composite scores for examples in that class]}
      class_order: classes from worst to best (e.g. ["No Fit", "Potential Fit", "Good Fit"])

    Returns:
      {high_class: {low_class: pairwise_accuracy}} for high_class index > low_class index.
      Ties count as 0.5 (consistent with Wilcoxon-Mann-Whitney convention).
    """
    out: dict[str, dict[str, float]] = {}
    for hi_idx in range(len(class_order) - 1, -1, -1):
        hi = class_order[hi_idx]
        out[hi] = {}
        for lo_idx in range(hi_idx):
            lo = class_order[lo_idx]
            hi_scores = scores_by_class.get(hi, [])
            lo_scores = scores_by_class.get(lo, [])
            if not hi_scores or not lo_scores:
                out[hi][lo] = 0.0
                continue
            wins = 0.0
            total = 0
            for h in hi_scores:
                for l in lo_scores:
                    total += 1
                    if h > l:
                        wins += 1.0
                    elif h == l:
                        wins += 0.5
            out[hi][lo] = wins / total if total else 0.0
    return out


def pairwise_accuracy_from_ranking(
    our_ranking: list,
    truth_ranking: list,
) -> float:
    """Pairwise accuracy between two complete rankings of the same items.

    For each unordered pair (i, j), check whether our ranking puts them in the
    same relative order as the truth ranking. Returns the fraction we got right.

    For 5 items there are 10 pairs, so this gives discrete steps of 0.1.
    """
    if len(our_ranking) != len(truth_ranking) or len(our_ranking) < 2:
        return 0.0
    # Position lookup: item -> rank (1 = best)
    our_pos = {item: i for i, item in enumerate(our_ranking)}
    truth_pos = {item: i for i, item in enumerate(truth_ranking)}
    items = list(set(our_pos) & set(truth_pos))
    n = len(items)
    if n < 2:
        return 0.0
    correct = 0
    total = 0
    for i in range(n):
        for j in range(i + 1, n):
            a, b = items[i], items[j]
            our_says_a_first = our_pos[a] < our_pos[b]
            truth_says_a_first = truth_pos[a] < truth_pos[b]
            if our_says_a_first == truth_says_a_first:
                correct += 1
            total += 1
    return correct / total if total else 0.0


def mean_score_by_class(
    scored_examples: list[tuple[float, str]],
) -> dict[str, dict[str, float]]:
    """Compute mean/min/max/n composite score grouped by class label.

    Args:
      scored_examples: list of (composite_score, class_label) tuples.

    Returns:
      {label: {"mean": ..., "min": ..., "max": ..., "n": ...}}
    """
    by_class: dict[str, list[float]] = defaultdict(list)
    for score, label in scored_examples:
        by_class[label].append(score)
    out: dict[str, dict[str, float]] = {}
    for label, scores in by_class.items():
        out[label] = {
            "n": len(scores),
            "mean": round(sum(scores) / len(scores), 1),
            "min": round(min(scores), 1),
            "max": round(max(scores), 1),
        }
    return out


# ---------------------------------------------------------------------------
# Pearson (linear) correlation — useful as a secondary metric
# ---------------------------------------------------------------------------


def pearson_r(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 2 or len(ys) != n:
        return 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    den_x = math.sqrt(sum((xs[i] - mean_x) ** 2 for i in range(n)))
    den_y = math.sqrt(sum((ys[i] - mean_y) ** 2 for i in range(n)))
    if den_x == 0 or den_y == 0:
        return 0.0
    return num / (den_x * den_y)
