"""Compare two ``eval_hot_search.py`` JSON reports — typically v1 baseline
vs v2.

Pulls the aggregate metrics + per-persona breakdown out of each report
and prints a side-by-side table. Used as the gate check before flipping
``settings.hot_search_v2`` default-on.

Usage:
    python -m scripts.eval.compare_v1_v2 path/to/v1.json path/to/v2.json
"""

from __future__ import annotations

import argparse
import json
from typing import Any


def _load(path: str) -> dict[str, Any]:
    with open(path) as fh:
        return json.load(fh)


def _fmt(v, decimals: int = 2) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.{decimals}f}"
    return str(v)


def _delta(new: float | None, old: float | None) -> str:
    if new is None or old is None:
        return "—"
    diff = new - old
    sign = "+" if diff >= 0 else ""
    return f"{sign}{diff:.2f}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("v1_path", help="JSON report from v1 baseline run")
    parser.add_argument("v2_path", help="JSON report from v2 run")
    args = parser.parse_args()

    v1 = _load(args.v1_path)
    v2 = _load(args.v2_path)

    # ---------------- Aggregate ----------------
    a1 = v1.get("aggregate", {})
    a2 = v2.get("aggregate", {})
    print("\n## Aggregate")
    print(f"{'metric':<28}  {'v1':>10}  {'v2':>10}  {'Δ':>10}")
    print("-" * 64)
    for key in (
        "coverage_rate",
        "mean_novelty_rate",
        "mean_relevance",
        "mean_elapsed_sec",
        "total_elapsed_sec",
    ):
        v1_val = a1.get(key) if key != "total_elapsed_sec" else v1.get(key)
        v2_val = a2.get(key) if key != "total_elapsed_sec" else v2.get(key)
        decimals = 0 if "sec" in key else 2
        print(
            f"{key:<28}  {_fmt(v1_val, decimals):>10}  {_fmt(v2_val, decimals):>10}  "
            f"{_delta(v2_val, v1_val):>10}"
        )

    # ---------------- Per-persona ----------------
    s1 = {s["persona"]: s for s in v1.get("scenarios", [])}
    s2 = {s["persona"]: s for s in v2.get("scenarios", [])}
    personas = sorted(set(s1.keys()) | set(s2.keys()))

    print("\n## Per-persona — mean_relevance")
    print(f"{'persona':<26} {'v1':>6}  {'v2':>6}  {'Δ':>6}  {'v1 hits':>8} {'v2 hits':>8}")
    print("-" * 70)
    for p in personas:
        r1 = (s1.get(p) or {}).get("mean_relevance")
        r2 = (s2.get(p) or {}).get("mean_relevance")
        n1 = (s1.get(p) or {}).get("imported_job_count", 0)
        n2 = (s2.get(p) or {}).get("imported_job_count", 0)
        flag = ""
        if r1 is not None and r2 is not None and (r1 - r2) > 0.5:
            flag = "  ⚠ regress"
        print(f"{p:<26} {_fmt(r1):>6}  {_fmt(r2):>6}  {_delta(r2, r1):>6}  {n1:>8} {n2:>8}{flag}")

    # ---------------- Gate summary ----------------
    print("\n## Gate Summary")
    rel1 = a1.get("mean_relevance")
    rel2 = a2.get("mean_relevance")
    cov1 = a1.get("coverage_rate")
    cov2 = a2.get("coverage_rate")
    el1 = a1.get("mean_elapsed_sec")
    el2 = a2.get("mean_elapsed_sec")

    def _pass(check: str, ok: bool):
        return f"  {'✓' if ok else '✗'} {check}"

    if rel1 is not None and rel2 is not None:
        print(
            _pass(
                f"mean_relevance v2 ≥ v1 ({_fmt(rel2)} vs {_fmt(rel1)})",
                rel2 >= rel1,
            )
        )
    if cov1 is not None and cov2 is not None:
        # Coverage is a rate (0-1) — both should be similar
        print(
            _pass(
                f"coverage_rate v2 ≥ v1 ({_fmt(cov2)} vs {_fmt(cov1)})",
                cov2 >= cov1 * 0.9,  # 10% tolerance
            )
        )
    if el1 is not None and el2 is not None:
        print(
            _pass(
                f"mean_elapsed_sec v2 ≤ v1 ({_fmt(el2, 0)}s vs {_fmt(el1, 0)}s)",
                el2 <= el1,
            )
        )
    # Per-persona regress check
    any_regress = False
    for p in personas:
        r1 = (s1.get(p) or {}).get("mean_relevance")
        r2 = (s2.get(p) or {}).get("mean_relevance")
        if r1 is not None and r2 is not None and (r1 - r2) > 0.5:
            any_regress = True
            break
    print(
        _pass(
            "no single persona drops >0.5 mean_relevance",
            not any_regress,
        )
    )

    print()


if __name__ == "__main__":
    main()
