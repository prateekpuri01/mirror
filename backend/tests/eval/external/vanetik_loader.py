"""Loader for the Vanetik vacancy-resume matching dataset.

Repo: https://github.com/NataliaVanetik/vacancy-resume-matching-dataset
License: GPL-3.0
Citation: Vanetik & Kogan (2023), "Job Vacancy Ranking with Sentence Embeddings,
Keywords, and Named Entities," Information 14(8):468.

Structure:
- 5_vacancies.csv (columns: id, job_description, job_title, uid)
- CV/1.docx ... CV/65.docx
- annotations-for-the-first-30-vacancies.txt (rankings of 5 vacancies per CV,
  for CVs 1-30, by two annotators)

The annotation file uses Python list literal format. Each row is a 5-element
list of vacancy positions (1-indexed) in best-to-worst order. The k-th element
is the position of the vacancy ranked k-th best for that CV.

Usage:
    files = ensure_dataset_local()  # downloads if missing
    bundle = load_bundle(files)     # parses everything
    # bundle.vacancies: list[dict] with 5 jobs in CSV order (positions 1-5)
    # bundle.cv_paths: dict[int, Path] for CVs 1-65
    # bundle.annotator_rankings: dict[int, dict[str, list[int]]]
    #   {cv_idx: {"annotator_1": [2,1,4,3,5], "annotator_2": [...]}}
"""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

REPO_URL = "https://raw.githubusercontent.com/NataliaVanetik/vacancy-resume-matching-dataset/main"
DEFAULT_CACHE = Path(__file__).parent / "cache" / "vanetik"


@dataclass
class VanetikBundle:
    """All loaded artifacts from the Vanetik dataset."""

    vacancies: list[dict] = field(default_factory=list)  # 5 dicts in CSV order
    cv_paths: dict[int, Path] = field(default_factory=dict)  # cv_idx -> .docx path
    annotator_rankings: dict[int, dict[str, list[int]]] = field(default_factory=dict)
    # {cv_idx: {"annotator_1": [vac_pos_1st, vac_pos_2nd, ...], "annotator_2": [...]}}


def _http_get(url: str) -> bytes:
    """Plain GET with reasonable timeout. Raises httpx errors on failure."""
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.content


