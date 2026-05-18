"""Ashby job board scraper via public GraphQL API.

Endpoints:
  POST https://jobs.ashbyhq.com/api/non-user-graphql
  - ApiJobBoardWithTeams: list all postings (title, location, team, compensation)
  - ApiJobPosting: full description HTML for a single posting
"""

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from app.models.companies import Company
from app.scrapers.base import ScrapedJob, html_to_text, parse_salary

logger = logging.getLogger(__name__)

ASHBY_GQL = "https://jobs.ashbyhq.com/api/non-user-graphql"

LIST_QUERY = """
query ApiJobBoardWithTeams($organizationHostedJobsPageName: String!) {
  jobBoard: jobBoardWithTeams(
    organizationHostedJobsPageName: $organizationHostedJobsPageName
  ) {
    teams { id name parentTeamId }
    jobPostings {
      id title teamId locationId locationName
      employmentType compensationTierSummary
      secondaryLocations { locationId locationName }
    }
  }
}
"""

DETAIL_QUERY = """
query ApiJobPosting(
  $organizationHostedJobsPageName: String!,
  $jobPostingId: String!
) {
  jobPosting(
    organizationHostedJobsPageName: $organizationHostedJobsPageName,
    jobPostingId: $jobPostingId
  ) {
    id title descriptionHtml departmentName locationName
    employmentType compensationTierSummary publishedDate
  }
}
"""


async def _post_with_429_retry(
    http_client: httpx.AsyncClient,
    payload: dict,
    *,
    label: str,
    max_attempts: int = 4,
) -> httpx.Response:
    """POST to ASHBY_GQL with exponential backoff on 429. Total backoff
    across 4 attempts is 1+2+4+8 = 15s. Was 6 attempts (63s) until a
    single hot-search run with concurrent Ashby-heavy candidates ground
    for 1.5 hours waiting on per-call retry chains. 15s is still enough
    to ride out transient throttling without letting any one call hijack
    the search wall-clock; the orchestration's global wall-clock budget
    handles the worst-case ceiling.
    """
    resp = None
    for attempt in range(max_attempts):
        resp = await http_client.post(ASHBY_GQL, json=payload)
        if resp.status_code != 429:
            return resp
        backoff = 2 ** attempt
        logger.debug("Ashby 429 for %s, backing off %ds (attempt %d/%d)",
                     label, backoff, attempt + 1, max_attempts)
        await asyncio.sleep(backoff)
    # Final failure; let the caller handle the raise_for_status
    return resp


class AshbyScraper:
    source_name = "ashby"

    def can_handle(self, company: Company) -> bool:
        return bool(company.ashby_slug)

    async def scrape_company(
        self, company: Company, http_client: httpx.AsyncClient,
        *, known_urls: set[str] | None = None,
    ) -> list[ScrapedJob]:
        slug = company.ashby_slug
        logger.info("Fetching Ashby jobs for %s (slug=%s)", company.name, slug)

        # Step 1: list all postings (with 429 retry — same as detail fetches)
        resp = await _post_with_429_retry(
            http_client,
            {
                "operationName": "ApiJobBoardWithTeams",
                "variables": {"organizationHostedJobsPageName": slug},
                "query": LIST_QUERY,
            },
            label=f"list/{slug}",
        )
        resp.raise_for_status()
        data = resp.json()

        board = data.get("data", {}).get("jobBoard")
        if not board:
            logger.warning("No Ashby board found for slug %s", slug)
            return []

        teams = {t["id"]: t["name"] for t in (board.get("teams") or [])}
        postings = board.get("jobPostings") or []
        logger.info("Found %d postings for %s, fetching details...", len(postings), company.name)

        # Step 2: fetch details for each posting (with rate limiting)
        from app.services.scrape_progress import progress

        # Split into known (skip detail fetch) and new (need detail fetch)
        new_postings = []
        known_jobs: list[ScrapedJob] = []
        for posting in postings:
            url = f"https://jobs.ashbyhq.com/{slug}/{posting['id']}"
            if known_urls and url in known_urls:
                # Return a minimal ScrapedJob from listing data (no API call)
                location = posting.get("locationName") or ""
                known_jobs.append(ScrapedJob(
                    title=posting.get("title", ""),
                    company_name=company.name,
                    url=url,
                    description="",  # already in DB
                    location=location or None,
                    remote="remote" in location.lower() if location else False,
                    source="ashby",
                    metadata={"ashby_posting_id": posting["id"]},
                ))
            else:
                new_postings.append(posting)

        if known_urls:
            logger.info(
                "Skipping detail fetch for %d known jobs, fetching %d new",
                len(known_jobs), len(new_postings),
            )

        progress.fetching(len(new_postings))

        jobs: list[ScrapedJob] = list(known_jobs)
        for posting in new_postings:
            try:
                job = await self._fetch_detail(
                    http_client, slug, posting, teams, company
                )
                if job:
                    jobs.append(job)
                progress.increment()
            except Exception:
                logger.warning(
                    "Failed to fetch detail for %s at %s",
                    posting.get("title"), company.name,
                    exc_info=True,
                )
            # Rate limit: 0.5s between detail requests
            await asyncio.sleep(0.5)

        logger.info("Scraped %d jobs with details for %s", len(jobs), company.name)
        return jobs

    async def _fetch_detail(
        self,
        http_client: httpx.AsyncClient,
        slug: str,
        posting: dict,
        teams: dict[str, str],
        company: Company,
    ) -> ScrapedJob | None:
        posting_id = posting["id"]

        resp = await _post_with_429_retry(
            http_client,
            {
                "operationName": "ApiJobPosting",
                "variables": {
                    "organizationHostedJobsPageName": slug,
                    "jobPostingId": posting_id,
                },
                "query": DETAIL_QUERY,
            },
            label=f"detail/{slug}/{posting_id}",
        )
        resp.raise_for_status()
        detail = resp.json().get("data", {}).get("jobPosting")

        if not detail:
            return None

        description_html = detail.get("descriptionHtml", "")
        plain_text = html_to_text(description_html) if description_html else ""

        location = posting.get("locationName") or detail.get("locationName") or ""
        remote = "remote" in location.lower() if location else False

        # Salary from compensation summary or description
        comp_summary = posting.get("compensationTierSummary") or ""
        salary_min, salary_max = parse_salary(comp_summary)
        if not salary_min:
            salary_min, salary_max = parse_salary(description_html or plain_text)

        # Posted date
        posted_at = None
        pub_date = detail.get("publishedDate")
        if pub_date:
            try:
                posted_at = datetime.fromisoformat(pub_date).replace(tzinfo=timezone.utc)
            except (ValueError, AttributeError):
                pass

        team_name = teams.get(posting.get("teamId"), "")
        dept_name = detail.get("departmentName") or team_name

        url = f"https://jobs.ashbyhq.com/{slug}/{posting_id}"

        metadata = {
            "ashby_posting_id": posting_id,
            "department": dept_name or None,
            "team": team_name or None,
            "employment_type": posting.get("employmentType"),
        }
        # Preserve raw compensation string so LLM extraction can use it
        if comp_summary:
            metadata["compensation_raw"] = comp_summary

        return ScrapedJob(
            title=detail.get("title", posting.get("title", "")),
            company_name=company.name,
            url=url,
            description=plain_text,
            description_html=description_html or None,
            location=location or None,
            remote=remote,
            salary_min=salary_min,
            salary_max=salary_max,
            posted_at=posted_at,
            application_url=url,
            source="ashby",
            metadata=metadata,
        )
