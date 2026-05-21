"""Hot Search v2 — single-pass orchestrator.

Replaces v1's 870-line iteration loop + 5-strategy drill cascade with
one linear pipeline:

    Phase A1  aggregator harvest        ─┐
    Phase A2  LLM-web discovery         ─┼─▶  candidate pool
    Phase A3  discovery cache recall    ─┘

    Phase B   batched LLM dedup + DB cross-check (already-tracked)

    Phase C   ATS resolution (per-candidate, parallel)
              cache hit → probe → web-search-careers-page

    Phase D   job fetching (per-candidate, parallel)
              (ats, slug) → SCRAPERS_BY_ATS  (full board)
              careers_url → careers_titles.list_job_titles (titles only)
              direct_job_url → single-URL extract (existing helper)

    Phase E   scoring
              ranking.rank_jobs (embed → cosine top-K → LLM rerank)

    Phase F   location/salary verification (batched LLM)

    Emit hits grouped by company in rerank order. Persist surviving and
    rejected companies to ``discovered_companies`` so future searches
    can recall them. Stream SearchEvent vocabulary identical to v1, so
    the SSE router and frontend hook need zero changes.

Single pass, no iteration. 4-minute wall-clock budget (down from v1's 8).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from typing import AsyncGenerator

import httpx

from app.scrapers.discovery_adapters import DISCOVERY_ADAPTERS
from app.services.company_discovery import _build_keyword_sets
from app.services.hot_search.discovery import (
    _build_reference_context,
    _enrich_keywords_from_references,
    _get_existing_company_names,
    _harvest_candidates_from_entries,
    _load_profile_data,
    _load_reference_jobs,
    _probe_name_for_ats,
    _search_careers_url,
    _search_company_careers_page,
)
from app.services.hot_search.discovery_cache import (
    mark_outcome,
    normalize_name,
    recall_relevant,
    upsert_many,
)
from app.services.hot_search.discovery_v2 import discover_via_llm_web
from app.services.hot_search.evaluation import (
    _batch_check_duplicates,
    _evaluate_tracked_company,
    _extract_direct_job_url,
    _extract_from_preview,
    _generate_hit_summary,
    _get_verify_semaphore,
    _job_passes_location_filter,
    _job_passes_salary_filter,
    _verify_jobs_with_extraction,
)
from app.services.hot_search.orchestration import _prefilter_aggregator_entries
from app.services.hot_search.ranking import (
    build_job_doc,
    build_query_doc,
    embed_batch,
    rank_jobs,
)
from app.services.hot_search.types import (
    CompanyCandidate,
    CompanyHit,
    SearchEvent,
)
from app.scrapers import SCRAPERS_BY_ATS, make_temp_company

logger = logging.getLogger(__name__)


_GLOBAL_BUDGET_S = 4 * 60   # 4-minute hard cap; v1 was 8
_PER_CANDIDATE_RESOLUTION_S = 30
_PER_CANDIDATE_FETCH_S = 45
_CACHE_RECALL_K = 30
_CACHE_RECALL_MIN_COSINE = 0.50
_COSINE_POOL_SIZE = 80
_TOP_K_DEFAULT = 20


async def _verify_jobs_v2_loose(
    jobs: list[dict],
    locations: list[str] | None,
    min_salary: int | None,
) -> list[dict]:
    """v2 verifier with looser unknown-policy semantics.

    Phase E (rerank) already vetted topic relevance and HARD CONSTRAINTS
    via the LLM. By the time a job reaches Phase F, it has been LLM-
    judged as on-topic AND not-in-conflict with locations/min_salary.
    The structured verification here is a second-pass sanity check on
    structured signals — when we can prove a job violates a filter,
    drop it; when we cannot, trust the rerank.

    Compare to v1's ``_verify_jobs_with_extraction`` which hard-rejects
    on unknown salary/location. That makes sense for v1 where the
    picker only sees per-company snippets and can't reason about
    constraints; for v2 it's too strict — every ATS scrape without a
    salary field gets dropped even when the rerank confidently picked
    it as a match.

    Per-job logic:
      - Cheap structured filter (scraper-provided fields): if explicit
        miss, drop.
      - If structured passes OR is unknown: run LLM extraction.
        * If extracted location_match is False → drop.
        * If extracted salary is set AND below min_salary → drop.
        * Otherwise (including unknown salary / unknown location) → keep.

    Returns the kept jobs annotated with any extracted_salary_min/max
    and extracted_locations fields.
    """
    if not jobs:
        return []

    from types import SimpleNamespace

    async def _check_one(job: dict) -> dict | None:
        # Cheap filter on scraper-provided fields first
        fake = SimpleNamespace(
            title=job.get("title", ""),
            location=job.get("location"),
            remote=job.get("remote", False),
            salary_min=job.get("salary_min"),
            salary_max=job.get("salary_max"),
        )
        if locations:
            passes, _ = _job_passes_location_filter(fake, locations)
            if not passes:
                # Scraper-provided location explicitly mismatched —
                # this is a confirmed miss, drop.
                return None
        if min_salary:
            if (
                job.get("salary_max") is not None
                and job["salary_max"] < min_salary
            ):
                return None  # scraper-confirmed below threshold

        # LLM extraction pass (only when we have enough description text)
        desc = job.get("description_html") or job.get("description") or ""
        if not desc.strip():
            # No body text — trust the rerank's LLM judgment.
            return job

        async with _get_verify_semaphore():
            try:
                extracted = await _extract_from_preview(
                    title=job.get("title", ""),
                    company=job.get("company", ""),
                    location=job.get("location"),
                    description=desc,
                    filter_locations=locations if locations else None,
                )
            except Exception:
                extracted = None

        if not extracted:
            # Extraction failed — fall back to the rerank's judgment.
            return job

        # Definite-miss rejections only — unknowns pass through.
        if locations and extracted.get("location_match") is False:
            return None
        if min_salary and extracted.get("salary_max") is not None:
            if extracted["salary_max"] < min_salary:
                return None

        out = dict(job)
        if extracted.get("salary_min"):
            out["extracted_salary_min"] = extracted["salary_min"]
        if extracted.get("salary_max"):
            out["extracted_salary_max"] = extracted["salary_max"]
        if extracted.get("work_model"):
            out["extracted_work_model"] = extracted["work_model"]
        if extracted.get("locations"):
            out["extracted_locations"] = extracted["locations"]
        return out

    results = await asyncio.gather(*[_check_one(j) for j in jobs])
    kept = [r for r in results if r is not None]
    logger.info(
        "_verify_jobs_v2_loose: kept %d/%d (locations=%s, min_salary=%s)",
        len(kept), len(jobs), locations, min_salary,
    )
    return kept


async def run_hot_company_search_v2(
    sources: list[str],
    guidance: str,
    max_hits: int = 20,
    max_iterations: int = 1,  # accepted for signature compat; ignored (single-pass)
    locations: list[str] | None = None,
    min_salary: int | None = None,
    reference_job_ids: list[str] | None = None,
    candidate_concurrency: int = 16,
    profile_fit_threshold: int = 50,  # signature compat; rerank floor lives in ranking module
) -> AsyncGenerator[SearchEvent, None]:
    """v2 entry point. Signature mirrors v1's run_hot_company_search so
    the router + eval script can route through the dispatcher unchanged.
    """
    start_time = time.monotonic()

    def _budget_left() -> float:
        return _GLOBAL_BUDGET_S - (time.monotonic() - start_time)

    funnel: Counter = Counter()
    skip_reasons: Counter = Counter()
    # Track which candidate names received a terminal event (hit or skip).
    # At the end of the run we emit safety-net skips for any candidate
    # that was emitted as a `candidate` event but didn't receive a
    # terminal — otherwise their UI row spins forever. Sources of silent
    # drops include: post-resolution dedup losers, companies whose jobs
    # didn't survive rerank, companies past max_hits.
    terminal_names_emitted: set[str] = set()

    def _mark_terminal(name: str | None) -> None:
        if name:
            terminal_names_emitted.add(name.lower().strip())

    def _safety_net_events() -> list[SearchEvent]:
        """Return skip events for every candidate that received a
        candidate-event but no terminal. Called immediately before each
        ``done`` emission so UI rows always resolve out of the spinner.
        Idempotent — once a candidate gets a skip here, it's added to
        terminal_names_emitted and won't double-emit on subsequent
        calls (relevant only if the function is called more than once
        in unusual control flow)."""
        out: list[SearchEvent] = []
        # unique_candidates may not exist yet if we early-return from
        # very early in the pipeline; the nonlocal lookup handles that
        # via try/except — there's nothing to clean up before then.
        try:
            candidates_iter = list(unique_candidates)
        except (NameError, UnboundLocalError):
            return out
        for c in candidates_iter:
            key = (c.name or "").lower().strip()
            if not key or key in terminal_names_emitted:
                continue
            terminal_names_emitted.add(key)
            funnel["safety_net_skip"] += 1
            out.append(SearchEvent("skip", {
                "name": c.name, "source": c.source,
                "reason": "Reviewed but didn't make the top results",
            }))
        return out

    yield SearchEvent("status", {
        "message": "Loading profile and existing companies...",
        "phase": "init", "iteration": 0,
        "total_queries": 0, "hits_so_far": 0,
    })

    # Load context (profile, tracked companies, reference jobs) in
    # parallel — these are independent DB reads.
    profile_data, existing_companies, reference_jobs = await asyncio.gather(
        _load_profile_data(),
        _get_existing_company_names(),
        _load_reference_jobs(reference_job_ids or []),
    )
    existing_lower = {n.lower() for n in existing_companies}
    profile_keywords = _build_keyword_sets(profile_data) if profile_data else {}
    reference_context = _build_reference_context(reference_jobs)
    if reference_jobs:
        profile_keywords = _enrich_keywords_from_references(profile_keywords, reference_jobs)

    # Build the query doc once — used by:
    #  - Phase A3 cache recall (cosine vs stored description embeddings)
    #  - Phase E ranking (cosine vs job docs + LLM rerank)
    query_doc = build_query_doc(
        guidance=guidance,
        profile_data=profile_data,
        reference_context=reference_context,
    )
    # One embeddings call for the query — used twice. Cache the vector.
    query_emb_list = await embed_batch([query_doc])
    query_emb = query_emb_list[0] if query_emb_list else []

    async with httpx.AsyncClient(timeout=60.0) as http_client:
        # -------------------------------------------------------------
        # Phase A — three discovery streams in parallel
        # -------------------------------------------------------------
        yield SearchEvent("status", {
            "message": "Discovering companies (aggregators + LLM-web + cache)...",
            "phase": "discovery", "iteration": 1,
            "total_queries": 0, "hits_so_far": 0,
        })

        # A1: aggregator harvest
        async def _phase_a1() -> list[CompanyCandidate]:
            try:
                tasks = [
                    adapter.fetch_entries(http_client, guidance, locations or [], min_salary)
                    for adapter in DISCOVERY_ADAPTERS
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                entries = []
                for adapter, res in zip(DISCOVERY_ADAPTERS, results):
                    if isinstance(res, Exception):
                        logger.warning("Adapter %s raised: %s", adapter.source_name, res)
                        continue
                    entries.extend(res)
                if guidance and entries:
                    entries = await _prefilter_aggregator_entries(entries, guidance)
                seen_keys: set[str] = set()
                candidates = await _harvest_candidates_from_entries(
                    entries, http_client,
                    seen=seen_keys, existing_companies_lower=existing_lower,
                )
                funnel["a1_aggregator_entries"] = len(entries)
                funnel["a1_aggregator_candidates"] = len(candidates)
                return candidates
            except Exception:
                logger.exception("Phase A1 failed; continuing")
                funnel["a1_failed"] = 1
                return []

        # A2: LLM-web discovery
        async def _phase_a2() -> list[CompanyCandidate]:
            try:
                candidates = await discover_via_llm_web(
                    guidance or "",
                    profile_data=profile_data,
                    existing_companies=existing_companies,
                    past_queries=[],
                    evaluated={},
                    reference_context=reference_context,
                    locations=locations,
                    min_salary=min_salary,
                    sources=sources,
                    n_queries=3,
                )
                funnel["a2_llm_web_candidates"] = len(candidates)
                return candidates
            except Exception:
                logger.exception("Phase A2 failed; continuing")
                funnel["a2_failed"] = 1
                return []

        # A3: discovery cache recall
        async def _phase_a3() -> list[CompanyCandidate]:
            if not query_emb:
                return []
            try:
                rows = await recall_relevant(
                    query_emb,
                    k=_CACHE_RECALL_K,
                    min_cosine=_CACHE_RECALL_MIN_COSINE,
                    only_resolved=False,
                    exclude_repeated_no_match=True,
                )
                funnel["a3_cache_recalls"] = len(rows)
                return [
                    CompanyCandidate(
                        name=r.name,
                        url=r.careers_url,
                        ats=r.ats,
                        slug=r.slug,
                        source="cache_recall",
                        origin="cache",
                    )
                    for r in rows
                ]
            except Exception:
                logger.exception("Phase A3 failed; continuing")
                return []

        a1_candidates, a2_candidates, a3_candidates = await asyncio.gather(
            _phase_a1(), _phase_a2(), _phase_a3(),
        )

        # Union — cache rows go LAST so query-driven candidates win the
        # dedup key tie-break (we trust fresh discovery over stale).
        raw_candidates = a2_candidates + a1_candidates + a3_candidates
        funnel["candidates_pre_dedup"] = len(raw_candidates)

        # Internal-collision dedup before we pay for the LLM dedup call —
        # same (ats, slug) seen from multiple sources collapse here.
        seen_internal: set[str] = set()
        unique_candidates: list[CompanyCandidate] = []
        for c in raw_candidates:
            if c.ats and c.slug:
                key = f"{c.ats}:{c.slug}"
            elif c.direct_job_url:
                key = f"direct:{c.direct_job_url}"
            else:
                key = f"name:{c.name.lower().strip()}"
            if key in seen_internal:
                continue
            seen_internal.add(key)
            unique_candidates.append(c)

        yield SearchEvent("status", {
            "message": (
                f"Found {len(unique_candidates)} candidates "
                f"(A1={len(a1_candidates)} aggregator, "
                f"A2={len(a2_candidates)} llm-web, "
                f"A3={len(a3_candidates)} cached)"
            ),
            "phase": "candidates_found", "iteration": 1,
            "total_queries": 3, "hits_so_far": 0,
        })

        for c in unique_candidates:
            yield SearchEvent("candidate", {"name": c.name, "source": c.source})

        # -------------------------------------------------------------
        # Phase B — LLM dedup + tracked-company DB cross-check
        # -------------------------------------------------------------
        cand_names = [c.name for c in unique_candidates if c.name]
        known_for_dedup = list(existing_companies)
        try:
            dup_map = await _batch_check_duplicates(cand_names, known_for_dedup)
        except Exception:
            logger.exception("Batched dedup failed; treating none as duplicates")
            dup_map = {}

        tracked_hits: list[CompanyHit] = []
        post_dedup: list[CompanyCandidate] = []
        for c in unique_candidates:
            dup_of = dup_map.get(c.name)
            # Already-tracked path: route to DB-only evaluator
            if c.name.lower() in existing_lower or (dup_of and dup_of.lower() in existing_lower):
                try:
                    th, _ = await _evaluate_tracked_company(
                        c.name, profile_keywords,
                        locations=locations, min_salary=min_salary,
                        guidance=guidance,
                    )
                except Exception:
                    th = None
                if th:
                    funnel["b_tracked_hits"] += 1
                    tracked_hits.append(th)
                    _mark_terminal(th.name)
                    _mark_terminal(c.name)  # might be a spelling variant
                    yield SearchEvent("hit", _hit_event_data(th))
                else:
                    funnel["b_tracked_no_match"] += 1
                    _mark_terminal(c.name)
                    yield SearchEvent("skip", {
                        "name": c.name, "source": c.source,
                        "reason": "Already tracked, no matching jobs right now",
                    })
                continue
            if dup_of:
                funnel["b_dedup_dropped"] += 1
                _mark_terminal(c.name)
                yield SearchEvent("skip", {
                    "name": c.name, "source": c.source,
                    "reason": f"Duplicate of '{dup_of}'",
                })
                continue
            post_dedup.append(c)

        # If we've already filled max_hits from tracked alone, we're done.
        if len(tracked_hits) >= max_hits:
            for _sn_ev in _safety_net_events():
                yield _sn_ev
            yield SearchEvent("done", _done_event_data(
                len(tracked_hits), len(unique_candidates), funnel, skip_reasons,
            ))
            return

        # -------------------------------------------------------------
        # Phase C — ATS resolution per candidate (parallel)
        # -------------------------------------------------------------
        if _budget_left() <= 0:
            for _sn_ev in _safety_net_events():
                yield _sn_ev
            yield SearchEvent("done", _done_event_data(
                len(tracked_hits), len(unique_candidates), funnel, skip_reasons,
            ))
            return

        sem_resolve = asyncio.Semaphore(candidate_concurrency)

        async def _resolve(c: CompanyCandidate) -> CompanyCandidate | None:
            """Return c with ats/slug or careers_url filled in, or None if unresolvable."""
            async with sem_resolve:
                # Already resolved (regex matched on URL or came from cache)
                if (c.ats and c.slug) or c.direct_job_url:
                    return c
                # Try ATS probe by name
                try:
                    probed = await asyncio.wait_for(
                        _probe_name_for_ats(c.name, http_client),
                        timeout=_PER_CANDIDATE_RESOLUTION_S,
                    )
                except (asyncio.TimeoutError, Exception):
                    probed = None
                if probed:
                    c.ats, c.slug = probed
                    return c
                # Already had a careers URL from discovery? Use it.
                if c.url:
                    return c
                # Search the web for one
                try:
                    careers_url = await asyncio.wait_for(
                        _search_company_careers_page(c.name),
                        timeout=_PER_CANDIDATE_RESOLUTION_S,
                    )
                except (asyncio.TimeoutError, Exception):
                    careers_url = None
                if careers_url:
                    c.url = careers_url
                    return c
                return None

        resolve_tasks = [
            asyncio.create_task(_resolve(c)) for c in post_dedup
        ]
        resolved_raw: list[CompanyCandidate] = []
        unresolvable: list[CompanyCandidate] = []
        for task, candidate in zip(resolve_tasks, post_dedup):
            try:
                r = await task
            except Exception:
                r = None
            if r is None:
                unresolvable.append(candidate)
                funnel["c_unresolvable"] += 1
                _mark_terminal(candidate.name)
                yield SearchEvent("skip", {
                    "name": candidate.name, "source": candidate.source,
                    "reason": "Couldn't find ATS slug or careers page",
                })
                continue
            resolved_raw.append(r)

        # Post-resolution dedup. Candidates that started with different
        # names (e.g. "Manifold Bio" from LLM extraction vs "Manifoldbio"
        # derived from a boards.greenhouse.io/manifoldbio URL slug) only
        # collide once Phase C resolves both to the same (ats, slug).
        # Pre-resolution dedup couldn't catch them. Collapse by:
        #   (ats, slug)        — same ATS company surfaced via different paths
        #   direct_job_url     — same posting URL extracted twice
        #   normalized name    — same company, neither resolved
        # When two collide, keep the candidate with the better display
        # name (more word boundaries beats kebab/concat).
        def _name_quality(name: str) -> tuple[int, int]:
            """Higher tuple wins. Prefer (1) more whitespace-separated
            tokens, (2) longer overall length. So "Manifold Bio" beats
            "Manifoldbio", "Atomic AI" beats "Atomai"."""
            n = (name or "").strip()
            return (len(n.split()), len(n))

        from app.services.hot_search.discovery_cache import normalize_name
        resolved_by_key: dict[str, CompanyCandidate] = {}
        # Track (loser_name, winner_name) pairs so we can emit informative
        # skip events after dedup. The UI shows a spinner for every
        # candidate event until a terminal lands — silent dedup leaves
        # the loser spinning forever.
        dedup_collapses: list[tuple[CompanyCandidate, CompanyCandidate]] = []
        for c in resolved_raw:
            if c.ats and c.slug:
                key = f"{c.ats}:{c.slug}"
            elif c.direct_job_url:
                key = f"direct:{c.direct_job_url}"
            elif c.url:
                key = f"careers:{c.url.rstrip('/').lower()}"
            else:
                key = f"name:{normalize_name(c.name)}"
            if key in resolved_by_key:
                existing = resolved_by_key[key]
                if _name_quality(c.name) > _name_quality(existing.name):
                    dedup_collapses.append((existing, c))  # existing is the loser
                    resolved_by_key[key] = c
                else:
                    dedup_collapses.append((c, existing))  # c is the loser
                funnel["c_post_resolution_dedup"] += 1
            else:
                resolved_by_key[key] = c
        resolved = list(resolved_by_key.values())
        funnel["c_resolved"] = len(resolved)

        # Emit a skip for each dedup loser so its UI row resolves out
        # of the spinner. Don't deduplicate by name first — both names
        # were in the candidate stream, both need terminals.
        for loser, winner in dedup_collapses:
            if loser.name.lower().strip() in terminal_names_emitted:
                continue
            _mark_terminal(loser.name)
            yield SearchEvent("skip", {
                "name": loser.name, "source": loser.source,
                "reason": f"Same company as '{winner.name}' (collapsed after ATS lookup)",
            })

        # -------------------------------------------------------------
        # Phase D — job fetching per candidate (parallel)
        # -------------------------------------------------------------
        if _budget_left() <= 0 or not resolved:
            # We may still have tracked hits; emit done.
            for _sn_ev in _safety_net_events():
                yield _sn_ev
            yield SearchEvent("done", _done_event_data(
                len(tracked_hits), len(unique_candidates), funnel, skip_reasons,
            ))
            return

        yield SearchEvent("status", {
            "message": f"Fetching jobs from {len(resolved)} companies...",
            "phase": "fetching", "iteration": 1,
            "total_queries": 3, "hits_so_far": len(tracked_hits),
        })

        sem_fetch = asyncio.Semaphore(candidate_concurrency)

        async def _fetch(c: CompanyCandidate) -> tuple[CompanyCandidate, list[dict]]:
            async with sem_fetch:
                try:
                    return c, await asyncio.wait_for(
                        _fetch_jobs_for_candidate(c, http_client),
                        timeout=_PER_CANDIDATE_FETCH_S,
                    )
                except asyncio.TimeoutError:
                    funnel["d_fetch_timeout"] += 1
                    return c, []
                except Exception:
                    logger.warning("fetch failed for %s", c.name, exc_info=True)
                    funnel["d_fetch_error"] += 1
                    return c, []

        fetch_tasks = [asyncio.create_task(_fetch(c)) for c in resolved]
        # Pool of all jobs, each tagged with its origin candidate
        all_jobs: list[dict] = []
        per_company_jobs: dict[str, list[dict]] = {}  # name → jobs list
        candidate_by_name: dict[str, CompanyCandidate] = {}
        for fut in asyncio.as_completed(fetch_tasks):
            c, jobs = await fut
            if not jobs:
                funnel["d_no_jobs"] += 1
                _mark_terminal(c.name)
                yield SearchEvent("skip", {
                    "name": c.name, "source": c.source,
                    "reason": "No jobs found at this company",
                })
                continue
            # Cheap pre-filter for location/salary using whatever the
            # scraper provided. Saves rerank cost on jobs that obviously
            # fail filters; the LLM verifier (Phase F) catches the rest.
            if locations or min_salary:
                jobs = _cheap_filter(jobs, locations, min_salary)
            if not jobs:
                funnel["d_cheap_filtered_to_zero"] += 1
                _mark_terminal(c.name)
                yield SearchEvent("skip", {
                    "name": c.name, "source": c.source,
                    "reason": "All jobs failed cheap location/salary filter",
                })
                continue
            # Stamp company name on each job for grouping later
            for j in jobs:
                j.setdefault("company", c.name)
                j.setdefault("_candidate_name", c.name)
            per_company_jobs[c.name] = jobs
            candidate_by_name[c.name] = c
            all_jobs.extend(jobs)

        funnel["d_total_jobs"] = len(all_jobs)

        if not all_jobs:
            for _sn_ev in _safety_net_events():
                yield _sn_ev
            yield SearchEvent("done", _done_event_data(
                len(tracked_hits), len(unique_candidates), funnel, skip_reasons,
            ))
            return

        # -------------------------------------------------------------
        # Phase E — scoring (cosine + LLM rerank)
        # -------------------------------------------------------------
        yield SearchEvent("status", {
            "message": f"Scoring {len(all_jobs)} jobs across {len(per_company_jobs)} companies...",
            "phase": "scoring", "iteration": 1,
            "total_queries": 3, "hits_so_far": len(tracked_hits),
        })

        # rank_jobs handles embed_batch + cosine top-K + LLM rerank in one
        # convenience call. We pass profile + guidance directly because
        # the function rebuilds query_doc internally — slightly
        # duplicative with the cache-recall embedding above but lets us
        # keep ranking.py self-contained.
        try:
            ranked = await rank_jobs(
                all_jobs,
                guidance=guidance,
                profile_data=profile_data,
                reference_context=reference_context,
                locations=locations,
                min_salary=min_salary,
                top_k=max_hits * 4,            # accept up to 4x max_hits before regrouping
                cosine_pool_size=_COSINE_POOL_SIZE,
            )
        except Exception:
            logger.exception("ranking failed; emitting done with no hits")
            for _sn_ev in _safety_net_events():
                yield _sn_ev
            yield SearchEvent("done", _done_event_data(
                len(tracked_hits), len(unique_candidates), funnel, skip_reasons,
            ))
            return

        funnel["e_ranked_jobs"] = len(ranked)

        if not ranked:
            for _sn_ev in _safety_net_events():
                yield _sn_ev
            yield SearchEvent("done", _done_event_data(
                len(tracked_hits), len(unique_candidates), funnel, skip_reasons,
            ))
            return

        # -------------------------------------------------------------
        # Phase F — location/salary LLM verification on the rerank
        #           survivors (cheap pre-filter already happened)
        # -------------------------------------------------------------
        verified_jobs: list[tuple[dict, int, bool]] = []
        if locations or min_salary:
            # Use the v2 looser verifier — definite-miss rejections only
            # (e.g. extracted salary < threshold), unknown-policy items
            # pass through. The rerank already vetted topic-fit and
            # HARD CONSTRAINTS via LLM judgment, so we don't double-
            # gate. See _verify_jobs_v2_loose docstring.
            top_job_dicts = [j for j, _r in ranked]
            try:
                verified_list = await _verify_jobs_v2_loose(
                    top_job_dicts, locations, min_salary,
                )
            except Exception:
                logger.exception("v2 verifier failed; passing rerank through unchanged")
                verified_list = top_job_dicts
            verified_by_url = {v.get("url"): v for v in verified_list if v.get("url")}
            verified_urls = set(verified_by_url.keys())
            for job, rj in ranked:
                u = job.get("url")
                if not u:
                    verified_jobs.append((job, rj.relevance, rj.is_tentative))
                    continue
                if u in verified_urls:
                    verified_jobs.append((verified_by_url[u], rj.relevance, rj.is_tentative))
                else:
                    funnel["f_filter_rejected"] += 1
        else:
            for job, rj in ranked:
                verified_jobs.append((job, rj.relevance, rj.is_tentative))

        funnel["f_post_verify"] = len(verified_jobs)

        # -------------------------------------------------------------
        # Group surviving jobs by company; emit hits in rerank order
        # -------------------------------------------------------------
        # Maintain encounter order of companies (highest-relevance job
        # of each company is what we emit first).
        company_jobs_ordered: dict[str, list[dict]] = {}
        company_scores: dict[str, int] = {}
        for job, rel, _tent in verified_jobs:
            cname = job.get("_candidate_name") or job.get("company") or "?"
            company_jobs_ordered.setdefault(cname, []).append(dict(job))
            company_scores[cname] = max(company_scores.get(cname, 0), rel)

        # Sort companies by their best-job's relevance, then alphabetic
        emit_order = sorted(
            company_jobs_ordered.keys(),
            key=lambda n: (-(company_scores.get(n, 0)), n),
        )

        emitted = list(tracked_hits)  # tracked hits already streamed
        upsert_rows: list[dict] = []
        outcome_hits: list[str] = []
        outcome_no_match: list[str] = []
        for cname in emit_order:
            if len(emitted) >= max_hits:
                break
            jobs = company_jobs_ordered[cname]
            cand = candidate_by_name.get(cname)
            if not cand:
                continue
            # Generate the description + match reason. This is a per-
            # company call; if we ever want to defer, this is the lever.
            try:
                rejected, desc, reason, reject_reason = await _generate_hit_summary(
                    cand.name, jobs[:5], profile_data, guidance=guidance or "",
                )
            except Exception:
                rejected, desc, reason, reject_reason = False, "", "", ""

            if rejected:
                funnel["e_summary_rejected"] += 1
                outcome_no_match.append(normalize_name(cand.name))
                _mark_terminal(cand.name)
                yield SearchEvent("skip", {
                    "name": cand.name, "source": cand.source,
                    "reason": f"Dropped: {reject_reason}",
                })
                continue

            best_rel = company_scores.get(cname, 0)
            is_tentative = best_rel == 2

            hit = CompanyHit(
                name=cand.name,
                ats=cand.ats or "direct",
                slug=cand.slug or normalize_name(cand.name).replace(" ", "-"),
                website=cand.url,
                total_jobs=len(jobs),
                relevant_jobs=len(jobs),
                top_jobs=jobs[:5],
                source=cand.source,
                description=desc or "",
                match_reason=reason or "",
                kind="ats" if (cand.ats and cand.slug) else "lead",
                careers_url=cand.url if not cand.ats else None,
                is_tentative=is_tentative,
                match_score=best_rel * 20,        # 1-5 → 20/40/60/80/100
            )
            emitted.append(hit)
            outcome_hits.append(normalize_name(cand.name))
            _mark_terminal(cand.name)
            yield SearchEvent("hit", _hit_event_data(hit))

            # Defer cache upsert; collect for one bulk write at the end
            # so we don't pay round-trip cost per hit.
            upsert_rows.append({
                "name": cand.name,
                "ats": cand.ats,
                "slug": cand.slug,
                "careers_url": cand.url,
                "description": desc or None,
                "description_embedding": None,    # filled below
                "source": cand.source or "unknown",
                "last_query": guidance or "(profile-driven)",
                "last_status": "hit",
            })

        # Cache misses too — embed and store their descriptions so they
        # become recallable for similar future queries.
        upserted_names = {r["name"] for r in upsert_rows}
        for cname in emit_order:
            if cname in upserted_names:
                continue
            cand = candidate_by_name.get(cname)
            if not cand:
                continue
            upserted_names.add(cname)
            upsert_rows.append({
                "name": cand.name,
                "ats": cand.ats,
                "slug": cand.slug,
                "careers_url": cand.url,
                "description": None,
                "description_embedding": None,
                "source": cand.source or "unknown",
                "last_query": guidance or "(profile-driven)",
                "last_status": "no_match",
            })
            outcome_no_match.append(normalize_name(cand.name))

        # Resolved companies that had NO jobs at all — still cache them
        # so we don't re-pay the slug-probe / careers-page-search cost
        # next time. Use the resolution metadata (ats/slug/careers_url)
        # learned during Phase C. last_status="no_jobs" so the
        # 2-strikes-out filter in recall_relevant can mute repeated
        # offenders.
        for cand in resolved:
            if cand.name in upserted_names:
                continue
            upsert_rows.append({
                "name": cand.name,
                "ats": cand.ats,
                "slug": cand.slug,
                "careers_url": cand.url,
                "description": None,
                "description_embedding": None,
                "source": cand.source or "unknown",
                "last_query": guidance or "(profile-driven)",
                "last_status": "no_jobs",
            })
            upserted_names.add(cand.name)

        # Compute description embeddings in one batched call so future
        # recall_relevant has something to match against. Use the job
        # title list as a stand-in description if we don't have a real
        # one — embedding the top 3 titles for a company captures the
        # gist (e.g. "Senior ML Engineer · ML Infra Engineer · Research
        # Eng" maps to "ML/research-heavy company" in embedding space).
        if upsert_rows:
            try:
                texts: list[str] = []
                for row in upsert_rows:
                    if row.get("description"):
                        texts.append(row["description"])
                    else:
                        jobs = per_company_jobs.get(row["name"], [])[:3]
                        title_list = " · ".join(
                            (j.get("title") or "").strip()
                            for j in jobs
                            if (j.get("title") or "").strip()
                        )
                        texts.append(title_list or row["name"])
                embs = await embed_batch(texts)
                for row, emb in zip(upsert_rows, embs):
                    row["description_embedding"] = emb
            except Exception:
                logger.exception("upsert embedding failed; storing without vectors")

            try:
                await upsert_many(upsert_rows)
            except Exception:
                logger.exception("discovery cache upsert_many failed")

        # Bulk-mark outcomes
        try:
            if outcome_hits:
                await mark_outcome(list(set(outcome_hits)), "hit")
            if outcome_no_match:
                await mark_outcome(list(set(outcome_no_match) - set(outcome_hits)), "no_match")
        except Exception:
            logger.exception("mark_outcome failed")

        for _sn_ev in _safety_net_events():
            yield _sn_ev
        yield SearchEvent("done", _done_event_data(
            len(emitted), len(unique_candidates), funnel, skip_reasons,
        ))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _fetch_jobs_for_candidate(
    c: CompanyCandidate,
    http_client: httpx.AsyncClient,
) -> list[dict]:
    """Branch on (ats+slug | careers_url | direct_job_url) and return job
    dicts shaped for the ranking + verification stages.

    Output shape (per job):
      {title, company, url, location, salary_min?, salary_max?,
       remote?, department?, description?}
    """
    # Branch 1: ATS API
    if c.ats and c.slug and c.ats in SCRAPERS_BY_ATS:
        try:
            scraper = SCRAPERS_BY_ATS[c.ats]
            company = make_temp_company(c.ats, c.slug)
            # Override the placeholder name so jobs are tagged with the
            # discovery-resolved company name, not the slug.
            company.name = c.name
            scraped = await scraper.scrape_company(company, http_client=http_client)
            jobs: list[dict] = []
            for sj in scraped:
                jobs.append({
                    "title": sj.title or "",
                    "company": c.name,
                    "url": sj.url or "",
                    "location": sj.location,
                    "remote": getattr(sj, "remote", False),
                    "salary_min": getattr(sj, "salary_min", None),
                    "salary_max": getattr(sj, "salary_max", None),
                    "department": getattr(sj, "department", None),
                    "description": getattr(sj, "description", None) or "",
                    "description_html": getattr(sj, "description_html", None) or "",
                })
            return jobs
        except Exception:
            logger.warning("ATS scrape failed for %s/%s", c.ats, c.slug, exc_info=True)
            return []

    # Branch 2: direct job URL (single-job extract)
    if c.direct_job_url:
        try:
            hit, _ = await _extract_direct_job_url(
                c.direct_job_url, {},  # profile_keywords unused for fetching
                locations=None, min_salary=None,
            )
            if not hit:
                return []
            jobs = []
            for tj in hit.top_jobs:
                tj = dict(tj)
                tj.setdefault("company", c.name)
                tj.setdefault("url", c.direct_job_url)
                jobs.append(tj)
            return jobs
        except Exception:
            logger.warning("direct_job_url extract failed: %s", c.direct_job_url, exc_info=True)
            return []

    # Branch 3: careers URL — title-only scraper
    if c.url:
        from app.services.hot_search.careers_titles import list_job_titles
        try:
            entries = await list_job_titles(c.url, max_titles=40, timeout_s=25)
        except Exception:
            logger.warning("list_job_titles failed for %s", c.url, exc_info=True)
            return []
        return [
            {
                "title": e.get("title") or "",
                "company": c.name,
                "url": e.get("url") or "",
                "location": e.get("location"),
                "description": "",
            }
            for e in entries
        ]

    return []


def _cheap_filter(
    jobs: list[dict],
    locations: list[str] | None,
    min_salary: int | None,
) -> list[dict]:
    """Apply the structured (scraper-provided) location/salary filters
    before paying for LLM verification or rerank. Mirrors v1 logic —
    leaves unknown-salary / unknown-location jobs through so the LLM
    verifier (Phase F) can take a second look."""
    from types import SimpleNamespace
    out: list[dict] = []
    for j in jobs:
        fake = SimpleNamespace(
            title=j.get("title", ""),
            location=j.get("location"),
            remote=j.get("remote", False),
            salary_min=j.get("salary_min"),
            salary_max=j.get("salary_max"),
        )
        if locations:
            passes, _ = _job_passes_location_filter(fake, locations)
            if not passes:
                continue
        if min_salary:
            passes, _ = _job_passes_salary_filter(fake, min_salary)
            if not passes:
                continue
        out.append(j)
    return out


def _hit_event_data(hit: CompanyHit) -> dict:
    """Same shape v1 emits — frontend hook reads these keys verbatim."""
    return {
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
    }


def _done_event_data(
    total_hits: int,
    total_candidates: int,
    funnel: Counter,
    skip_reasons: Counter,
) -> dict:
    return {
        "total_hits": total_hits,
        "total_candidates_checked": total_candidates,
        "funnel": dict(funnel),
        "top_skip_reasons": skip_reasons.most_common(8),
    }


__all__ = ["run_hot_company_search_v2"]
