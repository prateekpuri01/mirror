"""Map resume-edit dotted paths to content_memory entities.

A user edit comes in as a path like ``experience.rand.bullets.2.text`` plus
the post-edit ``Document.content_json``. This module decides which content
memory entity (if any) the edit corresponds to and produces the row payload.
"""

from __future__ import annotations

from typing import Any


# Sentinel entity_key for entities that are unique per resume but have no
# natural sub-key (summary, tagline).
SCALAR_KEY = "__scalar__"


# Public entity_type constants
RESEARCH_DESCRIPTION = "research_description"
EXPERIENCE_BULLETS_SET = "experience_bullets_set"
SKILL_BUCKET = "skill_bucket"
SUMMARY = "summary"
TAGLINE = "tagline"


def path_to_entity(path: str, content_json: dict) -> dict | None:
    """Return entity descriptor for an edit path, or None if not memoized.

    The descriptor shape:
        {
            "entity_type": str,
            "entity_key": str,
            "user_text": str | None,
            "user_payload_json": Any | None,
            "accomplishment_ids": list[str],   # for hashing source text
        }

    None means "this path is not a content_memory entity" (e.g. publications,
    education, awards in v1).
    """
    parts = path.split(".")
    if not parts:
        return None
    head = parts[0]

    if head == "summary":
        return {
            "entity_type": SUMMARY,
            "entity_key": SCALAR_KEY,
            "user_text": _safe_str(content_json.get("summary")),
            "user_payload_json": None,
            "accomplishment_ids": [],
        }

    if head == "tagline":
        return {
            "entity_type": TAGLINE,
            "entity_key": SCALAR_KEY,
            "user_text": _safe_str(content_json.get("tagline")),
            "user_payload_json": None,
            "accomplishment_ids": [],
        }

    if head == "technical_skills" and len(parts) >= 2:
        bucket = parts[1]
        skills = content_json.get("technical_skills") or {}
        return {
            "entity_type": SKILL_BUCKET,
            "entity_key": bucket,
            "user_text": _safe_str(skills.get(bucket)),
            "user_payload_json": None,
            "accomplishment_ids": [],
        }

    # selected_research.{i}.description → memorize that one accomplishment's
    # description. Other sub-edits (category_label, title) are not memoized
    # — they're labels, not the user's authored content.
    if head == "selected_research" and len(parts) >= 3 and parts[2] == "description":
        try:
            idx = int(parts[1])
        except ValueError:
            return None
        research = content_json.get("selected_research") or []
        if not (0 <= idx < len(research)):
            return None
        entry = research[idx] or {}
        accomplishment_id = entry.get("accomplishment_id")
        description = entry.get("description")
        if not accomplishment_id or not isinstance(description, str):
            return None
        return {
            "entity_type": RESEARCH_DESCRIPTION,
            "entity_key": accomplishment_id,
            "user_text": description,
            "user_payload_json": None,
            "accomplishment_ids": [accomplishment_id],
        }

    # experience.{employer}.bullets[.*] → memorize the FULL final bullet array
    # for that employer, regardless of which bullet (or what attribute) was
    # touched. Reorders, additions, and deletions all collapse to the same
    # row for the same employer + source doc.
    if head == "experience" and len(parts) >= 3 and parts[2] == "bullets":
        employer = parts[1]
        experience = content_json.get("experience") or {}
        emp_block = experience.get(employer) or {}
        bullets = emp_block.get("bullets")
        if not isinstance(bullets, list):
            return None
        # Collect every accomplishment_id referenced anywhere in the set
        # (deduped, order-stable) — used downstream for source-text hashing.
        seen: set[str] = set()
        acc_ids: list[str] = []
        for b in bullets:
            if isinstance(b, dict):
                for aid in b.get("accomplishment_ids") or []:
                    if isinstance(aid, str) and aid not in seen:
                        seen.add(aid)
                        acc_ids.append(aid)
        return {
            "entity_type": EXPERIENCE_BULLETS_SET,
            "entity_key": employer,
            "user_text": None,
            "user_payload_json": bullets,
            "accomplishment_ids": acc_ids,
        }

    return None


def _safe_str(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, str):
        return v
    return str(v)
