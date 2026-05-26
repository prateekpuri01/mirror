"""Aggregator-style discovery adapters used by Hot Jobs Search.

These adapters pull from public, no-auth job aggregator APIs (and the
existing HN Who Is Hiring scraper) and emit ``AggregatorEntry`` records.
The hot-search pipeline turns each entry into a Candidate by extracting an
ATS slug from its URL when possible (so we get a comprehensive per-company
scrape from Greenhouse/Lever/Ashby) and falling back to direct-URL import
otherwise.

This is deliberately separate from ``ScraperProtocol`` (which is per-
company, slug-keyed). Aggregators don't fit that shape — they're broad
discovery feeds, not targeted boards.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.scrapers.base import html_to_text
from app.scrapers.hn_who_is_hiring import scrape_hn_who_is_hiring

logger = logging.getLogger(__name__)


@dataclass
class AggregatorEntry:
    """One job surfaced by a discovery adapter.

    Fields are intentionally lean — just what the harvester needs to
    decide between an ATS slug extraction (URL → comprehensive scrape) and
    the direct-URL fallback path (single-job preview).
    """

    company_name: str
    job_url: str
    title: str
    location: str | None = None
    description: str | None = None  # Plain text; used by direct-URL fallback
    salary_min: int | None = None
    salary_max: int | None = None
    remote: bool = False
    source: str = ""


class DiscoveryAdapter(Protocol):
    source_name: str

    async def fetch_entries(
        self,
        http_client: httpx.AsyncClient,
        guidance: str,
        locations: list[str],
        min_salary: int | None,
    ) -> list[AggregatorEntry]: ...


# ---------------------------------------------------------------------------
# HN Who Is Hiring adapter
# ---------------------------------------------------------------------------


class HNAdapter:
    """Wrap the existing scrape_hn_who_is_hiring() into an aggregator feed.

    HN URLs are notoriously ATS-heavy (founders post their Greenhouse/Lever
    links), so the slug-harvest yield from this source is high.
    """

    source_name = "hn_who_is_hiring"

    async def fetch_entries(
        self,
        http_client: httpx.AsyncClient,
        guidance: str,
        locations: list[str],
        min_salary: int | None,
    ) -> list[AggregatorEntry]:
        # Build include patterns from guidance words; fall back to the scraper
        # default include list (AI/ML focused) when guidance is empty.
        include = None
        if guidance:
            include = [w for w in guidance.split() if len(w) >= 3]
            if not include:
                include = None

        # 24h Redis cache keyed by guidance — the HN "Who is hiring" thread
        # only changes once a month, so re-fetching every run is wasteful.
        # The cache stores already-shaped AggregatorEntry dicts so we skip
        # the scraper + URL-extraction work entirely on a hit.
        from app.services.scrape_cache import (
            get_aggregator_pull,
            set_aggregator_pull,
        )

        cache_scope = (guidance or "").lower().strip()
        cached = await get_aggregator_pull(self.source_name, cache_scope)
        if cached is not None:
            logger.info("HN adapter cache HIT (%d entries)", len(cached))
            return [AggregatorEntry(**d) for d in cached]

        try:
            jobs = await scrape_hn_who_is_hiring(
                http_client,
                include_patterns=include,
            )
        except Exception:
            logger.warning("HN adapter fetch failed", exc_info=True)
            return []

        # The HN scraper sets `url` to the HN comment permalink (good for the
        # "view original" UI link) and stashes the embedded ATS/career URL in
        # `application_url`. For slug-harvesting we want the latter — that's
        # where the boards.greenhouse.io / jobs.lever.co URLs live.
        entries: list[AggregatorEntry] = []
        for j in jobs:
            harvest_url = j.application_url or j.url
            if not harvest_url:
                continue
            entries.append(
                AggregatorEntry(
                    company_name=j.company_name,
                    job_url=harvest_url,
                    title=j.title,
                    location=j.location,
                    description=j.description,
                    salary_min=j.salary_min,
                    salary_max=j.salary_max,
                    remote=j.remote,
                    source=self.source_name,
                )
            )

        # Persist for the next run. asdict() handles the dataclass cleanly;
        # AggregatorEntry has only primitive fields so JSON serializes fine.
        from dataclasses import asdict

        await set_aggregator_pull(
            self.source_name,
            cache_scope,
            [asdict(e) for e in entries],
        )
        return entries


# ---------------------------------------------------------------------------
# Remotive adapter
# ---------------------------------------------------------------------------


class RemotiveAdapter:
    """Remote-first job board. Free public JSON API at remotive.com.

    Useful when the user's location filter includes "Remote" or is empty.
    Skipped entirely otherwise to avoid noise.
    """

    source_name = "remotive"
    ENDPOINT = "https://remotive.com/api/remote-jobs"

    async def fetch_entries(
        self,
        http_client: httpx.AsyncClient,
        guidance: str,
        locations: list[str],
        min_salary: int | None,
    ) -> list[AggregatorEntry]:
        if locations and not any("remote" in l.lower() for l in locations):
            # User wants a specific city, not remote — skip the source.
            return []

        params = {"limit": 50}
        if guidance:
            params["search"] = guidance[:80]
        try:
            resp = await http_client.get(self.ENDPOINT, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            logger.warning("Remotive adapter fetch failed", exc_info=True)
            return []

        entries: list[AggregatorEntry] = []
        for j in data.get("jobs", []):
            url = j.get("url")
            if not url:
                continue
            # Remotive's `salary` is a free-text field; we don't try to parse
            # it here — the LLM verifier downstream will read the JD.
            entries.append(
                AggregatorEntry(
                    company_name=j.get("company_name", "Unknown"),
                    job_url=url,
                    title=j.get("title", ""),
                    location=j.get("candidate_required_location") or "Remote",
                    description=html_to_text(j.get("description", "") or "")[:4000],
                    remote=True,
                    source=self.source_name,
                )
            )
        logger.info("Remotive adapter: %d entries", len(entries))
        return entries


# ---------------------------------------------------------------------------
# The Muse adapter
# ---------------------------------------------------------------------------


class TheMuseAdapter:
    """The Muse public jobs API — free, no auth, supports server-side
    location filtering. Paginated; we cap at 3 pages per query.
    """

    source_name = "themuse"
    ENDPOINT = "https://www.themuse.com/api/public/jobs"
    MAX_PAGES = 3

    async def fetch_entries(
        self,
        http_client: httpx.AsyncClient,
        guidance: str,
        locations: list[str],
        min_salary: int | None,
    ) -> list[AggregatorEntry]:
        entries: list[AggregatorEntry] = []
        # The Muse expects either a city name or "Flexible / Remote". We pass
        # every user-provided location as a separate `location` param if the
        # API supports repeated keys; in practice it accepts a single value
        # so we iterate.
        loc_iter = locations or [""]
        for loc in loc_iter:
            page = 1
            while page <= self.MAX_PAGES:
                params: dict[str, object] = {"page": page}
                if loc:
                    params["location"] = loc
                try:
                    resp = await http_client.get(self.ENDPOINT, params=params, timeout=15)
                    resp.raise_for_status()
                    data = resp.json()
                except Exception:
                    logger.warning(
                        "TheMuse adapter fetch failed (loc=%s page=%d)",
                        loc,
                        page,
                        exc_info=True,
                    )
                    break

                results = data.get("results") or []
                if not results:
                    break

                for j in results:
                    url = (j.get("refs") or {}).get("landing_page")
                    if not url:
                        continue
                    company = (j.get("company") or {}).get("name", "Unknown")
                    locs = j.get("locations") or []
                    # Muse jobs are commonly tagged with multiple locations
                    # (e.g. ["San Francisco, CA", "Flexible / Remote"]) but
                    # the API's response only lists ONE per job — usually
                    # "Flexible / Remote" — even when the location filter
                    # matched on a city. To stop the downstream cheap
                    # filter from auto-rejecting these as "remote-only", we
                    # prepend the requested filter location to the location
                    # string when the API returned only Flexible/Remote.
                    api_locs = [
                        l.get("name") for l in locs if isinstance(l, dict) and l.get("name")
                    ]
                    location_str = ", ".join(api_locs) if api_locs else None
                    if loc and location_str and "flexible" in location_str.lower():
                        # Trust the server-side filter: include the requested
                        # location so substring matching can still pass.
                        location_str = f"{loc}; {location_str}"
                    elif loc and not location_str:
                        location_str = loc
                    entries.append(
                        AggregatorEntry(
                            company_name=company,
                            job_url=url,
                            title=j.get("name", ""),
                            location=location_str,
                            description=html_to_text(j.get("contents", "") or "")[:4000],
                            source=self.source_name,
                        )
                    )
                page_count = data.get("page_count", 0)
                if page >= page_count:
                    break
                page += 1
        logger.info("TheMuse adapter: %d entries", len(entries))
        return entries


# ---------------------------------------------------------------------------
# Arbeitnow adapter
# ---------------------------------------------------------------------------


class ArbeitnowAdapter:
    """Arbeitnow free job board API — no auth, no server-side filter.

    Feed is small (~100 jobs/page); we filter client-side. EU-leaning, so
    complementary to The Muse's US bias.
    """

    source_name = "arbeitnow"
    ENDPOINT = "https://www.arbeitnow.com/api/job-board-api"

    async def fetch_entries(
        self,
        http_client: httpx.AsyncClient,
        guidance: str,
        locations: list[str],
        min_salary: int | None,
    ) -> list[AggregatorEntry]:
        try:
            resp = await http_client.get(self.ENDPOINT, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            logger.warning("Arbeitnow adapter fetch failed", exc_info=True)
            return []

        entries: list[AggregatorEntry] = []
        for j in data.get("data", []):
            url = j.get("url")
            if not url:
                continue
            entries.append(
                AggregatorEntry(
                    company_name=j.get("company_name", "Unknown"),
                    job_url=url,
                    title=j.get("title", ""),
                    location=j.get("location"),
                    description=(j.get("description") or "")[:4000],
                    remote=bool(j.get("remote", False)),
                    source=self.source_name,
                )
            )
        logger.info("Arbeitnow adapter: %d entries", len(entries))
        return entries


# ---------------------------------------------------------------------------
# RemoteOK adapter
# ---------------------------------------------------------------------------


class RemoteOKAdapter:
    """RemoteOK API — free, JSON, no auth. Remote-only roles.

    Returns up to ~100 jobs per call, partly overlapping with Remotive but
    with a different curation. The first array element is metadata, not a
    job, so we skip it.
    """

    source_name = "remoteok"
    ENDPOINT = "https://remoteok.com/api"

    async def fetch_entries(
        self,
        http_client: httpx.AsyncClient,
        guidance: str,
        locations: list[str],
        min_salary: int | None,
    ) -> list[AggregatorEntry]:
        # Like Remotive, only relevant when "Remote" is in the user's
        # locations (or no location filter). Skip otherwise to avoid noise.
        if locations and not any("remote" in l.lower() for l in locations):
            return []

        # Skip the `tags` query param — RemoteOK's tag taxonomy is narrow
        # (engineer, design, marketing, …) and most guidance words ("machine",
        # "learning", "ml") aren't in it, returning 0 results. Pull the full
        # feed (~100 jobs) and let downstream slug-harvest + verifier do the
        # filtering.
        try:
            resp = await http_client.get(
                self.ENDPOINT,
                headers={"User-Agent": "jobboard-mirror/1.0"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            logger.warning("RemoteOK adapter fetch failed", exc_info=True)
            return []

        if not isinstance(data, list):
            return []

        entries: list[AggregatorEntry] = []
        for j in data:
            if not isinstance(j, dict):
                continue
            # Skip the metadata entry that always sits at index 0
            if not (j.get("position") and j.get("company")):
                continue
            url = j.get("apply_url") or j.get("url")
            if not url:
                continue
            entries.append(
                AggregatorEntry(
                    company_name=j.get("company", "Unknown"),
                    job_url=url,
                    title=j.get("position", ""),
                    location=j.get("location") or "Remote",
                    description=html_to_text(j.get("description", "") or "")[:4000],
                    salary_min=j.get("salary_min"),
                    salary_max=j.get("salary_max"),
                    remote=True,
                    source=self.source_name,
                )
            )
        logger.info("RemoteOK adapter: %d entries", len(entries))
        return entries


# ---------------------------------------------------------------------------
# Jobicy adapter
# ---------------------------------------------------------------------------


class JobicyAdapter:
    """Jobicy API — free, JSON, no auth. Remote-leaning multi-industry pool.

    Supports `industry`, `tag`, and `count` query params. We pass a count
    cap and (optionally) industry derived from guidance keywords.
    """

    source_name = "jobicy"
    ENDPOINT = "https://jobicy.com/api/v2/remote-jobs"

    async def fetch_entries(
        self,
        http_client: httpx.AsyncClient,
        guidance: str,
        locations: list[str],
        min_salary: int | None,
    ) -> list[AggregatorEntry]:
        if locations and not any("remote" in l.lower() for l in locations):
            return []

        # Don't pass `tag` — Jobicy's tag taxonomy is narrow and multi-word
        # guidance ("machine learning engineer") almost always misses. Just
        # pull a larger count and filter downstream.
        params: dict[str, str] = {"count": "50"}
        try:
            resp = await http_client.get(self.ENDPOINT, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            logger.warning("Jobicy adapter fetch failed", exc_info=True)
            return []

        entries: list[AggregatorEntry] = []
        for j in data.get("jobs", []):
            url = j.get("url")
            if not url:
                continue
            entries.append(
                AggregatorEntry(
                    company_name=j.get("companyName", "Unknown"),
                    job_url=url,
                    title=j.get("jobTitle", ""),
                    location=j.get("jobGeo") or "Remote",
                    description=html_to_text(j.get("jobDescription", "") or "")[:4000],
                    remote=True,
                    source=self.source_name,
                )
            )
        logger.info("Jobicy adapter: %d entries", len(entries))
        return entries


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

DISCOVERY_ADAPTERS: list[DiscoveryAdapter] = [
    HNAdapter(),
    RemotiveAdapter(),
    TheMuseAdapter(),
    ArbeitnowAdapter(),
    RemoteOKAdapter(),
    JobicyAdapter(),
]


# Re-export for callers that just want the entry type
__all__ = [
    "AggregatorEntry",
    "DiscoveryAdapter",
    "HNAdapter",
    "RemotiveAdapter",
    "TheMuseAdapter",
    "ArbeitnowAdapter",
    "RemoteOKAdapter",
    "JobicyAdapter",
    "DISCOVERY_ADAPTERS",
]
