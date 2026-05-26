"""Company and team research for resume tailoring.

Runs web search queries via SearXNG (self-hosted) + page fetching, then
synthesizes findings into structured JSON with actionable framing angles.
Results are cached on Job.extra_metadata["company_research"].
"""

import json
import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import RESUME_MODEL, get_openai_client
from app.config import settings
from app.models import Company, Job
from app.services.web_search import web_search


def _domain_from_url(url: str | None) -> str | None:
    """Pull a canonical domain from a job URL — used as a disambiguation
    anchor in research queries so a vague company name like "Surge" gets
    resolved against the right entity.

    Strips ATS subdomains so the resulting domain points at the actual
    company site (e.g. ``boards.greenhouse.io/anthropic`` does NOT yield
    ``greenhouse.io``; it returns None and we fall back to other signals).
    """
    if not url:
        return None
    try:
        from urllib.parse import urlparse

        # ``removeprefix`` is critical here — ``lstrip("www.")`` would strip
        # any leading char in {'w', '.'}, mangling hosts like "wellfound.com"
        # into "ellfound.com" (silently leaking the ATS filter below).
        host = (urlparse(url).netloc or "").lower().removeprefix("www.")
    except Exception:
        return None
    if not host:
        return None
    ATS_HOSTS = (
        "greenhouse.io",
        "lever.co",
        "workday.com",
        "myworkdayjobs.com",
        "ashbyhq.com",
        "rippling.com",
        "smartrecruiters.com",
        "bamboohr.com",
        "jobvite.com",
        "icims.com",
        "breezy.hr",
        "workable.com",
        "linkedin.com",
        "indeed.com",
        "glassdoor.com",
        "ycombinator.com",
        "wellfound.com",
        "angel.co",
    )
    for ats in ATS_HOSTS:
        if host == ats or host.endswith("." + ats):
            return None
    return host


def _disambiguator_line(company_name: str, website: str | None, job_url: str | None) -> str | None:
    """Return a leading line that pins the LLM to the right company entity."""
    domain = None
    if website:
        domain = _domain_from_url(website if website.startswith("http") else f"https://{website}")
    if not domain:
        domain = _domain_from_url(job_url)
    if not domain:
        return None
    bits = [
        f'IMPORTANT: "{company_name}" here refers specifically to the company at the domain {domain}',
    ]
    if job_url:
        bits.append(f" (job posting URL: {job_url})")
    bits.append(
        f'. DO NOT confuse with other companies that share the name "{company_name}". '
        f"All research below must describe the {domain} company specifically."
    )
    return "".join(bits)


def _build_company_query(company_name: str, website: str | None, job_url: str | None = None) -> str:
    parts: list[str] = []
    disambig = _disambiguator_line(company_name, website, job_url)
    if disambig:
        parts.append(disambig)
        parts.append("")
    parts.extend(
        [
            f"Research {company_name} for a job application. I need:",
            "1. What does the company build or do? What is their mission?",
            "2. Company stage (startup/growth/public, approximate size, recent funding)",
            "3. Technology stack and technical approach",
            "4. Culture: do they publish research? open-source projects? what domains?",
            "5. Recent news: funding rounds, product launches, notable hires (2024-2026)",
            "6. What skills and backgrounds do they value in technical/research hires?",
        ]
    )
    if website:
        parts.append(f"\nCompany website: {website}")
    return "\n".join(parts)


def _build_team_query(
    company_name: str,
    team_name: str,
    job_title: str,
    description_excerpt: str,
    website: str | None = None,
    job_url: str | None = None,
) -> str:
    header_lines: list[str] = []
    disambig = _disambiguator_line(company_name, website, job_url)
    if disambig:
        header_lines.append(disambig)
        header_lines.append("")
    header = "\n".join(header_lines)
    return f"""{header}\
Research the {team_name} team at {company_name}. I need:
1. What does this specific team do?
2. Recent blog posts, papers, or talks by people on this team (2024-2026)
3. What technical problems are they working on?
4. What does a typical hire on this team look like (background, skills)?

The role being applied for: {job_title}
Job description excerpt: {description_excerpt[:500]}"""


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Synthesis prompt (runs on OpenAI/GPT, not Perplexity)
# ---------------------------------------------------------------------------

