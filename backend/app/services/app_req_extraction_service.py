"""Orchestrates application requirements extraction: Playwright + LLM agent + DB persistence."""

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.app_req_extraction import run_extraction_agent
from app.models.documents import ApplicationRequirements
from app.models.jobs import Job

logger = logging.getLogger(__name__)

# Per-job in-memory status tracking
_job_extraction_status: dict[str, dict[str, Any]] = {}


def get_req_extraction_status(job_id: str) -> dict[str, Any] | None:
    return _job_extraction_status.get(job_id)


def _reset_status(job_id: str) -> None:
    _job_extraction_status.pop(job_id, None)


async def cleanup_stale_extractions(session: AsyncSession) -> int:
    """Reset extraction_status='running' to null for jobs stuck from a previous server run.

    Call this on app startup to recover from interrupted extractions.
    Returns the number of jobs cleaned up.
    """
    from sqlalchemy import update

    result = await session.execute(
        update(ApplicationRequirements)
        .where(ApplicationRequirements.extraction_status == "running")
        .values(extraction_status=None, extraction_error="Interrupted by server restart")
    )
    count = result.rowcount
    if count > 0:
        await session.commit()
        logger.info("Cleaned up %d stale extraction(s) from previous run", count)
    return count


async def extract_application_requirements(
    session: AsyncSession,
    job_id: str,
) -> None:
    """Main entry point: extract application requirements for a single job."""
    job_id_str = str(job_id)

    # Check if already running
    status = _job_extraction_status.get(job_id_str)
    if status and status.get("running"):
        logger.warning("Extraction already running for job %s", job_id)
        return

    _job_extraction_status[job_id_str] = {
        "running": True,
        "started_at": time.time(),
        "error": None,
    }

    try:
        # 1. Load job
        result = await session.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one_or_none()
        if job is None:
            raise ValueError(f"Job {job_id} not found")

        # Determine URL to visit
        url = job.application_url or job.url
        if not url:
            raise ValueError(f"Job {job_id} has no URL")

        # 2. Upsert ApplicationRequirements with extraction_status = "running"
        req_result = await session.execute(
            select(ApplicationRequirements).where(ApplicationRequirements.job_id == job_id)
        )
        app_req = req_result.scalar_one_or_none()
        if app_req is None:
            app_req = ApplicationRequirements(job_id=job_id)
            session.add(app_req)

        app_req.extraction_status = "running"
        app_req.extraction_error = None
        app_req.extraction_source_url = url
        await session.commit()
        await session.refresh(app_req)

        # 3. Launch Playwright and run extraction agent
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--ignore-certificate-errors"],
            )
            context = await browser.new_context(ignore_https_errors=True)
            page = await context.new_page()

            try:
                extraction_result = await asyncio.wait_for(
                    run_extraction_agent(
                        page=page,
                        url=url,
                        job_title=job.display_title,
                        company=job.display_company,
                    ),
                    timeout=300,  # 5-minute wall-clock timeout
                )
            except TimeoutError:
                logger.warning("Extraction agent timed out after 5 minutes for job %s", job_id)
                raise RuntimeError("Extraction timed out after 5 minutes")
            finally:
                await browser.close()

        # 4. Map result to DB columns
        app_req.raw_extraction = extraction_result

        # Cover letter
        cl_status = extraction_result.get("cover_letter_status", "not_needed")
        app_req.cover_letter_status = cl_status
        app_req.needs_cover_letter = cl_status in ("required", "optional")

        # Resume
        needs_resume = extraction_result.get("needs_resume")
        if needs_resume is not None:
            app_req.needs_resume = needs_resume

        # Application fields (unified, classified list)
        fields = extraction_result.get("application_fields", [])
        app_req.application_fields = fields

        # Derive legacy columns from application_fields
        short_answer_fields = [
            f for f in fields if f.get("response_type") in ("short_answer", "free_text")
        ]
        fixed_fields = [f for f in fields if f.get("response_type") == "fixed_response"]

        # Short answer questions
        if short_answer_fields:
            app_req.short_answer_questions = [
                {
                    "question": f.get("label", ""),
                    "max_length": f.get("max_length"),
                    "required": f.get("required", True),
                }
                for f in short_answer_fields
            ]
            app_req.needs_short_answers = True
            app_req.short_answer_prompts = {
                "questions": [f.get("label", "") for f in short_answer_fields]
            }
        else:
            app_req.short_answer_questions = []
            app_req.needs_short_answers = False

        # Other requirements = fixed response fields
        if fixed_fields:
            app_req.other_requirements = [
                {
                    "type": f.get("field_type", "input"),
                    "description": f.get("label", ""),
                }
                for f in fixed_fields
            ]
            app_req.needs_other = True
            app_req.other_description = "; ".join(f.get("label", "") for f in fixed_fields)
        else:
            app_req.other_requirements = []
            app_req.needs_other = False

        app_req.extraction_status = "completed"
        app_req.extracted_at = datetime.now(UTC)
        await session.commit()

        logger.info("Extraction completed for job %s", job_id)

    except Exception as e:
        logger.exception("Extraction failed for job %s", job_id)
        _job_extraction_status[job_id_str]["error"] = str(e)

        # Try to persist the error status
        try:
            req_result = await session.execute(
                select(ApplicationRequirements).where(ApplicationRequirements.job_id == job_id)
            )
            app_req = req_result.scalar_one_or_none()
            if app_req:
                app_req.extraction_status = "failed"
                app_req.extraction_error = str(e)
                await session.commit()
        except Exception:
            logger.exception("Failed to persist extraction error for job %s", job_id)

    finally:
        _job_extraction_status[job_id_str] = {
            "running": False,
            "finished_at": time.time(),
            "error": _job_extraction_status.get(job_id_str, {}).get("error"),
        }
