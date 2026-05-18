"""CRUD + reload helpers for runtime-overridable settings.

Storage lives in the ``app_settings`` table. The set of writable keys is
allowlisted to prevent the /setup endpoint from being abused to flip
arbitrary fields on the global ``Settings`` object.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_settings import AppSetting

logger = logging.getLogger(__name__)


# Keys the /setup wizard can write. Must mirror field names on
# ``app.config.Settings`` so ``load_into_settings`` can ``setattr``.
ALLOWED_KEYS: frozenset[str] = frozenset({
    "llm_provider",
    "openai_api_key",
    "anthropic_api_key",
    "ollama_base_url",
    "llm_resume_model",
    "llm_scoring_model",
    "llm_extraction_model",
    "llm_web_search_model",
    "llm_web_search_effort",
})


async def get_all(session: AsyncSession) -> dict[str, str]:
    """Return every persisted setting as ``{key: value}``. Skips NULL values."""
    result = await session.execute(select(AppSetting.key, AppSetting.value))
    return {k: v for k, v in result.all() if v}


async def set_value(session: AsyncSession, key: str, value: str | None) -> None:
    """Upsert a single setting. Raises ``ValueError`` for unknown keys."""
    if key not in ALLOWED_KEYS:
        raise ValueError(f"Setting key not allowed: {key!r}")
    stmt = (
        pg_insert(AppSetting)
        .values(key=key, value=value)
        .on_conflict_do_update(index_elements=["key"], set_={"value": value})
    )
    await session.execute(stmt)


async def set_many(session: AsyncSession, values: dict[str, str | None]) -> int:
    """Upsert several settings in one transaction. Returns the number written.
    Skips keys not in ``ALLOWED_KEYS`` and silently skips ``None`` values."""
    written = 0
    for key, val in values.items():
        if key not in ALLOWED_KEYS:
            continue
        if val is None:
            continue
        await set_value(session, key, val)
        written += 1
    return written


async def load_into_settings(session: AsyncSession, settings_obj) -> int:
    """Read all DB-persisted settings and override matching fields on the
    global ``Settings`` object. Returns the number of fields overridden.

    DB values take precedence over env-var defaults. Callers that need new
    values to propagate to the LLM client should follow this with a call to
    ``app.ai.client.reset_openai_client()``.
    """
    rows = await get_all(session)
    overridden = 0
    for key, value in rows.items():
        if hasattr(settings_obj, key):
            setattr(settings_obj, key, value)
            overridden += 1
    if overridden:
        logger.info("Loaded %d setting(s) from DB into runtime config", overridden)
    return overridden
