import asyncio
import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.accomplishment_enricher import enrich_accomplishment
from app.ai.docx_builder import build_docx
from app.ai.keyword_generator import generate_preference_keywords
from app.ai.linkedin_enricher import generate_accomplishments_from_linkedin
from app.ai.profile_suggester import suggest_profile_section
from app.ai.publication_enricher import enrich_publication
from app.ai.resume_presets import (
    COLOR_SCHEMES,
    FONTS,
    LAYOUTS,
    SAMPLE_PROFILE_DATA,
    SAMPLE_RESUME_CONTENT,
)
from app.database import get_session
from app.models.documents import DocType, Document
from app.models.profile import UserProfile
from app.services.linkedin_scraper import scrape_linkedin_profile
from app.services.profile_sync import update_complete_profile, update_profile_section
from app.services.publication_lookup import (
    fetch_author_publications,
    search_publication,
)

logger = logging.getLogger(__name__)


class GenerateKeywordsRequest(BaseModel):
    looking_for: str = ""
    not_looking_for: str = ""


class GenerateKeywordsResponse(BaseModel):
    positive_signals: list[str]
    exclusions: list[str]


class PublicationLookupRequest(BaseModel):
    title: str


class ScholarImportRequest(BaseModel):
    url: str | None = None


class LinkedInScrapeRequest(BaseModel):
    url: str | None = None


router = APIRouter(prefix="/api", tags=["profile"])


async def _get_profile(session: AsyncSession) -> UserProfile:
    """Helper to fetch the singleton profile or raise 404."""
    result = await session.execute(select(UserProfile).limit(1))
    profile = result.scalar_one_or_none()
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not synced yet")
    return profile


@router.get("/profile")
async def get_profile(session: AsyncSession = Depends(get_session)):
    profile = await _get_profile(session)
    # Return base profile data without complete_profile to keep response small
    data = dict(profile.data)
    data.pop("complete_profile", None)
    return data


@router.get("/profile/complete")
async def get_complete_profile(session: AsyncSession = Depends(get_session)):
    """Return the full comprehensive profile with all accomplishments and publications."""
    profile = await _get_profile(session)
    complete = profile.data.get("complete_profile")
    if complete is None:
        raise HTTPException(status_code=404, detail="Complete profile not synced yet")
    return complete


@router.patch("/profile")
async def patch_profile(body: dict, session: AsyncSession = Depends(get_session)):
    """Merge top-level sections into profile.data JSONB.

    Body: {"personal": {...}, "skills": {...}, ...}
    Returns full profile (without complete_profile).
    """
    # Check if LinkedIn URL changed — trigger background scrape
    result = await session.execute(select(UserProfile).limit(1))
    existing = result.scalar_one_or_none()

    updated = await update_profile_section(session, body)

    # Auto-trigger LinkedIn scrape on URL change
    if existing and "personal" in body:
        new_linkedin = body["personal"].get("linkedin", "")
        old_linkedin = (existing.data or {}).get("personal", {}).get("linkedin", "")
        if new_linkedin and new_linkedin != old_linkedin:
            # Fire-and-forget background scrape
            asyncio.create_task(_background_linkedin_scrape(new_linkedin))

    return updated


@router.post("/profile/generate-keywords", response_model=GenerateKeywordsResponse)
async def generate_keywords(body: GenerateKeywordsRequest):
    """Generate keyword lists from free-text search preferences using LLM."""
    result = await generate_preference_keywords(body.looking_for, body.not_looking_for)
    return result


class SuggestSectionRequest(BaseModel):
    section: str  # "domains" | "skills" | "target_roles"


@router.post("/profile/suggest")
async def suggest_section(
    body: SuggestSectionRequest,
    session: AsyncSession = Depends(get_session),
):
    """Generate AI suggestions for a profile section (domains, skills, target_roles)."""
    profile = await _get_profile(session)
    # Pass the full data blob so the suggester can see everything
    try:
        result = await suggest_profile_section(body.section, profile.data or {})
    except Exception as e:
        logger.exception("Profile suggestion failed for %s", body.section)
        return {"error": str(e)}
    return result


@router.patch("/profile/complete")
async def patch_complete_profile(body: dict, session: AsyncSession = Depends(get_session)):
    """Merge sections into profile.data['complete_profile'].

    Body: {"accomplishments": [...], "publications": [...]}
    Returns complete_profile.
    """
    try:
        return await update_complete_profile(session, body)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ---------------------------------------------------------------------------
# Accomplishment enrichment
# ---------------------------------------------------------------------------


