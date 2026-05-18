"""Deterministic quality checks on saved resume JSON files.

Runs against all JSON files in /output/resumes/ — no LLM calls, no DB needed.
Provides an instant baseline of current resume quality.

Usage:
    # Run inside Docker container:
    docker compose exec api python -m pytest tests/eval/test_resume_checks.py -v

    # With full report output:
    docker compose exec api python -m pytest tests/eval/test_resume_checks.py -v -s
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.eval.resume_checks import (
    ResumeCheckReport,
    check_banned_phrases,
    check_critic_leakage,
    check_participial_tails_fast,
    check_pronoun_violations,
    check_redundancy,
    check_schema_completeness,
    check_verb_repetition,
    check_word_counts,
    run_deterministic_checks,
)

# ---------------------------------------------------------------------------
# Load saved resumes
# ---------------------------------------------------------------------------

RESUME_DIR = Path("/app/output/resumes")
# Fallback for running outside Docker
if not RESUME_DIR.exists():
    RESUME_DIR = Path(__file__).resolve().parents[3] / "output" / "resumes"


def _load_saved_resumes() -> list[tuple[str, dict]]:
    """Load all JSON resume files from the output directory."""
    if not RESUME_DIR.exists():
        return []
    resumes = []
    for path in sorted(RESUME_DIR.glob("*.json")):
        try:
            with open(path) as f:
                data = json.load(f)
            # Skip files that don't look like resume JSON
            if "experience" not in data and "summary" not in data:
                continue
            resumes.append((path.stem[:50], data))
        except (json.JSONDecodeError, KeyError):
            continue
    return resumes


SAVED_RESUMES = _load_saved_resumes()

if not SAVED_RESUMES:
    pytest.skip("No saved resume JSON files found", allow_module_level=True)


# ---------------------------------------------------------------------------
# Parametrized tests — one per check category
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,resume", SAVED_RESUMES, ids=[r[0] for r in SAVED_RESUMES])
def test_no_participial_tails(name: str, resume: dict):
    results = check_participial_tails_fast(resume)
    if results:
        details = "\n".join(f"  [{r.location}] {r.offending_text}" for r in results)
        pytest.fail(f"{len(results)} participial tail(s) found:\n{details}")


@pytest.mark.parametrize("name,resume", SAVED_RESUMES, ids=[r[0] for r in SAVED_RESUMES])
def test_word_counts(name: str, resume: dict):
    results = check_word_counts(resume)
    errors = [r for r in results if r.severity == "error"]
    if errors:
        details = "\n".join(f"  [{r.location}] {r.message}" for r in errors)
        pytest.fail(f"{len(errors)} word count violation(s):\n{details}")


@pytest.mark.parametrize("name,resume", SAVED_RESUMES, ids=[r[0] for r in SAVED_RESUMES])
def test_no_pronoun_violations(name: str, resume: dict):
    results = check_pronoun_violations(resume)
    if results:
        details = "\n".join(f"  [{r.location}] {r.offending_text}" for r in results)
        pytest.fail(f"{len(results)} pronoun violation(s):\n{details}")


@pytest.mark.parametrize("name,resume", SAVED_RESUMES, ids=[r[0] for r in SAVED_RESUMES])
def test_no_banned_phrases(name: str, resume: dict):
    results = check_banned_phrases(resume)
    if results:
        details = "\n".join(f"  [{r.location}] '{r.offending_text}'" for r in results)
        pytest.fail(f"{len(results)} banned phrase(s):\n{details}")


@pytest.mark.parametrize("name,resume", SAVED_RESUMES, ids=[r[0] for r in SAVED_RESUMES])
def test_no_verb_repetition(name: str, resume: dict):
    results = check_verb_repetition(resume)
    if results:
        details = "\n".join(f"  {r.message}" for r in results)
        pytest.fail(f"Verb repetition:\n{details}")


@pytest.mark.parametrize("name,resume", SAVED_RESUMES, ids=[r[0] for r in SAVED_RESUMES])
def test_schema_completeness(name: str, resume: dict):
    results = check_schema_completeness(resume)
    errors = [r for r in results if r.severity == "error"]
    if errors:
        details = "\n".join(f"  [{r.location}] {r.message}" for r in errors)
        pytest.fail(f"{len(errors)} schema error(s):\n{details}")


@pytest.mark.parametrize("name,resume", SAVED_RESUMES, ids=[r[0] for r in SAVED_RESUMES])
def test_no_critic_leakage(name: str, resume: dict):
    results = check_critic_leakage(resume)
    if results:
        details = "\n".join(f"  [{r.location}] {r.offending_text}" for r in results)
        pytest.fail(f"{len(results)} critic leakage instance(s):\n{details}")


@pytest.mark.parametrize("name,resume", SAVED_RESUMES, ids=[r[0] for r in SAVED_RESUMES])
def test_low_redundancy(name: str, resume: dict):
    results = check_redundancy(resume)
    if results:
        details = "\n".join(f"  [{r.location}] {r.message}" for r in results)
        # Redundancy is a warning, not a hard failure
        pytest.warns(UserWarning, match="redundancy") if False else None
        # But still report it
        print(f"\n  REDUNDANCY WARNINGS for {name}:")
        print(details)


# ---------------------------------------------------------------------------
# Aggregate report (runs with -s flag)
# ---------------------------------------------------------------------------


def test_aggregate_report():
    """Print aggregate stats across all saved resumes."""
    from collections import Counter

    check_counts: Counter = Counter()
    total_errors = 0
    total_warnings = 0
    resumes_with_errors = 0

    for name, resume in SAVED_RESUMES:
        report = run_deterministic_checks(resume)
        total_errors += report.error_count
        total_warnings += report.warning_count
        if report.error_count > 0:
            resumes_with_errors += 1
        for r in report.results:
            check_counts[r.check_name] += 1

    total = len(SAVED_RESUMES)
    clean = total - resumes_with_errors
    print(f"\n{'='*60}")
    print(f"DETERMINISTIC CHECKS — {total} saved resumes")
    print(f"{'='*60}")
    print(f"  Clean resumes: {clean}/{total} ({100*clean/total:.0f}%)")
    print(f"  Total errors:   {total_errors}")
    print(f"  Total warnings: {total_warnings}")
    print(f"\n  By check type:")
    for check_name, count in check_counts.most_common():
        print(f"    {check_name:25s} {count:3d}")
    print(f"{'='*60}")
