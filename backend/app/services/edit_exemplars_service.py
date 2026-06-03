"""Service for the edit_exemplars convergence-record store.

Two responsibilities:
  1. ``upsert_from_edit`` — called from every user-facing edit hook (action
     card apply, edit_section commit, broad_rewrite per-section). Creates
     the row the first time a section is touched and bumps iteration_count
     on subsequent edits. Skips no-op edits (text unchanged after strip).
  2. ``retrieve_for_prompt`` — called from brainstorm and edit handlers.
     Returns top-K cosine matches across the global exemplar set + the most
     recent exemplars from the *current* job, dedup'd by (doc, section).

Embeddings are stored as JSONB lists of floats (mirrors the
``discovered_companies.description_embedding`` pattern) and computed via the
existing ``embed_batch`` helper that powers hot search.

The exemplar is "the gap between what the LLM tends to produce unprompted
and what the user actually wants" — that's the personalization signal. We
keep the *original* LLM output frozen and let *final* drift with the user's
iterations. Rejected brainstorm cards write nothing.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.edit_exemplars import EditExemplar
from app.services.hot_search.ranking import cosine_top_k, embed_batch

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Coarse entity classification — keeps similar-shape edits clustered.
# ---------------------------------------------------------------------------


def classify_entity(section_path: str) -> str:
    """Map a dotted section_path to a coarse entity_type label."""
    if section_path == "summary":
        return "summary"
    if section_path == "tagline":
        return "tagline"
    if section_path == "awards":
        return "awards"
    if section_path.startswith("technical_skills"):
        return "skills"
    if section_path.startswith("selected_research"):
        if section_path.endswith(".description"):
            return "research_description"
        if section_path.endswith(".title"):
            return "research_title"
        if section_path.endswith(".category_label"):
            return "research_category"
        return "research_entry"
    if section_path.startswith("experience.") and ".bullets" in section_path:
        if section_path.endswith(".bullets"):
            return "bullets_array"
        return "bullet"
    if section_path.startswith("publications"):
        return "publication"
    return "other"


def _stringify(value: Any) -> str:
    """Coerce a section value to a stable string form for storage / dedup."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(value)


def _embedding_text(instruction: str, entity_type: str) -> str:
    """Text we embed for similarity lookup. Includes the entity type as a
    soft bias toward same-shape passages."""
    inst = (instruction or "").strip()
    return f"[{entity_type}] {inst}" if inst else f"[{entity_type}]"


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------


async def upsert_from_edit(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    doc_id: uuid.UUID,
    section_path: str,
    before_value: Any,
    after_value: Any,
    instruction: str,
    source: str,
) -> EditExemplar | None:
    """Record a user-facing edit as an exemplar.

    First touch of a section: insert with ``original_llm_value=before``.
    Subsequent edits: keep the original frozen, update ``final_user_value``
    to the latest, append the instruction, re-embed.

    Returns the persisted row, or ``None`` if the edit was a no-op (final
    text identical to current final_user_value).
    """
    before_str = _stringify(before_value)
    after_str = _stringify(after_value)
    instruction = (instruction or "").strip()

    if after_str.strip() == before_str.strip():
        # True no-op — nothing changed. Don't touch the exemplar.
        return None

    entity_type = classify_entity(section_path)
    now_iso = datetime.now(timezone.utc).isoformat()
    new_instruction_entry = {"text": instruction, "source": source, "at": now_iso}

    # Embed the latest instruction. embed_batch returns a list; we want
    # the first vector. If embedding fails it returns a zero vector,
    # which cosine treats as orthogonal (effectively excluded from top-K).
    embed_input = _embedding_text(instruction, entity_type)
    try:
        embeddings = await embed_batch([embed_input])
        embedding = embeddings[0] if embeddings else None
    except Exception:
        logger.exception("edit_exemplars: embedding failed for path=%s", section_path)
        embedding = None

    existing = await session.execute(
        select(EditExemplar).where(
            EditExemplar.doc_id == doc_id,
            EditExemplar.section_path == section_path,
        )
    )
    row = existing.scalar_one_or_none()

    if row is None:
        row = EditExemplar(
            job_id=job_id,
            doc_id=doc_id,
            section_path=section_path,
            entity_type=entity_type,
            original_llm_value=before_str,
            final_user_value=after_str,
            iteration_count=1,
            instructions=[new_instruction_entry],
            source_first=source,
            instruction_embedding=embedding,
        )
        session.add(row)
    else:
        # No-op against the row's current state too (idempotent retries).
        if (row.final_user_value or "").strip() == after_str.strip():
            return None
        row.final_user_value = after_str
        row.iteration_count = (row.iteration_count or 0) + 1
        row.instructions = (row.instructions or []) + [new_instruction_entry]
        if embedding is not None:
            row.instruction_embedding = embedding

    await session.commit()
    await session.refresh(row)
    return row


# ---------------------------------------------------------------------------
# Read path
# ---------------------------------------------------------------------------


