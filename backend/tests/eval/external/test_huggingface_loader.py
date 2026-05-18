"""Unit tests for huggingface_loader.py — pure parsing logic, no network.

We synthesize fake "rows from datasets-server" payloads and verify the example
extraction + stratification logic.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from tests.eval.external.huggingface_loader import (
    HFExample,
    LABEL_TO_ORDINAL,
    example_to_job_dict,
    fetch_examples,
)


# ---------------------------------------------------------------------------
# Label ordinals
# ---------------------------------------------------------------------------


def test_label_to_ordinal_monotonic():
    """Higher fit = higher ordinal."""
    assert LABEL_TO_ORDINAL["No Fit"] < LABEL_TO_ORDINAL["Potential Fit"]
    assert LABEL_TO_ORDINAL["Potential Fit"] < LABEL_TO_ORDINAL["Good Fit"]


# ---------------------------------------------------------------------------
# HFExample dataclass
# ---------------------------------------------------------------------------


class TestHFExample:
    def test_label_ordinal_property(self):
        ex = HFExample(
            resume_text="resume",
            job_description="jd",
            label="Good Fit",
            row_idx=42,
        )
        assert ex.label_ordinal == 3

    def test_unknown_label_ordinal_zero(self):
        ex = HFExample(
            resume_text="r",
            job_description="j",
            label="Unknown Class",
            row_idx=0,
        )
        assert ex.label_ordinal == 0


# ---------------------------------------------------------------------------
# example_to_job_dict
# ---------------------------------------------------------------------------


def test_example_to_job_dict_shape():
    ex = HFExample(
        resume_text="some resume",
        job_description="some job",
        label="Good Fit",
        row_idx=1,
    )
    job = example_to_job_dict(ex)
    # Must contain the keys score_pair() / format_job_for_scoring expects
    assert job["title"] == "Job Posting"
    assert job["company"] == "HF Dataset"
    assert job["description"] == "some job"
    assert "salary_min" in job
    assert "salary_max" in job
    assert "user_notes" in job
    assert "extra_metadata" in job


# ---------------------------------------------------------------------------
# fetch_examples — uses a stubbed _fetch_page so no network needed
# ---------------------------------------------------------------------------


def _make_fake_pages(rows: list[dict], page_size: int = 100):
    """Build a fake _fetch_page that returns paginated chunks of `rows`."""
    def fake_fetch_page(split="test", config="default", offset=0, length=page_size, cache_dir=None):
        return rows[offset : offset + length]
    return fake_fetch_page


def test_fetch_examples_basic(monkeypatch):
    """fetch_examples should parse rows correctly with no network."""
    fake_rows = [
        {
            "row_idx": i,
            "row": {
                "resume_text": f"resume {i}",
                "job_description_text": f"job {i}",
                "label": ("No Fit", "Potential Fit", "Good Fit")[i % 3],
            },
        }
        for i in range(30)
    ]
    monkeypatch.setattr(
        "tests.eval.external.huggingface_loader._fetch_page",
        _make_fake_pages(fake_rows),
    )
    examples = fetch_examples(n=9, stratified=True, seed=42, max_pool=30)
    assert len(examples) == 9
    counts = Counter(ex.label for ex in examples)
    # Stratified → roughly equal across classes
    assert counts["No Fit"] == 3
    assert counts["Potential Fit"] == 3
    assert counts["Good Fit"] == 3


def test_fetch_examples_skips_invalid_rows(monkeypatch):
    """Rows with missing/invalid fields should be silently skipped."""
    fake_rows = [
        {"row_idx": 0, "row": {"resume_text": "r0", "job_description_text": "j0", "label": "Good Fit"}},
        {"row_idx": 1, "row": {"resume_text": "", "job_description_text": "j1", "label": "Good Fit"}},  # empty resume
        {"row_idx": 2, "row": {"resume_text": "r2", "job_description_text": "", "label": "Good Fit"}},  # empty jd
        {"row_idx": 3, "row": {"resume_text": "r3", "job_description_text": "j3", "label": "Garbage"}},  # bad label
        {"row_idx": 4, "row": {"resume_text": "r4", "job_description_text": "j4", "label": "Good Fit"}},
    ]
    monkeypatch.setattr(
        "tests.eval.external.huggingface_loader._fetch_page",
        _make_fake_pages(fake_rows),
    )
    examples = fetch_examples(n=10, stratified=False, max_pool=10)
    # Only 2 valid examples (rows 0 and 4) — others were filtered
    assert len(examples) == 2
    assert {ex.row_idx for ex in examples} == {0, 4}


def test_fetch_examples_handles_empty_pool(monkeypatch):
    monkeypatch.setattr(
        "tests.eval.external.huggingface_loader._fetch_page",
        _make_fake_pages([]),
    )
    examples = fetch_examples(n=10, max_pool=10)
    assert examples == []


def test_fetch_examples_stratified_balances_uneven_input(monkeypatch):
    """Even when input is heavily skewed toward one class, stratified sampling
    should produce roughly balanced output (limited by the smaller buckets)."""
    fake_rows = (
        [{"row_idx": i, "row": {"resume_text": f"r{i}", "job_description_text": f"j{i}", "label": "No Fit"}} for i in range(50)]
        + [{"row_idx": i + 50, "row": {"resume_text": f"r{i}", "job_description_text": f"j{i}", "label": "Potential Fit"}} for i in range(5)]
        + [{"row_idx": i + 100, "row": {"resume_text": f"r{i}", "job_description_text": f"j{i}", "label": "Good Fit"}} for i in range(5)]
    )
    monkeypatch.setattr(
        "tests.eval.external.huggingface_loader._fetch_page",
        _make_fake_pages(fake_rows),
    )
    examples = fetch_examples(n=9, stratified=True, max_pool=60)
    counts = Counter(ex.label for ex in examples)
    # Each class should be capped by min(n // 3, available)
    assert counts["Potential Fit"] == 3
    assert counts["Good Fit"] == 3
    assert counts["No Fit"] == 3
