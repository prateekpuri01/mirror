"""Hot Search — Evaluation layer.

Per-candidate evaluation: ATS scrape, picker, verifier, drill strategies
for non-ATS companies, location/salary filters, hit summary, dedup. The
orchestrator hands a `CompanyCandidate` in; we hand a `CompanyHit` (or
None + skip reason) out. Cross-deps go one direction: evaluation imports
from discovery (URL/slug helpers), never the reverse.
"""

import asyncio
import html
import json
import logging
import re

import httpx
from sqlalchemy import select

from app.ai.client import get_openai_client
from app.config import settings
from app.database import async_session
from app.models.companies import Company
from app.models.jobs import Job
from app.models.profile import UserProfile
from app.scrapers import make_temp_company, SCRAPERS_BY_ATS
from app.services.company_discovery import _verify_ats_slug, score_job_relevance
from app.services.extraction import (
    EXTRACTION_PROMPT,
    EXTRACTION_SYSTEM,
    _call_llm,
    _parse_json_response,
    _truncate_description,
)
from app.services.hot_search.discovery import (
    _ATS_URL_PATTERNS,
    _ats_url_has_specific_job,
    _domain_of,
    _domain_plausible_for_company,
    _is_skip_domain,
    _looks_like_direct_job_url,
    _looks_like_job_url_relaxed,
    _precise_search,
    _probe_name_for_ats,
    _search_careers_url,
    _search_company_careers_page,
    _slug_plausible_for_name,
)
from app.services.hot_search.llm_helpers import _openai_chat, _parse_json_array
from app.services.hot_search.types import CompanyCandidate, CompanyHit
from app.services.scrape_cache import get_scraped_jobs, set_scraped_jobs

logger = logging.getLogger(__name__)


# Backwards-compat aliases for the renamed scrapers helpers — older callsites
# in this module reference _make_temp_company / _SCRAPER_MAP.
_make_temp_company = make_temp_company
_SCRAPER_MAP = SCRAPERS_BY_ATS


_eval_semaphore: asyncio.Semaphore | None = None


def _get_eval_semaphore() -> asyncio.Semaphore:
    global _eval_semaphore
    if _eval_semaphore is None:
        from app.services.rate_limits import max_concurrent_scoring
        _eval_semaphore = asyncio.Semaphore(max_concurrent_scoring())
    return _eval_semaphore


# ---------------------------------------------------------------------------
# Data types — canonical definitions live in app.services.hot_search.types
# ---------------------------------------------------------------------------

from app.services.hot_search.types import (  # noqa: E402
    CompanyCandidate,
    CompanyHit,
    SearchEvent,
)


# ---------------------------------------------------------------------------
# Web search delegate
# ---------------------------------------------------------------------------


async def _drill_lead_company_jobs(
    company_name: str,
    careers_url: str | None,
    profile_keywords: dict,
    max_jobs: int = 1,
    locations: list[str] | None = None,
    min_salary: int | None = None,
) -> list[CompanyHit]:
    """For a non-ATS company, find individual job posting URLs and import them.

    Uses two search strategies in order:
      1. Domain-scoped: `site:{domain} role_keyword` (best precision)
      2. Open web: `"{company}" hiring role_keyword` (broader fallback)

    Returns a list of CompanyHit objects, one per imported job.
    """
    # Build BROAD role keywords — NOT the user's exact target titles (which are
    # too specific for cross-company search). Always include the generic base
    # words first, then add profile-specific words on top.
    base_keywords = ["engineer", "scientist", "researcher", "developer", "analyst"]
    extra_keywords: list[str] = []
    if isinstance(profile_keywords, dict):
        role_titles = profile_keywords.get("role_titles") or set()
        seen = set(base_keywords)
        for title in role_titles:
            for word in title.lower().split():
                if (
                    len(word) >= 5
                    and word not in seen
                    and word not in ("senior", "staff", "junior", "lead", "principal")
                ):
                    extra_keywords.append(word)
                    seen.add(word)
    # Base words first (guaranteed), then extras (best-effort), cap at 8
    all_keywords = base_keywords + extra_keywords[:3]
    keyword_clause = " OR ".join(f'"{k}"' for k in all_keywords)

    # Strategy 1: domain-scoped search (if we have a careers URL)
    direct_urls: list[str] = []
    domain = _domain_of(careers_url) if careers_url else None
    if domain:
        query = f'site:{domain} ({keyword_clause})'
        results = await _precise_search(query, max_results=10)
        for r in results:
            url = r.get("url", "")
            if _looks_like_direct_job_url(url) and len(direct_urls) < max_jobs:
                direct_urls.append(url)
        if direct_urls:
            logger.info("Lead drill domain-scoped for '%s': found %d URLs", company_name, len(direct_urls))

    # Strategy 2: open web search (if domain search found nothing)
    if not direct_urls:
        query = f'"{company_name}" hiring apply ({keyword_clause})'
        results = await _precise_search(query, max_results=10)
        for r in results:
            url = r.get("url", "")
            if (
                _looks_like_direct_job_url(url)
                and _domain_plausible_for_company(url, company_name)
                and len(direct_urls) < max_jobs
            ):
                direct_urls.append(url)
        if direct_urls:
            logger.info("Lead drill open-web for '%s': found %d URLs", company_name, len(direct_urls))

    if not direct_urls:
        logger.info("Lead drill for '%s': no direct job URLs found via search", company_name)
        return []

    # Import each direct URL via the existing flow
    imported: list[CompanyHit] = []
    for url in direct_urls:
        hit, reason = await _extract_direct_job_url(
            url, profile_keywords,
            locations=locations, min_salary=min_salary,
        )
        if hit:
            imported.append(hit)
        else:
            logger.debug("Lead drill skipped %s: %s", url, reason)
    return imported


def _extract_jobs_from_api_responses(
    captured: list[dict],
    page_host: str | None = None,
) -> list[dict]:
    """Mine JSON responses captured during browser-agent operation for jobs.

    Many enterprise careers SPAs (Google, Apple, Cox/Workday, Taleo) render
    their job lists dynamically from XHR/fetch calls rather than putting
    job links in the server-rendered HTML. That's why DOM extraction often
    returns zero links for these pages even when jobs ARE displayed.

    By listening to network responses during agent operation, we can pull
    the job data from these API calls directly — regardless of how the
    page chooses to render it.

    Each ``captured`` entry is ``{"url": response_url, "data": parsed_json}``.
    We walk common response shapes (lists, {"jobs": [...]}, Workday's
    nested structure, GraphQL envelopes) and return ``[{url, title}]``
    pairs. Relative URLs are resolved against the page host.
    """
    from urllib.parse import urlparse, urljoin

    # Common response-envelope paths. Tried in order; first non-empty list wins.
    LIST_PATHS = [
        ("jobPostings",),                      # Workday
        ("results",),                          # Taleo, many ATS
        ("jobs",),                             # generic
        ("postings",),                         # generic
        ("items",),                            # generic
        ("hits",),                             # Algolia-backed sites
        ("requisitions",),                     # SAP SuccessFactors
        ("openings",),                         # some ATS
        ("searchResults",),
        ("searchResults", "jobPostings"),
        ("data", "jobs"),                      # GraphQL
        ("data", "jobPostings"),
        ("data", "careers", "jobs"),
        ("data", "search", "jobs"),
        ("data", "requisitions"),
        ("payload", "jobs"),
        ("records",),                          # Salesforce-style
    ]

    # Per-job field candidates
    URL_KEYS = (
        "externalPath",     # Workday: path relative to tenant
        "url", "jobUrl", "href", "link",
        "apply_url", "external_url", "applyUrl", "canonicalUrl",
        "detailUrl", "permaLink", "jobDetailsUrl",
    )
    TITLE_KEYS = (
        "title", "jobTitle", "name", "heading", "displayTitle",
        "postingTitle", "requisitionTitle",
    )
    ID_KEYS = (
        "id", "jobId", "requisitionId", "externalId", "postingId",
        "reqId", "jobRequisitionId",
    )

    results: list[dict] = []
    seen_urls: set[str] = set()

    for cap in captured:
        data = cap.get("data")
        response_url = cap.get("url", "")

        # Walk the envelope to find a list of job items
        candidates: list = []
        if isinstance(data, list):
            candidates = data
        elif isinstance(data, dict):
            for path in LIST_PATHS:
                node = data
                ok = True
                for key in path:
                    if isinstance(node, dict) and key in node:
                        node = node[key]
                    else:
                        ok = False
                        break
                if ok and isinstance(node, list) and node:
                    candidates = node
                    break

        if not candidates:
            continue

        # Resolve relative URLs against the API host (works for branded
        # Workday domains like jobs.coxenterprises.com)
        try:
            resp_parsed = urlparse(response_url)
            host_base = f"{resp_parsed.scheme}://{resp_parsed.hostname}"
        except Exception:
            host_base = f"https://{page_host}" if page_host else ""

        for item in candidates[:40]:
            if not isinstance(item, dict):
                continue

            url = None
            for k in URL_KEYS:
                v = item.get(k)
                if isinstance(v, str) and v:
                    url = v
                    break

            title = None
            for k in TITLE_KEYS:
                v = item.get(k)
                if isinstance(v, str) and v.strip():
                    title = v.strip()
                    break

            # Resolve relative URLs
            if url and not url.startswith(("http://", "https://")):
                if host_base:
                    url = urljoin(host_base + "/", url.lstrip("/"))
                else:
                    url = None

            if url and title and url not in seen_urls:
                seen_urls.add(url)
                results.append({"url": url, "title": title[:200]})

    return results


