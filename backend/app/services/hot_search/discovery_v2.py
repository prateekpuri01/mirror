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


# Recruiter-style discovery prompt. Picked after a 9-variant prompt eval
# (backend/scripts/eval/eval_discovery_research.py) and a 5-profile LLM-
# judge run: this prompt averaged 4.55 / 94.6% ≥4 across non-pivot
# scenarios, and remained the top performer on career-pivot scenarios
# (60-80% ≥4 in 4 of 5 profiles, vs. 5-42% for other variants). The
# "where would they thrive" framing implicitly handles pivots by
# reasoning about skill transfer rather than domain match — no special
# pivot-mode toggle needed.


def _format_profile_summary(profile: dict | None) -> str:
    """Compact candidate summary used as recruiter context."""
    if not profile:
        return "(no profile available)"
    parts: list[str] = []
    roles = [r.get("title", "") for r in profile.get("target_roles", []) if r.get("title")]
    if roles:
        parts.append(f"Target roles: {', '.join(roles[:5])}")
    domains = profile.get("domains", [])
    if domains:
        parts.append(f"Domains: {', '.join(domains[:5])}")
    skills = profile.get("skills", {}).get("technical", [])
    if skills:
        parts.append(f"Strong with: {', '.join(skills[:12])}")
    prefs = profile.get("search_preferences", {})
    looking_for = prefs.get("looking_for") or ""
    if looking_for:
        parts.append(f"Looking for: {looking_for}")
    not_looking_for = prefs.get("not_looking_for") or ""
    if not_looking_for:
        parts.append(f"Avoiding: {not_looking_for}")
    return "\n".join(parts) if parts else "(no profile data)"


def _format_work_history_for_prompt(profile: dict | None) -> str:
    """Last 3 jobs, most recent first."""
    if not profile:
        return ""
    wh = profile.get("work_history") or []
    if not wh:
        return ""
    lines = []
    for entry in wh[:3]:
        title = entry.get("title", "")
        emp = entry.get("employer", "")
        start = entry.get("start", "")
        end = entry.get("end") or "present"
        loc = entry.get("location", "")
        line = f"  {title} at {emp} ({start} – {end})"
        if loc:
            line += f", {loc}"
        lines.append(line)
    return "Recent work history:\n" + "\n".join(lines)


def _format_accomplishments_for_prompt(profile: dict | None, n: int = 5) -> str:
    """Top-N accomplishment titles."""
    if not profile:
        return ""
    cp = profile.get("complete_profile") or {}
    accs = cp.get("accomplishments") or []
    if not accs:
        return ""
    lines = []
    for a in accs[:n]:
        title = a.get("title", "") or a.get("id", "")
        if not title:
            continue
        tags = a.get("tags") or []
        tag_str = f" [{', '.join(tags[:3])}]" if tags else ""
        lines.append(f"  • {title}{tag_str}")
    if not lines:
        return ""
    return "Notable accomplishments:\n" + "\n".join(lines)


def _build_llm_web_query(
    *,
    guidance: str,
    profile: dict | None = None,
    locations: list[str] | None = None,
    min_salary: int | None = None,
) -> str:
    """Recruiter-style discovery prompt sent to llm_web_search.

    Frames the task as an executive recruiter placing the candidate.
    The "where would they thrive" framing forces the agent to reason
    about skill transfer (which is what makes it robust to pivots) and
    surfaces high-fit companies the candidate would actually want.

    Output format is "Name — why they're a fit" lines; the answer-text
    parser (``extract_companies_from_answer``) lifts these into
    name-only candidates that Phase C then resolves to ATS/careers
    URLs.

    See eval results commit f4e13e3 / 6060fdc for the numbers behind
    this choice.
    """
    sections: list[str] = []
    sections.append("ROLE")
    sections.append(
        "You are an executive recruiter who specializes in placing this candidate. "
        "Your task is to identify 15 companies that would be most excited to "
        "interview them right now and where they would thrive."
    )
    sections.append("")
    sections.append("CANDIDATE")
    if guidance and guidance.strip():
        sections.append(f"Search topic: \"{guidance.strip()}\"")
    else:
        sections.append(
            "The candidate hasn't specified a topic — infer from their target "
            "roles and domains below."
        )
    sections.append("")
    sections.append(_format_profile_summary(profile))
    wh = _format_work_history_for_prompt(profile)
    if wh:
        sections.append("")
        sections.append(wh)
    accs = _format_accomplishments_for_prompt(profile, n=5)
    if accs:
        sections.append("")
        sections.append(accs)
    sections.append("")
    sections.append("PREFERENCES (bias, not gate)")
    pref_lines = []
    if locations:
        pref_lines.append(f"Locations: {', '.join(locations)}")
    if min_salary:
        pref_lines.append(f"Min salary: ${min_salary:,}")
    sections.append("\n".join(pref_lines) if pref_lines else "(no location/salary preference)")
    sections.append("")
    sections.append("TASK")
    sections.append(
        "Pitch 15 companies that would value this candidate's background and "
        "are likely hiring for roles they would be interested in. For each, "
        "explain in one line why this candidate is a strong fit."
    )
    sections.append("")
    sections.append(
        "Use any sources — VC portfolios, news, LinkedIn company pages, "
        "industry roundups, listicles. You're not required to find job-posting "
        "URLs; naming the companies and explaining fit is the primary output."
    )
    sections.append("")
    sections.append(
        'Output one company per line: "Name — why they\'re a fit." '
        "No numbering, no extra prose."
    )
    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Company-name extraction from agent answer text.
