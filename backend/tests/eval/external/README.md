# External Evaluation of the Fit-Score Algorithm

This directory holds the evaluation harness that grounds the scoring algorithm
(`backend/app/ai/scoring.py`) against open-source datasets. The goal is to
answer "is the algorithm producing realistic results?" — not just "does it
catch regressions?" (the existing 16-case synthetic eval in `tests/eval/`
already covers regression).

## TL;DR — How to run

```bash
# Tier 1 — human ground truth (small, slow, the gold standard)
docker compose exec api python -m scripts.eval.eval_vanetik --cvs 5
docker compose exec api python -m scripts.eval.eval_vanetik --cvs 30 --concurrency 8

# Tier 2 — large-scale algorithm comparison
docker compose exec api python -m scripts.eval.eval_huggingface --n 30
docker compose exec api python -m scripts.eval.eval_huggingface --n 200 --concurrency 8
```

Reports are written to `tests/eval/external/results/<tier>_<timestamp>.json`.

## Why these datasets and not others

The literature review (see `/Users/ppuri/.claude/plans/hazy-snacking-eich.md`)
surveyed every public resume↔job matching dataset I could find. Most are either
not publicly available (ConFit's AliYun and Intellipro datasets, RecSys 2016/17
XING dataset), behind paywalls, or unlabeled. The two below are the only
freely-downloadable ones with usable structure.

### Tier 1 — Vanetik vacancy-resume matching dataset

- **Source:** https://github.com/NataliaVanetik/vacancy-resume-matching-dataset
- **Citation:** Vanetik & Kogan (2023), *"Job Vacancy Ranking with Sentence
  Embeddings, Keywords, and Named Entities,"* Information 14(8):468.
- **Size:** 5 vacancies × 30 ranked CVs (out of 65 total CVs)
- **Ground truth:** Two human annotators ranked the 5 vacancies for each CV,
  using HR-specialist instructions (`annotation_instructions.docx`).
- **License:** GPL-3.0
- **Why this matters:** The only freely-downloadable, professionally-annotated
  resume↔job ranking benchmark I found. Real human ground truth is the gold
  standard, even at small scale.

The loader downloads the repo to `cache/vanetik/` on first run. Idempotent.

