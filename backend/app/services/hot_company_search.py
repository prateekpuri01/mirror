"""Hot Company Search — AI-powered company discovery agent.

Two-phase approach:
  Phase 1: Discover companies via diverse search strategies (news, funding,
           industry reports, job boards) — LLM extracts company names from results.
  Phase 2: For each company, probe ATS boards (Greenhouse/Lever/Ashby),
           scrape jobs, score relevance, yield hits.
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import AsyncGenerator

import httpx
from sqlalchemy import select

from app.config import settings
from app.database import async_session
from app.models.companies import Company
from app.models.profile import UserProfile
from app.services.company_discovery import (
    _build_keyword_sets,
    _make_temp_company,
    _SCRAPER_MAP,
    _verify_ats_slug,
    resolve_job_url,
    score_job_relevance,
)

logger = logging.getLogger(__name__)

_EVAL_SEMAPHORE = asyncio.Semaphore(3)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class CompanyCandidate:
    name: str
    url: str | None = None
    ats: str | None = None
    slug: str | None = None
    source: str = ""


@dataclass
class CompanyHit:
    name: str
    ats: str
    slug: str
    website: str | None = None
    total_jobs: int = 0
    relevant_jobs: int = 0
    top_jobs: list[dict] = field(default_factory=list)
    source: str = ""
    description: str = ""
    match_reason: str = ""


@dataclass
class SearchEvent:
    event: str  # "status" | "hit" | "candidate" | "error" | "done"
    data: dict


# ---------------------------------------------------------------------------
# Tavily search
# ---------------------------------------------------------------------------


async def _tavily_search(query: str, max_results: int = 10) -> list[dict]:
    """Call Tavily Search API. Returns list of {title, url, content}."""
    api_key = settings.tavily_api_key
    if not api_key:
        return []

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": api_key,
                    "query": query,
                    "max_results": max_results,
                    "include_answer": False,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("results", [])
    except Exception:
        logger.warning("Tavily search failed for query: %s", query, exc_info=True)
        return []


# ---------------------------------------------------------------------------
# OpenAI helper
# ---------------------------------------------------------------------------


async def _openai_chat(system: str, user: str, temperature: float = 0.7) -> str | None:
    """Call OpenAI chat completions. Returns content string or None."""
    api_key = settings.openai_api_key
    if not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": settings.hot_search_model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": temperature,
                    "max_tokens": 1500,
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
    except Exception:
        logger.warning("OpenAI call failed", exc_info=True)
        return None


def _parse_json_array(text: str) -> list | None:
    """Parse a JSON array from LLM output, handling markdown code blocks."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        # Try to find a JSON array in the text
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
    return None


# ---------------------------------------------------------------------------
# Phase 1: Query generation — diverse search strategies
# ---------------------------------------------------------------------------

_QUERY_SYSTEM_PROMPT = """\
You generate diverse web search queries to discover companies that might have \
relevant job openings for a user. You are NOT just searching for job postings — \
you are finding COMPANIES.

Generate a MIX of query types:
- **Company discovery**: "top NLP startups 2026", "companies working on LLM evaluation"
- **Funding/news**: "Series A B AI governance startup funding 2026", "new policy research lab"
- **Industry lists**: "best places to work data science", "emerging computational social science companies"
- **Hiring signals**: "hiring machine learning researcher remote 2026"
- **ATS-specific** (only if enabled): "site:boards.greenhouse.io data scientist NLP"

For ATS-specific sources, use site: prefix. For general web search, focus on \
finding companies and organizations, NOT individual job postings on job boards.

CRITICAL: The user's guidance section contains HARD CONSTRAINTS. If the user \
says to EXCLUDE something, you MUST NOT generate queries that would find those \
things. If the user says to AVOID certain types of companies, respect that \
absolutely. Read the guidance carefully before generating queries.

Return ONLY a JSON array of 4-6 search query strings."""

_QUERY_USER_TEMPLATE = """\
## User Profile
Target roles: {role_titles}
Domains: {domains}
Technical skills: {skills}
Preferred industries: {industries}
Deal breakers: {deal_breakers}
Remote preference: {remote_pref}

## User Guidance (MUST FOLLOW — these are hard constraints, not suggestions)
{guidance}

## Enabled Sources
{sources_desc}

## Session Context
Queries already tried: {past_queries}
Companies found so far: {evaluated_summary}
Hits: {hit_count} / {target}

## Companies Already In DB (skip these)
{existing_companies}

Generate 4-6 NEW, diverse search queries. Vary the strategy — don't repeat \
approaches that already failed. If previous queries found few results, try \
a different angle (different industries, company stages, geographies, etc.)."""


