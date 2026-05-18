"""Playwright-based LinkedIn profile scraper."""

import logging
from datetime import datetime, timezone

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

logger = logging.getLogger(__name__)

# Indicators that LinkedIn is blocking access
AUTH_WALL_INDICATORS = [
    "sign in",
    "join now",
    "authwall",
    "login",
    "session_redirect",
]


async def scrape_linkedin_profile(url: str) -> dict:
    """Scrape text content from a public LinkedIn profile.

    Returns:
        {
            "raw_text": str,
            "scraped_at": str (ISO),
            "url": str,
            "success": bool,
            "error": str | None,
        }
    """
    if not url or "linkedin.com" not in url:
        return {
            "raw_text": "",
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "url": url,
            "success": False,
            "error": "Invalid LinkedIn URL",
        }

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                ignore_https_errors=True,
            )
            page = await context.new_page()

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                # Wait for content to settle (SPAs)
                await page.wait_for_timeout(3000)
            except PlaywrightTimeout:
                await browser.close()
                return {
                    "raw_text": "",
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                    "url": url,
                    "success": False,
                    "error": "Page load timed out",
                }

            # Check for auth wall
            current_url = page.url.lower()
            page_text = await page.inner_text("body")

            auth_blocked = any(
                indicator in current_url for indicator in AUTH_WALL_INDICATORS
            )
            if not auth_blocked and len(page_text.strip()) < 200:
                auth_blocked = True

            await browser.close()

            if auth_blocked:
                return {
                    "raw_text": "",
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                    "url": url,
                    "success": False,
                    "error": "LinkedIn requires authentication to view this profile",
                }

            return {
                "raw_text": page_text[:20000],  # Cap text size
                "scraped_at": datetime.now(timezone.utc).isoformat(),
                "url": url,
                "success": True,
                "error": None,
            }

    except Exception as e:
        logger.exception("LinkedIn scrape failed for %s", url)
        return {
            "raw_text": "",
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "url": url,
            "success": False,
            "error": f"Scrape failed: {str(e)}",
        }