@router.post("/profile/accomplishments/enrich")
async def enrich_accomplishment_endpoint(
    body: dict,
    session: AsyncSession = Depends(get_session),
):
    """Generate derived fields (so_what, skills, relevance, tags) for an accomplishment."""
    profile = await _get_profile(session)
    try:
        result = await enrich_accomplishment(body, profile.data or {})
    except Exception as e:
        logger.exception("Accomplishment enrichment failed")
        return {"success": False, "error": str(e)}
    return {"success": True, **result}


# ---------------------------------------------------------------------------
# Publication lookup & import
# ---------------------------------------------------------------------------


@router.post("/profile/publications/lookup")
async def lookup_publication(
    body: PublicationLookupRequest,
    session: AsyncSession = Depends(get_session),
):
    """Search Semantic Scholar for a publication and return an enriched draft."""
    paper = await search_publication(body.title)
    if paper is None:
        return {"found": False}

    profile = await _get_profile(session)
    enriched = await enrich_publication(paper, profile.data or {})
    return {"found": True, "publication": enriched}


@router.post("/profile/publications/import-scholar")
async def import_scholar_publications(
    body: ScholarImportRequest,
    session: AsyncSession = Depends(get_session),
):
    """Bulk import publications from Google Scholar via Semantic Scholar API."""
    profile = await _get_profile(session)
    profile_data = profile.data or {}

    scholar_url = body.url or profile_data.get("personal", {}).get("google_scholar")
    author_name = profile_data.get("personal", {}).get("name")

    if not scholar_url and not author_name:
        return {
            "success": False,
            "publications": [],
            "error": "No Google Scholar URL or author name available",
        }

    try:
        papers = await fetch_author_publications(scholar_url=scholar_url, author_name=author_name)
    except Exception as e:
        logger.exception("Scholar import failed")
        return {"success": False, "publications": [], "error": str(e)}

    if not papers:
        return {"success": False, "publications": [], "error": "No publications found"}

    # Enrich each paper with profile context
    enriched = []
    for paper in papers:
        try:
            pub = await enrich_publication(paper, profile_data)
            enriched.append(pub)
        except Exception:
            logger.warning("Failed to enrich paper: %s", paper.get("title", ""))
            continue

    return {"success": True, "publications": enriched, "error": None}


@router.post("/profile/publications/fetch-full-text")
async def fetch_publications_full_text(
    session: AsyncSession = Depends(get_session),
):
    """Fetch full text from arXiv for all publications that have arXiv IDs."""
    from app.services.publication_lookup import enrich_publication_with_full_text

    profile = await _get_profile(session)
    complete = (profile.data or {}).get("complete_profile", {})
    publications = complete.get("publications", [])

    updated_count = 0
    for i, pub in enumerate(publications):
        before = pub.get("full_text")
        enriched = await enrich_publication_with_full_text(pub)
        publications[i] = enriched
        if enriched.get("full_text") and not before:
            updated_count += 1

    if updated_count > 0:
        updated = dict(profile.data)
        updated["complete_profile"]["publications"] = publications
        profile.data = updated
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(profile, "data")
        await session.commit()

    return {"fetched": updated_count, "total": len(publications)}


# ---------------------------------------------------------------------------
# LinkedIn scrape & import
# ---------------------------------------------------------------------------


@router.post("/profile/linkedin/scrape")
async def scrape_linkedin(
    body: LinkedInScrapeRequest,
    session: AsyncSession = Depends(get_session),
):
    """Scrape LinkedIn profile and generate draft accomplishments."""
    profile = await _get_profile(session)
    profile_data = profile.data or {}

    url = body.url or profile_data.get("personal", {}).get("linkedin")
    if not url:
        return {"success": False, "accomplishments": [], "error": "No LinkedIn URL provided"}

    # Scrape
    scrape_result = await scrape_linkedin_profile(url)
    if not scrape_result["success"]:
        return {
            "success": False,
            "accomplishments": [],
            "error": scrape_result["error"],
        }

    # Cache the raw text in profile data
    updated_data = dict(profile_data)
    updated_data["linkedin_cache"] = {
        "raw_text": scrape_result["raw_text"],
        "scraped_at": scrape_result["scraped_at"],
        "url": url,
    }
    profile.data = updated_data
    await session.commit()

    # Generate accomplishments
    try:
        accomplishments = await generate_accomplishments_from_linkedin(
            scrape_result["raw_text"], profile_data
        )
    except Exception as e:
        logger.exception("LinkedIn enrichment failed")
        return {"success": False, "accomplishments": [], "error": str(e)}

    return {"success": True, "accomplishments": accomplishments, "error": None}


