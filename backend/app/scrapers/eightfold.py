"""Eightfold AI (PCSX) job board scraper.

Used by companies like NVIDIA that run their careers site on Eightfold's platform.

The eightfold_slug stores the domain (e.g. "nvidia.com") optionally followed by
query-string filters: "nvidia.com?filter_job_category=research".

Endpoints:
  GET https://{host}/api/pcsx/search?domain={domain}&start=0&sort_by=timestamp
  GET https://{host}/api/pcsx/position_details?position_id={id}&domain={domain}&hl=en
"""

import asyncio
import logging
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import httpx

from app.models.companies import Company
from app.scrapers.base import ScrapedJob, html_to_text, parse_salary

logger = logging.getLogger(__name__)

# Browser-like headers required by Eightfold's PCSX API
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

PAGE_SIZE = 10  # Eightfold API always returns 10 per page


def parse_eightfold_slug(slug: str) -> tuple[str, dict[str, str]]:
    """Parse 'nvidia.com?filter_job_category=research' into domain + filter params."""
    if "?" in slug:
        domain, qs = slug.split("?", 1)
        filters = {k: v[0] for k, v in parse_qs(qs).items()}
        return domain, filters
    return slug, {}


class EightfoldScraper:
    source_name = "eightfold"

    def can_handle(self, company: Company) -> bool:
        return bool(company.eightfold_slug)

    def _api_base(self, company: Company, domain: str) -> str:
        """Derive the API base URL from careers_url or the domain."""
        if company.careers_url:
            parsed = urlparse(company.careers_url)
            return f"{parsed.scheme}://{parsed.netloc}"
        return f"https://jobs.{domain}"

    async def _init_session(self, http_client: httpx.AsyncClient, api_base: str) -> None:
        """Visit careers page to obtain session cookies required by the PCSX API."""
        try:
            resp = await http_client.get(
                f"{api_base}/careers",
                headers=_HEADERS,
                follow_redirects=True,
            )
            resp.raise_for_status()
            logger.debug(
                "Eightfold session initialized, cookies: %s", list(http_client.cookies.keys())
            )
        except Exception:
            logger.warning("Failed to init Eightfold session at %s", api_base, exc_info=True)

    async def scrape_company(
        self,
        company: Company,
        http_client: httpx.AsyncClient,
        *,
        known_urls: set[str] | None = None,
    ) -> list[ScrapedJob]:
        domain, extra_filters = parse_eightfold_slug(company.eightfold_slug)
        api_base = self._api_base(company, domain)
        logger.info(
            "Fetching Eightfold jobs for %s (domain=%s, base=%s, filters=%s)",
            company.name,
            domain,
            api_base,
            extra_filters or "none",
        )

        # Initialize session (cookies)
        await self._init_session(http_client, api_base)

        # Step 1: paginate through search results
        all_positions = []
        start = 0
        total = None

        while total is None or start < total:
            params: dict[str, str | int] = {
                "domain": domain,
                "query": "",
                "location": "",
                "start": start,
                "sort_by": "timestamp",
                **extra_filters,
            }
            resp = await http_client.get(
                f"{api_base}/api/pcsx/search",
                params=params,
                headers={**_HEADERS, "Referer": f"{api_base}/careers"},
            )
            resp.raise_for_status()
            body = resp.json()
            data = body.get("data") or body  # handle both nested and flat responses

            if total is None:
                total = data.get("count", 0)
                logger.info("Found %d total positions for %s", total, company.name)
                if total == 0:
                    return []

            positions = data.get("positions", [])
            if not positions:
                break

            all_positions.extend(positions)
            start += len(positions)

        logger.info(
            "Collected %d positions for %s, fetching details...", len(all_positions), company.name
        )

        # Step 2: split into known (skip detail fetch) and new
        from app.services.scrape_progress import progress

        new_positions = []
        known_jobs: list[ScrapedJob] = []

        for pos in all_positions:
            url = self._position_url(api_base, pos)

            if known_urls and url in known_urls:
                location = ", ".join(pos.get("locations") or []) or None
                known_jobs.append(
                    ScrapedJob(
                        title=pos.get("name", ""),
                        company_name=company.name,
                        url=url,
                        description="",  # already in DB
                        location=location,
                        remote=pos.get("workLocationOption", "").lower()
                        in ("remote", "remote_local"),
                        source="eightfold",
                        metadata={"eightfold_position_id": pos["id"]},
                    )
                )
            else:
                new_positions.append(pos)

        if known_urls:
            logger.info(
                "Skipping detail fetch for %d known jobs, fetching %d new",
                len(known_jobs),
                len(new_positions),
            )

        progress.fetching(len(new_positions))

        # Step 3: fetch details for new positions
        jobs: list[ScrapedJob] = list(known_jobs)
        for pos in new_positions:
            try:
                job = await self._fetch_detail(http_client, api_base, domain, pos, company)
                if job:
                    jobs.append(job)
                progress.increment()
            except Exception:
                logger.warning(
                    "Failed to fetch detail for %s at %s",
                    pos.get("name"),
                    company.name,
                    exc_info=True,
                )
            # Rate limit: 0.3s between detail requests
            await asyncio.sleep(0.3)

        logger.info("Scraped %d jobs with details for %s", len(jobs), company.name)
        return jobs

    @staticmethod
    def _position_url(api_base: str, pos: dict) -> str:
        """Build the canonical public URL for a position."""
        position_url = pos.get("positionUrl", f"/careers/job/{pos['id']}")
        return f"{api_base}{position_url}"

    async def _fetch_detail(
        self,
        http_client: httpx.AsyncClient,
        api_base: str,
        domain: str,
        pos: dict,
        company: Company,
    ) -> ScrapedJob | None:
        position_id = pos["id"]

        # Retry with exponential backoff on 429
        resp = None
        for attempt in range(4):
            resp = await http_client.get(
                f"{api_base}/api/pcsx/position_details",
                params={
                    "position_id": position_id,
                    "domain": domain,
                    "hl": "en",
                },
                headers={**_HEADERS, "Referer": f"{api_base}/careers"},
            )
            if resp.status_code != 429:
                break
            backoff = 2**attempt
            logger.debug("429 for position %s, backing off %ds", position_id, backoff)
            await asyncio.sleep(backoff)

        resp.raise_for_status()
        body = resp.json()
        detail = body.get("data") or body
        if not detail or not detail.get("jobDescription"):
            return None

        description_html = detail.get("jobDescription", "")
        plain_text = html_to_text(description_html) if description_html else ""

        # Location from listing (array) or detail (single string)
        locations = pos.get("locations") or []
        location = ", ".join(locations) if locations else detail.get("location") or None

        # Remote detection
        work_option = pos.get("workLocationOption", "").lower()
        remote = work_option in ("remote", "remote_local")

        # Salary from description
        salary_min, salary_max = parse_salary(description_html or plain_text)

        # Posted date from Unix timestamp
        posted_at = None
        posted_ts = pos.get("postedTs")
        if posted_ts:
            try:
                posted_at = datetime.fromtimestamp(posted_ts, tz=UTC)
            except (ValueError, OSError):
                pass

        # URL
        url = self._position_url(api_base, pos)

        metadata = {
            "eightfold_position_id": position_id,
            "display_job_id": pos.get("displayJobId") or detail.get("displayJobId"),
            "department": pos.get("department"),
            "work_location_option": pos.get("workLocationOption"),
        }

        # Extra fields from detail if available
        for key in ("efcustomTextJobFmailyGroup", "efcustomTextTimeType"):
            val = detail.get(key)
            if val:
                metadata[key] = val

        return ScrapedJob(
            title=detail.get("name") or pos.get("name", ""),
            company_name=company.name,
            url=url,
            description=plain_text,
            description_html=description_html or None,
            location=location,
            remote=remote,
            salary_min=salary_min,
            salary_max=salary_max,
            posted_at=posted_at,
            application_url=url,
            source="eightfold",
            metadata=metadata,
        )
