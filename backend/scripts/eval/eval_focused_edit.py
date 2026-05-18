"""Eval: multi-turn focused-edit ability of the chat agent.

Runs three scenarios that simulate the user clicking on a resume item, asking
for a rewrite, then iterating with feedback:

  1. Tighten a bullet, then tighten more.
  2. Rewrite a research description with a specific opening-verb feedback.
  3. Clean up a skills bucket with cross-bucket dedup feedback.

Each turn calls ``edit_section`` directly (skips the LangGraph router) and
then runs an LLM-as-judge to grade:

  - respects_instruction: did the new value actually do what the user asked?
  - no_fabrication: did the new value avoid metrics/skills not in the
    accomplishment data?
  - differs_from_prior: is the new value materially different from the
    immediately-prior turn's output?
  - voice_matches_grounding: when content_memory grounding is shown, does
    the new value mirror the past versions' opening verbs / structure?

Usage (inside the api container):
    docker compose exec api python scripts/eval/eval_focused_edit.py

CI usage: the script exits non-zero when the aggregate pass rate falls
below ``EVAL_PASS_THRESHOLD`` (default 0.80) and writes a JSON summary to
``output/eval_results.json`` for the workflow to upload as an artifact.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import sys
from pathlib import Path

# Make ``app`` importable when run as a standalone script
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from app.ai.client import RESUME_MODEL, get_openai_client  # noqa: E402
from app.ai.resume_agent import edit_section  # noqa: E402
from app.database import async_session  # noqa: E402
from app.models import UserProfile  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logger = logging.getLogger("eval_focused_edit")
logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Resume + state fixtures
# ---------------------------------------------------------------------------


def _build_state(
    *,
    resume_json: dict,
    profile_data: dict,
    user_message: str,
    target_path: str,
    chat_history: list[dict],
) -> dict:
    """Construct a complete AgentState dict for ``edit_section``."""
    from app.services.document_service import _get_nested
    try:
        target_value = _get_nested(resume_json, target_path)
    except (KeyError, IndexError, TypeError):
        target_value = None
    return {
        "user_message": user_message,
        "section_context": target_path,
        "resume_json": resume_json,
        "job_context": (
            "Senior Applied AI Scientist at a frontier model lab. "
            "Focus on evaluation methodology, human-in-the-loop systems, and "
            "shipping production AI tools."
        ),
        "profile_text": "",  # focused slice replaces this
        "company_research": None,
        "chat_history": chat_history + [{"role": "user", "content": user_message}],
        "generation_log": None,
        "strategic_plan": resume_json.get("_strategic_plan"),
        "writing_memory_text": "",
        "intent": "make_edit",
        "target_section_path": target_path,
        "target_section_value": target_value,
        "response_text": "",
        "updated_json": None,
        "updated_section_path": None,
        "_new_preference": None,
        "_session_factory": async_session,
        "_profile_data": profile_data,
    }


# ---------------------------------------------------------------------------
# LLM-as-judge
# ---------------------------------------------------------------------------


_JUDGE_SYSTEM = """\
You grade a single resume-editing turn. You receive: the user's instruction, \
the section's value BEFORE the edit, the section's value AFTER the edit, the \
underlying accomplishment data the edit is supposed to draw from, and \
optionally past hand-tuned versions of this same content.

Grade on 4 axes (each pass/fail):
1. respects_instruction: Did the AFTER value actually do what the user asked?
2. no_fabrication: Are all facts/metrics/skills in AFTER also present in the \
accomplishment data? (Stylistic phrasing is fine — only flag NEW facts.)
3. differs_from_prior: Is AFTER materially different from BEFORE? Trivial \
whitespace/punctuation tweaks count as fail.
4. voice_matches_grounding: If past hand-tuned versions are shown, does AFTER \
mirror their opening verb pattern / sentence structure? If no past versions \
are shown, score "n/a".

