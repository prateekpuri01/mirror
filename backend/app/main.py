import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import async_session
from app.routers.application_requirements import router as app_req_router
from app.routers.companies import router as companies_router
from app.routers.documents import router as documents_router
from app.routers.jobs import router as jobs_router
from app.routers.profile import router as profile_router
from app.routers.scoring import router as scoring_router
from app.routers.scrape import router as scrape_router
from app.routers.search_profiles import router as search_profiles_router
from app.routers.tags import router as tags_router
from app.routers.cleaning import router as cleaning_router
from app.routers.chat import router as chat_router
from app.routers.extraction import router as extraction_router
from app.routers.pipeline import router as pipeline_router
from app.routers.webhook import router as webhook_router
from app.routers.hot_search import router as hot_search_router
from app.services.profile_sync import sync_complete_profile, sync_profile_from_yaml

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: sync profile from YAML
    async with async_session() as session:
        try:
            await sync_profile_from_yaml(session, settings.profile_yaml_path)
        except FileNotFoundError:
            logger.warning("Profile YAML not found — skipping initial sync")
        except Exception:
            logger.exception("Failed to sync profile on startup")

        try:
            await sync_complete_profile(session, settings.profile_complete_yaml_path)
        except Exception:
            logger.exception("Failed to sync complete profile on startup")
    yield


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
app.include_router(webhook_router)
app.include_router(scrape_router)
app.include_router(scoring_router)
app.include_router(chat_router)
app.include_router(cleaning_router)
app.include_router(extraction_router)
app.include_router(pipeline_router)
app.include_router(hot_search_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