@router.get("/profile/linkedin/status")
async def linkedin_status(session: AsyncSession = Depends(get_session)):
    """Check if we have a cached LinkedIn scrape."""
    profile = await _get_profile(session)
    cache = (profile.data or {}).get("linkedin_cache")
    if cache:
        return {
            "cached": True,
            "scraped_at": cache.get("scraped_at"),
            "url": cache.get("url"),
        }
    return {"cached": False, "scraped_at": None, "url": None}


# ---------------------------------------------------------------------------
# Background helpers
# ---------------------------------------------------------------------------


async def _background_linkedin_scrape(url: str):
    """Fire-and-forget LinkedIn scrape triggered by URL change."""
    try:
        result = await scrape_linkedin_profile(url)
        if result["success"]:
            logger.info("Background LinkedIn scrape succeeded for %s", url)
        else:
            logger.warning("Background LinkedIn scrape failed: %s", result["error"])
    except Exception:
        logger.exception("Background LinkedIn scrape error")


# ---------------------------------------------------------------------------
# Resume design (layout / color / font) — saved selection lives at
# ``user_profile.data["resume_design"]`` via the regular PATCH /api/profile.
# This sample endpoint builds a one-off preview .docx so the user can see
# their chosen design without regenerating a real tailored resume.
# ---------------------------------------------------------------------------


class ResumeDesignSampleRequest(BaseModel):
    layout: str | None = None
    color_scheme: str | None = None
    font: str | None = None


@router.get("/profile/resume-design/presets")
async def list_resume_design_presets():
    """Return the available design preset IDs (so the frontend can validate
    that its hardcoded display metadata is in sync with the backend)."""
    return {
        "layouts": list(LAYOUTS.keys()),
        "color_schemes": list(COLOR_SCHEMES.keys()),
        "fonts": list(FONTS.keys()),
    }


async def _resolve_preview_content(
    session: AsyncSession,
) -> tuple[dict, dict, bool]:
    """Return ``(resume_data, profile_data, is_sample)`` for design previews.

    Prefers the user's most recent resume content_json so previews reflect
    real content; falls back to the bundled sample fixture when no resume
    has been generated yet.
    """
    profile_result = await session.execute(select(UserProfile).limit(1))
    profile = profile_result.scalar_one_or_none()
    profile_data: dict | None = profile.data if profile else None

    if profile_data:
        doc_result = await session.execute(
            select(Document)
            .where(Document.doc_type == DocType.resume)
            .where(Document.content_json.isnot(None))
            .order_by(Document.created_at.desc())
            .limit(1)
        )
        latest_doc = doc_result.scalar_one_or_none()
        if latest_doc is not None and latest_doc.content_json:
            return latest_doc.content_json, profile_data, False

    return dict(SAMPLE_RESUME_CONTENT), SAMPLE_PROFILE_DATA, True


@router.get("/profile/resume-design/preview-content")
async def get_resume_design_preview_content(
    session: AsyncSession = Depends(get_session),
):
    """Resume + profile data the design tab's side preview renders against.

    Same source-of-truth selection as the sample download (real resume if
    one exists, sample fixture otherwise) — keeps the in-app preview and
    the downloadable .docx showing the same content.
    """
    resume_data, profile_data, is_sample = await _resolve_preview_content(session)
    return {
        "is_sample": is_sample,
        "resume": resume_data,
        "profile": {
            "personal": profile_data.get("personal") or {},
            "education": profile_data.get("education") or [],
            "work_history": profile_data.get("work_history") or [],
        },
    }


@router.post("/profile/resume-design/sample")
async def download_resume_design_sample(
    body: ResumeDesignSampleRequest,
    session: AsyncSession = Depends(get_session),
):
    """Build a one-off sample .docx with the requested design.

    Content source preference:
      1. The user's most recent resume Document with content_json (so the
         sample reflects their actual content styled with the new design).
      2. ``SAMPLE_RESUME_CONTENT`` + ``SAMPLE_PROFILE_DATA`` as a fallback
         when no real resume exists yet.

    The user's saved design (in profile.data) is *not* used; the caller
    passes whatever they're currently previewing, which may differ from
    what's persisted.
    """
    selection = {
        k: v
        for k, v in {
            "layout": body.layout,
            "color_scheme": body.color_scheme,
            "font": body.font,
        }.items()
        if v
    }

    resume_data, profile_data, _is_sample = await _resolve_preview_content(session)

    filepath = build_docx(
        resume_data=resume_data,
        profile_data=profile_data or SAMPLE_PROFILE_DATA,
        job_id="design-sample",
        resume_design=selection or None,
    )
    if not os.path.exists(filepath):
        raise HTTPException(status_code=500, detail="Failed to build sample resume")

    filename = "resume_design_sample.docx"
    return FileResponse(
        path=filepath,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
