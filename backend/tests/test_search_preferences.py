"""Tests for search preference consumer wiring — new fields + legacy fallback."""

import re

import pytest

from app.ai.prefilter import build_keyword_lists
from app.ai.prompts import build_interest_fit_system, build_company_enrichment_system
from app.ai.scoring import _build_compact_profile
from app.services.company_discovery import _build_keyword_sets


class TestPrefilterKeywords:
    """build_keyword_lists() reads exclusions or falls back to legacy."""

    def test_new_fields(self, sample_profile_new):
        exclude, _include = build_keyword_lists(sample_profile_new)
        assert "defense contractor" in exclude
        assert "surveillance" in exclude
        assert "adtech" in exclude

    def test_legacy_fallback(self, sample_profile_legacy):
        exclude, _include = build_keyword_lists(sample_profile_legacy)
        # Legacy fields go through _extract_keywords — check known substrings
        lowered = [e.lower() for e in exclude]
        assert any("defense" in e for e in lowered)
        assert any("surveillance" in e for e in lowered)
        # org_anti_patterns should also be included
        assert any("remote" in e or "micromanagement" in e for e in lowered)

    def test_include_unchanged(self, sample_profile_new):
        """INCLUDE list still reads target_roles + domains + skills."""
        _exclude, include = build_keyword_lists(sample_profile_new)
        assert "research scientist" in include
        assert "python" in include


class TestInterestFitPrompt:
    """build_interest_fit_system() includes free-text verbatim or legacy fallback."""

    def test_new_fields(self, sample_profile_new):
        prompt = build_interest_fit_system(sample_profile_new)
        assert "Research-heavy roles at mission-driven orgs" in prompt
        assert "Defense contractors, surveillance tech" in prompt
        assert "What the candidate is looking for:" in prompt
        assert "What the candidate wants to AVOID" in prompt
        # Supplementary keywords should appear
        assert "ai safety" in prompt
        assert "Hard exclusion keywords:" in prompt

    def test_legacy_fallback(self, sample_profile_legacy):
        prompt = build_interest_fit_system(sample_profile_legacy)
        assert "Academic research labs" in prompt
        assert "Defense contractors" in prompt

    def test_avoidance_instruction(self, sample_profile_new):
        prompt = build_interest_fit_system(sample_profile_new)
        assert "Check avoidances/exclusions FIRST" in prompt


class TestCompactProfile:
    """_build_compact_profile() includes new-format or legacy sections."""

    def test_new_fields(self, sample_profile_new):
        text = _build_compact_profile(sample_profile_new)
        assert "Looking for:" in text
        assert "Research-heavy roles" in text
        assert "Not looking for:" in text
        assert "Defense contractors" in text
        assert "Positive signals:" in text

    def test_legacy_fallback(self, sample_profile_legacy):
        text = _build_compact_profile(sample_profile_legacy)
        assert "Preferred org types" in text
        assert "Deal-breakers:" in text


class TestHotSearchTemplate:
    """_QUERY_USER_TEMPLATE uses new placeholders."""

    def test_template_has_new_placeholders(self):
        from app.services.hot_company_search import _QUERY_USER_TEMPLATE

        assert "{looking_for}" in _QUERY_USER_TEMPLATE
        assert "{not_looking_for}" in _QUERY_USER_TEMPLATE
        # Old placeholders should be gone
        assert "{industries}" not in _QUERY_USER_TEMPLATE
        assert "{deal_breakers}" not in _QUERY_USER_TEMPLATE


class TestCompanyEnrichment:
    """build_company_enrichment_system() uses looking_for or falls back."""

    def test_new_fields(self, sample_profile_new):
        prompt = build_company_enrichment_system(sample_profile_new)
        # looking_for[:100] should appear
        assert "Research-heavy roles" in prompt

    def test_legacy_fallback(self, sample_profile_legacy):
        prompt = build_company_enrichment_system(sample_profile_legacy)
        assert "Academic research labs" in prompt

    def test_empty_fallback(self):
        prompt = build_company_enrichment_system({})
        assert "research-oriented, mission-driven work" in prompt


class TestDiscoveryKeywords:
    """_build_keyword_sets() tokenizes positive_signals / exclusions."""

    def test_new_fields(self, sample_profile_new):
        kw = _build_keyword_sets(sample_profile_new)
        # positive_signals tokenized into industry_keywords
        assert "safety" in kw["industry_keywords"]
        assert "mission" in kw["industry_keywords"]
        # exclusions lowercased into deal_breakers
        assert "defense contractor" in kw["deal_breakers"]
        assert "surveillance" in kw["deal_breakers"]

    def test_legacy_fallback(self, sample_profile_legacy):
        kw = _build_keyword_sets(sample_profile_legacy)
        # industries_ranked tokenized
        assert "academic" in kw["industry_keywords"]
        # deal_breakers from legacy
        assert any("defense" in b for b in kw["deal_breakers"])
