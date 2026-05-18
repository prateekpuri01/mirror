"""Unit tests for scoring_runner.py — pure parts only.

The actual `score_pair()` calls a live LLM, so we don't test it here. We test
the in-memory pieces: `_make_fake_job` (the SimpleNamespace adapter that lets
us reuse `format_job_for_scoring` without a DB) and the `ScoredPair` dataclass.
"""

from __future__ import annotations

import pytest

from tests.eval.external.scoring_runner import ScoredPair, _make_fake_job


# ---------------------------------------------------------------------------
# _make_fake_job — adapter from a plain dict to the Job ORM interface
# ---------------------------------------------------------------------------


class TestMakeFakeJob:
    def test_basic_fields(self):
        job_dict = {
            "title": "Senior ML Engineer",
            "company": "OpenAI",
            "description": "Build agents.",
            "salary_min": 200000,
            "salary_max": 350000,
        }
        fake = _make_fake_job(job_dict)
        assert fake.title == "Senior ML Engineer"
        assert fake.company == "OpenAI"
        assert fake.description == "Build agents."
        assert fake.salary_min == 200000
        assert fake.salary_max == 350000

    def test_display_attributes_mirror_canonical(self):
        """format_job_for_scoring uses display_title/display_company; the
        adapter mirrors them from title/company."""
        fake = _make_fake_job({"title": "X", "company": "Y"})
        assert fake.display_title == "X"
        assert fake.display_company == "Y"

    def test_optional_fields_default_to_none(self):
        fake = _make_fake_job({"title": "X", "company": "Y"})
        assert fake.salary_min is None
        assert fake.salary_max is None
        assert fake.user_notes is None
        assert fake.extra_metadata is None
        # description defaults to "" not None so format_job_for_scoring slicing works
        assert fake.description == ""

    def test_extra_metadata_passthrough(self):
        meta = {"team_name": "Frontier Red Team"}
        fake = _make_fake_job({"title": "X", "company": "Y", "extra_metadata": meta})
        assert fake.extra_metadata == meta

    def test_user_notes_passthrough(self):
        fake = _make_fake_job({"title": "X", "company": "Y", "user_notes": "I know the hiring manager"})
        assert fake.user_notes == "I know the hiring manager"

    def test_compatible_with_format_job_for_scoring(self):
        """End-to-end check: the fake job can be passed to format_job_for_scoring
        without missing-attribute errors. This is the main risk of the adapter
        falling out of sync with the real Job ORM."""
        from app.ai.prompts import format_job_for_scoring

        fake = _make_fake_job({
            "title": "Senior ML Engineer",
            "company": "OpenAI",
            "description": "Build agents.",
            "salary_min": 200000,
            "salary_max": 350000,
        })
        formatted = format_job_for_scoring(fake)
        assert "Senior ML Engineer" in formatted
        assert "OpenAI" in formatted
        assert "Build agents" in formatted
        assert "200,000" in formatted


# ---------------------------------------------------------------------------
# ScoredPair dataclass
# ---------------------------------------------------------------------------


class TestScoredPair:
    def test_basic_construction(self):
        sp = ScoredPair(
            role_fit_score=75,
            interest_fit_score=82,
            composite=77.8,
            role_fit_detail={"hard_skills": {"score": 22}},
            interest_fit_detail={"role_alignment": {"score": 20}},
        )
        assert sp.role_fit_score == 75
        assert sp.interest_fit_score == 82
        assert sp.composite == 77.8
        assert sp.error is None

    def test_error_field(self):
        sp = ScoredPair(
            role_fit_score=0,
            interest_fit_score=0,
            composite=0.0,
            role_fit_detail={},
            interest_fit_detail={},
            error="LLM returned 400",
        )
        assert sp.error == "LLM returned 400"
