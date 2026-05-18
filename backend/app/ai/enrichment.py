"""Company context enrichment pipeline.

Step 1: Resolve all jobs to company records (fuzzy name matching + create new).
Step 2: Enrich each company with LLM-generated context (web fetch + summarize).
Step 3: Extract team names from job titles at large companies.
"""

import asyncio
import logging
import re
from datetime import datetime, timezone

import httpx
from sqlalchemy import select, func, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import get_openai_client, EXTRACTION_MODEL
from app.ai.prompts import build_company_enrichment_system
from app.models import Company, Job
from app.models.profile import UserProfile

logger = logging.getLogger(__name__)

# Rate limiting
_enrichment_semaphore = asyncio.Semaphore(1)
_INTER_ENRICHMENT_DELAY = 8.0

# In-memory progress tracking
_enrichment_status: dict = {
    "running": False,
    "phase": None,
    "total_companies": 0,
    "resolved": 0,
    "enriched": 0,
    "skipped": 0,
    "teams_extracted": 0,
    "failed": 0,
    "failed_companies": [],
    "started_at": None,
}

# Web search is handled by app.services.web_search (SearXNG + Brave fallback)

# ---------------------------------------------------------------------------
# Known junk company names (lowercase)
# ---------------------------------------------------------------------------
_JUNK_NAMES: set[str] = {
    "company",
    "https",
    "http",
    "location",
    "remote",
    "random startup",
    "tech startup",
    "stealth",
    "stealth startup",
    "stealth mode",
    "confidential",
    "n/a",
    "na",
    "none",
    "unknown",
    "various",
    "tbd",
    "hiring",
    "we're hiring",
    "apply now",
    "job",
    "jobs",
    "startup",
    "see description",
}