async def _drill_perplexity_for_job(
    company_name: str,
    guidance: str,
    profile_keywords: dict,
    locations: list[str] | None = None,
    min_salary: int | None = None,
) -> list[CompanyHit]:
    """Ask Perplexity for specific job-posting URLs at the company.

    Used for non-ATS companies where the SearXNG-based lead drill didn't
    find direct URLs. Perplexity's LLM-grounded search is substantially
    better for natural-language queries like "current Data Engineer
    opening at IBM in Los Angeles" — it can name specific postings and
    return live URLs. Each returned URL is verified via
    extract_job_from_url (which fails cleanly on non-job pages), so
    hallucinated URLs don't produce phantom imports.

    Sits between the SearXNG lead drill and the browser agent in the
    cascade: cheaper than the browser agent, more capable than SearXNG
    for well-known companies.

    Returns a list of 0 or 1 CompanyHit.
    """
    # Skip if Perplexity isn't configured
    if not settings.perplexity_api_key:
        return []
    if not guidance:
        return []

    from app.services.web_search import _perplexity_search

    location_hint = ""
    if locations:
        location_hint = f" in {' or '.join(locations[:2])}"

    # Ask for specific job-posting URLs (with a requisition ID in the path),
    # not listing/search pages. The emphasis on "with an ID" helps Perplexity
    # return deep-links like /jobs/12345 instead of /careers/engineering.
    query = (
        f"Find 1-3 SPECIFIC {guidance} job postings currently open at "
        f"{company_name}{location_hint}. I need direct URLs that point to an "
        f"individual job requisition (with a job ID in the URL), not category "
        f"pages or search-result pages. Use only {company_name}'s official "
        f"careers site. Do not include LinkedIn, Indeed, Glassdoor, Built In, "
        f"The Muse, or other aggregators."
    )

    try:
        results = await _perplexity_search(query, num_results=6)
    except Exception as e:
        logger.debug("Perplexity drill error for '%s': %s", company_name, e)
        return []

    if not results:
        logger.info("Perplexity drill for '%s': no results", company_name)
        return []

    # Filter to likely job URLs, drop aggregators
    candidates: list[dict] = []
    for r in results:
        url = r.get("url", "")
        if not url or _is_skip_domain(url):
            continue
        # Accept strict OR relaxed job URL shapes
        if _looks_like_direct_job_url(url) or _looks_like_job_url_relaxed(url):
            candidates.append({
                "title": r.get("title", "") or r.get("snippet", ""),
                "url": url,
            })

    if not candidates:
        logger.info(
            "Perplexity drill for '%s': %d results, none looked like job URLs",
            company_name, len(results),
        )
        return []

    logger.info(
        "Perplexity drill for '%s': %d candidate job URLs",
        company_name, len(candidates),
    )

    # Let the LLM picker choose the best fit (reuses existing logic).
    # Pass min_salary=None here — the salary filter is already applied at
    # the evaluate_candidate layer via _job_passes_salary_filter.
    best, _ = await _pick_best_job_for_guidance(
        candidates[:5], guidance, locations, min_salary,
    )
    if not best or not best.get("url"):
        logger.info("Perplexity drill for '%s': picker rejected all candidates", company_name)
        return []

    hit, reason = await _extract_direct_job_url(
        best["url"], profile_keywords,
        locations=locations, min_salary=min_salary,
    )
    if hit:
        logger.info(
            "Perplexity drill SUCCEEDED for '%s': imported '%s'",
            company_name, (best.get("title") or "?")[:50],
        )
        return [hit]

    logger.info(
        "Perplexity drill for '%s': picked URL but import failed — %s",
        company_name, reason,
    )
    return []


