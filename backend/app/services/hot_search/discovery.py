"""Hot Search — Discovery layer.

Query generation, web search wrappers, candidate extraction, slug
harvesting from aggregator entries, ATS slug probing, careers-URL
discovery, and profile/reference loading. Pure inputs → candidate stream
out. No per-candidate evaluation lives here (see `evaluation.py`).
"""

import asyncio
import json
import logging
import re

import httpx
from sqlalchemy import select

from app.config import settings
from app.database import async_session
from app.models.companies import Company
from app.models.jobs import Job
from app.models.profile import UserProfile
from app.scrapers import SCRAPERS_BY_ATS  # noqa: F401  (re-export for callers)
from app.services.company_discovery import (
    _build_keyword_sets,
    _verify_ats_slug,
    score_job_relevance,  # noqa: F401  (re-exported for downstream)
)
from app.services.hot_search.llm_helpers import _openai_chat, _parse_json_array
from app.services.hot_search.types import CompanyCandidate

logger = logging.getLogger(__name__)


def _remap_results(raw: list[dict]) -> list[dict]:
    """Convert the unified web_search format ({snippet}) to legacy ({content})."""
    return [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "content": r.get("snippet", ""),
        }
        for r in raw
    ]


async def _discovery_search(query: str, max_results: int = 10) -> list[dict]:
    """Discovery search — Perplexity → SearXNG → Brave.

    Used for broad company-finding queries. Cascades through whichever
    backends are configured (Perplexity preferred; falls back to SearXNG
    then Brave).
    """
    from app.services.web_search import web_search

    return _remap_results(await web_search(query, num_results=max_results))


async def _precise_search(query: str, max_results: int = 10) -> list[dict]:
    """Precision search — SearXNG → Brave only (skip Perplexity).

    Used for targeted queries that need keyword precision: site: operators,
    ATS slug probing, careers-page finding, domain-scoped job drilling.
    Perplexity can't handle these because it's an LLM, not a search index.
    """
    from app.services.web_search import web_search_precise

    return _remap_results(await web_search_precise(query, num_results=max_results))


# ---------------------------------------------------------------------------
# OpenAI helper
# ---------------------------------------------------------------------------


_QUERY_SYSTEM_PROMPT = """\
You generate diverse web search queries to discover companies AND specific jobs \
for the user.

## Priority rules (read carefully)

1. If the user provides an explicit search instruction (guidance), that is the \
   PRIMARY intent. Every query you generate MUST directly serve that instruction. \
   The user's background profile is secondary context only — use it to refine or \
   disambiguate the instruction, NOT to override it.

   Example: If the user says "Microsoft AI Engineer", ALL queries must be about \
   Microsoft AND AI Engineer roles. Do NOT generate queries about other companies \
   or other role types, even if they match the user's profile better.

2. If there is NO explicit instruction, use the profile to find relevant companies \
   and roles.

3. The user's avoidances are always hard constraints — never generate queries \
   that would find things they said to avoid.

## Query strategies

Generate a MIX of query types (all aligned with the user's primary intent):
- **Company discovery**: "top NLP startups 2026", "companies working on LLM evaluation"
- **Funding/news**: "Series A B AI governance startup funding 2026"
- **Hiring signals**: "hiring machine learning researcher remote 2026"
- **Direct job search at non-ATS companies** (Microsoft, Google, Apple, Amazon, \
   Meta, Netflix, IBM): "site:careers.microsoft.com machine learning", \
   "Microsoft LLM research scientist jobs", "site:jobs.apple.com AI researcher". \
   These surface individual job postings we can import directly. USE THESE WHEN \
   THE USER EXPLICITLY MENTIONS A NON-ATS COMPANY BY NAME.
- **ATS-specific** (only if enabled): "site:boards.greenhouse.io data scientist NLP"

Return ONLY a JSON array of 4-6 search query strings."""


_QUERY_USER_TEMPLATE = """\
## PRIMARY USER INSTRUCTION

{guidance}

{guidance_emphasis}

---

## User Profile (secondary context — refine the primary instruction, don't override it)
Target roles: {role_titles}
Domains: {domains}
Technical skills: {skills}
What I'm looking for: {looking_for}
What I want to avoid: {not_looking_for}
Remote preference: {remote_pref}

## Enabled Sources
{sources_desc}

## Session Context
Queries already tried: {past_queries}
Companies found so far: {evaluated_summary}
Hits: {hit_count} / {target}

## Companies Already In DB (skip these)
{existing_companies}

## Location Preference
{location_section}

## Salary Preference
{salary_section}

## Reference Jobs (find similar)
{reference_section}

Generate 4-6 NEW, diverse search queries. If the user provided an explicit \
instruction above, ALL queries must directly serve that instruction. Vary the \
query style and approach, but keep the subject matter on target."""