_JUNK_PATTERNS = re.compile(
    r"^(head of|director of|vp of|chief |cto |ceo |manager of|"
    r"https?://|www\.)|"
    r"^[A-Z\s]{4,}$",  # ALL CAPS 4+ chars like "COMPANY", "REMOTE ONLY"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_company_name(name: str) -> str:
    """Normalize a company name for fuzzy matching."""
    name = name.strip()

    # Strip parenthetical content: "Courted (https://courted.io)" → "Courted"
    name = re.sub(r"\s*\([^)]*\)", "", name)

    # Strip bare URLs after separators: "Tether - https://..." → "Tether"
    name = re.sub(r"\s*[-–—]\s*https?://\S+", "", name)

    # Strip trailing bare URLs
    name = re.sub(r"\s*https?://\S+", "", name)

    name = name.lower().strip()

    # Remove domain suffixes: .io, .ai, .com, .co, .dev, .tech, .org, .net
    name = re.sub(r"\.(io|ai|com|co|dev|tech|org|net)$", "", name)

    # Normalize & → and
    name = name.replace("&", "and")

    # Strip leading "the "
    name = re.sub(r"^the\s+", "", name)

    # Remove common legal suffixes (but NOT "ai" — that breaks "Mistral AI" etc.)
    name = re.sub(
        r",?\s*\b(inc\.?|llc\.?|ltd\.?|corp\.?|co\.?|company|"
        r"technologies|technology|labs?)\b",
        "",
        name,
    )

    # Trailing punctuation cleanup
    name = re.sub(r"[.,;:!?]+$", "", name)

    # Collapse whitespace
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _is_junk_company_name(name: str) -> bool:
    """Return True if this company name is a known junk/placeholder entry."""
    stripped = name.strip()
    if not stripped or len(stripped) < 2:
        return True
    if stripped.lower() in _JUNK_NAMES:
        return True
    if _JUNK_PATTERNS.match(stripped):
        return True
    return False


def _levenshtein(s: str, t: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if len(s) < len(t):
        return _levenshtein(t, s)
    if len(t) == 0:
        return len(s)

    prev = list(range(len(t) + 1))
    for i, sc in enumerate(s):
        curr = [i + 1]
        for j, tc in enumerate(t):
            cost = 0 if sc == tc else 1
            curr.append(min(curr[j] + 1, prev[j + 1] + 1, prev[j] + cost))
        prev = curr
    return prev[-1]


def _find_merge_candidates(
    name_lookup: dict[str, Company],
    normalized: str,
) -> Company | None:
    """Find a fuzzy match in the lookup at edit distance ≤ 2."""
    if len(normalized) < 4:
        return None
    best_dist = 3  # threshold + 1
    best_match: Company | None = None
    for existing_norm, company in name_lookup.items():
        # Quick length filter — edit distance can't be less than length diff
        if abs(len(existing_norm) - len(normalized)) > 2:
            continue
        dist = _levenshtein(existing_norm, normalized)
        if dist < best_dist:
            best_dist = dist
            best_match = company
    return best_match


def _is_enriched(company: Company) -> bool:
    """Check if a company has already been enriched."""
    return company.enriched_at is not None


# ---------------------------------------------------------------------------
# Step 1: Company Resolution
# ---------------------------------------------------------------------------

async def resolve_companies(session: AsyncSession) -> dict:
    """Link all jobs with null company_id to company records.

    Three-phase approach:
    1. Clean: Delete junk Company records, null out their job FKs
    2. Build lookup: Exact (lowercase) + normalized + fuzzy match
    3. Resolve: For each unlinked job.company name → exact → normalized → fuzzy → create new
    """
    stats = {"matched": 0, "created": 0, "linked": 0, "junk_skipped": 0, "merged": 0}

    # --- Phase 1: Clean junk companies ---
    existing_result = await session.execute(select(Company))
    existing_companies = list(existing_result.scalars().all())

    junk_ids = []
    for company in existing_companies:
        if _is_junk_company_name(company.name):
            junk_ids.append(company.id)
            logger.info("Junk company: %s", company.name)

    if junk_ids:
        # Null out job FKs pointing to junk companies
        await session.execute(
            update(Job)
            .where(Job.company_id.in_(junk_ids))
            .values(company_id=None)
        )
        # Delete junk company records
        await session.execute(
            delete(Company).where(Company.id.in_(junk_ids))
        )
        await session.flush()
        stats["junk_skipped"] = len(junk_ids)
        logger.info("Deleted %d junk company records", len(junk_ids))

    # --- Phase 2: Build lookup ---
    existing_result = await session.execute(select(Company))
    existing_companies = list(existing_result.scalars().all())

    # exact_lookup: lowercase name → Company
    exact_lookup: dict[str, Company] = {}
    # norm_lookup: normalized name → Company
    norm_lookup: dict[str, Company] = {}
    # alias_lookup: lowercase alias → Company
    alias_lookup: dict[str, Company] = {}

    for company in existing_companies:
        exact_lookup[company.name.lower().strip()] = company
        norm = _normalize_company_name(company.name)
        norm_lookup[norm] = company
        # Register aliases
        for alias in (company.aliases or []):
            alias_lookup[alias.lower().strip()] = company
            alias_norm = _normalize_company_name(alias)
            if alias_norm:
                norm_lookup[alias_norm] = company

    # --- Phase 3: Resolve unlinked jobs ---
    distinct_result = await session.execute(
        select(Job.company, func.count(Job.id).label("job_count"))
        .where(Job.company_id.is_(None))
        .group_by(Job.company)
    )
    unlinked = list(distinct_result.all())

    for company_name, job_count in unlinked:
        # Skip junk names from job records
        if _is_junk_company_name(company_name):
            stats["junk_skipped"] += 1
            logger.info("Skipping junk job company name: %s", company_name)
            continue

        norm = _normalize_company_name(company_name)

        # Try exact match → alias → normalized → fuzzy
        company = exact_lookup.get(company_name.lower().strip())

        if company is None:
            company = alias_lookup.get(company_name.lower().strip())

        if company is None:
            company = norm_lookup.get(norm)

        if company is None:
            company = _find_merge_candidates(norm_lookup, norm)
            if company:
                stats["merged"] += 1
                logger.info(
                    "Fuzzy merged '%s' → '%s'", company_name, company.name
                )

        if company is None:
            company = Company(name=company_name, monitoring_active=False)
            session.add(company)
            await session.flush()
            exact_lookup[company_name.lower().strip()] = company
            norm_lookup[norm] = company
            stats["created"] += 1
            logger.info("Created company: %s (%d jobs)", company_name, job_count)
        else:
            stats["matched"] += 1

        # Link all jobs with this company name
        await session.execute(
            update(Job)
            .where(Job.company == company_name, Job.company_id.is_(None))
            .values(company_id=company.id)
        )
        stats["linked"] += job_count

    await session.commit()
    logger.info(
        "Company resolution: %d matched, %d created, %d merged, %d junk, %d jobs linked",
        stats["matched"],
        stats["created"],
        stats["merged"],
        stats["junk_skipped"],
        stats["linked"],
    )
    return stats


# ---------------------------------------------------------------------------
# Step 2: Company Enrichment (web fetch + LLM summarize)
# ---------------------------------------------------------------------------

async def _web_search_compat(query: str, brave_api_key: str = "") -> list[dict]:
    """Search the web using SearXNG (with Brave fallback).

    Returns results in the legacy format with 'description' key for backward compat.
    """
    from app.services.web_search import web_search as unified_search
    results = await unified_search(query, num_results=5)
    return [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "description": r.get("snippet", ""),
        }
        for r in results
    ]


async def _fetch_page(url: str, *, max_chars: int = 8000) -> str:
    """Fetch a web page and return text content (tags stripped)."""
    try:
        async with httpx.AsyncClient(
            timeout=20, follow_redirects=True
        ) as client:
            resp = await client.get(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (compatible; JobBoardBot/1.0)"
                    )
                },
            )
            resp.raise_for_status()
            html = resp.text

        # Simple HTML → text
        text = re.sub(
            r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.I
        )
        text = re.sub(
            r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.I
        )
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]
    except Exception:
        logger.warning("Failed to fetch: %s", url)
        return ""


