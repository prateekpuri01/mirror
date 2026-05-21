"""Smaller v2-only smoke evaluation.

Runs ``run_hot_company_search_v2`` directly against a small subset of
the eval_hot_search personas and reports per-scenario metrics without
needing the v1 baseline to finish first. Used during overnight rollout
when we need v2 signal independent of the long v1 baseline.

Does NOT do LLM-as-judge scoring of imports (that lives in
eval_hot_search.py and runs on the same data). Reports the funnel +
hit count + wall clock instead — enough to confirm the pipeline isn't
producing zero hits or blowing the budget.

Usage:
    docker compose exec api python -m scripts.eval.eval_v2_smoke
    docker compose exec api python -m scripts.eval.eval_v2_smoke --personas ml_engineer_loose,fintech_ds
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

# Make `app` importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config import settings
from app.database import async_session
from app.services import app_settings_service
from app.services.hot_company_search import run_hot_company_search

logger = logging.getLogger(__name__)


@dataclass
class Persona:
    name: str
    guidance: str
    locations: list[str] | None = None
    min_salary: int | None = None


# Small subset of eval_hot_search SCENARIOS — covers the main shapes
# (loose, geo-filtered, named-domain) without the full 9-persona run.
PERSONAS: list[Persona] = [
    Persona(
        name="ml_engineer_loose",
        guidance="machine learning engineer",
    ),
    Persona(
        name="ai_safety_sf",
        guidance="senior machine learning engineer at AI safety lab",
        locations=["San Francisco", "Remote"],
    ),
    Persona(
        name="fintech_ds",
        guidance="data scientist at fintech or crypto",
        min_salary=200000,
    ),
    Persona(
        name="healthcare_ml",
        guidance="machine learning at health-tech or biotech",
    ),
]


async def _hydrate_settings() -> None:
    async with async_session() as s:
        await app_settings_service.load_into_settings(s, settings)
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY missing — configure via /setup or env var")


async def run_one(persona: Persona, max_hits: int) -> dict:
    print(f"\n=== {persona.name} ===")
    print(f"  guidance: {persona.guidance}")
    print(f"  locations: {persona.locations or '—'}")
    print(f"  min_salary: ${persona.min_salary:,}" if persona.min_salary else "  min_salary: —")

    settings.hot_search_v2 = True  # force v2

    t0 = time.monotonic()
    hits: list[dict] = []
    skips_n = 0
    final_funnel: dict = {}
    last_status = ""

    gen = run_hot_company_search(
        sources=["web", "greenhouse", "lever", "ashby"],
        guidance=persona.guidance,
        max_hits=max_hits,
        max_iterations=1,
        locations=persona.locations,
        min_salary=persona.min_salary,
    )
    try:
        async for ev in gen:
            if ev.event == "status":
                last_status = ev.data.get("message", "")
            elif ev.event == "hit":
                hits.append({
                    "name": ev.data.get("name"),
                    "match_score": ev.data.get("match_score"),
                    "is_tentative": ev.data.get("is_tentative"),
                    "relevant_jobs": ev.data.get("relevant_jobs"),
                    "top_job_title": (ev.data.get("top_jobs", [{}]) or [{}])[0].get("title"),
                    "match_reason": (ev.data.get("match_reason") or "")[:150],
                })
                print(f"  HIT: {hits[-1]['name']} (score={hits[-1]['match_score']}, "
                      f"jobs={hits[-1]['relevant_jobs']}, tent={hits[-1]['is_tentative']})")
            elif ev.event == "skip":
                skips_n += 1
            elif ev.event == "done":
                final_funnel = ev.data.get("funnel", {})
    except Exception as e:
        logger.exception("Persona %s raised", persona.name)
        return {
            "persona": persona.name,
            "error": str(e),
            "elapsed_sec": time.monotonic() - t0,
        }

    elapsed = time.monotonic() - t0
    print(f"  → {len(hits)} hits, {skips_n} skips in {elapsed:.1f}s")
    print(f"  funnel: {final_funnel}")
    return {
        "persona": persona.name,
        "guidance": persona.guidance,
        "locations": persona.locations,
        "min_salary": persona.min_salary,
        "hits": hits,
        "n_hits": len(hits),
        "n_skips": skips_n,
        "elapsed_sec": round(elapsed, 1),
        "funnel": final_funnel,
        "tentative_count": sum(1 for h in hits if h.get("is_tentative")),
    }


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--personas", default=None,
                        help="Comma-separated names; default: all")
    parser.add_argument("--max-hits", type=int, default=5)
    parser.add_argument("--output",
                        default=str(Path(__file__).resolve().parent / "output" / "v2_smoke.json"),
                        help="Where to dump JSON results")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    await _hydrate_settings()
    print(f"Runtime settings loaded; provider={settings.llm_provider}")

    selected = PERSONAS
    if args.personas:
        wanted = {p.strip().lower() for p in args.personas.split(",") if p.strip()}
        selected = [p for p in PERSONAS if p.name.lower() in wanted]
        if not selected:
            print("No personas matched", file=sys.stderr)
            return

    results = []
    t_start = time.time()
    for p in selected:
        try:
            r = await run_one(p, max_hits=args.max_hits)
        except Exception:
            logger.exception("persona %s failed", p.name)
            continue
        results.append(r)

    total = time.time() - t_start

    # Aggregate
    mean_hits = sum(r.get("n_hits", 0) for r in results) / max(1, len(results))
    mean_elapsed = sum(r.get("elapsed_sec", 0) for r in results) / max(1, len(results))
    coverage = sum(1 for r in results if r.get("n_hits", 0) > 0)
    print()
    print("=" * 56)
    print(f"Total: {len(results)} personas in {total:.0f}s")
    print(f"  coverage: {coverage}/{len(results)} got >= 1 hit")
    print(f"  mean hits/persona: {mean_hits:.1f}")
    print(f"  mean wall-clock/persona: {mean_elapsed:.0f}s")
    print("=" * 56)

    # Dump
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "version": "v2_smoke",
        "n_personas": len(results),
        "total_elapsed_sec": round(total, 1),
        "aggregate": {
            "coverage_count": f"{coverage}/{len(results)}",
            "mean_hits_per_persona": round(mean_hits, 2),
            "mean_elapsed_sec": round(mean_elapsed, 1),
        },
        "results": results,
    }, indent=2))
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
