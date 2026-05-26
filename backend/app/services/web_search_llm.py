"""Native LLM web search.

OpenAI and Anthropic both ship grounded web search as a built-in tool on
their respective LLMs:

  - OpenAI: ``web_search_preview`` tool on the Responses API.
  - Anthropic: ``web_search_20250305`` tool on the Messages API.

Both return a synthesized answer plus structured citations. This module
normalizes them under one interface so callers don't have to branch on
provider:

    result = await llm_web_search("AI safety startups in SF", num_results=5)
    # WebSearchResult(answer="...", citations=[Citation(...), ...])

Falls back to ``None`` when the provider doesn't support native search
(Ollama) or no key is configured. ``web_search.py`` is the place that
decides whether to route through this module vs. Perplexity/SearXNG/Brave.

Implementation notes:
  - The Anthropic SDK is imported lazily so users who never use
    ``LLM_PROVIDER=anthropic`` don't pay the cold-start cost.
  - Both adapters do best-effort citation extraction — the wire formats
    differ enough that title/url shape is what we can guarantee. Snippet
    is filled in when the provider exposes it (Anthropic does for some
    tool result blocks; OpenAI's URL citations don't include snippets).
  - **OpenAI tier**: this module uses the ``web_search`` tool (production,
    not the legacy ``web_search_preview``) paired with a reasoning model
    (``gpt-5.5`` default) and ``reasoning.effort=high``. Per OpenAI's docs
    this is the "agentic search" config — the model performs searches as
    part of its chain of thought rather than dumping raw results. Slower
    and more expensive than the vanilla path, but the eval shows it
    closes most of the gap to Perplexity. Override both via env vars
    (``LLM_WEB_SEARCH_MODEL``, ``LLM_WEB_SEARCH_EFFORT``) if you want a
    cheaper-but-vaguer config.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.config import settings

logger = logging.getLogger(__name__)


# Default models for native web search. Overridable via env var so users
# can pin a different model if they want.
#
# OpenAI: ``gpt-5.5`` is the current high-quality reasoning model that
# pairs well with the ``web_search`` tool. The OpenAI docs are explicit
# that ``gpt-5.4`` with no reasoning produces lower quality results
# than ``gpt-5.5`` with reasoning.effort=high.
#
# Anthropic: Claude's web_search tool is reasoning-capable on every
# Claude 4.x model; pick the strongest available.
_OPENAI_WEB_SEARCH_MODEL_DEFAULT = "gpt-5.5"
_ANTHROPIC_WEB_SEARCH_MODEL_DEFAULT = "claude-opus-4-7"

# Default reasoning effort for the OpenAI Responses API. Supported values:
# "none" | "low" | "medium" (default) | "high" | "xhigh".
# Was "high" historically — calibrated for the broadest open-ended
# discovery searches. In practice the dominant caller is company-specific
# fact gathering ("what does Foxglove do; what does the eng team work
# on") where "high" effort doubled wall-clock without measurably better
# results: per-resume Phase 0 was eating 4-5 min on first-time research.
# Medium has been a clean win across the company_research call sites.
# Override via env var (``LLM_WEB_SEARCH_EFFORT``) when a specific call
# site needs more reasoning.
_OPENAI_REASONING_EFFORT_DEFAULT = "low"

# OpenAI's production web-search tool type. ``web_search_preview`` is the
# legacy variant kept around for backward compat but doesn't support
# newer controls (filters, return_token_budget) and is documented as
# lower-quality.
_OPENAI_WEB_SEARCH_TOOL_TYPE = "web_search"

# Anthropic's web search tool identifier. This is provider-versioned; bump
# if the SDK announces a newer revision.
_ANTHROPIC_WEB_SEARCH_TOOL_TYPE = "web_search_20250305"


@dataclass
class Citation:
    """A normalized search citation. ``snippet`` may be empty when the
    provider doesn't expose per-citation snippets."""

    title: str
    url: str
    snippet: str = ""


@dataclass
class WebSearchResult:
    """Uniform shape returned by every native-LLM-search adapter."""

    answer: str
    citations: list[Citation] = field(default_factory=list)
    # Token-usage diagnostics. Populated when the provider returns
    # structured usage (OpenAI Responses API does). Empty dict when the
    # provider doesn't expose it. Keys mirror OpenAI's shape:
    #   {
    #     "input_tokens": int,
    #     "output_tokens": int,                  # includes reasoning
    #     "reasoning_tokens": int,               # subset of output
    #     "cached_input_tokens": int,            # may be 0
    #     "search_calls": int,                   # web_search tool calls
    #   }
    # Callers that want cost estimates can apply per-tier rates.
    usage: dict = field(default_factory=dict)