async def _crawl_careers_page_for_job(
    company_name: str,
    careers_url: str,
    guidance: str,
    profile_keywords: dict,
    locations: list[str] | None = None,
    min_salary: int | None = None,
) -> list[CompanyHit]:
    """LLM-guided browser agent that interactively navigates a careers page
    to find a job matching the user's search criteria.

    Unlike the old SPA drill (passive link extraction), this agent can:
    - Type search queries into search boxes
    - Click category filters and navigation elements
    - Wait for JavaScript-rendered results to load
    - Extract job links from the updated DOM

    Uses the same tool pattern as app_req_extraction.py but with a different
    goal: find one relevant job posting instead of extracting form fields.

    Returns a list of CompanyHit objects (0 or 1 elements).
    """
    import json as _json
    from app.services.browser_pool import _ensure_browser
    from app.ai.browser_tools import PlaywrightToolExecutor, CAREERS_CRAWLER_TOOLS
    from app.ai.client import EXTRACTION_MODEL

    _USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    logger.info("Browser agent for '%s': navigating %s", company_name, careers_url)

    # Build search criteria for the agent prompt
    criteria_parts = []
    if guidance:
        criteria_parts.append(f'Search topic: "{guidance}"')
    if locations:
        criteria_parts.append(f"Preferred locations: {', '.join(locations)}")
    if min_salary:
        criteria_parts.append(f"Min salary: ${min_salary:,}")
    criteria_text = "\n".join(criteria_parts) or "General job search"

    system_prompt = f"""\
You are navigating {company_name}'s careers page to find a specific job posting.

Search criteria:
{criteria_text}

Your goal: Find ONE job posting URL that matches the search criteria.

Strategy:
1. First, look at the page. If you see a search/filter input, use fill_and_search to type relevant keywords.
2. If you see category links or filter buttons, click the most relevant one.
3. Call extract_job_links to get URLs from <a href> tags.
4. If extract_job_links returns zero URLs BUT the page text shows job
   titles (the listings are visible), use click_job_card — it clicks a
   card-like element and captures the URL it navigates to. This is the
   only way to get jobs on SPAs like Google Careers or Apple Jobs that
   render job cards as clickable divs instead of anchor tags.
5. If the page has no search, no filters, and no visible job listings, call done.

Tips:
- For search queries, use 2-3 keywords from the search criteria (e.g. "data scientist", "AI safety", "ML engineer")
- Common search input selectors: input[type="search"], input[placeholder*="Search"], input[name="q"], input[id*="search"]
- If the first search returns no results, try broader keywords
- Don't navigate away from the careers site
- If extract_job_links returns 0 URLs twice in a row, try click_job_card
  with the role keyword as contains_text"""

    try:
        browser = await _ensure_browser()
        context = await browser.new_context(
            user_agent=_USER_AGENT,
            ignore_https_errors=True,
        )
        page = await context.new_page()

        # --- Network response interception ---
        # Many enterprise careers SPAs (Google, Apple, Cox/Workday) load
        # jobs via XHR/fetch calls that return JSON. If we listen for
        # those responses, we can mine structured job data directly —
        # no DOM scraping needed. This supplements extract_job_links for
        # sites where jobs aren't rendered as <a href> tags.
        captured_api_responses: list[dict] = []

        # Track all JSON responses for diagnostic purposes (we cap the list).
        # Most match our job-hint keywords; for debugging, also remember
        # responses that contained arrays of objects — these often ARE job
        # lists even when the URL doesn't have a job-related keyword
        # (e.g. Apple's /api/role/... or Google's experimental /v3/...).
        _API_HINT_KEYWORDS = (
            "job", "career", "position", "vacanc", "opening", "posting",
            "requisit", "search", "/wday/", "/cxs/", "graphql", "/role",
            "/listing", "/hiring",
        )

        async def _on_response(response):
            try:
                url = response.url
                if response.status != 200:
                    return
                ct = (response.headers.get("content-type") or "").lower()
                if "json" not in ct:
                    return
                cl = response.headers.get("content-length")
                if cl and cl.isdigit() and int(cl) > 5_000_000:
                    return
                if len(captured_api_responses) >= 25:
                    return

                url_lower = url.lower()
                matches_keyword = any(kw in url_lower for kw in _API_HINT_KEYWORDS)

                # If URL doesn't match keywords, still peek at the JSON to see
                # if it contains what looks like a job list. This catches
                # pages that use obscure endpoint names (Apple, Google's
                # internal APIs). Peek is cheap — it's one response read.
                data = await response.json()
                looks_like_jobs = False
                if not matches_keyword and isinstance(data, (dict, list)):
                    # Quickly check if this response contains a list of items
                    # with title-like fields — a strong signal of a job list
                    sample: list = []
                    if isinstance(data, list):
                        sample = data[:3]
                    elif isinstance(data, dict):
                        for v in data.values():
                            if isinstance(v, list) and len(v) > 2:
                                sample = v[:3]
                                break
                            if isinstance(v, dict):
                                for nv in v.values():
                                    if isinstance(nv, list) and len(nv) > 2:
                                        sample = nv[:3]
                                        break
                            if sample:
                                break
                    if sample and all(
                        isinstance(item, dict)
                        and any(
                            tk in item for tk in (
                                "title", "jobTitle", "postingTitle",
                                "name", "heading",
                            )
                        )
                        for item in sample
                    ):
                        looks_like_jobs = True

                if not matches_keyword and not looks_like_jobs:
                    return

                captured_api_responses.append({"url": url, "data": data})
            except Exception:
                # Silently ignore — bad JSON, closed response, encoding issues
                pass

        page.on("response", _on_response)

        try:
            # Navigate to the careers page
            await page.goto(careers_url, wait_until="domcontentloaded", timeout=25000)
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            await page.wait_for_timeout(3000)

            executor = PlaywrightToolExecutor(page)
            client = get_openai_client()

            # Get initial page state for the first message
            initial_text = await page.inner_text("body")
            initial_text = initial_text[:3000] if initial_text else "(empty page)"

            form_fields = await page.evaluate("""() => {
                const inputs = document.querySelectorAll('input[type="search"], input[type="text"], input[placeholder*="earch"], input[name*="earch"], input[id*="earch"]');
                return Array.from(inputs).map(el => ({
                    tag: el.tagName.toLowerCase(),
                    type: el.type,
                    name: el.name,
                    id: el.id,
                    placeholder: el.placeholder,
                    selector: el.id ? '#' + el.id : (el.name ? 'input[name="' + el.name + '"]' : 'input[type="' + el.type + '"]')
                })).slice(0, 5);
            }""")

            user_message = (
                f"I've navigated to {company_name}'s careers page: {careers_url}\n\n"
                f"Page content (first 3000 chars):\n{initial_text}\n\n"
                f"Search inputs found: {_json.dumps(form_fields) if form_fields else 'None visible'}\n\n"
                f"Find a job matching: {criteria_text}"
            )

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ]

            # Accumulate strict + relaxed matches across rounds so we don't
            # lose useful links when the agent pivots to another search.
            # Keyed by URL to dedupe.
            collected_strict: dict[str, dict] = {}
            collected_relaxed: dict[str, dict] = {}

            # Pre-extract: seed collections from the landing page BEFORE the
            # agent starts interacting. Many enterprise careers pages
            # (IBM, Cox, BlackLine) have job links on the default page;
            # if the agent's first fill_and_search fails (common), it
            # often leaves the page in a degraded state where subsequent
            # extractions return nothing. Pre-extracting preserves these
            # links so the fallback has something to work with.
            try:
                pre_text = await executor.execute("extract_job_links", {})
                pre_links = _json.loads(pre_text) if pre_text else []
            except (_json.JSONDecodeError, TypeError):
                pre_links = []
            except Exception:
                pre_links = []

            for link in pre_links:
                url = link.get("url", "")
                if not url:
                    continue
                if _looks_like_direct_job_url(url):
                    collected_strict[url] = link
                elif _looks_like_job_url_relaxed(url):
                    collected_relaxed[url] = link

            if collected_strict or collected_relaxed:
                logger.info(
                    "Browser agent for '%s': pre-extract found %d strict + %d relaxed job links",
                    company_name,
                    len(collected_strict),
                    len(collected_relaxed),
                )

            # Agent loop — max 5 rounds
            for round_num in range(5):
                logger.info("Browser agent round %d for '%s'", round_num + 1, company_name)

                response = await client.chat.completions.create(
                    model=EXTRACTION_MODEL,
                    messages=messages,
                    tools=CAREERS_CRAWLER_TOOLS,
                    max_completion_tokens=500,
                )

                choice = response.choices[0]
                message = choice.message

                # No tool calls = agent is done (shouldn't happen with our tool set, but handle it)
                if not message.tool_calls:
                    logger.info("Browser agent for '%s': no tool calls, stopping", company_name)
                    break

                messages.append(message)

                for tool_call in message.tool_calls:
                    fn_name = tool_call.function.name
                    fn_args = _json.loads(tool_call.function.arguments) if tool_call.function.arguments else {}
                    logger.info("Browser agent tool: %s(%s)", fn_name, str(fn_args)[:100])

                    if fn_name == "done":
                        reason = fn_args.get("reason", "no reason")
                        logger.info("Browser agent for '%s': done — %s", company_name, reason)
                        # Don't return immediately — try the fallback below
                        # in case we collected useful links in prior rounds
                        break

                    if fn_name == "extract_job_links":
                        # Extract links and filter for job URLs
                        result_text = await executor.execute(fn_name, fn_args)
                        try:
                            all_links = _json.loads(result_text)
                        except (_json.JSONDecodeError, TypeError):
                            all_links = []

                        for link in all_links:
                            url = link.get("url", "")
                            if not url:
                                continue
                            if _looks_like_direct_job_url(url):
                                collected_strict[url] = link
                            elif _looks_like_job_url_relaxed(url):
                                collected_relaxed[url] = link

                        if collected_strict:
                            logger.info(
                                "Browser agent for '%s': %d strict job links (+%d relaxed) from %d total this round",
                                company_name, len(collected_strict),
                                len(collected_relaxed), len(all_links),
                            )
                            logger.debug(
                                "Browser agent candidate titles: %s",
                                [
                                    (l.get("title") or "")[:60]
                                    for l in list(collected_strict.values())[:5]
                                ],
                            )
                            # Try strict matches immediately — high confidence
                            best, _ = await _pick_best_job_for_guidance(
                                [{"title": l["title"], "url": l["url"]}
                                 for l in list(collected_strict.values())[:10]],
                                guidance, locations, min_salary,
                            )
                            if best and best.get("url"):
                                hit, reason = await _extract_direct_job_url(
                                    best["url"], profile_keywords,
                                    locations=locations, min_salary=min_salary,
                                )
                                if hit:
                                    logger.info(
                                        "Browser agent SUCCEEDED for '%s': imported '%s'",
                                        company_name, best.get("title", "?")[:50],
                                    )
                                    return [hit]
                                else:
                                    logger.info("Browser agent import failed: %s", reason)
                            # Fall through: keep collecting in case this
                            # particular pick didn't verify as a real job
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "content": f"Found {len(all_links)} links, {len(collected_strict)} look like job postings. Still searching for a relevant match.",
                            })
                            continue
                        else:
                            # Tell the agent no strict job URLs matched,
                            # mention if we have relaxed candidates to fall back on
                            hint = (
                                f" ({len(collected_relaxed)} candidate URLs saved for fallback)"
                                if collected_relaxed else ""
                            )
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "content": (
                                    f"Found {len(all_links)} links but none strictly match "
                                    f"job posting URL patterns{hint}. Try searching with "
                                    f"different keywords or clicking a category filter."
                                ),
                            })
                            continue

                    # click_job_card returns a JSON object with a URL when
                    # a card click successfully surfaces a job URL. Capture
                    # those results just like extract_job_links output.
                    if fn_name == "click_job_card":
                        result_text = await executor.execute(fn_name, fn_args)
                        logger.info("Browser agent tool result: %s", result_text[:200])
                        try:
                            parsed = _json.loads(result_text)
                        except (_json.JSONDecodeError, TypeError):
                            parsed = None
                        if isinstance(parsed, dict) and parsed.get("url"):
                            url = parsed["url"]
                            title = parsed.get("title", "")
                            link = {"url": url, "title": title}
                            if _looks_like_direct_job_url(url):
                                collected_strict[url] = link
                            elif _looks_like_job_url_relaxed(url):
                                collected_relaxed[url] = link
                            else:
                                # Trust click-through URLs even without pattern
                                # match — they were reached by clicking a real
                                # card, so they're almost certainly valid jobs.
                                collected_relaxed[url] = link
                            logger.info(
                                "click_job_card SUCCEEDED for '%s': captured %s",
                                company_name, url[:100],
                            )
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": result_text,
                        })
                        continue

                    # Execute other tools normally (fill_and_search, click_element)
                    result_text = await executor.execute(fn_name, fn_args)
                    logger.info("Browser agent tool result: %s", result_text[:200])
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result_text,
                    })

                # If the inner loop broke out due to "done", exit the agent loop
                if any(
                    tc.function.name == "done" for tc in (message.tool_calls or [])
                ):
                    break

            logger.info("Browser agent for '%s': exhausted rounds", company_name)

            # -----------------------------------------------------------
            # Fallback: one last extraction on whatever page is rendered,
            # combined with any links we collected during the agent loop.
            # Use relaxed URL matching so enterprise-portal URLs (query-
            # string IDs, slug-based IDs) can still be tried. Each
            # candidate is verified by extract_job_from_url, which fails
            # cleanly on non-job pages — so permissive matching is safe.
            # -----------------------------------------------------------
            try:
                final_result_text = await executor.execute("extract_job_links", {})
                final_links = _json.loads(final_result_text)
            except (_json.JSONDecodeError, TypeError):
                final_links = []
            except Exception as e:
                logger.info("Browser agent fallback extraction failed for '%s': %s", company_name, e)
                final_links = []

            for link in final_links:
                url = link.get("url", "")
                if not url:
                    continue
                if _looks_like_direct_job_url(url):
                    collected_strict.setdefault(url, link)
                elif _looks_like_job_url_relaxed(url):
                    collected_relaxed.setdefault(url, link)

            # -----------------------------------------------------------
            # Network-capture fallback: mine JSON responses collected
            # during the agent's operation for job listings. Covers SPAs
            # (Google Careers, Apple Jobs, Cox/Workday) that load jobs
            # via XHR/fetch instead of rendering them as <a> tags.
            # -----------------------------------------------------------
            if captured_api_responses:
                page_host = None
                try:
                    from urllib.parse import urlparse
                    page_host = urlparse(careers_url).hostname
                except Exception:
                    pass
                api_jobs = _extract_jobs_from_api_responses(
                    captured_api_responses, page_host,
                )
                if api_jobs:
                    # Deduplicate URL-by-URL; classify each by existing filters
                    pre_len_s, pre_len_r = len(collected_strict), len(collected_relaxed)
                    for job in api_jobs:
                        url = job.get("url", "")
                        if not url or _is_skip_domain(url):
                            continue
                        if _looks_like_direct_job_url(url):
                            collected_strict.setdefault(url, job)
                        elif _looks_like_job_url_relaxed(url):
                            collected_relaxed.setdefault(url, job)
                        else:
                            # API data is trustworthy — if it has a title
                            # and URL, treat it as a relaxed candidate even
                            # if the URL doesn't match our patterns
                            collected_relaxed.setdefault(url, job)
                    logger.info(
                        "Browser agent for '%s': API response mining added "
                        "%d strict + %d relaxed candidates (from %d JSON responses, %d jobs)",
                        company_name,
                        len(collected_strict) - pre_len_s,
                        len(collected_relaxed) - pre_len_r,
                        len(captured_api_responses),
                        len(api_jobs),
                    )

            # Try strict first, then relaxed
            fallback_candidates = (
                list(collected_strict.values())
                or list(collected_relaxed.values())
            )

            if fallback_candidates:
                logger.info(
                    "Browser agent fallback for '%s': trying %d candidates (%d strict, %d relaxed)",
                    company_name, len(fallback_candidates),
                    len(collected_strict), len(collected_relaxed),
                )
                best, _ = await _pick_best_job_for_guidance(
                    [{"title": l.get("title", ""), "url": l["url"]}
                     for l in fallback_candidates[:10]],
                    guidance, locations, min_salary,
                )
                if best and best.get("url"):
                    hit, reason = await _extract_direct_job_url(
                        best["url"], profile_keywords,
                        locations=locations, min_salary=min_salary,
                    )
                    if hit:
                        logger.info(
                            "Browser agent fallback SUCCEEDED for '%s': imported '%s'",
                            company_name, best.get("title", "?")[:50],
                        )
                        return [hit]
                    else:
                        logger.info(
                            "Browser agent fallback import failed for '%s': %s",
                            company_name, reason,
                        )

            return []

        finally:
            await context.close()

    except Exception as e:
        logger.warning("Browser agent error for '%s': %s", company_name, e)
        return []


# ---------------------------------------------------------------------------
# Hit summary generation (description + match reason)
# ---------------------------------------------------------------------------


