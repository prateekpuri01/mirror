"""Setup wizard API: provider + API key configuration, validation, hot-reload.

The wizard persists into the ``app_settings`` table (not ``.env``) so a
fresh container boot picks up the user's keys without any host-side
file editing. Each save:

  1. Writes the supplied keys / provider into ``app_settings``.
  2. Calls ``load_into_settings`` to refresh the in-process ``Settings``.
  3. Resets the AsyncOpenAI singleton so the next LLM call picks up the
     new provider/key.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from openai import AsyncOpenAI
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import reset_openai_client
from app.config import settings
from app.database import get_session
from app.services import app_settings_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/setup", tags=["setup"])

# Detected rate limits cached in memory so the dashboard can surface them
rate_limits: dict = {}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class SetupStatusResponse(BaseModel):
    """What the wizard reports about current configuration.

    ``needs_setup`` is true iff the configured provider has no API key set,
    so the frontend can route fresh users into ``/setup`` automatically.
    """

    needs_setup: bool
    llm_provider: str
    has_openai_key: bool
    has_anthropic_key: bool
    ollama_base_url: str


class TestKeyRequest(BaseModel):
    openai_api_key: str


class TestKeyResponse(BaseModel):
    valid: bool
    error: str | None = None
    rate_limits: dict | None = None


class SaveKeysRequest(BaseModel):
    """All fields are optional; only the supplied ones get persisted.

    The endpoint validates that the chosen ``llm_provider`` has its
    matching key (or a base URL for Ollama) before saving.
    """

    llm_provider: str | None = None  # "openai" | "anthropic" | "ollama"
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    ollama_base_url: str | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def _has_provider_key(provider: str) -> bool:
    if provider == "openai":
        return bool(settings.openai_api_key)
    if provider == "anthropic":
        return bool(settings.anthropic_api_key)
    if provider == "ollama":
        return bool(settings.ollama_base_url)
    return False


@router.get("/status", response_model=SetupStatusResponse)
async def setup_status():
    """Whether the active provider has a usable key.

    Drives the frontend's auto-redirect to ``/setup`` on first launch.
    """
    provider = (settings.llm_provider or "openai").lower()
    return SetupStatusResponse(
        needs_setup=not _has_provider_key(provider),
        llm_provider=provider,
        has_openai_key=bool(settings.openai_api_key),
        has_anthropic_key=bool(settings.anthropic_api_key),
        ollama_base_url=settings.ollama_base_url or "",
    )


@router.post("/test-key", response_model=TestKeyResponse)
async def test_key(request: TestKeyRequest):
    """Test an OpenAI API key and detect rate limits.

    Anthropic and Ollama keys aren't tested here — Anthropic doesn't
    expose rate-limit headers in the same shape, and Ollama is local.
    Save flow surfaces invalid keys at first generation.
    """
    key = request.openai_api_key.strip().strip('"').strip("'")
    if not key:
        return TestKeyResponse(valid=False, error="API key is empty")
    logger.info("Testing OpenAI key: length=%d, prefix=%s...", len(key), key[:8])

    client = AsyncOpenAI(api_key=key)
    try:
        models_resp = await client.models.list()
        model_ids = [m.id for m in models_resp.data]

        detected_limits: dict = {}
        test_model = "gpt-4o-mini" if "gpt-4o-mini" in model_ids else model_ids[0]
        try:
            response = await client.chat.completions.with_raw_response.create(
                model=test_model,
                messages=[{"role": "user", "content": "Say ok"}],
                max_completion_tokens=5,
            )
            for header_name, key_name in [
                ("x-ratelimit-limit-requests", "requests_per_minute"),
                ("x-ratelimit-limit-tokens", "tokens_per_minute"),
                ("x-ratelimit-remaining-requests", "remaining_requests"),
                ("x-ratelimit-remaining-tokens", "remaining_tokens"),
            ]:
                val = response.headers.get(header_name)
                if val:
                    try:
                        detected_limits[key_name] = int(val)
                    except ValueError:
                        detected_limits[key_name] = val
        except Exception:
            pass  # Rate limit detection is best-effort

        rate_limits.update(detected_limits)
        from app.services.rate_limits import update_from_test

        update_from_test(detected_limits)

        return TestKeyResponse(
            valid=True,
            rate_limits=detected_limits if detected_limits else None,
        )
    except Exception as e:
        msg = str(e)
        logger.warning("Key test failed: %s", msg[:300])
        if "401" in msg or "invalid" in msg.lower() or "Incorrect API key" in msg:
            return TestKeyResponse(valid=False, error="Invalid API key")
        if "429" in msg:
            return TestKeyResponse(valid=False, error="Rate limited — try again in a moment")
        if "SSL" in msg or "CERTIFICATE" in msg:
            return TestKeyResponse(
                valid=False, error="SSL/certificate error — check your network proxy settings"
            )
        return TestKeyResponse(valid=False, error=f"Connection error: {msg[:200]}")


@router.post("/save-keys")
async def save_keys(
    request: SaveKeysRequest,
    session: AsyncSession = Depends(get_session),
):
    """Persist provider + keys into ``app_settings`` and hot-reload.

    Validates that the chosen provider has its matching key set
    (combining what's already in ``settings`` with the new request).
    No ``.env`` file is written — settings live in the DB so they
    persist across container rebuilds.
    """
    provider = (request.llm_provider or settings.llm_provider or "openai").lower()
    if provider not in ("openai", "anthropic", "ollama"):
        raise HTTPException(status_code=400, detail=f"Unknown llm_provider: {provider!r}")

    # Resolve the post-save value of the matching provider field. If the
    # request omits a key, fall back to whatever's already in settings.
    def _resolved(field: str, override: str | None) -> str:
        if override is not None and override.strip():
            return override.strip()
        return getattr(settings, field, "") or ""

    final_openai = _resolved("openai_api_key", request.openai_api_key)
    final_anthropic = _resolved("anthropic_api_key", request.anthropic_api_key)
    final_ollama = _resolved("ollama_base_url", request.ollama_base_url)

    if provider == "openai" and not final_openai:
        raise HTTPException(
            status_code=400, detail="OpenAI provider selected but no API key supplied."
        )
    if provider == "anthropic" and not final_anthropic:
        raise HTTPException(
            status_code=400, detail="Anthropic provider selected but no API key supplied."
        )
    if provider == "ollama" and not final_ollama:
        raise HTTPException(
            status_code=400, detail="Ollama provider selected but no base URL supplied."
        )

    to_persist: dict[str, str | None] = {"llm_provider": provider}
    for field, val in [
        ("openai_api_key", request.openai_api_key),
        ("anthropic_api_key", request.anthropic_api_key),
        ("ollama_base_url", request.ollama_base_url),
    ]:
        if val is not None and val.strip():
            to_persist[field] = val.strip()

    try:
        written = await app_settings_service.set_many(session, to_persist)
        await session.commit()
    except Exception:
        logger.exception("Failed to persist settings")
        await session.rollback()
        raise HTTPException(status_code=500, detail="Failed to persist settings.")

    # Refresh the in-process settings from the DB and reset the LLM client
    # so the next call uses the new provider/key without a server restart.
    await app_settings_service.load_into_settings(session, settings)
    reset_openai_client()

    logger.info("Saved %d setting(s) — provider=%s", written, provider)
    return {
        "success": True,
        "saved": written,
        "llm_provider": provider,
    }
