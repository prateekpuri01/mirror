"""Discovery v2 — LLM-web-first company discovery for hot search v2.

Three input streams unioned for the candidate pool:
  1. Aggregator harvest (HN, Remotive, Muse, Arbeitnow, RemoteOK, Jobicy)
     — free, parallel, reuses existing `_harvest_candidates_from_entries`.
  2. LLM-web discovery (OpenAI Responses API + web_search tool).
     The eval (eval_discovery.py) showed this wins decisively on
     broad-domain queries vs SearXNG. Drops SearXNG entirely.
  3. Discovery cache recall — top-K cosine-similar rows from
     ``discovered_companies`` table. Lets a company found by query A
     surface for query B without re-paying the discovery cost.

The output is a deduplicated list of ``CompanyCandidate`` records,
classified by URL rank when a URL is available:
  rank 1: direct ATS URL  (boards.greenhouse.io/X, jobs.lever.co/X, jobs.ashbyhq.com/X)
  rank 2: direct job-posting URL on company domain
  rank 3: careers page URL on company domain
  rank 0: aggregator / noise  (LinkedIn, Indeed, Glassdoor, ZipRecruiter, …)

Phase B (dedup) and Phase C (ATS resolution) live downstream; we just
produce candidates here.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Sequence
from urllib.parse import urlparse

from app.services.hot_search.discovery import (
    _ATS_URL_PATTERNS,
    _SKIP_DOMAINS,
    _llm_extract_companies,
    _looks_like_careers_url,
    _looks_like_direct_job_url,
    _generate_queries,
)
from app.services.hot_search.types import CompanyCandidate
from app.services.web_search_llm import llm_web_search

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# URL classifier (kept in sync with backend/scripts/eval/eval_discovery.py)
# ---------------------------------------------------------------------------


def classify_url(url: str) -> tuple[int, str]:
    """Return ``(rank, label)``.

      rank 1: direct ATS URL (full board scrape possible)
      rank 2: direct job-posting URL on a company domain
      rank 3: careers page URL on a company domain
      rank 0: aggregator / noise / non-actionable

    The eval harness uses this same logic — production and eval share
    one classifier so behavior cannot drift.
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
# LLM-web search prompt
# ---------------------------------------------------------------------------


# The eval showed LLM-web with a raw user query (~"AI safety startups in
# SF") already produces 66% actionable URLs on broad-domain. We can push
# that higher with explicit URL-shape negatives in the system prompt.
# Construction is per-call because guidance / filters need to be folded
# in dynamically.
def _build_llm_web_query(
    *,
    guidance: str,
    locations: list[str] | None,
    min_salary: int | None,
) -> str:
    """Build the full prompt sent to llm_web_search.

    The `web_search` tool accepts a single string input — there's no
    system/user split — so we pack instructions and constraints together.
    Phrasing matters: explicit URL-shape preferences and negatives push
    the model toward direct postings rather than aggregator listicles.
    """
    parts: list[str] = []
    parts.append(f"Find current job openings matching: {guidance.strip()}")

    if locations:
        parts.append(f"Locations: {', '.join(locations)}")
    if min_salary:
        parts.append(f"Minimum salary: ${min_salary:,}")

    parts.append("")
    parts.append(
        "Return URLs to specific job postings or company careers pages. "
        "PREFER URLs from:"
    )
    parts.append("  - boards.greenhouse.io/<company>/...")
    parts.append("  - jobs.lever.co/<company>/...")
    parts.append("  - jobs.ashbyhq.com/<company>/...")
    parts.append("  - company careers pages (e.g. anthropic.com/careers)")
    parts.append("  - direct job-posting pages on company domains")
    parts.append("")
    parts.append("AVOID:")
    parts.append("  - LinkedIn, Indeed, Glassdoor, ZipRecruiter, Monster")
    parts.append("  - builtin.com, wellfound.com, ycombinator portfolio listicles")
    parts.append("  - news articles, blog posts, generic 'top 10' listicles")
    parts.append("")
    parts.append(
        "Aim for 8-10 distinct companies. Cite each URL you used."
    )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# LLM-web discovery
