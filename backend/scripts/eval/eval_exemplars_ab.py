"""Pairwise A/B harness: brainstorm output with vs without exemplar context.

Runs the brainstorm handler twice for each test case — once with the
edit_exemplars retrieval enabled, once disabled (via EXEMPLARS_DISABLED).
An LLM judge then scores each pair on three dimensions:

  - faithfulness: how well the output matches the user's historical voice
    (as inferred from current resume + any visible exemplars)
  - job_fit: how well the output fits the target role
  - prose_quality: clarity, directness, absence of corporate jargon

Pairwise (A vs B) is more reliable than absolute scoring with sparse gold
data, which is why we use it here. The judge sees both outputs labeled
neutrally as "Output X" / "Output Y" — order is randomized per case so the
judge can't bias toward position.

Usage:
  docker compose exec -T api python -m scripts.eval.eval_exemplars_ab \
    --job-id <uuid> --cases backend/scripts/eval/exemplars_cases.json

Test cases JSON shape:
  [{"job_id": "<uuid>", "instruction": "make the summary punchier",
    "section_path": "summary"}, ...]

If no --cases is provided, a built-in default set runs against --job-id.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from app.ai.client import RESUME_MODEL, get_openai_client
from app.ai.prompts import format_job_for_scoring
from app.ai.resume_agent import brainstorm
from app.ai.resume_prompts import build_full_profile_for_resume
from app.database import async_session
from app.models import Company, DocType, Job, UserProfile
from app.services import chat_service, document_service

logger = logging.getLogger(__name__)


JUDGE_SYSTEM = """\
You are evaluating two AI assistant outputs that were generated to respond
to the same user instruction about a resume. The two outputs were produced
by the same model with the same prompt — except Output X had access to the
user's historical edit patterns (a "How you've edited similar passages
before" block in its context), and Output Y did not.

You don't know which is X and which is Y. Compare them on three dimensions:

  - faithfulness: which output sounds more like the user's actual voice
    (use the current resume content as a reference for what their voice is)
  - job_fit: which output better matches the target role's needs
  - prose_quality: which output is clearer, more direct, less corporate

For each dimension, pick a winner ("X" / "Y" / "tie") and give a one-
sentence reason. Then pick an overall winner.

Respond with ONLY this JSON (no fences):

{
  "faithfulness": {"winner": "X|Y|tie", "reason": "..."},
  "job_fit":      {"winner": "X|Y|tie", "reason": "..."},
  "prose_quality":{"winner": "X|Y|tie", "reason": "..."},
  "overall":      {"winner": "X|Y|tie", "reason": "..."}
}
"""


@dataclass
class Case:
    job_id: uuid.UUID
    instruction: str
    section_path: str | None = None


@dataclass
class PairResult:
    case: Case
    with_exemplars: str
    without_exemplars: str
    judge: dict
    # Which letter (X / Y) corresponded to the with-exemplars output, so
    # downstream tally can re-map winners correctly.
    x_is_with_exemplars: bool


async def _load_state(job_id: uuid.UUID) -> dict:
    """Build the minimum agent_state the brainstorm handler needs."""
    async with async_session() as session:
        job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
        if job is None:
            raise SystemExit(f"Job not found: {job_id}")

        docs = await document_service.list_documents_for_job(session, job_id)
        resume_doc = next(
            (d for d in docs if d.doc_type == DocType.resume and d.content_json), None
        )
        if resume_doc is None:
            raise SystemExit(f"No resume doc for job {job_id}")

        profile = (await session.execute(select(UserProfile).limit(1))).scalar_one_or_none()
        if profile is None:
            raise SystemExit("No UserProfile")

        company_notes = None
        if job.company_id:
            company = (
                await session.execute(select(Company).where(Company.id == job.company_id))
            ).scalar_one_or_none()
            if company and company.notes:
                company_notes = company.notes

        msgs = await chat_service.list_messages(session, job_id)
        chat_history = [{"role": m.role, "content": m.content} for m in msgs[-10:]]

    company_research = None
    if resume_doc.content_json:
        company_research = resume_doc.content_json.get("_research")
    if not company_research and job.extra_metadata:
        company_research = (job.extra_metadata or {}).get("company_research")

    return {
        "resume_json": resume_doc.content_json,
        "job_context": format_job_for_scoring(job, company_notes=company_notes),
        "profile_text": build_full_profile_for_resume(profile.data),
        "company_research": company_research,
        "chat_history": chat_history,
        "strategic_plan": (resume_doc.content_json or {}).get("_strategic_plan"),
        "writing_memory_text": "",
        "_session_factory": async_session,
        "_profile_data": profile.data,
        "_job_id": job_id,
        "_doc_id": resume_doc.id,
    }


async def _run_brainstorm(case: Case, *, with_exemplars: bool) -> str:
    base_state = await _load_state(case.job_id)
    state = {
        **base_state,
        "user_message": case.instruction,
        "section_context": case.section_path,
        "intent": "brainstorm",
        "target_section_path": None,
        "target_section_value": None,
        "response_text": "",
        "updated_json": None,
        "updated_section_path": None,
        "_new_preference": None,
        "_action_cards": None,
    }
    # Toggle exemplars per-run via env var (the handler reads it on each call).
    prev = os.environ.get("EXEMPLARS_DISABLED")
    os.environ["EXEMPLARS_DISABLED"] = "0" if with_exemplars else "1"
    try:
        result = await brainstorm(state)
    finally:
        if prev is None:
            os.environ.pop("EXEMPLARS_DISABLED", None)
        else:
            os.environ["EXEMPLARS_DISABLED"] = prev
    return result.get("response_text") or ""


async def _judge_pair(case: Case, output_x: str, output_y: str, resume_summary: str) -> dict:
    client = get_openai_client()
    user_content = (
        f"## User instruction\n{case.instruction}\n\n"
        f"## Target section\n{case.section_path or '(not specified)'}\n\n"
        f"## Current resume (excerpt)\n{resume_summary[:1500]}\n\n"
        f"## Output X\n{output_x}\n\n"
        f"## Output Y\n{output_y}\n"
    )
    resp = await client.chat.completions.create(
        model=RESUME_MODEL,
        max_completion_tokens=800,
        temperature=0.2,
        messages=[
            {"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content": user_content},
        ],
    )
    text = resp.choices[0].message.content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    return json.loads(text)


def _remap_judge(judge: dict, x_is_with_exemplars: bool) -> dict:
    """Translate judge's X/Y verdict into with-exemplars / without-exemplars."""
    mapping = (
        {"X": "with", "Y": "without"} if x_is_with_exemplars else {"X": "without", "Y": "with"}
    )
    out: dict = {}
    for dim, verdict in judge.items():
        winner = verdict.get("winner", "tie")
        out[dim] = {
            "winner": mapping.get(winner, "tie"),
            "reason": verdict.get("reason", ""),
        }
    return out


