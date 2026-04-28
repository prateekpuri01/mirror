"""Redis cache for scraped job data.

Avoids re-scraping entire ATS boards when importing or refreshing, and
holds LLM-extracted single-URL job data between a Hot Search preview and
the user confirming an import (keys prefixed with ``direct_extract:``).
"""

import hashlib
import json
import logging
from dataclasses import asdict
from datetime import datetime

import redis.asyncio as aioredis

from app.config import settings
from app.scrapers.base import ScrapedJob

logger = logging.getLogger(__name__)

_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days
_DIRECT_TTL_SECONDS = 24 * 60 * 60  # 1 day — user should import within this window

_pool: aioredis.Redis | None = None


def _get_redis() -> aioredis.Redis:
    global _pool
    if _pool is None:
        _pool = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _pool


def _cache_key(ats: str, slug: str) -> str:
    return f"scrape:{ats}:{slug}"


def _direct_key(url: str) -> str:
    # Hash the URL to keep the key short and filesystem-safe in Redis.
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
    return f"direct_extract:{digest}"


def _serialize_job(sj: ScrapedJob) -> dict:
    d = asdict(sj)
    if d.get("posted_at") and isinstance(d["posted_at"], datetime):
        d["posted_at"] = d["posted_at"].isoformat()
    return d


def _deserialize_job(d: dict) -> ScrapedJob:
    if d.get("posted_at"):
        d["posted_at"] = datetime.fromisoformat(d["posted_at"])
    return ScrapedJob(**d)


async def get_scraped_jobs(ats: str, slug: str) -> list[ScrapedJob] | None:
    """Return cached scraped jobs, or None on cache miss."""
    try:
        r = _get_redis()
        raw = await r.get(_cache_key(ats, slug))
        if raw is None:
            return None
        jobs = [_deserialize_job(d) for d in json.loads(raw)]
        logger.info("Cache hit for %s/%s: %d jobs", ats, slug, len(jobs))
        return jobs
    except Exception:
        logger.warning("Scrape cache read failed for %s/%s", ats, slug, exc_info=True)
        return None


async def set_scraped_jobs(ats: str, slug: str, jobs: list[ScrapedJob]) -> None:
    """Cache scraped jobs with 7-day TTL."""
    try:
        r = _get_redis()
        data = json.dumps([_serialize_job(sj) for sj in jobs])
        await r.set(_cache_key(ats, slug), data, ex=_TTL_SECONDS)
        logger.info("Cached %d jobs for %s/%s", len(jobs), ats, slug)
    except Exception:
        logger.warning("Scrape cache write failed for %s/%s", ats, slug, exc_info=True)


# ---------------------------------------------------------------------------
# Direct-URL extraction cache (one LLM-extracted job per URL)
# ---------------------------------------------------------------------------


async def get_direct_extract(url: str) -> dict | None:
    """Return cached LLM-extracted job data for a URL, or None."""
    try:
        r = _get_redis()
        raw = await r.get(_direct_key(url))
        return json.loads(raw) if raw else None
    except Exception:
        logger.warning("Direct extract read failed for %s", url, exc_info=True)
        return None


async def set_direct_extract(url: str, job_data: dict) -> None:
    """Cache an LLM-extracted job payload keyed by URL."""
    try:
        r = _get_redis()
        await r.set(_direct_key(url), json.dumps(job_data), ex=_DIRECT_TTL_SECONDS)
    except Exception:
        logger.warning("Direct extract write failed for %s", url, exc_info=True)


# ---------------------------------------------------------------------------
# Generic JSON cache for aggregator pulls and other slow lookups. Used by
# the discovery adapters (HN's monthly thread, etc.) so we don't re-fetch
# expensive data every search run.
# ---------------------------------------------------------------------------

_AGGREGATOR_TTL_SECONDS = 24 * 60 * 60  # 1 day


def _aggregator_key(name: str, scope: str) -> str:
    digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:16]
    return f"aggregator:{name}:{digest}"


async def get_aggregator_pull(name: str, scope: str) -> list | None:
    """Return cached aggregator results (list of dicts) or None on miss."""
    try:
        r = _get_redis()
        raw = await r.get(_aggregator_key(name, scope))
        return json.loads(raw) if raw else None
    except Exception:
        logger.warning("Aggregator cache read failed for %s/%s", name, scope[:40], exc_info=True)
        return None


async def set_aggregator_pull(name: str, scope: str, items: list) -> None:
    """Cache aggregator results (list of JSON-serializable dicts) for 24h."""
    try:
        r = _get_redis()
        await r.set(
            _aggregator_key(name, scope), json.dumps(items),
            ex=_AGGREGATOR_TTL_SECONDS,
        )
    except Exception:
        logger.warning("Aggregator cache write failed for %s/%s", name, scope[:40], exc_info=True)


# ---------------------------------------------------------------------------
# Do-not-retry cache for ATS slugs that recently failed to scrape (404,
# repeated 429, network errors, etc.). Avoids burning time on companies
# we know we can't reach.
# ---------------------------------------------------------------------------

_DEAD_SLUG_TTL_SECONDS = 24 * 60 * 60  # 1 day


def _dead_slug_key(ats: str, slug: str) -> str:
    return f"dead_slug:{ats}:{slug}"


async def is_slug_dead(ats: str, slug: str) -> bool:
    """Has this ats/slug pair been marked dead in the last 24h?"""
    try:
        r = _get_redis()
        return bool(await r.exists(_dead_slug_key(ats, slug)))
    except Exception:
        return False


async def mark_slug_dead(ats: str, slug: str, reason: str = "") -> None:
    """Stop trying this ats/slug for the next 24h. `reason` is stored as the
    value for debugging; size is bounded by the TTL."""
    try:
        r = _get_redis()
        await r.set(
            _dead_slug_key(ats, slug), reason[:120],
            ex=_DEAD_SLUG_TTL_SECONDS,
        )
    except Exception:
        logger.warning("Dead-slug write failed for %s/%s", ats, slug, exc_info=True)