# ---------------------------------------------------------------------------


async def _one_query_to_candidates(
    query: str,
    *,
    locations: list[str] | None,
    min_salary: int | None,
    num_results: int = 10,
) -> list[CompanyCandidate]:
    """Run one LLM-web call and convert its citations to candidates.

    URLs are classified; rank-1/2 yield candidates with the URL already
    attached (skip resolution downstream), rank-3 carries the careers
    URL as ``url`` so Phase C knows to try ATS-probing the host first.

    Rank-0 URLs are discarded — they're aggregator garbage we don't
    want in the candidate pool. The LLM-web answer text itself often
    names companies the model couldn't cite; we send the citations
    through ``_llm_extract_companies`` as a secondary pass to pick up
    these "named but uncited" companies.
    """
    full_query = _build_llm_web_query(
        guidance=query, locations=locations, min_salary=min_salary,
    )

    res = await llm_web_search(full_query, num_results=num_results)
    if res is None:
        logger.warning("llm_web_search returned None for query: %s", query[:80])
        return []

    candidates: list[CompanyCandidate] = []
    seen: set[str] = set()

    for c in res.citations:
        url = (c.url or "").strip()
        if not url:
            continue
        rank, kind = classify_url(url)
        if rank == 0:
            continue
        title = (c.title or "").strip()

        if rank == 1:
            # Direct ATS URL — derive (ats, slug) immediately.
            for ats, pat in _ATS_URL_PATTERNS.items():
                m = pat.search(url)
                if m:
                    slug = m.group(1)
                    key = f"{ats}:{slug}"
                    if key in seen:
                        break
                    seen.add(key)
                    # Heuristic: if the URL has a specific job ID we set
                    # direct_job_url too — downstream can choose direct
                    # import vs full board scrape. Lazy slug→name (slug
                    # may be a slug-with-dashes; orchestrator's dedup
                    # LLM call will normalize names later).
                    name_guess = slug.replace("-", " ").title()
                    candidates.append(CompanyCandidate(
                        name=name_guess,
                        url=url,
                        ats=ats,
                        slug=slug,
                        source="llm_web",
                        origin="query",
                    ))
                    break
            continue

        if rank == 2:
            # Direct job posting on company domain — pass through as a
            # direct-URL candidate. Downstream extracts company name.
            key = f"direct:{url}"
            if key in seen:
                continue
            seen.add(key)
            host = (urlparse(url).hostname or "").lower()
            if host.startswith("www."):
                host = host[4:]
            # Use citation title as best-guess name; orchestrator will
            # refine via LLM during dedup/scrape.
            name_guess = (
                title.split(" - ")[0].split(" | ")[0].strip()
                or host.split(".")[0].title()
                or "Unknown"
            )
            candidates.append(CompanyCandidate(
                name=name_guess,
                url=url,
                source="llm_web",
                direct_job_url=url,
                origin="query",
            ))
            continue

        if rank == 3:
            # Careers page — derive name from host, set careers URL for
            # Phase C to drill.
            host = (urlparse(url).hostname or "").lower()
            if host.startswith("www."):
                host = host[4:]
            # Strip common careers-subdomain prefixes so the name doesn't
            # come out as "Careers.acme.com".
            host = re.sub(r"^(careers|jobs|work|join)\.", "", host)
            root = host.split(".")[0]
            if not root:
                continue
            key = f"careers:{host}"
            if key in seen:
                continue
            seen.add(key)
            candidates.append(CompanyCandidate(
                name=root.title(),
                url=url,
                source="llm_web",
                origin="query",
            ))
            continue

    # Secondary pass — pull any other companies the model mentioned but
    # didn't cite. _llm_extract_companies expects the legacy
    # ``content`` field, so map snippets onto it. Rank-0 URLs are
    # excluded from this pass too (they're noise; the model's mention
    # of a company adjacent to a LinkedIn URL still tells us about the
    # company).
    secondary_input = [
        {"title": c.title or "", "url": c.url or "", "content": ""}
        for c in res.citations
        if c.url and classify_url(c.url)[0] != 0
    ]
    if secondary_input:
        try:
            extracted = await _llm_extract_companies(secondary_input, query)
            for ec in extracted:
                key = f"named:{ec.name.lower()}"
                if key in seen:
                    continue
                seen.add(key)
                # Only add as a name-only candidate (Phase C will
                # resolve); preserve origin="query".
                candidates.append(CompanyCandidate(
                    name=ec.name,
                    url=ec.url,
                    source="llm_web",
                    origin="query",
                ))
        except Exception:
            logger.exception(
                "Secondary _llm_extract_companies pass failed for query %r",
                query[:60],
            )

    return candidates


