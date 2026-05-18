"""Full resume generation benchmark.

Generates resumes for all benchmark test cases via the real LLM pipeline,
then runs deterministic + LLM checks + LLM judges. Produces a report.

Usage:
    docker compose exec api python -m pytest tests/eval/test_resume_eval.py -m eval -v -s

Requires @pytest.mark.eval — skipped by default (makes real LLM calls).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from itertools import combinations

import pytest

from tests.eval.resume_checks import run_deterministic_checks, ResumeCheckReport
from tests.eval.resume_metrics import (
    run_llm_checks,
    run_all_judges,
    JudgeResult,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


@dataclass
class ResumeEvalResult:
    case_id: str
    strategy: str = ""
    first_draft: dict = field(default_factory=dict)
    critique: dict = field(default_factory=dict)
    final_resume: dict = field(default_factory=dict)
    generation_time_s: float = 0.0


# ---------------------------------------------------------------------------
# Resume generation (calls LLM pipeline directly, no DB)
# ---------------------------------------------------------------------------


async def _generate_one_resume(
    case_id: str,
    job_dict: dict,
    profile_data: dict,
) -> ResumeEvalResult:
    """Generate a resume for one test case using the real pipeline steps."""
    from types import SimpleNamespace

    from app.ai.client import get_openai_client, RESUME_MODEL
    from app.ai.prompts import format_job_for_scoring
    from app.ai.resume_prompts import (
        RESUME_CRITIC_SYSTEM,
        build_resume_system,
        build_resume_prompt,
        build_critic_prompt,
        build_refiner_system,
        build_refiner_prompt,
        build_full_profile_for_resume,
    )
    from app.ai.resume_builder import normalize_experience_order

    client = get_openai_client()

    async def call_llm(system: str, messages: list[dict], temp: float = 0.3, max_tokens: int = 4000) -> dict:
        oai_messages = [{"role": "system", "content": system}]
        for msg in messages:
            oai_messages.append({"role": msg["role"], "content": msg["content"]})
        response = await client.chat.completions.create(
            model=RESUME_MODEL,
            messages=oai_messages,
            max_completion_tokens=max_tokens,
            temperature=temp,
        )
        text = response.choices[0].message.content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        return json.loads(text)

    async def call_llm_text(system: str, user_content: str, temp: float = 0.4) -> str:
        response = await client.chat.completions.create(
            model=RESUME_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            max_completion_tokens=500,
            temperature=temp,
        )
        return response.choices[0].message.content.strip()

    # Build context
    fake_job = SimpleNamespace(
        title=job_dict["title"],
        company=job_dict["company"],
        description=job_dict.get("description", ""),
        salary_min=job_dict.get("salary_min"),
        salary_max=job_dict.get("salary_max"),
        display_title=job_dict["title"],
        display_company=job_dict["company"],
        extra_metadata=None,
        user_notes=job_dict.get("user_notes"),
    )
    profile_text = build_full_profile_for_resume(profile_data)
    job_text = format_job_for_scoring(fake_job)

    start = time.monotonic()

    # Phase 0.5: Strategy
    strategy = await call_llm_text(
        "You are a resume strategist planning a tailored resume. Output ONLY the 5 numbered "
        "planning lines. Do NOT write any resume content.",
        f"""Given this job and candidate, answer in 5 numbered lines (max 200 words total).
1. STRONGEST ARGUMENT: Why should this person get an interview?
2. ANCHOR ACCOMPLISHMENTS: Which 3 accomplishment IDs should anchor the resume?
3. SUMMARY ANGLE: What framing should the summary use?
4. TONE: What voice?
5. REDUNDANCY TRAPS: What content overlaps to watch?

Job title: {fake_job.title} at {fake_job.company}
Job description: {fake_job.description[:1000]}

