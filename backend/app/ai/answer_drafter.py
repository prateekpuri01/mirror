"""AI-powered drafting of short-answer application responses."""

import json
import logging

from app.ai.client import RESUME_MODEL, get_openai_client
from app.ai.prompts import format_job_for_scoring
from app.ai.resume_prompts import build_full_profile_for_resume

logger = logging.getLogger(__name__)

ANSWER_SYSTEM = """\
You are an expert application strategist helping a candidate draft compelling short-answer \
responses for job applications.

## CRITICAL RULES
- NEVER fabricate accomplishments, metrics, publications, or skills not in the provided profile.
- Every claim must be grounded in the candidate's actual experience from their profile.
- Write in first person as the candidate.
- Be specific and quantitative where possible — use real numbers from the profile.
- Mirror the job posting's language and priorities.
- Be concise but substantive — aim for 2-4 paragraphs unless the field has a character limit.
- If a character limit is specified, stay well within it.
- Sound authentic and genuine, not like a form letter. Avoid corporate buzzwords.
- Connect the candidate's specific experience to WHY this role/company excites them.

## Tone
- Confident but not arrogant
- Specific rather than generic
- Show genuine intellectual curiosity and enthusiasm
- Professional but with personality

## Iterative Editing
When an editing history is provided, ALL previous instructions remain in effect as cumulative \
constraints. The latest instruction adds to (does not replace) earlier ones. If the user manually \
edited the draft, preserve the spirit of their manual changes unless the new instruction \
explicitly contradicts them."""

DRAFT_ALL_USER = """\
Draft responses for ALL of the short-answer questions below.

## Job Posting
{job_text}

## Candidate Profile & Accomplishments
{profile_text}

## Questions to Answer
{questions_text}

Respond with ONLY a JSON object mapping each question's NUMBER (as a string) \
to its drafted response. Use the leading number from each question above. \
Include every numbered question. Example:
{{
  "1": "I'm drawn to...",
  "2": "At RAND, I led..."
}}

No markdown fences. Just the JSON object."""

DRAFT_SINGLE_USER = """\
Draft a response for this specific application question.

## Job Posting
{job_text}

## Candidate Profile & Accomplishments
{profile_text}

## Question
**{label}**
{field_details}

{instructions_section}
{history_section}
{existing_section}

Write ONLY the response text — no JSON, no label, no extra formatting. \
Just the answer as the candidate would type it into the application form."""


def _format_research(research: dict | None) -> str:
    """Format company research as context for answer drafting."""
    if not research:
        return ""
    parts = ["## Company & Team Research"]
    summary = research.get("company_summary", "")
    if summary:
        parts.append(summary[:300])
    tf = research.get("team_function")
    if tf and tf not in ("unknown", "General technical role"):
        parts.append(f"Team focus: {tf}")
    fa = research.get("framing_angles", [])
    if fa:
        parts.append("Key themes to weave into your answers:")
        for angle in fa[:3]:
            parts.append(f"- {angle}")
    vs = research.get("valued_skills", [])
    if vs:
        parts.append(f"Skills they value: {', '.join(vs[:6])}")
    parts.append(
        "\nUse these insights to make your answers specific to this company/team. "
        "Reference concrete things they build or care about — but sound natural, "
        "not like you're reciting their About page."
    )
    return "\n".join(parts)


def _format_questions(fields: list[dict]) -> str:
    """Format short answer fields into numbered questions for the prompt."""
    parts = []
    for i, f in enumerate(fields, 1):
        line = f"{i}. **{f['label']}**"
        if f.get("max_length"):
            line += f" (max {f['max_length']} characters)"
        if not f.get("required", True):
            line += " [Optional]"
        if f.get("description"):
            line += f"\n   Helper text: {f['description']}"
        if f.get("draft_response"):
            line += f"\n   Current draft: {f['draft_response']}"
        parts.append(line)
    return "\n".join(parts)


def _format_history(history: list[dict]) -> str:
    """Format editing history for the prompt."""
    labels = {"ai_draft": "AI Draft", "instruction": "Instruction", "manual_edit": "Manual Edit"}
    lines = []
    for i, entry in enumerate(history, 1):
        label = labels.get(entry["type"], entry["type"])
        content = entry["content"]
        # Truncate long drafts in history to keep prompt lean
        if entry["type"] in ("ai_draft", "manual_edit") and len(content) > 500:
            content = content[:500] + "..."
        lines.append(f"{i}. [{label}] {content}")
    return "\n".join(lines)


def _prune_history(history: list[dict], max_entries: int = 10) -> list[dict]:
    """Cap history length, preserving instruction entries."""
    if len(history) <= max_entries:
        return history
    # Always keep instruction entries (they carry hard constraints)
    instructions = [e for e in history if e["type"] == "instruction"]
    others = [e for e in history if e["type"] != "instruction"]
    # Keep the most recent non-instruction entries to fill remaining slots
    budget = max_entries - len(instructions)
    if budget < 1:
        # More than max_entries instructions — just keep the most recent entries
        return history[-max_entries:]
    return instructions + others[-budget:]


