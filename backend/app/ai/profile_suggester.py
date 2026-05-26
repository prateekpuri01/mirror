"""LLM-based suggestions for profile sections (domains, skills, target roles)."""

import json
import logging

from app.ai.client import EXTRACTION_MODEL, get_openai_client
from app.ai.keyword_generator import _strip_fences

logger = logging.getLogger(__name__)

_PROMPTS = {
    "domains": """\
You are a career profile assistant. Given the user's professional background, \
suggest professional domains they operate in.

Output valid JSON: {"domains": ["domain1", "domain2", ...]}

Rules:
- 5-12 domains, ordered by relevance
- Short phrases (1-4 words), title case
- Focus on areas where the user has demonstrated expertise, not aspirational ones
- Examples: "AI/ML Systems", "National Security Policy", "Data Science", "Science Communication"
- If the user already has domains listed, keep ones that are well-supported and add any missing ones
- Output ONLY valid JSON, no markdown fences""",
    "skills": """\
You are a career profile assistant. Given the user's professional background, \
suggest skills organized into three categories.

Output valid JSON:
{
  "technical": ["skill1", "skill2", ...],
  "communication": ["skill1", "skill2", ...],
  "tools": ["tool1", "tool2", ...]
}

Rules:
- technical: 10-20 skills evidenced by their work (e.g. "machine learning", "NLP", "Bayesian statistics")
- communication: 3-8 skills (e.g. "technical writing", "stakeholder presentations", "science communication")
- tools: 5-15 specific technologies, languages, or platforms (e.g. "Python", "PyTorch", "PostgreSQL", "Docker")
- Only include skills actually evidenced by the user's accomplishments, publications, and work
- If the user already has skills listed, keep well-supported ones and add missing ones
- Output ONLY valid JSON, no markdown fences""",
    "target_roles": """\
You are a career profile assistant. Given the user's professional background, \
suggest target job roles they would be competitive for.

Output valid JSON: {"target_roles": [{"title": "Role Title", "seniority": "senior"}, ...]}

Rules:
- 4-8 roles, ordered by fit
- seniority must be one of: junior, mid, senior, staff, principal, lead, director
- Base suggestions on what they've actually done, not just what they know
- Include both exact-match roles and stretch roles
- Consider both tech and policy/research tracks if their background spans both
- If the user already has target roles listed, keep well-supported ones and add missing ones
- Output ONLY valid JSON, no markdown fences""",
}


def _build_suggestion_context(profile_data: dict) -> str:
    """Build a compact profile context for the LLM.

    Reads whatever exists, skips what doesn't. No branching.
    Order: LinkedIn (richest single source) > work_history > accomplishments >
           publications > education > existing section values.
    """
    lines = []

    # 1. LinkedIn cache — richest holistic source when available
    linkedin_cache = profile_data.get("linkedin_cache", {})
    raw_text = linkedin_cache.get("raw_text", "")
    if raw_text:
        lines.append("LinkedIn profile (self-described):")
        lines.append(raw_text[:4000])
        lines.append("")

    # 2. Work history — titles + employers + dates
    wh = profile_data.get("work_history", [])
    if wh:
        lines.append("Work history:")
        for w in wh:
            end = w.get("end") or "present"
            lines.append(
                f"  - {w.get('employer', '')} — {w.get('title', '')} ({w.get('start', '')}-{end})"
            )
        lines.append("")

    # 3. Top 10 accomplishments — impact_summary + metrics only
    complete = profile_data.get("complete_profile", {})
    accs = complete.get("accomplishments", [])
    if accs:
        top = sorted(accs, key=lambda a: a.get("relevance_weight", 0), reverse=True)[:10]
        lines.append(f"Top accomplishments ({len(accs)} total, showing top 10):")
        for a in top:
            lines.append(f"  - [{a.get('employer', '')}] {a.get('title', '')}")
            if a.get("impact_summary"):
                summary = a["impact_summary"].strip().replace("\n", " ")[:200]
                lines.append(f"    {summary}")
            quants = a.get("quantitative_specifics", [])
            if quants:
                lines.append(f"    Metrics: {'; '.join(quants[:3])}")
        lines.append("")

    # 4. Publications — titles + venues + first_author flag
    pubs = complete.get("publications", [])
    if pubs:
        lines.append(f"Publications ({len(pubs)} total):")
        for p in pubs:
            fa = " [first author]" if p.get("first_author") else ""
            lines.append(
                f'  - "{p.get("title", "")}" ({p.get("venue", "")}, {p.get("year", "")}){fa}'
            )
        lines.append("")

    # 5. Education
    education = profile_data.get("education", [])
    if education:
        lines.append("Education:")
        for edu in education:
            honors = f" — {edu['honors']}" if edu.get("honors") else ""
            lines.append(
                f"  - {edu.get('degree', '')} {edu.get('field', '')}, "
                f"{edu.get('institution', '')} ({edu.get('year', '')}){honors}"
            )
        lines.append("")

    # 6. Existing values for augmentation context
    existing_skills = profile_data.get("skills", {})
    if any(v for v in existing_skills.values() if v):
        lines.append(f"Current skills: {json.dumps(existing_skills)}")

    existing_domains = profile_data.get("domains", [])
    if existing_domains:
        lines.append(f"Current domains: {', '.join(existing_domains)}")

    existing_roles = profile_data.get("target_roles", [])
    if existing_roles:
        role_strs = [f"{r.get('title', '')} ({r.get('seniority', '')})" for r in existing_roles]
        lines.append(f"Current target roles: {', '.join(role_strs)}")

    return "\n".join(lines)


async def suggest_profile_section(section: str, profile_data: dict) -> dict:
    """Generate AI suggestions for a profile section.

    Args:
        section: One of "domains", "skills", "target_roles"
        profile_data: Full profile data dict (including complete_profile and linkedin_cache)

    Returns:
        Dict with the suggested data for the section.
    """
    if section not in _PROMPTS:
        return {"error": f"Unknown section: {section}"}

    context = _build_suggestion_context(profile_data)
    user_content = f"{context}\n\nGenerate suggestions for the {section} section."

    client = get_openai_client()
    response = await client.chat.completions.create(
        model=EXTRACTION_MODEL,
        messages=[
            {"role": "system", "content": _PROMPTS[section]},
            {"role": "user", "content": user_content},
        ],
        max_completion_tokens=2000,
    )

    raw = response.choices[0].message.content or ""
    raw = _strip_fences(raw)

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Failed to parse %s suggestions: %s", section, raw[:200])
        return {"error": "Failed to parse AI response"}

    return result