async def _generate_queries(
    guidance: str,
    profile_data: dict,
    existing_companies: list[str],
    past_queries: list[str],
    evaluated: dict[str, str],
    hit_count: int,
    max_hits: int,
    sources: list[str],
) -> list[str]:
    """Use LLM to generate diverse search queries."""
    role_titles = [r.get("title", "") for r in profile_data.get("target_roles", [])]
    domains = profile_data.get("domains", [])
    skills = profile_data.get("skills", {}).get("technical", [])[:10]
    prefs = profile_data.get("search_preferences", {})
    industries = prefs.get("industries_ranked", [])
    deal_breakers = prefs.get("deal_breakers", [])
    personal = profile_data.get("personal", {})
    remote_pref = personal.get("remote_preference", "any")

    sources_desc = []
    if "tavily" in sources:
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

    user_prompt = _QUERY_USER_TEMPLATE.format(
        role_titles=", ".join(role_titles),
        domains=", ".join(domains),
        skills=", ".join(skills),
        industries=", ".join(industries),
        deal_breakers=", ".join(deal_breakers),
        remote_pref=remote_pref,
        guidance=guidance or "(no specific guidance)",
        sources_desc="\n".join(sources_desc),
        past_queries=json.dumps(past_queries[-15:]) if past_queries else "[]",
        evaluated_summary=eval_summary or "(none yet)",
        hit_count=hit_count,
        target=max_hits,
        existing_companies=", ".join(existing_companies[:100]) or "(none)",
    )

    logger.info("Generating queries with guidance: %s", guidance or "(none)")
    content = await _openai_chat(_QUERY_SYSTEM_PROMPT, user_prompt, temperature=0.8)
    if content:
        queries = _parse_json_array(content)
        if queries:
            result = [q for q in queries if isinstance(q, str)][:6]
            logger.info("LLM generated queries: %s", result)
            return result

    result = _fallback_queries(guidance, profile_data, sources, past_queries)
    logger.info("Fallback queries: %s", result)
    return result


def _fallback_queries(
    guidance: str,
    profile_data: dict,
    sources: list[str],
    past_queries: list[str],
) -> list[str]:
    """Generate basic queries without LLM."""
    role_titles = [r.get("title", "") for r in profile_data.get("target_roles", [])]
    domains = profile_data.get("domains", [])
    base = guidance or " ".join(role_titles[:2])
    queries = []

    if "tavily" in sources:
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
    return queries or [f"emerging companies {base}"]


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

# Domains to skip — job boards, aggregators, news sites
_SKIP_DOMAINS = {
    "indeed.com", "linkedin.com", "glassdoor.com", "ziprecruiter.com",
    "monster.com", "dice.com", "simplyhired.com", "careerbuilder.com",
    "builtin.com", "wellfound.com", "angel.co", "ycombinator.com",
    "news.ycombinator.com", "reddit.com", "twitter.com", "x.com",
    "medium.com", "wikipedia.org", "youtube.com", "github.com",
    "crunchbase.com", "pitchbook.com", "techcrunch.com",
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


async def _extract_candidates_from_results(
    results: list[dict], query: str
) -> list[CompanyCandidate]:
    """Two-pronged extraction: regex for ATS URLs + LLM for everything else."""
    candidates: list[CompanyCandidate] = []
    seen: set[str] = set()
    non_ats_results: list[dict] = []

    # First pass: extract direct ATS slugs from URLs (fast, reliable)
    for r in results:
        url = r.get("url", "")
        for ats, pattern in _ATS_URL_PATTERNS.items():
            m = pattern.search(url)
            if m:
                slug = m.group(1)
                key = f"{ats}:{slug}"
                if key not in seen:
                    seen.add(key)
                    name = slug.replace("-", " ").title()
                    candidates.append(CompanyCandidate(
                        name=name, url=url, ats=ats, slug=slug,
                        source=ats if "site:" in query else "tavily",
                    ))
                break
        else:
            # Not an ATS URL — collect for LLM extraction
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
    """Use LLM to extract company names from Tavily search results."""
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
            name=name, url=url, source="tavily",
        ))

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
    results = await _tavily_search(query, max_results=5)

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


# ---------------------------------------------------------------------------
# Hit summary generation (description + match reason)
# ---------------------------------------------------------------------------

_SUMMARY_SYSTEM = """\
Given a company name and its top matching job titles, produce a brief JSON object:
{"description": "1-2 sentence company description", "match_reason": "1 sentence why this company matches the user"}
Keep both concise. For description, mention what the company does and its domain.
For match_reason, reference the specific roles/skills that matched.
Return ONLY the JSON object."""


