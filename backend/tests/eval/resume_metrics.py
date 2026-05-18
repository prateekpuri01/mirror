"""LLM-verified checks and LLM-as-judge evaluations for resume quality.

Two tiers:
  - LLM-verified checks: focused calls that catch what regex misses (compound clauses,
    semantic redundancy, critic leakage, passive voice quality, skills accuracy)
  - LLM-as-judge: holistic quality scores (naturalness, coherence, tailoring, critique incorporation)

All functions are async and require a running OpenAI API key.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from app.ai.client import get_openai_client, RESUME_MODEL
from tests.eval.resume_checks import CheckResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class JudgeResult:
    metric_name: str
    score: int  # 1-5 Likert scale
    rationale: str
    flagged_sections: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Shared LLM helper
# ---------------------------------------------------------------------------


async def _call_judge(system: str, user_content: str, max_tokens: int = 2000) -> dict:
    """Call the LLM and parse JSON response."""
    client = get_openai_client()
    response = await client.chat.completions.create(
        model=RESUME_MODEL,
        max_completion_tokens=max_tokens,
        temperature=0.2,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
    )
    text = response.choices[0].message.content.strip()
    # Strip markdown fences
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    return json.loads(text)


def _format_resume_text(resume: dict) -> str:
    """Format resume content fields as readable text for LLM evaluation."""
    lines = []
    if resume.get("summary"):
        lines.append(f"SUMMARY: {resume['summary']}")
    for i, entry in enumerate(resume.get("selected_research", [])):
        lines.append(f"\nRESEARCH {i+1} [{entry.get('category_label', '')}] — {entry.get('title', '')}")
        lines.append(f"  {entry.get('description', '')}")
    for emp_key, emp_data in resume.get("experience", {}).items():
        lines.append(f"\nEXPERIENCE — {emp_key}")
        for bi, bullet in enumerate(emp_data.get("bullets", [])):
            lines.append(f"  [{bi}] {bullet}")
    return "\n".join(lines)


# ===========================================================================
# LLM-VERIFIED CHECKS (targeted, catch what regex misses)
# ===========================================================================


async def llm_check_compound_clauses(resume: dict) -> list[CheckResult]:
    """LLM identifies sentences that fuse two unrelated ideas — catches forms regex misses."""
    resume_text = _format_resume_text(resume)

    system = """\
You are a writing quality checker. Your ONLY job is to find sentences that contain \
two independent ideas joined in a single sentence. This includes:
- Participial tails: ", demonstrating X", ", which enables Y", " — a result that shows Z"
- Compound sentences: "Built X and also achieved Y" where X and Y are unrelated ideas
- Run-on clauses that fuse a technical fact with an impact statement

Output ONLY valid JSON (no markdown fences): an array of objects, each with:
- "location": where in the resume (e.g., "summary", "experience.rand.bullets.2")
- "sentence": the exact offending sentence
- "issue": brief description of the two ideas that should be separated

If no issues found, return an empty array: []"""

    user_content = f"Check each sentence in this resume for compound clause violations:\n\n{resume_text}"

    try:
        findings = await _call_judge(system, user_content, max_tokens=1500)
    except (json.JSONDecodeError, Exception) as e:
        logger.warning("LLM compound clause check failed: %s", e)
        return []

    results = []
    for f in findings:
        results.append(CheckResult(
            check_name="compound_clause_llm",
            severity="error",
            location=f.get("location", "unknown"),
            message=f.get("issue", "Two ideas fused in one sentence"),
            offending_text=f.get("sentence", ""),
        ))
    return results


async def llm_check_passive_voice(resume: dict) -> list[CheckResult]:
    """LLM distinguishes bad passive (hiding agency) from OK passive (result is the point)."""
    resume_text = _format_resume_text(resume)

    system = """\
You are checking resume bullets for BAD passive voice — where the candidate's agency is hidden.

BAD passive (flag these):
- "Was designed to enable..." → who designed it? The candidate! Write "Designed..."
- "The system was built..." → "Built the system..."
- "Evaluation protocols were developed..." → "Developed evaluation protocols..."

OK passive (do NOT flag):
- "Was adopted by 400 users" → the adoption/scale is the point
- "Was published in Nature Chemistry" → standard academic framing
- "Was awarded the NSF fellowship" → passive is natural for awards

