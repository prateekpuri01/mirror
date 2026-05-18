"""Daily maintenance scheduler.

Runs as a background asyncio task. Checks every hour whether daily
maintenance is overdue (>24h since last run) and triggers:
  1. Liveness URL checks on stale jobs
  2. Garbage collection of old expired jobs
  3. Pipeline processing of unscored jobs
"""

import asyncio
import logging
from datetime import datetime, timezone

import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger(__name__)

REDIS_KEY = "jobboard:last_maintenance"
MAINTENANCE_INTERVAL = 24 * 60 * 60  # 24 hours
CHECK_INTERVAL = 60 * 60  # Check every hour


async def _get_last_maintenance(r: aioredis.Redis) -> datetime | None:
    val = await r.get(REDIS_KEY)
    if val:
        return datetime.fromisoformat(val.decode())
    return None


async def _set_last_maintenance(r: aioredis.Redis) -> None:
    await r.set(REDIS_KEY, datetime.now(timezone.utc).isoformat())


async def _run_maintenance() -> None:
    """Execute all daily maintenance tasks."""
    from app.database import async_session
    from app.services.liveness import check_urls_batch, garbage_collect
    from app.services.pipeline import process_new_jobs

    logger.info("Starting daily maintenance...")

    async with async_session() as session:
        # 1. Process any jobs that missed their per-scrape pipeline trigger.
        # Catches stragglers from failed background tasks, restarts, etc.
        try:
            process_stats = await process_new_jobs(session)
            if process_stats.get("scored", 0) > 0 or process_stats.get("cleaned", 0) > 0:
                logger.info(
                    "Catch-up pipeline: %d cleaned, %d scored, %d skipped, %d errors",
                    process_stats.get("cleaned", 0),
                    process_stats.get("scored", 0),
                    process_stats.get("skipped", 0),
                    process_stats.get("errors", 0),
                )
        except Exception:
            logger.exception("Catch-up pipeline processing failed")

        # 2. Liveness check — HTTP HEAD on jobs not seen recently
        try:
            result = await check_urls_batch(session)
            logger.info(
                "Liveness check: %d checked, %d expired, %d still alive",
                result.get("checked", 0),
                result.get("expired", 0),
                result.get("alive", 0),
            )
        except Exception:
            logger.exception("Liveness check failed")

        # 3. Garbage collect — archive old expired jobs
        try:
            gc_result = await garbage_collect(session)
            logger.info("Garbage collect: %d archived", gc_result.get("archived", 0))
        except Exception:
            logger.exception("Garbage collection failed")

    logger.info("Daily maintenance complete")


async def run_scheduler() -> None:
    """Background loop: check hourly, run maintenance if overdue."""
    # Wait a bit for app startup to complete
    await asyncio.sleep(10)

    try:
        r = aioredis.from_url(settings.redis_url)
    except Exception:
        logger.exception("Failed to connect to Redis for scheduler")
        return

    logger.info("Scheduler started — checking maintenance every %d seconds", CHECK_INTERVAL)

    while True:
        try:
            last_run = await _get_last_maintenance(r)
            now = datetime.now(timezone.utc)

            if last_run is None:
                logger.info("No previous maintenance recorded — running now")
                overdue = True
            else:
                elapsed = (now - last_run).total_seconds()
                overdue = elapsed >= MAINTENANCE_INTERVAL
                if overdue:
                    logger.info("Maintenance overdue (last run %.1f hours ago)", elapsed / 3600)

            if overdue:
                await _run_maintenance()
                await _set_last_maintenance(r)

        except Exception:
            logger.exception("Scheduler iteration failed")

        await asyncio.sleep(CHECK_INTERVAL)


async def get_maintenance_status() -> dict:
    """Return scheduler status for the API."""
    try:
        r = aioredis.from_url(settings.redis_url)
        last_run = await _get_last_maintenance(r)
        await r.aclose()
    except Exception:
        last_run = None

    now = datetime.now(timezone.utc)
    if last_run:
        elapsed = (now - last_run).total_seconds()
        next_run_in = max(0, MAINTENANCE_INTERVAL - elapsed)
    else:
        next_run_in = 0

    return {
        "last_run": last_run.isoformat() if last_run else None,
        "next_run_in_seconds": int(next_run_in),
        "interval_hours": MAINTENANCE_INTERVAL // 3600,
    }