_SUMMARY_SYSTEM = """\
You help a job seeker understand why a specific company + role was surfaced.

You receive the user's SEARCH INTENT (what they asked for), their PROFILE \
(background/preferences), and the top matching JOB(S) at a company.

## Decision: accept or reject

Ask TWO questions — in this order:

  1. **Does this company/role match the SEARCH INTENT?**
     If the user searched "ML startups Series A" and the company is an ML \
     startup, it's relevant — even if the specific open role (e.g. ML Engineer) \
     isn't a perfect profile match. The search is about COMPANY DISCOVERY.

  2. **Is the company obviously wrong for this person?**
     Reject ONLY when the company is in a completely unrelated field \
     (e.g. realtor.com for an AI search, a restaurant chain for an ML query) \
     or the role is clearly a non-technical/non-adjacent position (recruiter, \
     office manager, brand ambassador) at an otherwise relevant company.

When in doubt, ACCEPT. A relevant company with an imperfect role match is \
still useful — the user might find other roles there, or the company might \
post a better fit soon.

## Output

Return ONLY a JSON object, no markdown fences:

{
  "reject": false,
  "description": "1 sentence plain-English company description",
  "match_reason": "1-2 sentences. Say why this company is relevant to the search, and what the open role involves. Be specific — cite the domain, the tech, the problem space."
}

OR (if rejecting):

{
  "reject": true,
  "reason": "Short explanation of why this company is off-topic for the search"
}

Rules:
- Never use words like "leverage", "synergize", "stakeholder", "best-in-class".
- Never mention internal tools or pipelines (no "DB", "Perplexity", "extraction").
- Reject sparingly. Only for truly off-topic companies/roles.
- The match_reason is for the user to read. Address them plainly."""


def _summarize_profile_for_match(profile_data: dict) -> str:
    """Build a compact profile summary the LLM can ground its reasoning in."""
    if not profile_data:
        return "(no profile available)"

    lines: list[str] = []

    roles = profile_data.get("target_roles") or []
    if roles:
        titles = [
            r.get("title", "").strip()
            for r in roles
            if isinstance(r, dict) and r.get("title")
        ]
        if titles:
            lines.append(f"Target roles: {', '.join(titles[:5])}")

    domains = profile_data.get("domains") or []
    if domains:
        lines.append(f"Domains: {', '.join(domains[:6])}")

    prefs = profile_data.get("search_preferences") or {}
    if prefs.get("looking_for"):
        lines.append(f"Looking for: {prefs['looking_for']}")
    if prefs.get("not_looking_for"):
        lines.append(f"Not looking for: {prefs['not_looking_for']}")
    pos = prefs.get("positive_signals") or []
    if pos:
        lines.append(f"Positive signals: {'; '.join(pos[:5])}")
    excl = prefs.get("exclusions") or prefs.get("deal_breakers") or []
    if excl:
        lines.append(f"Exclusions: {', '.join(excl[:5])}")

    # A couple of accomplishment headlines help ground reasoning in real work
    history = profile_data.get("work_history") or []
    if history:
        recent = history[:2]
        summaries = []
        for h in recent:
            if isinstance(h, dict) and h.get("title") and h.get("employer"):
                summaries.append(f"{h['title']} at {h['employer']}")
        if summaries:
            lines.append(f"Recent roles: {'; '.join(summaries)}")

    return "\n".join(lines) or "(no profile available)"


def _format_jobs_for_match(top_jobs: list[dict]) -> str:
    """Format the top jobs (title + description snippet) for the prompt."""
    out: list[str] = []
    for i, j in enumerate(top_jobs[:3], 1):
        title = j.get("title") or "(untitled)"
        loc = j.get("location") or ""
        desc = j.get("description") or j.get("description_html") or ""
        # Strip HTML tags for the prompt if present
        desc_text = re.sub(r"<[^>]+>", " ", desc)
        desc_text = re.sub(r"\s+", " ", desc_text).strip()
        if len(desc_text) > 600:
            desc_text = desc_text[:600] + "…"
        header = f"Job {i}: {title}"
        if loc:
            header += f" — {loc}"
        out.append(f"{header}\n{desc_text or '(no description available)'}")
    return "\n\n".join(out) if out else "(no jobs to evaluate)"