async def _gather_company_context(
    company: Company,
    job_titles: list[str],
) -> dict:
    """Gather web content about a company for LLM summarization."""
    context: dict = {
        "website_text": "",
        "about_text": "",
        "news": [],
        "job_titles": job_titles,
    }

    website = company.website

    # Find website if missing
    if not website:
        results = await _web_search_compat(
            f'"{company.name}" company website'
        )
        if results:
            website = results[0].get("url")
            if website:
                company.website = website

    # Fetch homepage
    if website:
        context["website_text"] = await _fetch_page(website, max_chars=4000)

        # Try subpages
        base_url = website.rstrip("/")
        for path in ("/about", "/about-us", "/research", "/blog"):
            about_text = await _fetch_page(
                f"{base_url}{path}", max_chars=3000
            )
            if len(about_text) > 200:
                context["about_text"] = about_text
                break

    # Search for recent news
    context["news"] = await _web_search_compat(
        f'"{company.name}" AI research 2025 2026'
    )

    return context


async def _gather_job_description_context(
    session: AsyncSession,
    company: Company,
) -> str:
    """Pull job descriptions as fallback context when web sources fail.

    Returns up to 5 longest descriptions (800 chars each, ~4K total).
    Many HN-sourced companies have no website but rich job descriptions
    with "About us" preambles.
    """
    # Try by company_id first, fall back to name match
    result = await session.execute(
        select(Job.description)
        .where(Job.company_id == company.id)
        .where(Job.description.isnot(None))
        .order_by(func.length(Job.description).desc())
        .limit(5)
    )
    descriptions = [row[0] for row in result.all() if row[0]]

    if not descriptions:
        result = await session.execute(
            select(Job.description)
            .where(Job.company == company.name)
            .where(Job.description.isnot(None))
            .order_by(func.length(Job.description).desc())
            .limit(5)
        )
        descriptions = [row[0] for row in result.all() if row[0]]

    if not descriptions:
        return ""

    parts = []
    for desc in descriptions:
        # Take first 800 chars of each description
        parts.append(desc[:800])

    return "\n\n---\n\n".join(parts)


