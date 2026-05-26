"""Persistent Playwright browser pool.

Keeps a single Chromium instance alive across the app lifetime. Page fetches
open a new tab and close it when done — no per-request launch overhead.

Typical speedup: 15s → 3s per page fetch.
"""

import asyncio
import logging

from playwright.async_api import Browser, Playwright, async_playwright

logger = logging.getLogger(__name__)

_pw: Playwright | None = None
_browser: Browser | None = None
_lock = asyncio.Lock()

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


async def _ensure_browser() -> Browser:
    """Launch the browser if not already running."""
    global _pw, _browser
    async with _lock:
        if _browser and _browser.is_connected():
            return _browser
        if _pw is None:
            _pw = await async_playwright().start()
        _browser = await _pw.chromium.launch(headless=True)
        logger.info("Browser pool: launched Chromium")
        return _browser


async def fetch_page_text(url: str, wait_ms: int = 3000, timeout_ms: int = 20000) -> str:
    """Fetch a URL in a new tab and return visible text. Reuses the shared browser.

    Args:
        url: Page URL.
        wait_ms: Extra settle time for JS rendering after domcontentloaded.
        timeout_ms: Navigation timeout.

    Returns:
        Visible text content (up to 15000 chars), or empty string on failure.
    """
    try:
        browser = await _ensure_browser()
        context = await browser.new_context(
            user_agent=_USER_AGENT,
            ignore_https_errors=True,
        )
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            if wait_ms > 0:
                await page.wait_for_timeout(wait_ms)
            text = await page.inner_text("body")
        except Exception as e:
            logger.debug("Page fetch failed for %s: %s", url, e)
            text = ""
        finally:
            await context.close()

        return text[:15000] if text else ""

    except Exception as e:
        logger.warning("Browser pool fetch error for %s: %s", url, e)
        return ""


async def fetch_page_links(
    url: str,
    wait_ms: int = 4000,
    timeout_ms: int = 25000,
) -> list[tuple[str, str]]:
    """Render a page in Playwright and return all <a> links as (href, text) tuples.

    Useful for extracting job posting links from JavaScript-rendered careers pages
    (Workday, Taleo, custom SPAs) that traditional search engines can't index.
    """
    try:
        browser = await _ensure_browser()
        context = await browser.new_context(
            user_agent=_USER_AGENT,
            ignore_https_errors=True,
        )
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            if wait_ms > 0:
                await page.wait_for_timeout(wait_ms)
            # Extract all visible links with their text
            links = await page.evaluate("""() => {
                const results = [];
                for (const a of document.querySelectorAll('a[href]')) {
                    const href = a.href;
                    const text = (a.innerText || '').trim().substring(0, 200);
                    if (href && text && href.startsWith('http')) {
                        results.push([href, text]);
                    }
                }
                return results;
            }""")
            return links or []
        except Exception as e:
            logger.debug("Link extraction failed for %s: %s", url, e)
            return []
        finally:
            await context.close()
    except Exception as e:
        logger.warning("Browser pool link extraction error for %s: %s", url, e)
        return []


async def discover_ashby_org_slug(
    parent_url: str, timeout_ms: int = 25000, wait_ms: int = 4000
) -> str | None:
    """Open ``parent_url`` in Playwright and sniff requests for the org slug.

    Many companies embed Ashby via ``?ashby_jid=<uuid>`` on their own careers
    page. The HTML is a JS-rendered shell that doesn't expose the canonical
    Ashby slug — but at runtime the page fires off network requests to
    ``https://jobs.ashbyhq.com/<slug>/...``. We capture the slug from the
    first such URL and let the caller build the canonical posting URL.
    """
    import re

    pattern = re.compile(r"https?://jobs\.ashbyhq\.com/([A-Za-z0-9_-]+)/", re.IGNORECASE)
    found: list[str] = []

    try:
        browser = await _ensure_browser()
        context = await browser.new_context(
            user_agent=_USER_AGENT,
            ignore_https_errors=True,
        )
        page = await context.new_page()

        def _on_request(req):
            m = pattern.match(req.url)
            if m:
                found.append(m.group(1))

        page.on("request", _on_request)
        try:
            await page.goto(parent_url, wait_until="domcontentloaded", timeout=timeout_ms)
            await page.wait_for_timeout(wait_ms)
        except Exception as e:
            logger.debug("Ashby slug discovery navigation failed for %s: %s", parent_url, e)
        finally:
            await context.close()
    except Exception as e:
        logger.warning("Ashby slug discovery error for %s: %s", parent_url, e)
        return None

    # Skip "embed" — that's a generic path, not the org slug
    for slug in found:
        if slug.lower() != "embed":
            return slug
    return None


async def shutdown() -> None:
    """Close the shared browser. Call on app shutdown."""
    global _pw, _browser
    if _browser:
        try:
            await _browser.close()
        except Exception:
            pass
        _browser = None
    if _pw:
        try:
            await _pw.stop()
        except Exception:
            pass
        _pw = None
    logger.info("Browser pool: shut down")
