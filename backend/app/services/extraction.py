"""Extensible job field extraction pipeline.

One LLM call per job extracts all fields. Field processors apply results.
To add a new field: extend the prompt schema, write a processor, register it.
"""

import asyncio
import json
import logging
import time
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import EXTRACTION_MODEL, get_openai_client
from app.models.jobs import Job
from app.models.locations import JobLocation, Location

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Extraction prompt (extend the JSON schema here to add new fields)
# ---------------------------------------------------------------------------

EXTRACTION_SYSTEM = "You extract structured data from job postings. Return valid JSON only."

EXTRACTION_PROMPT = """\
Extract structured fields from this job posting.

Job Title: {title}
Company: {company}
Listed Location: {location}
{ats_metadata}Description:
{description}

Return this exact JSON structure:
{{
  "salary_min": <int annual USD or null>,
  "salary_max": <int annual USD or null>,
  "salary_confidence": "high"|"medium"|"low"|"none",
  "work_model": "remote"|"hybrid"|"onsite",
  "locations": [
    {{"city": "...", "state": "CA"|null, "country": "US"}}
  ]
}}

Rules:

SALARY:
- Convert to annual USD (hourly x2080, monthly x12). null if not found.
- salary_confidence: "high" if explicit number, "medium" if range/estimate, "low" if vague, "none" if absent.

WORK MODEL (pick exactly one):
- "remote": Fully remote, OR remote is an option for the role ("remote possible for select \
candidates", "open to remote", "remote-friendly", etc.)
- "hybrid": Requires some on-site presence. Indicators: "hybrid", "X days in office", \
"requires Y% on-site", "flexible with some in-office", etc.
- "onsite": Fully in-person with no remote/hybrid language.
- When in doubt between remote and hybrid, prefer "hybrid". When in doubt between hybrid \
and onsite, prefer "onsite".

LOCATIONS:
- ALWAYS extract physical office/city locations, even for remote or hybrid jobs.
  Remote and hybrid jobs often list the cities where offices are located or where the team \
is based — extract those.
- Extract from BOTH the listed location field AND the description text.
- "City, ST" format for US (2-letter state code). 2-letter ISO country code for non-US.
- Multi-city postings → multiple location objects.
- If the ONLY location info is "Remote" with no physical city mentioned, return an empty array.
- Do NOT include a location object just to represent "Remote" — that is captured by work_model.
- Do NOT fabricate locations. If unsure, use an empty array.

Return ONLY the JSON object, no markdown fences or extra text."""

BROWSER_SALARY_SYSTEM = (
    "You extract salary information from job posting page text. Return valid JSON only."
)

BROWSER_SALARY_PROMPT = """\
The following is the full visible text from a job posting page. Extract the salary/compensation information.

Job Title: {title}
Company: {company}
URL: {url}

Page text:
{page_text}

Return this exact JSON:
{{
  "salary_min": <int annual USD or null>,
  "salary_max": <int annual USD or null>,
  "salary_confidence": "high"|"medium"|"low"|"none"
}}

Rules:
- Convert to annual USD (hourly x2080, monthly x12). null if not found.
- If only one number is given, use it for both min and max.
- "high" if explicit number, "medium" if range/estimate, "low" if vague, "none" if absent.
- Return ONLY the JSON object, no markdown fences."""


LOCATION_MATCH_SYSTEM = "You match location strings to a canonical list. Return valid JSON only."

LOCATION_MATCH_PROMPT = """\
Match these extracted locations to existing canonical locations, or mark them as new.

Extracted locations to resolve:
{unmatched}

Existing canonical locations:
{candidates}

For each extracted location, return a JSON array of objects:
[
  {{"extracted": "San Francisco, CA, US", "match": "San Francisco, CA" or null}}
]

"match" should be the exact display_name from the canonical list if it's the same place,
or null if no match exists and a new location should be created.
Return ONLY the JSON array."""


# ---------------------------------------------------------------------------
# Field processor protocol + implementations
# ---------------------------------------------------------------------------


class FieldProcessor(Protocol):
    def should_process(self, job: Job) -> bool: ...
    async def apply(self, job: Job, extracted: dict, context: dict) -> None: ...


