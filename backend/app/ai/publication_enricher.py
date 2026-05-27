"""Publication enrichment — deterministic normalization, no LLM.

Earlier versions of this module ran a per-paper LLM call to generate
``impact_summary``, ``skills_demonstrated``, ``so_what``,
``quantitative_specifics``, and ``relevance_weight`` fields. Two problems
with that design surfaced in real use:

1. **The LLM only had the abstract to work from.** Downstream consumers
   (resume generator, scoring agent) also see the abstract — they can
   re-derive any of those fields at the moment they actually matter,
   with strictly more context (the job they're targeting). Pre-computing
   them was duplicating speculation.

2. **It cost 28 LLM calls per academic onboarding (~5-7 minutes wall
   clock at 4-wide parallelism, ~$0.05 in tokens).** Effectively wasted
   spend on derived fields that downstream agents would re-derive anyway.

This module now does the work the LLM was previously approximating —
``first_author``, ``work_history_key``, ``relevance_weight``, ``type`` —
with simple deterministic heuristics. The abstract is passed through
unmodified from Semantic Scholar. Downstream LLMs do the rest.

For backward compat, the dead fields (``impact_summary``, ``so_what``,
``quantitative_specifics``, ``skills_demonstrated``) are emitted as
empty strings / empty lists so old DB rows and TypeScript types don't
need to change in lockstep.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime

logger = logging.getLogger(__name__)


def _parse_year(value) -> int | None:
    """Extract a 4-digit year from a string or int. ``None`` if unparseable."""
    if value is None:
        return None
    if isinstance(value, int) and 1900 < value < 2100:
        return value
    text = str(value).strip()
    if not text:
        return None
    # Prefix is fine — "2024-06" → 2024
    head = text[:4]
    try:
        n = int(head)
    except ValueError:
        return None
    return n if 1900 < n < 2100 else None


def _match_work_history_key(paper_year: int | None, work_history: list[dict]) -> str | None:
    """Return the work_history.key whose date range contains ``paper_year``.

    Falls back to ``None`` when the year is unparseable or no entry covers it.
    Picks the first match if multiple ranges overlap (work_history is ordered
    most-recent-first in the schema, so the first match is the latest tenure
    that covers the publication — usually right).
    """
    if paper_year is None:
        return None
    current_year = datetime.now().year
    for wh in work_history:
        start = _parse_year(wh.get("start"))
        end_raw = wh.get("end")
        end = current_year if end_raw is None or str(end_raw).lower() == "present" else _parse_year(end_raw)
        if start is None or end is None:
            continue
        if start <= paper_year <= end:
            key = wh.get("key") or wh.get("employer", "").lower().replace(" ", "_")
            return key or None
    return None


def _compute_relevance_weight(
    *,
    year: int | None,
    citation_count: int,
    first_author: bool,
) -> float:
    """Heuristic priority score in [0, 1] for selecting top pubs in scoring.

    Three factors that tend to track resume-relevance in practice:
      * recency — papers from the last 5 years rate higher
      * citation count (log-scaled so a single 5k-citation paper doesn't
        crush everything else)
      * first authorship — a strong signal that the user owned the work

    Replaces the LLM-generated ``relevance_weight`` from the prior design,
    which depended on stale "target roles" context computed at upload
    time rather than per-job-match time. This formula is more legible AND
    holds up better at job-match time because the scoring agent re-sees
    the abstract directly anyway.
    """
    current_year = datetime.now().year
    recency = 1.0
    if year is not None:
        years_old = max(0, current_year - year)
        # Decay: every year subtracts 0.05, floored at 0.4 for very old work
        recency = max(0.4, 1.0 - 0.05 * years_old)

    # log10(citations + 1) → 0 for 0 cites, 1 for 10, 2 for 100, ...
    cite_factor = 0.5 + 0.15 * math.log10(max(1, citation_count + 1))
    cite_factor = min(1.0, cite_factor)

    fa_bonus = 1.25 if first_author else 1.0

    score = recency * cite_factor * fa_bonus
    # Cap and floor to [0, 1] so the scoring agent's sort behaves predictably.
    return max(0.0, min(1.0, score))


def _slug_for_pub(title: str) -> str:
    """Stable, URL-safe id derived from the title."""
    head = (title or "untitled")[:40].lower()
    # Replace anything non-alphanumeric with hyphens, collapse runs, strip ends.
    out: list[str] = []
    prev_dash = False
    for ch in head:
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        elif not prev_dash:
            out.append("-")
            prev_dash = True
    slug = "".join(out).strip("-")
    return f"pub-auto-{slug or 'untitled'}"


def _infer_type(paper_metadata: dict) -> str:
    """Use Semantic Scholar's publicationTypes if present, otherwise infer from venue."""
    pre_normalized = paper_metadata.get("type")
    if pre_normalized:
        return pre_normalized
    venue = (paper_metadata.get("venue") or "").lower()
    if "arxiv" in venue or "preprint" in venue:
        return "preprint"
    if any(kw in venue for kw in ("conf", "proceedings", "workshop", "symp")):
        return "conference"
    return "journal"


async def enrich_publication(paper_metadata: dict, profile_data: dict) -> dict:
    """Deterministic normalization of a Semantic Scholar paper into a
    ProfilePublication dict. Kept as ``async`` so the streaming pipeline's
    ``await`` callsites don't need to change.

    Args:
        paper_metadata: Normalized paper dict from
            ``publication_lookup._normalize_paper``.
        profile_data: User profile (used only for first-author detection
            and work-history date matching — no LLM call).

    Returns:
        ProfilePublication-shaped dict with ``auto_populated: true``.
        Deprecated narrative fields are present as empty placeholders
        so downstream code (and old TypeScript types) keeps working.
    """
    authors = paper_metadata.get("authors") or []
    user_name = (profile_data.get("personal") or {}).get("name") or ""
    first_author = bool(
        authors and user_name and user_name.lower() in (authors[0] or "").lower()
    )

    year = _parse_year(paper_metadata.get("year"))
    citation_count = int(paper_metadata.get("citation_count") or 0)

    work_history_key = _match_work_history_key(
        year, profile_data.get("work_history") or []
    )

    relevance_weight = _compute_relevance_weight(
        year=year,
        citation_count=citation_count,
        first_author=first_author,
    )

    title = paper_metadata.get("title") or ""

    return {
        "id": _slug_for_pub(title),
        "title": title,
        "authors": authors,
        "venue": paper_metadata.get("venue") or "",
        "year": paper_metadata.get("year") or "",
        "type": _infer_type(paper_metadata),
        "url": paper_metadata.get("url"),
        "doi": paper_metadata.get("doi"),
        "abstract": paper_metadata.get("abstract") or "",
        "citation_count": citation_count,
        "first_author": first_author,
        "relevance_weight": relevance_weight,
        "work_history_key": work_history_key,
        # Deprecated narrative fields — kept as empty placeholders so
        # existing TypeScript types and old DB rows keep loading. The
        # downstream resume + scoring agents now read ``abstract``
        # directly instead of these LLM-derived paraphrases.
        "impact_summary": "",
        "so_what": "",
        "quantitative_specifics": [],
        "skills_demonstrated": [],
        "auto_populated": True,
    }