async def _generate_hit_summary(
    company_name: str,
    top_jobs: list[dict],
    profile_data: dict,
    guidance: str = "",
) -> tuple[bool, str, str, str]:
    """Generate a grounded description + match reason, or reject the hit.

    Returns ``(rejected, description, match_reason, reject_reason)``.
    On rejection, ``description`` and ``match_reason`` are empty and
    ``reject_reason`` explains why the hit wasn't a real fit.
    On parse failure, returns ``(False, "", "", "")`` so the caller can
    fall back to emitting the hit with empty strings — preferable to
    dropping a legitimate match because of a transient LLM issue.
    """
    profile_block = _summarize_profile_for_match(profile_data)
    jobs_block = _format_jobs_for_match(top_jobs)

    user_prompt = (
        f"Company: {company_name}\n\n"
        f"User profile:\n{profile_block}\n\n"
        f"User's current search: {guidance or '(no explicit guidance)'}\n\n"
        f"Top matching job(s):\n{jobs_block}"
    )

    try:
        content = await _openai_chat(_SUMMARY_SYSTEM, user_prompt, temperature=0.3)
        if not content:
            return (False, "", "", "")
        text = content.strip()
        if text.startswith("```"):
            text = re.sub(r"^```\w*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        obj = json.loads(text)
        if not isinstance(obj, dict):
            return (False, "", "", "")
        if obj.get("reject"):
            reject_reason = (obj.get("reason") or "No defensible match").strip()
            return (True, "", "", reject_reason)
        return (
            False,
            (obj.get("description") or "").strip(),
            (obj.get("match_reason") or "").strip(),
            "",
        )
    except Exception:
        logger.debug("Failed to generate hit summary for %s", company_name)
        return (False, "", "", "")


# ---------------------------------------------------------------------------
# Cheap location/salary prefilter — uses ScrapedJob fields, no LLM
# ---------------------------------------------------------------------------


# Common location aliases. Both directions of every pair so substring matching
# works regardless of which side the user/job uses. Lowercase only.

_LOCATION_ALIASES: dict[str, list[str]] = {
    "sf": ["san francisco", "bay area"],
    "san francisco": ["sf", "bay area"],
    "bay area": ["san francisco", "sf"],
    "nyc": ["new york"],
    "new york": ["nyc"],
    "ny": ["new york", "nyc"],
    "dc": ["washington"],
    "washington dc": ["dc", "washington"],
    "washington": ["dc"],
    "la": ["los angeles"],
    "los angeles": ["la"],
    "boston": ["cambridge"],
    "uk": ["united kingdom", "england", "london"],
    "united kingdom": ["uk", "england"],
}

# Locations that are ambiguous enough to let through ("anywhere", "various", etc.)

_VAGUE_LOCATION_TOKENS = ("anywhere", "multiple", "various", "global", "tbd", "worldwide")


def _expand_location(loc: str) -> set[str]:
    """Return the lowercase location plus its known aliases."""
    norm = loc.lower().strip()
    expanded = {norm}
    expanded.update(_LOCATION_ALIASES.get(norm, []))
    return expanded


def _job_passes_location_filter(
    sj: "ScrapedJob",
    filter_locations: list[str] | None,
) -> tuple[bool, str]:
    """Cheap location filter using ScrapedJob fields only — no LLM.

    Returns (passes, reason). The semantics:
      - filter_locations is empty/None → pass (no filter active)
      - sj.remote and "remote" in filter → pass
      - sj.location matches any filter or alias (substring) → pass
      - sj.location is empty/None → pass ("unknown" — let LLM verification decide)
      - sj.location is a vague string ("anywhere", etc.) → pass
      - otherwise → REJECT (the location is known and doesn't match)
    """
    if not filter_locations:
        return True, ""

    filter_lower = [f.lower().strip() for f in filter_locations if f and f.strip()]
    if not filter_lower:
        return True, ""

    wants_remote = any("remote" in f for f in filter_lower)

    # Remote check first
    if sj.remote and wants_remote:
        return True, "remote"

    # Unknown location → defer to LLM verification
    if not sj.location:
        return True, "unknown_location"

    job_loc = sj.location.lower()

    # Vague location → defer to LLM verification
    if any(tok in job_loc for tok in _VAGUE_LOCATION_TOKENS):
        return True, "vague_location"

    # Substring match against any filter or its aliases
    for f in filter_lower:
        for variant in _expand_location(f):
            if variant and variant in job_loc:
                return True, f"matched '{variant}'"

    # Special case: a job tagged remote that doesn't match any filter — but the
    # filter list doesn't include remote either. The location filter is specific
    # geography, so a remote-only job is a definite reject.
    return False, f"location '{sj.location}' not in {filter_locations}"


def _job_passes_salary_filter(
    sj: "ScrapedJob",
    min_salary: int | None,
) -> tuple[bool, str]:
    """Cheap salary filter using ScrapedJob fields only — no LLM.

    Returns (passes, reason). Semantics:
      - min_salary is None → pass
      - sj.salary_max is set and < min_salary → REJECT (confirmed below threshold)
      - otherwise → pass (unknown salary defers to LLM verification)
    """
    if not min_salary:
        return True, ""
    if sj.salary_max is not None and sj.salary_max < min_salary:
        return False, f"salary_max ${sj.salary_max} < ${min_salary}"
    return True, ""


# ---------------------------------------------------------------------------
# LLM extraction-based verification for location/salary filters
# ---------------------------------------------------------------------------


_LOCATION_FILTER_ADDENDUM = """

ADDITIONAL TASK — LOCATION MATCH:
The user is filtering for jobs in these locations: {locations}
Add this field to your JSON response:
  "location_match": true | false | null
- true:  the job has positions in one of the listed locations.
- false: the job has positions only in OTHER locations not in the list.
- null:  the job posting genuinely does not mention any specific location.

Match rules:
- "Remote" in the filter list matches any remote-eligible role (work_model "remote").
- For city/region filters, match if the job has an office or is based in that area.
  Use common sense: "SF" matches "San Francisco", "NYC" matches "New York",
  "Bay Area" matches "San Francisco", "London" matches "UK", etc.

The downstream filter is STRICT: when the user has specified locations, both
`false` and `null` will reject the job. So only return `null` when the posting
truly contains zero location information — not when you're merely uncertain."""


async def _extract_from_preview(
    title: str, company: str, location: str | None, description: str | None,
    filter_locations: list[str] | None = None,
) -> dict | None:
    """Run LLM extraction on a job preview (raw strings, not a DB model).

    When filter_locations is provided, adds a location_match field to the response.
    """
    try:
        client = get_openai_client()
        prompt = EXTRACTION_PROMPT.format(
            title=title,
            company=company,
            location=location or "Not specified",
            description=_truncate_description(description or ""),
        )
        if filter_locations:
            prompt += _LOCATION_FILTER_ADDENDUM.format(
                locations=", ".join(filter_locations),
            )
        messages = [
            {"role": "system", "content": EXTRACTION_SYSTEM},
            {"role": "user", "content": prompt},
        ]
        text = await _call_llm(client, messages)
        return _parse_json_response(text)
    except Exception:
        logger.debug("Extraction failed for preview: %s at %s", title, company)
        return None


_verify_semaphore: asyncio.Semaphore | None = None


def _get_verify_semaphore() -> asyncio.Semaphore:
    global _verify_semaphore
    if _verify_semaphore is None:
        from app.services.rate_limits import max_concurrent_browser
        _verify_semaphore = asyncio.Semaphore(max_concurrent_browser())
    return _verify_semaphore


async def _verify_jobs_with_extraction(
    jobs: list[dict],
    locations: list[str],
    min_salary: int | None,
) -> list[dict]:
    """Verify top candidate jobs against location/salary filters using LLM extraction.

    Returns only jobs that pass verification, annotated with extracted data.
    """
    async def _verify_one(job: dict) -> dict | None:
        # Cheap salary check FIRST using scraper-provided structured data,
        # before paying for LLM extraction.
        if min_salary and job.get("salary_max") is not None:
            if job["salary_max"] < min_salary:
                return None  # Definite reject — scraper said so

        async with _get_verify_semaphore():
            extracted = await _extract_from_preview(
                title=job.get("title", ""),
                company="",
                location=job.get("location"),
                description=job.get("description_html", ""),
                filter_locations=locations if locations else None,
            )
            if not extracted:
                # Extraction failed (LLM error / JSON parse / timeout). When
                # filters are active we cannot leave it as "pass by default" —
                # that's how NYC and Remote-only jobs were leaking past SF
                # filters in real runs. Hard-reject when we can't verify.
                if locations or min_salary:
                    return None
                return job

            # Salary filter: extracted data is the fallback when scraper didn't
            # have salary fields (most ATS scrapers don't surface them). Hard
            # reject: if min_salary is set and we still can't find a salary
            # after the LLM has read the JD, treat unknown as failing — the
            # user asked for a specific minimum and unknown is not acceptable.
            if min_salary:
                extracted_max = extracted.get("salary_max")
                if extracted_max is None:
                    return None  # Unknown after LLM read — reject
                if extracted_max < min_salary:
                    return None  # Confirmed below threshold

            # Location filter (LLM-verified). When locations are explicitly set,
            # both False and None (unknown) reject the job — this is the strict
            # behavior the user wants. Only `True` passes.
            if locations:
                location_match = extracted.get("location_match")
                if location_match is False or location_match is None:
                    return None

            # Annotate job with extracted data
            job = dict(job)  # copy
            if extracted.get("salary_min"):
                job["extracted_salary_min"] = extracted["salary_min"]
            if extracted.get("salary_max"):
                job["extracted_salary_max"] = extracted["salary_max"]
            if extracted.get("work_model"):
                job["extracted_work_model"] = extracted["work_model"]
            if extracted.get("locations"):
                job["extracted_locations"] = extracted["locations"]
            return job

    # Verify all candidates the picker passed in, capped at 10 to bound LLM
    # cost when guidance is absent (with guidance the picker collapses to 1).
    # Previously capped at 5, which leaked: a job ranked 6+ skipped the
    # location/salary verification entirely.
    candidates = jobs[:10]
    tasks = [_verify_one(job) for job in candidates]
    results = await asyncio.gather(*tasks)
    verified = [r for r in results if r is not None]
    logger.info(
        "Verified %d/%d jobs with extraction (locations=%s, min_salary=%s)",
        len(verified), len(candidates), locations, min_salary,
    )
    return verified


# ---------------------------------------------------------------------------
# Phase 2a': Direct job URL import (non-ATS, one-off jobs)
# ---------------------------------------------------------------------------


async def _job_url_exists(url: str) -> bool:
    """Check if a job with this URL already exists in the DB."""
    async with async_session() as session:
        result = await session.execute(select(Job).where(Job.url == url).limit(1))
        return result.scalar_one_or_none() is not None


async def _extract_direct_job_url(
    url: str,
    profile_keywords: dict,
    locations: list[str] | None = None,
    min_salary: int | None = None,
) -> tuple[CompanyHit | None, str]:
    """Fetch a job URL, extract details via LLM, return as a preview CompanyHit.

    Does NOT save to the DB — the extracted data is stashed in Redis and
    persisted only when the user confirms the import via /companies/import.
    Returns (hit, skip_reason).

    When `locations` or `min_salary` is set, the resulting one-job hit is
    routed through `_verify_jobs_with_extraction` for hard-reject filtering
    so the direct-URL path enforces the same constraints as ATS scrapes.
    """
    from app.services.job_url_importer import extract_job_from_url
    from app.services.scrape_cache import get_direct_extract, set_direct_extract

    # Dedupe against already-imported jobs in the DB
    if await _job_url_exists(url):
        return None, "Job URL already in database"

    # Reuse cached extraction if we already did the LLM work recently
    cached = await get_direct_extract(url)
    if cached:
        job_data = cached
    else:
        try:
            job_data = await extract_job_from_url(url)
        except ValueError as e:
            return None, f"Extraction failed: {str(e)[:80]}"
        except Exception as e:
            logger.warning("Direct URL extract error for %s: %s", url, e)
            return None, "Extract error"
        await set_direct_extract(url, job_data)

    # Score relevance against the user profile. score_job_relevance accesses
    # .title, .description, and .remote on its first argument — duck-type a
    # SimpleNamespace from job_data so we don't have to build a full ScrapedJob.
    from types import SimpleNamespace
    company_name = job_data.get("company", "Unknown")
    title = job_data.get("title", "")
    description = job_data.get("description", "")
    fake = SimpleNamespace(
        title=title,
        description=description,
        remote=job_data.get("remote", False),
    )
    score = score_job_relevance(fake, profile_keywords)

    desc_snippet = (description or "").strip()

    hit = CompanyHit(
        name=company_name,
        ats="direct",
        slug=_direct_slug_for(company_name),
        website=None,
        total_jobs=1,
        relevant_jobs=1 if score > 0 else 0,
        top_jobs=[{
            "title": title,
            "url": url,
            "location": job_data.get("location"),
            "relevance": score,
            "remote": job_data.get("remote", False),
            "salary_min": job_data.get("salary_min"),
            "salary_max": job_data.get("salary_max"),
            "description": desc_snippet,
            # The review UI renders description_html via dangerouslySetInnerHTML,
            # so escape the raw extracted text before wrapping it.
            "description_html": (
                f"<div style='white-space:pre-wrap'>{html.escape(desc_snippet)}</div>"
                if desc_snippet else None
            ),
        }],
        source="direct_url",
        description="",
        match_reason="",
    )

    # Hard-reject through the same verifier the ATS path uses, so direct-URL
    # hits don't leak past the user's location/salary constraints.
    if locations or min_salary:
        verified = await _verify_jobs_with_extraction(
            hit.top_jobs, locations or [], min_salary,
        )
        if not verified:
            return None, "Failed location/salary verification"
        hit.top_jobs = verified

    return hit, ""


def _direct_slug_for(name: str) -> str:
    """Synthetic slug for direct-URL hits — gives the frontend a stable
    per-company key and never collides with real ATS slugs."""
    normalized = re.sub(r"[^a-z0-9]+", "-", (name or "unknown").lower()).strip("-")
    return f"direct-{normalized or 'unknown'}"


# ---------------------------------------------------------------------------
# Phase 2b: Candidate evaluation
# ---------------------------------------------------------------------------


# 180s per candidate — empirically the previous 90s cap was cutting off
# slow but legitimate evals: an ATS scrape (~10-20s) + cheap-filter +
# picker (~5s) + verifier (~5s) + a Playwright drill that hits 50s on
# its own can comfortably exceed 90s. The semaphore (rate_limits) keeps
# concurrency bounded; this is just per-candidate wall-time headroom.
_CANDIDATE_TIMEOUT = 180


async def _evaluate_candidate(
    candidate: CompanyCandidate,
    profile_keywords: dict,
    http_client: httpx.AsyncClient,
    locations: list[str] | None = None,
    min_salary: int | None = None,
    guidance: str = "",
) -> tuple[CompanyHit | None, str]:
    """Evaluate a candidate: resolve ATS, scrape jobs, score. Timeout-protected.

    Returns (hit, skip_reason). If hit is not None, skip_reason is empty.
    """
    async with _get_eval_semaphore():
        try:
            return await asyncio.wait_for(
                _evaluate_candidate_inner(
                    candidate, profile_keywords, http_client,
                    locations, min_salary, guidance=guidance,
                ),
                timeout=_CANDIDATE_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Candidate evaluation timed out after %ds: %s",
                _CANDIDATE_TIMEOUT, candidate.name,
            )
            return None, "Evaluation timed out"
        except Exception:
            logger.warning(
                "Failed to evaluate candidate: %s", candidate.name, exc_info=True,
            )
            return None, "Evaluation error"


def _derive_guidance_from_profile(profile_keywords: dict) -> str:
    """Build a lightweight search hint from profile keywords when the user
    didn't provide explicit guidance.  Used to steer the Perplexity drill
    and browser agent so they know what role to look for."""
    parts: list[str] = []
    for title in list(profile_keywords.get("role_titles", set()))[:2]:
        if title:
            parts.append(title)
    for domain in list(profile_keywords.get("domains", set()))[:2]:
        if domain:
            parts.append(domain)
    return ", ".join(parts) if parts else ""


async def _evaluate_candidate_inner(
    candidate: CompanyCandidate,
    profile_keywords: dict,
    http_client: httpx.AsyncClient,
    locations: list[str] | None = None,
    min_salary: int | None = None,
    guidance: str = "",
) -> tuple[CompanyHit | None, str]:
    ats = candidate.ats
    slug = candidate.slug

    # When the user didn't type explicit guidance, derive a search hint from
    # their profile so the Perplexity drill and browser agent have something
    # concrete to search for (instead of skipping entirely).
    effective_guidance = guidance or _derive_guidance_from_profile(profile_keywords)

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
            # No supported ATS. Recovery:
            #   1. Find the company's careers page via web search
            #   2. Drill for individual job posting URLs (domain-scoped,
            #      then open web) and import them
            careers_url = await _search_company_careers_page(candidate.name)

            # Try drilling for individual jobs — works even without a careers
            # URL (the open-web search strategy doesn't need the domain).
            logger.info("DRILL_STRATEGY: lead_drill ATTEMPT '%s'", candidate.name)
            drilled = await _drill_lead_company_jobs(
                candidate.name, careers_url, profile_keywords,
                locations=locations, min_salary=min_salary,
            )

            if drilled:
                # Apply prefilter on the imported jobs (each is wrapped in a
                # CompanyHit with one job in top_jobs)
                merged_top_jobs: list[dict] = []
                for h in drilled:
                    merged_top_jobs.extend(h.top_jobs)

                # Cheap location/salary prefilter on the imported job dicts
                if locations or min_salary:
                    filtered: list[dict] = []
                    for j in merged_top_jobs:
                        # Build a fake ScrapedJob-like for the helpers
                        from types import SimpleNamespace
                        fake = SimpleNamespace(
                            title=j.get("title", ""),
                            location=j.get("location"),
                            remote=j.get("remote", False),
                            salary_min=j.get("salary_min"),
                            salary_max=j.get("salary_max"),
                        )
                        if not _job_passes_location_filter(fake, locations)[0]:
                            continue
                        if not _job_passes_salary_filter(fake, min_salary)[0]:
                            continue
                        filtered.append(j)
                    merged_top_jobs = filtered

                if merged_top_jobs:
                    logger.info(
                        "DRILL_STRATEGY: lead_drill SUCCESS '%s' n_jobs=%d",
                        candidate.name, len(merged_top_jobs),
                    )
                    return CompanyHit(
                        name=candidate.name,
                        ats="direct",
                        slug=_direct_slug_for(candidate.name),
                        website=careers_url,
                        careers_url=careers_url,
                        total_jobs=len(merged_top_jobs),
                        relevant_jobs=len(merged_top_jobs),
                        top_jobs=merged_top_jobs,
                        source=candidate.source,
                        description="",
                        match_reason="",
                        kind="ats",  # treat as ATS-like since we have real jobs
                    ), ""

            # Strategy 3: SPA drill — render the careers page in Playwright
            # and extract job links from the rendered DOM. This catches
            # Workday, Taleo, and custom JS career portals that search
            # engines can't index.
            #
            # If we don't have a careers_url yet, try a targeted search for
            # the company's Workday/career portal before giving up.
            if not careers_url:
                workday_results = await _precise_search(
                    f'"{candidate.name}" site:myworkdayjobs.com OR site:taleo.net', max_results=3
                )
                for r in workday_results:
                    url = r.get("url", "")
                    if "myworkdayjobs.com" in url or "taleo.net" in url:
                        # Verify the subdomain matches the company name.
                        # Workday: "adobe.wd5.myworkdayjobs.com" → "adobe"
                        # Taleo: "adobe.taleo.net" → "adobe"
                        domain = _domain_of(url)
                        if domain:
                            subdomain = domain.split(".")[0].lower()
                            if _slug_plausible_for_name(subdomain, candidate.name):
                                careers_url = url
                                logger.info("Found Workday/Taleo portal for '%s': %s", candidate.name, url)
                                break
                            else:
                                logger.debug(
                                    "Workday/Taleo URL %s subdomain '%s' doesn't match '%s', skipping",
                                    url, subdomain, candidate.name,
                                )

            # Strategy 3a: Perplexity drill — ask an LLM-grounded search
            # engine for specific current job URLs at this company. Cheaper
            # and often more reliable than the browser agent for well-known
            # companies (IBM, Google, PwC, Accenture) where Perplexity's
            # index is strong. Each URL is verified via extract_job_from_url.
            if effective_guidance:
                logger.info("DRILL_STRATEGY: perplexity_drill ATTEMPT '%s'", candidate.name)
                try:
                    perplexity_results = await asyncio.wait_for(
                        _drill_perplexity_for_job(
                            candidate.name, effective_guidance, profile_keywords,
                            locations, min_salary,
                        ),
                        timeout=30,
                    )
                except asyncio.TimeoutError:
                    logger.warning("Perplexity drill timed out for '%s'", candidate.name)
                    perplexity_results = []

                if perplexity_results:
                    merged_top_jobs = []
                    for h in perplexity_results:
                        merged_top_jobs.extend(h.top_jobs)
                    if merged_top_jobs:
                        logger.info(
                            "DRILL_STRATEGY: perplexity_drill SUCCESS '%s' n_jobs=%d",
                            candidate.name, len(merged_top_jobs),
                        )
                        return CompanyHit(
                            name=candidate.name,
                            ats="direct",
                            slug=_direct_slug_for(candidate.name),
                            website=careers_url,
                            careers_url=careers_url,
                            total_jobs=len(merged_top_jobs),
                            relevant_jobs=len(merged_top_jobs),
                            top_jobs=merged_top_jobs,
                            source=candidate.source,
                            description="",
                            match_reason="",
                            kind="ats",
                        ), ""

            # Strategy 3b: LLM-guided browser agent — interactively navigates
            # the careers page (types in search boxes, clicks filters, waits
            # for JS rendering) to find a matching job posting. Disabled by
            # default — it's a 50s-per-candidate cost and the funnel data
            # shows it produces <5% of hits. Toggle with
            # HOT_SEARCH_BROWSER_AGENT=1 in .env for broad searches.
            if careers_url and effective_guidance and settings.hot_search_browser_agent:
                logger.info("DRILL_STRATEGY: browser_agent ATTEMPT '%s'", candidate.name)
                try:
                    agent_results = await asyncio.wait_for(
                        _crawl_careers_page_for_job(
                            candidate.name, careers_url, effective_guidance,
                            profile_keywords, locations, min_salary,
                        ),
                        timeout=50,  # 50s budget within the 90s candidate timeout
                    )
                except asyncio.TimeoutError:
                    logger.warning("Browser agent timed out for '%s'", candidate.name)
                    agent_results = []

                if agent_results:
                    merged_top_jobs = []
                    for h in agent_results:
                        merged_top_jobs.extend(h.top_jobs)
                    if merged_top_jobs:
                        logger.info(
                            "DRILL_STRATEGY: browser_agent SUCCESS '%s' n_jobs=%d",
                            candidate.name, len(merged_top_jobs),
                        )
                        return CompanyHit(
                            name=candidate.name,
                            ats="direct",
                            slug=_direct_slug_for(candidate.name),
                            website=careers_url,
                            careers_url=careers_url,
                            total_jobs=len(merged_top_jobs),
                            relevant_jobs=len(merged_top_jobs),
                            top_jobs=merged_top_jobs,
                            source=candidate.source,
                            description="",
                            match_reason="",
                            kind="ats",
                        ), ""

            # Before surfacing a bare lead, check if the company is even
            # relevant to the search. This prevents "Logitech" from showing
            # up in an "AI safety" search just because SearXNG returned it.
            if effective_guidance:
                is_relevant, desc, reason = await _check_lead_relevance_and_context(
                    candidate.name, effective_guidance, careers_url,
                )
                if not is_relevant:
                    logger.info(
                        "Lead '%s' rejected as irrelevant to '%s': %s",
                        candidate.name, guidance[:40], reason,
                    )
                    return None, f"Not relevant to search: {reason}"
            else:
                desc = ""
                reason = ""

            # If no careers URL was found, generate a Google search link as fallback
            if not careers_url:
                from urllib.parse import quote_plus
                careers_url = f"https://www.google.com/search?q={quote_plus(candidate.name + ' careers jobs')}"

            logger.info(
                "Surfacing '%s' as bare lead hit (careers page: %s)",
                candidate.name, careers_url,
            )
            return CompanyHit(
                name=candidate.name,
                ats="",
                slug="",
                website=careers_url,
                careers_url=careers_url,
                source=candidate.source,
                description=desc or "",
                match_reason=reason or "",
                kind="lead",
            ), ""

    # Skip slugs we marked dead recently (404, repeated 429, etc.). Saves
    # ~5-30s of slug-verify + scrape work on companies we know we can't
    # reach. TTL is 24h so transient outages self-heal.
    from app.services.scrape_cache import is_slug_dead, mark_slug_dead
    if await is_slug_dead(ats, slug):
        return None, f"ATS slug recently failed (cached, retry in 24h): {ats}/{slug}"

    # Verify the slug exists
    verified = await _verify_ats_slug(ats, slug, http_client)
    if not verified:
        await mark_slug_dead(ats, slug, reason="slug verify returned False")
        return None, "ATS board not reachable"

    # Load from cache or scrape
    scraper = _SCRAPER_MAP.get(ats)
    if not scraper:
        return None, f"Unsupported ATS: {ats}"

    scraped_jobs = await get_scraped_jobs(ats, slug)
    if scraped_jobs is None:
        temp_company = _make_temp_company(ats, slug)
        try:
            scraped_jobs = await scraper.scrape_company(temp_company, http_client)
        except Exception as e:
            await mark_slug_dead(ats, slug, reason=f"scrape error: {type(e).__name__}")
            raise
        await set_scraped_jobs(ats, slug, scraped_jobs)

    if not scraped_jobs:
        # Empty board today — don't mark dead (the company may post tomorrow),
        # but we won't waste another scrape on this run.
        return None, "No open jobs found"

    total_scraped = len(scraped_jobs)

    # Cheap location/salary prefilter BEFORE relevance scoring — drops jobs
    # in the wrong city / below salary threshold based on the ScrapedJob's
    # structured fields, so we don't waste relevance ranking on jobs the
    # user definitely doesn't want.
    if locations or min_salary:
        prefiltered: list = []
        rejected_loc = 0
        rejected_sal = 0
        for sj in scraped_jobs:
            loc_pass, _loc_reason = _job_passes_location_filter(sj, locations)
            if not loc_pass:
                rejected_loc += 1
                continue
            sal_pass, _sal_reason = _job_passes_salary_filter(sj, min_salary)
            if not sal_pass:
                rejected_sal += 1
                continue
            prefiltered.append(sj)
        logger.info(
            "Prefilter for %s: %d → %d (rejected %d by location, %d by salary)",
            slug, len(scraped_jobs), len(prefiltered), rejected_loc, rejected_sal,
        )
        scraped_jobs = prefiltered

    if not scraped_jobs:
        return None, "No jobs in target location / above salary threshold"

    # Cap to 100 jobs AFTER prefiltering — gives us 100 jobs in the right
    # location instead of 100 random jobs and then a post-hoc filter.
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
            # Surface scraper salary fields so the verification step can do
            # a cheap numeric check instead of always calling the LLM.
            "salary_min": sj.salary_min,
            "salary_max": sj.salary_max,
        })

    job_previews.sort(key=lambda j: j["relevance"], reverse=True)

    # Hit if any job scores >= 75 on profile relevance
    relevant_jobs = [j for j in job_previews if j["relevance"] >= 75]
    if not relevant_jobs:
        top_score = job_previews[0]["relevance"] if job_previews else 0
        top_title = job_previews[0].get("title", "?") if job_previews else "?"
        if job_previews:
            # Rich skip reason so the activity log shows what was considered
            return None, (
                f"Considered {len(job_previews)} role(s); best profile match "
                f"was '{top_title[:50]}' (score {top_score}/100, threshold 75)"
            )
        return None, f"No relevant jobs (best score: {top_score})"

    # If the user provided search guidance, use LLM to pick the single best
    # job that matches the FULL search criteria (guidance + location + salary).
    # This prevents "Office Manager" from showing up in an "AI safety" search.
    # Only one LLM call per company — we send the top 10 job titles and ask
    # which one best matches.
    if effective_guidance:
        best_job, rejection_info = await _pick_best_job_for_guidance(
            relevant_jobs[:10], effective_guidance, locations, min_salary,
        )
        if best_job is None:
            # Scraped + profile-filtered but the LLM picker judged no role
            # matches the search topic (or the best match fell below the
            # relevance floor). Surface a rich skip reason so the activity
            # log shows WHY the company was rejected.
            if rejection_info:
                reason = rejection_info.get(
                    "reason", "LLM filter rejected all candidates"
                )
                return None, (
                    f"Considered {len(job_previews)} role(s); {reason}"
                )
            return None, f"No jobs matching search criteria (LLM filter)"
        relevant_jobs = [best_job]

    # LLM verification on the top relevant candidates. Now that the cheap
    # prefilter has done its work, this step is mostly catching ambiguous
    # cases (vague locations, missing salary fields, etc.).
    if locations or min_salary:
        relevant_jobs = await _verify_jobs_with_extraction(
            relevant_jobs, locations or [], min_salary,
        )
        if not relevant_jobs:
            return None, "No jobs matching location/salary filters (after LLM verification)"
        # Merge extracted annotations back into job_previews by URL
        verified_by_url = {j["url"]: j for j in relevant_jobs}
        for i, jp in enumerate(job_previews):
            if jp["url"] in verified_by_url:
                job_previews[i] = verified_by_url[jp["url"]]

    # Use company name from candidate (LLM-extracted), not slug
    display_name = candidate.name
    if display_name == slug.replace("-", " ").title():
        # Already slug-derived, keep it
        pass

    # description + match_reason are filled in centrally by _run_eval before
    # emission so every path (ATS + drills) gets the same grounded treatment.
    return CompanyHit(
        name=display_name,
        ats=ats,
        slug=slug,
        website=candidate.url,
        total_jobs=total_scraped,
        relevant_jobs=len(relevant_jobs),
        top_jobs=job_previews[:10],
        source=candidate.source,
        description="",
        match_reason="",
    ), ""


