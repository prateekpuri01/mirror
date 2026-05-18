"""Generate keyword lists from free-text search preferences using LLM."""

import json
import logging

from app.ai.client import EXTRACTION_MODEL, get_openai_client

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a keyword extraction assistant for a job search tool.

Given a user's free-text description of what they're looking for (and not looking for) \
in a job, extract concise keyword phrases suitable for bulk keyword matching against \
job postings.

Rules:
- Each keyword should be 1-4 words, lowercase
- Be specific enough to match in job titles, descriptions, and company profiles
- Extract 8-15 positive signal keywords and 5-10 exclusion keywords
- Do NOT repeat the same concept in different phrasings
- Output ONLY valid JSON (no markdown fences) in this structure:

{"positive_signals": ["keyword1", "keyword2", ...], "exclusions": ["keyword1", "keyword2", ...]}"""


def _strip_fences(text: str) -> str:
    """Strip markdown code fences if present."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3].strip()
    return text


async def generate_preference_keywords(
    looking_for: str,
    not_looking_for: str,
) -> dict:
    """Extract keyword lists from free-text search preferences.

    Returns {"positive_signals": [...], "exclusions": [...]}.
    """
    user_content = ""
    if looking_for:
        user_content += f"What I'm looking for:\n{looking_for}\n\n"
    if not_looking_for:
        user_content += f"What I want to avoid:\n{not_looking_for}\n\n"

    if not user_content.strip():
        return {"positive_signals": [], "exclusions": []}

    user_content += "Extract keywords from the above."

    client = get_openai_client()
    response = await client.chat.completions.create(
        model=EXTRACTION_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        max_completion_tokens=4000,
    )

    message = response.choices[0].message
    raw = message.content or ""
    if not raw:
        logger.warning(
            "Empty content from keyword generation. finish_reason=%s, refusal=%s, message=%s",
            response.choices[0].finish_reason,
            getattr(message, "refusal", None),
            message,
        )
        return {"positive_signals": [], "exclusions": []}
    raw = _strip_fences(raw)

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Failed to parse keyword generation response: %s", raw[:200])
        return {"positive_signals": [], "exclusions": []}

    return {
        "positive_signals": [
            str(k).lower().strip()
            for k in result.get("positive_signals", [])
            if isinstance(k, str) and k.strip()
        ],
        "exclusions": [
            str(k).lower().strip()
            for k in result.get("exclusions", [])
            if isinstance(k, str) and k.strip()
        ],
    }
