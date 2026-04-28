# Hot Search Eval — 2026-04-28

End-to-end evaluation of the AI-powered job-discovery pipeline. Each persona below is a realistic user query (guidance + location + min salary). For every job the pipeline returns, an LLM judge scores it 1–5 against the search criteria. Higher relevance = the pipeline is actually finding what was asked for; higher novelty = it's surfacing jobs not already in the database.

## Per-persona results

| Persona | Coverage | Novelty | Mean relevance (1–5) | Hits (ATS / lead / tracked) | Imported (novel) | Wall time |
|---|---|---|---|---|---|---|
| `ml_engineer_loose` | ✅ | 100% | 2.70 | 5 / 0 / 0 | 43 (43) | 1064s |
| `ai_policy` | ❌ | — | — | 0 / 0 / 0 | 0 (0) | 1739s |

## Aggregate

- **Coverage:** 1/2 personas (50%) returned at least one hit
- **Mean relevance:** 2.70 / 5  _(LLM judge scoring of returned jobs against the search query)_
- **Mean novelty:** 100%  _(jobs surfaced that weren't already in the DB snapshot)_
- **Total imported:** 43 jobs across 2 personas (43 novel)
- **Mean wall time:** 1401s per persona
- **Estimated cost:** ~$0.04 (rough; see source for assumptions)

## Source breakdown

Where the LLM judge's high-relevance hits actually came from. The slug-harvester + aggregator layer (`hn_who_is_hiring`, `remotive`, `themuse`, `arbeitnow`) feeds candidates into the same evaluation pipeline as web-search-discovered companies, so all sources show up here.

| Source | Hits |
|---|---|
| `hn_who_is_hiring` | 4 |
| `remotive` | 1 |

## Where candidates dropped (0-hit personas)

When a persona returned no hits, this is the orchestrator's candidate funnel: how many candidates entered, where they were dropped, and (if any) the most-cited skip reasons.

### `ai_policy`

- `aggregator_entries`: 399
- `seed_candidates`: 310
- `candidates_seen`: 384
- `already_checked`: 53
- `dedup_dropped`: 12
- `tracked_no_match`: 4
- `direct_cap_reached`: 73
- `direct_miss`: 67
- `full_miss`: 129
- `final_hits`: 0

Top skip reasons:
  - `58` × Failed location/salary verification
  - `23` × No jobs in target location / above salary threshold
  - `21` × Lead skipped (filters active, can't verify)
  - `6` × No open jobs found
  - `5` × Extraction failed: This URL doesn't look like a job posting.

## Top finds (highest LLM-judged relevance)

- **5/5** — Higharc / [Sr. AI Engineer, Labs](https://jobs.ashbyhq.com/higharc/4cedbfac-f0ad-42c0-abed-3f161fca27ef)  _(persona: `ml_engineer_loose`)_
- **4/5** — Matterworks / [Senior Machine Learning Scientist](https://jobs.ashbyhq.com/matterworks/41be74b6-c7cf-4ad3-a908-510a03efe0f3)  _(persona: `ml_engineer_loose`)_
- **3/5** — Matterworks / [Data Manager](https://jobs.ashbyhq.com/matterworks/5a5f7cd3-bf06-4238-b3e6-87c14d53fbc3)  _(persona: `ml_engineer_loose`)_
- **3/5** — Higharc / [Senior Product Manager, AI Experiences](https://jobs.ashbyhq.com/higharc/da3e4a14-a3cb-4060-8a61-6ee23294d222)  _(persona: `ml_engineer_loose`)_
- **3/5** — Higharc / [Research Engineer ](https://jobs.ashbyhq.com/higharc/39a7afd6-0124-49dc-826e-d2248c284cbb)  _(persona: `ml_engineer_loose`)_

## Weakest finds (where the pipeline misfired)

- **1/5** — Higharc / Sr. Software Engineer, Integrations  _(persona: `ml_engineer_loose`)_
- **1/5** — Higharc / Sr. Design Systems Engineer, Labs  _(persona: `ml_engineer_loose`)_
- **2/5** — Matterworks / Future Opportunities  _(persona: `ml_engineer_loose`)_

---

_Generated from `hot_search` JSON at timestamp `20260428T154900Z`. To regenerate: `./backend/scripts/eval/run.sh`._
