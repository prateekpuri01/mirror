"""Ranking — hot search v2 scoring stack.

Replaces the v1 per-company LLM picker (``_pick_best_job_for_guidance`` in
``evaluation.py``) with a two-stage cross-company ranker:

    embed_batch  → cosine_top_k  → batched_llm_rerank

Stage 1 (embeddings + cosine) is cheap, runs over the entire job pool
(hundreds to thousands of jobs), and surfaces a top-K candidate set.

Stage 2 (LLM rerank) is the taste-critical step — gpt-5.4 scoring with
the rerank prompt, JSON output, structured around a 1-5 relevance scale.
Operates on top-K only (default K=20) so cost is bounded regardless of
pool size.

Both stages handle the with-guidance / no-guidance cases by composing
the query document from profile + guidance + reference-job context.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from dataclasses import dataclass
from typing import Sequence

from app.ai.client import SCORING_MODEL, get_openai_client

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# OpenAI's embeddings API caps inputs at 2048 per request. We chunk just
# under that to leave margin for retries.
_EMBED_BATCH_LIMIT = 2000

# text-embedding-3-small: 1536-d, $0.02/1M tokens. Cheap and fast.
# text-embedding-3-large would be better quality but ~6x cost; revisit
# only if cosine ordering shows quality issues against the rerank gate.
_EMBED_MODEL = "text-embedding-3-small"

# Rerank scale floors. Mirrors v1 PICKER_RELEVANCE_FLOOR (evaluation.py:2414).
# Strict floor 3 = "adjacent domain" or better.
# Tentative floor 2 = "tangentially related" — surfaced but flagged.
RERANK_STRICT_FLOOR = 3
RERANK_TENTATIVE_FLOOR = 2


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class RankedJob:
    """A job after stage-2 rerank.

    ``index`` is the position in the original input list passed to
    ``batched_llm_rerank`` — callers reconstruct the full job dict from
    their own pool using this index.
    """
    index: int
    relevance: int          # 1-5
    reason: str             # one-line explanation
    is_tentative: bool      # True iff relevance == RERANK_TENTATIVE_FLOOR


# ---------------------------------------------------------------------------
# Stage 1a — embeddings
# ---------------------------------------------------------------------------


async def embed_batch(texts: Sequence[str]) -> list[list[float]]:
    """Embed a batch of texts using ``text-embedding-3-small``.

    Chunks at the OpenAI 2048-input cap. Empty strings are replaced with
    a single space — OpenAI rejects truly empty input, and we want the
    output list to align positionally with the input.

    Returns a list of 1536-d vectors, one per input, in input order.
    """
    if not texts:
        return []
    client = get_openai_client()
    sanitized = [(t if t and t.strip() else " ") for t in texts]
    out: list[list[float]] = []
    for start in range(0, len(sanitized), _EMBED_BATCH_LIMIT):
        chunk = sanitized[start : start + _EMBED_BATCH_LIMIT]
        try:
            resp = await client.embeddings.create(
                model=_EMBED_MODEL, input=chunk,
            )
        except Exception as e:
            logger.warning(
                "embed_batch chunk %d-%d failed: %s — returning zero vectors for chunk",
                start, start + len(chunk), e,
            )
            # Return zero vectors so downstream cosine treats these as
            # orthogonal to everything (effective rank = bottom). Better
            # than aborting the whole rank.
            out.extend([[0.0] * 1536 for _ in chunk])
            continue
        # OpenAI guarantees response data is ordered by input index
        # (https://platform.openai.com/docs/api-reference/embeddings/create).
        out.extend([item.embedding for item in resp.data])
    return out


# ---------------------------------------------------------------------------
# Stage 1b — cosine ranking
# ---------------------------------------------------------------------------


def _norm(v: Sequence[float]) -> float:
    return math.sqrt(sum(x * x for x in v))


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity. Returns 0.0 if either vector is zero-norm."""
    if len(a) != len(b):
        raise ValueError(f"vector dim mismatch: {len(a)} vs {len(b)}")
    na = _norm(a)
    nb = _norm(b)
    if na == 0.0 or nb == 0.0:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (na * nb)


def cosine_top_k(
    query_emb: Sequence[float],
    doc_embs: Sequence[Sequence[float]],
    k: int,
) -> list[tuple[int, float]]:
    """Return ``[(doc_index, score), ...]`` sorted by score desc, truncated to k.

    Pure Python — fine up to ~5K docs × 1536-d. If we ever push past
    that, swap to numpy.dot.
    """
    if k <= 0 or not doc_embs:
        return []
    qn = _norm(query_emb)
    if qn == 0.0:
        return []
    scored: list[tuple[int, float]] = []
    for i, d in enumerate(doc_embs):
        dn = _norm(d)
        if dn == 0.0:
            continue
        dot = sum(x * y for x, y in zip(query_emb, d))
        scored.append((i, dot / (qn * dn)))
    scored.sort(key=lambda t: t[1], reverse=True)
    return scored[:k]