Output ONLY this JSON:
{
  "respects_instruction": "pass" | "fail",
  "no_fabrication": "pass" | "fail",
  "differs_from_prior": "pass" | "fail",
  "voice_matches_grounding": "pass" | "fail" | "n/a",
  "summary": "one short sentence: what worked or what failed"
}"""


async def judge_turn(
    *,
    user_instruction: str,
    before_value,
    after_value,
    accomplishment_data: dict | None,
    grounding_examples: list[str] | None,
) -> dict:
    """Ask a separate LLM call to grade a turn."""
    grounding_text = ""
    if grounding_examples:
        grounding_text = "\n\n## Past hand-tuned versions for this content\n" + "\n\n".join(
            f"- {g}" for g in grounding_examples
        )
    body = (
        f"## User instruction\n{user_instruction}\n\n"
        f"## BEFORE\n{json.dumps(before_value, indent=2) if not isinstance(before_value, str) else before_value}\n\n"
        f"## AFTER\n{json.dumps(after_value, indent=2) if not isinstance(after_value, str) else after_value}\n\n"
        f"## Accomplishment data (the only fact source)\n"
        f"{json.dumps(accomplishment_data, indent=2) if accomplishment_data else '(no anchor accomplishment)'}"
        f"{grounding_text}"
    )
    client = get_openai_client()
    resp = await client.chat.completions.create(
        model=RESUME_MODEL,
        messages=[
            {"role": "system", "content": _JUDGE_SYSTEM},
            {"role": "user", "content": body},
        ],
        max_completion_tokens=400,
        temperature=0,
    )
    text = (resp.choices[0].message.content or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {
            "respects_instruction": "fail",
            "no_fabrication": "fail",
            "differs_from_prior": "fail",
            "voice_matches_grounding": "n/a",
            "summary": f"judge JSON parse failed: {text[:200]}",
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _accomplishment_by_id(profile_data: dict, accomplishment_id: str) -> dict | None:
    accomplishments = (profile_data.get("complete_profile") or {}).get("accomplishments") or []
    for a in accomplishments:
        if a.get("id") == accomplishment_id:
            return a
    return None


def _short(value, n: int = 120) -> str:
    if isinstance(value, str):
        return (value[:n] + ("…" if len(value) > n else ""))
    rendered = json.dumps(value)
    return rendered[:n] + ("…" if len(rendered) > n else "")


# ---------------------------------------------------------------------------
# Scenario runners
# ---------------------------------------------------------------------------


async def run_scenario(
    *,
    name: str,
    profile_data: dict,
    resume_json: dict,
    target_path: str,
    accomplishment_id: str | None,
    turns: list[str],
    grounding_for_judge: list[str] | None = None,
) -> dict:
    """Run a multi-turn scenario. Each turn re-invokes ``edit_section`` with
    the accumulated chat history. Returns a per-turn report."""
    print()
    print("=" * 78)
    print(f"SCENARIO: {name}")
    print(f"  path: {target_path}")
    print("=" * 78)

    accomplishment = _accomplishment_by_id(profile_data, accomplishment_id) if accomplishment_id else None
    chat_history: list[dict] = []
    state_resume = copy.deepcopy(resume_json)

    turn_reports: list[dict] = []
    prior_value = None
    from app.services.document_service import _get_nested
    prior_value = _get_nested(state_resume, target_path)

    for i, instruction in enumerate(turns, 1):
        print()
        print(f"--- Turn {i} ---")
        print(f"USER: {instruction}")
        print(f"BEFORE: {_short(prior_value, 200)}")

        state = _build_state(
            resume_json=state_resume,
            profile_data=profile_data,
            user_message=instruction,
            target_path=target_path,
            chat_history=chat_history,
        )
        result = await edit_section(state)
        new_resume = result.get("updated_json") or state_resume
        try:
            new_value = _get_nested(new_resume, target_path)
        except Exception:
            new_value = result.get("response_text", "")
        response_text = result.get("response_text", "")

        print(f"AFTER:  {_short(new_value, 240)}")

        # Append to chat history as the chat router would
        chat_history.append({"role": "user", "content": instruction})
        chat_history.append({"role": "assistant", "content": response_text})
        # Apply update so next turn sees the new state
        state_resume = new_resume

        # Judge
        judgment = await judge_turn(
            user_instruction=instruction,
            before_value=prior_value,
            after_value=new_value,
            accomplishment_data=accomplishment,
            grounding_examples=grounding_for_judge,
        )
        print(
            f"JUDGE:  instruction={judgment['respects_instruction']:5} "
            f"no_fabrication={judgment['no_fabrication']:5} "
            f"differs={judgment['differs_from_prior']:5} "
            f"voice={judgment['voice_matches_grounding']}"
        )
        print(f"  {judgment.get('summary', '')}")

        turn_reports.append({
            "turn": i,
            "instruction": instruction,
            "before": prior_value,
            "after": new_value,
            "judgment": judgment,
        })
        prior_value = new_value

    return {"name": name, "turns": turn_reports}


# ---------------------------------------------------------------------------
# Sample resume — minimal but realistic, anchored to known accomplishment IDs
# ---------------------------------------------------------------------------


def _sample_resume() -> dict:
    """Build a small resume_json with content for our 3 scenarios."""
    return {
        "tagline": "AI Systems · Evaluation · Research Engineering",
        "summary": (
            "Senior applied ML researcher and tech lead who turns ambiguous, "
            "high-stakes problems into production tools for regulated and "
            "research-heavy settings."
        ),
        "selected_research": [
            {
                "category_label": "RESEARCH INFRASTRUCTURE",
                "title": "MUSE: LLM-Assisted Qualitative Research System",
                "description": (
                    "Designed and built MUSE, an internal human-AI research platform that "
                    "combines retrieval-backed LLM workflows with human review for "
                    "structured coding, thematic synthesis, and document Q&A across "
                    "qualitative research projects. Validated against trained human coders "
                    "on real research tasks rather than generic benchmarks, reaching "
                    "agreement rates that cleared the bar for production use. Now used "
                    "across the organization."
                ),
                "accomplishment_id": "rand-muse",
            }
        ],
        "experience": {
            "rand_corporation": {
                "bullets": [
                    {
                        "text": (
                            "Shipped MUSE, an internal Python-based human-AI research "
                            "platform that 400+ researchers now use across 600+ projects, "
                            "owning system design, implementation, and researcher-driven "
                            "feature priorities, demonstrating end-to-end product ownership."
                        ),
                        "accomplishment_ids": ["rand-muse"],
                    }
                ],
            },
            "finra": {
                "bullets": [
                    {
                        "text": (
                            "Improved fraud discovery efficiency 5X by converting trading "
                            "time series into image-like representations and applying "
                            "recommendation algorithms to surface suspicious patterns."
                        ),
                        "accomplishment_ids": ["finra-image-recommendation"],
                    }
                ],
            },
        },
        "technical_skills": {
            "ai_systems": "LLM evaluation, RAG pipelines, NLP, Python, LangGraph, SQL, Claude, REST API design",
            "data_science": "Python, SQL, scikit-learn, PySpark, Bayesian inference",
            "engineering": "FastAPI, PostgreSQL, Docker, Git, REST API design",
        },
        "_strategic_plan": {
            "core_argument": "Builds AI tools that domain experts actually adopt — production rigor in research-heavy settings.",
            "tone": "shipping velocity",
        },
    }


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------


async def main():
    async with async_session() as session:
        result = await session.execute(select(UserProfile).limit(1))
        profile = result.scalar_one_or_none()
    if profile is None:
        print("No UserProfile in DB — run profile sync first.")
        return
    profile_data = profile.data
    resume_json = _sample_resume()

    # Grounding examples used by the judge for the research scenario. These
    # mirror what content_memory holds for rand-muse — the agent's edit_section
    # will fetch them itself when grounding is available.
    research_grounding = [
        "Designed and built an internal human-AI research platform for structured coding, "
        "thematic synthesis, and dynamic question-answering over qualitative datasets.",
        "Replaced a manual qualitative research workflow with an AI-assisted system that "
        "now handles structured coding, thematic synthesis, and policy analysis.",
        "Designed and developed a platform to replace largely manual and inefficient "
        "qualitative research workflows with AI-powered annotation and synthesis.",
    ]

    scenarios = [
        run_scenario(
            name="Tighten a bullet, then tighten more",
            profile_data=profile_data,
            resume_json=resume_json,
            target_path="experience.rand_corporation.bullets.0.text",
            accomplishment_id="rand-muse",
            turns=[
                "This bullet is way too long and reads like marketing copy. Make it punchier and lose the participial tail.",
                "Still too wordy — cut to one sentence under 22 words. Lead with what changed for users, not the system name.",
                "Better. Now end the sentence with the scale figure (400+ researchers) — that's the punch.",
            ],
        ),
        run_scenario(
            name="Rewrite research description with explicit feedback",
            profile_data=profile_data,
            resume_json=resume_json,
            target_path="selected_research.0.description",
            accomplishment_id="rand-muse",
            turns=[
                "Rewrite this. The opener feels generic — lead with a stronger transformation verb.",
                "I specifically want it to OPEN with 'Replaced' — that's how I've written it before. Keep the kappa metric in the validation sentence.",
                "Good. Now drop the last sentence — 'now used across the organization' duplicates the bullet.",
            ],
            grounding_for_judge=research_grounding,
        ),
        run_scenario(
            name="Skill bucket dedup feedback",
            profile_data=profile_data,
            resume_json=resume_json,
            target_path="technical_skills.ai_systems",
            accomplishment_id=None,
            turns=[
                "Clean this up. SQL belongs in data_science, not here. REST API design belongs in engineering. Drop them.",
                "Also drop Python — it's already implied by the data_science bucket. Lead with LLM evaluation since this role wants eval rigor.",
            ],
        ),
    ]

    reports = []
    for s in scenarios:
        reports.append(await s)

    # Aggregate
    print()
    print("=" * 78)
    print("AGGREGATE")
    print("=" * 78)
    pass_count = 0
    total = 0
    failed_checks: list[dict] = []
    for r in reports:
        for t in r["turns"]:
            j = t["judgment"]
            for axis in ("respects_instruction", "no_fabrication", "differs_from_prior"):
                total += 1
                if j.get(axis) == "pass":
                    pass_count += 1
            if j.get("voice_matches_grounding") in ("pass", "fail"):
                total += 1
                if j.get("voice_matches_grounding") == "pass":
                    pass_count += 1
            fails = [
                a for a in ("respects_instruction", "no_fabrication", "differs_from_prior", "voice_matches_grounding")
                if j.get(a) == "fail"
            ]
            if fails:
                failed_checks.append({
                    "scenario": r["name"],
                    "turn": t["turn"],
                    "axes": fails,
                    "summary": j.get("summary", ""),
                })

    pass_rate = (pass_count / total) if total else 0.0
    print(f"  passed {pass_count} / {total} graded checks   ({pass_rate * 100:.1f}%)")

    if failed_checks:
        print()
        print("  failed checks:")
        for fc in failed_checks:
            print(f"    [{fc['scenario']}] turn {fc['turn']}: {', '.join(fc['axes'])} — {fc['summary']}")

    # ----- CI artifact -----------------------------------------------------
    threshold = float(os.environ.get("EVAL_PASS_THRESHOLD", "0.80"))
    summary_path = Path(os.environ.get("EVAL_SUMMARY_PATH", "output/eval_results.json"))
    summary = {
        "pass_count": pass_count,
        "total_checks": total,
        "pass_rate": round(pass_rate, 4),
        "threshold": threshold,
        "passed_gate": pass_rate >= threshold,
        "failed_checks": failed_checks,
        "scenarios": [
            {
                "name": r["name"],
                "turns": [
                    {
                        "turn": t["turn"],
                        "instruction": t["instruction"],
                        "judgment": t["judgment"],
                    }
                    for t in r["turns"]
                ],
            }
            for r in reports
        ],
    }
    try:
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, indent=2, default=str))
        print()
        print(f"  wrote summary → {summary_path}")
    except Exception as e:
        print(f"  warning: failed to write summary: {e}")

    # ----- Gate ------------------------------------------------------------
    if pass_rate < threshold:
        print()
        print(f"  ✗ FAIL: pass rate {pass_rate * 100:.1f}% < threshold {threshold * 100:.1f}%")
        sys.exit(1)
    else:
        print()
        print(f"  ✓ PASS: pass rate {pass_rate * 100:.1f}% ≥ threshold {threshold * 100:.1f}%")


if __name__ == "__main__":
    asyncio.run(main())