async def _generate_queries(
    guidance: str,
    profile_data: dict,
    existing_companies: list[str],
    past_queries: list[str],
    evaluated: dict[str, str],
    hit_count: int,
    max_hits: int,
    sources: list[str],
    locations: list[str] | None = None,
    min_salary: int | None = None,
    reference_context: str = "",
) -> list[str]:
    """Use LLM to generate diverse search queries."""
    role_titles = [r.get("title", "") for r in profile_data.get("target_roles", [])]
    domains = profile_data.get("domains", [])
    skills = profile_data.get("skills", {}).get("technical", [])[:10]
    prefs = profile_data.get("search_preferences", {})
    # Prefer new free-text fields, fall back to legacy
    looking_for = prefs.get("looking_for", "")
    not_looking_for = prefs.get("not_looking_for", "")
    if not looking_for:
        industries = prefs.get("industries_ranked", [])
        nice_to_haves = prefs.get("nice_to_haves", [])
        looking_for = ", ".join(industries + nice_to_haves) if (industries or nice_to_haves) else ""
    if not not_looking_for:
        deal_breakers_legacy = prefs.get("deal_breakers", [])
        anti_patterns = prefs.get("org_anti_patterns", [])
        not_looking_for = ", ".join(deal_breakers_legacy + anti_patterns) if (deal_breakers_legacy or anti_patterns) else ""
    personal = profile_data.get("personal", {})
    remote_pref = personal.get("remote_preference", "any")

    sources_desc = []
    if "web" in sources:
        sources_desc.append("General web search (find companies, not job boards)")
    if "greenhouse" in sources:
        sources_desc.append("Greenhouse ATS (use site:boards.greenhouse.io)")
    if "lever" in sources:
        sources_desc.append("Lever ATS (use site:jobs.lever.co)")
    if "ashby" in sources:
        sources_desc.append("Ashby ATS (use site:jobs.ashbyhq.com)")

    eval_summary = ", ".join(
        f"{name}({outcome})" for name, outcome in list(evaluated.items())[-20:]
    )

    location_section = (
        f"HARD CONSTRAINT: every query MUST include a location term from "
        f"{{{', '.join(locations)}}} (or a clear alias). Do NOT generate queries "
        f"that would surface jobs outside these locations."
        if locations else "(no location preference)"
    )
    salary_section = (
        f"HARD CONSTRAINT: target roles paying at least ${min_salary // 1000}K+. "
        f"When the ATS supports it, include a salary or seniority qualifier in the "
        f"query so we don't waste candidates on roles that can't meet the bar."
        if min_salary else "(no salary preference)"
    )
    reference_section = reference_context or "(no reference jobs)"

    if guidance and guidance.strip():
        guidance_text = guidance.strip()
        guidance_emphasis = (
            "**Every query you generate MUST be directly about the instruction above.** "
            "The profile section below is only for refinement/disambiguation — do not "
            "pivot away from the primary instruction to match the profile instead."
        )
    elif reference_context and reference_context != "(no reference jobs)":
        # The user selected reference jobs but didn't type guidance. Synthesize
        # a clear instruction from the reference jobs so they steer the search
        # instead of being buried at the bottom of the prompt.
        guidance_text = (
            "Find companies with jobs SIMILAR to the reference jobs listed below. "
            "Focus on the same role types, seniority level, and domain.\n\n"
            + reference_context
        )
        guidance_emphasis = (
            "**Every query you generate MUST target roles similar to these reference jobs.** "
            "The user's profile is secondary context — do not pivot away from the "
            "reference job types to chase unrelated profile interests."
        )
    else:
        guidance_text = "(no explicit instruction — use the profile below to drive queries)"
        guidance_emphasis = ""

    user_prompt = _QUERY_USER_TEMPLATE.format(
        role_titles=", ".join(role_titles),
        domains=", ".join(domains),
        skills=", ".join(skills),
        looking_for=looking_for or "(no specific preference)",
        not_looking_for=not_looking_for or "(none)",
        remote_pref=remote_pref,
        guidance=guidance_text,
        guidance_emphasis=guidance_emphasis,
        sources_desc="\n".join(sources_desc),
        past_queries=json.dumps(past_queries[-15:]) if past_queries else "[]",
        evaluated_summary=eval_summary or "(none yet)",
        hit_count=hit_count,
        target=max_hits,
        existing_companies=", ".join(existing_companies[:100]) or "(none)",
        location_section=location_section,
        salary_section=salary_section,
        reference_section=reference_section,
    )

    # When the user gave explicit guidance (or selected reference jobs), use a
    # lower temperature so the LLM sticks to the intent instead of wandering.
    has_direction = bool(guidance and guidance.strip()) or (
        reference_context and reference_context != "(no reference jobs)"
    )
    temp = 0.4 if has_direction else 0.8

    logger.info("Generating queries with guidance: %s (temp=%.1f)", guidance or "(none)", temp)
    content = await _openai_chat(_QUERY_SYSTEM_PROMPT, user_prompt, temperature=temp)
    if content:
        queries = _parse_json_array(content)
        if queries:
            result = [q for q in queries if isinstance(q, str)][:6]
            logger.info("LLM generated queries: %s", result)
            # When guidance is provided, guarantee at least 1 literal query
            # from the user's words so we don't completely drift into profile
            # territory. Prepend it; the LLM's creative queries follow.
            if guidance and guidance.strip():
                result = _seed_literal_queries(guidance, sources, past_queries, result)
            return result

    result = _fallback_queries(guidance, profile_data, sources, past_queries)
    logger.info("Fallback queries: %s", result)
    return result


