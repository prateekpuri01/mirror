"""Unified web search.

Two entry points:
  web_search()         — discovery: native LLM search → SearXNG
  web_search_precise() — verification: SearXNG → native LLM (on miss)

Use web_search() for broad queries ("AI safety startups in SF").
Use web_search_precise() for queries that need keyword precision —
``site:`` operators, exact-match company names, ATS slug probing.

Backend priority:
  1. **Native LLM search** (OpenAI ``web_search`` tool on the Responses
     API with ``gpt-5.5`` + ``reasoning.effort=high``, or Anthropic
     ``web_search_20250305`` on the Messages API). Highest quality across
     every axis in the in-tree eval (``scripts/eval/eval_web_search.py``):
     mean total 6.62/9 vs SearXNG's 5.25/9 over 8 representative queries.
     Activates automatically when the configured LLM provider has its key
     set, with no extra setup.
  2. **SearXNG** — free, self-hosted, ships in ``docker-compose.yml``.
     The always-on baseline. Used as the fast tier for ``web_search_precise``
     (sub-second; handles ``site:`` operators natively) and as a fallback
     when native LLM search fails.

Returns a uniform list of {"title", "url", "snippet"} dicts regardless
of backend.

History:
  v0.1 used Perplexity Sonar (premium grounded search) and Brave Search
  (paid fallback). The 2026-05 web-search eval showed native LLM search
  outperformed Perplexity on every axis at acceptable cost, and SearXNG
  served every query without Brave ever needing to fire. Both backends
  were retired in favor of zero external paid-search dependencies. The
  decision data lives in ``scripts/eval/eval_web_search.py`` for retro
  comparison.
"""

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def web_search(
    query: str,
    num_results: int = 5,
    time_range: str | None = None,
) -> list[dict]:
    """Search the web and return structured results.

    Tries native LLM search first (best quality if the active provider
    supports it). Falls back to SearXNG (free, self-hosted).

    Args:
        query: Search query string.
        num_results: Max results to return.
        time_range: Optional time filter ("day", "week", "month", "year").
            Only honored by SearXNG; native LLM search relies on the model
            inferring recency from the query.

    Returns:
        List of {"title": str, "url": str, "snippet": str}.
    """
    # Tier 1: Native LLM search (OpenAI Responses API or Anthropic Messages API)
    results = await _llm_native_search(query, num_results)
    if results:
        return results

    # Tier 2: SearXNG (free, self-hosted, always-on baseline)
    results = await _searxng_search(query, num_results, time_range)
    if results:
        return results

    logger.warning("All search backends failed for query: %s", query[:80])
    return []


async def web_search_precise(
    query: str,
    num_results: int = 5,
    time_range: str | None = None,
) -> list[dict]:
    """Precision web search — SearXNG first (sub-second, native ``site:``
    support), then native LLM search as a fallback.

    Use this for queries that need keyword precision: ``site:`` operators,
    exact-match company names, ATS slug probing, domain-scoped job
    searches. SearXNG is the fast tier; native LLM search is the
    quality fallback if SearXNG returns nothing.
    """
    # Tier 1: SearXNG (fast, the right tool for high-volume precision)
    results = await _searxng_search(query, num_results, time_range)
    if results:
        return results

    # Tier 2: Native LLM search (slower; handles site: operators
    # reasonably well as of the 2026-05 eval, though that's not what it
    # was designed for).
    results = await _llm_native_search(query, num_results)
    if results:
        return results

    logger.warning("Precise search backends failed for query: %s", query[:80])
    return []


async def _searxng_search(query: str, num_results: int, time_range: str | None) -> list[dict]:
    """Query SearXNG JSON API."""
    try:
        params: dict = {
            "q": query,
            "format": "json",
            "categories": "general",
            "language": "en",
        }
        if time_range:
            params["time_range"] = time_range

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{settings.searxng_url}/search",
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()

        results = []
        for item in data.get("results", [])[:num_results]:
            results.append(
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": item.get("content", ""),
                }
            )
        return results

    except Exception as e:
        logger.warning("SearXNG search failed: %s", e)
        return []


async def _llm_native_search(query: str, num_results: int) -> list[dict]:
    """Adapt the native-LLM-search adapter to the unified result shape.

    Native LLM search returns a synthesized answer + structured citations;
    we discard the answer here (this entry point's contract is "return raw
    results") and convert citations to ``{title, url, snippet}``. Callers
    that want the synthesized answer should use
    ``web_search_llm.llm_web_search()`` directly.
    """
    try:
        from app.services.web_search_llm import (
            llm_web_search,
            native_web_search_supported,
        )
    except ImportError:
        return []

    if not native_web_search_supported():
        return []

    result = await llm_web_search(query, num_results=num_results)
    if result is None or not result.citations:
        return []
    return [
        {"title": c.title, "url": c.url, "snippet": c.snippet}
        for c in result.citations[:num_results]
    ]
