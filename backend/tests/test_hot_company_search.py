"""Unit tests for hot_company_search.py prefilter helpers.

These tests cover the cheap, LLM-free filtering helpers introduced to fix the
"hot search ignores location/salary specs" bug. They use lightweight ScrapedJob
fixtures and don't touch network or LLM.
"""

from __future__ import annotations

import pytest

from app.scrapers.base import ScrapedJob
from app.services.hot_company_search import (
    _expand_location,
    _job_passes_location_filter,
    _job_passes_salary_filter,
)


def _job(
    *,
    title: str = "Engineer",
    location: str | None = None,
    remote: bool = False,
    salary_min: int | None = None,
    salary_max: int | None = None,
) -> ScrapedJob:
    """Build a minimal ScrapedJob for tests."""
    return ScrapedJob(
        title=title,
        company_name="Acme",
        url="https://example.com/job/1",
        description="",
        description_html=None,
        location=location,
        remote=remote,
        salary_min=salary_min,
        salary_max=salary_max,
    )


# ---------------------------------------------------------------------------
# _expand_location — alias expansion
# ---------------------------------------------------------------------------


class TestExpandLocation:
    def test_no_alias_returns_self(self):
        # A location with no known alias just returns itself, lowercase
        assert _expand_location("Berlin") == {"berlin"}

    def test_sf_aliases(self):
        result = _expand_location("SF")
        assert "sf" in result
        assert "san francisco" in result
        assert "bay area" in result

    def test_san_francisco_aliases(self):
        result = _expand_location("San Francisco")
        assert "san francisco" in result
        assert "sf" in result
        assert "bay area" in result

    def test_nyc_aliases(self):
        result = _expand_location("NYC")
        assert "nyc" in result
        assert "new york" in result

    def test_dc_aliases(self):
        result = _expand_location("DC")
        assert "dc" in result
        assert "washington" in result

    def test_uk_aliases(self):
        result = _expand_location("UK")
        assert "uk" in result
        assert "united kingdom" in result
        assert "london" in result

    def test_normalizes_whitespace_and_case(self):
        assert _expand_location("  SF  ") == _expand_location("sf")


# ---------------------------------------------------------------------------
# _job_passes_location_filter
# ---------------------------------------------------------------------------


class TestLocationFilter:
    def test_no_filter_passes_everything(self):
        passes, _ = _job_passes_location_filter(_job(location="Mars"), None)
        assert passes is True

    def test_empty_filter_passes_everything(self):
        passes, _ = _job_passes_location_filter(_job(location="Mars"), [])
        assert passes is True

    def test_exact_match(self):
        passes, reason = _job_passes_location_filter(
            _job(location="San Francisco, CA"), ["San Francisco"]
        )
        assert passes is True
        assert "matched" in reason

    def test_case_insensitive(self):
        passes, _ = _job_passes_location_filter(
            _job(location="SAN FRANCISCO"), ["san francisco"]
        )
        assert passes is True

    def test_alias_match_sf_to_san_francisco(self):
        passes, _ = _job_passes_location_filter(
            _job(location="San Francisco, CA"), ["SF"]
        )
        assert passes is True

    def test_alias_match_san_francisco_to_sf(self):
        passes, _ = _job_passes_location_filter(_job(location="SF Bay"), ["San Francisco"])
        assert passes is True

    def test_bay_area_alias(self):
        passes, _ = _job_passes_location_filter(
            _job(location="Mountain View, CA — Bay Area"), ["SF"]
        )
        assert passes is True

    def test_nyc_to_new_york(self):
        passes, _ = _job_passes_location_filter(
            _job(location="New York, NY"), ["NYC"]
        )
        assert passes is True

    def test_remote_job_with_remote_filter(self):
        passes, reason = _job_passes_location_filter(
            _job(remote=True, location="USA Remote"), ["Remote"]
        )
        assert passes is True
        assert reason == "remote"

    def test_remote_job_without_remote_filter_uses_location(self):
        # Remote job in NY, filter is SF — should reject (location mismatch)
        passes, _ = _job_passes_location_filter(
            _job(remote=True, location="New York, NY"), ["SF"]
        )
        assert passes is False

    def test_unknown_location_passes_to_llm(self):
        # No location info → defer to LLM verification
        passes, reason = _job_passes_location_filter(_job(location=None), ["SF"])
        assert passes is True
        assert reason == "unknown_location"

    def test_empty_location_passes_to_llm(self):
        passes, _ = _job_passes_location_filter(_job(location=""), ["SF"])
        assert passes is True

    def test_vague_location_passes_to_llm(self):
        for vague in ("Anywhere", "Multiple offices", "Worldwide", "TBD", "Various"):
            passes, reason = _job_passes_location_filter(_job(location=vague), ["SF"])
            assert passes is True, f"vague '{vague}' should pass"
            assert reason == "vague_location"

    def test_known_mismatch_rejects(self):
        passes, reason = _job_passes_location_filter(
            _job(location="London, UK"), ["SF"]
        )
        assert passes is False
        assert "London" in reason

    def test_multiple_filters_any_match_passes(self):
        passes, _ = _job_passes_location_filter(
            _job(location="Boston, MA"), ["SF", "NYC", "Boston"]
        )
        assert passes is True

    def test_multiple_filters_no_match_rejects(self):
        passes, _ = _job_passes_location_filter(
            _job(location="Berlin, Germany"), ["SF", "NYC", "Boston"]
        )
        assert passes is False

    def test_substring_within_larger_location(self):
        # "Bay Area" filter should match "SF Bay Area office"
        passes, _ = _job_passes_location_filter(
            _job(location="SF Bay Area office"), ["Bay Area"]
        )
        assert passes is True


