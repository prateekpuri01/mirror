# Hot Search Eval — 2026-04-28

End-to-end evaluation of the AI-powered job-discovery pipeline. Each persona below is a realistic user query (guidance + location + min salary). For every job the pipeline returns, an LLM judge scores it 1–5 against the search criteria. Higher relevance = the pipeline is actually finding what was asked for; higher novelty = it's surfacing jobs not already in the database.

## Per-persona results

| Persona | Coverage | Novelty | Mean relevance (1–5) | Hits (ATS / lead / tracked) | Imported (novel) | Wall time |
|---|---|---|---|---|---|---|
| `ml_engineer_loose` | ✅ | 100% | 2.50 | 5 / 0 / 0 | 43 (43) | 898s |
| `ai_policy` | ❌ | — | — | 0 / 0 / 0 | 0 (0) | 1594s |

## Aggregate

- **Coverage:** 1/2 personas (50%) returned at least one hit
- **Mean relevance:** 2.50 / 5  _(LLM judge scoring of returned jobs against the search query)_
- **Mean novelty:** 100%  _(jobs surfaced that weren't already in the DB snapshot)_
- **Total imported:** 43 jobs across 2 personas (43 novel)
- **Mean wall time:** 1246s per persona
- **Estimated cost:** ~$0.04 (rough; see source for assumptions)

## Source breakdown

Where the LLM judge's high-relevance hits actually came from. The slug-harvester + aggregator layer (`hn_who_is_hiring`, `remotive`, `themuse`, `arbeitnow`) feeds candidates into the same evaluation pipeline as web-search-discovered companies, so all sources show up here.

| Source | Hits |
|---|---|
| `hn_who_is_hiring` | 5 |

## Where candidates dropped (0-hit personas)

When a persona returned no hits, this is the orchestrator's candidate funnel: how many candidates entered, where they were dropped, and (if any) the most-cited skip reasons.

### `ai_policy`

- `aggregator_entries`: 398
- `seed_candidates`: 312
- `candidates_seen`: 394
- `already_checked`: 47
- `dedup_dropped`: 14
- `tracked_no_match`: 5
- `direct_cap_reached`: 79
- `direct_miss`: 65
- `full_miss`: 136
- `final_hits`: 0

Top skip reasons:
  - `56` × Failed location/salary verification
  - `24` × No jobs in target location / above salary threshold
  - `23` × Lead skipped (filters active, can't verify)
  - `6` × No open jobs found
  - `5` × Extraction failed: This URL doesn't look like a job posting.

## Top finds (highest LLM-judged relevance)

- **4/5** — Solace Health / [Data Scientist](https://jobs.ashbyhq.com/Solace/94e34d8e-264f-4cd6-a3d8-12492aa3c203)  _(persona: `ml_engineer_loose`)_
- **3/5** — Solace Health / [Data Analyst](https://jobs.ashbyhq.com/Solace/d33b9659-7c3d-48a9-9a24-dd0f903e3a03)  _(persona: `ml_engineer_loose`)_
- **3/5** — Solace Health / [Data Analyst - Customer Experience](https://jobs.ashbyhq.com/Solace/0d10836e-2faf-4dd4-b187-9cc830e17613)  _(persona: `ml_engineer_loose`)_
- **3/5** — Solace Health / [Data Engineer](https://jobs.ashbyhq.com/Solace/f19207b3-5fad-4cd4-9a7e-5dd64fb78401)  _(persona: `ml_engineer_loose`)_
- **3/5** — Solace Health / [Staff Software Engineer](https://jobs.ashbyhq.com/Solace/87748158-42b6-4c55-91ca-8f3d95799ee9)  _(persona: `ml_engineer_loose`)_

## Weakest finds (where the pipeline misfired)

- **1/5** — Solace Health / Lead Commercial Counsel   _(persona: `ml_engineer_loose`)_
- **1/5** — Solace Health / Senior Partnership Marketing Manager  _(persona: `ml_engineer_loose`)_
- **1/5** — Solace Health / Growth Product Designer   _(persona: `ml_engineer_loose`)_

---

_Generated from `hot_search` JSON at timestamp `20260428T173608Z`. To regenerate: `./backend/scripts/eval/run.sh`._
