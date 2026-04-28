"""Shared low-level LLM/JSON helpers used by both discovery and evaluation.

Lifted into its own module so `discovery.py` and `evaluation.py` can both
import from it without creating a cycle. There is intentionally nothing
hot-search-specific in here — these are general-purpose utilities.
"""

from __future__ import annotations

import json
import logging
import re

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def _openai_chat(system: str, user: str, temperature: float = 0.7) -> str | None:
    """Call OpenAI chat completions. Returns content string or None.

    Uses ``settings.hot_search_model`` and the OpenAI HTTP API directly via
    httpx (rather than the openai SDK) so we can keep the timeout knob
    explicit and avoid pulling in the SDK's retry layer for this code path.
    """
    api_key = settings.openai_api_key
    if not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": settings.hot_search_model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": temperature,
                    "max_tokens": 1500,
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
    except Exception:
        logger.warning("OpenAI call failed", exc_info=True)
        return None


def _parse_json_array(text: str) -> list | None:
    """Parse a JSON array from LLM output, handling markdown code blocks
    and the common case of an array embedded in surrounding prose.
    """
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        # Try to find a JSON array in the text
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
    return None


__all__ = ["_openai_chat", "_parse_json_array"]
