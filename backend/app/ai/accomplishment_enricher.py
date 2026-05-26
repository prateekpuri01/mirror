"""LLM enrichment for accomplishment derived fields."""

import json
import logging

from app.ai.client import EXTRACTION_MODEL, get_openai_client
from app.ai.keyword_generator import _strip_fences

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a career profile assistant. Given an accomplishment's core details and the user's \
professional profile, generate the derived fields that help with job matching and resume generation.

Your output must be valid JSON:
{
  "so_what": "1-2 sentences explaining why this accomplishment matters for the user's target roles",
  "skills_demonstrated": ["skill1", "skill2", ...],
  "relevance_weight": 0.0-1.0,
  "tags": ["tag1", "tag2", ...]
}

Rules:
- so_what: Frame through the lens of the user's target roles. Why would a hiring manager care?
- skills_demonstrated: Only include skills from the user's actual skill list that are evidenced \
  by the impact summary and metrics. 4-8 skills max.
- relevance_weight: How relevant is this to the user's target roles? 0.9+ for core work, \
  0.5-0.8 for adjacent, below 0.5 for tangential.
- tags: 2-5 short lowercase tags for categorization (e.g. "ai-systems", "leadership", "nlp").
- Output ONLY valid JSON, no markdown fences."""


async def enrich_accomplishment(accomplishment: dict, profile_data: dict) -> dict:
    """Generate derived fields for an accomplishment.

    Args:
        accomplishment: The accomplishment dict (must have impact_summary at minimum).
        profile_data: Full user profile data.

    Returns:
        Dict with so_what, skills_demonstrated, relevance_weight, tags.
    """
    from app.ai.skill_utils import flatten_skills

    all_skills = flatten_skills(profile_data.get("skills", {}))
    target_roles = [r.get("title", "") for r in profile_data.get("target_roles", [])]
    domains = profile_data.get("domains", [])

    user_content = f"""Accomplishment:
Title: {accomplishment.get("title", "")}
Employer: {accomplishment.get("employer", "")}
Date: {accomplishment.get("date_range", "")}
Impact: {accomplishment.get("impact_summary", "")}
Metrics: {"; ".join(accomplishment.get("quantitative_specifics", []))}

User context:
Skills: {", ".join(all_skills[:30])}
Target roles: {", ".join(target_roles)}
Domains: {", ".join(domains)}

Generate the derived fields."""

    client = get_openai_client()
    response = await client.chat.completions.create(
        model=EXTRACTION_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        max_completion_tokens=1000,
    )

    raw = response.choices[0].message.content or ""
    raw = _strip_fences(raw)

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Failed to parse accomplishment enrichment: %s", raw[:200])
        return {}

    return {
        "so_what": result.get("so_what", ""),
        "skills_demonstrated": result.get("skills_demonstrated", []),
        "relevance_weight": result.get("relevance_weight", 0.5),
        "tags": result.get("tags", []),
    }
