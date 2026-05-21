"""Title-only careers-page scraper for hot search v2.

Replaces the v1 agentic ``_crawl_careers_page_for_job`` (in
``evaluation.py``, 600+ lines of LLM-driven tool-call loop) with a much
simpler primitive: navigate the careers page, capture XHR responses,
extract job titles + URLs. No LLM in this loop.

The v2 pipeline does relevance matching downstream via the ranking
module (embeddings + LLM rerank), so this scraper has only one job:
return as many ``(title, url, location)`` triples as the page exposes,
within a per-page time budget.

Two extraction sources are unioned:
  1. XHR JSON interception — enterprise SPAs (Workday, Taleo, Apple,
     Google) typically load jobs via an API call; we mine those JSON
     responses directly. The shape-walker is reused verbatim from
     ``evaluation._extract_jobs_from_api_responses``.
  2. DOM link extraction — for old-school careers pages that render
     jobs as ``<a href>`` server-side.

Per-page budget: 30s hard cap. If a SPA hasn't loaded by then we move
on; downstream still has the candidate (just with no titles).
"""

from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)


# Hard cap so a slow SPA can't stall the whole search. The v1 browser
# agent's ~50s/page was the dominant tail-latency culprit.
_PAGE_BUDGET_S = 30.0
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Keywords used to decide if an XHR response *might* be a job listing.
# Liberal — the JSON-shape sniffer below will validate before we accept.
_API_HINT_KEYWORDS = (
    "job", "career", "position", "vacanc", "opening", "posting",
    "requisit", "search", "/wday/", "/cxs/", "graphql", "/role",
    "/listing", "/hiring",
)

# Heuristic title-field names spanning common ATSes.
_TITLE_KEYS = (
    "title", "jobTitle", "name", "heading", "displayTitle",
    "postingTitle", "requisitionTitle",
)
_URL_KEYS = (
    "externalPath", "url", "jobUrl", "href", "link",
    "apply_url", "external_url", "applyUrl", "canonicalUrl",
    "detailUrl", "permaLink", "jobDetailsUrl",
)
_LOC_KEYS = (
    "location", "locations", "primaryLocation", "city",
    "jobLocation", "loc",
)

# Common envelope paths to find the job array inside an API response.
# Ordered most-specific-first.
_LIST_PATHS: tuple[tuple[str, ...], ...] = (
    ("jobPostings",),
    ("results",),
    ("jobs",),
    ("postings",),
    ("items",),
    ("hits",),
    ("requisitions",),
    ("openings",),
    ("searchResults",),
    ("searchResults", "jobPostings"),
    ("data", "jobs"),
    ("data", "jobPostings"),
    ("data", "careers", "jobs"),
    ("data", "search", "jobs"),
    ("data", "requisitions"),
    ("payload", "jobs"),
    ("records",),
)


def _looks_like_job_list_shape(data) -> bool:
    """Quick shape sniffer — does this JSON look like a list of job-like
    objects? Catches XHRs whose URL doesn't match keyword hints (e.g.
    Apple's /api/role/... endpoints)."""
    if not isinstance(data, (list, dict)):
        return False
    sample: list = []
    if isinstance(data, list):
        sample = data[:3]
    else:
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
    if not sample:
        return False
    return all(
        isinstance(item, dict) and any(tk in item for tk in _TITLE_KEYS)
        for item in sample
    )


def _extract_loc(item: dict) -> str | None:
    """Pull a location string from a job item, handling list/dict/string
    variants — every ATS shapes this field differently."""
    for k in _LOC_KEYS:
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()[:200]
        if isinstance(v, list) and v:
            first = v[0]
            if isinstance(first, str):
                return first.strip()[:200]
            if isinstance(first, dict):
                for nk in ("name", "city", "label", "displayName"):
                    nv = first.get(nk)
                    if isinstance(nv, str) and nv.strip():
                        return nv.strip()[:200]
        if isinstance(v, dict):
            for nk in ("name", "city", "label", "displayName"):
                nv = v.get(nk)
                if isinstance(nv, str) and nv.strip():
                    return nv.strip()[:200]
    return None