Candidate: {profile_data.get('experience_years', 'N/A')} years post-PhD.
Recent roles: {', '.join(wh.get('title', '') + ' at ' + wh.get('employer', '') for wh in profile_data.get('work_history', [])[:3])}""",
        temp=0.4,
    )
    logger.info("Strategy for %s: %s", case_id, strategy[:150])

    # Phase 1: Generate first draft
    resume_system = build_resume_system(profile_data)
    messages = build_resume_prompt(
        profile_text, job_text, None, None,
        profile_data=profile_data,
        strategy=strategy,
    )
    first_draft = await call_llm(resume_system, messages, temp=0.5)
    first_draft = normalize_experience_order(first_draft, profile_data)

    # Phase 2: Critique
    critic_messages = build_critic_prompt(
        resume_json=json.dumps(first_draft, indent=2),
        job_text=job_text,
        profile_text=profile_text,
    )
    critique = await call_llm(RESUME_CRITIC_SYSTEM, critic_messages, temp=0.3)
    logger.info(
        "Critique for %s: %s, %d weaknesses",
        case_id, critique.get("overall_competitiveness", "?"),
        len(critique.get("weaknesses", [])),
    )

    # Phase 3: Refine
    refiner_system = build_refiner_system(profile_data)
    refiner_messages = build_refiner_prompt(
        resume_json=json.dumps(first_draft, indent=2),
        critique_json=json.dumps(critique, indent=2),
        profile_text=profile_text,
        job_text=job_text,
    )
    final_resume = await call_llm(refiner_system, refiner_messages, temp=0.4)
    final_resume = normalize_experience_order(final_resume, profile_data)
    final_resume["_critique"] = critique

    elapsed = time.monotonic() - start
    logger.info("Generated resume for %s in %.1fs", case_id, elapsed)

    return ResumeEvalResult(
        case_id=case_id,
        strategy=strategy,
        first_draft=first_draft,
        critique=critique,
        final_resume=final_resume,
        generation_time_s=elapsed,
    )


# ---------------------------------------------------------------------------
# Session-scoped fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def resume_profile() -> dict:
    """Return the eval profile data with complete_profile."""
    from tests.eval.resume_fixtures import RESUME_EVAL_PROFILE
    return RESUME_EVAL_PROFILE


@pytest.fixture(scope="session")
def all_resumes(resume_profile) -> dict[str, ResumeEvalResult]:
    """Generate resumes for all benchmark test cases (session-scoped).

    Makes real LLM calls. Results cached for all test functions.
    """
    from tests.eval.resume_fixtures import RESUME_TEST_CASES

    async def _run():
        sem = asyncio.Semaphore(2)  # 2 concurrent resume generations

        async def bounded(tc):
            async with sem:
                return await _generate_one_resume(tc.case_id, tc.job, resume_profile)

        gathered = await asyncio.gather(
            *[bounded(tc) for tc in RESUME_TEST_CASES],
            return_exceptions=True,
        )
        results = {}
        for item in gathered:
            if isinstance(item, Exception):
                logger.error("Resume generation failed: %s", item)
            else:
                results[item.case_id] = item
        return results

    return asyncio.get_event_loop().run_until_complete(_run())


@pytest.fixture
def test_cases():
    from tests.eval.resume_fixtures import RESUME_TEST_CASES_BY_ID
    return RESUME_TEST_CASES_BY_ID


# ---------------------------------------------------------------------------
# Deterministic tests (parametrized)
# ---------------------------------------------------------------------------


def _case_ids():
    from tests.eval.resume_fixtures import RESUME_TEST_CASES
    return [tc.case_id for tc in RESUME_TEST_CASES]


@pytest.mark.eval
@pytest.mark.parametrize("case_id", _case_ids())
def test_no_participial_tails(all_resumes, case_id):
    if case_id not in all_resumes:
        pytest.skip(f"Resume not generated for {case_id}")
    from tests.eval.resume_checks import check_participial_tails_fast
    results = check_participial_tails_fast(all_resumes[case_id].final_resume)
    assert len(results) == 0, f"{len(results)} participial tails:\n" + \
        "\n".join(f"  [{r.location}] {r.offending_text}" for r in results)


@pytest.mark.eval
@pytest.mark.parametrize("case_id", _case_ids())
def test_word_counts(all_resumes, case_id):
    if case_id not in all_resumes:
        pytest.skip(f"Resume not generated for {case_id}")
    from tests.eval.resume_checks import check_word_counts
    results = check_word_counts(all_resumes[case_id].final_resume)
    errors = [r for r in results if r.severity == "error"]
    assert len(errors) == 0, f"{len(errors)} word count violations:\n" + \
        "\n".join(f"  [{r.location}] {r.message}" for r in errors)


@pytest.mark.eval
@pytest.mark.parametrize("case_id", _case_ids())
def test_no_critic_leakage(all_resumes, case_id):
    if case_id not in all_resumes:
        pytest.skip(f"Resume not generated for {case_id}")
    from tests.eval.resume_checks import check_critic_leakage
    results = check_critic_leakage(all_resumes[case_id].final_resume)
    assert len(results) == 0, f"{len(results)} critic leakage:\n" + \
        "\n".join(f"  [{r.location}] {r.offending_text}" for r in results)


@pytest.mark.eval
@pytest.mark.parametrize("case_id", _case_ids())
def test_no_pronoun_violations(all_resumes, case_id):
    if case_id not in all_resumes:
        pytest.skip(f"Resume not generated for {case_id}")
    from tests.eval.resume_checks import check_pronoun_violations
    results = check_pronoun_violations(all_resumes[case_id].final_resume)
    assert len(results) == 0, f"{len(results)} pronoun violations:\n" + \
        "\n".join(f"  [{r.location}] {r.offending_text}" for r in results)


@pytest.mark.eval
@pytest.mark.parametrize("case_id", _case_ids())
def test_schema_complete(all_resumes, case_id):
    if case_id not in all_resumes:
        pytest.skip(f"Resume not generated for {case_id}")
    from tests.eval.resume_checks import check_schema_completeness
    results = check_schema_completeness(all_resumes[case_id].final_resume)
    errors = [r for r in results if r.severity == "error"]
    assert len(errors) == 0, f"{len(errors)} schema errors:\n" + \
        "\n".join(f"  [{r.location}] {r.message}" for r in errors)


@pytest.mark.eval
@pytest.mark.parametrize("case_id", _case_ids())
def test_no_fabrication(all_resumes, resume_profile, case_id):
    if case_id not in all_resumes:
        pytest.skip(f"Resume not generated for {case_id}")
    from tests.eval.resume_checks import check_fabricated_ids
    results = check_fabricated_ids(all_resumes[case_id].final_resume, resume_profile)
    assert len(results) == 0, f"{len(results)} fabricated IDs:\n" + \
        "\n".join(f"  [{r.location}] {r.message}" for r in results)


# ---------------------------------------------------------------------------
# Cross-case comparison tests
# ---------------------------------------------------------------------------


@pytest.mark.eval
def test_summaries_are_different(all_resumes):
    """Perfect-match cases should produce meaningfully different summaries."""
    from tests.eval.resume_checks import _content_words

    perfect_cases = [cid for cid in all_resumes if cid.startswith("RS_") and
                     all_resumes[cid].final_resume.get("summary")]

    if len(perfect_cases) < 2:
        pytest.skip("Need at least 2 resumes for comparison")

    for a, b in combinations(perfect_cases, 2):
        sum_a = all_resumes[a].final_resume.get("summary", "")
        sum_b = all_resumes[b].final_resume.get("summary", "")
        words_a = _content_words(sum_a)
        words_b = _content_words(sum_b)
        if not words_a or not words_b:
            continue
        jaccard = len(words_a & words_b) / len(words_a | words_b)
        assert jaccard < 0.5, (
            f"Summaries for {a} and {b} too similar (Jaccard={jaccard:.2f}):\n"
            f"  {a}: {sum_a[:80]}...\n"
            f"  {b}: {sum_b[:80]}..."
        )


# ---------------------------------------------------------------------------
# Critique → Refine loop tests
# ---------------------------------------------------------------------------


@pytest.mark.eval
@pytest.mark.parametrize("case_id", _case_ids())
def test_refine_doesnt_regress(all_resumes, case_id):
    """Refinement should not introduce MORE errors than the first draft had."""
    if case_id not in all_resumes:
        pytest.skip(f"Resume not generated for {case_id}")

    result = all_resumes[case_id]
    draft_report = run_deterministic_checks(result.first_draft)
    final_report = run_deterministic_checks(result.final_resume)

    if final_report.error_count > draft_report.error_count:
        new_errors = final_report.error_count - draft_report.error_count
        details = "\n".join(f"  [{r.location}] {r.check_name}: {r.message}"
                          for r in final_report.errors)
        pytest.fail(
            f"Refiner introduced {new_errors} new errors "
            f"(draft: {draft_report.error_count}, final: {final_report.error_count}):\n{details}"
        )


# ---------------------------------------------------------------------------
# Aggregate report
# ---------------------------------------------------------------------------


@pytest.mark.eval
def test_print_full_report(all_resumes, resume_profile):
    """Print comprehensive report across all generated resumes."""
    from collections import Counter

    print(f"\n{'='*70}")
    print(f"RESUME BENCHMARK REPORT — {len(all_resumes)} resumes generated")
    print(f"{'='*70}")

    check_counts: Counter = Counter()
    for case_id, result in sorted(all_resumes.items()):
        report = run_deterministic_checks(result.final_resume, resume_profile)
        print(f"\n  {case_id}: {report.error_count} errors, {report.warning_count} warnings "
              f"({result.generation_time_s:.1f}s)")
        for r in report.results:
            check_counts[r.check_name] += 1

        # Print critique summary
        critique = result.critique
        if critique:
            print(f"    Critique: {critique.get('overall_competitiveness', '?')}, "
                  f"{len(critique.get('weaknesses', []))} weaknesses")
            print(f"    Top concern: {critique.get('top_concern', '?')[:80]}")

        # Print summary preview
        summary = result.final_resume.get("summary", "")
        if summary:
            print(f"    Summary: {summary[:100]}...")

    print(f"\n  Check counts across all resumes:")
    for check_name, count in check_counts.most_common():
        print(f"    {check_name:25s} {count:3d}")

    # Draft vs final comparison
    print(f"\n  Draft → Final error comparison:")
    for case_id, result in sorted(all_resumes.items()):
        draft_report = run_deterministic_checks(result.first_draft)
        final_report = run_deterministic_checks(result.final_resume)
        delta = final_report.error_count - draft_report.error_count
        arrow = "↓" if delta < 0 else "↑" if delta > 0 else "="
        print(f"    {case_id}: {draft_report.error_count} → {final_report.error_count} {arrow}")

    print(f"{'='*70}")