async def _generate_hit_summary(
    company_name: str,
    top_jobs: list[dict],
    profile_keywords: dict,
) -> tuple[str, str]:
    """Generate a short company description and match reason. Best-effort."""
    job_titles = [j.get("title", "") for j in top_jobs[:5]]
    roles = list(profile_keywords.get("role_titles", set()))[:3]
    domains = list(profile_keywords.get("domains", set()))[:3]

    user_prompt = (
        f"Company: {company_name}\n"
        f"Top matching jobs: {', '.join(job_titles)}\n"
        f"User's target roles: {', '.join(roles)}\n"
        f"User's domains: {', '.join(domains)}"
    )

    try:
        content = await _openai_chat(_SUMMARY_SYSTEM, user_prompt, temperature=0.3)
        if content:
            parsed = _parse_json_array(content)  # won't work — it's an object
            if not parsed:
                # Try parsing as object
                text = content.strip()
                if text.startswith("```"):
                    text = re.sub(r"^```\w*\n?", "", text)
                    text = re.sub(r"\n?```$", "", text)
                obj = json.loads(text)
                if isinstance(obj, dict):
                    return (
                        obj.get("description", ""),
                        obj.get("match_reason", ""),
                    )
    except Exception:
        logger.debug("Failed to generate hit summary for %s", company_name)

    return ("", "")


# ---------------------------------------------------------------------------
# Phase 2b: Candidate evaluation
# ---------------------------------------------------------------------------


_CANDIDATE_TIMEOUT = 45  # seconds per candidate


async def _evaluate_candidate(
    candidate: CompanyCandidate,
    profile_keywords: dict,
    http_client: httpx.AsyncClient,
) -> CompanyHit | None:
    """Evaluate a candidate: resolve ATS, scrape jobs, score. Timeout-protected."""
    async with _EVAL_SEMAPHORE:
        try:
            return await asyncio.wait_for(
                _evaluate_candidate_inner(
                    candidate, profile_keywords, http_client,
                ),
                timeout=_CANDIDATE_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Candidate evaluation timed out after %ds: %s",
                _CANDIDATE_TIMEOUT, candidate.name,
            )
            return None
        except Exception:
            logger.warning(
                "Failed to evaluate candidate: %s", candidate.name, exc_info=True,
            )
            return None


async def _evaluate_candidate_inner(
    candidate: CompanyCandidate,
    profile_keywords: dict,
    http_client: httpx.AsyncClient,
) -> CompanyHit | None:
    ats = candidate.ats
    slug = candidate.slug

    # If we don't have ats+slug, resolve in 3 tiers:
    #   1. Slug probing from company name (fast, no web calls except ATS API)
    #   2. Web search for company careers page → full resolution pipeline
    if not ats or not slug:
        result = await _probe_name_for_ats(candidate.name, http_client)
        if not result:
            logger.info(
                "Slug probing failed for '%s', searching for careers page...",
                candidate.name,
            )
            result = await _search_careers_url(candidate.name)
        if result:
            ats, slug = result
        else:
            return None

    # Verify the slug exists
    verified = await _verify_ats_slug(ats, slug, http_client)
    if not verified:
        return None

    # Scrape jobs
    scraper = _SCRAPER_MAP.get(ats)
    if not scraper:
        return None

    temp_company = _make_temp_company(ats, slug)
    scraped_jobs = await scraper.scrape_company(temp_company, http_client)

    if not scraped_jobs:
        return None

    total_scraped = len(scraped_jobs)
    # Cap scoring to first 100 jobs — enough to find relevant ones
    if len(scraped_jobs) > 100:
        scraped_jobs = scraped_jobs[:100]

    # Score each job
    job_previews = []
    for sj in scraped_jobs:
        relevance = (
            score_job_relevance(sj, profile_keywords) if profile_keywords else 0
        )
        meta = sj.metadata or {}
        job_previews.append({
            "title": sj.title,
            "location": sj.location,
            "department": meta.get("departments", meta.get("department")),
            "url": sj.url,
            "posted_at": sj.posted_at.isoformat() if sj.posted_at else None,
            "relevance": relevance,
            "description_html": sj.description_html,
            "remote": sj.remote,
        })

    job_previews.sort(key=lambda j: j["relevance"], reverse=True)

    # Hit if any job scores >= 75
    relevant_jobs = [j for j in job_previews if j["relevance"] >= 75]
    if not relevant_jobs:
        return None

    # Use company name from candidate (LLM-extracted), not slug
    display_name = candidate.name
    if display_name == slug.replace("-", " ").title():
        # Already slug-derived, keep it
        pass

    # Generate description + match reason (non-blocking, best-effort)
    description, match_reason = await _generate_hit_summary(
        display_name, relevant_jobs[:5], profile_keywords,
    )

    return CompanyHit(
        name=display_name,
        ats=ats,
        slug=slug,
        website=candidate.url,
        total_jobs=total_scraped,
        relevant_jobs=len(relevant_jobs),
        top_jobs=job_previews[:10],
        source=candidate.source,
        description=description,
        match_reason=match_reason,
    )


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Main search loop
# ---------------------------------------------------------------------------