async def enrich_single_company(
    session: AsyncSession,
    company: Company,
    job_titles: list[str],
    brave_api_key: str = "",
    *,
    force: bool = False,
) -> bool:
    """Enrich a single company with LLM-generated notes. Returns True on success."""
    # Skip already-enriched unless forced
    if _is_enriched(company) and not force:
        logger.info("Skipping already-enriched: %s", company.name)
        return False

    context = await _gather_company_context(company, job_titles)

    # Check if we got any web content
    has_web_content = bool(context["website_text"] or context["about_text"] or context["news"])

    # Build source material for the LLM
    source_parts: list[str] = []
    if context["website_text"]:
        source_parts.append(
            f"Website content:\n{context['website_text'][:3000]}"
        )
    if context["about_text"]:
        source_parts.append(
            f"About/Research page:\n{context['about_text'][:2000]}"
        )
    if context["news"]:
        news_text = "\n".join(
            f"- {r['title']}: {r['description']}" for r in context["news"]
        )
        source_parts.append(f"Recent news:\n{news_text}")

    # Fallback: use job descriptions when web sources fail
    if not has_web_content:
        jd_context = await _gather_job_description_context(session, company)
        if jd_context:
            source_parts.append(
                f"Job descriptions from this company (use for company context):\n{jd_context}"
            )

    if job_titles:
        source_parts.append(
            "Job titles at this company:\n"
            + "\n".join(f"- {t}" for t in job_titles[:20])
        )

    if not source_parts:
        logger.warning("No context available for %s, skipping", company.name)
        return False

    source_text = "\n\n".join(source_parts)

    # Load profile data for dynamic system prompt
    profile_result = await session.execute(select(UserProfile).limit(1))
    profile = profile_result.scalar_one_or_none()
    profile_data = profile.data if profile else {}
    enrichment_system = build_company_enrichment_system(profile_data)

    # Call LLM
    client = get_openai_client()
    async with _enrichment_semaphore:
        response = await client.chat.completions.create(
            model=EXTRACTION_MODEL,
            max_completion_tokens=800,
            temperature=0,
            messages=[
                {"role": "system", "content": enrichment_system},
                {
                    "role": "user",
                    "content": (
                        f"Company name: {company.name}\n"
                        f"Website: {company.website or 'unknown'}\n\n"
                        f"{source_text}"
                    ),
                },
            ],
        )

    notes = response.choices[0].message.content.strip()
    company.notes = notes
    company.enriched_at = datetime.now(timezone.utc)
    await session.commit()
    logger.info("Enriched: %s (%d chars)", company.name, len(notes))
    return True


# ---------------------------------------------------------------------------
# Step 3: Team Extraction
# ---------------------------------------------------------------------------

_SKIP_TEAM_PATTERNS = re.compile(
    r"^(Senior|Junior|Staff|Principal|Lead|Sr\.?|Jr\.?|Remote|Hybrid|"
    r"NYC|SF|New York|San Francisco|Backend|Frontend|Fullstack|Full Stack|"
    r"Part.time|Full.time|\d)",
    re.IGNORECASE,
)


def _extract_team_from_job(job: Job) -> str | None:
    """Extract team name from job title or description opening."""
    title = job.title or ""

    # Pattern: "Role, Team Name" or "Role — Team Name" or "Role - Team Name"
    for sep in (", ", " — ", " – ", " - "):
        if sep in title:
            parts = title.split(sep, 1)
            candidate = parts[1].strip()
            if candidate and len(candidate) > 3 and not _SKIP_TEAM_PATTERNS.match(candidate):
                return candidate

    # Pattern: "Role (Team Name)"
    paren_match = re.search(r"\(([^)]+)\)", title)
    if paren_match:
        candidate = paren_match.group(1).strip()
        if not _SKIP_TEAM_PATTERNS.match(candidate):
            return candidate

    # Check description opening for explicit team mentions
    desc = (job.description or "")[:300]
    team_match = re.search(
        r"(?:team|group|org(?:anization)?|department):\s*([^\n.]+)",
        desc,
        re.IGNORECASE,
    )
    if team_match:
        return team_match.group(1).strip()

    return None


async def extract_teams(session: AsyncSession) -> int:
    """For companies with 10+ jobs, extract team names into extra_metadata."""
    large_result = await session.execute(
        select(Job.company_id, func.count(Job.id).label("cnt"))
        .where(Job.company_id.isnot(None))
        .group_by(Job.company_id)
        .having(func.count(Job.id) >= 10)
    )
    large_companies = list(large_result.all())

    count = 0
    for company_id, _ in large_companies:
        jobs_result = await session.execute(
            select(Job).where(Job.company_id == company_id)
        )
        jobs = list(jobs_result.scalars().all())

        for job in jobs:
            team_name = _extract_team_from_job(job)
            if team_name:
                meta = dict(job.extra_metadata or {})
                if meta.get("team_name") != team_name:
                    meta["team_name"] = team_name
                    job.extra_metadata = meta
                    count += 1

        await session.commit()

    logger.info("Extracted team names for %d jobs", count)
    return count