def _extract_jobs_from_xhr(captured: list[dict]) -> list[dict]:
    """Walk captured XHR responses, find job arrays, return ``[{title, url,
    location?}]``. Mirrors evaluation._extract_jobs_from_api_responses,
    extended to also pull location.
    """
    results: list[dict] = []
    seen_urls: set[str] = set()
    for cap in captured:
        data = cap.get("data")
        response_url = cap.get("url", "")

        candidates: list = []
        if isinstance(data, list):
            candidates = data
        elif isinstance(data, dict):
            for path in _LIST_PATHS:
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

        try:
            resp_parsed = urlparse(response_url)
            host_base = f"{resp_parsed.scheme}://{resp_parsed.hostname}"
        except Exception:
            host_base = ""

        for item in candidates[:60]:
            if not isinstance(item, dict):
                continue
            url = None
            for k in _URL_KEYS:
                v = item.get(k)
                if isinstance(v, str) and v:
                    url = v
                    break
            title = None
            for k in _TITLE_KEYS:
                v = item.get(k)
                if isinstance(v, str) and v.strip():
                    title = v.strip()
                    break
            if not (url and title):
                continue
            # Resolve relative URLs against the API host
            if not url.startswith(("http://", "https://")):
                if host_base:
                    url = urljoin(host_base + "/", url.lstrip("/"))
                else:
                    continue
            if url in seen_urls:
                continue
            seen_urls.add(url)
            results.append({
                "title": title[:200],
                "url": url,
                "location": _extract_loc(item),
            })
    return results


_DOM_JOB_LINK_JS = r"""
(pageHost) => {
    // Collect <a href> links that point at specific job postings.
    // We prefer "URL shape says job posting" over "text says job-like" —
    // job titles in nav menus and content cards otherwise leak through.
    const links = Array.from(document.querySelectorAll('a[href]'));
    const out = [];
    const seen = new Set();

    // ATS host links — strongest signal. Notion, lots of startups, etc.
    // these out to greenhouse/lever/ashby.
    const ATS_HOSTS = /^https?:\/\/(jobs\.lever\.co|jobs\.ashbyhq\.com|boards\.greenhouse\.io|job-boards\.greenhouse\.io|jobs\.eightfold\.ai|careers\.eightfold\.ai)\//i;

    // URL paths that strongly suggest a specific job posting on a
    // company's own domain. Each requires a non-trivial ID/slug segment
    // after a JOB keyword in the PATH (not query string) — bare /jobs
    // or /careers are listing pages, not postings, and a UUID in a
    // tracking query param ("?did=<uuid>") isn't a job ID.
    const JOB_URL_PATTERNS = [
        /\/jobs?\/[^\/?#]{4,}/i,                           // /jobs/<id-or-slug>
        /\/job\/\d{3,}/i,                                  // /job/12345
        /\/careers\/[^\/?#]+\/[^\/?#]+/i,                  // /careers/<dept>/<role>
        /\/positions?\/[^\/?#]{4,}/i,                      // /positions/<id>
        /\/openings?\/[^\/?#]{4,}/i,                       // /openings/<id>
        /\/requisitions?\/[^\/?#]{4,}/i,                   // /requisitions/<id>
        /[?&](jobId|job_id|reqId|requisitionId|gh_jid)=/i, // query-string IDs
        /\/listing\/[^\/?#]+\/\d{3,}/i,                    // /listing/<slug>/<id>
        // UUID in path AFTER /jobs/ or /careers/ — used by some custom
        // portals that aren't ATS-hosted. Constrain to path-after-jobs
        // so URLs like chatgpt.com/?session-id=<uuid> don't count.
        /\/(job|career|position|opening)s?\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i,
    ];

    // Explicit blocks — non-job content masquerading as job-like.
    // Tested ON THE PATH portion only so a path like /jobs/about-the-team
    // (a real careers landing page) gets through if its tail says
    // something else.
    const URL_BLOCKLIST = /\/(docs?|help|about-us|contact|press|news|blog|legal|privacy|terms|support|status|community|partners|investors|customers|company\/?$|pricing|enterprise|signin|signup|login|register|account|home)(\/|$|\?|#)/i;

    for (const a of links) {
        const href = a.href || '';
        if (!href || !href.startsWith('http')) continue;
        if (seen.has(href)) continue;

        const txt = (a.innerText || a.textContent || '').trim();

        // Branch 1: ATS host — accept regardless of text length, the URL
        // itself proves it's a posting.
        if (ATS_HOSTS.test(href)) {
            seen.add(href);
            out.push({ title: txt ? txt.slice(0, 200) : '', url: href });
            if (out.length >= 80) break;
            continue;
        }

        // Branch 2: company-domain URLs — require same-or-subdomain
        // match against the page we're on (cross-domain link-outs like
        // openai.com → chatgpt.com aren't job postings), URL-shape
        // match, non-trivial text, and not in blocklist.
        let host = '';
        try { host = new URL(href).hostname.replace(/^www\./, ''); } catch (e) { continue; }
        if (pageHost && host !== pageHost && !host.endsWith('.' + pageHost) && !pageHost.endsWith('.' + host)) continue;
        if (URL_BLOCKLIST.test(href)) continue;
        if (txt.length < 8) continue;
        let matched = false;
        for (const pat of JOB_URL_PATTERNS) {
            if (pat.test(href)) { matched = true; break; }
        }
        if (!matched) continue;

        seen.add(href);
        out.push({ title: txt.slice(0, 200), url: href });
        if (out.length >= 80) break;
    }
    return out;
}
"""


