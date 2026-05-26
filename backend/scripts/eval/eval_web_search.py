"""Eval: web search backend comparison.

Runs a fixed set of representative queries through every available web
search backend and grades each result with an LLM-as-judge. Used to
decide whether to retire Perplexity (or SearXNG, or Brave) in favor of
native LLM search — answer comes from the numbers, not vibes.

Backends are probed dynamically. Any with a missing API key is skipped:

  - Perplexity Sonar     (PERPLEXITY_API_KEY)
  - OpenAI native        (OPENAI_API_KEY,    via Responses API)
  - Anthropic native     (ANTHROPIC_API_KEY, via Messages API)
  - SearXNG              (always-on baseline — raw results only)

Judging axes (1-3 scale, plus a short reason):

  - **relevance**         — does the answer / citations actually address the query?
  - **citation_quality**  — do the cited URLs come from authoritative,
                            on-topic sources?
  - **recency**           — for time-sensitive queries, are results
                            from the right window? Otherwise ``n/a``.

Output:

  - ``output/web_search_eval.json`` — full per-query × per-backend dump,
    including each backend's answer + citations + elapsed wall time.
  - ``output/web_search_eval.md``   — markdown summary table for the PR.

Usage:

    docker compose exec api python scripts/eval/eval_web_search.py
    docker compose exec api python scripts/eval/eval_web_search.py --limit 3   # smoke test
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Make ``app`` importable across both layouts.
_HERE = Path(__file__).resolve().parent
for _root in (_HERE.parents[1], _HERE.parents[2] / "backend"):
    if (_root / "app" / "__init__.py").exists():
        if str(_root) not in sys.path:
            sys.path.insert(0, str(_root))
        break

from app.ai.client import RESUME_MODEL, get_openai_client  # noqa: E402
from app.config import settings  # noqa: E402
from app.services.web_search import _searxng_search  # noqa: E402
from app.services.web_search_llm import (  # noqa: E402
    _ANTHROPIC_WEB_SEARCH_MODEL_DEFAULT,
    _ANTHROPIC_WEB_SEARCH_TOOL_TYPE,
    _OPENAI_WEB_SEARCH_MODEL_DEFAULT,
    _extract_anthropic_citations,
    _extract_openai_citations,
    _join_anthropic_text,
)

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logger = logging.getLogger("eval_web_search")
logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Query suite — covers four use-case shapes the codebase actually exercises
# ---------------------------------------------------------------------------


@dataclass
class Query:
    label: str  # short name for the table column
    text: str  # the actual search query
    shape: str  # company_research | discovery | recency | disambiguation | precision
    expects_recency: bool  # whether the recency axis is graded
    judge_notes: str  # what "good" looks like for this query
    expected_answer_hint: str = ""  # short ground-truth hint the judge can use


QUERIES: list[Query] = [
    Query(
        label="company-research-anthropic",
        text=(
            "Research Anthropic, an AI safety company headquartered at "
            "anthropic.com. I need: products and APIs, recent funding or news "
            "from the past year, company stage and approximate size, what they "
            "value in technical hires. Be specific and cite sources."
        ),
        shape="company_research",
        expects_recency=True,
        judge_notes="Should mention Claude, the API, recent fundraising, and that they value AI safety research, evals, and policy work.",
        expected_answer_hint="Anthropic builds Claude (LLM family). Founded 2021. Significant funding from Google, Amazon. Focus on AI safety.",
    ),
    Query(
        label="company-research-cohere",
        text=(
            "Research Cohere for a job application. I need: what they build, "
            "company stage, technology stack, and what backgrounds they value in "
            "technical hires."
        ),
        shape="company_research",
        expects_recency=True,
        judge_notes="Should mention enterprise LLM platform, Command/Embed/Rerank models, retrieval focus, Toronto HQ.",
    ),
    Query(
        label="discovery-ai-safety-sf",
        text="Small AI safety startups in San Francisco hiring ML research engineers in 2026",
        shape="discovery",
        expects_recency=True,
        judge_notes="Should list several real companies (e.g. Anthropic, Conjecture, Apollo Research, Redwood Research). Generic AI labs are partial credit.",
    ),
    Query(
        label="discovery-rlhf-labs",
        text="Companies that specialize in RLHF or human-in-the-loop data labeling for frontier AI models",
        shape="discovery",
        expects_recency=False,
        judge_notes="Should list real specialists like Surge AI, Scale AI, Snorkel AI, Invisible. Generic 'AI companies' are wrong.",
    ),
    Query(
        label="recency-openai-news",
        text="OpenAI news from the past 14 days — funding, product launches, leadership changes",
        shape="recency",
        expects_recency=True,
        judge_notes="Citations should be dated within the past 2-3 weeks. Older news is wrong.",
    ),
    Query(
        label="disambiguation-surge",
        text=(
            "Surge AI, the data labeling startup at surgehq.ai — NOT the SMS API "
            "company also called Surge. What does Surge AI build, and who do they work with?"
        ),
        shape="disambiguation",
        expects_recency=False,
        judge_notes="Must describe the data-labeling / RLHF company, NOT the SMS company. Citations should reference surgehq.ai or articles about the data labeling business.",
        expected_answer_hint="Surge AI is a data labeling / RLHF company that works with frontier AI labs (Anthropic, OpenAI, etc.).",
    ),
    Query(
        label="person-research-anthropic-founders",
        text="Who founded Anthropic? When? What were their backgrounds before starting it?",
        shape="discovery",
        expects_recency=False,
        judge_notes="Should mention Dario and Daniela Amodei, founded 2021, prior roles at OpenAI (Dario as VP of Research).",
        expected_answer_hint="Dario Amodei (CEO, prior VP Research at OpenAI), Daniela Amodei (President). Co-founders include several other ex-OpenAI researchers. Founded 2021.",
    ),
    Query(
        label="precision-greenhouse-anthropic",
        text="site:greenhouse.io anthropic open job listings",
        shape="precision",
        expects_recency=False,
        judge_notes="CONTROL: native LLM search is expected to underperform here because it can't reliably honor site: operators. SearXNG should win this one. Good results contain greenhouse.io URLs that resolve to actual Anthropic job listings.",
    ),
]


# ---------------------------------------------------------------------------
# Backend testers
# ---------------------------------------------------------------------------


@dataclass
class BackendResult:
    backend: str
    query_label: str
    elapsed_s: float
    answer: str = ""
    citations: list[dict] = field(default_factory=list)
    error: str | None = None
    skipped: bool = False


async def test_perplexity(q: Query) -> BackendResult:
    """Tests Perplexity Sonar via the chat completions API — the same
    grounded-LLM mode that ``_research_via_perplexity`` uses, not the
    raw-results helper."""
    if not settings.perplexity_api_key:
        return BackendResult(backend="perplexity", query_label=q.label, elapsed_s=0.0, skipped=True)
    import httpx

    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            resp = await client.post(
                "https://api.perplexity.ai/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.perplexity_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "sonar",
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are a research assistant. Give a concise, "
                                "factual answer with sources. 200-400 words."
                            ),
                        },
                        {"role": "user", "content": q.text},
                    ],
                    "max_tokens": 1000,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        answer = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        citations = []
        for item in data.get("search_results", [])[:8]:
            citations.append(
                {
                    "title": item.get("title", "") or "",
                    "url": item.get("url", "") or "",
                    "snippet": item.get("date", "") or "",
                }
            )
        if not citations:
            for url in data.get("citations", [])[:8]:
                if url:
                    citations.append({"title": "", "url": url, "snippet": ""})
        return BackendResult(
            backend="perplexity",
            query_label=q.label,
            elapsed_s=time.perf_counter() - t0,
            answer=answer,
            citations=citations,
        )
    except Exception as e:
        return BackendResult(
            backend="perplexity",
            query_label=q.label,
            elapsed_s=time.perf_counter() - t0,
            error=str(e)[:300],
        )


async def test_openai_native(q: Query) -> BackendResult:
    """Direct OpenAI Responses API call — bypasses ``llm_web_search`` so
    we can run this even when LLM_PROVIDER != openai."""
    if not settings.openai_api_key:
        return BackendResult(
            backend="openai-native", query_label=q.label, elapsed_s=0.0, skipped=True
        )
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    model = settings.llm_web_search_model or _OPENAI_WEB_SEARCH_MODEL_DEFAULT
    t0 = time.perf_counter()
    try:
        response = await client.responses.create(
            model=model,
            tools=[{"type": "web_search_preview"}],
            input=q.text,
        )
        answer = (getattr(response, "output_text", "") or "").strip()
        cits = _extract_openai_citations(response, num_results=8)
        return BackendResult(
            backend="openai-native",
            query_label=q.label,
            elapsed_s=time.perf_counter() - t0,
            answer=answer,
            citations=[asdict(c) for c in cits],
        )
    except Exception as e:
        return BackendResult(
            backend="openai-native",
            query_label=q.label,
            elapsed_s=time.perf_counter() - t0,
            error=str(e)[:300],
        )


async def test_anthropic_native(q: Query) -> BackendResult:
    """Direct Anthropic Messages API call with web_search tool."""
    if not settings.anthropic_api_key:
        return BackendResult(
            backend="anthropic-native", query_label=q.label, elapsed_s=0.0, skipped=True
        )
    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        return BackendResult(
            backend="anthropic-native",
            query_label=q.label,
            elapsed_s=0.0,
            error="anthropic SDK not installed",
        )
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    model = settings.llm_web_search_model or _ANTHROPIC_WEB_SEARCH_MODEL_DEFAULT
    t0 = time.perf_counter()
    try:
        response = await client.messages.create(
            model=model,
            max_tokens=1500,
            tools=[
                {
                    "type": _ANTHROPIC_WEB_SEARCH_TOOL_TYPE,
                    "name": "web_search",
                    "max_uses": 5,
                }
            ],
            messages=[{"role": "user", "content": q.text}],
        )
        answer = _join_anthropic_text(response)
        cits = _extract_anthropic_citations(response, num_results=8)
        return BackendResult(
            backend="anthropic-native",
            query_label=q.label,
            elapsed_s=time.perf_counter() - t0,
            answer=answer,
            citations=[asdict(c) for c in cits],
        )
    except Exception as e:
        return BackendResult(
            backend="anthropic-native",
            query_label=q.label,
            elapsed_s=time.perf_counter() - t0,
            error=str(e)[:300],
        )


async def test_searxng(q: Query) -> BackendResult:
    """SearXNG raw results — no synthesized answer."""
    t0 = time.perf_counter()
    try:
        raw = await _searxng_search(q.text, num_results=8, time_range=None)
        return BackendResult(
            backend="searxng",
            query_label=q.label,
            elapsed_s=time.perf_counter() - t0,
            answer="",  # No synthesis from SearXNG
            citations=raw,
        )
    except Exception as e:
        return BackendResult(
            backend="searxng",
            query_label=q.label,
            elapsed_s=time.perf_counter() - t0,
            error=str(e)[:300],
        )


BACKENDS = [
    ("perplexity", test_perplexity),
    ("openai-native", test_openai_native),
    ("anthropic-native", test_anthropic_native),
    ("searxng", test_searxng),
]


# ---------------------------------------------------------------------------
# LLM-as-judge
# ---------------------------------------------------------------------------


_JUDGE_SYSTEM = """\
You grade a web search result for a specific query, in a research context
where a downstream system will use this result to inform a job application
or hiring decision. Be strict — bad search results lead to wrong resumes.