def _seed_literal_queries(
    guidance: str,
    sources: list[str],
    past_queries: list[str],
    llm_queries: list[str],
) -> list[str]:
    """Prepend 1-2 near-literal queries from the user's guidance text.

    Guarantees at least some queries directly reflect what the user typed,
    regardless of how far the LLM drifts toward profile-driven topics.
    """
    g = guidance.strip()
    past_set = set(past_queries)
    seeds: list[str] = []

    # A straightforward web query using the user's own words
    literal_web = f"{g} jobs hiring 2026"
    if literal_web not in past_set:
        seeds.append(literal_web)

    # An ATS site: query if any ATS source is enabled
    for ats_domain in ["boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com"]:
        ats_type = ats_domain.split(".")[0]  # "boards" → check "greenhouse"
        ats_name = {"boards": "greenhouse", "jobs": ats_domain.split(".")[1]}.get(ats_type, "")
        if ats_name in sources or ats_type in sources:
            q = f"site:{ats_domain} {g}"
            if q not in past_set:
                seeds.append(q)
                break

    if not seeds:
        return llm_queries

    # Deduplicate: drop any LLM queries that are near-identical to seeds
    g_lower = g.lower()
    deduped = [
        q for q in llm_queries
        if q.lower().strip() not in {s.lower().strip() for s in seeds}
    ]
    return (seeds + deduped)[:6]


def _fallback_queries(
    guidance: str,
    profile_data: dict,
    sources: list[str],
    past_queries: list[str],
) -> list[str]:
    """Generate basic queries without LLM. Guidance takes priority when provided."""
    role_titles = [r.get("title", "") for r in profile_data.get("target_roles", [])]
    domains = profile_data.get("domains", [])
    queries = []

    if guidance and guidance.strip():
        # Guidance mode: every query derives from the user's instruction
        g = guidance.strip()
        if "web" in sources:
            queries.append(f"{g} jobs 2026")
            queries.append(f"{g} careers hiring")
            queries.append(f"{g} open positions")
        if "greenhouse" in sources:
            queries.append(f"site:boards.greenhouse.io {g}")
        if "lever" in sources:
            queries.append(f"site:jobs.lever.co {g}")
        if "ashby" in sources:
            queries.append(f"site:jobs.ashbyhq.com {g}")
    else:
        # Profile-driven mode
        base = " ".join(role_titles[:2]) or "research scientist"
        if "web" in sources:
            queries.append(f"top companies {base} 2026")
            if domains:
                queries.append(f"{domains[0]} startups hiring 2026")
        if "greenhouse" in sources:
            queries.append(f"site:boards.greenhouse.io {base}")
        if "lever" in sources:
            queries.append(f"site:jobs.lever.co {base}")
        if "ashby" in sources:
            queries.append(f"site:jobs.ashbyhq.com {base}")

    past_set = set(past_queries)
    queries = [q for q in queries if q not in past_set]
    return queries or [f"emerging companies {(guidance or role_titles[0] if role_titles else '').strip() or 'research'}"]


# ---------------------------------------------------------------------------
# Phase 1b: LLM entity extraction — pull company names from search results
# ---------------------------------------------------------------------------


_EXTRACT_SYSTEM = """\
Extract company/organization names from search results. Return a JSON array of \
objects: [{"name": "Company Name", "url": "https://...", "context": "brief reason"}].

Rules:
- Only include actual companies or organizations, NOT job boards (Indeed, LinkedIn, \
Glassdoor, ZipRecruiter, Monster, etc.), news sites, or aggregators.
- Do not include generic terms like "Job", "Careers", "Hiring".
- Include the most specific URL for the company (their website, not a job board listing).
- If the same company appears multiple times, include it only once.
- Include 0-10 companies. Return [] if no real companies are found."""


_EXTRACT_USER_TEMPLATE = """\
Extract company names from these search results. Skip job boards and aggregators.

Search query: {query}

Results:
{results_text}

Return ONLY a JSON array of objects with "name", "url", and "context" fields."""


_ATS_URL_PATTERNS = {
    "greenhouse": re.compile(r"boards\.greenhouse\.io/([^/?\s]+)"),
    "lever": re.compile(r"jobs\.lever\.co/([^/?\s]+)"),
    "ashby": re.compile(r"jobs\.ashbyhq\.com/([^/?\s]+)"),
}


def _ats_url_has_specific_job(url: str, ats: str) -> bool:
    """Return True if an ATS URL points to a specific job posting rather
    than a company listing page.

    Formats:
      Greenhouse: boards.greenhouse.io/{slug}/jobs/{numeric_id}
      Lever:      jobs.lever.co/{slug}/{uuid}
      Ashby:      jobs.ashbyhq.com/{slug}/{uuid}

    When a site: query (e.g. `site:boards.greenhouse.io ml engineer`)
    returns a URL with a job ID, we want to direct-import THAT specific
    job rather than scrape the whole company's board. The user's search
    literally pointed to this posting — that's a strong signal we should
    honor instead of losing it in a full-board scrape.
    """
    try:
        from urllib.parse import urlparse
        parts = urlparse(url).path.strip("/").split("/")
        if ats == "greenhouse":
            # /{slug}/jobs/{id}
            return (
                len(parts) >= 3
                and parts[1].lower() == "jobs"
                and parts[2].isdigit()
            )
        if ats in ("lever", "ashby"):
            # /{slug}/{uuid}
            if len(parts) < 2:
                return False
            return bool(re.match(
                r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                parts[1],
                re.IGNORECASE,
            ))
        return False
    except Exception:
        return False

