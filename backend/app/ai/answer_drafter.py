"""AI-powered drafting of short-answer application responses."""

import json
import logging

from app.ai.client import get_openai_client, RESUME_MODEL
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
- Professional but with personality"""

DRAFT_ALL_USER = """\
Draft responses for ALL of the short-answer questions below.

## Job Posting
{job_text}

## Candidate Profile & Accomplishments
{profile_text}

## Questions to Answer
{questions_text}

Respond with ONLY a JSON object mapping each question label to its drafted response. \
Use the exact label text as keys. Example:
{{
  "Why do you want to work here?": "I'm drawn to...",
  "Tell us about a project you're proud of": "At RAND, I led..."
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
{existing_section}

Write ONLY the response text — no JSON, no label, no extra formatting. \
Just the answer as the candidate would type it into the application form."""


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
) -> dict[str, str]:
    """Draft responses for all short-answer fields. Returns {label: response}."""
    client = get_openai_client()

    job_text = format_job_for_scoring(job, company_notes=company_notes)
    profile_text = build_full_profile_for_resume(profile_data)
    questions_text = _format_questions(short_answer_fields)

    user_content = DRAFT_ALL_USER.format(
        job_text=job_text,
        profile_text=profile_text,
        questions_text=questions_text,
    )

    response = await client.chat.completions.create(
        model=RESUME_MODEL,
        messages=[
            {"role": "system", "content": ANSWER_SYSTEM},
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

    return json.loads(text)


async def draft_single_answer(
    job,
    profile_data: dict,
    field: dict,
    instructions: str | None = None,
    company_notes: str | None = None,
) -> str:
    """Draft a response for a single short-answer field. Returns response text."""
    client = get_openai_client()

    job_text = format_job_for_scoring(job, company_notes=company_notes)
    profile_text = build_full_profile_for_resume(profile_data)
    field_details = _format_field_details(field)

    instructions_section = ""
    if instructions:
        instructions_section = f"## Special Instructions from Candidate\n{instructions}\n"

    existing_section = ""
    if field.get("draft_response"):
        existing_section = (
            f"## Existing Draft (revise based on instructions above)\n"
            f"{field['draft_response']}\n"
        )

    user_content = DRAFT_SINGLE_USER.format(
        job_text=job_text,
        profile_text=profile_text,
        label=field["label"],
        field_details=field_details,
        instructions_section=instructions_section,
        existing_section=existing_section,
    )

    response = await client.chat.completions.create(
        model=RESUME_MODEL,
        messages=[
            {"role": "system", "content": ANSWER_SYSTEM},
            {"role": "user", "content": user_content},
        ],
        max_completion_tokens=4096,
    )

    return (response.choices[0].message.content or "").strip()