# ---------------------------------------------------------------------------
# Full Pipeline
# ---------------------------------------------------------------------------

async def run_enrichment_pipeline(
    session: AsyncSession,
    brave_api_key: str = "",
    *,
    force: bool = False,
    limit: int = 0,
) -> dict:
    """Run the full company enrichment pipeline (Steps 1-3)."""
    global _enrichment_status

    if _enrichment_status["running"]:
        raise RuntimeError("Enrichment pipeline is already running")

    _enrichment_status = {
        "running": True,
        "phase": "resolving",
        "total_companies": 0,
        "resolved": 0,
        "enriched": 0,
        "skipped": 0,
        "teams_extracted": 0,
        "failed": 0,
        "failed_companies": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        # Step 1: Resolve companies
        resolve_stats = await resolve_companies(session)
        _enrichment_status["resolved"] = (
            resolve_stats["created"] + resolve_stats["matched"]
        )

        # Step 2: Enrich companies
        _enrichment_status["phase"] = "enriching"

        all_companies_result = await session.execute(
            select(Company).order_by(Company.name)
        )
        all_companies = list(all_companies_result.scalars().all())

        companies_to_enrich = [
            c for c in all_companies if force or not _is_enriched(c)
        ]
        if limit > 0:
            companies_to_enrich = companies_to_enrich[:limit]
        _enrichment_status["total_companies"] = len(companies_to_enrich)
        _enrichment_status["skipped"] = len(all_companies) - len(companies_to_enrich)

        for company in companies_to_enrich:
            # Get job titles for context
            titles_result = await session.execute(
                select(Job.title)
                .where(Job.company_id == company.id)
                .limit(30)
            )
            job_titles = [row[0] for row in titles_result.all()]

            # Fallback: match by company name string
            if not job_titles:
                titles_result = await session.execute(
                    select(Job.title)
                    .where(Job.company == company.name)
                    .limit(30)
                )
                job_titles = [row[0] for row in titles_result.all()]

            try:
                success = await enrich_single_company(
                    session, company, job_titles, force=force
                )
                if success:
                    _enrichment_status["enriched"] += 1
                else:
                    _enrichment_status["failed"] += 1
                    _enrichment_status["failed_companies"].append(
                        {"name": company.name, "reason": "no context available"}
                    )
            except Exception as exc:
                _enrichment_status["failed"] += 1
                _enrichment_status["failed_companies"].append(
                    {"name": company.name, "reason": str(exc)[:200]}
                )
                logger.exception("Failed to enrich: %s", company.name)

            await asyncio.sleep(_INTER_ENRICHMENT_DELAY)

        # Step 3: Extract teams
        _enrichment_status["phase"] = "extracting_teams"
        teams_count = await extract_teams(session)
        _enrichment_status["teams_extracted"] = teams_count

        _enrichment_status["phase"] = "complete"

    except Exception:
        _enrichment_status["phase"] = "failed"
        logger.exception("Enrichment pipeline failed")
        raise
    finally:
        _enrichment_status["running"] = False

    return dict(_enrichment_status)


async def enrich_single(
    session: AsyncSession,
    company_id,
    brave_api_key: str = "",
    *,
    force: bool = False,
) -> Company | None:
    """Enrich a single company by ID."""
    result = await session.execute(
        select(Company).where(Company.id == company_id)
    )
    company = result.scalar_one_or_none()
    if not company:
        return None

    titles_result = await session.execute(
        select(Job.title).where(Job.company_id == company.id).limit(30)
    )
    job_titles = [row[0] for row in titles_result.all()]

    await enrich_single_company(
        session, company, job_titles, force=force
    )
    await session.refresh(company)
    return company


def get_enrichment_status() -> dict:
    """Return current enrichment pipeline progress."""
    return dict(_enrichment_status)
