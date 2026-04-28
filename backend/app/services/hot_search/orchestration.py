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
import logging
from collections import Counter
from typing import AsyncGenerator

import httpx

from app.config import settings
from app.scrapers.discovery_adapters import DISCOVERY_ADAPTERS
from app.services.hot_search.types import (
    CompanyCandidate,
    CompanyHit,
    SearchEvent,
)

logger = logging.getLogger(__name__)


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
    # 8 in flight at once. Per-candidate eval is mostly LLM and HTTP wait
    # time; OpenAI handles parallel just fine and Ashby's 429 retries
    # already cope with bursts. The downstream semaphores in rate_limits
    # cap browser-pool and OpenAI concurrency separately, so this is just
    # the per-search worker count.
    candidate_concurrency: int = 8,
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
        _evaluate_candidate,
        _evaluate_tracked_company,
        _extract_direct_job_url,
        _generate_hit_summary,
        _is_duplicate_company,
    )

    valid_sources = {"web", "greenhouse", "lever", "ashby"}
    sources = [s for s in sources if s in valid_sources]
    if not sources:
        yield SearchEvent("error", {"message": "No valid sources selected"})
        yield SearchEvent("done", {"total_hits": 0, "total_candidates_checked": 0})
        return

    # Validate at least one web search backend is reachable. The unified
    # web_search() will silently return empty if nothing is configured.
    if not (
        settings.perplexity_api_key
        or settings.brave_api_key
        or settings.searxng_url
    ):
        yield SearchEvent("error", {
            "message": (
                "No web search backend configured. Set PERPLEXITY_API_KEY, "
                "BRAVE_API_KEY, or run a SearXNG instance and set SEARXNG_URL."
            ),
        })
        yield SearchEvent("done", {"total_hits": 0, "total_candidates_checked": 0})
        return

    yield SearchEvent("status", {
        "message": "Loading profile and existing companies...",
        "phase": "init", "iteration": 0,
        "total_queries": 0, "hits_so_far": 0,
    })

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
    direct_import_count = 0  # Cap one-off URL imports per session
    # Bumped to 100 once the aggregator harvester started producing 350+
    # seed candidates per run; previously 25 was throwing away ~120 viable
    # candidates per run (see funnel: direct_cap_reached). The verifier
    # already runs per-candidate so the cap is now a cost ceiling, not a
    # quality gate.
    _MAX_DIRECT_IMPORTS = 100

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
            yield SearchEvent("status", {
                "message": "Harvesting from aggregator feeds (HN, Remotive, Muse, Arbeitnow)...",
                "phase": "harvesting", "iteration": 0,
                "total_queries": 0, "hits_so_far": 0,
            })
            adapter_tasks = [
                adapter.fetch_entries(http_client, guidance, locations or [], min_salary)
                for adapter in DISCOVERY_ADAPTERS
            ]
            adapter_results = await asyncio.gather(
                *adapter_tasks, return_exceptions=True,
            )
            all_entries: list = []
            for adapter, result in zip(DISCOVERY_ADAPTERS, adapter_results):
                if isinstance(result, Exception):
                    logger.warning(
                        "Discovery adapter %s raised: %s",
                        adapter.source_name, result,
                    )
                    continue
                logger.info(
                    "Discovery adapter %s yielded %d entries",
                    adapter.source_name, len(result),
                )
                all_entries.extend(result)
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
                len(all_entries), len(seed_candidates),
            )
        except Exception:
            logger.exception("Aggregator harvest failed; continuing without seed candidates")
            seed_candidates = []

        for iteration in range(max_iterations):
            if len(hits) >= max_hits:
                break
            if consecutive_dry >= 3:
                yield SearchEvent("status", {
                    "message": "Stopping — 3 iterations with no new hits",
                    "phase": "stopping", "iteration": iteration,
                    "total_queries": len(past_queries), "hits_so_far": len(hits),
                })
                break

            yield SearchEvent("status", {
                "message": f"Generating search queries (round {iteration + 1}/{max_iterations})...",
                "phase": "generating", "iteration": iteration + 1,
                "total_queries": len(past_queries), "hits_so_far": len(hits),
            })

            queries = await _generate_queries(
                guidance, profile_data, existing_companies,
                past_queries, evaluated, len(hits), max_hits, sources,
                locations=locations,
                min_salary=min_salary,
                reference_context=reference_context,
            )
            past_queries.extend(queries)
            iteration_hits = 0

            for query in queries:
                if len(hits) >= max_hits:
                    break

                yield SearchEvent("status", {
                    "message": f"Searching: {query[:80]}...",
                    "phase": "searching", "iteration": iteration + 1,
                    "total_queries": len(past_queries), "hits_so_far": len(hits),
                })

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
                #   - dedup (LLM, ~1s)
                #   - already-evaluated skip
                # Output: pending_evals = list of (candidate, norm_key, kind)
                #         for candidates that need the slow ATS-scrape pipeline.
                # ------------------------------------------------------------
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
                            yield SearchEvent("skip", {
                                "name": candidate.name,
                                "source": candidate.source,
                                "reason": "Already checked",
                            })
                            continue
                        total_candidates += 1
                        yield SearchEvent("candidate", {
                            "name": candidate.name,
                            "source": candidate.source,
                        })
                        tracked_hit, _ = await _evaluate_tracked_company(
                            candidate.name, profile_keywords,
                            locations=locations, min_salary=min_salary,
                            guidance=guidance,
                        )
                        if tracked_hit:
                            evaluated[norm_key] = "hit"
                            hits.append(tracked_hit)
                            iteration_hits += 1
                            funnel["tracked_hit"] += 1
                            yield SearchEvent("hit", {
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
                            })
                            continue
                        evaluated[norm_key] = "checking"
                        funnel["tracked_no_match"] += 1
                        # Fall through into regular eval pipeline

                    # Dedup: LLM-assisted check for name variations
                    all_known = list(existing_companies) + [h.name for h in hits]
                    dup_of = await _is_duplicate_company(candidate.name, all_known)
                    if dup_of:
                        funnel["dedup_dropped"] += 1
                        yield SearchEvent("skip", {
                            "name": candidate.name,
                            "source": candidate.source,
                            "reason": f"Duplicate of '{dup_of}'",
                        })
                        evaluated[candidate.name.lower()] = "miss"
                        continue

                    norm_key = candidate.name.lower()
                    if candidate.slug:
                        norm_key = f"{candidate.ats}:{candidate.slug}"
                    if norm_key in evaluated:
                        funnel["already_checked"] += 1
                        yield SearchEvent("skip", {
                            "name": candidate.name,
                            "source": candidate.source,
                            "reason": "Already checked",
                        })
                        continue

                    total_candidates += 1
                    yield SearchEvent("candidate", {
                        "name": candidate.name,
                        "source": candidate.source,
                    })

                    # Direct-URL branch: check cap at queue time. We reserve
                    # a slot eagerly so concurrent direct imports can't exceed
                    # the cap (though in practice direct URLs are rare).
                    if candidate.direct_job_url:
                        if direct_import_count >= _MAX_DIRECT_IMPORTS:
                            evaluated[norm_key] = "miss"
                            funnel["direct_cap_reached"] += 1
                            yield SearchEvent("skip", {
                                "name": candidate.name,
                                "source": candidate.source,
                                "reason": f"Direct-import cap reached ({_MAX_DIRECT_IMPORTS}/run)",
                            })
                            continue
                        direct_import_count += 1
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
                    candidate: CompanyCandidate, norm_key: str, kind: str,
                ):
                    """Returns (candidate, norm_key, kind, hit, skip_reason).
                    kind is "direct" or "full"."""
                    async with sem:
                        try:
                            if kind == "direct":
                                hit, skip_reason = await _extract_direct_job_url(
                                    candidate.direct_job_url, profile_keywords,
                                    locations=locations, min_salary=min_salary,
                                )
                            else:
                                hit, skip_reason = await _evaluate_candidate(
                                    candidate, profile_keywords, http_client,
                                    locations=locations,
                                    min_salary=min_salary,
                                    guidance=guidance,
                                )

                            # Grounded match-reason + optional rejection. Skip
                            # for "lead" hits — those have no job content to
                            # reason over and already carry a factual message.
                            if hit and hit.kind != "lead" and hit.top_jobs:
                                # Pass reference context as guidance fallback
                                # so the match reasoning knows why we searched.
                                effective_guidance = guidance or ""
                                if not effective_guidance and reference_context and reference_context != "(no reference jobs)":
                                    effective_guidance = f"Finding jobs similar to: {reference_context}"
                                rejected, desc, reason, reject_reason = (
                                    await _generate_hit_summary(
                                        hit.name, hit.top_jobs, profile_data,
                                        guidance=effective_guidance,
                                    )
                                )
                                if rejected:
                                    return (
                                        candidate, norm_key, kind, None,
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
                                candidate.name, e,
                            )
                            return (
                                candidate, norm_key, kind, None,
                                f"Eval error: {str(e)[:80]}",
                            )

                task_futures = [
                    asyncio.create_task(_run_eval(c, k, kind))
                    for (c, k, kind) in pending_evals
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
                                yield SearchEvent("hit", {
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
                                })
                            else:
                                # Direct URL import failed — release the slot
                                # we reserved at queue time so we don't count
                                # it against the cap.
                                direct_import_count = max(0, direct_import_count - 1)
                                evaluated[norm_key] = "miss"
                                funnel["direct_miss"] += 1
                                if skip_reason:
                                    skip_reasons[skip_reason[:80]] += 1
                                yield SearchEvent("skip", {
                                    "name": candidate.name,
                                    "source": candidate.source,
                                    "reason": skip_reason,
                                })
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
                                yield SearchEvent("skip", {
                                    "name": candidate.name,
                                    "source": candidate.source,
                                    "reason": reason,
                                })
                                continue
                        if hit:
                            evaluated[norm_key] = "hit"
                            funnel["full_hit"] += 1
                            if hit.ats and hit.slug:
                                evaluated[f"{hit.ats}:{hit.slug}"] = "hit"
                            hits.append(hit)
                            iteration_hits += 1
                            yield SearchEvent("hit", {
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
                            })
                        else:
                            evaluated[norm_key] = "miss"
                            funnel["full_miss"] += 1
                            if skip_reason:
                                skip_reasons[skip_reason[:80]] += 1
                            yield SearchEvent("skip", {
                                "name": candidate.name,
                                "source": candidate.source,
                                "reason": skip_reason,
                            })
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
    yield SearchEvent("done", {
        "total_hits": len(hits),
        "total_candidates_checked": total_candidates,
        "funnel": dict(funnel),
        "top_skip_reasons": skip_reasons.most_common(8),
    })
    logger.info(
        "Hot search funnel: %s | top skip reasons: %s",
        dict(funnel), skip_reasons.most_common(5),
    )


__all__ = ["run_hot_company_search"]
