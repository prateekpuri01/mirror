"""Playwright-based URL crawler for onboarding profile enrichment."""

import asyncio
import logging
from collections.abc import Callable

from playwright.async_api import TimeoutError as PlaywrightTimeout
from playwright.async_api import async_playwright

from app.services.linkedin_scraper import scrape_linkedin_profile

logger = logging.getLogger(__name__)

_crawl_semaphore: asyncio.Semaphore | None = None


def _get_crawl_semaphore() -> asyncio.Semaphore:
    global _crawl_semaphore
    if _crawl_semaphore is None:
        from app.services.rate_limits import max_concurrent_browser

        _crawl_semaphore = asyncio.Semaphore(max_concurrent_browser())
    return _crawl_semaphore


# User agent for generic crawling
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


async def _crawl_generic_url(url: str, timeout_ms: int = 15000) -> dict:
    """Crawl a generic URL using Playwright and extract body text."""
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=_USER_AGENT,
                ignore_https_errors=True,
            )
            page = await context.new_page()

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                await page.wait_for_timeout(2000)  # Wait for SPA content
            except PlaywrightTimeout:
                await browser.close()
                return {
                    "url": url,
                    "text": "",
                    "success": False,
                    "error": "Page load timed out",
                }

            page_text = await page.inner_text("body")
            await browser.close()

            if not page_text or len(page_text.strip()) < 50:
                return {
                    "url": url,
                    "text": "",
                    "success": False,
                    "error": "No meaningful content found on page",
                }

            return {
                "url": url,
                "text": page_text[:15000],
                "success": True,
                "error": None,
            }

    except Exception as e:
        logger.exception("Crawl failed for %s", url)
        return {
            "url": url,
            "text": "",
            "success": False,
            "error": f"Crawl failed: {str(e)}",
        }


async def crawl_url(url: str, url_type: str) -> dict:
    """Crawl a single URL and return its text content.

    Args:
        url: The URL to crawl.
        url_type: One of "linkedin", "github", "google_scholar", "website", "other".

    Returns:
        {"url": str, "text": str, "success": bool, "error": str|None}
    """
    async with _get_crawl_semaphore():
        if url_type == "linkedin" or "linkedin.com" in url:
            result = await scrape_linkedin_profile(url)
            return {
                "url": url,
                "text": result.get("raw_text", ""),
                "success": result.get("success", False),
                "error": result.get("error"),
            }
        else:
            return await _crawl_generic_url(url)


async def crawl_urls(
    urls: list[dict],
    on_complete: Callable[[str, dict], None] | None = None,
) -> dict[str, dict]:
    """Crawl multiple URLs concurrently.

    Args:
        urls: List of {"type": str, "url": str} dicts.
        on_complete: Optional callback(url, result) called after each URL completes.

    Returns:
        Dict mapping URL -> crawl result.
    """
    results = {}

    async def _crawl_one(entry: dict) -> None:
        url = entry["url"]
        url_type = entry.get("type", "other")
        result = await crawl_url(url, url_type)
        results[url] = result
        if on_complete:
            on_complete(url, result)

    tasks = [_crawl_one(entry) for entry in urls]
    await asyncio.gather(*tasks, return_exceptions=True)

    return results