# Domains to skip — job boards, aggregators, news sites

_SKIP_DOMAINS = {
    "indeed.com", "linkedin.com", "glassdoor.com", "ziprecruiter.com",
    "monster.com", "dice.com", "simplyhired.com", "careerbuilder.com",
    "builtin.com", "builtinnyc.com", "builtinla.com", "builtinsf.com",
    "builtinchicago.org", "builtinaustin.com", "builtinboston.com",
    "builtincolorado.com", "builtinseattle.com",
    "themuse.com", "flexjobs.com", "workatastartup.com", "remoteok.com",
    "wellfound.com", "angel.co", "ycombinator.com",
    "news.ycombinator.com", "reddit.com", "twitter.com", "x.com",
    "medium.com", "wikipedia.org", "youtube.com", "github.com",
    "crunchbase.com", "pitchbook.com", "techcrunch.com",
    # Video hosts — some careers pages embed Vimeo/YouTube testimonials
    # whose URL paths end with numeric IDs, which falsely match our
    # generic /\d{6,}/?$ pattern for job URLs.
    "vimeo.com", "player.vimeo.com", "youtu.be",
}


def _is_skip_domain(url: str) -> bool:
    """Check if URL is from a domain we should skip."""
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
        host = re.sub(r"^www\.", "", host)
        return any(host == d or host.endswith(f".{d}") for d in _SKIP_DOMAINS)
    except Exception:
        return False


# Patterns that suggest a URL points to a SPECIFIC job posting (not a listing/search page).
# Require a numeric job ID or UUID-like identifier to avoid matching category pages
# like /jobs/remote-data-science or /jobs/seattle-wa.

