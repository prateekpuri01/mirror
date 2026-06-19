"""Google Scholar profile scraper.

Scrapes the public ``citations?user=<id>`` page to enumerate an author's
COMPLETE publication list — better coverage than Semantic Scholar's
``/author/search`` which often misses papers or returns the wrong
homonym researcher for common names.

Returns sparse metadata per paper (title, authors, venue, year, citation
count). The caller is expected to enrich each entry by fuzzy-matching
Semantic Scholar's ``/paper/search`` to fill in abstract + DOI + arxiv
IDs, since Scholar's profile page only shows a one-line snippet.
"""

import logging
from urllib.parse import parse_qs, urlparse

from playwright.async_api import TimeoutError as PlaywrightTimeout
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)


def _extract_scholar_user_id(url: str) -> str | None:
    """Parse the unique Scholar author ID out of a profile URL.

    https://scholar.google.com/citations?user=XYZ123&hl=en → 'XYZ123'
    Returns None on malformed URLs or non-Scholar hosts.
    """
    try:
        parsed = urlparse(url)
        if "scholar.google" not in (parsed.netloc or "").lower():
            return None
        qs = parse_qs(parsed.query)
        ids = qs.get("user", [])
        if ids and ids[0]:
            return ids[0]
    except Exception:
        return None
    return None


async def scrape_scholar_publications(
    scholar_url: str,
    max_papers: int = 200,
) -> list[dict]:
    """Scrape an author's full publication list from their Scholar profile.

    Strategy:
      1. Open profile in Playwright headless Chromium with a real-browser
         user-agent (Scholar serves a different DOM to bots).
      2. Click "Show more" until all papers are loaded (Scholar paginates
         the publication table — initial load = 20 rows).
      3. Parse each ``tr.gsc_a_tr`` row for title, authors, venue, year,
         citation count, and the paper's Scholar-internal link.

    Returns: list of dicts with keys
        title, authors (list), venue, year, citation_count, scholar_link
    Empty list on scrape failure (caller falls back to name search).
    """
    user_id = _extract_scholar_user_id(scholar_url)
    if not user_id:
        logger.warning(
            "Could not extract user= from Scholar URL: %s — bailing",
            scholar_url,
        )
        return []

    # Ask for 100 papers per page so most authors load in one request.
    profile_url = f"https://scholar.google.com/citations?user={user_id}&hl=en&cstart=0&pagesize=100"

    try:
        async with async_playwright() as pw:
            # Anti-bot bypass: Google Scholar's anti-headless heuristics
            # have gotten more aggressive — the default headless=True browser
            # gets served a stripped/captcha page that never renders the
            # gsc_a_tr citation rows. New-mode headless + disabling the
            # AutomationControlled blink feature is the cheapest robust fix.
            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-features=IsolateOrigins,site-per-process",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                ],
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 900},
                locale="en-US",
                timezone_id="America/Los_Angeles",
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": (
                        "text/html,application/xhtml+xml,application/xml;q=0.9,"
                        "image/avif,image/webp,*/*;q=0.8"
                    ),
                },
                ignore_https_errors=True,
            )
            page = await context.new_page()
            # Mask navigator.webdriver — the most commonly-checked
            # automation signal. Done via init script so it runs before
            # any Scholar JS executes.
            await page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )

            try:
                await page.goto(
                    profile_url,
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                # Let the publication table render. Increased from 8s -> 20s
                # because the anti-detection landing sometimes adds 5-10s
                # to the initial table render.
                await page.wait_for_selector("tr.gsc_a_tr", timeout=20000)
            except PlaywrightTimeout:
                await browser.close()
                logger.warning(
                    "Scholar page load / table render timed out: %s",
                    profile_url,
                )
                return []

            # Click "Show more" until all papers loaded or we hit the cap.
            # Safety cap iterations so a stuck button can't hang us.
            for _ in range(20):
                rows = await page.query_selector_all("tr.gsc_a_tr")
                if len(rows) >= max_papers:
                    break
                show_more = await page.query_selector("button#gsc_bpf_more")
                if not show_more:
                    break
                # Scholar disables the button when there's nothing more
                disabled = await show_more.get_attribute("disabled")
                if disabled is not None:
                    break
                try:
                    await show_more.click()
                    await page.wait_for_timeout(800)
                except Exception:
                    break

            rows = await page.query_selector_all("tr.gsc_a_tr")
            papers: list[dict] = []
            for row in rows[:max_papers]:
                try:
                    title_el = await row.query_selector("td.gsc_a_t a.gsc_a_at")
                    if not title_el:
                        continue
                    title = (await title_el.inner_text()).strip()
                    if not title:
                        continue
                    paper_link_attr = await title_el.get_attribute("href")
                    scholar_link = (
                        f"https://scholar.google.com{paper_link_attr}" if paper_link_attr else None
                    )

                    # Scholar puts authors + venue in two .gs_gray divs
                    # under the title cell. Order: [0]=authors, [1]=venue.
                    gray_els = await row.query_selector_all("td.gsc_a_t div.gs_gray")
                    authors_text = ""
                    venue_text = ""
                    if len(gray_els) >= 1:
                        authors_text = (await gray_els[0].inner_text()).strip()
                    if len(gray_els) >= 2:
                        venue_text = (await gray_els[1].inner_text()).strip()

                    year_el = await row.query_selector("td.gsc_a_y span.gsc_a_h")
                    year_text = (await year_el.inner_text()).strip() if year_el else ""

                    cite_el = await row.query_selector("td.gsc_a_c a.gsc_a_ac")
                    cite_text = (await cite_el.inner_text()).strip() if cite_el else ""

                    authors = [
                        a.strip().rstrip("…").strip() for a in authors_text.split(",") if a.strip()
                    ]
                    year: int | None = None
                    if year_text.isdigit():
                        year = int(year_text)
                    citation_count: int = 0
                    if cite_text.isdigit():
                        citation_count = int(cite_text)

                    papers.append(
                        {
                            "title": title,
                            "authors": authors,
                            "venue": venue_text,
                            "year": year,
                            "citation_count": citation_count,
                            "scholar_link": scholar_link,
                        }
                    )
                except Exception:
                    logger.warning(
                        "Failed to parse a Scholar row — skipping",
                        exc_info=True,
                    )
                    continue

            await browser.close()
            logger.info(
                "Scraped %d papers from Scholar profile (user=%s)",
                len(papers),
                user_id,
            )
            return papers
    except Exception:
        logger.exception("Scholar scrape failed for %s", scholar_url)
        return []
