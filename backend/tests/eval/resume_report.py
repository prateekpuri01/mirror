"""Human-readable report generator for resume evaluation.

Produces formatted text reports and structured JSON for cross-version
comparison of resume generation quality.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from tests.eval.resume_checks import CheckResult, ResumeCheckReport
from tests.eval.resume_metrics import JudgeResult


# ---------------------------------------------------------------------------
# Versioned report dataclass (JSON-serialisable)
# ---------------------------------------------------------------------------

@dataclass
class VersionedReport:
    prompt_version: str
    timestamp: str
    deterministic_pass_rate: float
    check_counts: dict[str, int]
    judge_means: dict[str, float]
    error_count: int
    warning_count: int


# ---------------------------------------------------------------------------
# JSON persistence
# ---------------------------------------------------------------------------


def save_report_json(report: VersionedReport, path: Path) -> None:
    """Save a VersionedReport as indented JSON for cross-version comparison."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(report), indent=2) + "\n")


# ---------------------------------------------------------------------------
# Text report
# ---------------------------------------------------------------------------

_JUDGE_COLUMNS = ["Natural", "Coherent", "Tailored", "CritIncorp"]


def generate_resume_report(
    deterministic_results: dict[str, ResumeCheckReport],
    judge_results: dict[str, dict[str, JudgeResult]] | None = None,
    prompt_version: str = "unknown",
) -> str:
    """Generate a human-readable evaluation report.

    Parameters
    ----------
    deterministic_results:
        Mapping of resume_name -> ResumeCheckReport from deterministic checks.
    judge_results:
        Optional mapping of resume_name -> {metric_name -> JudgeResult} from
        LLM judge scoring.  ``None`` means no judge pass was run.
    prompt_version:
        Version string of the resume prompts being evaluated.
    """
    lines: list[str] = []
    today = date.today().isoformat()
    n_resumes = len(deterministic_results)

    # --- Header -----------------------------------------------------------
    lines.append(f"RESUME EVAL REPORT \u2014 {today} \u2014 resume_prompts {prompt_version}")
    lines.append("\u2550" * 60)
    lines.append("")

    # --- Deterministic section --------------------------------------------
    total_errors = sum(r.error_count for r in deterministic_results.values())
    total_warnings = sum(r.warning_count for r in deterministic_results.values())
    clean = sum(
        1 for r in deterministic_results.values() if r.error_count == 0
    )
    clean_pct = (clean / n_resumes * 100) if n_resumes else 0.0

    lines.append(f"DETERMINISTIC CHECKS ({n_resumes} resumes)")
    lines.append(f"  Clean resumes:       {clean}/{n_resumes} ({clean_pct:.0f}%)")
    lines.append(f"  Total errors:        {total_errors}")
    lines.append(f"  Total warnings:      {total_warnings}")
    lines.append("")

    # By check type
    check_counts: Counter[str] = Counter()
    for report in deterministic_results.values():
        for cr in report.results:
            check_counts[cr.check_name] += 1

    if check_counts:
        lines.append("  By check type:")
        for check_name, count in check_counts.most_common():
            lines.append(f"    {check_name:24}{count}")
        lines.append("")

    # Worst offenders (sorted by errors desc, then warnings desc)
    offenders = sorted(
        deterministic_results.items(),
        key=lambda kv: (kv[1].error_count, kv[1].warning_count),
        reverse=True,
    )
    offenders = [(name, rpt) for name, rpt in offenders if rpt.error_count + rpt.warning_count > 0]

    if offenders:
        lines.append("  Worst offenders:")
        for name, rpt in offenders:
            lines.append(
                f"    {name}:  {rpt.error_count} errors, {rpt.warning_count} warnings"
            )
        lines.append("")

    # --- LLM Judge section ------------------------------------------------
    if judge_results:
        lines.append("LLM JUDGE SCORES (1-5 scale)")

        # Discover all metric names across all resumes, preserving order
        all_metrics: list[str] = []
        seen: set[str] = set()
        for per_resume in judge_results.values():
            for metric in per_resume:
                if metric not in seen:
                    all_metrics.append(metric)
                    seen.add(metric)

        col_labels = all_metrics if all_metrics else _JUDGE_COLUMNS
        col_width = 10
        name_width = 20

        header = f"  {'Resume':<{name_width}}" + "".join(
            f"{c:>{col_width}}" for c in col_labels
        )
        lines.append(header)

        # Per-resume rows
        metric_accumulators: dict[str, list[int]] = {m: [] for m in col_labels}

        for resume_name in sorted(judge_results):
            per_resume = judge_results[resume_name]
            truncated = (resume_name[:name_width - 2] + "..") if len(resume_name) > name_width else resume_name
            row = f"  {truncated:<{name_width}}"
            for metric in col_labels:
                jr = per_resume.get(metric)
                if jr is not None:
                    row += f"{jr.score:>{col_width}}"
                    metric_accumulators[metric].append(jr.score)
                else:
                    row += f"{'—':>{col_width}}"
            lines.append(row)

        # Mean row
        mean_row = f"  {'Mean':<{name_width}}"
        judge_means: dict[str, float] = {}
        for metric in col_labels:
            scores = metric_accumulators[metric]
            if scores:
                mean_val = statistics.mean(scores)
                judge_means[metric] = mean_val
                mean_row += f"{mean_val:>{col_width}.1f}"
            else:
                mean_row += f"{'—':>{col_width}}"
        lines.append(mean_row)
        lines.append("")

    # --- Footer -----------------------------------------------------------
    lines.append("\u2550" * 60)
    return "\n".join(lines)