**Per-CV evaluation procedure:**
1. Extract a profile from the .docx via the live LLM (`extract_profile_from_resume`)
2. Score the profile against all 5 vacancies via `score_pair()`
3. Rank the 5 vacancies by our composite score
4. Compare to each annotator's ranking using:
   - **Spearman ρ** (full rank correlation)
   - **Kendall τ** (rank correlation, robust to ties)
   - **nDCG@5** (ranking quality with relevance grades)
   - **Top-1 hit** (did our top-1 vacancy match the annotator's top-1?)
   - **Top-3 precision** (overlap between our top-3 and annotator's top-3)

**Pass criteria** (calibrated to NAACL 2025 findings — see below):
- Mean Spearman ρ > **0.4** = "realistic"
- Mean nDCG@5 > **0.6** = "ranking quality is meaningful"
- Mean top-3 precision > **0.6** = "right candidates float to the top"

**Cost:** Full 30-CV run = 30 extractions + 150 score calls × 2 LLM calls each
≈ 330 LLM calls per run. At ~$0.01/call this is ~$3 per full run on gpt-5.4.

### Tier 2 — Hugging Face resume-job-description-fit (cnamuangtoun)

- **Source:** https://huggingface.co/datasets/cnamuangtoun/resume-job-description-fit
- **Size:** 8,000 rows (6,240 train / 1,760 test)
- **Fields:** `resume_text`, `job_description_text`, `label` ∈ {No Fit, Potential
  Fit, Good Fit}
- **Ground truth:** Not strictly human-labeled — likely weak supervision plus
  light review. Use as a sanity check, not ground truth.
- **License:** see HF dataset card

The loader uses the public `datasets-server` JSON API (no `datasets` package
dependency). Pages are cached to `cache/huggingface/`.

**Important:** The dataset is sorted by label (all No Fit first), so the loader
fetches a deep pool (~20 pages = 2,000 rows) and stratified-samples to give
balanced No Fit / Potential Fit / Good Fit coverage by default.

**Per-example evaluation procedure:**
1. Extract a profile from `resume_text`
2. Score against the `job_description_text`
3. Bin our composite (0-100) into No Fit / Potential Fit / Good Fit using
   thresholds (40, 70)
4. Compare predicted bin to actual label

**Pass criteria:**
- 3-class accuracy > **0.50** (chance is 0.33)
- Spearman ρ vs label ordinal > **0.30**
- No tier collapse: each predicted bucket has > 0 examples

## Methodological notes (from the literature)

### Don't expect high LLM↔human correlation

The NAACL 2025 paper *"Human and LLM-Based Resume Matching"*
([aclanthology.org/2025.findings-naacl.270](https://aclanthology.org/2025.findings-naacl.270/))
tested GPT-4 (with and without CoT), Claude, and Gemini against 736 real
resumes with strong inter-rater agreement (Fleiss' κ = 0.7859). The key
finding: **LLM scores correlate only "minorly" with human ratings even with
chain-of-thought prompting.**

Practical implication: a Spearman ρ of 0.4-0.6 against humans is *good*, not
failure. We are not looking for r > 0.9 — that bar is unrealistic for any
LLM-based scorer in the current state of the art.

### Use rank correlation, not RMSE

Absolute fit scores are arbitrary across systems. What matters is **ordering**:
if a recruiter ranks job A > job B > job C, our algorithm should reproduce that
ordering. Spearman / Kendall / nDCG are the right metrics.

### Pair human ground truth with algorithm comparison

Human ground truth (Tier 1) is the gold standard but small. Algorithm-comparison
data (Tier 2) is large but reflects another algorithm's bias. Use both to
triangulate. If both tiers fail, that's strong evidence of a problem. If only
one fails, look at *which* and *how*.

## File layout

```
backend/tests/eval/external/
├── __init__.py
├── README.md                  ← this file
├── scoring_runner.py          ← in-memory score_pair() + concurrent runner
├── resume_to_profile.py       ← .docx / text → UserProfile dict via LLM
├── metrics_external.py        ← pure-Python Spearman/nDCG/precision/etc.
├── vanetik_loader.py          ← Vanetik dataset download + parser
├── huggingface_loader.py      ← HF datasets-server API loader
├── cache/
│   ├── vanetik/               ← downloaded vacancies + 65 CVs (lazy)
│   └── huggingface/           ← cached page JSON files (lazy)
└── results/
    ├── vanetik_<timestamp>.json
    └── huggingface_<timestamp>.json

backend/scripts/eval/
├── eval_vanetik.py            ← Tier 1 runner (CLI)
└── eval_huggingface.py        ← Tier 2 runner (CLI)
```

## Implementation notes

- Both eval scripts run inside the API container so they pick up the same
  OpenAI API key, `RESUME_MODEL`, and corporate proxy / cert chain that the
  rest of the app uses.
- Concurrency defaults to 4 (matches the `_CONCURRENCY = 10` semaphore in
  `scoring.py` but conservatively scaled). Bump with `--concurrency 8` for
  faster runs.
- Reports are JSON-serializable with full per-CV / per-example detail so you
  can inspect outliers afterwards (`jq` works well on these files).
- The scoring runner reuses the existing prompt builders from
  `app.ai.prompts` and `app.ai.scoring._build_compact_profile` so the eval
  always tracks whatever the live algorithm is doing — no risk of drift.

## Adding a new dataset

If you find another usable dataset later (the NAACL 2025 paper's 736-resume
dataset, for example), the pattern is:

1. Add a `*_loader.py` that exposes a function returning a list of examples
   with at minimum: a resume text/path, a job dict, and ground-truth labels.
2. Wire it into a new `scripts/eval/eval_<name>.py` script that mirrors the
   existing two — extract profiles, score pairs, compute metrics, write a
   report.
3. Document the dataset's licensing, size, and label provenance in this README
   so future readers know what kind of ground truth they're comparing against.
