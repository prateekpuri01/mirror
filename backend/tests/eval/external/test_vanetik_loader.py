"""Unit tests for vanetik_loader.py — pure parsing logic, no network.

Tests use temp files written from inline string fixtures so they don't depend
on the cached dataset existing on disk.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.eval.external.vanetik_loader import (
    _load_vacancies,
    _parse_annotation_file,
    annotator_ranking_to_relevance,
)

# ---------------------------------------------------------------------------
# CSV vacancy parser
# ---------------------------------------------------------------------------


def test_load_vacancies_basic(tmp_path: Path):
    """Parse a minimal CSV mirroring the real Vanetik format."""
    csv_path = tmp_path / "vacancies.csv"
    csv_path.write_text(
        "id,job_description,job_title,uid\n"
        '8,"Build .NET apps for insurance.",.Net Developer,abc123\n'
        '37,"Backend Python role for fintech.",Backend Engineer,def456\n'
    )
    rows = _load_vacancies(csv_path)
    assert len(rows) == 2
    assert rows[0]["title"] == ".Net Developer"
    assert rows[0]["description"] == "Build .NET apps for insurance."
    assert rows[0]["company"] == "Vanetik Vacancy"
    assert rows[0]["salary_min"] is None
    assert rows[0]["_dataset_id"] == "8"
    assert rows[1]["title"] == "Backend Engineer"


def test_load_vacancies_handles_missing_columns(tmp_path: Path):
    csv_path = tmp_path / "v.csv"
    csv_path.write_text("id,job_description,job_title,uid\n1,,,xyz\n")
    rows = _load_vacancies(csv_path)
    assert len(rows) == 1
    assert rows[0]["title"] == ""
    assert rows[0]["description"] == ""


# ---------------------------------------------------------------------------
# Annotation file parser — the trickiest part of the loader
# ---------------------------------------------------------------------------


VALID_ANNOTATIONS = """\
Header text describing the file.

ANNOTATOR_1_RANKINGS=[[2,1,4,3,5],[1,2,3,4,5],[3,1,2,4,5], #1-3
       [1,5,4,2,3],[3,2,1,4,5]] #4-5

ANNOTATOR_2_RANKINGS= [[4,3,1,5,2],[2,4,3,1,5],[5,4,2,3,1], #1-3
        [1,3,2,4,5],[5,1,2,4,3]] #4-5
"""


def test_parse_annotations_basic(tmp_path: Path):
    f = tmp_path / "ann.txt"
    f.write_text(VALID_ANNOTATIONS)
    result = _parse_annotation_file(f)
    assert len(result) == 5
    assert result[1]["annotator_1"] == [2, 1, 4, 3, 5]
    assert result[1]["annotator_2"] == [4, 3, 1, 5, 2]
    assert result[5]["annotator_1"] == [3, 2, 1, 4, 5]
    assert result[5]["annotator_2"] == [5, 1, 2, 4, 3]


def test_parse_annotations_skips_invalid_permutations(tmp_path: Path):
    """The real Vanetik dataset has 2 invalid rows (CV 9 ann1, CV 28 ann1).
    The parser must drop them silently rather than crashing or returning bad data.
    """
    bad = """\
ANNOTATOR_1_RANKINGS=[[1,2,3,4,5],[1,5,2,1,4],[3,2,1,4,5]]
ANNOTATOR_2_RANKINGS=[[1,2,3,4,5],[2,1,3,4,5],[3,2,1,4,5]]
"""
    f = tmp_path / "bad.txt"
    f.write_text(bad)
    result = _parse_annotation_file(f)
    # CV 1 and 3 should be present for both annotators
    assert "annotator_1" in result[1]
    assert "annotator_1" in result[3]
    # CV 2 should ONLY have annotator_2 (annotator_1 row was invalid)
    assert "annotator_1" not in result[2]
    assert result[2]["annotator_2"] == [2, 1, 3, 4, 5]


def test_parse_annotations_doesnt_confuse_annotator_1_with_2():
    """Regression test for an early parser bug: searching for 'annotator' + '2'
    matched ANNOTATOR_1_RANKINGS=[[2,...]] because it contained the digit 2.
    The fix uses regex on the explicit headers."""
    text = """\
ANNOTATOR_1_RANKINGS=[[2,1,4,3,5]]
ANNOTATOR_2_RANKINGS=[[5,4,3,2,1]]
"""
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(text)
        path = Path(f.name)
    try:
        result = _parse_annotation_file(path)
        assert result[1]["annotator_1"] == [2, 1, 4, 3, 5]
        assert result[1]["annotator_2"] == [5, 4, 3, 2, 1]
    finally:
        path.unlink()


def test_parse_annotations_strips_inline_comments(tmp_path: Path):
    """The dataset uses '#1-5' style comments inside the lists. They must be
    stripped before regex matching, otherwise an off-by-one in line numbers
    breaks the splits."""
    text = """\
ANNOTATOR_1_RANKINGS=[[1,2,3,4,5], #1
[2,1,3,4,5]] #2
ANNOTATOR_2_RANKINGS=[[5,4,3,2,1], #1
[4,5,3,2,1]] #2
"""
    f = tmp_path / "c.txt"
    f.write_text(text)
    result = _parse_annotation_file(f)
    assert len(result) == 2
    assert result[1]["annotator_1"] == [1, 2, 3, 4, 5]
    assert result[2]["annotator_1"] == [2, 1, 3, 4, 5]


# ---------------------------------------------------------------------------
# Annotator ranking → relevance grades
# ---------------------------------------------------------------------------


class TestRankingToRelevance:
    def test_best_first(self):
        # Vacancy 2 is rated best, vacancy 5 is worst
        grades = annotator_ranking_to_relevance([2, 1, 4, 3, 5], num_vacancies=5)
        # Best gets grade 5, worst gets grade 1
        assert grades[2] == 5  # 1st place
        assert grades[1] == 4  # 2nd place
        assert grades[4] == 3
        assert grades[3] == 2
        assert grades[5] == 1  # last place

    def test_grades_sum_correctly(self):
        # Sanity: grades should be {1, 2, 3, 4, 5}
        grades = annotator_ranking_to_relevance([3, 1, 4, 2, 5], num_vacancies=5)
        assert sorted(grades.values()) == [1, 2, 3, 4, 5]

    def test_three_items(self):
        grades = annotator_ranking_to_relevance([2, 1, 3], num_vacancies=3)
        assert grades[2] == 3
        assert grades[1] == 2
        assert grades[3] == 1