Output ONLY valid JSON: array of {"location", "sentence", "suggestion"}.
Empty array if no bad passive found."""

    user_content = f"Check for bad passive voice:\n\n{resume_text}"

    try:
        findings = await _call_judge(system, user_content, max_tokens=1000)
    except (json.JSONDecodeError, Exception) as e:
        logger.warning("LLM passive voice check failed: %s", e)
        return []

    results = []
    for f in findings:
        results.append(CheckResult(
            check_name="passive_voice_llm",
            severity="warning",
            location=f.get("location", "unknown"),
            message=f"Bad passive: {f.get('suggestion', 'rewrite in active voice')}",
            offending_text=f.get("sentence", ""),
        ))
    return results


async def llm_check_critic_leakage(resume: dict) -> list[CheckResult]:
    """LLM compares _critique text against resume content to find semantic leakage."""
    critique = resume.get("_critique")
    if not critique:
        return []

    resume_text = _format_resume_text(resume)
    critique_text = json.dumps(critique, indent=2)

    system = """\
You are checking whether a resume editor accidentally leaked internal critique \
language into the resume content. The _critique is internal analysis that should \
NEVER appear in the resume itself.

Types of leakage:
1. DIRECT COPY: Critic says "no RL experience" → summary says "not core RL"
2. REPHRASED: Critic says "no production ML" → summary avoids production entirely
3. DEFENSIVE: Resume sounds apologetic about gaps the critic identified
4. DISCLAIMERS: "Best aligned with X rather than Y", "strongest in A, not B"

A resume should SELL, never apologize. Any qualifying/hedging language about what \
the candidate can't do is leakage.

Output ONLY valid JSON: array of {"location", "leaked_text", "critique_source", "type"}.
Empty array if no leakage found."""

    user_content = f"""## Resume content:
{resume_text}

## Internal critique (should NOT appear in resume):
{critique_text}"""

    try:
        findings = await _call_judge(system, user_content, max_tokens=1500)
    except (json.JSONDecodeError, Exception) as e:
        logger.warning("LLM critic leakage check failed: %s", e)
        return []

    results = []
    for f in findings:
        results.append(CheckResult(
            check_name="critic_leakage_llm",
            severity="error",
            location=f.get("location", "unknown"),
            message=f"Critic leakage ({f.get('type', 'unknown')}): "
                    f"critique said '{f.get('critique_source', '?')[:60]}'",
            offending_text=f.get("leaked_text", ""),
        ))
    return results


async def llm_check_redundancy(resume: dict) -> list[CheckResult]:
    """LLM evaluates whether research descriptions and bullets for the same project are complementary or redundant."""
    # Find accomplishments appearing in both sections
    research_by_id = {}
    for i, entry in enumerate(resume.get("selected_research", [])):
        aid = entry.get("accomplishment_id")
        if aid:
            research_by_id[aid] = entry.get("description", "")

    pairs = []
    for emp_key, emp_data in resume.get("experience", {}).items():
        acc_ids = emp_data.get("accomplishment_ids", [])
        for bi, bullet in enumerate(emp_data.get("bullets", [])):
            if bi < len(acc_ids):
                aid = acc_ids[bi]
                if aid in research_by_id:
                    pairs.append({
                        "accomplishment_id": aid,
                        "research_description": research_by_id[aid],
                        "bullet": bullet,
                        "bullet_location": f"experience.{emp_key}.bullets.{bi}",
                    })

    if not pairs:
        return []

    system = """\
You evaluate whether a resume's research descriptions and experience bullets about the \
SAME project offer complementary or redundant information.

COMPLEMENTARY (good): Research description tells the STORY (what problem, what was built, \
what happened). Bullet states the OUTCOME (metric, scale, result). Together they're compelling.

REDUNDANT (bad): Both say the same thing in different words. Reader learns nothing new from \
reading both.

For each pair, output: {"accomplishment_id", "verdict": "complementary"|"redundant", "explanation"}.
Output ONLY valid JSON array."""

    user_content = f"Evaluate these research description / bullet pairs:\n\n{json.dumps(pairs, indent=2)}"

    try:
        findings = await _call_judge(system, user_content, max_tokens=1500)
    except (json.JSONDecodeError, Exception) as e:
        logger.warning("LLM redundancy check failed: %s", e)
        return []

    results = []
    for f in findings:
        if f.get("verdict") == "redundant":
            # Find the bullet location
            loc = next((p["bullet_location"] for p in pairs
                       if p["accomplishment_id"] == f.get("accomplishment_id")), "unknown")
            results.append(CheckResult(
                check_name="redundancy_llm",
                severity="warning",
                location=loc,
                message=f"Semantic redundancy for '{f.get('accomplishment_id', '?')}': "
                        f"{f.get('explanation', '')}",
                offending_text="",
            ))
    return results


async def llm_check_skills_accuracy(resume: dict, profile_data: dict) -> list[CheckResult]:
    """LLM does fuzzy skills whitelist matching and category placement checks."""
    skills = profile_data.get("skills", {})
    whitelist = []
    for cat in ["technical", "communication", "tools"]:
        cat_skills = skills.get(cat, [])
        if isinstance(cat_skills, list):
            whitelist.extend(cat_skills)
        elif isinstance(cat_skills, str):
            whitelist.append(cat_skills)

    tech_skills = resume.get("technical_skills", {})
    if not tech_skills or not whitelist:
        return []

    system = """\