#
# The recruiter prompt outputs lines like "Anthropic — frontier AI safety
# lab building Claude; needs ML researchers". We parse those into
# (name, context) pairs so the orchestrator can hand them to Phase C
# resolution as name-only candidates.
# ---------------------------------------------------------------------------


_BULLET_PREFIX_RE = re.compile(r"^\s*[-*•]?\s*")
_LEADING_NUM_RE = re.compile(r"^\s*\d+[.):]\s*")


def extract_companies_from_answer(text: str) -> list[dict]:
    """Pull "Name — context" lines out of the agent's answer text.

    Tolerant of leading bullets, numbering, and em-dash / en-dash /
    hyphen / colon separators. Returns ``[{"name": str, "context": str}, ...]``.
    Used both by the production discovery flow and the eval harness so
    behavior cannot drift between them.
    """
    out: list[dict] = []
    seen_names: set[str] = set()
    if not text:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        line = _LEADING_NUM_RE.sub("", line)
        line = _BULLET_PREFIX_RE.sub("", line)
        m = re.split(r"\s*[—–\-:]\s*", line, maxsplit=1)
        if len(m) == 2 and 2 <= len(m[0]) <= 80:
            name = m[0].strip().strip("*_`")
            context = m[1].strip()
            # Skip ALL-CAPS section headers ("TASK", "ROLE", etc.)
            if name.isupper() and len(name) <= 20:
                continue
            key = name.lower()
            if key in seen_names:
                continue
            seen_names.add(key)
            out.append({"name": name, "context": context[:300]})
    return out


# ---------------------------------------------------------------------------
# LLM-web discovery
# ---------------------------------------------------------------------------


async def _one_query_to_candidates(
    query: str,
    *,
    profile: dict | None = None,
    locations: list[str] | None,
    min_salary: int | None,
    num_results: int = 10,
    effort: str | None = None,
) -> list[CompanyCandidate]:
    """Run one recruiter-style LLM-web call and convert its output to
    candidates from TWO sources:

      1. Citation URLs — rank-1/2 yield candidates with the URL already
         attached (skip Phase C resolution), rank-3 carries the careers
         URL as ``url``. Rank-0 (aggregator/noise) is dropped.
      2. Company names extracted from the answer text via
         ``extract_companies_from_answer`` — these are the recruiter's
         actual recommendations and the primary output. They become
         name-only candidates that Phase C resolves to ATS/careers URLs.

    Both sources union into the candidate list, deduped on the
    (ats:slug | direct:url | name:lower) hierarchy used elsewhere in
    the pipeline.
    """
    full_query = _build_llm_web_query(
        guidance=query,
        profile=profile,
        locations=locations,
        min_salary=min_salary,
    )

    res = await llm_web_search(full_query, num_results=num_results, effort=effort)
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

    # Primary output of the recruiter prompt is the agent's answer
    # text — "Name — why they're a fit" lines. Parse them and add as
    # name-only candidates that Phase C will resolve. Dedupe against
    # the citation-derived candidates by name.
    answer_companies = extract_companies_from_answer(res.answer or "")
    for ac in answer_companies:
        name = (ac.get("name") or "").strip()
        if not name or len(name) < 2:
            continue
        # Skip if a citation-derived candidate already covers this name.
        norm = name.lower()
        if f"named:{norm}" in seen:
            continue
        # Also skip near-collisions with slug-derived candidates (slug
        # is title-cased so "Anthropic" from a citation vs "Anthropic"
        # from the answer would dedupe naturally; this catches the
        # spacing-variant case: "Manifold Bio" vs "Manifoldbio").
        # The orchestrator's post-resolution dedup catches the rest
        # once ATS slugs are known.
        collapsed = norm.replace(" ", "").replace("-", "")
        if any(c.name.lower().replace(" ", "").replace("-", "") == collapsed for c in candidates):
            continue
        seen.add(f"named:{norm}")
        candidates.append(CompanyCandidate(
            name=name,
            url=None,
            source="llm_web",
            origin="query",
        ))

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
    effort: str | None = None,
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
            q,
            profile=profile_data,
            locations=locations,
            min_salary=min_salary,
            num_results=num_results,
            effort=effort,
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