_SYNTHESIS_SYSTEM = """\
You synthesize raw web research about a company and team into structured, actionable \
resume-tailoring guidance. The most important output is "framing_angles" — 3-5 specific, \
concrete ways the candidate should frame their existing accomplishments to resonate with \
this company/team.

Good framing angles (specific, actionable):
- "Lead with evaluation rigor — their blog emphasizes benchmark methodology"
- "Highlight user adoption stories — their About page says they ship to millions"
- "Show cross-functional work — the team spans research and engineering"

Bad framing angles (too vague):
- "Show you're a good fit"
- "Mention relevant skills"
- "Demonstrate passion"

Respond with ONLY valid JSON (no markdown fences)."""


def _build_synthesis_prompt(
    company_name: str,
    team_name: str | None,
    company_response: str,
    team_response: str | None,
    job_title: str,
) -> str:
    parts = [
        f"## Raw Company Research for {company_name}\n",
        company_response,
    ]
    if team_response:
        parts.append(f"\n\n## Raw Team Research ({team_name})\n")
        parts.append(team_response)
    else:
        parts.append("\n\nNo team-specific research available.")

    parts.append(f"""

## Job Context
Title: {job_title}
Team: {team_name or "unspecified"}

---

Synthesize the above into this exact JSON structure:
{{
  "company_summary": "1-2 sentence description of what the company does",
  "company_stage": "e.g. Series C startup, ~500 employees",
  "tech_signals": ["signal1", "signal2"],
  "culture_signals": ["signal1", "signal2"],
  "recent_news": ["1-sentence item", "max 3"],
  "team_function": "What this specific team does (or 'General technical role' if unknown)",
  "team_recent_work": ["specific blog post, paper, or launch"],
  "team_open_problems": ["what they are trying to solve"],
  "valued_skills": ["skills they emphasize in hiring"],
  "interview_signals": ["what they likely screen for"],
  "framing_angles": ["3-5 specific, actionable framing suggestions for resume bullets"],
  "citations": ["URLs from the research"]
}}""")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Team name extraction
# ---------------------------------------------------------------------------


def _extract_team_name(job: Job) -> str | None:
    """Try to extract a team/department name from the job metadata or title."""
    meta = job.extra_metadata or {}

    # Check ATS-provided metadata first
    for key in ("department", "team", "team_name"):
        val = meta.get(key)
        if val and isinstance(val, str) and len(val.strip()) > 2:
            return val.strip()

    # Heuristic: look for common patterns in title like "Research Scientist, Frontier Red Team"
    title = job.title or ""
    if "," in title:
        after_comma = title.split(",", 1)[1].strip()
        if len(after_comma) > 3 and len(after_comma) < 60:
            return after_comma

    return None


# ---------------------------------------------------------------------------
# Core research function
# ---------------------------------------------------------------------------


async def _fetch_page_text(url: str, max_chars: int = 5000) -> str:
    """Fetch a web page and extract its visible text."""
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            # Strip HTML tags for a rough text extraction
            import re

            text = re.sub(r"<[^>]+>", " ", resp.text)
            text = re.sub(r"\s+", " ", text).strip()
            return text[:max_chars]
    except Exception:
        return ""