You check whether a resume's technical skills section only contains skills from an \
allowed whitelist. Use fuzzy matching — "RAG pipelines" matches "RAG", \
"LLM evaluation" matches "LLM evaluation & fine-tuning".

Also check category placement — a communication skill shouldn't be under "engineering".

Output ONLY valid JSON: array of {"skill", "category", "issue": "not_on_whitelist"|"wrong_category", "suggestion"}.
Empty array if all skills are valid."""

    user_content = f"""## Resume technical_skills:
{json.dumps(tech_skills, indent=2)}

## Allowed skills whitelist:
{json.dumps(whitelist)}"""

    try:
        findings = await _call_judge(system, user_content, max_tokens=1000)
    except (json.JSONDecodeError, Exception) as e:
        logger.warning("LLM skills accuracy check failed: %s", e)
        return []

    results = []
    for f in findings:
        results.append(CheckResult(
            check_name="skills_accuracy_llm",
            severity="error" if f.get("issue") == "not_on_whitelist" else "warning",
            location=f"technical_skills.{f.get('category', 'unknown')}",
            message=f"Skill '{f.get('skill', '?')}': {f.get('issue', '?')} — {f.get('suggestion', '')}",
            offending_text=f.get("skill", ""),
        ))
    return results


# ---------------------------------------------------------------------------
# Aggregate LLM checks
# ---------------------------------------------------------------------------

async def run_llm_checks(
    resume_json: dict,
    profile_data: dict | None = None,
) -> list[CheckResult]:
    """Run all LLM-verified checks. Returns list of CheckResults."""
    import asyncio
    tasks = [
        llm_check_compound_clauses(resume_json),
        llm_check_passive_voice(resume_json),
        llm_check_critic_leakage(resume_json),
        llm_check_redundancy(resume_json),
    ]
    if profile_data:
        tasks.append(llm_check_skills_accuracy(resume_json, profile_data))

    results_lists = await asyncio.gather(*tasks, return_exceptions=True)
    all_results = []
    for r in results_lists:
        if isinstance(r, Exception):
            logger.warning("LLM check failed: %s", r)
        else:
            all_results.extend(r)
    return all_results


# ===========================================================================
# LLM-AS-JUDGE (holistic quality scores, 1-5 scale)
# ===========================================================================


async def judge_naturalness(resume: dict, job_text: str = "") -> JudgeResult:
    """Rate how natural/human the resume writing sounds (1-5)."""
    resume_text = _format_resume_text(resume)

    system = """\
You are a senior technical recruiter at a top AI lab. Rate the NATURALNESS of this \
resume's writing on a 1-5 scale.

5 = Every sentence sounds like a confident, specific human wrote it
4 = Mostly natural with 1-2 slightly stiff sentences
3 = Serviceable but clearly polished by a tool — several awkward constructions
2 = Noticeably AI-generated — compound clauses, resume-speak, template patterns
1 = Obviously machine-generated — no human would write this way

Focus on:
(a) Do sentences flow when read aloud?
(b) Are there awkward compound clauses or participial tails?
(c) Specific vivid language vs. generic resume-speak?
(d) Would a real person write this summary/these bullets?

Output ONLY valid JSON: {"score": 1-5, "rationale": "...", "worst_sentences": ["...", "...", "..."]}"""

    try:
        result = await _call_judge(system, resume_text)
        return JudgeResult(
            metric_name="naturalness",
            score=result.get("score", 0),
            rationale=result.get("rationale", ""),
            flagged_sections=result.get("worst_sentences", []),
        )
    except Exception as e:
        logger.warning("Naturalness judge failed: %s", e)
        return JudgeResult(metric_name="naturalness", score=0, rationale=f"Error: {e}")


async def judge_summary_coherence(resume: dict, job_text: str = "") -> JudgeResult:
    """Rate whether the summary makes an argument vs. listing features (1-5)."""
    summary = resume.get("summary", "")

    system = """\
Rate this resume summary on 1-5 for ARGUMENT COHERENCE.

5 = Clear thesis — every sentence builds toward one compelling reason to interview. \
    Genuinely unique to this specific role.
4 = Has a point of view but one sentence feels generic or disconnected
3 = Reads like a career overview with some tailoring — could apply to 3+ similar jobs
2 = Feature list disguised as prose — "Built X, shipped Y, and trained Z"
1 = Generic career summary that could be copy-pasted to any job posting

