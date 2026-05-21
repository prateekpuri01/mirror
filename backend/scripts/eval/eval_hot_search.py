"""Evaluation harness for the hot_company_search feature.

Answers the product question: *is this tool actually useful for uncovering jobs
that are NOT already in our job board, across realistic user queries?*

For each persona scenario, we:
  1. Call run_hot_company_search() as an async generator
  2. Collect all yielded SearchEvent objects (hit/skip/candidate/status/done)
  3. For each imported job in a "hit" event, flag novelty vs. a DB snapshot
     taken BEFORE the scenario ran, and score relevance via an LLM judge
  4. Report per-scenario + aggregate metrics as JSON + stdout table

Output: backend/tests/eval/external/results/hot_search_<timestamp>.json

Usage (inside the API container):
    docker compose exec api python -m scripts.eval.eval_hot_search
    docker compose exec api python -m scripts.eval.eval_hot_search --personas ml_engineer_sf
    docker compose exec api python -m scripts.eval.eval_hot_search --max-hits 4 --personas ml_engineer_sf,fintech_ds

Cost note: ~7 scenarios * (full search pipeline + 1 LLM judge per imported job).
At ~6 imports/scenario that's ~42 judge calls + pipeline calls — roughly $1/run.

This script MUTATES the database by importing direct-URL jobs during the
search (that's how the tool normally works). Novelty is measured against a
snapshot taken at the start of the run, so repeated runs are still meaningful.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Make the backend modules importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import select

from app.ai.client import EXTRACTION_MODEL, get_openai_client
from app.config import settings
from app.database import async_session
from app.models.jobs import Job
from app.services import app_settings_service
from app.services.hot_company_search import run_hot_company_search

logger = logging.getLogger(__name__)
RESULTS_DIR = Path(__file__).resolve().parents[2] / "tests" / "eval" / "external" / "results"


async def _hydrate_runtime_settings() -> None:
    """Standalone-script bootstrap: the running app loads provider + API
    keys from the ``app_settings`` DB table on startup
    (``app.main:lifespan``). When this script runs as ``python -m`` we
    bypass the FastAPI lifespan, so ``settings.openai_api_key`` is empty
    and every LLM call inside the pipeline raises. Hydrate explicitly
    before any pipeline code runs.

    Without this hook the eval silently produces zero hits (every LLM
    call inside ``run_hot_company_search`` fails with "OPENAI_API_KEY is
    not set"), which looks like a pipeline regression but is just the
    eval harness missing config.
    """
    async with async_session() as session:
        await app_settings_service.load_into_settings(session, settings)
    if not settings.openai_api_key:
        raise RuntimeError(
            "Runtime settings hydrated but openai_api_key is still empty. "
            "Configure via /setup or env var before running this eval."
        )


# ---------------------------------------------------------------------------
# Scenario definitions
# ---------------------------------------------------------------------------


@dataclass
class Scenario:
    persona: str
    guidance: str
    locations: list[str] | None = None
    min_salary: int | None = None
    rationale: str = ""


SCENARIOS: list[Scenario] = [
    Scenario(
        persona="ml_engineer_loose",
        guidance="machine learning engineer",
        locations=None,
        min_salary=None,
        rationale=(
            "Baseline coverage check — loose query with no filters. The "
            "pipeline should produce hits here. If THIS scenario fails, "
            "something deeper is wrong than the strict-policy gates."
        ),
    ),
    Scenario(
        persona="ml_engineer_sf",
        guidance="senior machine learning engineer at AI startup",
        locations=["San Francisco", "Remote"],
        min_salary=220_000,
        rationale="Core tech persona — should be strong",
    ),
    Scenario(
        persona="fintech_ds",
        guidance="data scientist at fintech or crypto",
        locations=["New York", "Remote"],
        min_salary=180_000,
        rationale="Industry-specific, ATS-heavy space",
    ),
    Scenario(
        persona="ai_policy",
        guidance="AI policy or governance researcher",
        locations=["Washington DC", "Remote"],
        min_salary=None,
        rationale="Non-traditional tech — tests niche coverage",
    ),
    Scenario(
        persona="ai_policy_open",
        guidance="AI policy or governance researcher",
        locations=None,
        min_salary=None,
        rationale=(
            "Same niche query as ai_policy but with no location filter. "
            "Isolates whether the 0-hit result is driven by location "
            "strictness vs. a true shortage of policy roles in the pool."
        ),
    ),
    Scenario(
        persona="healthcare_ml",
        guidance="machine learning engineer in healthcare or biotech",
        locations=["Boston", "Remote"],
        min_salary=160_000,
        rationale="Industry-specific, mix of ATS + enterprise",
    ),
    Scenario(
        persona="security_eng",
        guidance="security engineer at cybersecurity company",
        locations=["Remote"],
        min_salary=170_000,
        rationale="Different domain, tests Perplexity strength",
    ),
    Scenario(
        persona="technical_pm",
        guidance="technical product manager AI or ML product",
        locations=["New York"],
        min_salary=200_000,
        rationale="Non-engineering — tests role-type breadth",
    ),
    Scenario(
        persona="research_scientist",
        guidance="research scientist computer vision or NLP",
        locations=None,
        min_salary=180_000,
        rationale="Academic-leaning, tests big-tech discovery",
    ),
]


# ---------------------------------------------------------------------------
# Core eval primitives
# ---------------------------------------------------------------------------


async def _snapshot_existing_job_urls() -> set[str]:
    """Return the set of all job URLs currently in the DB.

    Used for novelty detection — anything imported during the eval whose
    URL isn't in this set is considered a new find.
    """
    async with async_session() as session:
        result = await session.execute(select(Job.url))
        return {row[0] for row in result.all() if row[0]}


async def _judge_relevance(
    guidance: str,
    job: dict,
    company_name: str,
) -> int:
    """Ask an LLM to score how well this job matches the user's query.

    Returns 1-5, or 0 on parse failure. Uses a fresh LLM call that doesn't
    see the pipeline's own relevance scoring — gives us an independent
    signal on whether the returned jobs actually match user intent.
    """
    title = (job.get("title") or "").strip()
    location = (job.get("location") or "").strip()
    description = (job.get("description") or "").strip()[:500]

    user_msg = (
        f'Query: "{guidance}"\n'
        f'Job: {title} at {company_name}'
        + (f' in {location}' if location else '')
        + '\n'
        + (f'Description: {description}\n' if description else '')
        + '\n'
        + 'Score 1-5:\n'
        + '  1 = unrelated (e.g. "Office Manager" for "ML engineer")\n'
        + '  2 = tangentially related\n'
        + '  3 = adjacent domain but wrong focus\n'
        + '  4 = good match (same role family)\n'
        + '  5 = excellent match (exactly what was asked)\n'
        + 'Output only the number.'
    )

    try:
        client = get_openai_client()
        resp = await client.chat.completions.create(
            model=EXTRACTION_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Score how well a job matches a user's search query. "
                        "Output only a single digit 1-5."
                    ),
                },
                {"role": "user", "content": user_msg},
            ],
            # Generous budget — GPT-5 models consume tokens on hidden
            # reasoning before emitting the digit (same issue we fixed
            # in _pick_best_job_for_guidance)
            max_completion_tokens=500,
        )
        answer = (resp.choices[0].message.content or "").strip()
        match = re.search(r'[1-5]', answer)
        if match:
            return int(match.group())
        logger.debug("Judge returned un-parseable response: %r", answer[:60])
        return 0
    except Exception as e:
        logger.warning("LLM judge call failed: %s", e)
        return 0


@dataclass
class ScenarioResult:
    persona: str
    guidance: str
    locations: list[str] | None
    min_salary: int | None
    # Event counts by kind
    hits_total: int = 0
    hits_ats: int = 0
    hits_lead: int = 0
    hits_tracked: int = 0
    hits_no_match: int = 0  # scraped but picker rejected — "considered, ignored"
    skips: int = 0
    errors: int = 0
    no_match_companies: list[dict] = field(default_factory=list)  # {name, reason}
    # Job-level stats
    imported_jobs: list[dict] = field(default_factory=list)
    novel_job_count: int = 0
    judged_jobs: list[dict] = field(default_factory=list)  # {title, url, company, relevance, novel}
    # Timing
    elapsed_sec: float = 0.0
    t_first_hit_sec: float | None = None
    # Sources
    source_breakdown: dict[str, int] = field(default_factory=dict)
    # Funnel — captured from the orchestrator's `done` event payload. Lets
    # us see exactly where 0-hit scenarios lost their candidates (dedup
    # dropped, direct-cap reached, full-miss, etc.) without grepping logs.
    funnel: dict[str, int] = field(default_factory=dict)
    top_skip_reasons: list = field(default_factory=list)
    # Failure info
    error_msg: str | None = None

    @property
    def coverage(self) -> bool:
        return len(self.imported_jobs) > 0

    @property
    def novelty_rate(self) -> float | None:
        if not self.imported_jobs:
            return None
        return self.novel_job_count / len(self.imported_jobs)

    @property
    def mean_relevance(self) -> float | None:
        scores = [j["relevance"] for j in self.judged_jobs if j["relevance"] > 0]
        if not scores:
            return None
        return sum(scores) / len(scores)


async def run_scenario(
    scenario: Scenario,
    existing_urls: set[str],
    max_hits: int,
) -> ScenarioResult:
    """Run one scenario end-to-end and return metrics.

    Consumes the full SearchEvent stream; scores each imported job via an
    LLM judge after the search completes (so scoring is isolated from the
    search pipeline's own picker).
    """
    result = ScenarioResult(
        persona=scenario.persona,
        guidance=scenario.guidance,
        locations=list(scenario.locations) if scenario.locations else None,
        min_salary=scenario.min_salary,
    )

    t0 = time.time()
    try:
        async for event in run_hot_company_search(
            sources=["web", "greenhouse", "lever", "ashby"],
            guidance=scenario.guidance,
            max_hits=max_hits,
            locations=list(scenario.locations) if scenario.locations else None,
            min_salary=scenario.min_salary,
        ):
            if event.event == "hit":
                result.hits_total += 1
                hit_data = event.data or {}
                kind = hit_data.get("kind", "ats")
                source = hit_data.get("source", "unknown")
                result.source_breakdown[source] = result.source_breakdown.get(source, 0) + 1

                if result.t_first_hit_sec is None:
                    result.t_first_hit_sec = round(time.time() - t0, 2)

                if kind == "ats":
                    result.hits_ats += 1
                elif kind == "lead":
                    result.hits_lead += 1
                elif kind == "tracked":
                    result.hits_tracked += 1
                elif kind == "no_match":
                    result.hits_no_match += 1
                    result.no_match_companies.append({
                        "name": hit_data.get("name", "?"),
                        "reason": hit_data.get("match_reason", ""),
                    })
                    # No top_jobs to judge for a no_match; skip the import loop
                    continue

                # Collect importable jobs for later judging
                company = hit_data.get("name", "Unknown")
                for j in hit_data.get("top_jobs", []):
                    if j.get("url") and j.get("title"):
                        result.imported_jobs.append({**j, "_company": company, "_kind": kind})

            elif event.event == "skip":
                result.skips += 1
            elif event.event == "error":
                result.errors += 1
                # Don't abort — one bad query shouldn't kill the scenario
            elif event.event == "done":
                # Capture funnel + top skip reasons from the orchestrator
                # so we can diagnose 0-hit scenarios at a glance.
                result.funnel = dict(event.data.get("funnel", {}))
                result.top_skip_reasons = list(event.data.get("top_skip_reasons", []))
                break
    except Exception as e:
        result.error_msg = f"{type(e).__name__}: {e}"
        logger.exception("Scenario %s crashed", scenario.persona)

    result.elapsed_sec = round(time.time() - t0, 2)

    # Novelty classification
    for j in result.imported_jobs:
        if j.get("url") not in existing_urls:
            result.novel_job_count += 1

    # LLM relevance judging — cap at 10 per scenario to bound cost
    jobs_to_judge = result.imported_jobs[:10]
    if jobs_to_judge:
        scores = await asyncio.gather(
            *[
                _judge_relevance(scenario.guidance, j, j.get("_company", "Unknown"))
                for j in jobs_to_judge
            ],
            return_exceptions=False,
        )
        for j, score in zip(jobs_to_judge, scores):
            result.judged_jobs.append({
                "title": j.get("title"),
                "url": j.get("url"),
                "company": j.get("_company"),
                "location": j.get("location"),
                "kind": j.get("_kind"),
                "novel": j.get("url") not in existing_urls,
                "relevance": score,
            })

    return result


# ---------------------------------------------------------------------------
# Aggregation + reporting
# ---------------------------------------------------------------------------


def _aggregate(results: list[ScenarioResult]) -> dict:
    """Summary statistics across all scenarios."""
    if not results:
        return {}

    covered = sum(1 for r in results if r.coverage)
    novelties = [r.novelty_rate for r in results if r.novelty_rate is not None]
    relevances = [r.mean_relevance for r in results if r.mean_relevance is not None]
    all_judged = [j for r in results for j in r.judged_jobs]

    combined_sources: dict[str, int] = {}
    for r in results:
        for src, cnt in r.source_breakdown.items():
            combined_sources[src] = combined_sources.get(src, 0) + cnt

    return {
        "n_scenarios": len(results),
        "coverage_rate": round(covered / len(results), 3),
        "coverage_count": f"{covered}/{len(results)}",
        "mean_novelty_rate": round(sum(novelties) / len(novelties), 3) if novelties else None,
        "mean_relevance": round(sum(relevances) / len(relevances), 2) if relevances else None,
        "total_imported_jobs": sum(len(r.imported_jobs) for r in results),
        "total_novel_jobs": sum(r.novel_job_count for r in results),
        "total_judged_jobs": len(all_judged),
        "mean_elapsed_sec": round(sum(r.elapsed_sec for r in results) / len(results), 1),
        "source_breakdown": combined_sources,
    }


def _best_and_worst(results: list[ScenarioResult], n: int = 5) -> dict:
    """Pull out the top-N best-matching and worst-matching judged jobs
    for qualitative review."""
    all_judged = []
    for r in results:
        for j in r.judged_jobs:
            all_judged.append({**j, "persona": r.persona, "guidance": r.guidance})

    # Best: highest relevance, tiebreak on novelty
    best = sorted(all_judged, key=lambda j: (-j["relevance"], not j["novel"]))[:n]
    # Worst: lowest relevance (ignoring 0 = judge failed)
    rated = [j for j in all_judged if j["relevance"] > 0]
    worst = sorted(rated, key=lambda j: j["relevance"])[:n]

    return {"best_finds": best, "worst_finds": worst}


def _fmt_pct(v: float | None) -> str:
    return "—" if v is None else f"{v*100:.0f}%"


def _fmt_float(v: float | None, decimals: int = 1) -> str:
    return "—" if v is None else f"{v:.{decimals}f}"


def _print_stdout_summary(results: list[ScenarioResult], aggregate: dict, examples: dict) -> None:
    """At-a-glance readout — a non-engineer should get the signal in 60s."""
    print("\n" + "=" * 95)
    print("HOT SEARCH EVALUATION — per-scenario results")
    print("=" * 95)
    print(
        f"{'Persona':<22} {'Cov':<4} {'Novel':<7} {'Relev':<6} "
        f"{'t_1st':<7} {'Jobs':<5} {'Top source':<25} {'Time':<6}"
    )
    print("-" * 95)
    for r in results:
        cov = "✓" if r.coverage else "✗"
        top_src = (
            max(r.source_breakdown.items(), key=lambda kv: kv[1])[0]
            if r.source_breakdown else "—"
        )
        top_src_count = r.source_breakdown.get(top_src, 0) if top_src != "—" else 0
        top_src_display = f"{top_src} ({top_src_count})" if top_src != "—" else "—"

        print(
            f"{r.persona:<22} {cov:<4} "
            f"{_fmt_pct(r.novelty_rate):<7} "
            f"{_fmt_float(r.mean_relevance, 2):<6} "
            f"{_fmt_float(r.t_first_hit_sec, 1):<6}s "
            f"{len(r.imported_jobs):<5} "
            f"{top_src_display:<25} "
            f"{r.elapsed_sec:>5.0f}s"
        )

    print("-" * 95)
    print(
        f"{'AGGREGATE':<22} "
        f"{aggregate['coverage_count']:<4} "
        f"{_fmt_pct(aggregate.get('mean_novelty_rate')):<7} "
        f"{_fmt_float(aggregate.get('mean_relevance'), 2):<6} "
        f"{'—':<7} "
        f"{aggregate['total_imported_jobs']:<5} "
    )
    print()

    print("Source breakdown across all scenarios:")
    for src, cnt in sorted(
        aggregate.get("source_breakdown", {}).items(), key=lambda kv: -kv[1]
    ):
        print(f"  {src:<25} {cnt}")

    print("\n" + "=" * 95)
    print("TOP-5 BEST FINDS (high relevance, novel preferred)")
    print("=" * 95)
    for j in examples.get("best_finds", []):
        novel_tag = "NEW" if j["novel"] else "DB"
        print(
            f"  [{j['relevance']}] {novel_tag:<3} "
            f"{(j.get('title') or '?')[:60]:<60} @ {(j.get('company') or '?')[:20]}"
        )
        print(f"          persona={j['persona']}  url={j.get('url', '?')[:100]}")

    print("\n" + "=" * 95)
    print("TOP-5 WORST FINDS (low relevance — diagnose picker/scoring)")
    print("=" * 95)
    for j in examples.get("worst_finds", []):
        print(
            f"  [{j['relevance']}] "
            f"{(j.get('title') or '?')[:60]:<60} @ {(j.get('company') or '?')[:20]}"
        )
        print(f"          persona={j['persona']}  url={j.get('url', '?')[:100]}")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--personas",
        type=str,
        default=None,
        help="Comma-separated persona names to run (default: all). "
             "Names: " + ", ".join(s.persona for s in SCENARIOS),
    )
    parser.add_argument(
        "--max-hits",
        type=int,
        default=8,
        help="Max hits per scenario (default: 8)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show INFO-level logs from the search pipeline",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # Always show the hot-search progress log even without --verbose
    logging.getLogger("app.services.hot_company_search").setLevel(logging.INFO)

    # Filter scenarios
    scenarios = SCENARIOS
    if args.personas:
        wanted = {p.strip().lower() for p in args.personas.split(",") if p.strip()}
        scenarios = [s for s in SCENARIOS if s.persona.lower() in wanted]
        if not scenarios:
            print(f"No personas matched --personas={args.personas}", file=sys.stderr)
            print("Available:", ", ".join(s.persona for s in SCENARIOS), file=sys.stderr)
            return

    # Hydrate runtime settings (provider + API keys) from the DB —
    # the running app does this on startup but standalone scripts bypass
    # that path. See _hydrate_runtime_settings for the full reason.
    print("Hydrating runtime settings from app_settings table...")
    await _hydrate_runtime_settings()
    print(f"  provider={settings.llm_provider}, openai_key=set")

    # Snapshot DB URLs for novelty check
    print(f"Taking DB snapshot for novelty detection...")
    existing_urls = await _snapshot_existing_job_urls()
    print(f"  {len(existing_urls)} existing job URLs in DB")

    print(f"\nRunning {len(scenarios)} scenario(s), max_hits={args.max_hits}")
    print(f"Expected total time: ~{len(scenarios) * 2}-{len(scenarios) * 3} minutes\n")

    t_start = time.time()
    results: list[ScenarioResult] = []
    for i, scenario in enumerate(scenarios, 1):
        print(f"[{i}/{len(scenarios)}] === {scenario.persona} ===")
        print(f"  Guidance:   {scenario.guidance}")
        print(f"  Locations:  {scenario.locations or '—'}")
        print(f"  Min salary: {'$' + format(scenario.min_salary, ',') if scenario.min_salary else '—'}")
        result = await run_scenario(scenario, existing_urls, max_hits=args.max_hits)
        results.append(result)

        if result.error_msg:
            print(f"  ❌ ERROR: {result.error_msg}")
        else:
            print(
                f"  → {len(result.imported_jobs)} imported "
                f"({result.novel_job_count} novel), "
                f"{result.hits_ats} ATS + {result.hits_lead} lead + "
                f"{result.hits_tracked} tracked + "
                f"{result.hits_no_match} no_match  [{result.elapsed_sec:.0f}s]"
            )
            if result.no_match_companies:
                print(f"  Considered but rejected:")
                for c in result.no_match_companies[:5]:
                    print(f"    - {c['name']}: {c['reason'][:80]}")
        print()

    total_elapsed = time.time() - t_start

    # Aggregate + examples
    aggregate = _aggregate(results)
    examples = _best_and_worst(results, n=5)

    # Write JSON report
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = RESULTS_DIR / f"hot_search_{timestamp}.json"
    report = {
        "eval": "hot_search",
        "timestamp": timestamp,
        "max_hits": args.max_hits,
        "total_elapsed_sec": round(total_elapsed, 1),
        "db_snapshot_url_count": len(existing_urls),
        "aggregate": aggregate,
        "examples": examples,
        "scenarios": [
            {
                "persona": r.persona,
                "guidance": r.guidance,
                "locations": r.locations,
                "min_salary": r.min_salary,
                "coverage": r.coverage,
                "novelty_rate": r.novelty_rate,
                "mean_relevance": r.mean_relevance,
                "hits_ats": r.hits_ats,
                "hits_lead": r.hits_lead,
                "hits_tracked": r.hits_tracked,
                "hits_no_match": r.hits_no_match,
                "no_match_companies": r.no_match_companies,
                "skips": r.skips,
                "errors": r.errors,
                "imported_job_count": len(r.imported_jobs),
                "novel_job_count": r.novel_job_count,
                "t_first_hit_sec": r.t_first_hit_sec,
                "elapsed_sec": r.elapsed_sec,
                "source_breakdown": r.source_breakdown,
                "funnel": r.funnel,
                "top_skip_reasons": r.top_skip_reasons,
                "judged_jobs": r.judged_jobs,
                "error_msg": r.error_msg,
            }
            for r in results
        ],
    }
    report_path.write_text(json.dumps(report, indent=2, default=str))

    _print_stdout_summary(results, aggregate, examples)
    print(f"Full report: {report_path}")


if __name__ == "__main__":
    asyncio.run(main())
