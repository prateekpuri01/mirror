"""Loader for the Hugging Face resume-job-description-fit dataset.

Dataset: https://huggingface.co/datasets/cnamuangtoun/resume-job-description-fit
Size: 8,000 rows (6,240 train / 1,760 test)
Fields: resume_text, job_description_text, label (No Fit / Potential Fit / Good Fit)
License: see HF dataset card

This is the source dataset behind 0xnbk/resume-ats-score-v1-en. It uses 3-class
labels (no numeric scores), which is fine for our tier-separation evaluation.

Labels source: not strictly human ground truth — likely weak supervision from
similarity matching plus light human review. Use as a sanity check for ranking
agreement, not as authoritative ground truth.

We use the public datasets-server JSON API to avoid adding the `datasets` package
to requirements. Pages are cached to disk to avoid re-downloading.
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

DATASETS_SERVER = "https://datasets-server.huggingface.co"
DATASET = "cnamuangtoun/resume-job-description-fit"
DEFAULT_CACHE = Path(__file__).parent / "cache" / "huggingface"
PAGE_SIZE = 100  # max allowed by datasets-server

# Map text labels to ordinal scores so we can compute rank correlation
LABEL_TO_ORDINAL = {
    "No Fit": 1,
    "Potential Fit": 2,
    "Good Fit": 3,
}


@dataclass
class HFExample:
    """One (resume, job_description, label) example."""

    resume_text: str
    job_description: str
    label: str  # "No Fit" / "Potential Fit" / "Good Fit"
    row_idx: int

    @property
    def label_ordinal(self) -> int:
        return LABEL_TO_ORDINAL.get(self.label, 0)


def _fetch_page(
    split: str = "test",
    config: str = "default",
    offset: int = 0,
    length: int = PAGE_SIZE,
    cache_dir: Path = DEFAULT_CACHE,
) -> list[dict]:
    """Fetch one page of rows from the datasets-server, with disk cache."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{split}_{config}_offset{offset}_length{length}.json"
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text())
        except json.JSONDecodeError:
            logger.warning("Cache miss (corrupt JSON) for %s", cache_file)

    url = (
        f"{DATASETS_SERVER}/rows"
        f"?dataset={DATASET}&config={config}&split={split}"
        f"&offset={offset}&length={length}"
    )
    logger.info("Fetching %s", url)
    with httpx.Client(timeout=60.0) as client:
        resp = client.get(url)
        resp.raise_for_status()
        data = resp.json()

    rows = data.get("rows", [])
    cache_file.write_text(json.dumps(rows, indent=2))
    return rows


def fetch_examples(
    n: int,
    split: str = "test",
    seed: int | None = 42,
    cache_dir: Path = DEFAULT_CACHE,
    stratified: bool = True,
    max_pool: int = 2000,
) -> list[HFExample]:
    """Fetch `n` examples from the dataset.

    The HF dataset is sorted by label (all No Fit first), so we have to fetch
    a deep pool of pages to see the minority classes. We fetch sequential pages
    up to `max_pool` rows, then optionally stratified-sample by label to give
    balanced coverage across No Fit / Potential Fit / Good Fit.
    """
    target_pool = max_pool if stratified else max(n * 2, 200)
    collected: list[dict] = []
    offset = 0
    while len(collected) < target_pool:
        page = _fetch_page(
            split=split, offset=offset, length=PAGE_SIZE, cache_dir=cache_dir
        )
        if not page:
            break
        collected.extend(page)
        offset += PAGE_SIZE

    if seed is not None:
        rng = random.Random(seed)
        rng.shuffle(collected)

    def _to_example(item: dict) -> HFExample | None:
        row = item.get("row", {})
        resume = (row.get("resume_text") or "").strip()
        jd = (row.get("job_description_text") or "").strip()
        label = (row.get("label") or "").strip()
        if not resume or not jd or label not in LABEL_TO_ORDINAL:
            return None
        return HFExample(
            resume_text=resume,
            job_description=jd,
            label=label,
            row_idx=item.get("row_idx", -1),
        )

    candidates = [ex for ex in (_to_example(it) for it in collected) if ex is not None]

    if stratified:
        # Stratified sample: equal-ish counts per label
        per_label = max(1, n // len(LABEL_TO_ORDINAL))
        bucketed: dict[str, list[HFExample]] = {label: [] for label in LABEL_TO_ORDINAL}
        for ex in candidates:
            bucketed[ex.label].append(ex)
        examples: list[HFExample] = []
        for label, bucket in bucketed.items():
            examples.extend(bucket[:per_label])
        # Top off if any bucket was short
        if len(examples) < n:
            seen_idx = {ex.row_idx for ex in examples}
            for ex in candidates:
                if ex.row_idx not in seen_idx:
                    examples.append(ex)
                    if len(examples) >= n:
                        break
        return examples[:n]
    else:
        return candidates[:n]


def example_to_job_dict(ex: HFExample) -> dict:
    """Convert an HFExample's job_description into the dict shape score_pair() expects."""
    return {
        "title": "Job Posting",  # not in dataset
        "company": "HF Dataset",
        "description": ex.job_description,
        "salary_min": None,
        "salary_max": None,
        "user_notes": None,
        "extra_metadata": None,
    }
