"""Import a one-off job from any URL.

Fetches the page, extracts job details via LLM, and returns a JobCreate-ready dict.
Handles companies without a supported ATS (Microsoft, Google, Apple, etc.).
"""

import json
import logging
import re

import httpx

from app.ai.client import EXTRACTION_MODEL, get_openai_client
from app.ai.keyword_generator import _strip_fences

logger = logging.getLogger(__name__)

_EXTRACTION_SYSTEM = """\
You are a job listing parser. Given raw HTML text from a job posting page, extract \
the job details into a structured JSON object.

Output ONLY valid JSON (no markdown fences) matching this schema:
{
  "title": "Job title as listed",
  "company": "Company name",
  "location": "City, State (or 'Remote', or null if not clear)",
  "remote": true | false,
  "salary_min": 120000 | null,
  "salary_max": 180000 | null,
  "description": "Full job description text, preserving key responsibilities, qualifications, and benefits. Strip navigation, headers, footers, and marketing fluff. 500-3000 chars."
}

Rules:
- Extract ONLY what is on the page. Do not fabricate.
- If salary is given hourly, annualize (×2080). If monthly, annualize (×12). USD only.
- description: include the job's actual content — responsibilities, qualifications, what you'll do. Skip generic company boilerplate and "equal opportunity employer" language.
- If the page doesn't look like a job posting (e.g., landing page, 404), return {"error": "not a job page"}.
- Keep title concise (drop "Apply now" suffixes, department prefixes if redundant).
"""


async def _fetch_page_playwright(url: str) -> str:
    """Fetch a URL using the shared browser pool (handles JS rendering + SSL)."""
    from app.services.browser_pool import fetch_page_text
    return await fetch_page_text(url, wait_ms=3000)


async def _fetch_page_httpx(url: str) -> str:
    """Fetch a URL using httpx (fast, for static pages)."""
    try:
        async with httpx.AsyncClient(
            timeout=20, follow_redirects=True, verify=False
        ) as client:
            resp = await client.get(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Accept": "text/html,application/xhtml+xml",
                },
            )
            resp.raise_for_status()
            html = resp.text
    except Exception as e:
        logger.warning("httpx fetch failed for %s: %s", url, e)
        return ""

    # Strip scripts and styles
    html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:15000]


async def _fetch_page(url: str) -> str:
    """Fetch a URL, trying Playwright first (JS + SSL), falling back to httpx."""
    # Try Playwright first — handles JS-rendered pages (Microsoft, Google, etc.)
    text = await _fetch_page_playwright(url)
    if text and len(text.strip()) >= 200:
        return text

    # Fallback to httpx for simpler static pages
    text = await _fetch_page_httpx(url)
    return text


_ASHBY_JID_RE = re.compile(r"[?&]ashby_jid=([a-f0-9-]{8,})", re.IGNORECASE)


def _ashby_jid_from_url(url: str) -> str | None:
    m = _ASHBY_JID_RE.search(url or "")
    return m.group(1) if m else None


async def _resolve_ashby_canonical_url(parent_url: str, jid: str) -> str | None:
    """For pages that embed Ashby via ``?ashby_jid=...``, discover the org slug
    by sniffing network requests and return the canonical Ashby posting URL.
    """
    from app.services.browser_pool import discover_ashby_org_slug
    slug = await discover_ashby_org_slug(parent_url)
    if not slug:
        return None
    return f"https://jobs.ashbyhq.com/{slug}/{jid}"


async def extract_job_from_url(url: str) -> dict:
    """Fetch a URL and extract job details via LLM.

    Returns a dict with {title, company, location, remote, salary_min, salary_max, description}
    ready for JobCreate, or raises ValueError if extraction fails.
    """
    if not url or not url.startswith(("http://", "https://")):
        raise ValueError("Invalid URL")

    # Fetch page
    try:
        page_text = await _fetch_page(url)
    except httpx.HTTPStatusError as e:
        raise ValueError(f"Could not fetch page (HTTP {e.response.status_code})")
    except Exception as e:
        raise ValueError(f"Could not fetch page: {str(e)[:100]}")

    # Ashby-embedded SPA pivot: many companies (Ditto, Reka, etc.) embed Ashby
    # job postings on their own careers page via ``?ashby_jid=<uuid>``. The
    # parent page is a JS shell that returns ~120 chars of "Loading..." and
    # the actual job lives in a cross-origin Ashby iframe whose text we can't
    # scrape directly. Detect this pattern and pivot to the canonical Ashby
    # URL — discovering the org slug by sniffing network requests during a
    # Playwright render of the parent page.
    if len(page_text) < 500:
        jid = _ashby_jid_from_url(url)
        if jid:
            try:
                canonical = await _resolve_ashby_canonical_url(url, jid)
            except Exception:
                logger.exception("Ashby pivot failed for %s", url)
                canonical = None
            if canonical:
                logger.info("Pivoting Ashby embed %s -> %s", url, canonical)
                try:
                    pivoted = await _fetch_page(canonical)
                except Exception:
                    pivoted = ""
                if len(pivoted) >= 500:
                    page_text = pivoted

    if len(page_text) < 200:
        raise ValueError(
            "Page has very little content. The site may require login or JavaScript rendering. "
            "Please add the job manually instead."
        )

    # LLM extraction
    client = get_openai_client()
    try:
        response = await client.chat.completions.create(
            model=EXTRACTION_MODEL,
            messages=[
                {"role": "system", "content": _EXTRACTION_SYSTEM},
                {"role": "user", "content": f"URL: {url}\n\nPage text:\n{page_text}"},
            ],
            max_completion_tokens=4000,
        )
    except Exception as e:
        logger.exception("LLM extraction failed for %s", url)
        raise ValueError(f"LLM extraction failed: {str(e)[:100]}")

    raw = response.choices[0].message.content or ""
    raw = _strip_fences(raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Failed to parse extraction JSON: %s", raw[:300])
        raise ValueError("Could not parse job details from this page. Try adding manually.")

    if "error" in data:
        raise ValueError("This URL doesn't look like a job posting.")

    # Validate required fields
    if not data.get("title") or not data.get("company"):
        raise ValueError("Could not extract job title and company from this page.")

    # Normalize and set defaults
    return {
        "title": data.get("title", "").strip(),
        "company": data.get("company", "").strip(),
        "location": (data.get("location") or "").strip() or None,
        "remote": bool(data.get("remote", False)),
        "salary_min": data.get("salary_min"),
        "salary_max": data.get("salary_max"),
        "description": data.get("description", "").strip() or "(no description extracted)",
        "url": url,
        "source": "manual",
    }