async def _gather_search_context(
    company_name: str,
    website: str | None,
    team_name: str | None,
    job_title: str,
    job_url: str | None = None,
) -> tuple[str, str | None, list[str]]:
    """Run web searches and gather text for company + team research.

    Returns (company_context, team_context, citation_urls).
    """
    citations = []

    # Resolve a canonical domain (website preferred, else parsed from job_url)
    # so all searches can be biased toward the right entity when the company
    # name is ambiguous (e.g. multiple companies named "Surge").
    domain = None
    if website:
        domain = _domain_from_url(website if website.startswith("http") else f"https://{website}")
    if not domain:
        domain = _domain_from_url(job_url)

    # Company search — prefer site-restricted query when a domain is known
    if domain:
        site_results = await web_search(f"site:{domain} mission product team", num_results=5)
    else:
        site_results = []
    company_results = await web_search(
        f'"{company_name}" company mission technology team', num_results=5
    )
    company_parts = []
    if domain:
        company_parts.append(
            f'NOTE: "{company_name}" refers specifically to the company at {domain}. '
            f"Disregard other companies that share the name."
        )

    # Add website text if available
    if website:
        page_text = await _fetch_page_text(website)
        if page_text:
            company_parts.append(f"Website ({website}):\n{page_text[:3000]}")
            citations.append(website)

    for result in site_results + company_results:
        company_parts.append(f"{result['title']}: {result['snippet']}")
        if result.get("url"):
            citations.append(result["url"])

    # Recent news search — bias toward the canonical domain when available
    news_query = (
        f'"{company_name}" "{domain}" 2025 2026 news funding'
        if domain
        else f'"{company_name}" 2025 2026 news funding'
    )
    news_results = await web_search(news_query, num_results=3, time_range="year")
    for result in news_results:
        company_parts.append(f"News: {result['title']}: {result['snippet']}")
        if result.get("url"):
            citations.append(result["url"])

    company_context = (
        "\n\n".join(company_parts) if company_parts else f"No web results found for {company_name}."
    )

    # Team search (if team identifiable)
    team_context = None
    if team_name:
        team_query = (
            f'site:{domain} "{team_name}" team blog research'
            if domain
            else f'"{company_name}" "{team_name}" team blog research'
        )
        team_results = await web_search(team_query, num_results=5)
        team_parts = []
        for result in team_results:
            team_parts.append(f"{result['title']}: {result['snippet']}")
            if result.get("url"):
                citations.append(result["url"])
        if team_parts:
            team_context = "\n\n".join(team_parts)

    return company_context, team_context, citations


async def _research_via_llm_native(
    company_name: str,
    website: str | None,
    team_name: str | None,
    job_title: str,
    job_desc: str,
    job_url: str | None = None,
) -> tuple[str, str | None, list[str]]:
    """Use the active LLM provider's native web search for grounded research.

    Returns company text + optional team text + a list of citation URLs.
    Same shape the now-retired ``_research_via_perplexity`` used to return —
    team text), with citations exposed as a list so the caller can persist
    them into the research dict alongside any from other backends.
    """
    # Fire the company query and (optional) team query CONCURRENTLY. Each
    # native-LLM web search is an agentic-search call (reasoning model +
    # web_search tool + chain-of-thought search planning) that runs 30-60s
    # on its own; sequentially they were the dominant contributor to Phase
    # 0's wall-clock. They have no data dependency on each other.
    import asyncio

    from app.services.web_search_llm import llm_web_search

    company_query = _build_company_query(company_name, website, job_url)
    team_query = (
        _build_team_query(
            company_name,
            team_name,
            job_title,
            job_desc,
            website=website,
            job_url=job_url,
        )
        if team_name
        else None
    )

    async def _company_call():
        try:
            return await llm_web_search(company_query, num_results=8)
        except Exception:
            logger.exception("LLM-native company query failed for %s", company_name)
            return None

    async def _team_call():
        if team_query is None:
            return None
        try:
            return await llm_web_search(team_query, num_results=5)
        except Exception:
            logger.exception(
                "LLM-native team query failed for %s / %s",
                company_name,
                team_name,
            )
            return None

    company_result, team_result = await asyncio.gather(_company_call(), _team_call())

    company_text = f"Research unavailable for {company_name}."
    citations: list[str] = []
    if company_result:
        company_text = company_result.answer or company_text
        citations.extend(c.url for c in company_result.citations if c.url)

    team_text: str | None = None
    if team_result and team_result.answer:
        team_text = team_result.answer
        citations.extend(c.url for c in team_result.citations if c.url)

    return company_text, team_text, citations