_DIRECT_JOB_URL_PATTERNS = [
    # Numeric IDs: /job/12345, /jobs/12345, /jobs/12345/title, /jobs/results/12345
    re.compile(r"/job[s]?/(?:results/)?\d{3,}(?:/[^/?]*)?$", re.IGNORECASE),
    re.compile(r"/careers/\d{3,}(?:/[^/?]*)?$", re.IGNORECASE),
    re.compile(r"/positions?/\d{3,}(?:/[^/?]*)?$", re.IGNORECASE),
    re.compile(r"/openings?/\d{3,}(?:/[^/?]*)?$", re.IGNORECASE),
    re.compile(r"/requisition[s]?/\d{3,}", re.IGNORECASE),
    re.compile(r"/details/\d{3,}", re.IGNORECASE),              # Apple-style
    re.compile(r"/listing/[^/?]+/\d{3,}", re.IGNORECASE),       # Stripe-style
    # Microsoft-style: /job/1234567/Senior-ML-Engineer (ID must be numeric, usually long)
    re.compile(r"/job/\d{4,}/[^/?]+", re.IGNORECASE),
    # Ashby-style UUID: /jobs/f760ffd1-3795-4fb1-bb33-8478d4bab7f6
    re.compile(r"/jobs?/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE),
    # Workday-style: /job/Location/Title_R12345 or /job/Location/Title_JR12345
    # The ID is after an underscore at the end of a slug, format _R123456 or _JR123456
    re.compile(r"/job/.+?_[A-Z]?[RJ]?\d{4,}", re.IGNORECASE),
    # Workday with en-US locale: /en-US/External/job/Location/Title_R12345
    re.compile(r"/(?:en-\w+/)?[^/]+/job/.+?_\d{4,}", re.IGNORECASE),
    # Taleo-style: /job/Location/Title/12345 (numeric ID as last path segment)
    re.compile(r"/job(?:s)?/[^/]+/[^/]+/\d{4,}$", re.IGNORECASE),
    # Alphanumeric IDs with a letter prefix: /job/R167657/Title or /careers/JR12345/...
    re.compile(r"/(?:job|careers|positions?)/[A-Z]{1,2}\d{4,}", re.IGNORECASE),
    # Generic: any URL path ending with a numeric ID >= 6 digits (catches many career portals)
    re.compile(r"/\d{6,}/?$", re.IGNORECASE),
]

# Domains where we won't try direct-URL import (too much noise or duplicates ATS path)

_DIRECT_URL_SKIP_DOMAINS = _SKIP_DOMAINS | {
    "boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com",
    "jobs.eightfold.ai", "careers.eightfold.ai",
}


def _looks_like_direct_job_url(url: str) -> bool:
    """Heuristic: does this URL look like a specific job posting page?"""
    if not url or not url.startswith(("http://", "https://")):
        return False
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        host = re.sub(r"^www\.", "", host)
        if any(host == d or host.endswith(f".{d}") for d in _DIRECT_URL_SKIP_DOMAINS):
            return False
        path = parsed.path or ""
        return any(pat.search(path) for pat in _DIRECT_JOB_URL_PATTERNS)
    except Exception:
        return False


# Relaxed patterns used as a fallback when the browser agent exhausts rounds
# without finding strict-pattern matches. These accept more shapes of
# career-portal URLs (query-string IDs, short numeric IDs, lettered req IDs).
# Safety: the caller MUST verify each URL via extract_job_from_url, which fails
# cleanly on non-job pages, so being more permissive here is low-risk.

_RELAXED_JOB_URL_PATTERNS = [
    # Query-string IDs common on enterprise portals: ?id=R00324543, ?jobId=12345
    re.compile(r"[?&](?:id|jobId|reqId|job_id|requisitionId)=[A-Z]{0,3}\d{4,}", re.IGNORECASE),
    # /careers/<slug>/<id> or /careers/apply/<id> with shorter IDs (4+)
    re.compile(r"/careers?/(?:apply/|view/|details?/)?[\w-]*\d{4,}", re.IGNORECASE),
    # /jobs/<slug>-<id> — slug-with-id style (CoxEnterprises, Accenture)
    re.compile(r"/jobs?/[\w-]+-\d{4,}", re.IGNORECASE),
    # Any URL with /job/ or /jobs/ or /careers/ AND a non-trivial trailing segment
    # (excludes plain /jobs or /jobs/search which are listing pages, not postings)
    re.compile(r"/(?:job|career)s?/[^/?]{5,}$", re.IGNORECASE),
]


def _looks_like_job_url_relaxed(url: str) -> bool:
    """Permissive job-URL matcher for the browser-agent fallback.

    Accepts strict matches plus query-string IDs, slug-based IDs, and
    longer trailing path segments. Intended as a second-chance filter
    after the agent exhausts its rounds without finding strict matches.
    """
    if _looks_like_direct_job_url(url):
        return True
    if not url or not url.startswith(("http://", "https://")):
        return False
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        host = re.sub(r"^www\.", "", host)
        if any(host == d or host.endswith(f".{d}") for d in _DIRECT_URL_SKIP_DOMAINS):
            return False
        # Check full URL (path + query) since some matchers look at query
        target = (parsed.path or "") + (f"?{parsed.query}" if parsed.query else "")
        return any(pat.search(target) for pat in _RELAXED_JOB_URL_PATTERNS)
    except Exception:
        return False


async def _extract_candidates_from_results(
    results: list[dict], query: str
) -> list[CompanyCandidate]:
    """Three-pronged extraction:
      1. Regex for ATS URLs (fast, reliable)
      2. Direct job-posting URLs (fetch + LLM extract per job)
      3. LLM company extraction from remaining results
    """
    candidates: list[CompanyCandidate] = []
    seen: set[str] = set()
    non_ats_results: list[dict] = []

    # First pass: extract direct ATS slugs from URLs (fast, reliable)
    for r in results:
        url = r.get("url", "")

        # Check for ATS URL first
        matched_ats = False
        for ats, pattern in _ATS_URL_PATTERNS.items():
            m = pattern.search(url)
            if m:
                slug = m.group(1)
                name = slug.replace("-", " ").title()
                source = ats if "site:" in query else "web"

                # If the URL has a specific job ID (e.g. `.../mozilla/jobs/7114644`),
                # route it through the direct-import path so we fetch JUST
                # that job instead of scraping the full company board. The
                # user's search query matched THIS posting — preserve the
                # signal instead of losing it in a generic board scrape.
                if _ats_url_has_specific_job(url, ats):
                    key = f"direct:{url}"
                    if key not in seen:
                        seen.add(key)
                        candidates.append(CompanyCandidate(
                            name=name, url=url, ats=ats, slug=slug,
                            source=source,
                            direct_job_url=url,
                        ))
                else:
                    key = f"{ats}:{slug}"
                    if key not in seen:
                        seen.add(key)
                        candidates.append(CompanyCandidate(
                            name=name, url=url, ats=ats, slug=slug,
                            source=source,
                        ))
                matched_ats = True
                break

        if matched_ats:
            continue

        # Check if this looks like a direct job posting URL (non-ATS)
        if _looks_like_direct_job_url(url):
            key = f"direct:{url}"
            if key not in seen:
                seen.add(key)
                # Use the result title as a placeholder name; actual company/title
                # will be extracted from the page content during evaluation
                name_guess = r.get("title", "").split(" - ")[0].split(" | ")[0].strip() or "Unknown"
                candidates.append(CompanyCandidate(
                    name=name_guess,
                    url=url,
                    source="direct_url",
                    direct_job_url=url,
                ))
            continue

        # Not an ATS URL and not a direct job URL — collect for LLM company extraction
        if not _is_skip_domain(url):
            non_ats_results.append(r)

    # Second pass: LLM extracts company names from non-ATS results
    if non_ats_results:
        llm_candidates = await _llm_extract_companies(non_ats_results, query)
        logger.info(
            "LLM extracted %d companies from %d non-ATS results: %s",
            len(llm_candidates), len(non_ats_results),
            [c.name for c in llm_candidates],
        )
        for c in llm_candidates:
            key = c.name.lower()
            if key not in seen and len(key) > 1:
                seen.add(key)
                candidates.append(c)

    return candidates


async def _llm_extract_companies(
    results: list[dict], query: str
) -> list[CompanyCandidate]:
    """Use LLM to extract company names from web search results."""
    # Build a concise text representation of results
    results_text = ""
    for i, r in enumerate(results[:15], 1):
        title = r.get("title", "")
        url = r.get("url", "")
        snippet = (r.get("content", "") or "")[:200]
        results_text += f"{i}. [{title}]({url})\n   {snippet}\n\n"

    if not results_text.strip():
        return []

    user_prompt = _EXTRACT_USER_TEMPLATE.format(
        query=query, results_text=results_text,
    )
    content = await _openai_chat(_EXTRACT_SYSTEM, user_prompt, temperature=0.3)
    if not content:
        return []

    parsed = _parse_json_array(content)
    if not parsed:
        return []

    candidates = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        url = (item.get("url") or "").strip() or None
        if not name or len(name) < 2:
            continue
        # Skip obviously bad names
        if name.lower() in {"job", "jobs", "careers", "hiring", "company", "home"}:
            continue
        candidates.append(CompanyCandidate(
            name=name, url=url, source="web",
        ))

    return candidates


async def _harvest_candidates_from_entries(
    entries: list,
    http_client: httpx.AsyncClient,
    *,
    seen: set[str],
    existing_companies_lower: set[str],
) -> list[CompanyCandidate]:
    """Convert aggregator entries into CompanyCandidate records.

    Strategy per entry:
      1. URL → ATS slug via _ATS_URL_PATTERNS (gives full-board scrape).
      2. Else, name probe via _probe_name_for_ats (validated against live API).
      3. Else, direct-URL fallback (single-job preview, verified by Lane A).

    Unlike `_extract_candidates_from_results`, this path *always* prefers a
    comprehensive scrape over single-job direct-import when an ATS slug is
    found. The aggregator entry is a discovery signal — the user didn't
    point at this specific posting, they pointed at the search criteria.

    `seen` and `existing_companies_lower` are shared dedup sets passed by
    the caller so harvested candidates don't collide with web-discovered
    ones or with companies already tracked in the user's DB.

    Probes run in parallel with bounded concurrency. Sequential probing
    was the cause of multi-minute "Harvesting..." stalls before the first
    candidate event reached the SSE stream: ~100 entries × ~15 ATS probes
    per name name-probe makes 1,500+ serial HTTP calls. Parallel-10 cuts
    that to seconds while still respecting Ashby's rate-limit budget.
    """
    sem = asyncio.Semaphore(10)

    async def _resolve_entry(
        entry,
    ) -> tuple[str | None, str | None, object] | None:
        """Returns (ats, slug, entry) for ATS-matched entries; (None, None, entry)
        for direct-URL fallbacks; None if the entry has no usable URL."""
        url = (entry.job_url or "").strip()
        if not url:
            return None

        # 1. URL → ATS slug (fast, no HTTP)
        for ats, pattern in _ATS_URL_PATTERNS.items():
            m = pattern.search(url)
            if m:
                return (ats, m.group(1), entry)

        # 2. Name probe — the slow part. Bounded by `sem` so we don't
        # bury the ATS APIs (especially Ashby) with concurrent verifies.
        if entry.company_name:
            async with sem:
                probed = await _probe_name_for_ats(entry.company_name, http_client)
            if probed:
                return (probed[0], probed[1], entry)

        # 3. Direct-URL fallback (no probe needed)
        return (None, None, entry)

    results = await asyncio.gather(
        *[_resolve_entry(e) for e in entries],
        return_exceptions=False,
    )

    candidates: list[CompanyCandidate] = []
    for res in results:
        if res is None:
            continue
        ats, slug, entry = res
        url = (entry.job_url or "").strip()

        if ats is not None and slug is not None:
            key = f"{ats}:{slug}"
            if key in seen:
                continue
            if (entry.company_name or "").lower() in existing_companies_lower:
                continue
            seen.add(key)
            candidates.append(CompanyCandidate(
                name=entry.company_name,
                url=url,
                ats=ats,
                slug=slug,
                source=entry.source,
                origin="aggregator",
            ))
            continue

        # Direct-URL fallback. Lane A's verifier in _extract_direct_job_url
        # will hard-reject if filters don't match.
        key = f"direct:{url}"
        if key in seen:
            continue
        seen.add(key)
        candidates.append(CompanyCandidate(
            name=entry.company_name or "Unknown",
            url=url,
            ats=None,
            slug=None,
            source=entry.source,
            direct_job_url=url,
            origin="aggregator",
        ))

    # Reorder so ATS-resolved candidates run before direct-URL fallbacks.
    # Without this, the per-run direct-URL cap fills first-come-first-served
    # with whatever HN spat out first — crowding out higher-leverage ATS
    # scrapes that would have produced 10-40 jobs each.
    candidates.sort(key=lambda c: 0 if c.ats and c.slug else 1)

    return candidates


# ---------------------------------------------------------------------------
# Phase 2: ATS probing — resolve company name → ATS slug
# ---------------------------------------------------------------------------


def _slug_candidates_from_name(name: str) -> list[str]:
    """Generate plausible ATS slugs from a company name.

    "Acme Health AI" → ["acmehealthai", "acme-health-ai", "acmehealth", "acme-health", "acme"]
    """
    # Normalize
    clean = re.sub(r"[^a-z0-9\s]", "", name.lower()).strip()
    words = clean.split()
    if not words:
        return []

    candidates: list[str] = []

    def _add(s: str):
        s = s.strip().lower()
        if s and s not in candidates and len(s) >= 2:
            candidates.append(s)

    # Full name joined
    _add("".join(words))
    _add("-".join(words))

    # Without common suffixes (inc, ai, labs, io, tech, hq)
    suffixes = {"inc", "ai", "labs", "lab", "io", "tech", "hq", "co", "corp", "llc"}
    if len(words) > 1 and words[-1] in suffixes:
        core = words[:-1]
        _add("".join(core))
        _add("-".join(core))

    # First word alone (if multi-word)
    if len(words) > 1:
        _add(words[0])

    # First two words
    if len(words) > 2:
        _add("".join(words[:2]))
        _add("-".join(words[:2]))

    return candidates


async def _probe_name_for_ats(
    name: str, http_client: httpx.AsyncClient
) -> tuple[str, str] | None:
    """Try slug candidates derived from company name against all 3 ATS APIs.
    Returns (ats, slug) or None."""
    slugs = _slug_candidates_from_name(name)
    if not slugs:
        return None

    for ats in ("greenhouse", "lever", "ashby"):
        for slug in slugs:
            if await _verify_ats_slug(ats, slug, http_client):
                logger.info("Probed name '%s' → %s/%s", name, ats, slug)
                return (ats, slug)

    return None


def _slug_plausible_for_name(slug: str, name: str) -> bool:
    """Check if an ATS slug plausibly belongs to a company name.

    Prevents "McKinsey" from matching slug "openai" just because the search
    returned an unrelated ATS URL.
    """
    slug_lower = slug.lower().replace("-", "")
    name_lower = re.sub(r"[^a-z0-9]", "", name.lower())
    # Any word from the name (≥3 chars) appears in the slug, or vice versa
    name_words = [w for w in re.findall(r"[a-z0-9]+", name.lower()) if len(w) >= 3]
    if any(w in slug_lower for w in name_words):
        return True
    if any(w in name_lower for w in re.findall(r"[a-z0-9]+", slug_lower) if len(w) >= 3):
        return True
    return False


async def _search_careers_url(name: str) -> tuple[str, str] | None:
    """Fallback: search the web for a company's careers/jobs page.

    Only accepts ATS URLs where the slug plausibly matches the company name.
    Skips the heavy resolve_job_url pipeline to avoid runaway probing.

    Returns (ats, slug) or None.
    """
    query = f'"{name}" careers jobs site:boards.greenhouse.io OR site:jobs.lever.co OR site:jobs.ashbyhq.com'
    results = await _precise_search(query, max_results=5)

    for r in results:
        url = r.get("url", "")
        for ats, pattern in _ATS_URL_PATTERNS.items():
            m = pattern.search(url)
            if m:
                slug = m.group(1)
                if _slug_plausible_for_name(slug, name):
                    logger.info(
                        "Careers search for '%s' found matching ATS URL: %s/%s",
                        name, ats, slug,
                    )
                    return (ats, slug)
                else:
                    logger.debug(
                        "Careers search for '%s' found ATS URL %s/%s but slug doesn't match, skipping",
                        name, ats, slug,
                    )

    return None


# Heuristics for picking a careers page from arbitrary URLs

_CAREERS_PATH_HINTS = (
    "/careers", "/jobs", "/join-us", "/work-with-us", "/positions",
    "/openings", "/career", "/opportunities",
)

_CAREERS_DOMAIN_HINTS = ("careers.", "jobs.", "work.", "join.")


def _looks_like_careers_url(url: str) -> bool:
    """Heuristic check: does this URL look like a company careers page?"""
    if not url:
        return False
    url_lower = url.lower()
    # Skip ATS domains — those are handled by _search_careers_url already
    for ats_pattern in _ATS_URL_PATTERNS.values():
        if ats_pattern.search(url):
            return False
    # Skip job aggregators / news sites
    skip_domains = (
        "linkedin.com", "indeed.com", "glassdoor.com", "wellfound.com",
        "ycombinator.com", "techcrunch.com", "bloomberg.com", "forbes.com",
        "wikipedia.org", "crunchbase.com", "google.com/search", "reddit.com",
    )
    if any(d in url_lower for d in skip_domains):
        return False
    if any(hint in url_lower for hint in _CAREERS_PATH_HINTS):
        return True
    if any(hint in url_lower for hint in _CAREERS_DOMAIN_HINTS):
        return True
    return False


def _domain_plausible_for_company(url: str, company_name: str) -> bool:
    """Check if a URL's domain plausibly belongs to the named company.

    Compares slugified company name tokens against the domain. Allows for
    common patterns: company.com, careers.company.com, jobs.company.com.
    """
    domain = _domain_of(url)
    if not domain:
        return False
    # Slugify the company name into tokens
    name_tokens = set(
        re.sub(r"[^\w\s]", "", company_name.lower()).split()
    ) - {"the", "inc", "ai", "labs", "co", "io", "com"}
    if not name_tokens:
        return True  # can't verify, let it pass
    # Check if any meaningful name token appears in the domain
    domain_lower = domain.lower()
    return any(tok in domain_lower for tok in name_tokens if len(tok) >= 3)


async def _search_company_careers_page(name: str) -> str | None:
    """Find a company's careers page URL via web search.

    Unlike _search_careers_url (which only accepts ATS URLs), this returns ANY
    plausible careers page URL. Verifies the domain matches the company name
    to avoid returning wrong-company careers pages (e.g. VC portfolio pages).
    """
    query = f'"{name}" careers jobs'
    results = await _precise_search(query, max_results=8)
    for r in results:
        url = r.get("url", "")
        if _looks_like_careers_url(url) and _domain_plausible_for_company(url, name):
            logger.info("Careers page lead for '%s': %s", name, url)
            return url
    # Log what we rejected for debugging
    career_urls = [r.get("url", "") for r in results if _looks_like_careers_url(r.get("url", ""))]
    if career_urls:
        logger.info(
            "Careers page search for '%s': found %d career URLs but none matched domain: %s",
            name, len(career_urls), career_urls[:3],
        )
    return None


def _domain_of(url: str) -> str | None:
    """Return the bare hostname of a URL with leading 'www.' stripped, or None."""
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
        return re.sub(r"^www\.", "", host) or None
    except Exception:
        return None


async def _get_existing_company_names() -> list[str]:
    async with async_session() as session:
        result = await session.execute(select(Company.name))
        return [row[0] for row in result.all()]


async def _load_profile_data() -> dict:
    async with async_session() as session:
        result = await session.execute(select(UserProfile).limit(1))
        profile = result.scalar_one_or_none()
        if profile and profile.data:
            return profile.data
        return {}


async def _load_reference_jobs(job_ids: list[str]) -> list[dict]:
    """Fetch thumbed-up reference jobs from DB by IDs."""
    if not job_ids:
        return []
    async with async_session() as session:
        from sqlalchemy.dialects.postgresql import UUID as PG_UUID
        import uuid as _uuid
        uuids = []
        for jid in job_ids:
            try:
                uuids.append(_uuid.UUID(jid))
            except ValueError:
                continue
        if not uuids:
            return []
        result = await session.execute(
            select(Job).where(Job.id.in_(uuids))
        )
        jobs = result.scalars().all()
        refs = []
        for j in jobs:
            refs.append({
                "title": j.display_title,
                "company": j.display_company,
                "location": j.location,
                "salary_min": j.salary_min,
                "salary_max": j.salary_max,
                "description_snippet": (j.description or "")[:300],
                "work_model": j.work_model,
            })
        return refs


def _build_reference_context(reference_jobs: list[dict]) -> str:
    """Build a text block summarizing reference jobs for query generation."""
    if not reference_jobs:
        return "(no reference jobs)"
    lines = ["The user likes these jobs and wants more like them:"]
    for i, ref in enumerate(reference_jobs, 1):
        header_parts = [f'"{ref["title"]}" at {ref["company"]}']
        if ref.get("location"):
            header_parts.append(ref["location"])
        if ref.get("salary_min") and ref["salary_min"] > 0:
            sal = f"${ref['salary_min'] // 1000}K"
            if ref.get("salary_max"):
                sal += f"-${ref['salary_max'] // 1000}K"
            header_parts.append(sal)
        lines.append(f"  {i}. {' — '.join(header_parts)}")
        # Include the job description so the LLM knows what the company
        # does and what the role involves (e.g. "ABM company", "ML infra").
        snippet = (ref.get("description_snippet") or "").strip()
        if snippet:
            lines.append(f"     {snippet}")
    return "\n".join(lines)


def _enrich_keywords_from_references(
    profile_keywords: dict, reference_jobs: list[dict]
) -> dict:
    """Temporarily enrich profile keywords with data from reference jobs."""
    if not reference_jobs:
        return profile_keywords
    # Shallow copy the sets so we don't mutate the originals
    enriched = {k: (set(v) if isinstance(v, set) else v) for k, v in profile_keywords.items()}
    for ref in reference_jobs:
        # Add role title words
        title_words = ref["title"].lower().split()
        for w in title_words:
            if len(w) >= 3 and w not in {"the", "and", "for", "with"}:
                enriched.setdefault("role_titles", set()).add(ref["title"].lower())
        # Add keywords from description snippet
        snippet = ref.get("description_snippet", "").lower()
        for skill in enriched.get("technical_skills", set()):
            pass  # already there
        # Extract potential tech keywords from snippet
        tech_words = re.findall(r'\b[a-z][a-z+#.]{2,}\b', snippet)
        common_words = {"the", "and", "for", "with", "this", "that", "from", "will",
                       "have", "are", "been", "our", "you", "your", "can", "not",
                       "all", "may", "about", "more", "than", "other", "into",
                       "has", "was", "who", "what", "how", "but", "also", "some",
                       "any", "work", "team", "role", "experience", "ability",
                       "strong", "including", "across", "within"}
        for w in tech_words:
            if w not in common_words:
                enriched.setdefault("technical_skills", set()).add(w)
    return enriched
