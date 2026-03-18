"""Company discovery — self-improving URL resolution, relevance scoring, discover + import.

URL resolution pipeline:
  Step 1 — Fast path (no LLM):
    1a. Domain cache lookup (DB-backed)
    1b. Regex on known ATS domains (hardcoded)
    1c. Fetch HTML → hardcoded fingerprint patterns
    1d. Fetch HTML → learned fingerprint patterns (from DB)
    1e. Domain slug probing (derive slugs from domain, try all 3 ATS APIs)
    → Every verified result persists to domain cache

  Step 2 — LLM fallback (only if Step 1 fails):
    2a. Send HTML to LLM
    2b. LLM returns: {ats, slug} + {suggested_pattern}
    2c. API-verify the ats+slug
    2d. Persist domain mapping
    2e. Sanity-check suggested_pattern against the HTML
    2f. If pattern passes, persist to learned_patterns table
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models.ats_learning import AtsDomainCache, AtsLearnedPattern
from app.models.companies import Company
from app.models.jobs import Job, JobSource, JobStatus
from app.models.profile import UserProfile
from app.scrapers.ashby import AshbyScraper
from app.scrapers.base import ScrapedJob
from app.scrapers.greenhouse import GreenhouseScraper
from app.scrapers.lever import LeverScraper

logger = logging.getLogger(__name__)


# ===========================================================================
# URL Resolution data types
# ===========================================================================

@dataclass
class URLResolution:
    """Result of the multi-prong URL resolution pipeline."""

    ats: str | None = None  # "greenhouse", "lever", "ashby"
    slug: str | None = None
    job_url: str = ""
    method: str = "unresolved"  # "regex", "fingerprint", "llm", "cache", "learned_pattern", "unresolved"
    confidence: str = "none"  # "high", "medium", "low", "none"
    careers_url: str | None = None
    error: str | None = None


# ===========================================================================
# In-process pattern cache (loaded from DB, refreshed every 5 min)
# ===========================================================================

class _LearnedPatternCache:
    """In-memory cache of ats_domain_cache and ats_learned_patterns tables.

    Single-user single-container app — no cross-process coordination needed.
    """

    _TTL_SECONDS = 300  # 5 minutes

    def __init__(self) -> None:
        self._domains: dict[str, tuple[str, str]] = {}  # hostname → (ats, slug)
        self._patterns: list[tuple[re.Pattern, str, int | None, object]] = []  # (compiled, ats, slug_group, row_id)
        self._last_refresh: float = 0
        self._loaded: bool = False

    async def _refresh_if_needed(self) -> None:
        now = time.monotonic()
        if self._loaded and (now - self._last_refresh) < self._TTL_SECONDS:
            return
        await self._load()

    async def _load(self) -> None:
        try:
            async with async_session() as sess:
                # Load domain cache
                rows = (await sess.execute(select(AtsDomainCache))).scalars().all()
                self._domains = {r.domain: (r.ats, r.slug) for r in rows}

                # Load active learned patterns
                rows = (
                    await sess.execute(
                        select(AtsLearnedPattern).where(AtsLearnedPattern.is_active.is_(True))
                    )
                ).scalars().all()
                patterns = []
                for r in rows:
                    try:
                        compiled = re.compile(r.pattern)
                        patterns.append((compiled, r.ats, r.slug_group, r.id))
                    except re.error:
                        logger.warning("Invalid learned pattern (id=%s): %s", r.id, r.pattern)
                self._patterns = patterns

            self._last_refresh = time.monotonic()
            self._loaded = True
            logger.debug(
                "Pattern cache refreshed: %d domains, %d patterns",
                len(self._domains), len(self._patterns),
            )
        except Exception:
            logger.warning("Failed to refresh pattern cache", exc_info=True)

    async def lookup_domain(self, hostname: str) -> tuple[str, str] | None:
        """Check if domain has a cached ATS mapping. Returns (ats, slug) or None."""
        await self._refresh_if_needed()
        return self._domains.get(hostname)

    async def match_patterns(self, html: str) -> tuple[str, str | None, object] | None:
        """Try learned patterns against HTML. Returns (ats, slug_or_None, pattern_id) or None."""
        await self._refresh_if_needed()
        for compiled, ats, slug_group, pattern_id in self._patterns:
            m = compiled.search(html)
            if m:
                slug = None
                if slug_group is not None:
                    try:
                        slug = m.group(slug_group)
                    except (IndexError, re.error):
                        pass
                return (ats, slug, pattern_id)
        return None

    def update_domain(self, hostname: str, ats: str, slug: str) -> None:
        """Immediate in-process update after a write."""
        self._domains[hostname] = (ats, slug)

    def add_pattern(self, compiled: re.Pattern, ats: str, slug_group: int | None, pattern_id: object) -> None:
        """Immediate in-process update after persisting a new pattern."""
        self._patterns.append((compiled, ats, slug_group, pattern_id))


_pattern_cache = _LearnedPatternCache()


# ===========================================================================
# Step 1b: Regex match on known ATS domains
# ===========================================================================

# Greenhouse: boards.greenhouse.io/{slug}/jobs/{id} or {slug}.greenhouse.io/...
_GREENHOUSE_PATTERNS = [
    re.compile(r"boards\.greenhouse\.io/(?P<slug>[^/]+)/jobs/(?P<id>\d+)"),
    re.compile(r"(?P<slug>[^.]+)\.greenhouse\.io"),
]

# Lever: jobs.lever.co/{slug}/{id}
_LEVER_PATTERNS = [
    re.compile(r"jobs\.lever\.co/(?P<slug>[^/]+)(?:/(?P<id>[a-f0-9-]+))?"),
]

# Ashby: jobs.ashbyhq.com/{slug}/{id} or {slug}.ashbyhq.com/...
_ASHBY_PATTERNS = [
    re.compile(r"jobs\.ashbyhq\.com/(?P<slug>[^/]+)(?:/(?P<id>[a-f0-9-]+))?"),
    re.compile(r"(?P<slug>[^.]+)\.ashbyhq\.com"),
]


def _regex_match(url: str) -> URLResolution | None:
    """Step 1b: Try regex patterns against known ATS domains."""
    for pattern in _GREENHOUSE_PATTERNS:
        m = pattern.search(url)
        if m:
            return URLResolution(
                ats="greenhouse", slug=m.group("slug"), job_url=url,
                method="regex", confidence="high",
            )

    for pattern in _LEVER_PATTERNS:
        m = pattern.search(url)
        if m:
            return URLResolution(
                ats="lever", slug=m.group("slug"), job_url=url,
                method="regex", confidence="high",
            )

    for pattern in _ASHBY_PATTERNS:
        m = pattern.search(url)
        if m:
            return URLResolution(
                ats="ashby", slug=m.group("slug"), job_url=url,
                method="regex", confidence="high",
            )

    return None


# ===========================================================================
# API verification — lightweight probe to confirm a slug is valid
# ===========================================================================

async def _verify_ats_slug(
    ats: str, slug: str, http_client: httpx.AsyncClient
) -> bool:
    """Hit the ATS API to verify a slug actually exists. Returns True if valid."""
    try:
        if ats == "greenhouse":
            url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
            resp = await http_client.get(url, params={"per_page": "1"})
            return resp.status_code == 200

        elif ats == "lever":
            url = f"https://api.lever.co/v0/postings/{slug}"
            resp = await http_client.get(url, params={"mode": "json", "limit": "1"})
            return resp.status_code == 200

        elif ats == "ashby":
            from app.scrapers.ashby import ASHBY_GQL, LIST_QUERY
            resp = await http_client.post(
                ASHBY_GQL,
                json={
                    "operationName": "ApiJobBoardWithTeams",
                    "variables": {"organizationHostedJobsPageName": slug},
                    "query": LIST_QUERY,
                },
            )
            if resp.status_code != 200:
                return False
            data = resp.json()
            board = data.get("data", {}).get("jobBoard")
            return board is not None

    except Exception:
        logger.debug("Verification failed for %s/%s", ats, slug)
    return False


# ===========================================================================
# Step 1c: HTML fingerprinting (hardcoded patterns)
# ===========================================================================

async def _fetch_raw_html(
    url: str, http_client: httpx.AsyncClient, *, max_bytes: int = 500_000
) -> str:
    """Fetch raw HTML from a URL (follow redirects)."""
    try:
        resp = await http_client.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; JobBoardBot/1.0)"},
            follow_redirects=True,
        )
        resp.raise_for_status()
        return resp.text[:max_bytes]
    except Exception:
        logger.debug("Failed to fetch HTML from %s", url)
        return ""


# Fingerprint patterns — search raw HTML for ATS references.
# Ordered by specificity: most reliable patterns first.

_FP_GREENHOUSE = [
    # Direct API/board references (most reliable)
    re.compile(r'boards-api\.greenhouse\.io/v1/boards/([^/"\s?]+)'),
    re.compile(r'boards\.greenhouse\.io/([^/"\s?]+)'),
    re.compile(r'greenhouse\.io/embed/job_board/js\?for=([^"&\s]+)'),
    # Hidden form input: <input name="board_token" value="x9" />
    re.compile(r'board_token["\s]+value=["\']([^"\']+)'),
    # Data attribute on elements
    re.compile(r'data-board-token=["\']([^"\']+)'),
]

# Presence markers (no slug — indicates ATS is used, slug extracted via probing)
_GH_PRESENCE = re.compile(r'gh_jid[=:]')
_ASHBY_PRESENCE = re.compile(r'ashby_jid[=:]')

_FP_LEVER = [
    re.compile(r'jobs\.lever\.co/([^/"\s?]+)'),
    re.compile(r'api\.lever\.co/v0/postings/([^/"\s?]+)'),
]

_FP_ASHBY = [
    re.compile(r'jobs\.ashbyhq\.com/([^/"\s?]+)'),
    re.compile(r'ashbyhq\.com/api.*?organizationHostedJobsPageName.*?["\']([^"\']+)'),
]

# Slug extraction from Greenhouse JSON blobs embedded in HTML.
# Many custom-domain pages embed the full Greenhouse API response as JSON
# which contains the board_token or can be probed by domain-derived slugs.
_GH_JSON_SLUG = re.compile(
    r'"board_token"\s*:\s*"([^"]+)"'
)


def _fingerprint_html(html: str) -> tuple[str, str] | None:
    """Scan HTML for ATS markers. Returns (ats, slug) or None."""
    # --- Greenhouse ---
    for pattern in _FP_GREENHOUSE:
        m = pattern.search(html)
        if m:
            slug = m.group(1)
            if slug not in ("js", "api", "embed", "v1", "boards", ""):
                logger.info("Fingerprinted Greenhouse slug: %s", slug)
                return ("greenhouse", slug)

    # Greenhouse JSON blob (e.g. "board_token": "x9")
    m = _GH_JSON_SLUG.search(html)
    if m:
        slug = m.group(1)
        if slug:
            logger.info("Fingerprinted Greenhouse slug from JSON: %s", slug)
            return ("greenhouse", slug)

    # gh_jid present but no slug found yet — try domain-based slug guesses
    if _GH_PRESENCE.search(html):
        logger.info("Greenhouse presence detected (gh_jid) but no slug extracted")
        # Return a sentinel so the caller knows to try domain-based probing
        return ("greenhouse", "__probe_needed__")

    # --- Lever ---
    for pattern in _FP_LEVER:
        m = pattern.search(html)
        if m:
            slug = m.group(1)
            if slug not in ("v0", "api"):
                logger.info("Fingerprinted Lever slug: %s", slug)
                return ("lever", slug)

    # --- Ashby ---
    for pattern in _FP_ASHBY:
        m = pattern.search(html)
        if m:
            slug = m.group(1)
            if slug not in ("api", "non-user-graphql"):
                logger.info("Fingerprinted Ashby slug: %s", slug)
                return ("ashby", slug)

    # ashby_jid present but no slug found — need probing
    if _ASHBY_PRESENCE.search(html):
        logger.info("Ashby presence detected (ashby_jid) but no slug extracted")
        return ("ashby", "__probe_needed__")

    return None


# ===========================================================================
# Step 1e: Domain-based slug probing
# ===========================================================================

def _slug_candidates_from_url(url: str) -> list[str]:
    """Generate plausible ATS slug candidates from a URL's domain.

    E.g. https://x.company/careers/123 → ["x", "xcompany", "x-company"]
         https://www.adaptionlabs.ai/careers → ["adaptionlabs", "adaption-labs", "adaption", ...]
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""

    # Strip www
    host = re.sub(r"^www\.", "", host)
    parts = host.split(".")
    tlds = {"com", "org", "net", "io", "ai", "co", "dev", "tech", "company", "jobs", "careers"}

    candidates: list[str] = []

    def _add(s: str) -> None:
        s = s.lower().strip()
        if s and s not in candidates and len(s) >= 1:
            candidates.append(s)

    # First part of domain (most common slug): "adaptionlabs"
    if parts:
        _add(parts[0])

    # Full domain minus TLD parts: "adaptionlabs"
    non_tld = [p for p in parts if p not in tlds]
    if non_tld:
        _add("".join(non_tld))
        _add("-".join(non_tld))

    # Try splitting compound words in the primary domain part.
    # "adaptionlabs" → try removing common suffixes: "labs", "lab", "ai", "hq", "io", "tech"
    primary = parts[0] if parts else ""
    for suffix in ("labs", "lab", "ai", "hq", "io", "tech", "dev", "app", "inc"):
        if primary.endswith(suffix) and len(primary) > len(suffix) + 2:
            stem = primary[: -len(suffix)]
            _add(stem)  # "adaption"
            _add(f"{stem}-{suffix}")  # "adaption-labs"

    # Full domain with TLD joined
    if len(parts) >= 2:
        _add("".join(parts))

    return candidates


