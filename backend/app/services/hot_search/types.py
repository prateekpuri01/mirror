"""Data types for the hot-search pipeline.

These dataclasses are the lingua franca between the discovery, evaluation,
and orchestration modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CompanyCandidate:
    name: str
    url: str | None = None
    ats: str | None = None
    slug: str | None = None
    source: str = ""
    direct_job_url: str | None = None  # Set when this candidate is a one-off job URL
    # Origin tracks whether a candidate came from the guidance-blind
    # aggregator harvest (HN/Remotive/Muse/Arbeitnow) or from a
    # guidance-driven LLM query. Used by the orchestration to split the
    # direct-import budget so aggregator candidates can't crowd out
    # higher-relevance query-driven ones.
    origin: str = "query"  # "aggregator" | "query"


@dataclass
class CompanyHit:
    name: str
    ats: str
    slug: str
    website: str | None = None
    total_jobs: int = 0
    relevant_jobs: int = 0
    top_jobs: list[dict] = field(default_factory=list)
    source: str = ""
    description: str = ""
    match_reason: str = ""
    # Hit kind:
    #   "ats"      — full ATS-scraped result with relevant jobs (default)
    #   "lead"     — company we couldn't scrape, surfaced as careers-page link
    #   "tracked"  — company already in the user's DB with matching jobs
    # Companies considered but rejected (by profile filter or LLM picker)
    # are NOT emitted as hits — they're emitted as "skip" events with a
    # detailed reason so the activity log shows what was considered and why.
    kind: str = "ats"
    careers_url: str | None = None  # Set for "lead" kind
    company_id: str | None = None  # Set for "tracked" kind
    # Two-tier matching: a hit is "tentative" when its best job's
    # profile-fit score landed below the strict threshold but above the
    # loose one. We emit a capped number of tentative hits so exploratory
    # searches into out-of-profile domains always return SOMETHING — the
    # user can decide whether to investigate. UI shows these visually
    # distinct.
    is_tentative: bool = False
    match_score: int | None = None


@dataclass
class SearchEvent:
    event: str  # "status" | "hit" | "candidate" | "error" | "done"
    data: dict


__all__ = ["CompanyCandidate", "CompanyHit", "SearchEvent"]