async def discover_via_llm_web(
    guidance: str,
    *,
    profile_data: dict | None = None,
    existing_companies: list[str] | None = None,
    past_queries: list[str] | None = None,
    evaluated: dict[str, str] | None = None,
    reference_context: str = "",
    locations: list[str] | None = None,
    min_salary: int | None = None,
    sources: list[str] | None = None,
    n_queries: int = 3,
    num_results: int = 10,
) -> list[CompanyCandidate]:
    """Generate N queries from guidance + profile, fan out to llm_web_search
    in parallel, classify every returned URL, return deduplicated
    candidates.

    Reuses ``_generate_queries`` from discovery.py for the query-gen
    step — the existing prompt already handles the guidance-vs-profile
    branching well. We cap at ``n_queries`` (default 3); the eval showed
    one query already produces ~8 actionable URLs, so 3 is a healthy
    diversity number without burning budget.
    """
    sources = sources or ["web", "greenhouse", "lever", "ashby"]

    queries = await _generate_queries(
        guidance or "",
        profile_data or {},
        existing_companies or [],
        past_queries or [],
        evaluated or {},
        0,                   # hits_so_far
        n_queries,           # target — caps generation
        sources,
        locations=locations,
        min_salary=min_salary,
        reference_context=reference_context,
    )
    # _generate_queries may return more than n if it includes literal-
    # seed queries on top of the LLM output; trim to keep the fan-out
    # bounded.
    queries = (queries or [])[:n_queries]
    if not queries:
        logger.warning("discover_via_llm_web: no queries generated, returning empty")
        return []

    logger.info("discover_via_llm_web fanning out %d queries", len(queries))

    tasks = [
        _one_query_to_candidates(
            q, locations=locations, min_salary=min_salary,
            num_results=num_results,
        )
        for q in queries
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Union + dedup. Same candidate may surface from multiple queries;
    # we keep the first occurrence (which carries the better URL — ATS
    # always sorts before careers because rank-1 candidates come from
    # the regex pass before rank-3 careers candidates within a single
    # _one_query_to_candidates).
    out: list[CompanyCandidate] = []
    seen_keys: set[str] = set()
    for r in results:
        if isinstance(r, Exception):
            logger.warning("LLM-web query raised: %s", r)
            continue
        for c in r:
            # Key prefers (ats, slug) when present, then direct_job_url,
            # then name-lower. Mirrors the orchestrator dedup intent so
            # we don't ship internal-collision candidates downstream.
            if c.ats and c.slug:
                key = f"{c.ats}:{c.slug}"
            elif c.direct_job_url:
                key = f"direct:{c.direct_job_url}"
            else:
                key = f"name:{c.name.lower().strip()}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            out.append(c)

    logger.info(
        "discover_via_llm_web: %d candidates from %d queries",
        len(out), len(queries),
    )
    return out


__all__ = [
    "classify_url",
    "discover_via_llm_web",
]