async def list_job_titles(
    careers_url: str,
    *,
    max_titles: int = 50,
    timeout_s: float = _PAGE_BUDGET_S,
) -> list[dict]:
    """Navigate ``careers_url`` in a headless browser, capture XHR JSON +
    DOM job links, return up to ``max_titles`` ``(title, url, location?)``
    triples.

    Returns an empty list on timeout, navigation error, or no matches.
    Caller decides what to do with that signal — drop the company,
    requeue for v1 fallback, etc.

    Reuses the persistent browser pool from ``app.services.browser_pool``
    so we don't pay startup cost per page.
    """
    # Lazy imports — keeps the module importable in environments without
    # Playwright (tests, eval scripts that don't need this layer).
    from app.services.browser_pool import _ensure_browser

    captured: list[dict] = []

    async def _on_response(response):
        try:
            if response.status != 200:
                return
            ct = (response.headers.get("content-type") or "").lower()
            if "json" not in ct:
                return
            cl = response.headers.get("content-length")
            if cl and cl.isdigit() and int(cl) > 5_000_000:
                return
            if len(captured) >= 25:
                return

            url_lower = response.url.lower()
            matches_keyword = any(kw in url_lower for kw in _API_HINT_KEYWORDS)

            data = await response.json()
            looks_like_jobs = (
                _looks_like_job_list_shape(data)
                if not matches_keyword else False
            )
            if not matches_keyword and not looks_like_jobs:
                return
            captured.append({"url": response.url, "data": data})
        except Exception:
            # XHR may close mid-read; encoding can flake. Silent skip.
            pass

    try:
        browser = await _ensure_browser()
    except Exception:
        logger.exception("careers_titles: browser pool unavailable for %s", careers_url)
        return []

    context = None
    page = None
    try:
        context = await asyncio.wait_for(
            browser.new_context(user_agent=_USER_AGENT, ignore_https_errors=True),
            timeout=10.0,
        )
        page = await context.new_page()
        page.on("response", _on_response)

        # Outer wait_for caps total time spent on this page.
        async def _do_navigate():
            try:
                await page.goto(careers_url, wait_until="domcontentloaded", timeout=20000)
            except Exception as e:
                logger.info("careers_titles: navigate failed for %s: %s", careers_url, e)
                return
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            # Longer settle for SPA-heavy pages — Stripe in particular
            # streams job data ~5s after networkidle. The 30s outer
            # budget can absorb this.
            await page.wait_for_timeout(5000)
            # Scroll once to trigger lazy-load behaviors on SPA pages
            # that defer XHR until the user scrolls toward the listings.
            try:
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(2000)
            except Exception:
                pass

        await asyncio.wait_for(_do_navigate(), timeout=timeout_s - 5)

        # XHR mining
        xhr_jobs = _extract_jobs_from_xhr(captured)

        # DOM mining — runs even if XHR returned plenty, so we union
        # both sources (catches plain-HTML careers pages that ship some
        # jobs in HTML + some via XHR).
        # Pass the page host so the JS extractor can reject cross-domain
        # links (e.g. openai.com → chatgpt.com tracking links).
        try:
            page_host = (urlparse(careers_url).hostname or "").lower()
            if page_host.startswith("www."):
                page_host = page_host[4:]
            dom_jobs_raw = await asyncio.wait_for(
                page.evaluate(_DOM_JOB_LINK_JS, page_host or ""),
                timeout=5.0,
            )
        except Exception:
            dom_jobs_raw = []

        # Resolve DOM URLs and dedupe against XHR set.
        seen: set[str] = {j["url"] for j in xhr_jobs}
        merged = list(xhr_jobs)
        for j in dom_jobs_raw or []:
            url = (j.get("url") or "").strip()
            title = (j.get("title") or "").strip()
            if not (url and title):
                continue
            # Filter out obvious nav links (homepage, about, etc.) by
            # requiring the path to have at least one non-trivial segment.
            try:
                parsed = urlparse(url)
                if not parsed.path or parsed.path.strip("/") in {"", "careers", "jobs"}:
                    continue
            except Exception:
                continue
            if url in seen:
                continue
            seen.add(url)
            merged.append({"title": title[:200], "url": url, "location": None})

        return merged[:max_titles]

    except asyncio.TimeoutError:
        logger.info(
            "careers_titles: %s timed out at %.0fs; partial captures=%d",
            careers_url, timeout_s, len(captured),
        )
        # Even on timeout, what we captured via XHR may be useful.
        try:
            return _extract_jobs_from_xhr(captured)[:max_titles]
        except Exception:
            return []
    except Exception:
        logger.exception("careers_titles: unexpected error scraping %s", careers_url)
        return []
    finally:
        if page is not None:
            try:
                await page.close()
            except Exception:
                pass
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass


__all__ = ["list_job_titles"]
