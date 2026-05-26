"""Greenhouse public JSON API scraper.

API docs: https://developers.greenhouse.io/job-board.html
No authentication required.
"""

import html as html_lib
import logging
from datetime import datetime

import httpx

from app.models.companies import Company
from app.scrapers.base import ScrapedJob, html_to_text, parse_salary

logger = logging.getLogger(__name__)

GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"


class GreenhouseScraper:
    source_name = "greenhouse"

    def can_handle(self, company: Company) -> bool:
        return bool(company.greenhouse_slug)

    async def scrape_company(
        self,
        company: Company,
        http_client: httpx.AsyncClient,
        *,
        known_urls: set[str] | None = None,
    ) -> list[ScrapedJob]:
        url = GREENHOUSE_API.format(slug=company.greenhouse_slug)
        logger.info("Fetching Greenhouse jobs for %s (%s)", company.name, url)

        resp = await http_client.get(url, params={"content": "true"})
        resp.raise_for_status()
        data = resp.json()

        from app.services.scrape_progress import progress

        raw_jobs = data.get("jobs", [])
        progress.fetching(len(raw_jobs))

        jobs: list[ScrapedJob] = []
        for entry in raw_jobs:
            jobs.append(self._parse_job(entry, company))
            progress.increment()

        logger.info("Found %d jobs for %s", len(jobs), company.name)
        return jobs

    def _parse_job(self, entry: dict, company: Company) -> ScrapedJob:
        raw_content = entry.get("content", "")
        # Greenhouse may return entity-encoded HTML — decode first
        html_content = html_lib.unescape(raw_content) if raw_content else ""
        plain_text = html_to_text(html_content) if html_content else ""

        # Location
        location = entry.get("location", {}).get("name", "")

        # Remote detection from metadata array
        remote = False
        for meta in entry.get("metadata") or []:
            if meta.get("name") == "Location Type":
                val = str(meta.get("value", "")).lower()
                if "remote" in val:
                    remote = True
                break

        # Salary from description HTML
        salary_min, salary_max = parse_salary(html_content or plain_text)

        # Posted date
        posted_at = None
        raw_date = entry.get("first_published_at") or entry.get("updated_at")
        if raw_date:
            try:
                posted_at = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        return ScrapedJob(
            title=entry.get("title", ""),
            company_name=company.name,
            url=entry.get("absolute_url", ""),
            description=plain_text,
            description_html=html_content or None,
            location=location or None,
            remote=remote,
            salary_min=salary_min,
            salary_max=salary_max,
            posted_at=posted_at,
            application_url=entry.get("absolute_url", ""),
            source="greenhouse",
            metadata={
                "greenhouse_job_id": entry.get("id"),
                "departments": [d.get("name") for d in (entry.get("departments") or [])],
                "offices": [o.get("name") for o in (entry.get("offices") or [])],
                "updated_at": entry.get("updated_at"),
            },
        )