_DEFAULT_TOP_K = 12
_DEFAULT_RECENT_FROM_JOB = 8
# Skip exemplars whose stored values are this long or longer — keeps the
# injected block from blowing up the prompt.
_PER_VALUE_CHAR_CAP = 800


def _truncate(s: str, cap: int = _PER_VALUE_CHAR_CAP) -> str:
    if len(s) <= cap:
        return s
    return s[:cap].rstrip() + "…"


def _format_value_for_display(value: str, entity_type: str) -> str:
    """Make stored values readable in the prompt. JSON-encoded sections get
    decoded and re-rendered as plain text where possible."""
    raw = value or ""
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return _truncate(raw)
    if isinstance(parsed, dict):
        # Bullets and research entries — surface their prose-bearing fields.
        for key in ("text", "description"):
            if key in parsed:
                return _truncate(str(parsed[key]))
        return _truncate(raw)
    if isinstance(parsed, list):
        # Bullets array — render as a short list.
        lines = []
        for item in parsed[:6]:
            if isinstance(item, dict) and "text" in item:
                lines.append(f"- {item['text']}")
            else:
                lines.append(f"- {item}")
        return _truncate("\n".join(lines))
    return _truncate(str(parsed))


def _format_exemplar(row: EditExemplar) -> str:
    """Render one exemplar as a markdown block for prompt injection."""
    instructions = row.instructions or []
    latest = (
        instructions[-1].get("text", "") if instructions and isinstance(instructions[-1], dict) else ""
    )
    original = _format_value_for_display(row.original_llm_value, row.entity_type)
    final = _format_value_for_display(row.final_user_value, row.entity_type)
    iter_note = (
        f" (after {row.iteration_count} iterations)" if (row.iteration_count or 0) > 1 else ""
    )
    return (
        f"### {row.section_path} · instruction: \"{latest}\"\n"
        f"The model first produced:\n> {original}\n\n"
        f"You converged on{iter_note}:\n> {final}"
    )


async def retrieve_for_prompt(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    section_path: str | None,
    instruction: str,
    top_k: int = _DEFAULT_TOP_K,
    recent_from_job: int = _DEFAULT_RECENT_FROM_JOB,
) -> str:
    """Build the "How you've edited similar passages before" prompt block.

    Strategy:
      - Cosine top-K against the global exemplar pool, using the current
        instruction (and the inferred entity_type as a soft bias).
      - Plus N most-recent exemplars from the *current* job_id so in-job
        patterns are always visible regardless of similarity.
      - Dedup by (doc_id, section_path) so the same passage never shows up
        twice.
      - Returns empty string if there are no exemplars yet.
    """
    # Pull a working set. Bound it so we don't load the whole table on
    # large histories. 200 most-recent globally is plenty for cosine.
    result = await session.execute(
        select(EditExemplar)
        .order_by(desc(EditExemplar.updated_at))
        .limit(200)
    )
    rows: list[EditExemplar] = list(result.scalars().all())
    if not rows:
        return ""

    entity_type = classify_entity(section_path) if section_path else "other"
    query_text = _embedding_text(instruction or "", entity_type)
    try:
        query_emb = (await embed_batch([query_text]))[0]
    except Exception:
        logger.exception("edit_exemplars: failed to embed query")
        query_emb = None

    chosen: dict[tuple[uuid.UUID, str], EditExemplar] = {}

    # Cosine top-K
    if query_emb:
        with_embeds = [r for r in rows if r.instruction_embedding]
        if with_embeds:
            scored = cosine_top_k(
                query_emb,
                [r.instruction_embedding for r in with_embeds],
                top_k,
            )
            for idx, _score in scored:
                r = with_embeds[idx]
                chosen[(r.doc_id, r.section_path)] = r

    # Recent from current job
    in_job = [r for r in rows if r.job_id == job_id]
    for r in in_job[:recent_from_job]:
        chosen.setdefault((r.doc_id, r.section_path), r)

    if not chosen:
        return ""

    # Sort the final block: matches against the current section_path first,
    # then by recency. Helps the LLM scan the relevant ones first.
    ordered = sorted(
        chosen.values(),
        key=lambda r: (
            r.section_path != (section_path or ""),
            -(int(r.updated_at.timestamp()) if r.updated_at else 0),
        ),
    )

    header = (
        "## How you've edited similar passages before\n"
        "(The model's first draft vs. where you actually landed. Use this to "
        "match the user's voice — don't copy verbatim.)\n"
    )
    body = "\n\n".join(_format_exemplar(r) for r in ordered)
    return f"{header}\n{body}\n"


async def list_all_for_job(
    session: AsyncSession, job_id: uuid.UUID
) -> list[EditExemplar]:
    """Diagnostic helper — return every exemplar tied to a job."""
    result = await session.execute(
        select(EditExemplar)
        .where(EditExemplar.job_id == job_id)
        .order_by(desc(EditExemplar.updated_at))
    )
    return list(result.scalars().all())
