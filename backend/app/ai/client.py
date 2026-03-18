import logging

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

_client: AsyncAnthropic | None = None
_openai_client: AsyncOpenAI | None = None


def get_client() -> AsyncAnthropic:
    """Return a singleton AsyncAnthropic client."""
    global _client
    if _client is None:
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        _client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


def get_openai_client() -> AsyncOpenAI:
    """Return a singleton AsyncOpenAI client."""
    global _openai_client
    if _openai_client is None:
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        _openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _openai_client


SCORING_MODEL = "claude-sonnet-4-20250514"
RESUME_MODEL = "gpt-5.4"
EXTRACTION_MODEL = "gpt-5-mini-2025-08-07"