async def _evaluate_tracked_company(
    company_name: str,
    profile_keywords: dict,
    locations: list[str] | None = None,
    min_salary: int | None = None,
    guidance: str = "",
) -> tuple[CompanyHit | None, str]:
    """For a company already in the user's DB, query its existing jobs and
    surface any that match the current search filters as a hit.

    Unlike _evaluate_company (which scrapes the ATS), this reads jobs from the
    DB. The user's regular scraper pipeline keeps these fresh, so this is the
    cheapest possible path for tracked companies.
    """
    async with async_session() as session:
        # Find the Company record by name (case-insensitive)
        from sqlalchemy import func as sql_func, or_
        result = await session.execute(
            select(Company).where(sql_func.lower(Company.name) == company_name.lower())
        )
        company = result.scalar_one_or_none()
        if company is None:
            return None, "Company not found in DB"

        # Pull recent active jobs for this company
        result = await session.execute(
            select(Job)
            .where(Job.company_id == company.id)
            .where(Job.expired_at.is_(None))
            .order_by(Job.scraped_at.desc())
            .limit(200)
        )
        db_jobs: list[Job] = list(result.scalars().all())

    if not db_jobs:
        return None, "No active jobs in DB for tracked company"

    total_db_jobs = len(db_jobs)

    # Apply the same prefilter we use on scraped jobs. Job ORM objects expose
    # the same attributes (location, remote, salary_max) the helpers need.
    if locations or min_salary:
        prefiltered: list[Job] = []
        rejected_loc = 0
        rejected_sal = 0
        for j in db_jobs:
            loc_pass, _ = _job_passes_location_filter(j, locations)
            if not loc_pass:
                rejected_loc += 1
                continue
            sal_pass, _ = _job_passes_salary_filter(j, min_salary)
            if not sal_pass:
                rejected_sal += 1
                continue
            prefiltered.append(j)
        logger.info(
            "Tracked-company prefilter for %s: %d → %d (rejected %d loc, %d sal)",
            company_name, total_db_jobs, len(prefiltered), rejected_loc, rejected_sal,
        )
        db_jobs = prefiltered

    if not db_jobs:
        return None, "No tracked-company jobs match location/salary"

    # Score by relevance using the same keyword scorer as the ATS pipeline
    scored: list[tuple[int, Job]] = []
    for j in db_jobs:
        relevance = score_job_relevance(j, profile_keywords) if profile_keywords else 0
        scored.append((relevance, j))
    scored.sort(key=lambda pair: pair[0], reverse=True)

    relevant = [(s, j) for s, j in scored if s >= 75]
    if not relevant:
        top_score = scored[0][0] if scored else 0
        return None, f"No relevant tracked jobs (best score: {top_score})"

    # If guidance was provided, use LLM to pick the best matching job.
    if guidance:
        # Convert to the dict format _pick_best_job_for_guidance expects
        job_dicts = [
            {"title": j.title, "location": j.location, "department": "", "url": j.url}
            for s, j in relevant[:10]
        ]
        best, _ = await _pick_best_job_for_guidance(job_dicts, guidance, locations, min_salary)
        if best is None:
            return None, f"No tracked jobs matching search criteria"
        # Find the matching Job ORM object
        best_url = best.get("url")
        relevant = [(s, j) for s, j in relevant if j.url == best_url]
        if not relevant:
            return None, f"No tracked jobs matching search criteria"

    top_jobs = [
        {
            "id": str(j.id),
            "title": j.title,
            "location": j.location,
            "url": j.url,
            "posted_at": j.posted_at.isoformat() if j.posted_at else None,
            "relevance": s,
            "remote": j.remote,
            "salary_min": j.salary_min,
            "salary_max": j.salary_max,
            # Used by the LLM verifier below.
            "description_html": j.description_html or j.description,
        }
        for s, j in relevant[:10]
    ]

    # Hard reject through the same verifier the ATS scrape path uses. Cheap
    # filters above only catch jobs the scraper definitively flagged; the LLM
    # verifier handles vague locations and JDs whose salary lives in the body.
    if locations or min_salary:
        verified = await _verify_jobs_with_extraction(
            top_jobs, locations or [], min_salary,
        )
        if not verified:
            return None, "No tracked-company jobs survived LLM verification"
        top_jobs = verified

    # Company has per-ATS slug columns rather than a single `ats`/`slug`
    # pair — pick whichever is populated.
    if company.greenhouse_slug:
        tracked_ats, tracked_slug = "greenhouse", company.greenhouse_slug
    elif company.lever_slug:
        tracked_ats, tracked_slug = "lever", company.lever_slug
    elif company.ashby_slug:
        tracked_ats, tracked_slug = "ashby", company.ashby_slug
    elif company.eightfold_slug:
        tracked_ats, tracked_slug = "eightfold", company.eightfold_slug
    else:
        tracked_ats, tracked_slug = "", ""

    return CompanyHit(
        name=company.name,
        ats=tracked_ats,
        slug=tracked_slug,
        website=company.website,
        company_id=str(company.id),
        total_jobs=total_db_jobs,
        relevant_jobs=len(relevant),
        top_jobs=top_jobs,
        source="tracked",
        description=(
            f"You're already tracking {company.name}. "
            f"{len(relevant)} of their open roles match this search."
        ),
        match_reason=(
            f"You're already tracking {company.name} — "
            f"{len(relevant)} of their open roles match this search."
        ),
        kind="tracked",
    ), ""


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _is_duplicate_company(
    candidate_name: str,
    known_names: list[str],
) -> str | None:
    """Use a fast LLM to check if candidate_name is a duplicate of any known name.

    Returns the matching known name if it's a duplicate, or None if it's unique.
    Handles cases like "Meta AI" ↔ "Meta", "Google DeepMind" ↔ "DeepMind".
    """
    if not known_names:
        return None

    # Quick exact/substring check first (free, no LLM)
    cand_lower = candidate_name.lower().strip()
    for known in known_names:
        known_lower = known.lower().strip()
        if cand_lower == known_lower:
            return known
        # "Meta AI" contains "Meta", "Google DeepMind" contains "DeepMind"
        if cand_lower in known_lower or known_lower in cand_lower:
            return known

    # Only call LLM if there are plausible near-matches (share at least one word)
    cand_words = set(cand_lower.split()) - {"the", "inc", "co", "ai", "labs", "corp", "ltd"}
    near_matches = []
    for known in known_names:
        known_words = set(known.lower().split()) - {"the", "inc", "co", "ai", "labs", "corp", "ltd"}
        if cand_words & known_words:
            near_matches.append(known)

    if not near_matches:
        return None

    # LLM check for ambiguous cases
    try:
        client = get_openai_client()
        from app.ai.client import EXTRACTION_MODEL
        prompt = (
            f'Is "{candidate_name}" the same company as any of these?\n'
            + "\n".join(f"- {n}" for n in near_matches[:10])
            + '\n\nRespond with ONLY the matching company name, or "none" if no match.'
        )
        resp = await client.chat.completions.create(
            model=EXTRACTION_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=50,
        )
        answer = (resp.choices[0].message.content or "").strip().lower()
        if answer and answer != "none":
            # Find the best match from near_matches
            for nm in near_matches:
                if nm.lower() in answer or answer in nm.lower():
                    return nm
    except Exception:
        logger.debug("Dedup LLM check failed for %s", candidate_name)

    return None


