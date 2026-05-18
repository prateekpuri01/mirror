"""Utilities for working with the dynamic skills dict.

Skills are stored as {"category_name": ["skill1", "skill2"], ...}.
Default categories are Technical, Communication, Tools — but users
can add/rename/remove categories freely.
"""


def flatten_skills(skills: dict) -> list[str]:
    """Return all skills as a flat list, regardless of category."""
    result = []
    for category_skills in skills.values():
        if isinstance(category_skills, list):
            result.extend(category_skills)
    return result


def format_skills_for_prompt(skills: dict) -> str:
    """Format skills by category for LLM prompts."""
    lines = []
    for category, category_skills in skills.items():
        if isinstance(category_skills, list) and category_skills:
            label = category.replace("_", " ").title()
            lines.append(f"{label}: {', '.join(category_skills)}")
    return "\n".join(lines)