async def _probe_domain_slugs(
    ats: str, url: str, http_client: httpx.AsyncClient
) -> str | None:
    """Try domain-derived slug candidates against an ATS API."""
    candidates = _slug_candidates_from_url(url)
    logger.info("Probing %s with slug candidates: %s", ats, candidates)

    for slug in candidates:
        if await _verify_ats_slug(ats, slug, http_client):
            logger.info("Probed slug verified: %s/%s", ats, slug)
            return slug

    return None


# ===========================================================================
# Persistence helpers (use own sessions so learnings survive regardless)
# ===========================================================================

def _hostname_from_url(url: str) -> str:
    """Extract hostname from URL, stripping www."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    return re.sub(r"^www\.", "", host)


async def _persist_domain_mapping(url: str, ats: str, slug: str, method: str) -> None:
    """Upsert into ats_domain_cache and update in-process cache."""
    domain = _hostname_from_url(url)
    if not domain:
        return
    try:
        async with async_session() as sess:
            stmt = pg_insert(AtsDomainCache).values(
                domain=domain, ats=ats, slug=slug, source_method=method,
            ).on_conflict_do_update(
                index_elements=["domain"],
                set_={"ats": ats, "slug": slug, "source_method": method,
                       "updated_at": datetime.now(timezone.utc)},
            )
            await sess.execute(stmt)
            await sess.commit()
        _pattern_cache.update_domain(domain, ats, slug)
        logger.info("Persisted domain mapping: %s → %s/%s (via %s)", domain, ats, slug, method)
    except Exception:
        logger.warning("Failed to persist domain mapping for %s", domain, exc_info=True)


async def _record_cache_hit(domain: str) -> None:
    """Increment hit_count and update last_hit_at on a domain cache entry."""
    try:
        async with async_session() as sess:
            stmt = (
                update(AtsDomainCache)
                .where(AtsDomainCache.domain == domain)
                .values(
                    hit_count=AtsDomainCache.hit_count + 1,
                    last_hit_at=datetime.now(timezone.utc),
                )
            )
            await sess.execute(stmt)
            await sess.commit()
    except Exception:
        logger.debug("Failed to record cache hit for %s", domain)


def _sanity_check_pattern(
    regex_str: str, slug_group: int | None, html: str, expected_slug: str | None,
) -> bool:
    """Validate a learned pattern before persisting.

    Returns True only if the pattern compiles, matches the HTML, and (if slug_group is set)
    extracts the expected slug.
    """
    if len(regex_str) < 10:
        logger.debug("Pattern too short (%d chars), rejecting", len(regex_str))
        return False

    try:
        compiled = re.compile(regex_str)
    except re.error as e:
        logger.debug("Pattern failed to compile: %s — %s", regex_str, e)
        return False

    m = compiled.search(html)
    if not m:
        logger.debug("Pattern didn't match the source HTML")
        return False

    if slug_group is not None and expected_slug:
        try:
            extracted = m.group(slug_group)
        except (IndexError, re.error):
            logger.debug("slug_group %d not found in match", slug_group)
            return False
        if extracted != expected_slug:
            logger.debug(
                "Extracted slug %r != expected %r", extracted, expected_slug,
            )
            return False

    return True


async def _persist_learned_pattern(
    ats: str, regex_str: str, slug_group: int | None,
    description: str | None, source_url: str,
) -> None:
    """Insert a new learned pattern and update in-process cache."""
    try:
        compiled = re.compile(regex_str)
    except re.error:
        return

    try:
        async with async_session() as sess:
            stmt = pg_insert(AtsLearnedPattern).values(
                ats=ats, pattern=regex_str, slug_group=slug_group,
                description=description, source_url=source_url,
            ).on_conflict_do_nothing(index_elements=["pattern"])
            result = await sess.execute(stmt)
            await sess.commit()

            if result.rowcount:
                # Fetch the row to get its id
                row = (
                    await sess.execute(
                        select(AtsLearnedPattern).where(AtsLearnedPattern.pattern == regex_str)
                    )
                ).scalar_one_or_none()
                if row:
                    _pattern_cache.add_pattern(compiled, ats, slug_group, row.id)
                    logger.info("Persisted learned pattern: %s (ats=%s)", regex_str, ats)
            else:
                logger.debug("Learned pattern already exists: %s", regex_str)
    except Exception:
        logger.warning("Failed to persist learned pattern", exc_info=True)


async def _record_pattern_failure(pattern_id: object) -> None:
    """Increment failed_count on a learned pattern; auto-disable if >= 3 failures."""
    try:
        async with async_session() as sess:
            stmt = (
                update(AtsLearnedPattern)
                .where(AtsLearnedPattern.id == pattern_id)
                .values(failed_count=AtsLearnedPattern.failed_count + 1)
            )
            await sess.execute(stmt)

            # Auto-disable if too many failures
            stmt = (
                update(AtsLearnedPattern)
                .where(
                    AtsLearnedPattern.id == pattern_id,
                    AtsLearnedPattern.failed_count >= 3,
                )
                .values(is_active=False)
            )
            await sess.execute(stmt)
            await sess.commit()
            logger.info("Recorded pattern failure for pattern_id=%s", pattern_id)
    except Exception:
        logger.warning("Failed to record pattern failure", exc_info=True)


async def _increment_pattern_verified(pattern_id: object) -> None:
    """Increment verified_count on a learned pattern after successful use."""
    try:
        async with async_session() as sess:
            stmt = (
                update(AtsLearnedPattern)
                .where(AtsLearnedPattern.id == pattern_id)
                .values(verified_count=AtsLearnedPattern.verified_count + 1)
            )
            await sess.execute(stmt)
            await sess.commit()
    except Exception:
        logger.debug("Failed to increment verified count for pattern %s", pattern_id)


# ===========================================================================
# Step 2: LLM analysis (OpenAI) — enhanced with pattern teachback
# ===========================================================================

_LLM_SYSTEM = """\
You are an expert at identifying Applicant Tracking Systems (ATS) from career page HTML.