# ---------------------------------------------------------------------------
# Lead-hit relevance check (used when we couldn't scrape the company's ATS
# but the company name might still be relevant to the user's search).
# ---------------------------------------------------------------------------


async def _check_lead_relevance_and_context(
    company_name: str,
    guidance: str,
    careers_url: str | None,
) -> tuple[bool, str, str]:
    """Check if a lead company is relevant to the search and generate context.

    Uses a fast LLM to:
      1. Determine if the company is relevant to the user's search guidance
      2. Generate a brief description of the company
      3. Explain why it might match (or not)

    Returns (is_relevant, description, match_reason).
    """
    try:
        client = get_openai_client()
        from app.ai.client import EXTRACTION_MODEL

        prompt = (
            f'The user searched for: "{guidance}"\n\n'
            f'A company called "{company_name}" was found.'
        )
        if careers_url:
            prompt += f'\nTheir careers page is: {careers_url}'
        prompt += (
            '\n\nAnswer in JSON:\n'
            '{\n'
            '  "relevant": true/false,  // Is this company plausibly relevant to the search?\n'
            '  "description": "1 sentence about what this company does",\n'
            '  "match_reason": "1 sentence why it matches or doesn\'t match the search"\n'
            '}'
        )

        resp = await client.chat.completions.create(
            model=EXTRACTION_MODEL,
            messages=[
                {"role": "system", "content": "You evaluate whether companies match a job search. Respond with ONLY valid JSON."},
                {"role": "user", "content": prompt},
            ],
            max_completion_tokens=200,
        )
        raw = (resp.choices[0].message.content or "").strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

        import json
        data = json.loads(raw)
        return (
            data.get("relevant", True),
            data.get("description", ""),
            data.get("match_reason", ""),
        )
    except Exception:
        logger.debug("Relevance check failed for %s", company_name)
        return True, "", ""  # default to relevant if check fails