You receive:
  - the original query
  - what "good" looks like for this query (from the eval author)
  - the backend's synthesized answer (may be empty for raw-results backends)
  - the backend's citations (title + url + optional snippet)

Grade on a 1-3 scale (1=poor, 2=ok, 3=excellent) along three axes:

  1. relevance — does the answer/citations actually address the query?
     Don't reward off-topic citations even if they look authoritative.
  2. citation_quality — do the cited URLs come from authoritative,
     on-topic sources? Penalize bare/empty citations and irrelevant
     domains. For raw-results backends, score based on the snippets.
  3. recency — if the query specifies a time window, are the cited
     sources from the right window? Output "n/a" for queries where
     recency doesn't apply.

For raw-results backends (no synthesized answer), grade citation_quality
based on the snippets; relevance is the snippets' aggregate match to
the query.

Output ONLY this JSON (no markdown fences):
{
  "relevance": 1-3,
  "citation_quality": 1-3,
  "recency": 1-3 OR "n/a",
  "reason": "one short sentence on what worked or what failed"
}
"""


async def judge(
    query: Query,
    result: BackendResult,
) -> dict:
    """Grade one backend's result for one query."""
    if result.skipped:
        return {
            "relevance": None,
            "citation_quality": None,
            "recency": None,
            "reason": "skipped (backend not configured)",
        }
    if result.error:
        return {
            "relevance": 1,
            "citation_quality": 1,
            "recency": "n/a",
            "reason": f"backend errored: {result.error[:150]}",
        }

    body = (
        f"## Query ({query.shape})\n{query.text}\n\n"
        f"## What 'good' looks like\n{query.judge_notes}\n"
        + (
            f"\n## Ground-truth hint\n{query.expected_answer_hint}\n"
            if query.expected_answer_hint
            else ""
        )
        + f"\n## Backend: {result.backend}\n"
        f"\n## Answer ({len(result.answer)} chars)\n{result.answer[:2000]}\n\n"
        f"## Citations ({len(result.citations)})\n"
        + "\n".join(
            f"  - {c.get('title', '(no title)')} | {c.get('url', '(no url)')}"
            for c in result.citations[:8]
        )
        + (
            f"\n\n## Note\n{'Recency matters for this query.' if query.expects_recency else 'Recency does not apply.'}"
        )
    )

    client = get_openai_client()
    try:
        resp = await client.chat.completions.create(
            model=RESUME_MODEL,
            messages=[
                {"role": "system", "content": _JUDGE_SYSTEM},
                {"role": "user", "content": body},
            ],
            max_completion_tokens=400,
            temperature=0,
        )
        text = (resp.choices[0].message.content or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        return json.loads(text)
    except Exception as e:
        return {
            "relevance": 1,
            "citation_quality": 1,
            "recency": "n/a",
            "reason": f"judge call failed: {str(e)[:150]}",
        }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _score(j: dict) -> int | None:
    """Sum of (relevance + citation_quality + recency-if-numeric)."""
    if j.get("relevance") is None:
        return None
    total = (j.get("relevance") or 0) + (j.get("citation_quality") or 0)
    if isinstance(j.get("recency"), int):
        total += j["recency"]
    return total


def render_markdown(rows: list[dict]) -> str:
    """Build a per-query × per-backend table with averages."""
    backends = sorted({r["backend"] for r in rows})
    queries = sorted({r["query"] for r in rows})

    by_pair: dict[tuple[str, str], dict] = {}
    for r in rows:
        by_pair[(r["query"], r["backend"])] = r

    lines: list[str] = []
    lines.append("# Web search eval — backend comparison\n")
    lines.append(
        "Scores are `relevance / citation_quality / recency`. "
        "Each cell shows totals per axis on a 1-3 scale. "
        "`—` means the backend was skipped (no API key configured).\n"
    )

    # Per-backend averages
    lines.append("## Aggregate (mean of available axes)\n")
    lines.append(
        "| backend | mean relevance | mean citation | mean recency | mean total | wall time (s) |"
    )
    lines.append("|---|---|---|---|---|---|")
    for backend in backends:
        rel: list[int] = []
        cit: list[int] = []
        rec: list[int] = []
        totals: list[int] = []
        times: list[float] = []
        for q in queries:
            r = by_pair.get((q, backend))
            if r is None or r["judgment"].get("relevance") is None:
                continue
            j = r["judgment"]
            if isinstance(j["relevance"], int):
                rel.append(j["relevance"])
            if isinstance(j["citation_quality"], int):
                cit.append(j["citation_quality"])
            if isinstance(j.get("recency"), int):
                rec.append(j["recency"])
            t = _score(j)
            if t is not None:
                totals.append(t)
            times.append(r["elapsed_s"])

        def _avg(xs):
            return f"{sum(xs) / len(xs):.2f}" if xs else "—"

        lines.append(
            f"| {backend} | {_avg(rel)} | {_avg(cit)} | {_avg(rec)} | "
            f"{_avg(totals)} | {_avg(times)} |"
        )

    # Per-query × backend grid
    lines.append("\n## Per-query × per-backend (relevance / citation / recency)\n")
    header = "| query | " + " | ".join(backends) + " |"
    sep = "|" + "---|" * (len(backends) + 1)
    lines.append(header)
    lines.append(sep)
    for q in queries:
        row = [q]
        for backend in backends:
            r = by_pair.get((q, backend))
            if r is None or r["judgment"].get("relevance") is None:
                row.append("—")
                continue
            j = r["judgment"]
            row.append(f"{j['relevance']} / {j['citation_quality']} / {j.get('recency', 'n/a')}")
        lines.append("| " + " | ".join(row) + " |")

    lines.append("\n## Notes\n")
    for q in queries:
        lines.append(f"\n### {q}\n")
        for backend in backends:
            r = by_pair.get((q, backend))
            if r is None:
                continue
            j = r["judgment"]
            reason = j.get("reason", "")
            lines.append(f"- **{backend}**: {reason}")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--limit", type=int, default=None, help="Run only the first N queries (for smoke tests)."
    )
    args = parser.parse_args()

    queries = QUERIES[: args.limit] if args.limit else QUERIES

    print()
    print("=" * 78)
    print(f"Web search eval — {len(queries)} queries × {len(BACKENDS)} backends")
    print("=" * 78)

    rows: list[dict] = []
    for q in queries:
        print(f"\n→ {q.label}: {q.text[:100]}...")
        backend_results = await asyncio.gather(
            *[fn(q) for _, fn in BACKENDS], return_exceptions=False
        )

        # Judge each result (cheaper to do sequentially per-query to avoid
        # bursting the judge model)
        for r in backend_results:
            if r.skipped:
                status = "skipped"
            elif r.error:
                status = f"error: {r.error[:80]}"
            else:
                status = f"{len(r.citations)} citations · {r.elapsed_s:.1f}s"
            print(f"   {r.backend:18} {status}")

            j = await judge(q, r)
            rows.append(
                {
                    "query": q.label,
                    "query_text": q.text,
                    "shape": q.shape,
                    "expects_recency": q.expects_recency,
                    "backend": r.backend,
                    "elapsed_s": r.elapsed_s,
                    "answer": r.answer,
                    "citations": r.citations,
                    "skipped": r.skipped,
                    "error": r.error,
                    "judgment": j,
                }
            )

    # Persist
    out_dir = Path(os.environ.get("EVAL_OUTPUT_DIR", "output"))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "web_search_eval.json").write_text(json.dumps(rows, indent=2, default=str))
    (out_dir / "web_search_eval.md").write_text(render_markdown(rows))

    print()
    print("=" * 78)
    print(f"Wrote: {out_dir / 'web_search_eval.json'}")
    print(f"Wrote: {out_dir / 'web_search_eval.md'}")
    print("=" * 78)
    print()
    print("Open the markdown for the readable summary. Backends with all `—`")
    print("rows were skipped because their API key isn't configured.")


if __name__ == "__main__":
    asyncio.run(main())
