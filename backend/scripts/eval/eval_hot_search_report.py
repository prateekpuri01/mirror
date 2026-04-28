"""Generate a human-readable markdown report from a hot-search eval JSON.

Reads the JSON produced by ``eval_hot_search.py`` (or the latest one in
the results dir) and emits a markdown summary suitable for committing to
the repo as ``EVAL.md`` or linking from the README. The signal: at a
glance, how well does the pipeline currently generate leads across the
test personas?

Usage:
    # Report on the latest JSON in the results dir
    python -m scripts.eval.eval_hot_search_report

    # Report on a specific JSON
    python -m scripts.eval.eval_hot_search_report path/to/hot_search_*.json

    # Write to a file
    python -m scripts.eval.eval_hot_search_report --out EVAL.md

The markdown includes (a) per-persona table, (b) aggregate row, (c) best
and worst LLM-judged finds, (d) source-breakdown bar, (e) wall-time and
estimated cost.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make the backend modules importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

RESULTS_DIR = Path(__file__).resolve().parents[2] / "tests" / "eval" / "external" / "results"


def _latest_json() -> Path:
    candidates = sorted(RESULTS_DIR.glob("hot_search_*.json"))
    if not candidates:
        raise SystemExit(
            f"No hot_search_*.json found under {RESULTS_DIR}. Run the eval first:\n"
            f"  docker compose exec api python -m scripts.eval.eval_hot_search"
        )
    return candidates[-1]


def _fmt_pct(v: float | None) -> str:
    return "—" if v is None else f"{v * 100:.0f}%"


def _fmt_float(v: float | None, decimals: int = 1) -> str:
    return "—" if v is None else f"{v:.{decimals}f}"


def _cov_emoji(covered: bool) -> str:
    return "✅" if covered else "❌"


def _estimate_cost_usd(report: dict) -> float:
    """Best-effort cost estimate. The eval harness doesn't currently track
    token counts directly, so we estimate from observable totals:
    - One LLM judge call per judged job (~500 tokens combined in/out)
    - Scenario pipelines burn LLM calls on query-gen / picker / verifier /
      extraction. Empirically ~80 calls × ~600 tokens per scenario.

    These are gpt-4o-mini-class prices: ~$0.15/M input + $0.60/M output.
    Treat the result as a ballpark, not a bill.
    """
    n_scenarios = report.get("aggregate", {}).get("n_scenarios", 0)
    n_judged = report.get("aggregate", {}).get("total_judged_jobs", 0)
    # Pipeline tokens (rough): ~80 LLM calls per scenario × ~600 tokens
    pipeline_tokens = n_scenarios * 80 * 600
    judge_tokens = n_judged * 500
    total_tokens = pipeline_tokens + judge_tokens
    # Blended rate ~$0.40 / M tokens for gpt-4o-mini class
    return total_tokens / 1_000_000 * 0.40


def generate_markdown(report: dict) -> str:
    """Render a hot-search eval report as markdown."""
    agg = report.get("aggregate", {})
    scenarios = report.get("scenarios", [])
    examples = report.get("examples", {})
    timestamp = report.get("timestamp", "")
    # Format timestamp as YYYY-MM-DD if it's the harness's compact form
    date = timestamp[:8]
    if len(date) == 8 and date.isdigit():
        date = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    else:
        date = timestamp

    lines: list[str] = []
    lines.append(f"# Hot Search Eval — {date}")
    lines.append("")
    lines.append(
        "End-to-end evaluation of the AI-powered job-discovery pipeline. Each "
        "persona below is a realistic user query (guidance + location + min "
        "salary). For every job the pipeline returns, an LLM judge scores it "
        "1–5 against the search criteria. Higher relevance = the pipeline is "
        "actually finding what was asked for; higher novelty = it's surfacing "
        "jobs not already in the database."
    )
    lines.append("")

    # ---- Per-scenario table
    lines.append("## Per-persona results")
    lines.append("")
    lines.append(
        "| Persona | Coverage | Novelty | Mean relevance (1–5) | Hits (ATS / lead / tracked) | Imported (novel) | Wall time |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    for s in scenarios:
        cov = _cov_emoji(s.get("coverage", False))
        novelty = _fmt_pct(s.get("novelty_rate"))
        relevance = _fmt_float(s.get("mean_relevance"), 2)
        hits = (
            f"{s.get('hits_ats', 0)} / {s.get('hits_lead', 0)} / "
            f"{s.get('hits_tracked', 0)}"
        )
        imp = (
            f"{s.get('imported_job_count', 0)} ({s.get('novel_job_count', 0)})"
        )
        elapsed = f"{s.get('elapsed_sec', 0):.0f}s"
        lines.append(
            f"| `{s.get('persona', '?')}` | {cov} | {novelty} | {relevance} "
            f"| {hits} | {imp} | {elapsed} |"
        )
    lines.append("")

    # ---- Aggregate row
    cost = _estimate_cost_usd(report)
    lines.append("## Aggregate")
    lines.append("")
    lines.append(
        f"- **Coverage:** {agg.get('coverage_count', '—')} personas "
        f"({_fmt_pct(agg.get('coverage_rate'))}) returned at least one hit"
    )
    lines.append(
        f"- **Mean relevance:** {_fmt_float(agg.get('mean_relevance'), 2)} / 5  "
        f"_(LLM judge scoring of returned jobs against the search query)_"
    )
    lines.append(
        f"- **Mean novelty:** {_fmt_pct(agg.get('mean_novelty_rate'))}  "
        f"_(jobs surfaced that weren't already in the DB snapshot)_"
    )
    lines.append(
        f"- **Total imported:** {agg.get('total_imported_jobs', 0)} jobs across "
        f"{agg.get('n_scenarios', 0)} personas "
        f"({agg.get('total_novel_jobs', 0)} novel)"
    )
    lines.append(
        f"- **Mean wall time:** {_fmt_float(agg.get('mean_elapsed_sec'), 0)}s per persona"
    )
    lines.append(f"- **Estimated cost:** ~${cost:.2f} (rough; see source for assumptions)")
    lines.append("")

    # ---- Source breakdown
    src_breakdown = agg.get("source_breakdown", {})
    if src_breakdown:
        lines.append("## Source breakdown")
        lines.append("")
        lines.append(
            "Where the LLM judge's high-relevance hits actually came from. The "
            "slug-harvester + aggregator layer (`hn_who_is_hiring`, `remotive`, "
            "`themuse`, `arbeitnow`) feeds candidates into the same evaluation "
            "pipeline as web-search-discovered companies, so all sources show up here."
        )
        lines.append("")
        lines.append("| Source | Hits |")
        lines.append("|---|---|")
        for src, n in sorted(src_breakdown.items(), key=lambda kv: -kv[1]):
            lines.append(f"| `{src}` | {n} |")
        lines.append("")

    # ---- Funnel diagnostics for 0-hit scenarios. When a persona gets no
    # hits, the why is more useful than the what. Surface the orchestrator's
    # candidate-funnel counters + top skip reasons so a reader can see at a
    # glance whether candidates died in dedup, eval, or post-filter.
    zero_hit = [s for s in scenarios if not s.get("coverage", False)]
    funnel_scenarios = [s for s in zero_hit if s.get("funnel")]
    if funnel_scenarios:
        lines.append("## Where candidates dropped (0-hit personas)")
        lines.append("")
        lines.append(
            "When a persona returned no hits, this is the orchestrator's "
            "candidate funnel: how many candidates entered, where they were "
            "dropped, and (if any) the most-cited skip reasons."
        )
        lines.append("")
        for s in funnel_scenarios:
            lines.append(f"### `{s.get('persona', '?')}`")
            lines.append("")
            funnel = s.get("funnel", {})
            for key in [
                "aggregator_entries", "seed_candidates", "candidates_seen",
                "already_checked", "dedup_dropped", "tracked_no_match",
                "direct_cap_reached", "direct_hit", "direct_miss",
                "full_hit", "full_miss", "final_hits",
            ]:
                if key in funnel:
                    lines.append(f"- `{key}`: {funnel[key]}")
            top_reasons = s.get("top_skip_reasons", [])
            if top_reasons:
                lines.append("")
                lines.append("Top skip reasons:")
                for reason, count in top_reasons[:5]:
                    lines.append(f"  - `{count}` × {reason}")
            lines.append("")

    # ---- Best finds
    best = examples.get("best_finds", [])
    if best:
        lines.append("## Top finds (highest LLM-judged relevance)")
        lines.append("")
        for j in best[:5]:
            company = j.get("company", "?")
            title = j.get("title", "?")
            relevance = j.get("relevance", 0)
            persona = j.get("persona", "?")
            url = j.get("url", "")
            link = f"[{title}]({url})" if url else title
            lines.append(
                f"- **{relevance}/5** — {company} / {link}  "
                f"_(persona: `{persona}`)_"
            )
        lines.append("")

    # ---- Worst finds (kept brief; useful for honest signal)
    worst = examples.get("worst_finds", [])
    if worst:
        lines.append("## Weakest finds (where the pipeline misfired)")
        lines.append("")
        for j in worst[:3]:
            company = j.get("company", "?")
            title = j.get("title", "?")
            relevance = j.get("relevance", 0)
            persona = j.get("persona", "?")
            lines.append(
                f"- **{relevance}/5** — {company} / {title}  "
                f"_(persona: `{persona}`)_"
            )
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        f"_Generated from `{report.get('eval', 'hot_search')}` JSON at "
        f"timestamp `{timestamp}`. To regenerate: `./backend/scripts/eval/run.sh`._"
    )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate markdown report from hot-search eval JSON.")
    parser.add_argument(
        "json_path", nargs="?", default=None,
        help="Path to a hot_search_*.json. Defaults to the latest in the results dir.",
    )
    parser.add_argument(
        "--out", default=None,
        help="If provided, write the markdown to this path instead of stdout.",
    )
    args = parser.parse_args()

    json_path = Path(args.json_path) if args.json_path else _latest_json()
    if not json_path.exists():
        print(f"ERROR: {json_path} not found", file=sys.stderr)
        return 1

    report = json.loads(json_path.read_text())
    md = generate_markdown(report)

    if args.out:
        out_path = Path(args.out)
        out_path.write_text(md)
        print(f"Wrote {len(md)} chars to {out_path}")
    else:
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