Output ONLY valid JSON: {"score": 1-5, "rationale": "...", "flagged_issues": ["..."]}"""

    user_content = f"SUMMARY: {summary}"
    if job_text:
        user_content += f"\n\nJOB POSTING:\n{job_text[:500]}"

    try:
        result = await _call_judge(system, user_content, max_tokens=500)
        return JudgeResult(
            metric_name="summary_coherence",
            score=result.get("score", 0),
            rationale=result.get("rationale", ""),
            flagged_sections=result.get("flagged_issues", []),
        )
    except Exception as e:
        logger.warning("Summary coherence judge failed: %s", e)
        return JudgeResult(metric_name="summary_coherence", score=0, rationale=f"Error: {e}")


async def judge_tailoring(resume: dict, job_text: str) -> JudgeResult:
    """Rate how precisely the resume is tailored to the specific job (1-5)."""
    resume_text = _format_resume_text(resume)

    system = """\
Rate this resume's TAILORING PRECISION on 1-5.

5 = Accomplishments clearly selected and framed for this specific role. Research entries \
    use job-relevant category labels. Skills reordered. Tagline mirrors job language. \
    Could not be reused for a different role without changes.
4 = Mostly tailored — right accomplishments chosen but framing could be sharper
3 = Some tailoring visible but feels like a general resume with keyword adjustments
2 = Minimal tailoring — right skills listed but accomplishments not job-specific
1 = No visible tailoring — generic resume

Output ONLY valid JSON: {"score": 1-5, "rationale": "...", "tailoring_evidence": ["..."]}"""

    user_content = f"## Resume:\n{resume_text}\n\n## Job posting:\n{job_text[:800]}"

    try:
        result = await _call_judge(system, user_content, max_tokens=500)
        return JudgeResult(
            metric_name="tailoring",
            score=result.get("score", 0),
            rationale=result.get("rationale", ""),
            flagged_sections=result.get("tailoring_evidence", []),
        )
    except Exception as e:
        logger.warning("Tailoring judge failed: %s", e)
        return JudgeResult(metric_name="tailoring", score=0, rationale=f"Error: {e}")


async def judge_critique_incorporation(resume: dict) -> JudgeResult:
    """Rate whether the refiner addressed the critic's weaknesses (1-5)."""
    critique = resume.get("_critique")
    if not critique:
        return JudgeResult(
            metric_name="critique_incorporation",
            score=0,
            rationale="No _critique data available",
        )

    resume_text = _format_resume_text(resume)
    critique_text = json.dumps(critique, indent=2)

    system = """\
You are evaluating whether a resume revision addressed the weaknesses identified by a \
recruiter-critic. For each weakness in the critique:
- "addressed": the final resume clearly fixed this issue
- "partially_addressed": some improvement but not fully resolved
- "not_addressed": the weakness remains unchanged
- "over_corrected": fixing the weakness introduced a NEW problem (e.g., fabricating content)

Rate overall incorporation 1-5:
5 = All fixable weaknesses addressed, no regressions
4 = Most addressed, 1 partially
3 = Mixed — some addressed, some missed
2 = Mostly not addressed
1 = Critique completely ignored or caused regressions

Output ONLY valid JSON:
{
  "score": 1-5,
  "rationale": "...",
  "per_weakness": [{"requirement": "...", "status": "addressed|partially|not_addressed|over_corrected"}]
}"""

    user_content = f"## Final resume:\n{resume_text}\n\n## Critique:\n{critique_text}"

    try:
        result = await _call_judge(system, user_content, max_tokens=1500)
        statuses = [w.get("status", "unknown") for w in result.get("per_weakness", [])]
        return JudgeResult(
            metric_name="critique_incorporation",
            score=result.get("score", 0),
            rationale=result.get("rationale", ""),
            flagged_sections=statuses,
        )
    except Exception as e:
        logger.warning("Critique incorporation judge failed: %s", e)
        return JudgeResult(metric_name="critique_incorporation", score=0, rationale=f"Error: {e}")


# ---------------------------------------------------------------------------
# Aggregate judge runner
# ---------------------------------------------------------------------------

async def run_all_judges(
    resume: dict,
    job_text: str = "",
) -> dict[str, JudgeResult]:
    """Run all LLM-as-judge evaluations. Returns metric_name -> JudgeResult."""
    import asyncio
    results = await asyncio.gather(
        judge_naturalness(resume, job_text),
        judge_summary_coherence(resume, job_text),
        judge_tailoring(resume, job_text) if job_text else _noop_judge("tailoring"),
        judge_critique_incorporation(resume),
        return_exceptions=True,
    )
    judges = {}
    for r in results:
        if isinstance(r, Exception):
            logger.warning("Judge failed: %s", r)
        elif isinstance(r, JudgeResult):
            judges[r.metric_name] = r
    return judges


async def _noop_judge(name: str) -> JudgeResult:
    return JudgeResult(metric_name=name, score=0, rationale="Skipped — no job text provided")
