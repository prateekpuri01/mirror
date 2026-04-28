"""Unit tests for the high-leverage hot-search functions.

These cover the picker, verifier, slug-harvester, and relevance scorer —
the pieces that contain real engineering judgment and have produced real
bugs in the past (extraction-fail bypass, null-salary policy, URL→slug
extraction, etc.). Each test class targets one function.

LLM/HTTP dependencies are mocked using the same `unittest.mock` pattern
established in `tests/test_keyword_generator.py`. No network calls.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.scrapers.base import ScrapedJob
from app.scrapers.discovery_adapters import AggregatorEntry
from app.services.company_discovery import score_job_relevance
from app.services.hot_search.discovery import _harvest_candidates_from_entries
from app.services.hot_search.evaluation import (
    PICKER_RELEVANCE_FLOOR,
    _pick_best_job_for_guidance,
    _verify_jobs_with_extraction,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _job(**overrides) -> dict:
    """Default verifier-shaped job dict; override fields per test."""
    base = {
        "title": "Senior ML Engineer",
        "url": "https://boards.greenhouse.io/acme/jobs/1",
        "location": "San Francisco, CA",
        "description_html": "<p>Build ML systems.</p>",
        "salary_min": None,
        "salary_max": None,
        "remote": False,
    }
    base.update(overrides)
    return base


def _mock_openai_chat_completion(content: str) -> MagicMock:
    """Build a MagicMock matching the OpenAI chat-completion response shape."""
    message = MagicMock()
    message.content = content
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    return response


# ---------------------------------------------------------------------------
# _verify_jobs_with_extraction — the verifier
# ---------------------------------------------------------------------------


class TestVerifyJobsWithExtraction:
    """The verifier is the single source of truth for hard rejects on
    location and salary. Bugs here previously let NYC and Remote-only jobs
    leak past a SF filter; tests below pin the correct behavior.
    """

    @pytest.mark.asyncio
    async def test_cheap_salary_check_rejects_below_threshold(self):
        """When the scraper has already populated salary_max < min_salary,
        we reject without calling the LLM at all."""
        jobs = [_job(salary_max=150_000)]
        # Patch _extract_from_preview so we can assert it was NOT called.
        with patch(
            "app.services.hot_search.evaluation._extract_from_preview",
            new=AsyncMock(),
        ) as mock_extract:
            result = await _verify_jobs_with_extraction(
                jobs, locations=[], min_salary=200_000,
            )
        assert result == []
        mock_extract.assert_not_called()

    @pytest.mark.asyncio
    async def test_extraction_failure_with_filters_rejects(self):
        """The bug we fixed: when LLM extraction returns None and filters
        are active, the verifier MUST reject. Letting it pass-by-default
        is how NYC jobs slipped past a SF filter."""
        jobs = [_job(location="NYC", salary_max=None)]
        with patch(
            "app.services.hot_search.evaluation._extract_from_preview",
            new=AsyncMock(return_value=None),
        ):
            result = await _verify_jobs_with_extraction(
                jobs, locations=["San Francisco"], min_salary=None,
            )
        assert result == []

    @pytest.mark.asyncio
    async def test_extraction_failure_without_filters_keeps_job(self):
        """Symmetric to above: with no filters set there's no policy to
        apply, so a failed extraction is benign — keep the job."""
        jobs = [_job()]
        with patch(
            "app.services.hot_search.evaluation._extract_from_preview",
            new=AsyncMock(return_value=None),
        ):
            result = await _verify_jobs_with_extraction(
                jobs, locations=[], min_salary=None,
            )
        assert len(result) == 1
        assert result[0]["title"] == "Senior ML Engineer"

    @pytest.mark.asyncio
    async def test_null_salary_after_extraction_rejects_when_min_set(self):
        """Lane A6 policy: if min_salary is set and the LLM still can't find
        a salary in the JD, treat unknown as failing."""
        jobs = [_job()]
        with patch(
            "app.services.hot_search.evaluation._extract_from_preview",
            new=AsyncMock(return_value={
                "salary_max": None,
                "location_match": True,
            }),
        ):
            result = await _verify_jobs_with_extraction(
                jobs, locations=["San Francisco"], min_salary=200_000,
            )
        assert result == []

    @pytest.mark.asyncio
    async def test_extracted_salary_below_threshold_rejects(self):
        jobs = [_job()]
        with patch(
            "app.services.hot_search.evaluation._extract_from_preview",
            new=AsyncMock(return_value={
                "salary_max": 150_000,
                "location_match": True,
            }),
        ):
            result = await _verify_jobs_with_extraction(
                jobs, locations=["San Francisco"], min_salary=200_000,
            )
        assert result == []

    @pytest.mark.asyncio
    async def test_location_mismatch_rejects(self):
        """When location_match is False or None and a location filter is
        active, reject — only an explicit True passes."""
        jobs = [_job(location="NYC")]
        with patch(
            "app.services.hot_search.evaluation._extract_from_preview",
            new=AsyncMock(return_value={
                "salary_max": 250_000,
                "location_match": False,
            }),
        ):
            result = await _verify_jobs_with_extraction(
                jobs, locations=["San Francisco"], min_salary=None,
            )
        assert result == []

    @pytest.mark.asyncio
    async def test_pass_annotates_with_extracted_data(self):
        """Happy path: location_match=True + salary_max above threshold →
        job passes and is annotated with extracted fields."""
        jobs = [_job()]
        with patch(
            "app.services.hot_search.evaluation._extract_from_preview",
            new=AsyncMock(return_value={
                "salary_max": 250_000,
                "salary_min": 200_000,
                "location_match": True,
                "work_model": "hybrid",
                "locations": [{"city": "San Francisco", "state": "CA"}],
            }),
        ):
            result = await _verify_jobs_with_extraction(
                jobs, locations=["San Francisco"], min_salary=200_000,
            )
        assert len(result) == 1
        assert result[0]["extracted_salary_max"] == 250_000
        assert result[0]["extracted_work_model"] == "hybrid"


# ---------------------------------------------------------------------------
# _pick_best_job_for_guidance — the LLM picker
# ---------------------------------------------------------------------------


class TestPickBestJobForGuidance:
    """The picker collapses up to N candidate jobs at one company down to
    a single best match for the user's guidance. Tests cover the cheap
    pre-filter, LLM response parsing, and the relevance-floor reject."""

    @pytest.mark.asyncio
    async def test_empty_jobs_returns_none(self):
        result = await _pick_best_job_for_guidance(
            jobs=[], guidance="ML engineer",
        )
        assert result == (None, None)

    @pytest.mark.asyncio
    async def test_all_jobs_fail_prefilter_returns_rejection(self):
        """When every candidate fails the cheap location/salary filter,
        the picker returns (None, rejection_info) WITHOUT calling the LLM."""
        # Two jobs, both NYC, user wants SF — cheap location filter rejects both
        jobs = [
            {"title": "MLE", "location": "NYC", "remote": False},
            {"title": "Senior MLE", "location": "Boston", "remote": False},
        ]
        with patch("app.services.hot_search.evaluation.get_openai_client") as mock_client_factory:
            picked, rejection = await _pick_best_job_for_guidance(
                jobs, guidance="ML", locations=["San Francisco"],
            )
        assert picked is None
        assert rejection is not None
        assert "prefilter" in rejection["reason"]
        mock_client_factory.assert_not_called()

    @pytest.mark.asyncio
    async def test_relevance_below_floor_rejects(self):
        """LLM picks a job but rates it below PICKER_RELEVANCE_FLOOR — reject
        with structured rejection_info."""
        jobs = [{"title": "Office Manager", "location": "SF", "remote": False}]
        # LLM picks index 1 with relevance 1 (below floor of 3)
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_mock_openai_chat_completion("1:1"),
        )
        with patch(
            "app.services.hot_search.evaluation.get_openai_client",
            return_value=mock_client,
        ):
            picked, rejection = await _pick_best_job_for_guidance(
                jobs, guidance="ML engineer",
            )
        assert picked is None
        assert rejection is not None
        assert rejection["best_score"] == 1
        assert "below threshold" in rejection["reason"]

    @pytest.mark.asyncio
    async def test_valid_pick_returns_job(self):
        """LLM returns a high-relevance pick → job dict is returned."""
        jobs = [
            {"title": "Office Manager", "location": "SF", "remote": False},
            {"title": "Senior ML Engineer", "location": "SF", "remote": False},
        ]
        # Pick index 2, relevance 5
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_mock_openai_chat_completion("2:5"),
        )
        with patch(
            "app.services.hot_search.evaluation.get_openai_client",
            return_value=mock_client,
        ):
            picked, rejection = await _pick_best_job_for_guidance(
                jobs, guidance="ML engineer",
            )
        assert picked is not None
        assert picked["title"] == "Senior ML Engineer"
        assert rejection is None

    @pytest.mark.asyncio
    async def test_zero_zero_response_means_no_match(self):
        """LLM returns '0:0' meaning no candidate is a real match."""
        jobs = [{"title": "Gardener", "location": "SF", "remote": False}]
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_mock_openai_chat_completion("0:0"),
        )
        with patch(
            "app.services.hot_search.evaluation.get_openai_client",
            return_value=mock_client,
        ):
            picked, rejection = await _pick_best_job_for_guidance(
                jobs, guidance="ML engineer",
            )
        assert picked is None
        assert rejection is not None  # has best_title + reason

    @pytest.mark.asyncio
    async def test_relevance_floor_constant_is_sane(self):
        """Floor is in the 1-5 picker scale and isn't accidentally 0 or 5+."""
        assert 1 <= PICKER_RELEVANCE_FLOOR <= 5


