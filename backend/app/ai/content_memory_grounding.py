"""Format ``content_memory`` rows into a grounding block for prompts.

Each leaf generation prompt (research entry, skill bucket, bullet set,
summary/tagline) appends one of these blocks. The block lists the user's
past hand-tuned versions for the relevant entity, with the job context they
were written for, so the LLM can match tone and phrasing without copying
verbatim.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.models import ContentMemory
from app.services import content_memory_service as svc

_HEADER = (
    "## Your past hand-tuned versions for this content\n"
    "Use these as grounding for tone, phrasing, and emphasis. Do NOT copy "
    "verbatim — adapt to the current job context."
)

_EMPTY = ""


def format_grounding_block(
    rows: Iterable[ContentMemory],
    *,
    profile_data: dict | None = None,
    label: str | None = None,
) -> str:
    """Render up to 3 example blocks for a single entity.

    ``label`` overrides the default heading (useful for multi-entity blocks).
    Returns the empty string if ``rows`` is empty (cold-start, zero cost).
    """
    rows = list(rows)
    if not rows:
        return _EMPTY

    parts = [label or _HEADER]
    for i, row in enumerate(rows[:3]):
        recency = "Most recent" if i == 0 else "Earlier"
        ctx = _format_job_context(row.job_context)
        stale = svc.is_stale(row, profile_data)
        stale_note = (
            "(note: underlying accomplishment text has since changed — treat as loose stylistic reference)\n"
            if stale
            else ""
        )
        body = _render_payload(row)
        parts.append(f"\n### {recency}{ctx}\n{stale_note}{body}")
    return "\n".join(parts)


def format_multi_entity_block(
    grouped: dict[str, list[ContentMemory]],
    *,
    profile_data: dict | None,
    title_for_key: dict[str, str] | None = None,
) -> str:
    """Render a single block covering multiple entities (e.g. all employer
    bullet sets). Each entity gets a sub-section with up to 3 prior versions.
    """
    if not grouped:
        return _EMPTY

    parts = [_HEADER]
    title_for_key = title_for_key or {}
    for key, rows in grouped.items():
        if not rows:
            continue
        title = title_for_key.get(key, key)
        parts.append(f"\n### {title}")
        for i, row in enumerate(rows[:3]):
            recency = "Most recent" if i == 0 else "Earlier"
            ctx = _format_job_context(row.job_context)
            stale = svc.is_stale(row, profile_data)
            stale_note = (
                "(note: underlying accomplishment text has since changed — "
                "treat as loose stylistic reference)\n"
                if stale
                else ""
            )
            body = _render_payload(row)
            parts.append(f"\n**{recency}{ctx}**\n{stale_note}{body}")
    return "\n".join(parts)


def _format_job_context(ctx: dict | None) -> str:
    if not ctx:
        return ""
    title = (ctx.get("job_title") or "").strip()
    company = (ctx.get("company_name") or "").strip()
    if title and company:
        return f" (written for: {title} @ {company})"
    if title or company:
        return f" (written for: {title or company})"
    return ""


def _render_payload(row: ContentMemory) -> str:
    if row.user_text:
        return row.user_text.strip()
    payload = row.user_payload_json
    if isinstance(payload, list):
        # bullet set — render as a list
        lines: list[str] = []
        for b in payload:
            if isinstance(b, dict):
                text = (b.get("text") or "").strip()
            else:
                text = str(b).strip()
            if text:
                lines.append(f"- {text}")
        return "\n".join(lines)
    return ""
