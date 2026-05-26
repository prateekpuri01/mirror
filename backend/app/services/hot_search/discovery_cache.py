"""Discovery cache service.

Persists companies surfaced by hot search v2 so they can be recalled on
subsequent runs without re-paying the LLM-web discovery cost. Recall is
cosine-similarity over stored description embeddings against the
current search's query embedding.

Key operations:
  - ``upsert_company``         : insert or update a row keyed on
                                  normalized_name. Bumps counters,
                                  records last_query / last_status,
                                  fills in missing resolution fields.
  - ``recall_relevant``         : returns top-K stored companies by
                                  cosine vs query_embedding, optionally
                                  filtered to "resolvable" rows.
  - ``mark_outcome``           : updates last_status + the matching
                                  counter for a (normalized_name,
                                  outcome) pair after a search ends.

Schema lives at ``app.models.discovered_companies.DiscoveredCompany``.
Pure pure-Python cosine — fine up to a few thousand rows; if the table
ever grows past that we switch to pgvector and an HNSW index.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from sqlalchemy import and_, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models.discovered_companies import DiscoveredCompany

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


_NORM_SUFFIXES = {
    "inc",
    "inc.",
    "incorporated",
    "llc",
    "l.l.c.",
    "ltd",
    "ltd.",
    "limited",
    "corp",
    "corp.",
    "corporation",
    "co",
    "co.",
    "company",
    "ag",
    "gmbh",
    "sa",
    "s.a.",
    "pte",
    "plc",
    "pbc",
}


def normalize_name(name: str) -> str:
    """Lowercase, strip punctuation, drop common legal suffixes.

    Used as the unique key for the discovery cache. Two visually
    different spellings ("Anthropic, PBC" vs "Anthropic") must collapse
    to the same key, but we don't want to be so aggressive that distinct
    companies collide ("Apollo Research" vs "Apollo.io").
    """
    s = (name or "").lower().strip()
    # Strip punctuation but keep word boundaries.
    s = re.sub(r"[^\w\s-]", " ", s)
    tokens = [t for t in s.split() if t]
    # Drop trailing legal suffixes only — keep mid-name "inc." like
    # "Inc Magazine" intact (unlikely company name, but safer).
    while tokens and tokens[-1] in _NORM_SUFFIXES:
        tokens.pop()
    return " ".join(tokens) or s


# ---------------------------------------------------------------------------
# Cosine (duplicated from ranking.py to keep this module dep-light;
# the math is short enough that the duplication is cheaper than the
# import coupling)
# ---------------------------------------------------------------------------


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    return dot / (na * nb)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def upsert_company(
    *,
    name: str,
    ats: str | None = None,
    slug: str | None = None,
    careers_url: str | None = None,
    website: str | None = None,
    description: str | None = None,
    description_embedding: list[float] | None = None,
    source: str = "unknown",
    last_query: str | None = None,
    last_status: str = "discovered",
    session: AsyncSession | None = None,
) -> None:
    """Insert-or-update a discovered company row by normalized name.

    On conflict (existing row):
      - bumps ``times_seen``, ``last_seen_at``, ``last_query``, ``last_status``,
        ``source`` (latest wins);
      - fills in resolution fields (``ats``, ``slug``, ``careers_url``,
        ``website``, ``description``, ``description_embedding``) ONLY
        if the existing row's value is NULL — never overwrites known
        data with new NULL.

    Pass ``session`` to participate in a caller's transaction; else
    opens a fresh ``async_session``.
    """
    norm = normalize_name(name)
    if not norm:
        return

    async def _do(s: AsyncSession) -> None:
        # COALESCE pattern: keep existing non-NULL values, fill NULLs from
        # the new values. Postgres-specific ``ON CONFLICT DO UPDATE``.
        stmt = pg_insert(DiscoveredCompany).values(
            normalized_name=norm,
            name=name,
            ats=ats,
            slug=slug,
            careers_url=careers_url,
            website=website,
            description=description,
            description_embedding=description_embedding,
            source=source or "unknown",
            last_query=last_query,
            last_status=last_status or "discovered",
            times_seen=1,
            times_matched=0,
            times_no_jobs=0,
            first_seen_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["normalized_name"],
            set_={
                "name": stmt.excluded.name,  # latest spelling wins
                "ats": func.coalesce(DiscoveredCompany.ats, stmt.excluded.ats),
                "slug": func.coalesce(DiscoveredCompany.slug, stmt.excluded.slug),
                "careers_url": func.coalesce(
                    DiscoveredCompany.careers_url, stmt.excluded.careers_url
                ),
                "website": func.coalesce(DiscoveredCompany.website, stmt.excluded.website),
                "description": func.coalesce(
                    DiscoveredCompany.description, stmt.excluded.description
                ),
                "description_embedding": func.coalesce(
                    DiscoveredCompany.description_embedding,
                    stmt.excluded.description_embedding,
                ),
                "source": stmt.excluded.source,
                "last_query": stmt.excluded.last_query,
                "last_status": stmt.excluded.last_status,
                "times_seen": DiscoveredCompany.times_seen + 1,
                "last_seen_at": stmt.excluded.last_seen_at,
            },
        )
        await s.execute(stmt)

    if session is not None:
        await _do(session)
    else:
        async with async_session() as s:
            await _do(s)
            await s.commit()


async def upsert_many(
    rows: Iterable[dict],
    *,
    session: AsyncSession | None = None,
) -> None:
    """Bulk variant of upsert_company. Each row is a dict matching the
    kwargs of ``upsert_company`` (sans ``session``). Performs one row at
    a time within a single transaction — Postgres's ON CONFLICT DO UPDATE
    doesn't compose well with multi-row INSERT when we need the
    coalesce-on-NULL semantics.
    """
    rows = list(rows)
    if not rows:
        return

    async def _do(s: AsyncSession) -> None:
        for row in rows:
            await upsert_company(session=s, **row)

    if session is not None:
        await _do(session)
    else:
        async with async_session() as s:
            await _do(s)
            await s.commit()


async def mark_outcome(
    normalized_names: list[str],
    outcome: str,
    *,
    session: AsyncSession | None = None,
) -> None:
    """Record the final per-company outcome from a search run.

    ``outcome`` is one of: ``hit``, ``no_jobs``, ``no_match``, ``error``.
    Updates ``last_status`` and bumps the matching counter (``times_matched``
    for hits, ``times_no_jobs`` for no_jobs).
    """
    if not normalized_names:
        return

    async def _do(s: AsyncSession) -> None:
        values: dict = {
            "last_status": outcome,
            "last_seen_at": datetime.now(UTC),
        }
        # Counter bump piggybacks on the same UPDATE so we don't need
        # a second roundtrip.
        if outcome == "hit":
            values["times_matched"] = DiscoveredCompany.times_matched + 1
        elif outcome == "no_jobs":
            values["times_no_jobs"] = DiscoveredCompany.times_no_jobs + 1
        await s.execute(
            update(DiscoveredCompany)
            .where(DiscoveredCompany.normalized_name.in_(normalized_names))
            .values(**values)
        )

    if session is not None:
        await _do(session)
    else:
        async with async_session() as s:
            await _do(s)
            await s.commit()


async def recall_relevant(
    query_embedding: list[float],
    *,
    k: int = 50,
    min_cosine: float = 0.45,
    only_resolved: bool = False,
    exclude_repeated_no_match: bool = True,
    session: AsyncSession | None = None,
) -> list[DiscoveredCompany]:
    """Return the top-K stored companies most cosine-similar to the
    query embedding.

    ``min_cosine`` is a floor — rows below it are skipped even if k
    isn't filled.

    ``only_resolved=True`` filters to rows that have at least one of
    (ats+slug, careers_url) populated — useful when the caller wants
    only companies it can scrape immediately.

    ``exclude_repeated_no_match=True`` drops rows that have been
    rejected as "no_match" three or more times — the embedding said
    "looks relevant" but the LLM rerank disagreed multiple times, so
    we stop polluting the candidate pool with them.

    Returns DiscoveredCompany ORM objects, sorted by cosine desc.
    Caller is expected to convert to CompanyCandidate for downstream
    use.
    """
    if not query_embedding:
        return []

    async def _do(s: AsyncSession) -> list[DiscoveredCompany]:
        stmt = select(DiscoveredCompany).where(DiscoveredCompany.description_embedding.is_not(None))
        if only_resolved:
            stmt = stmt.where(
                and_(
                    DiscoveredCompany.description_embedding.is_not(None),
                    # Either an ATS+slug or a careers URL.
                    (
                        (DiscoveredCompany.ats.is_not(None) & DiscoveredCompany.slug.is_not(None))
                        | DiscoveredCompany.careers_url.is_not(None)
                    ),
                )
            )
        if exclude_repeated_no_match:
            # Tolerate up to two prior no_match rejections, then mute.
            # Two-strikes-keep-trying gives us the chance to re-evaluate
            # with a different query before permanently dropping.
            stmt = stmt.where(
                ~and_(
                    DiscoveredCompany.last_status == "no_match",
                    DiscoveredCompany.times_matched == 0,
                    DiscoveredCompany.times_seen >= 3,
                )
            )

        result = await s.execute(stmt)
        rows = list(result.scalars().all())
        if not rows:
            return []

        # Cosine in Python. At a few thousand rows this is fine; if it
        # ever becomes the bottleneck, swap to pgvector + ivfflat.
        scored: list[tuple[float, DiscoveredCompany]] = []
        for row in rows:
            emb = row.description_embedding
            if not emb:
                continue
            sim = _cosine(query_embedding, emb)
            if sim < min_cosine:
                continue
            scored.append((sim, row))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [row for _sim, row in scored[:k]]

    if session is not None:
        return await _do(session)
    async with async_session() as s:
        return await _do(s)


async def count_cached() -> int:
    """Diagnostic — how big is the cache?"""
    async with async_session() as s:
        result = await s.execute(select(func.count()).select_from(DiscoveredCompany))
        return int(result.scalar() or 0)