class SalaryProcessor:
    """Extracts salary_min/max from LLM output into job columns.

    Sets salary_min = -1 as sentinel for "parsed but no salary found",
    so we can distinguish from "never parsed" (salary_min IS NULL).

    If the initial extraction finds no salary, queues the job for a
    browser-based fallback pass (Playwright + LLM on the rendered page).
    """

    def should_process(self, job: Job) -> bool:
        return job.salary_min is None

    async def apply(self, job: Job, extracted: dict, context: dict) -> None:
        confidence = extracted.get("salary_confidence", "none")
        meta = dict(job.extra_metadata or {})
        meta["salary_source"] = "extraction"
        meta["salary_confidence"] = confidence

        if confidence == "none" or extracted.get("salary_min") is None:
            # Queue for browser fallback pass
            context.setdefault("salary_fallback_jobs", []).append(job)
            job.salary_min = -1
            job.extra_metadata = meta
            return

        job.salary_min = extracted["salary_min"]
        job.salary_max = extracted.get("salary_max")
        job.extra_metadata = meta


class LocationProcessor:
    """Buffers extracted locations for bulk resolution phase."""

    def should_process(self, job: Job) -> bool:
        return not (job.extra_metadata or {}).get("extracted")

    async def apply(self, job: Job, extracted: dict, context: dict) -> None:
        locs = extracted.get("locations", [])
        if not locs:
            return
        # Buffer in context for bulk processing later
        context.setdefault("location_buffer", {})[str(job.id)] = locs


class WorkModelProcessor:
    """Sets work_model (remote/hybrid/onsite) and syncs the legacy remote boolean."""

    def should_process(self, job: Job) -> bool:
        return job.work_model is None

    async def apply(self, job: Job, extracted: dict, context: dict) -> None:
        wm = extracted.get("work_model")
        if wm not in ("remote", "hybrid", "onsite"):
            return
        job.work_model = wm
        # Keep legacy boolean in sync
        job.remote = wm == "remote"


# Registry — add new processors here
FIELD_PROCESSORS: dict[str, FieldProcessor] = {
    "salary": SalaryProcessor(),
    "locations": LocationProcessor(),
    "work_model": WorkModelProcessor(),
}

# ---------------------------------------------------------------------------
# Status tracking (in-memory, same pattern as enrichment)
# ---------------------------------------------------------------------------

_extraction_status: dict[str, Any] = {
    "running": False,
    "phase": None,
    "total_jobs": 0,
    "processed": 0,
    "salary_extracted": 0,
    "locations_created": 0,
    "locations_matched": 0,
    "failed": 0,
    "started_at": None,
    "finished_at": None,
    "error": None,
}


def get_extraction_status() -> dict[str, Any]:
    return dict(_extraction_status)


def _reset_status() -> None:
    _extraction_status.update(
        running=False,
        phase=None,
        total_jobs=0,
        processed=0,
        salary_extracted=0,
        locations_created=0,
        locations_matched=0,
        failed=0,
        started_at=None,
        finished_at=None,
        error=None,
    )


# ---------------------------------------------------------------------------
# LLM call helpers
# ---------------------------------------------------------------------------


def _get_concurrency() -> int:
    from app.services.rate_limits import max_concurrent_llm_calls

    return max_concurrent_llm_calls()


BATCH_COMMIT_SIZE = 100


def _parse_json_response(text: str) -> dict:
    """Parse JSON from LLM response, stripping markdown fences if present."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    return json.loads(text)


def _truncate_description(desc: str) -> str:
    """Return the full description for extraction accuracy."""
    return desc


def _count_expected_locations(raw_location: str | None) -> int:
    """Estimate how many distinct locations are in the raw location string."""
    if not raw_location:
        return 0
    # Count delimiters: semicolons, pipes
    parts = [p.strip() for p in raw_location.replace("|", ";").split(";") if p.strip()]
    # Filter out non-location parts like "Remote", "REMOTE (US)", region names
    skip = {"remote", "united states", "us", "global", "north america", "canada"}
    city_parts = [
        p
        for p in parts
        if p.lower().split("(")[0].strip().rstrip("-").strip().lower() not in skip
        and "remote" not in p.lower()
        and " - " not in p  # region like "Northeast - United States"
    ]
    return len(city_parts)


RETRY_PROMPT = """\
Your previous extraction returned {got} locations, but the listed location field \
contains at least {expected} distinct cities/places delimited by semicolons or pipes:

Listed Location: {location}

