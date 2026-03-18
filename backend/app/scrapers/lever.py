"""Lever public postings API scraper.

API: https://api.lever.co/v0/postings/{company}?mode=json
No authentication required. Returns all active postings with descriptions.
"""

import logging
from datetime import datetime, timezone

import httpx

from app.models.companies import Company
from app.scrapers.base import ScrapedJob, html_to_text, parse_salary

logger = logging.getLogger(__name__)

LEVER_API = "https://api.lever.co/v0/postings/{slug}"


class LeverScraper:
    source_name = "lever"

    def can_handle(self, company: Company) -> bool:
        return bool(company.lever_slug)

    async def scrape_company(
        self, company: Company, http_client: httpx.AsyncClient,
        *, known_urls: set[str] | None = None,
    ) -> list[ScrapedJob]:
        url = LEVER_API.format(slug=company.lever_slug)
        logger.info("Fetching Lever jobs for %s (%s)", company.name, url)

        resp = await http_client.get(url, params={"mode": "json"})
        resp.raise_for_status()
        data = resp.json()

        # Lever returns a flat array of postings
        if not isinstance(data, list):
            logger.warning("Unexpected Lever response for %s: not a list", company.name)
            return []

        from app.services.scrape_progress import progress
        progress.fetching(len(data))

        jobs: list[ScrapedJob] = []
        for entry in data:
            jobs.append(self._parse_job(entry, company))
            progress.increment()

        logger.info("Found %d jobs for %s", len(jobs), company.name)
        return jobs

    def _parse_job(self, entry: dict, company: Company) -> ScrapedJob:
        # Lever provides both plain text and HTML descriptions
        description_html = entry.get("description", "")
        description_plain = entry.get("descriptionPlain", "")

        # Additional info (often contains requirements, salary, etc.)
        additional_html = entry.get("additional", "")
        additional_plain = entry.get("additionalPlain", "")

        # Combine description + additional
        full_html = description_html
        if additional_html:
            full_html += "\n" + additional_html

        full_plain = description_plain
        if additional_plain:
            full_plain += "\n" + additional_plain

        # If plain text is empty, fall back to html_to_text
        if not full_plain.strip() and full_html:
            full_plain = html_to_text(full_html)

        # Parse salary from the combined text
        salary_min, salary_max = parse_salary(full_html or full_plain)

        # Categories
        categories = entry.get("categories") or {}
        location = categories.get("location", "")
        department = categories.get("department", "")
        team = categories.get("team", "")
        commitment = categories.get("commitment", "")

        # Remote detection
        remote = False
        loc_lower = (location or "").lower()
        if "remote" in loc_lower or "anywhere" in loc_lower:
            remote = True

        # Posted date (Lever uses millisecond timestamps)
        posted_at = None
        created_ms = entry.get("createdAt")
        if created_ms:
            try:
                posted_at = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc)
            except (ValueError, OSError):
                pass

        hosted_url = entry.get("hostedUrl", "")
        apply_url = entry.get("applyUrl", hosted_url)

        return ScrapedJob(
            title=entry.get("text", ""),
            company_name=company.name,
            url=hosted_url,
            description=full_plain,
            description_html=full_html or None,
            location=location or None,
            remote=remote,
            salary_min=salary_min,
            salary_max=salary_max,
            posted_at=posted_at,
            application_url=apply_url or hosted_url,
            source="lever",
            metadata={
                "lever_id": entry.get("id"),
                "department": department or None,
                "team": team or None,
                "commitment": commitment or None,
            },
        )