async def research_company_for_job(
    session: AsyncSession,
    job: Job,
    company: Company | None = None,
    *,
    force: bool = False,
) -> dict:
    """Research a company/team for resume tailoring.

    Uses Perplexity API if key is configured (premium, better results).
    Falls back to SearXNG + OpenAI synthesis (free, self-hosted).

    Returns structured research dict. Caches on job.extra_metadata["company_research"].
    """
    # Check cache
    meta = dict(job.extra_metadata or {})
    if not force and meta.get("company_research"):
        logger.info("Using cached company research for job %s", job.id)
        return meta["company_research"]

    company_name = company.name if company else job.company or "Unknown"
    website = company.website if company else None
    team_name = _extract_team_name(job)

    # Decide research backend. Priority:
    #   1. Native LLM search (OpenAI Responses or Anthropic Messages w/
    #      web_search tool — works when the user's LLM provider supports it,
    #      no extra setup beyond the LLM API key they already have).
    #      Per the 2026-05 web-search eval this beats Perplexity on every
    #      axis; Perplexity was retired in favor of zero paid-search APIs.
    #   2. SearXNG + page-fetch fallback (free, self-hosted, always available)
    from app.services.web_search_llm import native_web_search_supported

    use_native_llm = native_web_search_supported()
    backend_label = f"LLM-native ({settings.llm_provider})" if use_native_llm else "SearXNG"

    logger.info(
        "Researching %s%s for job %s (%s) via %s",
        company_name,
        f" / {team_name}" if team_name else "",
        job.id,
        job.title,
        backend_label,
    )

    citations: list[str] = []

    job_url = job.url or job.application_url
    if use_native_llm:
        # Tier 1: native LLM web search — grounded answer + citations,
        # served by the user's configured LLM provider.
        company_context, team_context, native_citations = await _research_via_llm_native(
            company_name,
            website,
            team_name,
            job.title or "",
            job.description or "",
            job_url=job_url,
        )
        citations.extend(native_citations)
        research_model = f"{settings.llm_provider}-web-search"
    else:
        # Tier 2: SearXNG web search + page fetching
        company_context, team_context, citations = await _gather_search_context(
            company_name,
            website,
            team_name,
            job.title or "",
            job_url=job_url,
        )
        research_model = RESUME_MODEL

    # Synthesis: merge into structured output via OpenAI
    synthesis_prompt = _build_synthesis_prompt(
        company_name,
        team_name,
        company_context,
        team_context,
        job.title or "",
    )
    openai = get_openai_client()
    try:
        synth_resp = await openai.chat.completions.create(
            model=RESUME_MODEL,
            messages=[
                {"role": "system", "content": _SYNTHESIS_SYSTEM},
                {"role": "user", "content": synthesis_prompt},
            ],
            max_completion_tokens=2000,
            temperature=0.3,
        )
        raw = synth_resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[: raw.rfind("```")]
        research = json.loads(raw)
    except Exception:
        logger.exception("Research synthesis failed for %s", company_name)
        research = {
            "company_summary": company_context[:300]
            if company_context
            else "Research unavailable.",
            "company_stage": "unknown",
            "tech_signals": [],
            "culture_signals": [],
            "recent_news": [],
            "team_function": team_name or "unknown",
            "team_recent_work": [],
            "team_open_problems": [],
            "valued_skills": [],
            "interview_signals": [],
            "framing_angles": [],
            "citations": [],
        }

    # Ensure citations from search are included
    existing_citations = set(research.get("citations", []))
    for url in citations[:10]:
        if url not in existing_citations:
            research.setdefault("citations", []).append(url)

    # Add metadata
    research["researched_at"] = datetime.now(UTC).isoformat()
    research["model_used"] = research_model
    research["query_company"] = company_name
    research["query_team"] = team_name

    logger.info(
        "Research complete for %s: %d framing angles, %d valued skills",
        company_name,
        len(research.get("framing_angles", [])),
        len(research.get("valued_skills", [])),
    )

    return research


