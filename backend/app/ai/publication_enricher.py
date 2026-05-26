"""LLM enrichment for publications with profile-aware descriptions."""

import json
import logging

from app.ai.client import EXTRACTION_MODEL, get_openai_client
from app.ai.keyword_generator import _strip_fences

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a career profile assistant. Given a publication's metadata and the user's \
professional profile, generate job-search-relevant descriptions.

Your output must be valid JSON with these fields:
{
  "impact_summary": "1-2 sentence description of what the user contributed, framed through their skill set",
  "so_what": "1 sentence explaining why this publication matters for the user's target roles",
  "skills_demonstrated": ["skill1", "skill2", ...],
  "quantitative_specifics": ["metric1", "metric2", ...],
  "relevance_weight": 0.0-1.0,
  "work_history_key": "best_guess_employer_key_or_null",
  "type": "journal|conference|report|tool|commentary"
}

Rules:
- impact_summary: Frame through the user's likely contribution angle. If the user is a \
  tech person, emphasize the data pipeline/system they built, not the survey design.
- skills_demonstrated: Only include skills that appear in the user's actual skill list \
  AND are relevant to the abstract. Do not invent skills.
- relevance_weight: Based on alignment with user's target roles and domains.
- work_history_key: Match to user's work history by publication date/venue/employer. \
  Use the employer key slugs provided. Return null if uncertain.
- Output ONLY valid JSON, no markdown fences."""


async def enrich_publication(paper_metadata: dict, profile_data: dict) -> dict:
    """Enrich a publication with profile-aware descriptions.

    Args:
        paper_metadata: Normalized paper dict from Semantic Scholar.
        profile_data: Full user profile data (skills, target_roles, work_history, etc.)

    Returns:
        Complete ProfilePublication dict with auto_populated: true.
    """
    # Build profile context
    from app.ai.skill_utils import flatten_skills

    all_skills = flatten_skills(profile_data.get("skills", {}))
    target_roles = [r.get("title", "") for r in profile_data.get("target_roles", [])]
    domains = profile_data.get("domains", [])
    work_history = profile_data.get("work_history", [])
    wh_context = [
        f"{wh.get('key', '')}: {wh.get('employer', '')} ({wh.get('start', '')}-{wh.get('end', 'present')})"
        for wh in work_history
    ]

    user_content = f"""Publication metadata:
Title: {paper_metadata.get("title", "")}
Authors: {", ".join(paper_metadata.get("authors", []))}
Venue: {paper_metadata.get("venue", "")}
Year: {paper_metadata.get("year", "")}
Abstract: {paper_metadata.get("abstract", "")[:500]}
Citation count: {paper_metadata.get("citation_count", "unknown")}

User profile context:
Skills: {", ".join(all_skills[:30])}
Target roles: {", ".join(target_roles)}
Domains: {", ".join(domains)}
Work history (key: employer, dates):
{chr(10).join(wh_context)}

Generate the enrichment JSON."""

    client = get_openai_client()
    response = await client.chat.completions.create(
        model=EXTRACTION_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        max_completion_tokens=2000,
    )

    raw = response.choices[0].message.content or ""
    raw = _strip_fences(raw)

    try:
        enrichment = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Failed to parse publication enrichment: %s", raw[:200])
        enrichment = {}

    # Generate a stable ID from the title
    title_slug = paper_metadata.get("title", "untitled")[:40].lower().replace(" ", "-").strip("-")
    pub_id = f"pub-auto-{title_slug}"

    # Determine first_author status
    authors = paper_metadata.get("authors", [])
    user_name = profile_data.get("personal", {}).get("name", "")
    first_author = bool(authors and user_name and user_name.lower() in authors[0].lower())

    return {
        "id": pub_id,
        "title": paper_metadata.get("title", ""),
        "authors": authors,
        "venue": paper_metadata.get("venue", ""),
        "year": paper_metadata.get("year", ""),
        "type": enrichment.get("type", paper_metadata.get("type", "")),
        "url": paper_metadata.get("url"),
        "doi": paper_metadata.get("doi"),
        "abstract": paper_metadata.get("abstract", ""),
        "first_author": first_author,
        "impact_summary": enrichment.get("impact_summary", ""),
        "so_what": enrichment.get("so_what", ""),
        "quantitative_specifics": enrichment.get("quantitative_specifics", []),
        "skills_demonstrated": enrichment.get("skills_demonstrated", []),
        "relevance_weight": enrichment.get("relevance_weight", 0.5),
        "work_history_key": enrichment.get("work_history_key"),
        "auto_populated": True,
    }
