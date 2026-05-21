"""Eval: discovery-arm comparison for the hot-search rebuild.

Question: when discovering companies + careers pages + job postings, does
the agentic LLM web search surface URLs SearXNG misses? Does the union
of the two materially beat either alone?

This eval is *task-shaped*, not generic. The metric is not "did the
search answer the question" but "did it produce URLs we can feed
downstream into our ATS pipeline." So we classify every returned URL by
its downstream usefulness:

  rank 1  direct ATS URL (boards.greenhouse.io/X, jobs.lever.co/X, jobs.ashbyhq.com/X)
  rank 2  direct job-posting URL on a company domain (microsoft.com/job/12345)
  rank 3  careers page URL on a company domain (acme.com/careers)
  rank 0  aggregator / non-actionable noise (LinkedIn, Indeed, Glassdoor,
          news article, listicle, etc.)

For each arm we report:
  - candidates surfaced (per-rank breakdown)
  - aggregator-noise rate (rank-0 share of returned URLs)
  - unique URLs contributed (set diff vs the other arm)
  - wall-clock

Arms:
  A. searxng        — _searxng_search() only (free, self-hosted)
  B. llm_web        — llm_web_search() only (OpenAI Responses + web_search tool)
  C. union          — A ∪ B, deduplicated by URL

Output:
  output/discovery_eval.jsonl   per (case, arm, url) record
  output/discovery_eval.md      summary tables

Usage:
  docker compose exec api python scripts/eval/eval_discovery.py
  docker compose exec api python scripts/eval/eval_discovery.py --limit 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlparse

# Make `app` importable across both layouts.
_HERE = Path(__file__).resolve().parent
for _root in (_HERE.parents[1], _HERE.parents[2] / "backend"):
    if (_root / "app" / "__init__.py").exists():
        if str(_root) not in sys.path:
            sys.path.insert(0, str(_root))
        break

from app.config import settings  # noqa: E402
from app.database import async_session  # noqa: E402
from app.services import app_settings_service  # noqa: E402
from app.services.web_search import _searxng_search  # noqa: E402
from app.services.web_search_llm import llm_web_search  # noqa: E402
from app.services.hot_search.discovery import (  # noqa: E402
    _ATS_URL_PATTERNS,
    _SKIP_DOMAINS,
    _looks_like_direct_job_url,
    _looks_like_careers_url,
)


async def _load_runtime_settings() -> None:
    """The running app stores provider + API keys in the app_settings DB
    table, not in env vars. Standalone scripts need to hydrate `settings`
    from the DB before any code that reads `settings.openai_api_key`
    runs — otherwise llm_web_search() returns None silently."""
    async with async_session() as session:
        await app_settings_service.load_into_settings(session, settings)
    logger.info(
        "Runtime settings loaded: provider=%s, openai_key=%s",
        settings.llm_provider,
        "set" if settings.openai_api_key else "MISSING",
    )

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("eval_discovery")
logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Test cases — discovery-shaped, matching the user's actual workflow.
# ---------------------------------------------------------------------------


@dataclass
class Case:
    label: str
    query: str
    shape: str   # broad_domain | named_entity | ats_precision
    notes: str   # what "good" looks like, qualitative


CASES: list[Case] = [
    Case(
        label="broad-ai-safety-sf",
        query="AI safety startups in San Francisco hiring ML research engineers 2026",
        shape="broad_domain",
        notes="Expect ATS hits at Anthropic, Conjecture, Apollo, Redwood, etc. SearXNG should produce many results; LLM should reason to small lab subset.",
    ),
    Case(
        label="broad-llm-eval",
        query="companies building LLM evaluation tools hiring 2026",
        shape="broad_domain",
        notes="Niche enough that aggregator listicles dominate keyword results; LLM may extract the real specialists from snippets.",
    ),
    Case(
        label="broad-robotics-infra",
        query="robotics infrastructure startups Series B hiring infrastructure engineers",
        shape="broad_domain",
        notes="Cross-domain query — robotics + infra. Tests whether agent reasons across categories vs keyword match.",
    ),
    Case(
        label="named-microsoft-ai",
        query="Microsoft AI research scientist open positions 2026",
        shape="named_entity",
        notes="Big-company query. Expect careers.microsoft.com URLs. LLM should drill into the company site; SearXNG returns more LinkedIn/Indeed pollution.",
    ),
    Case(
        label="named-anthropic-careers",
        query="Anthropic careers open roles engineering",
        shape="named_entity",
        notes="Should land on anthropic.com/careers OR boards.greenhouse.io/anthropic. Crisp expected output.",
    ),
    Case(
        label="named-stripe-ml",
        query="Stripe machine learning engineer job postings",
        shape="named_entity",
        notes="Stripe runs its own portal at stripe.com/jobs. Expect direct-posting URLs.",
    ),
    Case(
        label="ats-greenhouse-ml",
        query="site:boards.greenhouse.io machine learning engineer",
        shape="ats_precision",
        notes="CONTROL: SearXNG is expected to crush this. LLM may ignore site: operator. If LLM still wins here, it changes the calculus.",
    ),
    Case(
        label="ats-lever-data",
        query="site:jobs.lever.co data scientist",
        shape="ats_precision",
        notes="Same control, different ATS. Confirms site:-precision behavior is consistent across ATS domains.",
    ),
]


# ---------------------------------------------------------------------------
# URL classifier — what is each surfaced URL worth to downstream?
# ---------------------------------------------------------------------------


def classify_url(url: str) -> tuple[int, str]:
    """Return (rank, label).
      rank 1: direct ATS URL (full board scrape possible)
      rank 2: direct job-posting URL on a company domain
      rank 3: careers page URL on a company domain
      rank 0: aggregator / noise / non-actionable
    """
    if not url or not url.startswith(("http://", "https://")):
        return 0, "invalid"

    # rank 1: ATS URLs
    for ats, pat in _ATS_URL_PATTERNS.items():
        if pat.search(url):
            return 1, f"ats:{ats}"

    try:
        host = (urlparse(url).hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
    except Exception:
        return 0, "invalid"

    # rank 0: explicit skip-domains (aggregators, news, social)
    if any(host == d or host.endswith(f".{d}") for d in _SKIP_DOMAINS):
        return 0, f"aggregator:{host}"

    # rank 2: direct posting URL (numeric ID, UUID, etc.) on a non-aggregator
    if _looks_like_direct_job_url(url):
        return 2, "direct_posting"

    # rank 3: careers page on a non-aggregator domain
    if _looks_like_careers_url(url):
        return 3, "careers_page"

    return 0, "off_topic"


# ---------------------------------------------------------------------------
# Arms
# ---------------------------------------------------------------------------


@dataclass
class ArmResult:
    arm: str
    urls: list[dict] = field(default_factory=list)  # [{url, title, snippet, rank, kind}]
    elapsed_s: float = 0.0
    error: str = ""


_ARM_TIMEOUT_S = 120.0  # hard cap per arm so a hang can't kill the whole run


async def run_searxng(query: str, num_results: int = 10) -> ArmResult:
    t0 = time.monotonic()
    try:
        raw = await asyncio.wait_for(
            _searxng_search(query, num_results, time_range=None),
            timeout=_ARM_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        return ArmResult(arm="searxng", elapsed_s=time.monotonic() - t0, error=f"timeout >{_ARM_TIMEOUT_S}s")
    except Exception as e:
        return ArmResult(arm="searxng", elapsed_s=time.monotonic() - t0, error=str(e)[:200])
    urls = []
    for r in raw:
        url = (r.get("url") or "").strip()
        if not url:
            continue
        rank, kind = classify_url(url)
        urls.append({
            "url": url,
            "title": r.get("title", ""),
            "snippet": (r.get("snippet") or "")[:200],
            "rank": rank,
            "kind": kind,
        })
    return ArmResult(arm="searxng", urls=urls, elapsed_s=time.monotonic() - t0)


async def run_llm_web(query: str, num_results: int = 10) -> ArmResult:
    t0 = time.monotonic()
    try:
        res = await asyncio.wait_for(
            llm_web_search(query, num_results=num_results),
            timeout=_ARM_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        return ArmResult(arm="llm_web", elapsed_s=time.monotonic() - t0, error=f"timeout >{_ARM_TIMEOUT_S}s")
    except Exception as e:
        return ArmResult(arm="llm_web", elapsed_s=time.monotonic() - t0, error=str(e)[:200])
    if res is None:
        return ArmResult(arm="llm_web", elapsed_s=time.monotonic() - t0, error="returned None")
    urls = []
    for c in res.citations:
        url = (c.url or "").strip()
        if not url:
            continue
        rank, kind = classify_url(url)
        urls.append({
            "url": url,
            "title": c.title or "",
            "snippet": "",
            "rank": rank,
            "kind": kind,
        })
    return ArmResult(arm="llm_web", urls=urls, elapsed_s=time.monotonic() - t0)


def union_arm(a: ArmResult, b: ArmResult) -> ArmResult:
    seen: set[str] = set()
    merged: list[dict] = []
    for r in a.urls + b.urls:
        if r["url"] in seen:
            continue
        seen.add(r["url"])
        merged.append(r)
    return ArmResult(
        arm="union",
        urls=merged,
        elapsed_s=max(a.elapsed_s, b.elapsed_s),  # parallel
    )


# ---------------------------------------------------------------------------
# Per-case driver
# ---------------------------------------------------------------------------


async def run_case(case: Case, num_results: int) -> dict:
    logger.info("Running case %s (%s)", case.label, case.shape)
    # Run searxng and llm_web in parallel
    sx_task = asyncio.create_task(run_searxng(case.query, num_results))
    llm_task = asyncio.create_task(run_llm_web(case.query, num_results))
    sx, llm = await asyncio.gather(sx_task, llm_task)
    un = union_arm(sx, llm)
    return {
        "case": asdict(case),
        "arms": {
            "searxng": asdict(sx),
            "llm_web": asdict(llm),
            "union": asdict(un),
        },
    }


# ---------------------------------------------------------------------------
# Aggregation + reporting
# ---------------------------------------------------------------------------


def summarize_arm(urls: list[dict]) -> dict:
    """Per-rank counts, share of actionable (rank 1-3) URLs, etc."""
    total = len(urls)
    counts = {1: 0, 2: 0, 3: 0, 0: 0}
    for u in urls:
        counts[u["rank"]] = counts.get(u["rank"], 0) + 1
    actionable = counts[1] + counts[2] + counts[3]
    return {
        "n_urls": total,
        "rank_1_ats": counts[1],
        "rank_2_posting": counts[2],
        "rank_3_careers": counts[3],
        "rank_0_noise": counts[0],
        "actionable": actionable,
        "actionable_pct": round(actionable / total * 100, 1) if total else 0.0,
    }


def unique_to(a_urls: list[dict], b_urls: list[dict]) -> list[dict]:
    """URLs in a not in b."""
    b_set = {u["url"] for u in b_urls}
    return [u for u in a_urls if u["url"] not in b_set]


def build_markdown(results: list[dict]) -> str:
    lines: list[str] = []
    lines.append("# Discovery Eval — SearXNG vs LLM-Web\n")
    lines.append("## Per-case results\n")
    lines.append("| Case | Shape | Arm | URLs | ATS | Post | Career | Noise | Action% | Wall |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        case = r["case"]
        for arm_name in ("searxng", "llm_web", "union"):
            arm = r["arms"][arm_name]
            err = arm.get("error", "")
            if err:
                lines.append(
                    f"| {case['label']} | {case['shape']} | {arm_name} | "
                    f"_error: {err[:40]}_ | | | | | | {arm['elapsed_s']:.1f}s |"
                )
                continue
            s = summarize_arm(arm["urls"])
            lines.append(
                f"| {case['label']} | {case['shape']} | {arm_name} | "
                f"{s['n_urls']} | {s['rank_1_ats']} | {s['rank_2_posting']} | "
                f"{s['rank_3_careers']} | {s['rank_0_noise']} | "
                f"{s['actionable_pct']}% | {arm['elapsed_s']:.1f}s |"
            )

    lines.append("\n## Unique contributions (URLs found by only one arm)\n")
    lines.append("| Case | SearXNG-only | LLM-only | SearXNG-only ATS+Post | LLM-only ATS+Post |")
    lines.append("|---|---|---|---|---|")
    for r in results:
        sx_urls = r["arms"]["searxng"].get("urls", [])
        llm_urls = r["arms"]["llm_web"].get("urls", [])
        sx_only = unique_to(sx_urls, llm_urls)
        llm_only = unique_to(llm_urls, sx_urls)
        sx_only_act = sum(1 for u in sx_only if u["rank"] in (1, 2))
        llm_only_act = sum(1 for u in llm_only if u["rank"] in (1, 2))
        lines.append(
            f"| {r['case']['label']} | {len(sx_only)} | {len(llm_only)} | "
            f"{sx_only_act} | {llm_only_act} |"
        )

    # Per-shape aggregate
    lines.append("\n## Aggregate by query shape\n")
    lines.append("| Shape | Arm | Mean ATS+Post | Mean Action% | Mean Wall |")
    lines.append("|---|---|---|---|---|")
    shapes = sorted(set(r["case"]["shape"] for r in results))
    for shape in shapes:
        for arm_name in ("searxng", "llm_web", "union"):
            rs = [r for r in results if r["case"]["shape"] == shape]
            atsp = []
            actp = []
            walls = []
            for r in rs:
                arm = r["arms"][arm_name]
                if arm.get("error"):
                    continue
                s = summarize_arm(arm["urls"])
                atsp.append(s["rank_1_ats"] + s["rank_2_posting"])
                actp.append(s["actionable_pct"])
                walls.append(arm["elapsed_s"])
            if not atsp:
                continue
            lines.append(
                f"| {shape} | {arm_name} | "
                f"{sum(atsp)/len(atsp):.1f} | "
                f"{sum(actp)/len(actp):.1f}% | "
                f"{sum(walls)/len(walls):.1f}s |"
            )

    # Sample of highest-value LLM-only URLs
    lines.append("\n## Sample: LLM-only actionable URLs (rank 1 or 2)\n")
    for r in results:
        llm_only = unique_to(
            r["arms"]["llm_web"].get("urls", []),
            r["arms"]["searxng"].get("urls", []),
        )
        actionable = [u for u in llm_only if u["rank"] in (1, 2)]
        if not actionable:
            continue
        lines.append(f"\n### {r['case']['label']}")
        for u in actionable[:5]:
            lines.append(f"- `{u['kind']}` [{u['title'][:60]}]({u['url']})")

    lines.append("\n## Sample: SearXNG-only actionable URLs (rank 1 or 2)\n")
    for r in results:
        sx_only = unique_to(
            r["arms"]["searxng"].get("urls", []),
            r["arms"]["llm_web"].get("urls", []),
        )
        actionable = [u for u in sx_only if u["rank"] in (1, 2)]
        if not actionable:
            continue
        lines.append(f"\n### {r['case']['label']}")
        for u in actionable[:5]:
            lines.append(f"- `{u['kind']}` [{u['title'][:60]}]({u['url']})")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Run only the first N cases")
    parser.add_argument("--num-results", type=int, default=10)
    parser.add_argument(
        "--output-dir",
        default=str(_HERE / "output"),
        help="Where to drop JSONL + markdown",
    )
    parser.add_argument(
        "--only",
        default=None,
        help="Comma-separated case labels to run (default: all)",
    )
    parser.add_argument(
        "--skip",
        default=None,
        help="Comma-separated case labels to skip",
    )
    args = parser.parse_args()

    await _load_runtime_settings()

    cases = CASES[: args.limit] if args.limit else CASES
    if args.only:
        keep = {s.strip() for s in args.only.split(",") if s.strip()}
        cases = [c for c in cases if c.label in keep]
    if args.skip:
        drop = {s.strip() for s in args.skip.split(",") if s.strip()}
        cases = [c for c in cases if c.label not in drop]
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Incremental JSONL — one record per case, flushed immediately.
    # Append mode so a resumed run accumulates on top of prior partial runs.
    jsonl_path = out_dir / "discovery_eval.jsonl"
    md_path = out_dir / "discovery_eval.md"

    logger.info("Running %d cases × 2 arms (+ union)", len(cases))
    results: list[dict] = []
    with jsonl_path.open("a") as jsonl_fh:
        for case in cases:
            try:
                res = await run_case(case, args.num_results)
            except Exception:
                logger.exception("Case %s failed", case.label)
                continue
            results.append(res)
            # Persist immediately so a kill doesn't lose progress.
            jsonl_fh.write(json.dumps(res) + "\n")
            jsonl_fh.flush()
            # Print a one-line progress summary as we go
            sx = res["arms"]["searxng"]
            lw = res["arms"]["llm_web"]
            sx_s = summarize_arm(sx.get("urls", [])) if not sx.get("error") else None
            lw_s = summarize_arm(lw.get("urls", [])) if not lw.get("error") else None
            logger.info(
                "  %s: searxng=%s llm_web=%s",
                case.label,
                (f"{sx_s['actionable']}/{sx_s['n_urls']} action@{sx['elapsed_s']:.1f}s"
                 if sx_s else f"ERR {sx.get('error', '')[:40]}"),
                (f"{lw_s['actionable']}/{lw_s['n_urls']} action@{lw['elapsed_s']:.1f}s"
                 if lw_s else f"ERR {lw.get('error', '')[:40]}"),
            )

    # Rebuild markdown from the FULL JSONL on disk (not just this run's
    # batch) so resumed runs produce a coherent report.
    all_results: list[dict] = []
    with jsonl_path.open("r") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                all_results.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    logger.info("Loaded %d total cases from %s for report", len(all_results), jsonl_path)

    md_path.write_text(build_markdown(all_results))
    logger.info("Wrote %s", md_path)

    print(f"\nDone — {len(results)} cases. See {md_path}")


if __name__ == "__main__":
    asyncio.run(main())