Please re-extract. Each semicolon/pipe-separated city should be its own location object. \
Return the full JSON structure again (salary + work_model + locations)."""


async def _call_llm(client, messages: list[dict]) -> str:
    """Make a single LLM call and return the raw text response."""
    resp = await client.chat.completions.create(
        model=EXTRACTION_MODEL,
        messages=messages,
        max_completion_tokens=2000,
    )
    content = resp.choices[0].message.content
    if not content:
        raise ValueError(f"Empty LLM response (finish_reason={resp.choices[0].finish_reason})")
    return content.strip()


async def _extract_one(
    job: Job,
    semaphore: asyncio.Semaphore,
) -> tuple[Job, dict | None]:
    """Call LLM for one job. Retries once if location count looks too low."""
    async with semaphore:
        client = get_openai_client()

        # Include ATS-provided structured data (e.g. Ashby compensationTierSummary)
        # so the LLM can extract salary even when it's not in the description text
        meta = job.extra_metadata or {}
        ats_lines = []
        if meta.get("compensation_raw"):
            ats_lines.append(f"Compensation (from ATS): {meta['compensation_raw']}")
        ats_metadata = "\n".join(ats_lines) + "\n" if ats_lines else ""

        prompt = EXTRACTION_PROMPT.format(
            title=job.display_title,
            company=job.display_company,
            location=job.location or "Not specified",
            ats_metadata=ats_metadata,
            description=_truncate_description(job.description or ""),
        )
        messages = [
            {"role": "system", "content": EXTRACTION_SYSTEM},
            {"role": "user", "content": prompt},
        ]
        try:
            text = await _call_llm(client, messages)
            extracted = _parse_json_response(text)

            # Validate: if raw location has multiple delimited cities but
            # the LLM returned too few, retry with an explicit nudge
            expected = _count_expected_locations(job.location)
            got = len(extracted.get("locations", []))
            if expected >= 2 and got < expected:
                retry_msg = RETRY_PROMPT.format(
                    got=got,
                    expected=expected,
                    location=job.location,
                )
                messages.append({"role": "assistant", "content": text})
                messages.append({"role": "user", "content": retry_msg})
                text2 = await _call_llm(client, messages)
                try:
                    retried = _parse_json_response(text2)
                    if len(retried.get("locations", [])) > got:
                        extracted = retried
                except (json.JSONDecodeError, ValueError):
                    pass  # keep original extraction

            return job, extracted
        except Exception:
            logger.exception("Extraction failed for job %s", job.id)
            return job, None


# ---------------------------------------------------------------------------
# Browser-based salary fallback
# ---------------------------------------------------------------------------


async def _browser_salary_fallback(job: Job) -> dict | None:
    """Render the job page in a browser and extract salary via LLM.

    Uses the shared browser pool (no per-job Chromium launch overhead).
    Catches JS-rendered compensation sections (e.g. Ashby, Workday).
    """
    if not job.url:
        return None

    try:
        from app.services.browser_pool import fetch_page_text

        page_text = await fetch_page_text(job.url, wait_ms=2000)
        if not page_text or len(page_text.strip()) < 100:
            return None

        # Truncate to avoid token waste — salary is usually near top or bottom
        if len(page_text) > 6000:
            page_text = page_text[:3000] + "\n...\n" + page_text[-3000:]

        client = get_openai_client()
        prompt = BROWSER_SALARY_PROMPT.format(
            title=job.display_title,
            company=job.display_company,
            url=job.url,
            page_text=page_text,
        )
        resp = await client.chat.completions.create(
            model=EXTRACTION_MODEL,
            messages=[
                {"role": "system", "content": BROWSER_SALARY_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            max_completion_tokens=200,
        )
        text = resp.choices[0].message.content.strip()
        result = _parse_json_response(text)

        if result.get("salary_confidence", "none") != "none" and result.get("salary_min"):
            logger.info(
                "Browser fallback found salary for %s: %s-%s (%s)",
                job.id,
                result["salary_min"],
                result.get("salary_max"),
                result["salary_confidence"],
            )
            return result

    except Exception:
        logger.warning("Browser salary fallback failed for job %s", job.id, exc_info=True)

    return None


# ---------------------------------------------------------------------------
# Location normalization phase
# ---------------------------------------------------------------------------


def _make_display_name(loc: dict) -> str:
    """Build display_name from extracted location dict."""
    city = (loc.get("city") or "").strip()
    state = (loc.get("state") or "").strip() or None
    country = (loc.get("country") or "US").strip()

    if not city:
        return ""

    if state:
        return f"{city}, {state}"
    if country and country != "US":
        return f"{city}, {country}"
    return city


async def _resolve_locations(
    session: AsyncSession,
    location_buffer: dict[str, list[dict]],
) -> None:
    """Bulk-resolve extracted locations → locations table + job_locations."""
    if not location_buffer:
        return

    _extraction_status["phase"] = "location_normalization"

    # 1. Collect unique extracted locations
    unique_locs: dict[str, dict] = {}  # display_name → first loc dict
    job_loc_pairs: list[tuple[str, str]] = []  # (job_id, display_name)

    for job_id, locs in location_buffer.items():
        for loc in locs:
            dn = _make_display_name(loc)
            if not dn:
                continue
            if dn not in unique_locs:
                unique_locs[dn] = loc
            job_loc_pairs.append((job_id, dn))

    if not unique_locs:
        return

    # 2. Direct match against existing locations
    existing_result = await session.execute(select(Location))
    existing: dict[str, Location] = {
        loc.display_name: loc for loc in existing_result.scalars().all()
    }

    resolved: dict[str, Location] = {}
    unmatched: dict[str, dict] = {}

    for dn, loc_dict in unique_locs.items():
        if dn in existing:
            resolved[dn] = existing[dn]
            _extraction_status["locations_matched"] += 1
        else:
            unmatched[dn] = loc_dict

    # 3. LLM match for unresolved locations (if we have candidates to match against)
    if unmatched and existing:
        try:
            candidate_names = sorted(existing.keys())
            unmatched_names = sorted(unmatched.keys())

            client = get_openai_client()
            prompt = LOCATION_MATCH_PROMPT.format(
                unmatched=json.dumps(unmatched_names, indent=2),
                candidates=json.dumps(candidate_names, indent=2),
            )
            resp = await client.chat.completions.create(
                model=EXTRACTION_MODEL,
                messages=[
                    {"role": "system", "content": LOCATION_MATCH_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                max_completion_tokens=2000,
            )
            text = resp.choices[0].message.content.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()

            matches = json.loads(text)
            for m in matches:
                extracted_name = m.get("extracted", "")
                matched_name = m.get("match")
                if matched_name and matched_name in existing and extracted_name in unmatched:
                    resolved[extracted_name] = existing[matched_name]
                    _extraction_status["locations_matched"] += 1
                    del unmatched[extracted_name]
        except Exception:
            logger.exception("Location LLM matching failed, creating new entries for all unmatched")

    # 4. Create new locations for remaining unmatched
    for dn, loc_dict in unmatched.items():
        new_loc = Location(
            display_name=dn,
            city=loc_dict.get("city") or dn,
            state=(loc_dict.get("state") or "").strip() or None,
            country=(loc_dict.get("country") or "US").strip(),
            is_remote=bool(loc_dict.get("is_remote")),
        )
        session.add(new_loc)
        resolved[dn] = new_loc
        _extraction_status["locations_created"] += 1

    await session.flush()  # get IDs for new locations

    # 5. Bulk-insert job_locations using ON CONFLICT DO NOTHING
    if job_loc_pairs:
        values = []
        for job_id, dn in job_loc_pairs:
            if dn in resolved:
                values.append({"job_id": job_id, "location_id": resolved[dn].id})

        if values:
            stmt = pg_insert(JobLocation).values(values).on_conflict_do_nothing()
            await session.execute(stmt)

    await session.commit()


# ---------------------------------------------------------------------------
# Targeted extraction for pipeline integration
# ---------------------------------------------------------------------------


async def extract_new_jobs(
    session: AsyncSession,
    jobs: list[Job],
    *,
    browser_fallback: bool = False,
) -> dict:
    """Extract salary/location/work_model for a list of jobs.

    Called by the pipeline after cleaning. Only processes jobs where at least
    one field processor says it needs work (salary_min is None, work_model is
    None, etc.).

    Args:
        browser_fallback: If True, launch Playwright for jobs where salary
            wasn't found. Expensive (~15s per job). Default False for pipeline
            auto-runs; set True for user-initiated extraction.

    Returns stats: {extracted, failed}.
    """
    stats = {"extracted": 0, "failed": 0}

    to_extract = [j for j in jobs if any(p.should_process(j) for p in FIELD_PROCESSORS.values())]

    if not to_extract:
        return stats

    logger.info("Extracting fields for %d new jobs", len(to_extract))

    semaphore = asyncio.Semaphore(_get_concurrency())
    context: dict[str, Any] = {}

    for batch_start in range(0, len(to_extract), BATCH_COMMIT_SIZE):
        batch = to_extract[batch_start : batch_start + BATCH_COMMIT_SIZE]
        tasks = [_extract_one(job, semaphore) for job in batch]
        results = await asyncio.gather(*tasks)

        for job, extracted in results:
            if extracted is None:
                stats["failed"] += 1
                continue

            for name, processor in FIELD_PROCESSORS.items():
                if processor.should_process(job):
                    try:
                        await processor.apply(job, extracted, context)
                    except Exception:
                        logger.exception("Processor %s failed for job %s", name, job.id)

            meta = dict(job.extra_metadata or {})
            meta["extracted"] = True
            job.extra_metadata = meta
            stats["extracted"] += 1

        await session.commit()

    # Bulk-resolve buffered locations
    location_buffer = context.get("location_buffer", {})
    if location_buffer:
        await _resolve_locations(session, location_buffer)

    # Browser fallback for jobs where salary wasn't found in description/API
    # Only runs when explicitly requested (expensive: browser + LLM per job)
    if browser_fallback:
        fallback_jobs = context.get("salary_fallback_jobs", [])
        if fallback_jobs:
            logger.info("Running browser salary fallback for %d jobs", len(fallback_jobs))
            # Run in parallel, capped by detected limits
            from app.services.rate_limits import max_concurrent_browser

            fallback_sem = asyncio.Semaphore(max_concurrent_browser())

            async def _fallback_one(job: Job) -> bool:
                async with fallback_sem:
                    result = await _browser_salary_fallback(job)
                    if result:
                        job.salary_min = result["salary_min"]
                        job.salary_max = result.get("salary_max")
                        meta = dict(job.extra_metadata or {})
                        meta["salary_source"] = "browser_fallback"
                        meta["salary_confidence"] = result["salary_confidence"]
                        job.extra_metadata = meta
                        return True
                    return False

            results = await asyncio.gather(*[_fallback_one(j) for j in fallback_jobs])
            fallback_count = sum(1 for r in results if r)
            await session.commit()
            logger.info(
                "Browser fallback: found salary for %d/%d jobs", fallback_count, len(fallback_jobs)
            )

    logger.info(
        "Extraction for new jobs complete: %d extracted, %d failed",
        stats["extracted"],
        stats["failed"],
    )
    return stats


# ---------------------------------------------------------------------------
# Main pipeline orchestrator
# ---------------------------------------------------------------------------


async def run_extraction_pipeline(session: AsyncSession) -> None:
    """Run the full extraction pipeline: LLM extraction → field processing → location normalization."""
    if _extraction_status["running"]:
        return

    _reset_status()
    _extraction_status["running"] = True
    _extraction_status["started_at"] = time.time()
    _extraction_status["phase"] = "querying_jobs"

    try:
        # 1. Query jobs that need extraction (any processor says should_process)
        result = await session.execute(select(Job))
        all_jobs = result.scalars().all()

        # Filter to jobs where at least one processor wants to run
        jobs_to_process = [
            j for j in all_jobs if any(p.should_process(j) for p in FIELD_PROCESSORS.values())
        ]

        _extraction_status["total_jobs"] = len(jobs_to_process)
        _extraction_status["phase"] = "extracting"
        logger.info("Extraction pipeline: %d jobs to process", len(jobs_to_process))

        if not jobs_to_process:
            return

        # Shared context for cross-job data (e.g., location buffer)
        context: dict[str, Any] = {}
        semaphore = asyncio.Semaphore(_get_concurrency())

        # 2. Process in batches for periodic commits
        for batch_start in range(0, len(jobs_to_process), BATCH_COMMIT_SIZE):
            batch = jobs_to_process[batch_start : batch_start + BATCH_COMMIT_SIZE]
            tasks = [_extract_one(job, semaphore) for job in batch]
            results = await asyncio.gather(*tasks)

            for job, extracted in results:
                if extracted is None:
                    _extraction_status["failed"] += 1
                    _extraction_status["processed"] += 1
                    continue

                # Apply each field processor
                for name, processor in FIELD_PROCESSORS.items():
                    if processor.should_process(job):
                        try:
                            await processor.apply(job, extracted, context)
                        except Exception:
                            logger.exception("Processor %s failed for job %s", name, job.id)

                # Stamp extraction complete so re-runs skip this job
                meta = dict(job.extra_metadata or {})
                meta["extracted"] = True
                job.extra_metadata = meta

                # Track salary extraction
                if extracted.get("salary_min") and extracted.get("salary_confidence") != "none":
                    if job.salary_min is not None:
                        _extraction_status["salary_extracted"] += 1

                _extraction_status["processed"] += 1

            await session.commit()

        # 3. Location normalization phase
        location_buffer = context.get("location_buffer", {})
        await _resolve_locations(session, location_buffer)

        _extraction_status["phase"] = "done"
        logger.info(
            "Extraction complete: %d processed, %d salary, %d locations created, %d matched, %d failed",
            _extraction_status["processed"],
            _extraction_status["salary_extracted"],
            _extraction_status["locations_created"],
            _extraction_status["locations_matched"],
            _extraction_status["failed"],
        )

    except Exception as e:
        _extraction_status["error"] = str(e)
        logger.exception("Extraction pipeline failed")
        raise
    finally:
        _extraction_status["running"] = False
        _extraction_status["finished_at"] = time.time()