def _format_field_details(field: dict) -> str:
    """Format a single field's metadata for the prompt."""
    parts = []
    if field.get("max_length"):
        parts.append(f"Character limit: {field['max_length']}")
    if not field.get("required", True):
        parts.append("This field is optional")
    if field.get("description"):
        parts.append(f"Helper text: {field['description']}")
    return "\n".join(parts) if parts else ""


async def draft_all_answers(
    job,
    profile_data: dict,
    short_answer_fields: list[dict],
    company_notes: str | None = None,
    company_research: dict | None = None,
    memory_text: str = "",
) -> dict[str, str]:
    """Draft responses for all short-answer fields. Returns {label: response}."""
    client = get_openai_client()

    job_text = format_job_for_scoring(job, company_notes=company_notes)
    profile_text = build_full_profile_for_resume(profile_data)
    questions_text = _format_questions(short_answer_fields)

    research_section = _format_research(company_research)
    user_content = DRAFT_ALL_USER.format(
        job_text=job_text,
        profile_text=profile_text,
        questions_text=questions_text,
    )
    if research_section:
        user_content += f"\n\n{research_section}"

    system_content = ANSWER_SYSTEM
    if memory_text:
        system_content += f"\n\n{memory_text}"

    response = await client.chat.completions.create(
        model=RESUME_MODEL,
        messages=[
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ],
        max_completion_tokens=8192,
    )

    text = (response.choices[0].message.content or "").strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    raw = json.loads(text)
    if not isinstance(raw, dict):
        logger.warning("draft_all_answers: LLM returned non-dict %r", type(raw))
        return {}

    # Map LLM keys → field labels. Prefer index-keyed responses ("1", "2", ...);
    # fall back to exact-label match for prompts that ignored the new format,
    # then to a normalized substring match (handles em-dash/asterisk drift
    # that previously made every field silently fail to update).
    def _normalize(s: str) -> str:
        return "".join(ch.lower() for ch in (s or "") if ch.isalnum())

    label_by_norm = {_normalize(f["label"]): f["label"] for f in short_answer_fields}
    out: dict[str, str] = {}
    unmatched: list[str] = []

    for k, v in raw.items():
        if not isinstance(v, str):
            continue
        # 1. Index match: "1" → fields[0]
        if isinstance(k, str) and k.strip().isdigit():
            idx = int(k.strip()) - 1
            if 0 <= idx < len(short_answer_fields):
                out[short_answer_fields[idx]["label"]] = v
                continue
        # 2. Exact-label match (legacy / belt-and-suspenders)
        if any(f["label"] == k for f in short_answer_fields):
            out[k] = v
            continue
        # 3. Normalized fuzzy match
        norm = _normalize(k)
        if norm in label_by_norm:
            out[label_by_norm[norm]] = v
            continue
        unmatched.append(k[:80])

    if unmatched:
        logger.warning(
            "draft_all_answers: %d response key(s) couldn't be matched to a field: %s",
            len(unmatched),
            unmatched,
        )
    if not out:
        logger.warning(
            "draft_all_answers: 0 of %d fields matched any LLM key. Raw keys: %s",
            len(short_answer_fields),
            list(raw.keys())[:5],
        )

    return out


async def draft_single_answer(
    job,
    profile_data: dict,
    field: dict,
    instructions: str | None = None,
    company_notes: str | None = None,
    draft_history: list[dict] | None = None,
    company_research: dict | None = None,
    memory_text: str = "",
) -> str:
    """Draft a response for a single short-answer field. Returns response text."""
    client = get_openai_client()

    job_text = format_job_for_scoring(job, company_notes=company_notes)
    profile_text = build_full_profile_for_resume(profile_data)
    field_details = _format_field_details(field)

    instructions_section = ""
    if instructions:
        instructions_section = f"## Special Instructions from Candidate\n{instructions}\n"

    history_section = ""
    if draft_history:
        history_section = (
            "## Editing History (honor ALL constraints from previous instructions)\n"
            + _format_history(draft_history)
            + "\n"
        )

    existing_section = ""
    if field.get("draft_response"):
        existing_section = (
            f"## Existing Draft (revise based on instructions above)\n{field['draft_response']}\n"
        )

    user_content = DRAFT_SINGLE_USER.format(
        job_text=job_text,
        profile_text=profile_text,
        label=field["label"],
        field_details=field_details,
        instructions_section=instructions_section,
        history_section=history_section,
        existing_section=existing_section,
    )
    research_section = _format_research(company_research)
    if research_section:
        user_content += f"\n\n{research_section}"

    system_content = ANSWER_SYSTEM
    if memory_text:
        system_content += f"\n\n{memory_text}"

    response = await client.chat.completions.create(
        model=RESUME_MODEL,
        messages=[
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ],
        max_completion_tokens=4096,
    )

    return (response.choices[0].message.content or "").strip()
