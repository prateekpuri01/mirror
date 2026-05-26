import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import async_session
from app.routers.application_requirements import router as app_req_router
from app.routers.chat import router as chat_router
from app.routers.cleaning import router as cleaning_router
from app.routers.companies import router as companies_router
from app.routers.documents import router as documents_router
from app.routers.extraction import router as extraction_router
from app.routers.hot_search import router as hot_search_router
from app.routers.jobs import router as jobs_router
from app.routers.onboarding import router as onboarding_router
from app.routers.pipeline import router as pipeline_router
from app.routers.profile import router as profile_router
from app.routers.scoring import router as scoring_router
from app.routers.scrape import router as scrape_router
from app.routers.search_profiles import router as search_profiles_router
from app.routers.setup import router as setup_router
from app.routers.tags import router as tags_router
from app.routers.writing_memory import router as writing_memory_router
from app.services.profile_sync import sync_complete_profile, sync_profile_from_yaml

_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, _LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with async_session() as session:
        # Load runtime settings (provider + API keys) from app_settings table
        # BEFORE anything else so subsequent startup work uses the right keys.
        # Falls back to env-var values for keys with no DB row.
        try:
            from app.services import app_settings_service

            await app_settings_service.load_into_settings(session, settings)
            from app.ai.client import reset_openai_client

            reset_openai_client()
        except Exception:
            logger.exception("Failed to load runtime settings on startup")

        # If no profile.yaml exists, fall back to the fictional .example so the
        # app boots into a usable demo state instead of an empty profile.
        from pathlib import Path

        profile_path = Path(settings.profile_yaml_path)
        if not profile_path.exists():
            example_path = profile_path.with_name("profile.yaml.example")
            if example_path.exists():
                logger.info("profile.yaml not found — using profile.yaml.example for first boot")
                profile_path = example_path

        try:
            await sync_profile_from_yaml(session, str(profile_path))
        except FileNotFoundError:
            logger.warning("Profile YAML not found — skipping initial sync")
        except Exception:
            logger.exception("Failed to sync profile on startup")

        # Same fallback for the rich accomplishment catalog.
        complete_path = Path(settings.profile_complete_yaml_path)
        if not complete_path.exists():
            complete_example = complete_path.with_name("profile_complete.yaml.example")
            if complete_example.exists():
                logger.info("profile_complete.yaml not found — using profile_complete.yaml.example")
                complete_path = complete_example
        try:
            await sync_complete_profile(session, str(complete_path))
        except Exception:
            logger.exception("Failed to sync complete profile on startup")

        # Clean up stale in-progress operations from previous server run
        try:
            from app.services.app_req_extraction_service import cleanup_stale_extractions

            await cleanup_stale_extractions(session)
        except Exception:
            logger.exception("Failed to clean up stale extractions")

    # Start background scheduler for daily maintenance
    from app.services.scheduler import run_scheduler

    scheduler_task = asyncio.create_task(run_scheduler())

    yield

    # Shutdown: cancel scheduler + close browser pool
    scheduler_task.cancel()
    try:
        await scheduler_task
    except asyncio.CancelledError:
        pass

    from app.services.browser_pool import shutdown as shutdown_browser

    await shutdown_browser()


app = FastAPI(title="Job Board API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(profile_router)
app.include_router(jobs_router)
app.include_router(tags_router)
app.include_router(search_profiles_router)
app.include_router(documents_router)
app.include_router(app_req_router)
app.include_router(companies_router)
app.include_router(scrape_router)
app.include_router(scoring_router)
app.include_router(chat_router)
app.include_router(cleaning_router)
app.include_router(extraction_router)
app.include_router(pipeline_router)
app.include_router(hot_search_router)
app.include_router(onboarding_router)
app.include_router(setup_router)
app.include_router(writing_memory_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