# ---------------------------------------------------------------------------
# Query / job document construction
# ---------------------------------------------------------------------------


def build_query_doc(
    *,
    guidance: str | None,
    profile_data: dict | None,
    reference_context: str | None = None,
) -> str:
    """Compose the text we embed as the "query" for cosine ranking.

    Branching matches the priority rules used in v1 query generation:
      1. If guidance is set, it leads.
      2. Else reference jobs (if any) become the implicit guidance.
      3. Else profile-only mode.

    Profile is always included as secondary context. Repeating tokens
    is intentional — embeddings reward term saturation up to a point.
    """
    parts: list[str] = []

    if guidance and guidance.strip():
        parts.append(f"Looking for: {guidance.strip()}")

    if reference_context and reference_context.strip() and reference_context != "(no reference jobs)":
        parts.append(reference_context.strip())

    if profile_data:
        roles = [r.get("title", "") for r in profile_data.get("target_roles", []) if r.get("title")]
        if roles:
            parts.append(f"Target roles: {', '.join(roles[:5])}")
        domains = profile_data.get("domains", [])
        if domains:
            parts.append(f"Domains: {', '.join(domains[:5])}")
        skills = profile_data.get("skills", {}).get("technical", [])
        if skills:
            parts.append(f"Technical skills: {', '.join(skills[:15])}")
        prefs = profile_data.get("search_preferences", {})
        looking_for = prefs.get("looking_for") or ""
        if looking_for:
            parts.append(f"Looking for: {looking_for}")
        not_looking_for = prefs.get("not_looking_for") or ""
        if not_looking_for:
            parts.append(f"Avoiding: {not_looking_for}")

    if not parts:
        # Last-ditch fallback so we don't embed an empty string.
        parts.append("software engineering role")

    return "\n".join(parts)


def build_job_doc(job: dict) -> str:
    """Compose the text we embed for a job. Title + company + first chunk of
    description carry the signal; longer descriptions get truncated to keep
    embedding cost in check.

    We deliberately include the company name so the embedding can pick up
    on company-domain signal (e.g. "Anthropic" → AI safety context).
    """
    parts: list[str] = []
    title = (job.get("title") or "").strip()
    if title:
        parts.append(title)
    company = (job.get("company") or job.get("company_name") or "").strip()
    if company:
        parts.append(f"at {company}")
    loc = (job.get("location") or "").strip()
    if loc:
        parts.append(f"({loc})")
    dept = (job.get("department") or "").strip()
    if dept:
        parts.append(f"[{dept}]")
    desc = (job.get("description") or job.get("description_html") or "").strip()
    if desc:
        # Strip basic HTML; this is good-enough for embedding signal.
        desc = re.sub(r"<[^>]+>", " ", desc)
        desc = re.sub(r"\s+", " ", desc)
        parts.append(desc[:600])
    return " ".join(parts) or "untitled role"


# ---------------------------------------------------------------------------
# Stage 2 — LLM rerank
# ---------------------------------------------------------------------------


_RERANK_SYSTEM = """\
You are a precise job-relevance scorer. Given a candidate's search criteria \
and a numbered list of jobs, score each job 1-5 on how well it matches.

RELEVANCE scale:
  1 = unrelated (e.g. "Office Manager" for an ML engineer search)
  2 = tangentially related (different field, transferable skills only)
  3 = adjacent domain (e.g. "ML Engineer" for a "data scientist" search)
  4 = good match (same role family, right domain)
  5 = excellent match (exactly what was asked for)

Be strict. Default to 2 if the role family is wrong, even if the company \
or technology overlaps. Reserve 4-5 for jobs that genuinely fit the \
search's primary intent.

If a job violates a HARD CONSTRAINT (e.g. "must be in San Francisco" but \
the job is NYC-only), score it 1 regardless of role fit.

Output ONLY valid JSON in this exact shape:
  {"results": [
    {"i": <1-indexed job number>, "r": <1-5 score>, "why": "<≤12 words>"},
    ...
  ]}

Include one object per input job, in input order. No prose, no markdown \
fences, no commentary outside the JSON object."""