def _web_search_model(default_for: str) -> str:
    """Return the model to use for native web search.

    Order: explicit env override → provider default. We don't reuse
    ``RESUME_MODEL`` because some heavy-reasoning models in our default
    table (e.g. ``gpt-5.4``) may not support the ``web_search_preview``
    tool yet; the defaults below are picked specifically for search
    compatibility.
    """
    override = (settings.llm_web_search_model or "").strip()
    if override:
        return override
    if default_for == "openai":
        return _OPENAI_WEB_SEARCH_MODEL_DEFAULT
    return _ANTHROPIC_WEB_SEARCH_MODEL_DEFAULT


def native_web_search_supported() -> bool:
    """True iff the active provider supports native web search.

    Used by ``app/services/web_search.py`` to decide whether to route
    discovery through this module before falling back to Perplexity / etc.
    """
    provider = (settings.llm_provider or "openai").lower()
    if provider == "openai":
        return bool(settings.openai_api_key)
    if provider == "anthropic":
        return bool(settings.anthropic_api_key)
    return False  # Ollama has no native web search.


async def llm_web_search(
    query: str,
    num_results: int = 5,
    *,
    effort: str | None = None,
) -> WebSearchResult | None:
    """Run a native LLM web search for the configured provider.

    Returns ``None`` when:
      - the provider doesn't support native search (Ollama)
      - no API key is configured for the provider
      - the call fails (logged at WARNING)

    ``effort`` overrides ``settings.llm_web_search_effort`` for this
    call. Hot search v2 uses this to let users opt into deeper search
    per-request without changing the global default.

    Callers that want a hard guarantee of a result should fall back to
    ``app.services.web_search`` after a ``None`` return.
    """
    provider = (settings.llm_provider or "openai").lower()
    if provider == "openai":
        return await _openai_web_search(query, num_results, effort=effort)
    if provider == "anthropic":
        return await _anthropic_web_search(query, num_results)
    return None


# ---------------------------------------------------------------------------
# OpenAI adapter — uses the Responses API + web_search_preview tool.
# ---------------------------------------------------------------------------


def _openai_reasoning_effort(override: str | None = None) -> str:
    """Resolve the reasoning effort for the OpenAI Responses API call.

    Resolution order:
      1. Per-call ``override`` argument (passed from callers that want
         this knob to be a runtime toggle, e.g. hot search v2).
      2. ``settings.llm_web_search_effort`` env override.
      3. ``_OPENAI_REASONING_EFFORT_DEFAULT`` constant (currently "low").

    Validated against the allowed set so a typo doesn't get silently
    swallowed by the API.
    """
    _ALLOWED = {"none", "low", "medium", "high", "xhigh"}
    if override:
        val = override.strip().lower()
        if val in _ALLOWED:
            return val
    val = (settings.llm_web_search_effort or "").strip().lower()
    if val in _ALLOWED:
        return val
    return _OPENAI_REASONING_EFFORT_DEFAULT


async def _openai_web_search(
    query: str,
    num_results: int,
    *,
    effort: str | None = None,
) -> WebSearchResult | None:
    if not settings.openai_api_key:
        return None
    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.openai_api_key)
        # ``web_search`` (production tool) + ``gpt-5.5`` + reasoning effort
        # is the agentic search configuration. Default effort is now "low"
        # (4x cheaper than medium with ~11pt judge-quality drop, offset by
        # running 4 queries instead of 3 — see eval data in commits
        # dab70af and the deep-search toggle flow).
        response = await client.responses.create(
            model=_web_search_model("openai"),
            tools=[{"type": _OPENAI_WEB_SEARCH_TOOL_TYPE}],
            reasoning={"effort": _openai_reasoning_effort(effort)},
            input=query,
        )
    except Exception as e:
        # Fall back to the legacy tool on models that don't yet support the
        # production ``web_search`` (some older accounts may not have rolled
        # out). One quiet retry — keeps us robust without burning the call
        # budget on every miss.
        if "web_search" in str(e) or "tool" in str(e).lower():
            try:
                logger.info("Retrying OpenAI search with legacy web_search_preview tool")
                response = await client.responses.create(
                    model=_web_search_model("openai"),
                    tools=[{"type": "web_search_preview"}],
                    input=query,
                )
            except Exception as e2:
                logger.warning("OpenAI native web search failed for %r: %s", query[:80], e2)
                return None
        else:
            logger.warning("OpenAI native web search failed for %r: %s", query[:80], e)
            return None

    answer = (getattr(response, "output_text", "") or "").strip()
    citations = _extract_openai_citations(response, num_results)
    usage = _extract_openai_usage(response)
    return WebSearchResult(answer=answer, citations=citations, usage=usage)


