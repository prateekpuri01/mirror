"""Unit tests for hot search v2 modules — ranking, discovery cache,
URL classifier, and the title-only XHR shape walker.

LLM and DB dependencies are mocked. No network calls. Pairs with the
existing test_hot_search_core.py which covers v1 helpers that v2 reuses.
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.hot_search.careers_titles import (
    _extract_jobs_from_xhr,
    _looks_like_job_list_shape,
)
from app.services.hot_search.discovery_cache import normalize_name
from app.services.hot_search.discovery_v2 import classify_url
from app.services.hot_search.ranking import (
    RERANK_STRICT_FLOOR,
    RERANK_TENTATIVE_FLOOR,
    RankedJob,
    batched_llm_rerank,
    build_job_doc,
    build_query_doc,
    cosine,
    cosine_top_k,
)

# ---------------------------------------------------------------------------
# classify_url — production must agree with the eval harness or scoring
# behavior drifts between dev and prod.
# ---------------------------------------------------------------------------


class TestClassifyUrl:
    @pytest.mark.parametrize(
        "url,expected_rank,expected_prefix",
        [
            ("https://boards.greenhouse.io/acme/jobs/123", 1, "ats:greenhouse"),
            ("https://jobs.lever.co/stripe/uuid-here", 1, "ats:lever"),
            ("https://jobs.ashbyhq.com/notion/abc", 1, "ats:ashby"),
            # Direct posting on company domain
            ("https://stripe.com/jobs/listing/ml-eng/7079044", 2, "direct"),
            ("https://microsoft.com/job/123456/Senior-Engineer", 2, "direct"),
            # Careers landing page
            ("https://anthropic.com/careers", 3, "careers"),
            ("https://acme.io/jobs", 3, "careers"),
            # Aggregator noise — must be rank 0
            ("https://linkedin.com/jobs/view/12345", 0, "aggregator"),
            ("https://www.indeed.com/jobs?q=ml", 0, "aggregator"),
            ("https://www.glassdoor.com/Jobs/Anthropic.htm", 0, "aggregator"),
            ("https://wellfound.com/jobs/anthropic", 0, "aggregator"),
            # Malformed
            ("not-a-url", 0, "invalid"),
            ("", 0, "invalid"),
        ],
    )
    def test_rank_and_kind(self, url, expected_rank, expected_prefix):
        rank, kind = classify_url(url)
        assert rank == expected_rank, f"{url} → rank={rank} kind={kind} (expected {expected_rank})"
        assert kind.startswith(expected_prefix), (
            f"{url} → kind={kind} (expected prefix {expected_prefix})"
        )


# ---------------------------------------------------------------------------
# normalize_name — the dedup key for the discovery cache. Spelling
# variants must collapse; distinct companies must not.
# ---------------------------------------------------------------------------


class TestNormalizeName:
    @pytest.mark.parametrize(
        "input_name,expected",
        [
            ("Anthropic", "anthropic"),
            ("Anthropic, PBC", "anthropic"),
            ("Anthropic Inc.", "anthropic"),
            ("Stripe, Inc", "stripe"),
            ("ACME Corp.", "acme"),
            ("Notion Labs", "notion labs"),  # Labs is not in suffix list
            ("OpenAI LLC", "openai"),
            ("  whitespace co  ", "whitespace"),
        ],
    )
    def test_collapses_variants(self, input_name, expected):
        assert normalize_name(input_name) == expected

    def test_keeps_distinct_companies_distinct(self):
        # These should NOT collapse to each other
        assert normalize_name("Apollo Research") != normalize_name("Apollo.io")
        assert normalize_name("Apple") != normalize_name("Apple Music")


# ---------------------------------------------------------------------------
# cosine + cosine_top_k — pure math, no network. Cover edge cases that
# bit us before in similar code (zero norms, dim mismatch).
# ---------------------------------------------------------------------------


class TestCosine:
    def test_identical_vectors_score_1(self):
        v = [0.5, 0.5, 0.5, 0.5]
        assert cosine(v, v) == pytest.approx(1.0, abs=1e-9)

    def test_orthogonal_vectors_score_0(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert cosine(a, b) == pytest.approx(0.0)

    def test_zero_vector_returns_0_not_error(self):
        assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
        assert cosine([1.0, 1.0], [0.0, 0.0]) == 0.0

    def test_dim_mismatch_raises(self):
        with pytest.raises(ValueError):
            cosine([1.0, 1.0], [1.0, 1.0, 1.0])

    def test_negative_components(self):
        # Antiparallel vectors → cosine = -1
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert cosine(a, b) == pytest.approx(-1.0)


class TestCosineTopK:
    def test_empty_docs(self):
        assert cosine_top_k([1.0], [], k=5) == []

    def test_zero_k(self):
        assert cosine_top_k([1.0, 0.0], [[1.0, 0.0]], k=0) == []

    def test_orders_by_cosine_desc(self):
        query = [1.0, 0.0]
        docs = [
            [0.5, 0.5],  # ~0.707
            [1.0, 0.0],  # 1.0
            [0.0, 1.0],  # 0.0
            [0.9, 0.1],  # ~0.994
        ]
        result = cosine_top_k(query, docs, k=4)
        # Indices in order of similarity to query
        indices = [i for i, _ in result]
        assert indices == [1, 3, 0, 2]

    def test_skips_zero_norm_docs(self):
        query = [1.0, 0.0]
        docs = [[1.0, 0.0], [0.0, 0.0], [0.5, 0.5]]
        result = cosine_top_k(query, docs, k=5)
        # Zero doc filtered out
        assert all(i != 1 for i, _ in result)

    def test_truncates_to_k(self):
        query = [1.0, 0.0]
        docs = [[1.0, 0.0]] * 10
        assert len(cosine_top_k(query, docs, k=3)) == 3


# ---------------------------------------------------------------------------
# build_query_doc / build_job_doc — text composition. Branching must
# match the guidance / no-guidance / reference-jobs priority rules.
# ---------------------------------------------------------------------------


class TestBuildQueryDoc:
    def test_guidance_leads_when_present(self):
        doc = build_query_doc(
            guidance="machine learning at AI safety lab",
            profile_data={"target_roles": [{"title": "Software Engineer"}]},
        )
        # Guidance text in output
        assert "machine learning at AI safety lab" in doc
        # Profile present as secondary
        assert "Software Engineer" in doc

    def test_no_guidance_uses_profile(self):
        doc = build_query_doc(
            guidance="",
            profile_data={
                "target_roles": [{"title": "ML Engineer"}],
                "domains": ["AI safety"],
            },
        )
        assert "ML Engineer" in doc
        assert "AI safety" in doc

    def test_reference_jobs_used_when_no_guidance(self):
        ref = "The user likes these jobs:\n1. ML Engineer at Anthropic"
        doc = build_query_doc(
            guidance="",
            profile_data={},
            reference_context=ref,
        )
        assert "Anthropic" in doc

    def test_fallback_when_all_empty(self):
        # Must not produce empty string — embeddings API rejects empty
        doc = build_query_doc(guidance="", profile_data={}, reference_context="")
        assert doc and doc.strip()


class TestBuildJobDoc:
    def test_includes_title_and_company(self):
        doc = build_job_doc(
            {
                "title": "Senior ML Engineer",
                "company": "Anthropic",
                "location": "San Francisco",
            }
        )
        assert "Senior ML Engineer" in doc
        assert "Anthropic" in doc
        assert "San Francisco" in doc

    def test_strips_html(self):
        doc = build_job_doc(
            {
                "title": "ML Engineer",
                "description_html": "<p>Build <b>ML</b> systems.</p>",
            }
        )
        assert "<p>" not in doc
        assert "<b>" not in doc
        assert "Build" in doc

    def test_empty_input_returns_fallback(self):
        # Empty embeds get replaced with a single space upstream, but
        # build_job_doc itself shouldn't return empty.
        doc = build_job_doc({})
        assert doc and doc.strip()


# ---------------------------------------------------------------------------
# batched_llm_rerank — taste-critical. Mock the LLM, test parsing,
# floor logic, and tentative-flag handling.
# ---------------------------------------------------------------------------


class TestBatchedLlmRerank:
    @pytest.mark.asyncio
    async def test_parses_json_array_and_sorts_desc(self):
        # LLM returns 3 jobs ranked 4, 2, 5
        llm_response = MagicMock()
        llm_response.choices = [
            MagicMock(
                message=MagicMock(
                    content='[{"i":1,"r":4,"why":"good"},{"i":2,"r":2,"why":"ok"},{"i":3,"r":5,"why":"perfect"}]'
                )
            )
        ]
        client = MagicMock()
        client.chat.completions.create = AsyncMock(return_value=llm_response)
        with patch("app.services.hot_search.ranking.get_openai_client", return_value=client):
            result = await batched_llm_rerank(
                [{"title": "a"}, {"title": "b"}, {"title": "c"}],
                guidance="test",
                top_k=10,
            )
        # Sorted descending: 5, 4, 2
        assert [r.relevance for r in result] == [5, 4, 2]
        # Indices map 1-indexed→0-indexed
        assert result[0].index == 2  # "c" was index 3 in prompt
        assert result[1].index == 0  # "a"
        assert result[2].index == 1  # "b" (tentative)

    @pytest.mark.asyncio
    async def test_drops_below_tentative_floor(self):
        llm_response = MagicMock()
        llm_response.choices = [
            MagicMock(
                message=MagicMock(content='[{"i":1,"r":3,"why":"ok"},{"i":2,"r":1,"why":"no"}]')
            )
        ]
        client = MagicMock()
        client.chat.completions.create = AsyncMock(return_value=llm_response)
        with patch("app.services.hot_search.ranking.get_openai_client", return_value=client):
            result = await batched_llm_rerank(
                [{"title": "a"}, {"title": "b"}],
                guidance="test",
            )
        # The r=1 entry is below the tentative floor (2), should be dropped
        assert len(result) == 1
        assert result[0].relevance == 3

    @pytest.mark.asyncio
    async def test_tentative_flag_set_at_floor(self):
        llm_response = MagicMock()
        llm_response.choices = [
            MagicMock(message=MagicMock(content='[{"i":1,"r":2,"why":"tentative"}]'))
        ]
        client = MagicMock()
        client.chat.completions.create = AsyncMock(return_value=llm_response)
        with patch("app.services.hot_search.ranking.get_openai_client", return_value=client):
            result = await batched_llm_rerank(
                [{"title": "a"}],
                guidance="test",
            )
        assert len(result) == 1
        assert result[0].relevance == 2
        assert result[0].is_tentative is True

    @pytest.mark.asyncio
    async def test_empty_jobs_returns_empty(self):
        result = await batched_llm_rerank([], guidance="test")
        assert result == []

    @pytest.mark.asyncio
    async def test_handles_dict_wrapper_response(self):
        # Some models wrap arrays in an outer object when response_format
        # is json_object. We unwrap common keys.
        llm_response = MagicMock()
        llm_response.choices = [
            MagicMock(message=MagicMock(content='{"results":[{"i":1,"r":4,"why":"good"}]}'))
        ]
        client = MagicMock()
        client.chat.completions.create = AsyncMock(return_value=llm_response)
        with patch("app.services.hot_search.ranking.get_openai_client", return_value=client):
            result = await batched_llm_rerank(
                [{"title": "a"}],
                guidance="test",
            )
        assert len(result) == 1
        assert result[0].relevance == 4

    @pytest.mark.asyncio
    async def test_drops_out_of_bounds_indices(self):
        llm_response = MagicMock()
        llm_response.choices = [
            MagicMock(message=MagicMock(content='[{"i":1,"r":4},{"i":99,"r":5}]'))
        ]
        client = MagicMock()
        client.chat.completions.create = AsyncMock(return_value=llm_response)
        with patch("app.services.hot_search.ranking.get_openai_client", return_value=client):
            result = await batched_llm_rerank(
                [{"title": "only one"}],
                guidance="test",
            )
        # i=99 is OOB; drop
        assert len(result) == 1
        assert result[0].index == 0

    @pytest.mark.asyncio
    async def test_handles_malformed_json_gracefully(self):
        llm_response = MagicMock()
        llm_response.choices = [MagicMock(message=MagicMock(content="not valid json"))]
        client = MagicMock()
        client.chat.completions.create = AsyncMock(return_value=llm_response)
        with patch("app.services.hot_search.ranking.get_openai_client", return_value=client):
            # Should not raise, should return empty
            result = await batched_llm_rerank(
                [{"title": "a"}],
                guidance="test",
            )
        assert result == []


# ---------------------------------------------------------------------------
# XHR shape walker — careers_titles._extract_jobs_from_xhr and
# _looks_like_job_list_shape. Tests cover envelope variants and
# location extraction.
# ---------------------------------------------------------------------------


class TestExtractJobsFromXhr:
    def test_top_level_list(self):
        captured = [
            {
                "url": "https://example.com/api/jobs",
                "data": [
                    {"title": "ML Eng", "url": "https://example.com/jobs/1", "location": "SF"},
                    {"title": "DS", "url": "https://example.com/jobs/2", "location": "NYC"},
                ],
            }
        ]
        result = _extract_jobs_from_xhr(captured)
        assert len(result) == 2
        assert result[0]["title"] == "ML Eng"
        assert result[0]["location"] == "SF"

    def test_workday_envelope(self):
        captured = [
            {
                "url": "https://acme.wd5.myworkdayjobs.com/api",
                "data": {
                    "jobPostings": [
                        {
                            "title": "Senior Eng",
                            "externalPath": "/job/Senior-Eng_R123",
                            "locationsText": "Remote",  # not in our key list
                            "primaryLocation": "USA",
                        }
                    ]
                },
            }
        ]
        result = _extract_jobs_from_xhr(captured)
        assert len(result) == 1
        # Relative externalPath resolved against API host
        assert result[0]["url"].startswith("https://acme.wd5.myworkdayjobs.com")
        assert result[0]["location"] == "USA"

    def test_graphql_envelope(self):
        captured = [
            {
                "url": "https://example.com/graphql",
                "data": {
                    "data": {
                        "jobs": [
                            {"title": "Backend Eng", "url": "https://example.com/jobs/5"},
                        ]
                    }
                },
            }
        ]
        result = _extract_jobs_from_xhr(captured)
        assert len(result) == 1
        assert result[0]["title"] == "Backend Eng"

    def test_dedups_by_url(self):
        captured = [
            {
                "url": "https://example.com/api",
                "data": [
                    {"title": "ML", "url": "https://example.com/jobs/1"},
                    {"title": "ML duplicate", "url": "https://example.com/jobs/1"},
                ],
            }
        ]
        result = _extract_jobs_from_xhr(captured)
        assert len(result) == 1

    def test_skips_items_missing_title_or_url(self):
        captured = [
            {
                "url": "https://example.com/api",
                "data": [
                    {"title": "Has title only"},
                    {"url": "https://example.com/jobs/x"},
                    {"title": "Both", "url": "https://example.com/jobs/y"},
                ],
            }
        ]
        result = _extract_jobs_from_xhr(captured)
        assert len(result) == 1
        assert result[0]["title"] == "Both"

    def test_empty_input(self):
        assert _extract_jobs_from_xhr([]) == []

    def test_non_job_response_ignored(self):
        # No matching envelope path
        captured = [{"url": "https://example.com/api/cart", "data": {"items": []}}]
        # Path "items" matches our list, but the list is empty → no output.
        result = _extract_jobs_from_xhr(captured)
        assert result == []


class TestLooksLikeJobListShape:
    def test_list_of_job_like_objects(self):
        assert (
            _looks_like_job_list_shape(
                [
                    {"title": "ML Eng", "company": "Acme"},
                    {"title": "Backend Eng", "company": "Acme"},
                    {"title": "DS", "company": "Acme"},
                ]
            )
            is True
        )

    def test_list_of_non_job_dicts_rejected(self):
        # No title-like fields
        assert (
            _looks_like_job_list_shape(
                [
                    {"price": 100, "currency": "USD"},
                    {"price": 200, "currency": "USD"},
                    {"price": 300, "currency": "USD"},
                ]
            )
            is False
        )

    def test_handles_nested_dict(self):
        # Job array nested one level deep
        assert (
            _looks_like_job_list_shape({"data": [{"title": "a"}, {"title": "b"}, {"title": "c"}]})
            is True
        )

    def test_empty_returns_false(self):
        assert _looks_like_job_list_shape([]) is False
        assert _looks_like_job_list_shape({}) is False
        assert _looks_like_job_list_shape(None) is False

    def test_short_list_returns_false(self):
        # Need >2 items for sample
        assert _looks_like_job_list_shape({"items": [{"title": "a"}, {"title": "b"}]}) is False
