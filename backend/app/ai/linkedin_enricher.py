"""LLM-based accomplishment generation from LinkedIn profile text."""

import json
import logging

from app.ai.client import EXTRACTION_MODEL, get_openai_client
from app.ai.keyword_generator import _strip_fences

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a career profile assistant. Given scraped LinkedIn profile text and the user's \
existing professional profile, generate draft accomplishment entries for positions that \
are not yet covered.

Your output must be a JSON array of accomplishment objects:
[
  {
    "id": "linkedin-<employer_slug>-<short_title_slug>",
    "title": "Short project/accomplishment title",
    "employer": "Company Name",
    "work_history_key": "employer_slug from work history",
    "date_range": "2020-2022",
    "impact_summary": "1-3 sentences describing what was done and why it mattered",
    "quantitative_specifics": ["metric1", "metric2"],
    "so_what": "Why this matters for target roles",
    "skills_demonstrated": ["skill1", "skill2"],
    "relevance_weight": 0.5,
    "related_publication_ids": []
  }
]

Rules:
- Parse experience entries from the LinkedIn text
- Match each to a work_history_key from the user's work history
- SKIP entries that duplicate existing accomplishments (compare employer + title + dates)
- Frame impact_summary through the user's skill lens
- If a LinkedIn entry mentions a project matching a known publication, include its ID in related_publication_ids
- skills_demonstrated: Only use skills from the user's actual skill list
- Generate 1-3 accomplishments per position, focusing on the most impactful work
- Set relevance_weight based on alignment with target roles
- Output ONLY valid JSON array, no markdown fences"""


async def generate_accomplishments_from_linkedin(
    linkedin_text: str, profile_data: dict
) -> list[dict]:
    """Generate draft accomplishments from LinkedIn profile text.

    Args:
        linkedin_text: Raw text scraped from LinkedIn profile.
        profile_data: Full user profile (work_history, skills, accomplishments, publications).

    Returns:
        List of draft accomplishment dicts with auto_populated=True, source="linkedin".
    """
    # Build context about what already exists
    complete = profile_data.get("complete_profile", {})
    existing_accs = complete.get("accomplishments", [])
    existing_pubs = complete.get("publications", [])

    existing_summary = "\n".join(
        f"- [{a.get('employer', '')}] {a.get('title', '')} ({a.get('date_range', '')})"
        for a in existing_accs
    )

    pub_summary = "\n".join(
        f"- id={p.get('id', '')}: {p.get('title', '')} ({p.get('year', '')})"
        for p in existing_pubs
    )

    from app.ai.skill_utils import flatten_skills
    all_skills = flatten_skills(profile_data.get("skills", {}))
    target_roles = [r.get("title", "") for r in profile_data.get("target_roles", [])]
    work_history = profile_data.get("work_history", [])
    wh_context = "\n".join(
        f"- key={wh.get('key', '')}: {wh.get('employer', '')} — {wh.get('title', '')} ({wh.get('start', '')}-{wh.get('end', 'present')})"
        for wh in work_history
    )

    user_content = f"""LinkedIn profile text:
---
{linkedin_text[:10000]}
---

User's work history (use these keys for work_history_key):
{wh_context}

User's skills: {', '.join(all_skills[:30])}
Target roles: {', '.join(target_roles)}

Existing accomplishments (DO NOT duplicate these):
{existing_summary}

Known publications (use these IDs for related_publication_ids if relevant):
{pub_summary}

Generate new accomplishment entries from the LinkedIn text that are NOT already covered above."""

    client = get_openai_client()
    response = await client.chat.completions.create(
        model=EXTRACTION_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        max_completion_tokens=4000,
    )

    raw = response.choices[0].message.content or ""
    raw = _strip_fences(raw)

    try:
        accomplishments = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Failed to parse LinkedIn accomplishments: %s", raw[:200])
        return []

    if not isinstance(accomplishments, list):
        return []

    # Tag each as auto-populated from LinkedIn
    for acc in accomplishments:
        acc["auto_populated"] = True
        acc["source"] = "linkedin"

    return accomplishments