# ---------------------------------------------------------------------------
# _job_passes_salary_filter
# ---------------------------------------------------------------------------


class TestSalaryFilter:
    def test_no_filter_passes(self):
        assert _job_passes_salary_filter(_job(salary_max=50_000), None)[0] is True

    def test_zero_filter_passes(self):
        assert _job_passes_salary_filter(_job(salary_max=50_000), 0)[0] is True

    def test_above_threshold_passes(self):
        passes, _ = _job_passes_salary_filter(_job(salary_max=200_000), 150_000)
        assert passes is True

    def test_at_threshold_passes(self):
        passes, _ = _job_passes_salary_filter(_job(salary_max=150_000), 150_000)
        assert passes is True

    def test_below_threshold_rejects(self):
        passes, reason = _job_passes_salary_filter(_job(salary_max=100_000), 150_000)
        assert passes is False
        assert "100" in reason
        assert "150" in reason

    def test_unknown_salary_passes_to_llm(self):
        passes, _ = _job_passes_salary_filter(_job(salary_max=None), 150_000)
        assert passes is True

    def test_only_min_set_passes_to_llm(self):
        # Job has salary_min but not salary_max — we can't reject without max
        passes, _ = _job_passes_salary_filter(
            _job(salary_min=80_000, salary_max=None), 150_000
        )
        assert passes is True


# ---------------------------------------------------------------------------
# Integration: prefilter + remote/location together
# ---------------------------------------------------------------------------


class TestPrefilterCombinations:
    """End-to-end checks that mirror real-world ATS data."""

    def test_remote_job_passes_remote_or_sf_filter(self):
        # User wants SF OR remote — a remote-only job should pass via remote
        passes, reason = _job_passes_location_filter(
            _job(remote=True, location="Remote"), ["SF", "Remote"]
        )
        assert passes is True
        assert reason == "remote"

    def test_remote_in_location_string_with_remote_filter(self):
        # ATS often puts "Remote - US" in the location field but doesn't set
        # the structured remote=True flag. The substring match should still
        # catch it via the "remote" filter token.
        passes, _ = _job_passes_location_filter(
            _job(remote=False, location="Remote - US"), ["Remote"]
        )
        # This currently relies on substring matching against the filter alias.
        # "remote" is not in our alias map but the filter literal "remote"
        # should substring-match "remote - us".
        assert passes is True

    def test_sf_filter_rejects_ny_office(self):
        passes, _ = _job_passes_location_filter(
            _job(location="New York, NY"), ["San Francisco"]
        )
        assert passes is False

    def test_combined_location_and_salary_both_pass(self):
        sj = _job(location="San Francisco, CA", salary_max=300_000)
        assert _job_passes_location_filter(sj, ["SF"])[0] is True
        assert _job_passes_salary_filter(sj, 150_000)[0] is True

    def test_combined_location_passes_salary_fails(self):
        sj = _job(location="San Francisco, CA", salary_max=80_000)
        assert _job_passes_location_filter(sj, ["SF"])[0] is True
        assert _job_passes_salary_filter(sj, 150_000)[0] is False