# Few-shot examples are folded into the user prompt because we need
# them keyed to the specific scoring intent of THIS call (different
# searches imply different "what counts as adjacent" judgments). They
# go above the live job list.
_RERANK_FEWSHOT = """\
Example scoring for search "machine learning research engineer at an AI safety lab":

Jobs:
1. Research Engineer, Evals — Anthropic (San Francisco)
2. Software Engineer, ML Infrastructure — Anthropic (San Francisco)
3. Account Executive, Enterprise — Anthropic (Remote)
4. Senior Data Scientist — Stripe (San Francisco)

Output:
{"results": [
  {"i": 1, "r": 5, "why": "exact match: research eng on evals at safety lab"},
  {"i": 2, "r": 4, "why": "adjacent ML infra role at the right org"},
  {"i": 3, "r": 1, "why": "sales role, unrelated to research engineering"},
  {"i": 4, "r": 2, "why": "DS role at fintech, wrong domain for AI safety"}
]}
"""


def _strip_json_fences(text: str) -> str:
    """Trim accidental markdown fences around a JSON array. Newer models
    sometimes wrap output despite system-prompt instructions."""
    t = text.strip()
    if t.startswith("```"):
        # Drop first fence line + optional ``` at end.
        lines = t.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    return t


async def batched_llm_rerank(
    jobs: list[dict],
    guidance: str,
    *,
    locations: list[str] | None = None,
    min_salary: int | None = None,
    top_k: int = 20,
    strict_floor: int = RERANK_STRICT_FLOOR,
    tentative_floor: int = RERANK_TENTATIVE_FLOOR,
) -> list[RankedJob]:
    """Score every job in ``jobs`` with one LLM call, return those at or above
    the tentative floor sorted by relevance desc.

    ``jobs`` is expected to be the cosine top-K already (default ~20).
    Caller passes ``top_k`` to control max output size — we return at
    most ``top_k`` entries.

    Hard constraints (locations, min_salary) are folded into the prompt
    so the LLM can hard-1 anything that violates them. The cheap
    pre-filter via ``_job_passes_location_filter`` / ``_job_passes_salary_filter``
    should happen UPSTREAM of this call — this function is the LLM
    judgment layer.
    """
    if not jobs:
        return []
    if not guidance or not guidance.strip():
        guidance = "(no explicit search instruction — match the candidate's profile)"

    # Build the numbered job list. Keep it compact — every token in the
    # job list pushes the rerank latency up.
    job_lines: list[str] = []
    for idx, j in enumerate(jobs, start=1):
        title = (j.get("title") or "?").strip()
        company = (j.get("company") or j.get("company_name") or "").strip()
        loc = (j.get("location") or "").strip()
        dept = (j.get("department") or "").strip()
        parts = [f"{idx}. {title}"]
        if company:
            parts.append(f"— {company}")
        if loc:
            parts.append(f"({loc})")
        if dept:
            parts.append(f"[{dept}]")
        # Optional one-line snippet for ambiguous titles. Cap to keep
        # the prompt small.
        snippet = (j.get("description") or j.get("description_html") or "")
        snippet = re.sub(r"<[^>]+>", " ", snippet)
        snippet = re.sub(r"\s+", " ", snippet).strip()
        if snippet and len(title) < 40:
            parts.append(f"— {snippet[:120]}")
        job_lines.append(" ".join(parts))

    constraints: list[str] = []
    if locations:
        constraints.append(f"MUST be located in one of: {', '.join(locations)}")
    if min_salary:
        constraints.append(f"MUST pay at least ${min_salary:,}")

    constraint_block = ""
    if constraints:
        constraint_block = "HARD CONSTRAINTS — score 1 if violated:\n" + "\n".join(
            f"  - {c}" for c in constraints
        ) + "\n\n"

    user_prompt = (
        f"Search criteria: {guidance.strip()}\n\n"
        f"{constraint_block}"
        f"{_RERANK_FEWSHOT}\n"
        f"---\n\n"
        f"Now score these {len(jobs)} jobs (1-indexed). Return JSON array, "
        f"one object per job in input order.\n\n"
        f"Jobs:\n" + "\n".join(job_lines)
    )

    client = get_openai_client()
    try:
        resp = await client.chat.completions.create(
            model=SCORING_MODEL,
            messages=[
                {"role": "system", "content": _RERANK_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            # Generous budget — gpt-5 family can burn completion tokens on
            # hidden reasoning before emitting output. For 20 jobs the
            # actual JSON is ~1200 tokens; we leave headroom.
            max_completion_tokens=8000,
        )
    except Exception:
        logger.exception("batched_llm_rerank API call failed; returning empty rank")
        return []

    raw = (resp.choices[0].message.content or "").strip()
    raw = _strip_json_fences(raw)
    if not raw:
        logger.warning(
            "batched_llm_rerank returned empty content (finish_reason=%s)",
            getattr(resp.choices[0], "finish_reason", "?"),
        )
        return []

    # The model occasionally wraps the array in an outer object when
    # response_format=json_object is forced. Handle both shapes.
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Try to find the first JSON array in the string as a last resort.
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if not m:
            logger.warning("batched_llm_rerank: could not parse JSON from %r", raw[:200])
            return []
        try:
            parsed = json.loads(m.group(0))
        except json.JSONDecodeError:
            logger.warning("batched_llm_rerank: re-parse also failed for %r", raw[:200])
            return []

    if isinstance(parsed, dict):
        # Look for the array under common keys.
        unwrapped = None
        for key in ("results", "rankings", "jobs", "scores", "data", "ranked"):
            if key in parsed and isinstance(parsed[key], list):
                unwrapped = parsed[key]
                break
        if unwrapped is not None:
            parsed = unwrapped
        elif "i" in parsed and "r" in parsed:
            # response_format=json_object forces an outer object. When
            # the rerank scored only one job, the model emits the entry
            # at the top level instead of inside a list. Treat the dict
            # as a single-element list.
            parsed = [parsed]
        else:
            logger.warning(
                "batched_llm_rerank: unexpected dict shape, keys=%s",
                list(parsed.keys())[:5],
            )
            return []

    if not isinstance(parsed, list):
        logger.warning("batched_llm_rerank: parsed is not a list (%s)", type(parsed).__name__)
        return []

    ranked: list[RankedJob] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        try:
            i = int(item.get("i", 0))
            r = int(item.get("r", 0))
        except (TypeError, ValueError):
            continue
        # Convert 1-indexed prompt index to 0-indexed list position.
        list_idx = i - 1
        if list_idx < 0 or list_idx >= len(jobs):
            continue
        if r < tentative_floor:
            continue
        ranked.append(RankedJob(
            index=list_idx,
            relevance=r,
            reason=str(item.get("why", ""))[:200],
            is_tentative=(r == tentative_floor),
        ))

    # Sort by relevance desc; stable so ties preserve LLM's order.
    ranked.sort(key=lambda x: x.relevance, reverse=True)
    return ranked[:top_k]


# ---------------------------------------------------------------------------
# High-level convenience — wraps the full pipeline for callers that just
# want "score this pool against this query and give me the top-K".
# ---------------------------------------------------------------------------


async def rank_jobs(
    jobs: list[dict],
    *,
    guidance: str | None,
    profile_data: dict | None,
    reference_context: str | None = None,
    locations: list[str] | None = None,
    min_salary: int | None = None,
    top_k: int = 20,
    cosine_pool_size: int = 100,
) -> list[tuple[dict, RankedJob]]:
    """Full ranking pipeline: build query doc → embed everything → cosine
    top-N → LLM rerank → return.

    ``cosine_pool_size`` is the size of the candidate set fed to the LLM
    rerank. ~5x ``top_k`` is the heuristic — broad enough to surface
    jobs whose embeddings under-represent their relevance, narrow enough
    to keep the LLM call bounded.

    Returns a list of ``(original_job_dict, RankedJob)`` tuples, sorted
    by ``RankedJob.relevance`` descending, capped at ``top_k``.
    """
    if not jobs:
        return []

    query_doc = build_query_doc(
        guidance=guidance,
        profile_data=profile_data,
        reference_context=reference_context,
    )
    job_docs = [build_job_doc(j) for j in jobs]

    # One batched embeddings call carries both query and all jobs.
    all_embs = await embed_batch([query_doc, *job_docs])
    if not all_embs or len(all_embs) != len(jobs) + 1:
        logger.warning(
            "rank_jobs: embedding count mismatch (got %d expected %d) — empty result",
            len(all_embs), len(jobs) + 1,
        )
        return []
    query_emb = all_embs[0]
    doc_embs = all_embs[1:]

    top = cosine_top_k(query_emb, doc_embs, k=cosine_pool_size)
    if not top:
        return []
    rerank_input = [jobs[i] for i, _ in top]

    ranked = await batched_llm_rerank(
        rerank_input,
        guidance=guidance or "",
        locations=locations,
        min_salary=min_salary,
        top_k=top_k,
    )

    # Map RankedJob.index (0-indexed into rerank_input) back to the
    # original job dict.
    return [(rerank_input[r.index], r) for r in ranked]
