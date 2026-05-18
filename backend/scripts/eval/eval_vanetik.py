"""Tier 1 evaluation: rank correlation against human annotators on the Vanetik dataset.

For each annotated CV (1-30), we:
  1. Extract a profile from the .docx resume via the live LLM extractor.
  2. Score that profile against all 5 vacancies via score_pair() (LLM call).
  3. Build our own ranking of the 5 vacancies (highest composite first).
  4. Compare to each annotator's ranking via Spearman / nDCG / top-1 hit.

Output: a JSON report at backend/tests/eval/external/results/vanetik_<timestamp>.json
plus a human-readable summary on stdout.

Usage (inside the API container):
    docker compose exec api python -m scripts.eval.eval_vanetik --cvs 5
    docker compose exec api python -m scripts.eval.eval_vanetik --cvs 30 --concurrency 8

Cost note: 30 CVs × 5 vacancies × 2 LLM calls (role + interest) = 300 calls per full
run, plus 30 extraction calls. At ~$0.01/call this is ~$3 per run on gpt-5.4.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Make the backend modules importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.eval.external.metrics_external import (
    spearman_rho,
    kendall_tau,
    ndcg_at_k,
    pairwise_accuracy_from_ranking,
    top_k_precision,
)
from tests.eval.external.resume_to_profile import docx_to_profile
from tests.eval.external.scoring_runner import score_pair, ScoredPair
from tests.eval.external.vanetik_loader import (
    annotator_ranking_to_relevance,
    ensure_dataset_local,
    load_bundle,
)

logger = logging.getLogger(__name__)
RESULTS_DIR = Path(__file__).resolve().parents[2] / "tests" / "eval" / "external" / "results"


def _our_ranking_from_scores(
    pair_scores: dict[int, ScoredPair],
) -> list[int]:
    """Convert {vac_pos: ScoredPair} into a ranked list of vac_pos best→worst."""
    items = sorted(pair_scores.items(), key=lambda kv: -kv[1].composite)
    return [vac_pos for vac_pos, _ in items]


async def evaluate_one_cv(
    cv_idx: int,
    cv_path: Path,
    vacancies: list[dict],
) -> dict:
    """Score one CV against all 5 vacancies and return a row dict."""
    t0 = time.time()
    try:
        profile = await docx_to_profile(cv_path)
    except Exception as e:
        logger.warning("CV %d: profile extraction failed: %s", cv_idx, e)
        return {"cv_idx": cv_idx, "error": f"extraction failed: {e}"}

    # Score against each vacancy. Vacancy positions are 1-indexed.
    pair_scores: dict[int, ScoredPair] = {}
    for vac_pos, vac in enumerate(vacancies, start=1):
        result = await score_pair(vac, profile)
        pair_scores[vac_pos] = result
        if result.error:
            logger.warning("CV %d vac %d: %s", cv_idx, vac_pos, result.error)

    our_rank = _our_ranking_from_scores(pair_scores)
    elapsed = time.time() - t0
    return {
        "cv_idx": cv_idx,
        "our_ranking": our_rank,
        "scores": {
            str(p): {
                "composite": s.composite,
                "role_fit": s.role_fit_score,
                "interest_fit": s.interest_fit_score,
            }
            for p, s in pair_scores.items()
        },
        "elapsed_sec": round(elapsed, 1),
    }


def _compute_per_cv_metrics(
    cv_row: dict,
    annotator_rankings: dict[int, dict[str, list[int]]],
) -> dict:
    """Add Spearman / nDCG / top-1 hit metrics against each annotator's ranking."""
    cv_idx = cv_row["cv_idx"]
    annotators = annotator_rankings.get(cv_idx, {})
    metrics: dict[str, dict] = {}
    our_rank: list[int] = cv_row.get("our_ranking", [])
    if not our_rank:
        return metrics

    # Map our_rank into a "position-of-vacancy" ordering: vacancy position → our rank position
    # for Spearman/Kendall we compare two rankings of the same items
    our_position = {vac: i + 1 for i, vac in enumerate(our_rank)}

    for label, ann_rank in annotators.items():
        # ann_rank is the same format: best vacancy first
        ann_position = {vac: i + 1 for i, vac in enumerate(ann_rank)}

        # Build aligned arrays of position-numbers for the same set of items (vacancies 1-5)
        items = sorted(set(our_position) | set(ann_position))
        xs = [our_position[i] for i in items]
        ys = [ann_position[i] for i in items]

        rho = spearman_rho(xs, ys)
        tau = kendall_tau(xs, ys)

        # nDCG@5: relevance grades from annotator
        relevance = annotator_ranking_to_relevance(ann_rank)
        ndcg = ndcg_at_k(our_rank, relevance, k=5)

        # Top-1 hit: did our top-1 match annotator's top-1?
        top1_hit = our_rank[0] == ann_rank[0]

        # Top-3 precision
        top3 = top_k_precision(our_rank[:3], ann_rank[:3])

        # Pairwise: for all (vac_i, vac_j) pairs, do we agree on relative order?
        # For 5 vacancies that's 10 pairs, so values are quantized to 0.1 steps.
        pairwise = pairwise_accuracy_from_ranking(our_rank, ann_rank)

        metrics[label] = {
            "spearman": round(rho, 3),
            "kendall": round(tau, 3),
            "ndcg_at_5": round(ndcg, 3),
            "top_1_hit": top1_hit,
            "top_3_precision": round(top3, 3),
            "pairwise_accuracy": round(pairwise, 3),
        }
    return metrics