# ---------------------------------------------------------------------------
# _harvest_candidates_from_entries — the slug-harvester
# ---------------------------------------------------------------------------


class TestHarvestCandidatesFromEntries:
    """The harvester is what makes Lane B work — turning aggregator URLs
    into ATS slugs that flow through comprehensive scrape. Tests pin the
    URL-pattern path, the name-probe fallback, the direct-URL fallback,
    and dedup."""

    @pytest.mark.asyncio
    async def test_greenhouse_url_yields_ats_candidate(self):
        entry = AggregatorEntry(
            company_name="Acme",
            job_url="https://boards.greenhouse.io/acmecorp/jobs/12345",
            title="ML Engineer",
            source="hn_who_is_hiring",
        )
        seen: set[str] = set()
        with patch(
            "app.services.hot_search.discovery._probe_name_for_ats",
            new=AsyncMock(),  # should NOT be called
        ) as mock_probe:
            result = await _harvest_candidates_from_entries(
                [entry], http_client=MagicMock(),
                seen=seen, existing_companies_lower=set(),
            )
        assert len(result) == 1
        assert result[0].ats == "greenhouse"
        assert result[0].slug == "acmecorp"
        # No fallback probe needed when URL parses cleanly
        mock_probe.assert_not_called()

    @pytest.mark.asyncio
    async def test_lever_url_with_specific_job_still_resolves_comprehensive(self):
        """The harvester ALWAYS prefers the comprehensive board scrape, even
        when the URL has a specific job ID. This is what makes Lane B
        different from Tavily-style site-search hits, which prefer the
        single direct-import."""
        entry = AggregatorEntry(
            company_name="Beta",
            job_url="https://jobs.lever.co/beta/abc-def-uuid-123",
            title="ML Engineer",
            source="remotive",
        )
        result = await _harvest_candidates_from_entries(
            [entry], http_client=MagicMock(),
            seen=set(), existing_companies_lower=set(),
        )
        assert len(result) == 1
        assert result[0].ats == "lever"
        assert result[0].slug == "beta"
        # direct_job_url should NOT be set — this is a comprehensive candidate
        assert result[0].direct_job_url is None

    @pytest.mark.asyncio
    async def test_name_probe_fallback_when_url_not_ats(self):
        """If the URL isn't on a known ATS, fall back to probing the company
        name against live ATS APIs."""
        entry = AggregatorEntry(
            company_name="Gamma Corp",
            job_url="https://gamma.example.com/jobs/123",
            title="ML Engineer",
            source="themuse",
        )
        with patch(
            "app.services.hot_search.discovery._probe_name_for_ats",
            new=AsyncMock(return_value=("ashby", "gamma")),
        ):
            result = await _harvest_candidates_from_entries(
                [entry], http_client=MagicMock(),
                seen=set(), existing_companies_lower=set(),
            )
        assert len(result) == 1
        assert result[0].ats == "ashby"
        assert result[0].slug == "gamma"
        assert result[0].direct_job_url is None

    @pytest.mark.asyncio
    async def test_direct_url_fallback_when_probe_fails(self):
        """If both URL-pattern and name-probe fail, fall back to a
        direct-URL candidate (single-job preview, verified downstream)."""
        entry = AggregatorEntry(
            company_name="Delta Inc",
            job_url="https://delta.example.com/jobs/456",
            title="ML Engineer",
            source="arbeitnow",
        )
        with patch(
            "app.services.hot_search.discovery._probe_name_for_ats",
            new=AsyncMock(return_value=None),
        ):
            result = await _harvest_candidates_from_entries(
                [entry], http_client=MagicMock(),
                seen=set(), existing_companies_lower=set(),
            )
        assert len(result) == 1
        assert result[0].ats is None
        assert result[0].slug is None
        assert result[0].direct_job_url == entry.job_url

    @pytest.mark.asyncio
    async def test_dedup_against_seen_set(self):
        """Two entries pointing at the same ATS slug → deduped via `seen`."""
        e1 = AggregatorEntry(
            company_name="Acme",
            job_url="https://boards.greenhouse.io/acme/jobs/1",
            title="MLE", source="hn",
        )
        e2 = AggregatorEntry(
            company_name="Acme",
            job_url="https://boards.greenhouse.io/acme/jobs/2",
            title="DS", source="remotive",
        )
        result = await _harvest_candidates_from_entries(
            [e1, e2], http_client=MagicMock(),
            seen=set(), existing_companies_lower=set(),
        )
        assert len(result) == 1
        assert result[0].slug == "acme"

    @pytest.mark.asyncio
    async def test_skips_existing_tracked_company(self):
        """If the company is already in the user's tracked set, skip — the
        regular scraper pipeline will pick it up."""
        entry = AggregatorEntry(
            company_name="Already-Tracked Co",
            job_url="https://boards.greenhouse.io/already-tracked/jobs/1",
            title="MLE", source="hn",
        )
        result = await _harvest_candidates_from_entries(
            [entry], http_client=MagicMock(),
            seen=set(),
            existing_companies_lower={"already-tracked co"},
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_ats_candidates_sorted_before_direct(self):
        """Fix #4: ATS-resolved candidates run before direct-URL ones so
        the per-run direct cap doesn't crowd out high-leverage scrapes."""
        ats_entry = AggregatorEntry(
            company_name="Acme",
            job_url="https://boards.greenhouse.io/acme/jobs/1",
            title="MLE", source="hn",
        )
        direct_entry = AggregatorEntry(
            company_name="Beta",
            job_url="https://example.com/jobs/2",
            title="DS", source="hn",
        )
        with patch(
            "app.services.hot_search.discovery._probe_name_for_ats",
            new=AsyncMock(return_value=None),
        ):
            # Pass direct first to verify ordering happens at the end
            result = await _harvest_candidates_from_entries(
                [direct_entry, ats_entry], http_client=MagicMock(),
                seen=set(), existing_companies_lower=set(),
            )
        assert len(result) == 2
        # ATS should come first regardless of input order
        assert result[0].ats == "greenhouse"
        assert result[1].direct_job_url is not None


# ---------------------------------------------------------------------------
# score_job_relevance — pure function, no mocks
# ---------------------------------------------------------------------------


def _scraped(**kw) -> ScrapedJob:
    base = dict(
        title="Engineer", company_name="Acme", url="https://x", description="",
        description_html=None, location=None, remote=False,
        salary_min=None, salary_max=None,
    )
    base.update(kw)
    return ScrapedJob(**base)


class TestScoreJobRelevance:
    """Pure keyword-scoring function. Tests pin the documented bonus/penalty
    structure so future tweaks don't silently change ranking."""

    def test_exact_role_title_match_scores_high(self):
        kw = {"role_titles": {"ml engineer"}}
        score = score_job_relevance(_scraped(title="ML Engineer"), kw)
        assert score >= 40

    def test_token_overlap_scores_lower(self):
        kw = {"role_titles": {"machine learning engineer"}}
        # "ML Engineer" shares one token ("engineer") with "machine learning engineer"
        score = score_job_relevance(_scraped(title="ML Engineer"), kw)
        assert 0 < score < 40

    def test_no_overlap_scores_zero(self):
        kw = {"role_titles": {"backend engineer"}}
        score = score_job_relevance(_scraped(title="Sales Manager"), kw)
        assert score == 0

    def test_deal_breaker_in_title_penalizes(self):
        kw = {
            "role_titles": {"engineer"},
            "deal_breakers": {"crypto"},
        }
        with_breaker = score_job_relevance(
            _scraped(title="Crypto Engineer"), kw,
        )
        without_breaker = score_job_relevance(
            _scraped(title="Backend Engineer"), kw,
        )
        # With deal-breaker, score should be reduced by 30 points
        assert without_breaker - with_breaker >= 30

    def test_remote_bonus_when_user_wants_remote(self):
        kw = {
            "role_titles": {"engineer"},
            "remote_preference": "remote",
        }
        remote_job = score_job_relevance(
            _scraped(title="Engineer", remote=True), kw,
        )
        onsite_job = score_job_relevance(
            _scraped(title="Engineer", remote=False), kw,
        )
        assert remote_job - onsite_job == 10

    def test_score_is_clamped_to_0_100(self):
        # Stack lots of bonuses to overshoot 100
        kw = {
            "role_titles": {"senior staff principal lead ml engineer"},
            "domains": {"ai", "nlp", "ml"},
            "technical_skills": {"python", "pytorch", "transformers"},
            "industry_keywords": {"research", "science"},
            "seniority": {"senior"},
            "remote_preference": "remote",
        }
        sj = _scraped(
            title="Senior Staff Principal Lead ML Engineer AI NLP",
            description="python pytorch transformers research science",
            remote=True,
        )
        score = score_job_relevance(sj, kw)
        assert 0 <= score <= 100