def ensure_dataset_local(cache_dir: Path = DEFAULT_CACHE) -> Path:
    """Download the Vanetik dataset to a local cache. Idempotent.

    Returns the cache directory containing all needed files.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cv_dir = cache_dir / "CV"
    cv_dir.mkdir(exist_ok=True)

    # Files at repo root
    root_files = [
        "5_vacancies.csv",
        "annotations-for-the-first-30-vacancies.txt",
    ]
    for fname in root_files:
        target = cache_dir / fname
        if target.exists() and target.stat().st_size > 0:
            continue
        url = f"{REPO_URL}/{fname}"
        logger.info("Downloading %s", url)
        target.write_bytes(_http_get(url))

    # CV files (1.docx ... 65.docx). Only download if missing.
    for i in range(1, 66):
        target = cv_dir / f"{i}.docx"
        if target.exists() and target.stat().st_size > 0:
            continue
        url = f"{REPO_URL}/CV/{i}.docx"
        logger.info("Downloading %s", url)
        try:
            target.write_bytes(_http_get(url))
        except httpx.HTTPStatusError as e:
            logger.warning("Failed to download CV/%d.docx: %s", i, e)

    return cache_dir


def _load_vacancies(csv_path: Path) -> list[dict]:
    """Parse 5_vacancies.csv into a list of job dicts in CSV order.

    The dataset has no `company` column. We synthesize a placeholder so the
    scoring prompt has somewhere to put it.
    """
    rows: list[dict] = []
    with csv_path.open("r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(
                {
                    "title": row.get("job_title", "").strip(),
                    "company": "Vanetik Vacancy",  # not in dataset
                    "description": row.get("job_description", "").strip(),
                    "salary_min": None,
                    "salary_max": None,
                    "user_notes": None,
                    "extra_metadata": None,
                    "_dataset_id": row.get("id"),
                    "_uid": row.get("uid"),
                }
            )
    if len(rows) != 5:
        logger.warning("Expected 5 vacancies, got %d", len(rows))
    return rows


def _parse_annotation_file(txt_path: Path) -> dict[int, dict[str, list[int]]]:
    """Parse annotations-for-the-first-30-vacancies.txt.

    File format: two assignments `ANNOTATOR_1_RANKINGS=[[...],[...],...]` and
    `ANNOTATOR_2_RANKINGS=[[...],[...],...]`, each containing 30 inner lists of
    5 vacancy positions in best-to-worst order.

    Note: the dataset contains a few invalid permutations (e.g. CV 9 annotator 1
    is `[1,5,2,1,4]` — missing 3, has 1 twice). We log a warning and exclude
    those CVs from the parsed output so downstream metrics aren't polluted.

    Returns: {cv_idx (1-based): {"annotator_1": [...], "annotator_2": [...]}}
    """
    text = txt_path.read_text(encoding="utf-8", errors="replace")
    text_no_comments = re.sub(r"#[^\n]*", "", text)  # strip "#1-5" inline comments

    # Find each annotator block by its explicit header. Capture everything from
    # the assignment up to the first closing `]]` (end of the outer list).
    blocks: dict[str, str] = {}
    for header_match in re.finditer(
        r"ANNOTATOR_([12])_RANKINGS\s*=\s*(\[.*?\]\s*\])",
        text_no_comments,
        flags=re.DOTALL,
    ):
        idx = header_match.group(1)
        body = header_match.group(2)
        blocks[f"annotator_{idx}"] = body

    inner_re = re.compile(r"\[\s*\d+(?:\s*,\s*\d+){4}\s*\]")

    parsed: dict[str, list[list[int]]] = {}
    for label, body in blocks.items():
        rows = inner_re.findall(body)
        parsed[label] = [[int(x.strip()) for x in s.strip("[] \t").split(",")] for s in rows]
        if len(parsed[label]) != 30:
            logger.warning("%s has %d rankings (expected 30)", label, len(parsed[label]))

    rankings: dict[int, dict[str, list[int]]] = {}
    for label, rows in parsed.items():
        for i, row in enumerate(rows[:30]):
            cv_idx = i + 1
            # Validate: must be a permutation of {1,2,3,4,5}
            if sorted(row) != [1, 2, 3, 4, 5]:
                logger.warning("Skipping CV %d %s: invalid permutation %s", cv_idx, label, row)
                continue
            rankings.setdefault(cv_idx, {})[label] = row
    return rankings


def load_bundle(cache_dir: Path = DEFAULT_CACHE) -> VanetikBundle:
    """Load the full Vanetik dataset from a local cache directory."""
    cache_dir = Path(cache_dir)
    bundle = VanetikBundle()

    csv_path = cache_dir / "5_vacancies.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"Missing {csv_path}. Call ensure_dataset_local() first.")
    bundle.vacancies = _load_vacancies(csv_path)

    cv_dir = cache_dir / "CV"
    for i in range(1, 66):
        p = cv_dir / f"{i}.docx"
        if p.exists() and p.stat().st_size > 0:
            bundle.cv_paths[i] = p

    ann_path = cache_dir / "annotations-for-the-first-30-vacancies.txt"
    if ann_path.exists():
        bundle.annotator_rankings = _parse_annotation_file(ann_path)

    logger.info(
        "Loaded Vanetik bundle: %d vacancies, %d CVs, %d annotated CVs",
        len(bundle.vacancies),
        len(bundle.cv_paths),
        len(bundle.annotator_rankings),
    )
    return bundle


def annotator_ranking_to_relevance(ranking: list[int], num_vacancies: int = 5) -> dict[int, int]:
    """Convert annotator ranking [2,1,4,3,5] (vacancy positions, best to worst)
    into a relevance grade dict {vacancy_position: grade}.

    Best vacancy gets grade num_vacancies, worst gets 1. This is the form
    nDCG@k expects.
    """
    grades: dict[int, int] = {}
    for rank_idx, vac_pos in enumerate(ranking):
        grades[vac_pos] = num_vacancies - rank_idx
    return grades
