"""Tier 2 evaluation: classification + correlation against the cnamuangtoun resume-job-description-fit dataset.

For each example we:
  1. Extract a synthetic profile from the resume_text via the live LLM extractor.
  2. Score that profile against the job_description_text via score_pair().
  3. Bin our composite score into No Fit / Potential Fit / Good Fit.
  4. Compare against the dataset's label.

Output:
  - 3-class accuracy
  - Confusion matrix
  - Spearman / Pearson between our composite score and the ordinal label (1/2/3)
  - JSON report at backend/tests/eval/external/results/huggingface_<timestamp>.json

Usage (inside the API container):
    docker compose exec api python -m scripts.eval.eval_huggingface --n 30
    docker compose exec api python -m scripts.eval.eval_huggingface --n 200 --concurrency 8
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

# Make backend modules importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.eval.external.huggingface_loader import (
    HFExample,
    fetch_examples,
    example_to_job_dict,
    LABEL_TO_ORDINAL,
)
from tests.eval.external.metrics_external import (
    classification_accuracy,
    classify_score,
    confusion_matrix,
    format_confusion_matrix,
    mean_score_by_class,
    pairwise_accuracy_by_class,
    pearson_r,
    spearman_rho,
)
from tests.eval.external.resume_to_profile import text_blob_to_profile
from tests.eval.external.scoring_runner import score_pair

logger = logging.getLogger(__name__)
RESULTS_DIR = Path(__file__).resolve().parents[2] / "tests" / "eval" / "external" / "results"


async def evaluate_one(ex: HFExample) -> dict:
    """Score one HF example end-to-end."""
    t0 = time.time()
    try:
        profile = await text_blob_to_profile(ex.resume_text)
    except Exception as e:
        return {"row_idx": ex.row_idx, "label": ex.label, "error": f"extraction: {e}"}

    job_dict = example_to_job_dict(ex)
    result = await score_pair(job_dict, profile)
    elapsed = time.time() - t0

    if result.error:
        return {"row_idx": ex.row_idx, "label": ex.label, "error": result.error}

    composite = result.composite  # 0-100
    predicted_label = classify_score(composite)
    return {
        "row_idx": ex.row_idx,
        "label": ex.label,
        "label_ordinal": ex.label_ordinal,
        "composite": round(composite, 1),
        "role_fit": result.role_fit_score,
        "interest_fit": result.interest_fit_score,
        "predicted_label": predicted_label,
        "correct": predicted_label == ex.label,
        "elapsed_sec": round(elapsed, 1),
    }


def _aggregate(rows: list[dict]) -> dict:
    """Compute aggregate metrics from per-example rows."""
    valid = [r for r in rows if not r.get("error")]
    if not valid:
        return {"n": 0, "n_errors": len(rows)}

    actual = [r["label"] for r in valid]
    predicted = [r["predicted_label"] for r in valid]
    actual_ord = [r["label_ordinal"] for r in valid]
    composites = [r["composite"] for r in valid]

    accuracy = classification_accuracy(predicted, actual)
    cm = confusion_matrix(predicted, actual, labels=list(LABEL_TO_ORDINAL.keys()))
    rho = spearman_rho(composites, actual_ord)
    r = pearson_r(composites, actual_ord)

    # Per-class accuracy
    per_class: dict[str, dict] = {}
    for label in LABEL_TO_ORDINAL:
        in_class = [(p, a) for p, a in zip(predicted, actual) if a == label]
        if in_class:
            per_class[label] = {
                "n": len(in_class),
                "accuracy": round(sum(p == a for p, a in in_class) / len(in_class), 3),
            }
        else:
            per_class[label] = {"n": 0, "accuracy": None}

    # Pairwise ordering accuracy + mean score per class — these are the metrics
    # that matter when relative ranking is more important than absolute calibration.
    means = mean_score_by_class([(r["composite"], r["label"]) for r in valid])
    scores_by_class: dict[str, list[float]] = {}
    for label in LABEL_TO_ORDINAL:
        scores_by_class[label] = [r["composite"] for r in valid if r["label"] == label]
    pairwise = pairwise_accuracy_by_class(
        scores_by_class, list(LABEL_TO_ORDINAL.keys())
    )

    return {
        "n": len(valid),
        "n_errors": len(rows) - len(valid),
        "accuracy": round(accuracy, 3),
        "spearman_vs_label_ordinal": round(rho, 3),
        "pearson_vs_label_ordinal": round(r, 3),
        "confusion_matrix": cm,
        "per_class_accuracy": per_class,
        "score_distribution": {
            "min": round(min(composites), 1),
            "max": round(max(composites), 1),
            "mean": round(sum(composites) / len(composites), 1),
        },
        "mean_score_by_class": means,
        "pairwise_accuracy": {
            hi: {lo: round(acc, 3) for lo, acc in lows.items()}
            for hi, lows in pairwise.items()
        },
    }


def _print_report(report: dict) -> None:
    agg = report["aggregate"]
    print()
    print("=" * 70)
    print(f"HUGGING FACE EVAL  —  {agg['n']} examples")
    print(f"Elapsed: {report['elapsed_sec']:.1f}s   Errors: {agg['n_errors']}")
    print("=" * 70)
    print(f"\n3-class accuracy        : {agg['accuracy']:.3f}    (chance ≈ 0.33)")
    print(f"Spearman ρ vs label ord : {agg['spearman_vs_label_ordinal']:+.3f}    (target > 0.3)")
    print(f"Pearson r vs label ord  : {agg['pearson_vs_label_ordinal']:+.3f}")
    print(f"\nScore distribution: min={agg['score_distribution']['min']}  "
          f"mean={agg['score_distribution']['mean']}  max={agg['score_distribution']['max']}")

    print("\nMean composite score per class (relative ordering):")
    for label in LABEL_TO_ORDINAL:
        m = agg["mean_score_by_class"].get(label, {})
        if m:
            print(f"  {label:18} n={m['n']:3}  mean={m['mean']:5.1f}  "
                  f"min={m['min']:5.1f}  max={m['max']:5.1f}")

    print("\nPairwise ordering accuracy (the relative-ranking metric):")
    print("  P(score(higher class) > score(lower class)) — random = 0.500")
    pa = agg.get("pairwise_accuracy", {})
    pairs = [
        ("Good Fit", "No Fit"),
        ("Good Fit", "Potential Fit"),
        ("Potential Fit", "No Fit"),
    ]
    for hi, lo in pairs:
        acc = pa.get(hi, {}).get(lo)
        if acc is not None:
            bar = "█" * int(acc * 30)
            target = " ✓" if acc >= 0.65 else (" ~" if acc >= 0.5 else " ✗")
            print(f"  {hi:14} > {lo:14}: {acc:.3f} {target} {bar}")

    print("\nPer-class accuracy:")
    for label, m in agg["per_class_accuracy"].items():
        if m["n"] > 0:
            print(f"  {label:18} ({m['n']:3} examples): {m['accuracy']:.3f}")
        else:
            print(f"  {label:18}   no examples")
    print("\nConfusion matrix:")
    print(format_confusion_matrix(agg["confusion_matrix"]))
    print()
    print("Pass criteria:")
    print("  - 3-class accuracy > 0.50  (chance is 0.33)")
    print("  - Spearman ρ > 0.30")
    print("  - Pairwise P(Good > No Fit) > 0.65  (the most important relative metric)")
    print("=" * 70)
    print(f"Full report: {report['report_path']}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Hugging Face fit-score evaluation (Tier 2)")
    parser.add_argument("--n", type=int, default=30, help="Number of examples to score")
    parser.add_argument("--concurrency", type=int, default=4, help="Parallel workers")
    parser.add_argument("--no-stratified", action="store_true", help="Disable stratified sampling")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    print(f"Fetching {args.n} examples (stratified={not args.no_stratified})...")
    examples = fetch_examples(n=args.n, stratified=not args.no_stratified)
    print(f"Got {len(examples)} examples. Scoring with concurrency={args.concurrency}...")

    t0 = time.time()
    sem = asyncio.Semaphore(args.concurrency)

    async def _bounded(ex):
        async with sem:
            return await evaluate_one(ex)

    rows = await asyncio.gather(*[_bounded(ex) for ex in examples])
    elapsed = time.time() - t0

    aggregate = _aggregate(rows)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = RESULTS_DIR / f"huggingface_{timestamp}.json"

    report = {
        "tier": "huggingface",
        "timestamp": timestamp,
        "n_requested": args.n,
        "elapsed_sec": round(elapsed, 1),
        "aggregate": aggregate,
        "per_example": rows,
        "report_path": str(report_path),
    }
    report_path.write_text(json.dumps(report, indent=2, default=str))

    _print_report(report)


if __name__ == "__main__":
    asyncio.run(main())
