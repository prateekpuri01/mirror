"""Hot Jobs Search — main orchestration loop.

This is the highest-level entry point of the pipeline. The actual work
(query generation, candidate extraction, ATS scraping, picker, verifier,
drill strategies, etc.) lives in ``app.services.hot_company_search``;
this file only choreographs them and emits SSE events for the streaming
endpoint at ``app/routers/hot_search.py``.

The flow at a glance:
  Phase 0  Aggregator harvest (HN, Remotive, The Muse, Arbeitnow)
  Phase 1  LLM query generation (per iteration)
  Phase 2  Web discovery (Perplexity → SearXNG → Brave) per query
  Phase 3  Per-candidate evaluation:
           - tracked-company DB hit
           - LLM dedup
           - direct-URL fallback OR full ATS evaluation
  Phase 4  Funnel emission (in the `done` SSE event) so we can see where
           candidates dropped at every stage.

The funnel counters are the new instrumentation: every reject site
increments a named bucket. The `done` event carries the full counter,
so the frontend (and any analyst) can answer "where did the pipeline
lose 90 candidates?" without grepping logs.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from collections.abc import AsyncGenerator

import httpx

from app.ai.client import EXTRACTION_MODEL, get_openai_client
from app.config import settings
from app.scrapers.discovery_adapters import DISCOVERY_ADAPTERS
from app.services.hot_search.types import (
    CompanyCandidate,
    CompanyHit,
    SearchEvent,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Aggregator pre-filter — one batched LLM call to drop guidance-incompatible
# aggregator entries BEFORE they consume the direct-import budget.
# ---------------------------------------------------------------------------


async def _prefilter_aggregator_entries(
    entries: list,
    guidance: str,
    *,
    max_entries: int = 250,
) -> list:
    """Batch-LLM-filter aggregator harvest entries by guidance fit.

    Without this, the harvest dumps ~200 guidance-blind entries (Bank of
    America, Uber, generic GmbH consultancies, etc.) into the candidate
    pool, where they burn the 100-direct-import budget before any
    guidance-driven LLM-query candidate gets a chance to run.

    One ~$0.01 LLM call drops the off-topic majority, leaving only
    entries that plausibly match the search topic. Returns the original
    list unfiltered if guidance is empty or the LLM call fails — better
    to over-include than to swallow real matches on a parse error.
    """
    if not guidance or not entries:
        return entries

    # Cap input to bound LLM cost. The cap is generous because the prompt
    # itself is just (company — title) one-liners, which compress well.
    sample = entries[:max_entries]

    lines = []
    for i, e in enumerate(sample):
        company = (getattr(e, "company_name", "") or "Unknown").strip()
        title = (getattr(e, "job_title", "") or "unknown role").strip()
        # Truncate to keep the prompt compact
        if len(title) > 100:
            title = title[:97] + "..."
        lines.append(f"{i}. {company} — {title}")

    user_msg = (
        f"User is searching for jobs matching this guidance:\n\n"
        f"  {guidance.strip()}\n\n"
        f"Below are {len(sample)} candidate (company — job-title) pairs from "
        f"aggregator feeds. Return the indices of entries that are "
        f"PLAUSIBLY relevant to the guidance. Be generous — include anything "
        f"that could plausibly fit, even loosely. Exclude only entries that "
        f"clearly don't match (e.g. retail, hospitality, generic admin/sales "
        f"roles at unrelated companies, foreign-language consultancies in "
        f"unrelated fields).\n\n"
        + "\n".join(lines)
        + '\n\nReturn ONLY valid JSON: {"relevant": [<indices>]}'
    )

    try:
        client = get_openai_client()
        # `reasoning_effort=minimal` for gpt-5-family models: this is a
        # straightforward classification task, no chain-of-thought needed.
        # Without it (the previous default "medium"), the model consumed
        # all 3000 tokens of max_completion_tokens budget on reasoning
        # and returned EMPTY content — silently corrupting the pre-filter
        # and letting 100% of aggregator entries through to evaluation.
        # max bumped to 8000 as headroom even with minimal reasoning,
        # since 250 short entries can produce a long index list.
        response = await client.chat.completions.create(
            model=EXTRACTION_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You filter aggregator job listings by relevance to a user's "
                        "search guidance. Be SELECTIVE — when the user's guidance "
                        "names a clear domain (e.g. 'AI in healthcare'), keep only "
                        "entries plausibly in that domain and drop anything obviously "
                        "unrelated (retail, hospitality, finance unless fintech, "
                        "manufacturing, generic admin/sales/HR roles, foreign-language "
                        "consultancies in unrelated fields). Better to lose a borderline "
                        "match than flood the result list with off-topic noise. "
                        "Return ONLY JSON."
                    ),
                },
                {"role": "user", "content": user_msg},
            ],
            response_format={"type": "json_object"},
            max_completion_tokens=8000,
            reasoning_effort="minimal",
        )
        text = (response.choices[0].message.content or "").strip()
        if not text:
            # If the model still returned nothing, log it loudly instead
            # of silently falling back to no-filter (which was the prior
            # bug). The caller's except path will then fall back, but at
            # least we'll see this in the logs.
            logger.warning(
                "Aggregator pre-filter LLM returned empty content "
                "(finish_reason=%s, usage=%s) — falling back to unfiltered set",
                getattr(response.choices[0], "finish_reason", "?"),
                getattr(response, "usage", "?"),
            )
            return entries
        parsed = json.loads(text)
        indices = parsed.get("relevant", [])
        if not isinstance(indices, list):
            return entries  # malformed — fall through
        kept = {int(i) for i in indices if isinstance(i, (int, float))}
        filtered = [sample[i] for i in sorted(kept) if 0 <= i < len(sample)]
        # If the LLM was over-aggressive and dropped everything, fall back
        # to the original list — better to evaluate all than block the search.
        if not filtered and sample:
            logger.info(
                "Aggregator pre-filter returned 0 of %d entries — falling back to unfiltered set",
                len(sample),
            )
            return entries
        logger.info(
            "Aggregator pre-filter: %d → %d entries (%.0f%% dropped)",
            len(sample),
            len(filtered),
            (1 - len(filtered) / max(1, len(sample))) * 100,
        )
        return filtered
    except Exception:
        logger.exception("Aggregator pre-filter failed — using unfiltered set")
        return entries


async def run_hot_company_search(
    sources: list[str],
    guidance: str,
    max_hits: int = 20,
    # 3 iterations covers ~90% of hits in our eval data; the 4th and 5th
    # were producing diminishing returns at full LLM cost. Drop default
    # for faster runs; users can override via the request payload.
    max_iterations: int = 3,
    locations: list[str] | None = None,
    min_salary: int | None = None,
    reference_job_ids: list[str] | None = None,
    # 16 in flight at once (was 8). Per-candidate eval is mostly LLM and
    # HTTP wait time; OpenAI handles parallel just fine and Ashby's 429
    # retries already cope with bursts. The downstream semaphores in
    # rate_limits cap browser-pool and OpenAI concurrency separately so
    # this is just the per-search worker count. Doubling halved
    # round-1-completion time in testing.
    candidate_concurrency: int = 16,
    profile_fit_threshold: int = 50,
) -> AsyncGenerator[SearchEvent, None]:
    """Main search loop. Yields SearchEvent objects for SSE streaming.

    ``candidate_concurrency`` controls how many candidate companies are
    evaluated in parallel (the bottleneck is the per-candidate scrape +
    LLM-picker pipeline, which runs ~10-30s each sequentially). Default 4
    gives a 3-4× speedup over serial without overwhelming OpenAI rate
    limits or the Playwright browser pool. Fast checks (tracked-company
    DB query, LLM dedup) remain sequential.
    """
    # Local imports: pull each helper from its real module rather than the
    # backwards-compat shim. The shim still works for external callers but
    # there's no reason to route through it from inside the package.
    from app.services.company_discovery import _build_keyword_sets
    from app.services.hot_search.discovery import (
        _build_reference_context,
        _discovery_search,
        _enrich_keywords_from_references,
        _extract_candidates_from_results,
        _generate_queries,
        _get_existing_company_names,
        _harvest_candidates_from_entries,
        _load_profile_data,
        _load_reference_jobs,
    )
    from app.services.hot_search.evaluation import (
        _batch_check_duplicates,
        _evaluate_candidate,
        _evaluate_tracked_company,
        _extract_direct_job_url,
        _generate_hit_summary,
    )

    valid_sources = {"web", "greenhouse", "lever", "ashby"}
    sources = [s for s in sources if s in valid_sources]
    if not sources:
        yield SearchEvent("error", {"message": "No valid sources selected"})
        yield SearchEvent("done", {"total_hits": 0, "total_candidates_checked": 0})
        return

    # Validate at least one web search backend is reachable. The unified
    # web_search() will silently return empty if nothing is configured.
    from app.services.web_search_llm import native_web_search_supported

    if not (native_web_search_supported() or settings.searxng_url):
        yield SearchEvent(
            "error",
            {
                "message": (
                    "No web search backend configured. Configure an LLM provider "
                    "key (OPENAI_API_KEY or ANTHROPIC_API_KEY) for native web "
                    "search, or run a SearXNG instance and set SEARXNG_URL."
                ),
            },
        )
        yield SearchEvent("done", {"total_hits": 0, "total_candidates_checked": 0})
        return

    yield SearchEvent(
        "status",
        {
            "message": "Loading profile and existing companies...",
            "phase": "init",
            "iteration": 0,
            "total_queries": 0,
            "hits_so_far": 0,
        },
    )

    # Load context once
    profile_data = await _load_profile_data()
    profile_keywords = _build_keyword_sets(profile_data) if profile_data else {}
    existing_companies = await _get_existing_company_names()
    existing_lower = {n.lower() for n in existing_companies}

    # Load reference jobs and build context
    reference_jobs = await _load_reference_jobs(reference_job_ids or [])
    reference_context = _build_reference_context(reference_jobs)
    if reference_jobs:
        profile_keywords = _enrich_keywords_from_references(profile_keywords, reference_jobs)

    # Session state
    past_queries: list[str] = []
    evaluated: dict[str, str] = {}  # key → "hit" | "miss" | "failed"
    hits: list[CompanyHit] = []
    total_candidates = 0
    consecutive_dry = 0
    # Split the per-session direct-import budget by candidate origin so the
    # guidance-blind aggregator harvest can't crowd out guidance-driven LLM
    # queries — which was the root cause of "0 hits, AI-and-agriculture
    # companies discovered too late to be evaluated" complaints. Each origin
    # gets its own cap; the verifier still runs per-candidate so these are
    # cost ceilings, not quality gates.
    aggregator_direct_count = 0
    query_direct_count = 0
    _AGGREGATOR_DIRECT_CAP = 50
    _QUERY_DIRECT_CAP = 50

    # Tentative-tier hits (best job scored between loose and strict
    # threshold). Capped separately so exploratory searches always return
    # SOMETHING ("here are the closest matches I could find") without
    # noise flooding the result list.
    tentative_emitted = 0
    _MAX_TENTATIVE_HITS = 5

    # Global wall-clock budget. Hard cap so a single search can't grind for
    # hours when Ashby is rate-limiting hard or candidate eval pipelines
    # stall. At 8 minutes the user has time to refine guidance and try
    # again; without it, individual stuck candidates (180s timeout each)
    # multiplied across 3 iterations × ~30 candidates × bounded concurrency
    # produced 1.5-hour stalls in practice.
    import time

    _GLOBAL_BUDGET_S = 8 * 60
    start_time = time.monotonic()

    def _budget_remaining() -> float:
        return _GLOBAL_BUDGET_S - (time.monotonic() - start_time)

    def _budget_exceeded() -> bool:
        return _budget_remaining() <= 0

    # Funnel: count where candidates drop. Emitted in the `done` event so we
    # can see at a glance whether the bottleneck is dedup, ATS resolution,
    # the picker, the verifier, etc. — instead of guessing from logs.
    funnel: Counter = Counter()
    skip_reasons: Counter = Counter()

    async with httpx.AsyncClient(timeout=60.0) as http_client:
        # ---------------------------------------------------------------
        # Phase 0: aggregator harvest. Pull from public free job feeds
        # (HN Who Is Hiring, Remotive, The Muse, Arbeitnow) in parallel,
        # then map their entries → ATS slugs (or direct-URL fallback) so
        # they flow through the same evaluation pipeline as web-search
        # candidates. This is the high-leverage source-coverage pass —
        # one Remotive entry that links at boards.greenhouse.io/acme can
        # become a comprehensive Acme scrape.
        # ---------------------------------------------------------------
        seen_aggregator_keys: set[str] = set()
        seed_candidates: list[CompanyCandidate] = []
        try:
            yield SearchEvent(
                "status",
                {
                    "message": "Harvesting from aggregator feeds (HN, Remotive, Muse, Arbeitnow)...",
                    "phase": "harvesting",
                    "iteration": 0,
                    "total_queries": 0,
                    "hits_so_far": 0,
                },
            )
            adapter_tasks = [
                adapter.fetch_entries(http_client, guidance, locations or [], min_salary)
                for adapter in DISCOVERY_ADAPTERS
            ]
            adapter_results = await asyncio.gather(
                *adapter_tasks,
                return_exceptions=True,
            )
            all_entries: list = []
            for adapter, result in zip(DISCOVERY_ADAPTERS, adapter_results, strict=False):
                if isinstance(result, Exception):
                    logger.warning(
                        "Discovery adapter %s raised: %s",
                        adapter.source_name,
                        result,
                    )
                    continue
                logger.info(
                    "Discovery adapter %s yielded %d entries",
                    adapter.source_name,
                    len(result),
                )
                all_entries.extend(result)
            # Pre-filter the aggregator pool by guidance fit before
            # spending HTTP probes resolving them. One LLM call drops the
            # obviously-off-topic entries (e.g. "Bank of America: Wealth
            # Manager" against "AI and agriculture") so the direct-import
            # budget goes to entries that could actually match.
            pre_count = len(all_entries)
            if guidance:
                yield SearchEvent(
                    "status",
                    {
                        "message": (
                            f"Filtering {pre_count} aggregator entries against your guidance..."
                        ),
                        "phase": "filtering",
                        "iteration": 0,
                        "total_queries": 0,
                        "hits_so_far": 0,
                    },
                )
                all_entries = await _prefilter_aggregator_entries(
                    all_entries,
                    guidance,
                )
            yield SearchEvent(
                "status",
                {
                    "message": (
                        f"Resolving {len(all_entries)} of {pre_count} aggregator "
                        f"entries to ATS boards..."
                    ),
                    "phase": "harvesting",
                    "iteration": 0,
                    "total_queries": 0,
                    "hits_so_far": 0,
                },
            )
            seed_candidates = await _harvest_candidates_from_entries(
                all_entries,
                http_client,
                seen=seen_aggregator_keys,
                existing_companies_lower=existing_lower,
            )
            funnel["aggregator_entries"] = len(all_entries)
            funnel["seed_candidates"] = len(seed_candidates)
            logger.info(
                "Aggregator harvest: %d entries → %d candidates",
                len(all_entries),
                len(seed_candidates),
            )
            yield SearchEvent(
                "status",
                {
                    "message": (
                        f"Harvested {len(seed_candidates)} candidates from "
                        f"{len(all_entries)} aggregator entries — searching..."
                    ),
                    "phase": "harvested",
                    "iteration": 0,
                    "total_queries": 0,
                    "hits_so_far": 0,
                },
            )
        except Exception:
            logger.exception("Aggregator harvest failed; continuing without seed candidates")
            seed_candidates = []

        for iteration in range(max_iterations):
            if len(hits) >= max_hits:
                break
            if consecutive_dry >= 3:
                yield SearchEvent(
                    "status",
                    {
                        "message": "Stopping — 3 iterations with no new hits",
                        "phase": "stopping",
                        "iteration": iteration,
                        "total_queries": len(past_queries),
                        "hits_so_far": len(hits),
                    },
                )
                break
            if _budget_exceeded():
                yield SearchEvent(
                    "status",
                    {
                        "message": (
                            f"Stopping — {_GLOBAL_BUDGET_S // 60}-minute wall-clock "
                            f"budget hit (likely Ashby rate-limiting). Returning "
                            f"{len(hits)} companies found so far. Try narrower "
                            f"guidance or fewer reference jobs for a faster run."
                        ),
                        "phase": "stopping",
                        "iteration": iteration,
                        "total_queries": len(past_queries),
                        "hits_so_far": len(hits),
                        "budget_exhausted": True,
                    },
                )
                break

            yield SearchEvent(
                "status",
                {
                    "message": f"Generating search queries (round {iteration + 1}/{max_iterations})...",
                    "phase": "generating",
                    "iteration": iteration + 1,
                    "total_queries": len(past_queries),
                    "hits_so_far": len(hits),
                },
            )

            queries = await _generate_queries(
                guidance,
                profile_data,
                existing_companies,
                past_queries,
                evaluated,
                len(hits),
                max_hits,
                sources,
                locations=locations,
                min_salary=min_salary,
                reference_context=reference_context,
            )
            past_queries.extend(queries)
            # Surface every generated query at INFO so a tail of api logs
            # tells you exactly what the LLM searched on this iteration.
            # Without this, "why didn't my search find X?" required code
            # changes to investigate.
            logger.info(
                "Hot search round %d/%d generated %d queries: %s",
                iteration + 1,
                max_iterations,
                len(queries),
                [q[:120] for q in queries],
            )
            iteration_hits = 0

            for query in queries:
                if len(hits) >= max_hits:
                    break
                if _budget_exceeded():
                    # Inner break — outer loop check will emit the
                    # user-visible "Stopping" status on the next pass.
                    break

                yield SearchEvent(
                    "status",
                    {
                        "message": f"Searching: {query[:80]}...",
                        "phase": "searching",
                        "iteration": iteration + 1,
                        "total_queries": len(past_queries),
                        "hits_so_far": len(hits),
                    },
                )

                results = await _discovery_search(query, max_results=10)
                candidates = await _extract_candidates_from_results(results, query)

                # Prepend any aggregator-harvested candidates to the very
                # first batch so they flow through the same dedup + eval
                # pipeline as web-search results. We only do this once per
                # run — `seed_candidates` is cleared after consumption.
                if seed_candidates:
                    candidates = seed_candidates + candidates
                    seed_candidates = []

                # ------------------------------------------------------------
                # Phase 1 (sequential): fast per-candidate pre-processing
                #   - tracked-company check (DB read + LLM picker, ~3s)
                #   - dedup (now BATCHED — one LLM call for the whole query)
                #   - already-evaluated skip
                # Output: pending_evals = list of (candidate, norm_key, kind)
                #         for candidates that need the slow ATS-scrape pipeline.
                # ------------------------------------------------------------
                # Pre-batch the dedup LLM check across every candidate of this
                # query at once. Was previously a sequential per-candidate
                # call (~30 candidates × 1-2s each = ~45s of sequential LLM
                # round-trips per query). Now: one LLM call per query.
                all_known = list(existing_companies) + [h.name for h in hits]
                cand_names = [c.name for c in candidates if c.name]
                dup_map = await _batch_check_duplicates(cand_names, all_known)

                pending_evals: list[tuple[CompanyCandidate, str, str]] = []

                for candidate in candidates:
                    if len(hits) >= max_hits:
                        break

                    funnel["candidates_seen"] += 1

                    # Already-tracked companies: check DB for matching jobs
                    # first; fall through to regular eval only if nothing matches.
                    if candidate.name.lower() in existing_lower:
                        norm_key = candidate.name.lower()
                        if norm_key in evaluated:
                            funnel["already_checked"] += 1
                            yield SearchEvent(
                                "skip",
                                {
                                    "name": candidate.name,
                                    "source": candidate.source,
                                    "reason": "Already checked",
                                },
                            )
                            continue
                        total_candidates += 1
                        yield SearchEvent(
                            "candidate",
                            {
                                "name": candidate.name,
                                "source": candidate.source,
                            },
                        )
                        tracked_hit, _ = await _evaluate_tracked_company(
                            candidate.name,
                            profile_keywords,
                            locations=locations,
                            min_salary=min_salary,
                            guidance=guidance,
                        )
                        if tracked_hit:
                            evaluated[norm_key] = "hit"
                            hits.append(tracked_hit)
                            iteration_hits += 1
                            funnel["tracked_hit"] += 1
                            yield SearchEvent(
                                "hit",
                                {
                                    "name": tracked_hit.name,
                                    "ats": tracked_hit.ats,
                                    "slug": tracked_hit.slug,
                                    "website": tracked_hit.website,
                                    "total_jobs": tracked_hit.total_jobs,
                                    "relevant_jobs": tracked_hit.relevant_jobs,
                                    "top_jobs": tracked_hit.top_jobs,
                                    "source": tracked_hit.source,
                                    "description": tracked_hit.description,
                                    "match_reason": tracked_hit.match_reason,
                                    "kind": tracked_hit.kind,
                                    "company_id": tracked_hit.company_id,
                                },
                            )
                            continue
                        evaluated[norm_key] = "checking"
                        funnel["tracked_no_match"] += 1
                        # Fall through into regular eval pipeline

                    # Dedup: result already pre-computed in the batched
                    # call above. Note: we look up the original candidate
                    # name against `dup_map`, but `all_known` was captured
                    # before this loop started — duplicates created within
                    # the same query are detected at eval time, not here.
                    dup_of = dup_map.get(candidate.name)
                    if dup_of:
                        funnel["dedup_dropped"] += 1
                        yield SearchEvent(
                            "skip",
                            {
                                "name": candidate.name,
                                "source": candidate.source,
                                "reason": f"Duplicate of '{dup_of}'",
                            },
                        )
                        evaluated[candidate.name.lower()] = "miss"
                        continue

                    norm_key = candidate.name.lower()
                    if candidate.slug:
                        norm_key = f"{candidate.ats}:{candidate.slug}"
                    if norm_key in evaluated:
                        funnel["already_checked"] += 1
                        yield SearchEvent(
                            "skip",
                            {
                                "name": candidate.name,
                                "source": candidate.source,
                                "reason": "Already checked",
                            },
                        )
                        continue

                    total_candidates += 1
                    yield SearchEvent(
                        "candidate",
                        {
                            "name": candidate.name,
                            "source": candidate.source,
                        },
                    )

                    # Direct-URL branch: check the appropriate per-origin
                    # cap at queue time. Aggregator vs query candidates have
                    # separate budgets so a flood of guidance-blind
                    # aggregator entries can't starve guidance-driven LLM
                    # query candidates of evaluation slots.
                    if candidate.direct_job_url:
                        is_aggregator = candidate.origin == "aggregator"
                        if is_aggregator:
                            cap = _AGGREGATOR_DIRECT_CAP
                            used = aggregator_direct_count
                            cap_label = "aggregator"
                        else:
                            cap = _QUERY_DIRECT_CAP
                            used = query_direct_count
                            cap_label = "query"
                        if used >= cap:
                            evaluated[norm_key] = "miss"
                            funnel[f"direct_cap_reached_{cap_label}"] += 1
                            yield SearchEvent(
                                "skip",
                                {
                                    "name": candidate.name,
                                    "source": candidate.source,
                                    "reason": (f"Direct-import cap reached ({cap}/{cap_label})"),
                                },
                            )
                            continue
                        if is_aggregator:
                            aggregator_direct_count += 1
                        else:
                            query_direct_count += 1
                        pending_evals.append((candidate, norm_key, "direct"))
                    else:
                        pending_evals.append((candidate, norm_key, "full"))

                if not pending_evals:
                    await asyncio.sleep(0.5)
                    continue

                # Deduplicate within the batch — parallel tasks can't check
                # `evaluated` against each other, so strip duplicate norm_keys
                # before launching (e.g. 4 Mashgin URLs in the same batch).
                seen_keys: set[str] = set()
                deduped_evals: list[tuple] = []
                for item in pending_evals:
                    _, nk, _ = item
                    if nk not in seen_keys:
                        seen_keys.add(nk)
                        deduped_evals.append(item)
                pending_evals = deduped_evals

                # ------------------------------------------------------------
                # Phase 2 (parallel): run the slow eval pipeline for all
                # pending candidates with bounded concurrency. Yield hits
                # as they complete (not in submission order) so the user
                # sees results streaming in.
                # ------------------------------------------------------------
                sem = asyncio.Semaphore(candidate_concurrency)

                async def _run_eval(
                    candidate: CompanyCandidate,
                    norm_key: str,
                    kind: str,
                ):
                    """Returns (candidate, norm_key, kind, hit, skip_reason).
                    kind is "direct" or "full"."""
                    async with sem:
                        try:
                            if kind == "direct":
                                hit, skip_reason = await _extract_direct_job_url(
                                    candidate.direct_job_url,
                                    profile_keywords,
                                    locations=locations,
                                    min_salary=min_salary,
                                )
                            else:
                                hit, skip_reason = await _evaluate_candidate(
                                    candidate,
                                    profile_keywords,
                                    http_client,
                                    locations=locations,
                                    min_salary=min_salary,
                                    guidance=guidance,
                                    profile_fit_threshold=profile_fit_threshold,
                                )

                            # Grounded match-reason + optional rejection. Skip
                            # for "lead" hits — those have no job content to
                            # reason over and already carry a factual message.
                            if hit and hit.kind != "lead" and hit.top_jobs:
                                # Pass reference context as guidance fallback
                                # so the match reasoning knows why we searched.
                                effective_guidance = guidance or ""
                                if (
                                    not effective_guidance
                                    and reference_context
                                    and reference_context != "(no reference jobs)"
                                ):
                                    effective_guidance = (
                                        f"Finding jobs similar to: {reference_context}"
                                    )
                                rejected, desc, reason, reject_reason = await _generate_hit_summary(
                                    hit.name,
                                    hit.top_jobs,
                                    profile_data,
                                    guidance=effective_guidance,
                                )
                                if rejected:
                                    return (
                                        candidate,
                                        norm_key,
                                        kind,
                                        None,
                                        f"Dropped: {reject_reason}",
                                    )
                                if desc:
                                    hit.description = desc
                                if reason:
                                    hit.match_reason = reason

                            return (candidate, norm_key, kind, hit, skip_reason)
                        except asyncio.CancelledError:
                            raise
                        except Exception as e:
                            logger.warning(
                                "Parallel eval task failed for %s: %s",
                                candidate.name,
                                e,
                            )
                            return (
                                candidate,
                                norm_key,
                                kind,
                                None,
                                f"Eval error: {str(e)[:80]}",
                            )

                task_futures = [
                    asyncio.create_task(_run_eval(c, k, kind)) for (c, k, kind) in pending_evals
                ]

                try:
                    for fut in asyncio.as_completed(task_futures):
                        if len(hits) >= max_hits:
                            break
                        try:
                            candidate, norm_key, kind, hit, skip_reason = await fut
                        except asyncio.CancelledError:
                            continue

                        if kind == "direct":
                            # Direct URL path: on success, group into an
                            # existing company hit if one exists, otherwise
                            # create a new hit.
                            if hit:
                                evaluated[norm_key] = "hit"
                                funnel["direct_hit"] += 1
                                company_lower = hit.name.lower()
                                existing_hit = next(
                                    (h for h in hits if h.name.lower() == company_lower),
                                    None,
                                )
                                if existing_hit:
                                    existing_hit.top_jobs.extend(hit.top_jobs)
                                    existing_hit.total_jobs += hit.total_jobs
                                    existing_hit.relevant_jobs += hit.relevant_jobs
                                    emit_hit = existing_hit
                                else:
                                    hits.append(hit)
                                    iteration_hits += 1
                                    emit_hit = hit
                                yield SearchEvent(
                                    "hit",
                                    {
                                        "name": emit_hit.name,
                                        "ats": emit_hit.ats,
                                        "slug": emit_hit.slug,
                                        "website": emit_hit.website,
                                        "total_jobs": emit_hit.total_jobs,
                                        "relevant_jobs": emit_hit.relevant_jobs,
                                        "top_jobs": emit_hit.top_jobs,
                                        "source": emit_hit.source,
                                        "description": emit_hit.description,
                                        "match_reason": emit_hit.match_reason,
                                    },
                                )
                            else:
                                # Direct URL import failed — release the slot
                                # we reserved at queue time so we don't count
                                # it against the cap. Refund the matching
                                # per-origin counter.
                                if candidate.origin == "aggregator":
                                    aggregator_direct_count = max(
                                        0,
                                        aggregator_direct_count - 1,
                                    )
                                else:
                                    query_direct_count = max(
                                        0,
                                        query_direct_count - 1,
                                    )
                                evaluated[norm_key] = "miss"
                                funnel["direct_miss"] += 1
                                if skip_reason:
                                    skip_reasons[skip_reason[:80]] += 1
                                yield SearchEvent(
                                    "skip",
                                    {
                                        "name": candidate.name,
                                        "source": candidate.source,
                                        "reason": skip_reason,
                                    },
                                )
                            continue

                        # Full eval path. Drop:
                        #   (a) ats-kind hits with no top_jobs (Salesforce 0j/0r leak)
                        #   (b) lead-kind hits when the user set location/salary
                        #       filters — leads are just careers-page links, we
                        #       can't verify them against the user's constraints,
                        #       so emitting them is a guaranteed leak past Lane A.
                        if hit and not hit.top_jobs:
                            is_lead = hit.kind == "lead"
                            filters_active = bool(locations or min_salary)
                            should_drop = (not is_lead) or filters_active
                            if should_drop:
                                evaluated[norm_key] = "miss"
                                funnel["full_miss"] += 1
                                reason = (
                                    "Lead skipped (filters active, can't verify)"
                                    if is_lead
                                    else "Eval returned hit with no top_jobs"
                                )
                                skip_reasons[reason[:80]] += 1
                                yield SearchEvent(
                                    "skip",
                                    {
                                        "name": candidate.name,
                                        "source": candidate.source,
                                        "reason": reason,
                                    },
                                )
                                continue
                        if hit:
                            # Cap tentative hits separately. The user asked
                            # for "always return something" without flooding
                            # — so strong hits flow freely up to max_hits,
                            # but tentative hits cap out independently so
                            # noise can't dominate. When the tentative cap
                            # is hit, downgrade to a skip with a reason.
                            if hit.is_tentative and tentative_emitted >= _MAX_TENTATIVE_HITS:
                                evaluated[norm_key] = "miss"
                                funnel["tentative_cap_reached"] += 1
                                yield SearchEvent(
                                    "skip",
                                    {
                                        "name": candidate.name,
                                        "source": candidate.source,
                                        "reason": (
                                            f"Best job match was {hit.match_score}/100 "
                                            f"(tentative tier) but we already showed "
                                            f"{_MAX_TENTATIVE_HITS} tentative hits"
                                        ),
                                    },
                                )
                                continue
                            evaluated[norm_key] = "hit"
                            funnel["full_hit" if not hit.is_tentative else "tentative_hit"] += 1
                            if hit.is_tentative:
                                tentative_emitted += 1
                            if hit.ats and hit.slug:
                                evaluated[f"{hit.ats}:{hit.slug}"] = "hit"
                            hits.append(hit)
                            iteration_hits += 1
                            yield SearchEvent(
                                "hit",
                                {
                                    "name": hit.name,
                                    "ats": hit.ats,
                                    "slug": hit.slug,
                                    "website": hit.website,
                                    "total_jobs": hit.total_jobs,
                                    "relevant_jobs": hit.relevant_jobs,
                                    "top_jobs": hit.top_jobs,
                                    "source": hit.source,
                                    "description": hit.description,
                                    "match_reason": hit.match_reason,
                                    "kind": hit.kind,
                                    "careers_url": hit.careers_url,
                                    "company_id": hit.company_id,
                                    "is_tentative": hit.is_tentative,
                                    "match_score": hit.match_score,
                                },
                            )
                        else:
                            evaluated[norm_key] = "miss"
                            funnel["full_miss"] += 1
                            if skip_reason:
                                skip_reasons[skip_reason[:80]] += 1
                            yield SearchEvent(
                                "skip",
                                {
                                    "name": candidate.name,
                                    "source": candidate.source,
                                    "reason": skip_reason,
                                },
                            )
                finally:
                    # Cancel any still-pending tasks (either max_hits reached
                    # or an unexpected early exit). Awaiting with
                    # return_exceptions=True lets all cancellations complete.
                    for t in task_futures:
                        if not t.done():
                            t.cancel()
                    await asyncio.gather(*task_futures, return_exceptions=True)

                await asyncio.sleep(0.5)

            if iteration_hits == 0:
                consecutive_dry += 1
            else:
                consecutive_dry = 0

    funnel["final_hits"] = len(hits)
    yield SearchEvent(
        "done",
        {
            "total_hits": len(hits),
            "total_candidates_checked": total_candidates,
            "funnel": dict(funnel),
            "top_skip_reasons": skip_reasons.most_common(8),
        },
    )
    logger.info(
        "Hot search funnel: %s | top skip reasons: %s",
        dict(funnel),
        skip_reasons.most_common(5),
    )


__all__ = ["run_hot_company_search"]