async def run_hot_company_search(
    sources: list[str],
    guidance: str,
    max_hits: int = 20,
    max_iterations: int = 5,
) -> AsyncGenerator[SearchEvent, None]:
    """Main search loop. Yields SearchEvent objects for SSE streaming."""

    valid_sources = {"tavily", "greenhouse", "lever", "ashby"}
    sources = [s for s in sources if s in valid_sources]
    if not sources:
        yield SearchEvent("error", {"message": "No valid sources selected"})
        yield SearchEvent("done", {"total_hits": 0, "total_candidates_checked": 0})
        return

    if not settings.tavily_api_key:
        yield SearchEvent("error", {
            "message": "Tavily API key not configured. Set TAVILY_API_KEY in .env",
        })
        yield SearchEvent("done", {"total_hits": 0, "total_candidates_checked": 0})
        return

    yield SearchEvent("status", {
        "message": "Loading profile and existing companies...",
        "phase": "init", "iteration": 0,
        "total_queries": 0, "hits_so_far": 0,
    })

    # Load context once
    profile_data = await _load_profile_data()
    profile_keywords = _build_keyword_sets(profile_data) if profile_data else {}
    existing_companies = await _get_existing_company_names()
    existing_lower = {n.lower() for n in existing_companies}

    # Session state
    past_queries: list[str] = []
    evaluated: dict[str, str] = {}  # key → "hit" | "miss" | "failed"
    hits: list[CompanyHit] = []
    total_candidates = 0
    consecutive_dry = 0

    async with httpx.AsyncClient(timeout=60.0) as http_client:
        for iteration in range(max_iterations):
            if len(hits) >= max_hits:
                break
            if consecutive_dry >= 3:
                yield SearchEvent("status", {
                    "message": "Stopping — 3 iterations with no new hits",
                    "phase": "stopping", "iteration": iteration,
                    "total_queries": len(past_queries), "hits_so_far": len(hits),
                })
                break

            yield SearchEvent("status", {
                "message": f"Generating search queries (round {iteration + 1}/{max_iterations})...",
                "phase": "generating", "iteration": iteration + 1,
                "total_queries": len(past_queries), "hits_so_far": len(hits),
            })

            queries = await _generate_queries(
                guidance, profile_data, existing_companies,
                past_queries, evaluated, len(hits), max_hits, sources,
            )
            past_queries.extend(queries)
            iteration_hits = 0

            for query in queries:
                if len(hits) >= max_hits:
                    break

                yield SearchEvent("status", {
                    "message": f"Searching: {query[:80]}...",
                    "phase": "searching", "iteration": iteration + 1,
                    "total_queries": len(past_queries), "hits_so_far": len(hits),
                })

                results = await _tavily_search(query, max_results=10)
                candidates = await _extract_candidates_from_results(results, query)

                for candidate in candidates:
                    if len(hits) >= max_hits:
                        break

                    # Dedup: skip companies already in DB
                    if candidate.name.lower() in existing_lower:
                        continue

                    # Dedup: skip already evaluated this session
                    norm_key = candidate.name.lower()
                    if candidate.slug:
                        norm_key = f"{candidate.ats}:{candidate.slug}"
                    if norm_key in evaluated:
                        continue

                    total_candidates += 1
                    yield SearchEvent("candidate", {
                        "name": candidate.name,
                        "source": candidate.source,
                    })

                    hit = await _evaluate_candidate(
                        candidate, profile_keywords, http_client,
                    )

                    if hit:
                        evaluated[norm_key] = "hit"
                        # Also mark the slug key so we don't re-probe
                        evaluated[f"{hit.ats}:{hit.slug}"] = "hit"
                        hits.append(hit)
                        iteration_hits += 1
                        yield SearchEvent("hit", {
                            "name": hit.name,
                            "ats": hit.ats,
                            "slug": hit.slug,
                            "website": hit.website,
                            "total_jobs": hit.total_jobs,
                            "relevant_jobs": hit.relevant_jobs,
                            "top_jobs": hit.top_jobs,
                            "source": hit.source,
                            "description": hit.description,
                            "match_reason": hit.match_reason,
                        })
                    else:
                        evaluated[norm_key] = "miss"

                await asyncio.sleep(0.5)

            if iteration_hits == 0:
                consecutive_dry += 1
            else:
                consecutive_dry = 0

    yield SearchEvent("done", {
        "total_hits": len(hits),
        "total_candidates_checked": total_candidates,
    })