def _aggregate(per_cv_rows: list[dict]) -> dict:
    """Compute aggregate metrics across all evaluated CVs (per annotator)."""
    agg: dict[str, dict[str, list[float]]] = {}
    top1_hits: dict[str, list[bool]] = {}
    for row in per_cv_rows:
        for ann_label, m in row.get("metrics", {}).items():
            agg.setdefault(ann_label, {
                "spearman": [], "kendall": [], "ndcg_at_5": [],
                "top_3_precision": [], "pairwise_accuracy": [],
            })
            agg[ann_label]["spearman"].append(m["spearman"])
            agg[ann_label]["kendall"].append(m["kendall"])
            agg[ann_label]["ndcg_at_5"].append(m["ndcg_at_5"])
            agg[ann_label]["top_3_precision"].append(m["top_3_precision"])
            agg[ann_label]["pairwise_accuracy"].append(m["pairwise_accuracy"])
            top1_hits.setdefault(ann_label, []).append(m["top_1_hit"])

    def _mean(xs: list[float]) -> float:
        return sum(xs) / max(1, len(xs))

    summary: dict[str, dict] = {}
    for label, vals in agg.items():
        summary[label] = {
            "n_cvs": len(vals["spearman"]),
            "mean_spearman": round(_mean(vals["spearman"]), 3),
            "mean_kendall": round(_mean(vals["kendall"]), 3),
            "mean_ndcg_at_5": round(_mean(vals["ndcg_at_5"]), 3),
            "mean_top_3_precision": round(_mean(vals["top_3_precision"]), 3),
            "mean_pairwise_accuracy": round(_mean(vals["pairwise_accuracy"]), 3),
            "top_1_hit_rate": round(_mean([1.0 if h else 0.0 for h in top1_hits[label]]), 3),
        }
    return summary


def _print_report(report: dict) -> None:
    print()
    print("=" * 70)
    print(f"VANETIK EVAL  —  {report['n_cvs_evaluated']} CVs × 5 vacancies = {report['n_pairs']} pairs")
    print(f"Elapsed: {report['elapsed_sec']:.1f}s   Errors: {report['n_errors']}")
    print("=" * 70)
    for ann_label, m in report["aggregate"].items():
        print(f"\n[{ann_label}]  ({m['n_cvs']} CVs)")
        print(f"  Mean pairwise acc    :  {m['mean_pairwise_accuracy']:.3f}    (chance 0.50, target > 0.65) ← relative ordering")
        print(f"  Mean nDCG@5          :  {m['mean_ndcg_at_5']:.3f}    (target > 0.6) ← top-of-list quality")
        print(f"  Top-1 hit rate       :  {m['top_1_hit_rate']:.3f}    (chance 0.20)")
        print(f"  Mean top-3 precision :  {m['mean_top_3_precision']:.3f}    (target > 0.60)")
        print(f"  Mean Spearman ρ      : {m['mean_spearman']:+.3f}    (full-rank correlation)")
        print(f"  Mean Kendall τ       : {m['mean_kendall']:+.3f}")
    print()
    print("Pass criteria (calibrated to NAACL 2025 findings on LLM-vs-human resume matching):")
    print("  - Pairwise accuracy > 0.65 = 'reliable relative ordering' (the most important metric)")
    print("  - nDCG@5 > 0.6           = 'meaningful top-of-list quality'")
    print("  - Spearman > 0.4         = 'good full-rank agreement' (hardest to hit)")
    print("=" * 70)
    print(f"Full report: {report['report_path']}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Vanetik fit-score evaluation")
    parser.add_argument("--cvs", type=int, default=30, help="Number of CVs to evaluate (max 30)")
    parser.add_argument("--concurrency", type=int, default=4, help="Parallel CV workers")
    parser.add_argument("--cv-ids", type=str, default=None, help="Comma-separated CV ids to evaluate (overrides --cvs)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cache_dir = ensure_dataset_local()
    bundle = load_bundle(cache_dir)
    annotated_cvs = sorted(bundle.annotator_rankings.keys())

    if args.cv_ids:
        cv_ids = [int(x) for x in args.cv_ids.split(",") if x.strip()]
    else:
        cv_ids = annotated_cvs[: args.cvs]

    print(f"Evaluating {len(cv_ids)} CVs against {len(bundle.vacancies)} vacancies "
          f"(concurrency={args.concurrency})")
    t0 = time.time()

    sem = asyncio.Semaphore(args.concurrency)

    async def _bounded(cv_idx):
        async with sem:
            cv_path = bundle.cv_paths.get(cv_idx)
            if cv_path is None:
                return {"cv_idx": cv_idx, "error": "no CV file"}
            row = await evaluate_one_cv(cv_idx, cv_path, bundle.vacancies)
            row["metrics"] = _compute_per_cv_metrics(row, bundle.annotator_rankings)
            return row

    rows = await asyncio.gather(*[_bounded(i) for i in cv_ids], return_exceptions=False)
    elapsed = time.time() - t0

    n_errors = sum(1 for r in rows if r.get("error"))
    n_evaluated = len(rows) - n_errors

    aggregate = _aggregate([r for r in rows if not r.get("error")])

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = RESULTS_DIR / f"vanetik_{timestamp}.json"

    report = {
        "tier": "vanetik",
        "timestamp": timestamp,
        "n_cvs_evaluated": n_evaluated,
        "n_errors": n_errors,
        "n_pairs": n_evaluated * len(bundle.vacancies),
        "elapsed_sec": round(elapsed, 1),
        "aggregate": aggregate,
        "per_cv": rows,
        "report_path": str(report_path),
    }
    report_path.write_text(json.dumps(report, indent=2, default=str))

    _print_report(report)


if __name__ == "__main__":
    asyncio.run(main())
