"""Provider-agnostic LLM client.

All AI features call into the OpenAI Python SDK. To support multiple LLM
providers without rewriting every callsite, we exploit the fact that
**Anthropic** and **Ollama** both expose OpenAI-compatible endpoints.
Switching providers is just a matter of pointing the AsyncOpenAI client
at the right ``base_url`` with the right ``api_key``.

Configure with the following env vars (all optional except the API key for
the provider you actually use):

    LLM_PROVIDER          openai | anthropic | ollama   (default: openai)
    OPENAI_API_KEY        for LLM_PROVIDER=openai
    ANTHROPIC_API_KEY     for LLM_PROVIDER=anthropic
    OLLAMA_BASE_URL       for LLM_PROVIDER=ollama       (default: http://localhost:11434/v1)

    LLM_RESUME_MODEL       override the default heavy-lift model
    LLM_SCORING_MODEL      override the default mid-tier model
    LLM_EXTRACTION_MODEL   override the default lightweight model

Per-provider defaults live in ``_PROVIDER_DEFAULTS`` below.

Existing callsites use ``get_openai_client()`` and the module-level model
constants (``RESUME_MODEL``, ``SCORING_MODEL``, ``EXTRACTION_MODEL``). Both
remain supported — they now resolve from whichever provider is configured.
"""

import logging

from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)


# Per-provider defaults. Override per env var if you want to pin a specific
# model. Anthropic and Ollama models are accessed via their OpenAI-
# compatibility shims, so the model names here are what those endpoints
# expect at the wire level.
_PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    "openai": {
        "resume": "gpt-5.4",
        "scoring": "gpt-5.4",
        "extraction": "gpt-5-mini-2025-08-07",
    },
    "anthropic": {
        # Use latest Claude family. The OpenAI-compat endpoint accepts these
        # canonical model IDs as documented at:
        # https://docs.anthropic.com/en/api/openai-sdk
        "resume": "claude-opus-4-7",
        "scoring": "claude-sonnet-4-6",
        "extraction": "claude-haiku-4-5-20251001",
    },
    "ollama": {
        # Sensible local-model defaults. Override via env vars for your hardware.
        "resume": "llama3.1:70b",
        "scoring": "llama3.1:8b",
        "extraction": "llama3.1:8b",
    },
}


def _provider() -> str:
    return (settings.llm_provider or "openai").lower().strip()


def _resolve_model(role: str) -> str:
    """Return the canonical model name for a logical role given current
    provider, honoring per-role env overrides."""
    overrides = {
        "resume": settings.llm_resume_model,
        "scoring": settings.llm_scoring_model,
        "extraction": settings.llm_extraction_model,
    }
    if role in overrides and overrides[role]:
        return overrides[role]
    table = _PROVIDER_DEFAULTS.get(_provider()) or _PROVIDER_DEFAULTS["openai"]
    return table[role]


def _resolve_endpoint() -> tuple[str, str | None]:
    """Return (api_key, base_url) for the current provider.

    base_url is None for the default OpenAI endpoint.
    """
    provider = _provider()
    if provider == "openai":
        if not settings.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set (LLM_PROVIDER=openai)."
            )
        return settings.openai_api_key, None
    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set (LLM_PROVIDER=anthropic)."
            )
        return settings.anthropic_api_key, "https://api.anthropic.com/v1/"
    if provider == "ollama":
        # Ollama doesn't validate the api_key, but the OpenAI SDK requires one.
        return "ollama", settings.ollama_base_url
    raise RuntimeError(f"Unknown LLM_PROVIDER={provider!r}")


_openai_client: AsyncOpenAI | None = None


def get_openai_client() -> AsyncOpenAI:
    """Return a singleton AsyncOpenAI client pointed at the configured provider.

    Despite the name, this is provider-agnostic — Anthropic and Ollama are
    served via their OpenAI-compat endpoints. Existing callsites that use
    ``client.chat.completions.create(...)`` work unchanged across providers
    for the common case (chat with system + user messages).
    """
    global _openai_client
    if _openai_client is None:
        api_key, base_url = _resolve_endpoint()
        kwargs: dict = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        _openai_client = AsyncOpenAI(**kwargs)
        logger.info("LLM client: provider=%s base_url=%s", _provider(), base_url or "default")
    return _openai_client


def reset_openai_client() -> None:
    """Reset the singleton so the next call picks up new config."""
    global _openai_client
    _openai_client = None


def current_provider() -> str:
    """Public accessor — useful for adapters that need to skip
    OpenAI-specific kwargs (e.g. ``reasoning_effort``) when running against
    a provider that doesn't understand them."""
    return _provider()


# Module-level model constants. Resolved on first import; reset_openai_client()
# does NOT change these (env-var changes require an app restart).
RESUME_MODEL = _resolve_model("resume")
SCORING_MODEL = _resolve_model("scoring")
EXTRACTION_MODEL = _resolve_model("extraction")