Given raw HTML from a company careers page, identify:
1. The ATS platform used (Greenhouse, Lever, Ashby, or null if unknown)
2. The company slug/identifier in that ATS

Also suggest a **generalizable regex pattern** that would detect this ATS integration \
in HTML from other companies using the same setup. This pattern will be used to \
automatically detect the ATS on future pages without needing LLM analysis.

Look for:
- References to greenhouse.io, lever.co, ashbyhq.com in links, scripts, iframes, fetch calls
- Embedded job board widgets, iframe sources, API endpoint calls
- JavaScript that loads job listings from an external ATS
- URL patterns: boards.greenhouse.io/{slug}, jobs.lever.co/{slug}, jobs.ashbyhq.com/{slug}
- Greenhouse board tokens in script tags or data attributes
- Hidden form inputs with board_token values
- JSON data embedded in script tags with ATS identifiers

Pattern guidelines:
- Use a numbered capture group (e.g. group 1) for the slug if extractable
- Make patterns general — do NOT include the specific company name or domain
- Presence-only patterns (no slug group) are fine when slug is in JS/API calls
- Minimum 10 characters, should match a structural marker in the HTML

Return ONLY a JSON object (no markdown, no explanation):
{
  "ats": "greenhouse" | "lever" | "ashby" | null,
  "slug": "company-slug" | null,
  "confidence": "high" | "medium" | "low",
  "reasoning": "brief explanation",
  "suggested_pattern": {
    "regex": "python regex that would detect this ATS in the HTML",
    "slug_group": 1 | null,
    "description": "what this pattern detects and why it generalizes"
  } | null
}"""


async def _llm_identify_ats(html: str, url: str) -> dict | None:
    """Use OpenAI to analyze HTML and identify the ATS.

    Returns full parsed dict with ats, slug, confidence, reasoning, suggested_pattern.
    Returns None on failure.
    """
    try:
        from app.ai.client import get_openai_client, EXTRACTION_MODEL
    except Exception:
        logger.warning("OpenAI client not available for LLM ATS detection")
        return None

    # Truncate HTML — keep head + scripts + first chunk of body
    truncated = html[:30_000]

    try:
        client = get_openai_client()
        response = await client.chat.completions.create(
            model=EXTRACTION_MODEL,
            max_tokens=500,
            temperature=0,
            messages=[
                {"role": "system", "content": _LLM_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"URL: {url}\n\n"
                        f"HTML content (truncated):\n{truncated}"
                    ),
                },
            ],
        )

        text = response.choices[0].message.content.strip()
        # Parse JSON from response (handle markdown code blocks)
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text)

        ats = result.get("ats")
        slug = result.get("slug")
        confidence = result.get("confidence", "low")

        if ats and slug:
            logger.info(
                "LLM identified ATS: %s/%s (confidence: %s, reason: %s)",
                ats, slug, confidence, result.get("reasoning", ""),
            )
            return result

    except Exception:
        logger.warning("LLM ATS identification failed for %s", url, exc_info=True)

    return None


# ===========================================================================
# Main resolution pipeline
# ===========================================================================

async def resolve_job_url(url: str) -> URLResolution:
    """Self-improving URL resolution pipeline.

    Step 1 — Fast path (no LLM):
      1a. Domain cache lookup
      1b. Regex match on known ATS domains
      1c. Hardcoded HTML fingerprinting
      1d. Learned pattern matching
      1e. Domain slug probing
    Step 2 — LLM fallback (teaches Step 1 for next time)
    """
    async with httpx.AsyncClient(timeout=30.0) as http_client:

        # --- Step 1a: Domain cache lookup ---
        hostname = _hostname_from_url(url)
        cached = await _pattern_cache.lookup_domain(hostname)
        if cached:
            ats, slug = cached
            verified = await _verify_ats_slug(ats, slug, http_client)
            if verified:
                logger.info("Step 1a (cache): %s/%s verified", ats, slug)
                # Record hit in background (fire-and-forget intent, but await for safety)
                await _record_cache_hit(hostname)
                return URLResolution(
                    ats=ats, slug=slug, job_url=url,
                    method="cache", confidence="high",
                )
            else:
                logger.info("Step 1a (cache): %s/%s stale, continuing pipeline", ats, slug)

        # --- Step 1b: Regex ---
        result = _regex_match(url)
        if result:
            verified = await _verify_ats_slug(result.ats, result.slug, http_client)
            if verified:
                logger.info("Step 1b (regex): %s/%s verified", result.ats, result.slug)
                await _persist_domain_mapping(url, result.ats, result.slug, "regex")
                return result
            else:
                logger.info(
                    "Step 1b (regex): %s/%s matched but failed verification",
                    result.ats, result.slug,
                )

        # --- Step 1c: Hardcoded HTML fingerprint ---
        html = await _fetch_raw_html(url, http_client)
        if html:
            fp = _fingerprint_html(html)
            if fp:
                ats, slug = fp

                # If fingerprinting detected the ATS but couldn't extract the slug,
                # try probing with slugs derived from the domain name.
                if slug == "__probe_needed__":
                    probed_slug = await _probe_domain_slugs(ats, url, http_client)
                    if probed_slug:
                        slug = probed_slug
                    else:
                        slug = None

                if slug:
                    verified = await _verify_ats_slug(ats, slug, http_client)
                    if verified:
                        logger.info("Step 1c (fingerprint): %s/%s verified", ats, slug)
                        await _persist_domain_mapping(url, ats, slug, "fingerprint")
                        return URLResolution(
                            ats=ats, slug=slug, job_url=url,
                            method="fingerprint", confidence="high",
                            careers_url=url,
                        )
                    else:
                        logger.info(
                            "Step 1c (fingerprint): %s/%s found but failed verification",
                            ats, slug,
                        )

        # --- Step 1d: Learned patterns ---
        if html:
            lp = await _pattern_cache.match_patterns(html)
            if lp:
                ats, slug, pattern_id = lp

                # If learned pattern is presence-only (no slug), try probing
                if not slug:
                    slug = await _probe_domain_slugs(ats, url, http_client)

                if slug:
                    verified = await _verify_ats_slug(ats, slug, http_client)
                    if verified:
                        logger.info("Step 1d (learned_pattern): %s/%s verified", ats, slug)
                        await _persist_domain_mapping(url, ats, slug, "learned_pattern")
                        await _increment_pattern_verified(pattern_id)
                        return URLResolution(
                            ats=ats, slug=slug, job_url=url,
                            method="learned_pattern", confidence="high",
                            careers_url=url,
                        )
                    else:
                        logger.info(
                            "Step 1d (learned_pattern): %s/%s failed verification",
                            ats, slug,
                        )
                        await _record_pattern_failure(pattern_id)

        # --- Step 1e: Domain slug probing (try all 3 ATS APIs) ---
        if html:
            for probe_ats in ("greenhouse", "lever", "ashby"):
                probed = await _probe_domain_slugs(probe_ats, url, http_client)
                if probed:
                    logger.info("Step 1e (probe): %s/%s verified", probe_ats, probed)
                    await _persist_domain_mapping(url, probe_ats, probed, "probe")
                    return URLResolution(
                        ats=probe_ats, slug=probed, job_url=url,
                        method="probe", confidence="medium",
                        careers_url=url,
                    )

        # --- Step 2: LLM fallback ---
        if html:
            llm_result = await _llm_identify_ats(html, url)
            if llm_result:
                ats = llm_result["ats"]
                slug = llm_result["slug"]
                confidence = llm_result.get("confidence", "low")

                verified = await _verify_ats_slug(ats, slug, http_client)
                if verified:
                    logger.info("Step 2 (LLM): %s/%s verified", ats, slug)

                    # 2d: Always persist domain mapping (safe after verification)
                    await _persist_domain_mapping(url, ats, slug, "llm")

                    # 2e–2f: Try to learn a pattern from the LLM's suggestion
                    suggested = llm_result.get("suggested_pattern")
                    if suggested and isinstance(suggested, dict):
                        regex_str = suggested.get("regex", "")
                        slug_group = suggested.get("slug_group")
                        description = suggested.get("description")

                        if regex_str and _sanity_check_pattern(
                            regex_str, slug_group, html, slug,
                        ):
                            await _persist_learned_pattern(
                                ats, regex_str, slug_group, description, url,
                            )
                        else:
                            logger.debug(
                                "LLM suggested pattern failed sanity check: %s",
                                regex_str,
                            )

                    return URLResolution(
                        ats=ats, slug=slug, job_url=url,
                        method="llm", confidence=confidence,
                        careers_url=url,
                    )
                else:
                    # LLM found something but API doesn't confirm — still
                    # return it with lower confidence so caller can decide
                    logger.info(
                        "Step 2 (LLM): %s/%s found but unverified", ats, slug,
                    )
                    return URLResolution(
                        ats=ats, slug=slug, job_url=url,
                        method="llm", confidence="low",
                        careers_url=url,
                    )

    # All steps failed
    return URLResolution(
        job_url=url, method="unresolved", confidence="none",
        careers_url=url,
        error="Could not detect ATS platform. Supported: Greenhouse, Lever, Ashby.",
    )


# ===========================================================================
# Relevance scoring (LLM-free)
# ===========================================================================

def _tokenize(text: str) -> set[str]:
    """Lowercase split into word tokens."""
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _build_keyword_sets(profile_data: dict) -> dict:
    """Extract keyword sets from profile YAML data."""
    keywords: dict = {
        "role_titles": set(),
        "domains": set(),
        "technical_skills": set(),
        "industry_keywords": set(),
        "deal_breakers": set(),
        "seniority": set(),
        "remote_preference": None,
    }

    for role in profile_data.get("target_roles", []):
        title = role.get("title", "").lower()
        keywords["role_titles"].add(title)
        sen = role.get("seniority", "")
        if sen:
            keywords["seniority"].add(sen.lower())

    for domain in profile_data.get("domains", []):
        keywords["domains"].add(domain.lower())

    skills = profile_data.get("skills", {})
    for skill in skills.get("technical", []):
        keywords["technical_skills"].add(skill.lower())

    prefs = profile_data.get("search_preferences", {})
    for industry in prefs.get("industries_ranked", []):
        keywords["industry_keywords"] |= _tokenize(industry)

    for breaker in prefs.get("deal_breakers", []):
        keywords["deal_breakers"].add(breaker.lower())

    personal = profile_data.get("personal", {})
    keywords["remote_preference"] = personal.get("remote_preference")

    return keywords


def score_job_relevance(job: ScrapedJob, profile_keywords: dict) -> int:
    """Score a job 0-100 based on keyword matching against user profile."""
    score = 0
    title_lower = job.title.lower()
    desc_lower = (job.description or "").lower()
    title_and_desc = title_lower + " " + desc_lower

    # 1. Title match (0-40)
    title_score = 0
    for role_title in profile_keywords.get("role_titles", set()):
        if role_title in title_lower:
            title_score = max(title_score, 40)
        else:
            role_tokens = _tokenize(role_title)
            title_tokens = _tokenize(title_lower)
            overlap = role_tokens & title_tokens
            if len(overlap) >= 1:
                title_score = max(title_score, 20)

    domain_title_score = 0
    for domain in profile_keywords.get("domains", set()):
        if domain in title_lower:
            domain_title_score += 10
    domain_title_score = min(domain_title_score, 30)
    title_score = min(title_score + domain_title_score, 40)
    score += title_score

    # 2. Description match (0-40)
    hits = 0
    all_keywords = set()
    all_keywords |= profile_keywords.get("domains", set())
    all_keywords |= profile_keywords.get("technical_skills", set())
    all_keywords |= profile_keywords.get("industry_keywords", set())

    for kw in all_keywords:
        if kw in title_and_desc:
            hits += 1
    score += min(40, hits * 4)

    # 3. Deal-breaker penalty (-30)
    for breaker in profile_keywords.get("deal_breakers", set()):
        if breaker in title_and_desc:
            score -= 30
            break

    # 4. Seniority bonus (0-10)
    seniority_words = {"senior", "staff", "lead", "principal", "sr."}
    target_seniorities = profile_keywords.get("seniority", set())
    if target_seniorities:
        for word in seniority_words:
            if word in title_lower:
                score += 10
                break

    # 5. Remote bonus (0-10)
    if job.remote and profile_keywords.get("remote_preference") == "remote":
        score += 10

    return max(0, min(100, score))


# ===========================================================================
# Discovery orchestration
# ===========================================================================

_SCRAPER_MAP = {
    "greenhouse": GreenhouseScraper(),
    "lever": LeverScraper(),
    "ashby": AshbyScraper(),
}


async def _load_profile_keywords(session: AsyncSession) -> dict:
    """Load user profile and build keyword sets for scoring."""
    result = await session.execute(select(UserProfile).limit(1))
    profile = result.scalar_one_or_none()
    if not profile or not profile.data:
        return {}
    return _build_keyword_sets(profile.data)


def _make_temp_company(ats: str, slug: str) -> Company:
    """Create a temporary (non-persisted) Company with the right slug set."""
    company = Company(name=slug)
    if ats == "greenhouse":
        company.greenhouse_slug = slug
    elif ats == "lever":
        company.lever_slug = slug
    elif ats == "ashby":
        company.ashby_slug = slug
    return company


async def discover_company(
    session: AsyncSession, job_url: str
) -> dict:
    """Resolve URL via multi-prong pipeline, fetch all jobs, score, return preview.

    Returns dict with ats, slug, company_name, total_jobs, jobs, resolution_method.
    """
    resolution = await resolve_job_url(job_url)

    if not resolution.ats or not resolution.slug:
        return {
            "ats": None,
            "slug": None,
            "company_name": None,
            "total_jobs": 0,
            "jobs": [],
            "resolution_method": resolution.method,
            "error": resolution.error
                or "Could not detect ATS platform from URL.",
        }

    scraper = _SCRAPER_MAP.get(resolution.ats)
    if not scraper:
        return {
            "ats": resolution.ats,
            "slug": resolution.slug,
            "company_name": None,
            "total_jobs": 0,
            "jobs": [],
            "resolution_method": resolution.method,
            "error": f"No scraper available for {resolution.ats}.",
        }

    temp_company = _make_temp_company(resolution.ats, resolution.slug)

    async with httpx.AsyncClient(timeout=60.0) as client:
        scraped_jobs = await scraper.scrape_company(temp_company, client)

    company_name = resolution.slug.replace("-", " ").title()

    # Score jobs
    profile_keywords = await _load_profile_keywords(session)
    job_previews = []
    for sj in scraped_jobs:
        relevance = score_job_relevance(sj, profile_keywords) if profile_keywords else 0
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

    return {
        "ats": resolution.ats,
        "slug": resolution.slug,
        "company_name": company_name,
        "total_jobs": len(job_previews),
        "jobs": job_previews,
        "resolution_method": resolution.method,
    }


# ===========================================================================
# Import orchestration
# ===========================================================================

async def import_company(
    session: AsyncSession,
    *,
    name: str,
    website: str | None,
    ats: str,
    slug: str,
    selected_urls: list[str] | None,
    monitoring_active: bool = True,
) -> dict:
    """Create company record (persisting the discovered slug), scrape, persist selected jobs."""
    # Build company data with the ATS slug
    company_data = {
        "name": name,
        "website": website,
        "monitoring_active": monitoring_active,
    }
    if ats == "greenhouse":
        company_data["greenhouse_slug"] = slug
    elif ats == "lever":
        company_data["lever_slug"] = slug
    elif ats == "ashby":
        company_data["ashby_slug"] = slug

    # Check if company with same slug already exists
    existing = await _find_company_by_slug(session, ats, slug)

    if existing:
        company = existing
        if name and name != company.name:
            company.name = name
        # Persist the slug if it wasn't already set
        _ensure_slug(company, ats, slug)
    else:
        company = Company(**company_data)
        session.add(company)
        await session.flush()

    # Scrape jobs
    scraper = _SCRAPER_MAP.get(ats)
    if not scraper:
        await session.commit()
        return {"company_id": str(company.id), "company_name": company.name, "jobs_imported": 0, "job_ids": []}

    async with httpx.AsyncClient(timeout=60.0) as client:
        scraped_jobs = await scraper.scrape_company(company, client)

    # Filter to selected URLs if provided
    if selected_urls:
        url_set = set(selected_urls)
        scraped_jobs = [sj for sj in scraped_jobs if sj.url in url_set]

    # Persist jobs
    new_count = 0
    job_ids: list[str] = []
    for sj in scraped_jobs:
        if not sj.url:
            continue
        existing_job = (
            await session.execute(select(Job).where(Job.url == sj.url))
        ).scalar_one_or_none()
        if existing_job:
            continue

        job = Job(
            title=sj.title,
            company=company.name,
            company_id=company.id,
            location=sj.location,
            remote=sj.remote,
            salary_min=sj.salary_min,
            salary_max=sj.salary_max,
            description=sj.description,
            description_html=sj.description_html,
            url=sj.url,
            application_url=sj.application_url,
            source=JobSource(sj.source),
            posted_at=sj.posted_at,
            status=JobStatus.new,
            extra_metadata=sj.metadata,
        )
        session.add(job)
        await session.flush()
        job_ids.append(str(job.id))
        new_count += 1

    company.last_scraped_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(company)

    return {
        "company_id": str(company.id),
        "company_name": company.name,
        "jobs_imported": new_count,
        "job_ids": job_ids,
    }


# ===========================================================================
# Refresh orchestration
# ===========================================================================


async def refresh_company_jobs(
    session: AsyncSession,
    company_id,
) -> dict:
    """Refresh a company's jobs by reusing discover_company with a stored job URL.

    1. Grab an existing job URL from the company's jobs in the DB.
    2. Run it through discover_company() (same as the Add Company flow).
    3. Deconflict: filter out jobs already in the DB.
    4. Mark disappeared jobs as expired.
    """
    import uuid as _uuid

    if isinstance(company_id, str):
        company_id = _uuid.UUID(company_id)

    company = await session.get(Company, company_id)
    if not company:
        raise ValueError("Company not found")

    # Find a job URL to use as the probe — any existing job from this company
    probe_result = await session.execute(
        select(Job.url).where(
            Job.company_id == company_id,
            Job.url.isnot(None),
        ).limit(1)
    )
    probe_url = probe_result.scalar_one_or_none()

    if not probe_url:
        return {
            "ats": None,
            "slug": None,
            "company_name": company.name,
            "total_jobs": 0,
            "jobs": [],
            "expired_count": 0,
            "error": "No existing job URLs found for this company.",
        }

    # Run the same discovery pipeline used by Add Company
    logger.info("Refreshing %s via discover_company(%s)", company.name, probe_url)
    discovery = await discover_company(session, probe_url)

    if discovery.get("error"):
        return {
            "ats": discovery.get("ats"),
            "slug": discovery.get("slug"),
            "company_name": company.name,
            "total_jobs": 0,
            "jobs": [],
            "expired_count": 0,
            "error": discovery["error"],
        }

    # Persist discovered ATS slug if not already set
    ats = discovery.get("ats")
    slug = discovery.get("slug")
    if ats and slug:
        _ensure_slug(company, ats, slug)

    # Get existing job URLs for this company
    existing_result = await session.execute(
        select(Job.url).where(Job.company_id == company_id)
    )
    existing_urls = {row[0] for row in existing_result.all()}

    # Mark expired: jobs in DB but no longer in discovery results
    all_discovered_urls = {j["url"] for j in discovery.get("jobs", [])}
    expired_count = 0
    disappeared_urls = existing_urls - all_discovered_urls
    if disappeared_urls:
        result = await session.execute(
            update(Job)
            .where(
                Job.company_id == company_id,
                Job.url.in_(disappeared_urls),
                Job.expired_at.is_(None),
            )
            .values(expired_at=datetime.now(timezone.utc))
        )
        expired_count = result.rowcount

    # Filter to new jobs only
    new_jobs = [j for j in discovery.get("jobs", []) if j["url"] not in existing_urls]

    company.last_scraped_at = datetime.now(timezone.utc)
    await session.commit()

    return {
        "ats": ats,
        "slug": slug,
        "company_name": company.name,
        "total_jobs": len(new_jobs),
        "jobs": new_jobs,
        "expired_count": expired_count,
    }


# ===========================================================================
# Helpers
# ===========================================================================

async def _find_company_by_slug(
    session: AsyncSession, ats: str, slug: str
) -> Company | None:
    """Find an existing company by ATS slug."""
    col = {
        "greenhouse": Company.greenhouse_slug,
        "lever": Company.lever_slug,
        "ashby": Company.ashby_slug,
    }.get(ats)
    if not col:
        return None
    result = await session.execute(select(Company).where(col == slug))
    return result.scalar_one_or_none()


def _ensure_slug(company: Company, ats: str, slug: str) -> None:
    """Set the ATS slug on a company if not already set."""
    if ats == "greenhouse" and not company.greenhouse_slug:
        company.greenhouse_slug = slug
    elif ats == "lever" and not company.lever_slug:
        company.lever_slug = slug
    elif ats == "ashby" and not company.ashby_slug:
        company.ashby_slug = slug
