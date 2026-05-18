"""Onboarding API: resume upload, URL crawling, and profile assembly."""

import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.publication_enricher import enrich_publication
from app.ai.resume_extractor import (
    assemble_profile_from_sources,
    extract_complete_profile_from_resume,
    extract_profile_from_resume,
)
from app.database import async_session
from app.database import get_session
from app.models.profile import UserProfile
from app.services.onboarding_progress import progress
from app.services.resume_parser import extract_text
from app.services.url_crawler import crawl_urls
from app.services.profile_sync import update_complete_profile, update_profile_section
from app.services.publication_lookup import fetch_author_publications

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class OnboardingStatusResponse(BaseModel):
    needs_onboarding: bool
    has_profile: bool


class CrawlUrlEntry(BaseModel):
    type: str  # linkedin | github | google_scholar | website | other
    url: str


class CrawlUrlsRequest(BaseModel):
    urls: list[CrawlUrlEntry]


class AssembleProfileRequest(BaseModel):
    resume_text: str
    resume_extracted: dict
    url_texts: dict[str, str]


class SaveProfileRequest(BaseModel):
    profile: dict
    complete_profile: dict | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/status", response_model=OnboardingStatusResponse)
async def onboarding_status(session: AsyncSession = Depends(get_session)):
    """Check whether the user needs to complete onboarding."""
    result = await session.execute(select(UserProfile).limit(1))
    profile = result.scalar_one_or_none()

    if profile is None:
        return OnboardingStatusResponse(needs_onboarding=True, has_profile=False)

    # Profile exists — check if it has meaningful data
    data = profile.data or {}
    personal = data.get("personal", {})
    name = personal.get("name", "").strip() if personal else ""
    has_meaningful_data = bool(name)

    return OnboardingStatusResponse(
        needs_onboarding=not has_meaningful_data,
        has_profile=True,
    )


@router.post("/upload-resume")
async def upload_resume(file: UploadFile):
    """Upload a resume file (PDF/DOCX), extract text, and parse into profile data."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    # Validate file type
    lower_name = file.filename.lower()
    if not (lower_name.endswith(".pdf") or lower_name.endswith(".docx")):
        raise HTTPException(
            status_code=400,
            detail="Unsupported file format. Please upload a PDF or DOCX file.",
        )

    # Read file
    file_bytes = await file.read()
    if len(file_bytes) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File too large. Maximum size is 10 MB.")

    # Extract text
    try:
        resume_text = await extract_text(file.filename, file_bytes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Extract profile via LLM
    try:
        extracted_profile = await extract_profile_from_resume(resume_text)
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Extract accomplishments (and publications if no Scholar URL)
    has_scholar = bool(
        (extracted_profile.get("personal") or {}).get("google_scholar")
    )
    try:
        extracted_complete = await extract_complete_profile_from_resume(
            resume_text, extracted_profile, has_scholar_url=has_scholar
        )
    except Exception:
        logger.exception("Complete profile extraction failed — continuing without")
        extracted_complete = {"accomplishments": [], "publications": []}

    return {
        "success": True,
        "extracted_profile": extracted_profile,
        "extracted_complete_profile": extracted_complete,
        "resume_text": resume_text,
        "error": None,
    }


@router.post("/crawl-urls")
async def crawl_urls_endpoint(
    request: CrawlUrlsRequest,
    background_tasks: BackgroundTasks,
):
    """Start crawling provided URLs in the background."""
    if not request.urls:
        raise HTTPException(status_code=400, detail="No URLs provided")

    if progress.active:
        raise HTTPException(status_code=409, detail="A crawl is already in progress")

    task_id = str(uuid.uuid4())
    url_list = [{"type": entry.type, "url": entry.url} for entry in request.urls]
    progress.start(task_id, len(url_list))

    async def _run_crawl():
        try:
            def on_complete(url: str, result: dict):
                progress.url_completed(url, result)

            await crawl_urls(url_list, on_complete=on_complete)
            if progress.status != "completed":
                progress.finish()
        except Exception as e:
            logger.exception("URL crawl failed")
            progress.fail(str(e))

    background_tasks.add_task(_run_crawl)

    return {"task_id": task_id, "status": "started"}


@router.get("/crawl-status/{task_id}")
async def crawl_status(task_id: str):
    """Poll crawl progress for a given task."""
    if progress.task_id != task_id:
        raise HTTPException(status_code=404, detail="Unknown task ID")
    return progress.to_dict()


@router.post("/assemble-profile")
async def assemble_profile(request: AssembleProfileRequest):
    """Merge resume extraction + URL crawl results via LLM."""
    try:
        profile, complete = await assemble_profile_from_sources(
            resume_text=request.resume_text,
            resume_extracted=request.resume_extracted,
            url_texts=request.url_texts,
        )
    except Exception as e:
        logger.exception("Profile assembly failed")
        raise HTTPException(status_code=422, detail=f"Profile assembly failed: {str(e)}")

    return {"profile": profile, "complete_profile": complete}


@router.post("/save-profile")
async def save_profile(
    request: SaveProfileRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """Save the finalized onboarding profile."""
    try:
        profile_data = await update_profile_section(session, request.profile)
    except Exception as e:
        logger.exception("Failed to save profile")
        raise HTTPException(status_code=500, detail=f"Failed to save profile: {str(e)}")

    if request.complete_profile:
        try:
            await update_complete_profile(session, request.complete_profile)
        except Exception as e:
            logger.exception("Failed to save complete profile")
            # Non-fatal — base profile was already saved

    # Auto-import publications from Google Scholar if URL is present
    scholar_url = (request.profile.get("personal") or {}).get("google_scholar")
    author_name = (request.profile.get("personal") or {}).get("name")
    if scholar_url:
        background_tasks.add_task(
            _import_scholar_publications, scholar_url, author_name, request.profile
        )

    return {"success": True, "profile": profile_data}


async def _import_scholar_publications(
    scholar_url: str, author_name: str | None, profile_data: dict
) -> None:
    """Background task: import publications from Google Scholar via Semantic Scholar API."""
    try:
        papers = await fetch_author_publications(
            scholar_url=scholar_url, author_name=author_name
        )
        if not papers:
            logger.info("Scholar import: no publications found for %s", scholar_url)
            return

        enriched = []
        for paper in papers:
            try:
                pub = await enrich_publication(paper, profile_data)
                pub["auto_populated"] = True
                enriched.append(pub)
            except Exception:
                logger.warning("Failed to enrich paper: %s", paper.get("title", ""))
                continue

        if not enriched:
            return

        # Save to DB — merge into existing publications
        async with async_session() as session:
            result = await session.execute(select(UserProfile).limit(1))
            profile = result.scalar_one_or_none()
            if profile is None:
                return

            data = dict(profile.data) if profile.data else {}
            complete = data.get("complete_profile", {})
            existing_pubs = complete.get("publications", [])

            # Deduplicate by title (case-insensitive)
            existing_titles = {
                p.get("title", "").lower().strip() for p in existing_pubs
            }
            new_pubs = [
                p for p in enriched
                if p.get("title", "").lower().strip() not in existing_titles
            ]

            if new_pubs:
                complete["publications"] = existing_pubs + new_pubs
                data["complete_profile"] = complete
                profile.data = data
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(profile, "data")
                await session.commit()
                logger.info(
                    "Scholar import: added %d publications (total %d)",
                    len(new_pubs),
                    len(complete["publications"]),
                )
    except Exception:
        logger.exception("Background Scholar import failed")