# ---------------------------------------------------------------------------
# LLM job picker — selects the best job matching the full search criteria
# ---------------------------------------------------------------------------


# Relevance floor: picker rejects jobs whose LLM-judged match score is below
# this (1=unrelated, 5=excellent). Keeping at 3 accepts "adjacent-role" picks
# (e.g. ML Engineer for a Data Scientist query) while rejecting weak picks
# where the company has nothing in the right domain (the Mozilla → Quant User
# Researcher case we hit in the eval).

PICKER_RELEVANCE_FLOOR = 3


async def _pick_best_job_for_guidance(
    jobs: list[dict],
    guidance: str,
    locations: list[str] | None = None,
    min_salary: int | None = None,
    relevance_floor: int = PICKER_RELEVANCE_FLOOR,
) -> tuple[dict | None, dict | None]:
    """Use a fast LLM to pick the single best job that matches the user's
    full search criteria (natural language guidance + location + salary).

    Returns ``(picked_job, rejection_info)``:
      - picked_job: the selected dict from ``jobs``, or None if rejected
      - rejection_info: None when picked_job is not None; otherwise a dict
        ``{'best_title': str, 'best_score': int, 'reason': str}`` for
        callers that want to surface the rejection in the UI.
        Note: for empty/error/LLM-malformed paths, rejection_info may be
        None even when picked_job is None — callers should handle both.

    The picker asks the LLM for both an INDEX and a 1-5 RELEVANCE score in
    one call. If RELEVANCE < ``relevance_floor``, the pick is rejected —
    this prevents the "best of bad choices" bias where the picker
    confidently picks something even when nothing in the company's list is
    a real match for the search.
    """
    if not jobs:
        return None, None

    # Belt-and-suspenders: drop jobs the cheap filter already knows fail
    # location/salary. The LLM picker has been observed to "round up" close
    # candidates (e.g. an LA job for a SF query); pre-filtering means the
    # picker can't pick a reject in the first place.
    if locations or min_salary:
        from types import SimpleNamespace
        filtered: list[dict] = []
        for j in jobs:
            fake = SimpleNamespace(
                title=j.get("title", ""),
                location=j.get("location"),
                remote=j.get("remote", False),
                salary_min=j.get("salary_min"),
                salary_max=j.get("salary_max"),
            )
            if locations and not _job_passes_location_filter(fake, locations)[0]:
                continue
            if min_salary and not _job_passes_salary_filter(fake, min_salary)[0]:
                continue
            filtered.append(j)
        if not filtered:
            return None, {
                "best_title": jobs[0].get("title", "?"),
                "best_score": 0,
                "reason": "all candidates failed location/salary prefilter",
            }
        jobs = filtered

    try:
        client = get_openai_client()
        from app.ai.client import EXTRACTION_MODEL

        # Build the job list for the prompt
        job_lines = []
        for i, j in enumerate(jobs):
            parts = [f"{i+1}. {j.get('title', '?')}"]
            if j.get("location"):
                parts.append(f"({j['location']})")
            if j.get("department"):
                parts.append(f"[{j['department']}]")
            job_lines.append(" ".join(parts))

        criteria_parts = [f'Search: "{guidance}"']
        constraint_lines: list[str] = []
        if locations:
            criteria_parts.append(f"Locations: {', '.join(locations)}")
            constraint_lines.append(
                f"- REJECT any job not located in one of: {', '.join(locations)}"
            )
        if min_salary:
            criteria_parts.append(f"Min salary: ${min_salary:,}")
            constraint_lines.append(
                f"- REJECT any job whose salary is below ${min_salary:,}"
            )

        constraint_block = ""
        if constraint_lines:
            constraint_block = (
                "\nHARD CONSTRAINTS (return '0:0' if all candidates violate any):\n"
                + "\n".join(constraint_lines)
                + "\n"
            )

        prompt = (
            f"User's search criteria:\n"
            f"{chr(10).join(criteria_parts)}\n"
            f"{constraint_block}\n"
            f"Jobs at this company:\n"
            f"{chr(10).join(job_lines)}\n\n"
            f"Pick the job that best matches, and rate how well it matches.\n"
            f"Respond with 'INDEX:RELEVANCE' — e.g. '3:5'. Use '0:0' if no job\n"
            f"is a reasonable match OR if every candidate violates a hard\n"
            f"constraint above.\n"
            f"RELEVANCE scale (1-5):\n"
            f"  1 = unrelated (e.g. 'Office Manager' for 'ML engineer')\n"
            f"  2 = tangentially related\n"
            f"  3 = adjacent domain (e.g. ML Engineer for 'data scientist')\n"
            f"  4 = good match (same role family)\n"
            f"  5 = excellent match (exactly what was asked)"
        )

        resp = await client.chat.completions.create(
            model=EXTRACTION_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Pick the best-matching job and rate relevance 1-5. "
                        "Output exactly 'INDEX:RELEVANCE' (e.g. '3:4'). "
                        "Use '0:0' if no job reasonably matches."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            # Generous budget: GPT-5-family models can consume completion
            # tokens on hidden reasoning before emitting content.
            max_completion_tokens=1500,
        )

        answer = (resp.choices[0].message.content or "").strip()
        logger.debug("LLM picker raw answer: %r (for %d jobs)", answer[:100], len(jobs))
        # Parse "INDEX:RELEVANCE" — extract first two integers in order.
        nums = re.findall(r"\d+", answer)
        if not nums:
            logger.info(
                "LLM picker returned no numbers (response: %r) — rejecting all %d candidates",
                answer[:80], len(jobs),
            )
            return None, None

        idx = int(nums[0]) - 1  # Convert 1-indexed to 0-indexed
        relevance = int(nums[1]) if len(nums) >= 2 else 0

        if idx < 0 or idx >= len(jobs):
            logger.info(
                "LLM picker returned 0 (none match) — rejecting %d candidates; "
                "first 3 titles: %s",
                len(jobs),
                [j.get("title", "?")[:50] for j in jobs[:3]],
            )
            return None, {
                "best_title": jobs[0].get("title", "?"),
                "best_score": 0,
                "reason": "LLM judged no job in the listing matches the search topic",
            }

        picked_title = jobs[idx].get("title", "?")

        if relevance < relevance_floor:
            logger.info(
                "LLM picker below floor (relevance=%d, floor=%d) — rejecting "
                "'%s' (picked for search '%s' but match is weak)",
                relevance, relevance_floor, picked_title[:60], guidance[:40],
            )
            return None, {
                "best_title": picked_title,
                "best_score": relevance,
                "reason": (
                    f"Best match '{picked_title[:60]}' scored {relevance}/5 "
                    f"— below threshold ({relevance_floor}/5) for the search topic"
                ),
            }

        logger.info(
            "LLM picked job #%d '%s' (relevance=%d) for search '%s'",
            idx + 1, picked_title[:50], relevance, guidance[:40],
        )
        return jobs[idx], None

    except Exception:
        logger.debug("LLM job picker failed, returning first job as fallback")
        return (jobs[0] if jobs else None), None



# ---------------------------------------------------------------------------
# Main search loop — extracted to app.services.hot_search.orchestration so
# this file stays focused on helpers. Re-exported here for backward compat
# (the router and tests import `run_hot_company_search` from this module).
# The import lives at the bottom of the file so the orchestration module
# can pull helpers from this one without circular-load issues.
# ---------------------------------------------------------------------------

from app.services.hot_search.orchestration import run_hot_company_search  # noqa: E402,F401