def _extract_openai_usage(response) -> dict:
    """Pull token counts + web-search tool calls out of the Responses
    API response. Returns an empty dict if the shape isn't recognized.

    The Responses API exposes:
      response.usage.input_tokens
      response.usage.output_tokens          # includes reasoning
      response.usage.output_tokens_details.reasoning_tokens
      response.usage.input_tokens_details.cached_tokens   # may be 0

    Web-search tool calls show up as items in ``response.output`` with
    type == "web_search_call"; we count them to estimate tool-fee cost.
    """
    out: dict = {}
    usage = getattr(response, "usage", None)
    if usage is not None:
        out["input_tokens"] = getattr(usage, "input_tokens", 0) or 0
        out["output_tokens"] = getattr(usage, "output_tokens", 0) or 0
        out_details = getattr(usage, "output_tokens_details", None)
        if out_details is not None:
            out["reasoning_tokens"] = getattr(out_details, "reasoning_tokens", 0) or 0
        in_details = getattr(usage, "input_tokens_details", None)
        if in_details is not None:
            out["cached_input_tokens"] = getattr(in_details, "cached_tokens", 0) or 0
    # Count web_search tool invocations
    search_calls = 0
    for item in getattr(response, "output", []) or []:
        t = getattr(item, "type", "")
        if t == "web_search_call" or t == "web_search_preview_call":
            search_calls += 1
    out["search_calls"] = search_calls
    return out


def _extract_openai_citations(response, num_results: int) -> list[Citation]:
    """Pull ``url_citation`` annotations from the Responses API output.

    The response carries one or more message items; each text content
    block can have ``annotations`` of type ``url_citation`` with the
    cited URL and page title. We deduplicate by URL to keep the list
    tight.
    """
    out: list[Citation] = []
    seen_urls: set[str] = set()
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", None) != "message":
            continue
        for block in getattr(item, "content", []) or []:
            for annotation in getattr(block, "annotations", []) or []:
                if getattr(annotation, "type", None) != "url_citation":
                    continue
                url = getattr(annotation, "url", "") or ""
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                out.append(
                    Citation(
                        title=getattr(annotation, "title", "") or "",
                        url=url,
                        snippet="",  # OpenAI URL citations don't carry snippets
                    )
                )
                if len(out) >= num_results:
                    return out
    return out


# ---------------------------------------------------------------------------
# Anthropic adapter — uses the Messages API + web_search tool.
# ---------------------------------------------------------------------------


async def _anthropic_web_search(query: str, num_results: int) -> WebSearchResult | None:
    if not settings.anthropic_api_key:
        return None
    try:
        # Lazy import: users on LLM_PROVIDER=openai never pay this cost.
        from anthropic import AsyncAnthropic
    except ImportError:
        logger.warning(
            "anthropic SDK not installed — set LLM_PROVIDER=openai or run `pip install anthropic`."
        )
        return None

    try:
        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        response = await client.messages.create(
            model=_web_search_model("anthropic"),
            max_tokens=1024,
            tools=[
                {
                    "type": _ANTHROPIC_WEB_SEARCH_TOOL_TYPE,
                    "name": "web_search",
                    "max_uses": max(1, min(num_results, 5)),
                }
            ],
            messages=[{"role": "user", "content": query}],
        )
    except Exception as e:
        logger.warning("Anthropic native web search failed for %r: %s", query[:80], e)
        return None

    answer = _join_anthropic_text(response)
    citations = _extract_anthropic_citations(response, num_results)
    return WebSearchResult(answer=answer, citations=citations)


def _join_anthropic_text(response) -> str:
    """Concatenate ``text`` blocks from a Messages-API response into a
    single answer string."""
    chunks: list[str] = []
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "text":
            chunks.append((getattr(block, "text", "") or "").strip())
    return "\n\n".join(c for c in chunks if c)


def _extract_anthropic_citations(response, num_results: int) -> list[Citation]:
    """Pull citations from Anthropic Messages-API content blocks.

    Two sources can carry URL/title pairs:
      - ``web_search_tool_result`` blocks — raw results from the search
        tool, with title + url + optional page_age.
      - ``text`` blocks with ``citations`` arrays — the model's actual
        in-text references.

    We prefer in-text citations (those are the URLs the model *used* to
    answer, not just everything the search returned), and fall back to
    raw tool results if the text didn't cite anything.
    """
    in_text: list[Citation] = []
    raw_results: list[Citation] = []
    seen_urls: set[str] = set()

    for block in getattr(response, "content", []) or []:
        block_type = getattr(block, "type", None)

        # In-text citations (preferred)
        if block_type == "text":
            for cit in getattr(block, "citations", []) or []:
                url = getattr(cit, "url", "") or ""
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                in_text.append(
                    Citation(
                        title=getattr(cit, "title", "") or "",
                        url=url,
                        snippet=getattr(cit, "cited_text", "") or "",
                    )
                )

        # Raw search results (fallback)
        elif block_type == "web_search_tool_result":
            for item in getattr(block, "content", []) or []:
                url = getattr(item, "url", "") or ""
                if not url:
                    continue
                raw_results.append(
                    Citation(
                        title=getattr(item, "title", "") or "",
                        url=url,
                        snippet=getattr(item, "page_age", "") or "",
                    )
                )

    preferred = in_text if in_text else raw_results
    return preferred[:num_results]