# ---------------------------------------------------------------------------
# Helper: format research for prompt injection
# ---------------------------------------------------------------------------


def format_research_for_generator(research: dict, company_name: str) -> str:
    """Format research as a prompt section for the resume generator."""
    lines = ["## Company & Team Research\n"]
    lines.append(
        f"**{company_name}** — {research.get('company_summary', 'N/A')}. "
        f"{research.get('company_stage', '')}"
    )

    tf = research.get("team_function")
    if tf and tf != "unknown":
        lines.append(f"\n**What this team works on**: {tf}")

    trw = research.get("team_recent_work", [])
    if trw:
        lines.append("\n**Recent team output**:")
        for item in trw[:4]:
            lines.append(f"- {item}")

    top = research.get("team_open_problems", [])
    if top:
        lines.append("\n**Problems they're solving**:")
        for item in top[:4]:
            lines.append(f"- {item}")

    fa = research.get("framing_angles", [])
    if fa:
        lines.append(
            "\n**Resume framing guidance** (use these angles when selecting and writing bullets):"
        )
        for i, angle in enumerate(fa, 1):
            lines.append(f"{i}. {angle}")

    vs = research.get("valued_skills", [])
    if vs:
        lines.append(f"\n**Skills they value**: {', '.join(vs)}")

    lines.append(
        "\nIMPORTANT: Use this research to choose the RIGHT accomplishments and emphasize "
        "the RIGHT aspects. Do NOT copy company jargon verbatim or stuff keywords — the "
        "resume should read naturally. Let the research inform your judgment, not dictate your words."
    )
    return "\n".join(lines)


def format_research_for_critic(research: dict) -> str:
    """Format research as a compact section for the critic."""
    lines = ["## Company & Team Intelligence\n"]

    fa = research.get("framing_angles", [])
    if fa:
        lines.append("Framing angles used to tailor this resume:")
        for i, angle in enumerate(fa, 1):
            lines.append(f"{i}. {angle}")

    top = research.get("team_open_problems", [])
    if top:
        lines.append(f"\nTeam focus areas: {', '.join(top[:4])}")

    vs = research.get("valued_skills", [])
    if vs:
        lines.append(f"Skills they value: {', '.join(vs)}")

    lines.append(
        "\nEvaluate whether the resume effectively demonstrates fit for what this team "
        "actually does and values. A weakness might be: the resume highlights X, but "
        "the team's recent work shows they care about Y instead."
    )
    return "\n".join(lines)


def format_research_for_refiner(research: dict) -> str:
    """Format research as a compact section for the refiner."""
    lines = ["## Company & Team Intelligence\n"]

    fa = research.get("framing_angles", [])
    if fa:
        lines.append("Framing angles: " + " | ".join(fa[:4]))

    top = research.get("team_open_problems", [])
    if top:
        lines.append(f"Team problems: {', '.join(top[:4])}")

    vs = research.get("valued_skills", [])
    if vs:
        lines.append(f"Valued skills: {', '.join(vs)}")

    lines.append(
        "\nUse this when addressing genuine_gap weaknesses — look for adjacent "
        "experience that demonstrates transferable capability toward what this team values."
    )
    return "\n".join(lines)


def format_research_for_chat(research: dict) -> str:
    """Format research as compact context for the chat agent."""
    lines = []
    tf = research.get("team_function")
    if tf and tf != "unknown":
        lines.append(f"Team focus: {tf}")
    fa = research.get("framing_angles", [])
    if fa:
        lines.append(f"Framing angles: {'; '.join(fa[:3])}")
    vs = research.get("valued_skills", [])
    if vs:
        lines.append(f"Valued skills: {', '.join(vs)}")
    return "\n".join(lines)