async def run_case(case: Case) -> PairResult:
    with_text = await _run_brainstorm(case, with_exemplars=True)
    without_text = await _run_brainstorm(case, with_exemplars=False)

    # Randomize X/Y assignment so the judge can't position-bias.
    x_is_with_exemplars = random.random() < 0.5
    if x_is_with_exemplars:
        out_x, out_y = with_text, without_text
    else:
        out_x, out_y = without_text, with_text

    # Build a thin resume excerpt for the judge's reference.
    state = await _load_state(case.job_id)
    resume_excerpt = json.dumps(state["resume_json"], indent=2)[:3000]

    raw_judge = await _judge_pair(case, out_x, out_y, resume_excerpt)
    judge = _remap_judge(raw_judge, x_is_with_exemplars)

    return PairResult(
        case=case,
        with_exemplars=with_text,
        without_exemplars=without_text,
        judge=judge,
        x_is_with_exemplars=x_is_with_exemplars,
    )


def _tally(results: list[PairResult]) -> dict:
    counts: dict[str, dict[str, int]] = {}
    for r in results:
        for dim, verdict in r.judge.items():
            d = counts.setdefault(dim, {"with": 0, "without": 0, "tie": 0})
            d[verdict["winner"]] = d.get(verdict["winner"], 0) + 1
    return counts


def _print_tally(counts: dict, n: int) -> None:
    print(f"\n=== Pairwise A/B tally (n={n}) ===")
    for dim in ("faithfulness", "job_fit", "prose_quality", "overall"):
        if dim not in counts:
            continue
        c = counts[dim]
        with_pct = 100 * c.get("with", 0) / n if n else 0
        without_pct = 100 * c.get("without", 0) / n if n else 0
        tie_pct = 100 * c.get("tie", 0) / n if n else 0
        print(
            f"  {dim:<14}  with={c.get('with', 0)} ({with_pct:.0f}%)  "
            f"without={c.get('without', 0)} ({without_pct:.0f}%)  "
            f"tie={c.get('tie', 0)} ({tie_pct:.0f}%)"
        )


async def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", type=str, help="default job_id when --cases is omitted")
    parser.add_argument(
        "--cases",
        type=str,
        help="path to JSON file with [{job_id, instruction, section_path?}, ...]",
    )
    parser.add_argument(
        "--out", type=str, default=None, help="optional path to write the raw results JSON"
    )
    args = parser.parse_args()

    cases: list[Case] = []
    if args.cases:
        raw = json.loads(Path(args.cases).read_text())
        for c in raw:
            cases.append(
                Case(
                    job_id=uuid.UUID(c["job_id"]),
                    instruction=c["instruction"],
                    section_path=c.get("section_path"),
                )
            )
    else:
        if not args.job_id:
            print("error: --job-id or --cases required", file=sys.stderr)
            return 2
        default = [
            ("summary", "Make the summary punchier and more directly aligned with the role."),
            (
                "selected_research.0.description",
                "Tighten this to 75 words and lead with what's different now because of the work.",
            ),
            (None, "Score my current resume's fit for this role out of 100 and explain the deduction."),
        ]
        jid = uuid.UUID(args.job_id)
        for section, instr in default:
            cases.append(Case(job_id=jid, instruction=instr, section_path=section))

    print(f"Running {len(cases)} case(s)…")
    results: list[PairResult] = []
    for i, case in enumerate(cases, 1):
        print(f"  [{i}/{len(cases)}] {case.section_path or '(no section)'}: {case.instruction[:60]}…")
        try:
            r = await run_case(case)
            results.append(r)
            print(f"    -> overall: {r.judge.get('overall', {}).get('winner', '?')}")
        except Exception as e:
            print(f"    -> FAILED: {e}")

    counts = _tally(results)
    _print_tally(counts, len(results))

    if args.out:
        Path(args.out).write_text(
            json.dumps(
                [
                    {
                        "instruction": r.case.instruction,
                        "section_path": r.case.section_path,
                        "with_exemplars": r.with_exemplars,
                        "without_exemplars": r.without_exemplars,
                        "judge": r.judge,
                    }
                    for r in results
                ],
                indent=2,
            )
        )
        print(f"\nWrote raw results to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
