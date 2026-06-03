"""LangGraph decision graph for agentic resume editing.

Runs as pure Python inside the FastAPI process — no separate server needed.
"""

import copy
import json
import logging
import re
from typing import Any

from langgraph.graph import END, StateGraph

from app.ai.agent_state import AgentState
from app.ai.client import EXTRACTION_MODEL, RESUME_MODEL, get_openai_client
from app.ai.resume_prompts import BRAINSTORM_SYSTEM, RESUME_REVISION_SYSTEM
from app.services.document_service import _get_nested, _set_nested

logger = logging.getLogger(__name__)


def _format_research_context(state: dict) -> str:
    """Format company research as a compact context block for the agent."""
    research = state.get("company_research")
    if not research:
        return ""
    from app.ai.company_research import format_research_for_chat

    text = format_research_for_chat(research)
    return f"\n## Company Research\n{text}\n" if text else ""


# ---------------------------------------------------------------------------
# Section path metadata — built dynamically from resume JSON
# ---------------------------------------------------------------------------

# Base sections that always exist
_BASE_SECTION_LABELS: dict[str, str] = {
    "tagline": "tagline",
    "summary": "summary",
    "selected_research": "selected_research",
    "publications": "publications",
    "technical_skills": "technical_skills",
    "technical_skills.ai_systems": "technical_skills.ai_systems",
    "technical_skills.data_science": "technical_skills.data_science",
    "technical_skills.engineering": "technical_skills.engineering",
    "awards": "awards",
}


def build_section_labels(resume_json: dict) -> dict[str, str]:
    """Build section labels dynamically from the resume JSON.

    Adds experience.{key} and experience.{key}.bullets for each employer
    found in the experience block.
    """
    labels = dict(_BASE_SECTION_LABELS)
    experience = resume_json.get("experience", {})
    for emp_key in experience:
        labels[f"experience.{emp_key}"] = f"experience.{emp_key}"
        labels[f"experience.{emp_key}.bullets"] = f"experience.{emp_key}.bullets"
    return labels


async def _call_openai(
    system: str, user_content: str, max_tokens: int = 2000, temperature: float = 0.3
) -> str:
    """Call OpenAI and return the raw text response."""
    client = get_openai_client()
    response = await client.chat.completions.create(
        model=RESUME_MODEL,
        max_completion_tokens=max_tokens,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
    )
    return response.choices[0].message.content.strip()


async def _call_openai_json(system: str, user_content: str, max_tokens: int = 2000) -> Any:
    """Call OpenAI and parse JSON from the response."""
    text = await _call_openai(system, user_content, max_tokens=max_tokens)
    # Strip markdown fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    return json.loads(text)


# ---------------------------------------------------------------------------
# Graph nodes
# ---------------------------------------------------------------------------


async def check_section_context(state: AgentState) -> dict:
    """Annotate the state with the clicked section value (if any).

    Previously this short-circuited the router and forced make_edit. Under
    the unified chat model the click is just a *signal* — the classifier
    decides whether the user wants a scoped_edit (card), quick_edit
    (commit), broad_rewrite, or pure brainstorm advisory.
    """
    if state["section_context"]:
        try:
            value = _get_nested(state["resume_json"], state["section_context"])
        except (KeyError, IndexError, TypeError):
            value = None
        return {
            "target_section_path": state["section_context"],
            "target_section_value": value,
        }
    return {}


# Live intents under the unified chat model. Older intents (`make_edit`,
# `ask_question`, `multiple_changes`) are still accepted by the override
# path for backward compatibility and remapped — see ``route_intent``.
_VALID_INTENTS: frozenset[str] = frozenset(
    {
        "scoped_edit",
        "quick_edit",
        "broad_rewrite",
        "brainstorm",
        "remember_preference",
        "proofread",
    }
)

# Legacy intents we still accept on inbound (UI may send them) and remap.
_INTENT_ALIASES: dict[str, str] = {
    "make_edit": "scoped_edit",
    "ask_question": "brainstorm",
    "multiple_changes": "brainstorm",
}


async def route_intent(state: AgentState) -> dict:
    """Classify the user's intent.

    Short-circuits when ``state['intent']`` is already a valid value (or a
    legacy alias) — used by deterministic UI affordances:
      - "proofread" button       -> proofread (read-only)
      - "quick_edit" toggle      -> commit edit directly, bypass card

    Otherwise the LLM classifier picks one of: scoped_edit, broad_rewrite,
    brainstorm, remember_preference. (Proofread and quick_edit are
    button-only — the classifier never picks them.)

    When section_context is set but the message is not a clear directive,
    we still bias toward scoped_edit so the click lands as a card.
    """
    preset = state.get("intent")
    if preset:
        canonical = _INTENT_ALIASES.get(preset, preset)
        if canonical in _VALID_INTENTS:
            logger.info("route_intent: using preset intent %r -> %r", preset, canonical)
            return {"intent": canonical}

    has_section = bool(state.get("target_section_path") or state.get("section_context"))
    section_note = (
        "The user has a section anchored. Treat this as a FOCUS HINT, not a "
        "scope constraint. If the message is clearly an edit directive on "
        "that section, pick scoped_edit. If the message is a question, "
        "advisory, multi-section, or whole-resume, pick the right intent "
        "for the message and ignore the anchor.\n\n"
        if has_section
        else ""
    )

    system = """You classify resume chat messages. Output ONLY one of these exact words:

- scoped_edit: user wants a specific change to ONE section (e.g., "rewrite \
my RAND bullets to focus on infra", "change the tagline to mention evals", \
"tighten this", "make this punchier", "less corporate", "shorter"). Pick \
this for clear edit directives that target one section.

- broad_rewrite: user wants a change affecting the WHOLE resume \
(e.g., "rewrite the resume to focus on evals", "redo this targeting the \
Distyl posting", "make the whole thing more conversational", "tighten up \
everything"). Whole-resume scope.

- brainstorm: pick this for ANY of:
  - questions / advice / opinions / scoring ("is this on target?", "what's \
the weakest part?", "score this 1-100")
  - multi-section asks ("change the others", "top 3 things to fix", \
"redo all the bullets", "fix the experience section AND the summary")
  - variants ("give me 3 punchier versions")
  - outreach drafts (LinkedIn, recruiter, cover letter)
  - "what would you do" / "what should I lead with"
  The brainstorm path can still propose cards — one per concrete edit it \
sees worth proposing — so don't avoid brainstorm thinking "the user wants \
edits committed." Edits are the COMMIT step. brainstorm is the THINKING \
step that produces cards.

- remember_preference: user is telling you a writing preference to keep \
in mind for all future generations (e.g., "always keep bullets under 15 \
words", "never use the word architected", "remember: I prefer casual tone").

## How to break ties
- "Change the others" / "do the rest" / "now do X too" -> brainstorm \
(multi-section), even if a section is anchored.
- "What about X?" / "is X right?" / "should I X?" -> brainstorm.
- Short imperative on a clicked section ("tighter", "more punchy", \
"less corporate") -> scoped_edit.
- Plural "the others" / "them" / "these" -> brainstorm, ALWAYS.

Output only the classification word."""

    history_text = ""
    for msg in state["chat_history"][-4:]:
        history_text += f"{msg['role']}: {msg['content']}\n"

    section_hint = ""
    if has_section:
        section_hint = (
            f"\nClicked section: {state.get('target_section_path') or state.get('section_context')}\n"
        )

    user_content = f"""{section_note}Chat history:
{history_text}{section_hint}
Latest message: {state["user_message"]}

Classification:"""

    result = await _call_openai(system, user_content, max_tokens=10, temperature=0)
    intent = result.strip().lower().strip('"').strip("'")
    intent = _INTENT_ALIASES.get(intent, intent)
    if intent not in _VALID_INTENTS:
        # Sensible defaults: scoped_edit when a section is in play, brainstorm
        # otherwise. Avoids dropping the user into a wrong terminal node.
        intent = "scoped_edit" if has_section else "brainstorm"

    logger.info("route_intent: classified as %r (has_section=%s)", intent, has_section)
    return {"intent": intent}


async def identify_section(state: AgentState) -> dict:
    """Identify which section the user wants to edit from their message."""
    section_labels = build_section_labels(state["resume_json"])
    valid_sections = list(section_labels.keys())
    sections_list = "\n".join(f"- {s}" for s in valid_sections)

    system = f"""You identify which resume section a user wants to edit.

Available sections:
{sections_list}

Output ONLY the section path from the list above. If unsure, pick the closest match."""

    # Provide resume structure overview
    resume_keys = list(state["resume_json"].keys())
    user_content = f"""Resume sections present: {", ".join(resume_keys)}

User message: {state["user_message"]}

Section path:"""

    result = await _call_openai(system, user_content, max_tokens=20, temperature=0)
    path = result.strip().lower().strip('"').strip("'")

    # Validate the path
    if path not in section_labels:
        # Try partial matching
        for valid_path in section_labels:
            if path in valid_path or valid_path in path:
                path = valid_path
                break
        else:
            # Fallback — try to find it in resume JSON directly
            path = path if path in state["resume_json"] else "summary"

    try:
        value = _get_nested(state["resume_json"], path)
    except (KeyError, IndexError, TypeError):
        value = None

    return {"target_section_path": path, "target_section_value": value}


_EDIT_SECTION_SYSTEM_BASE = """\
You edit ONE resume section per turn. Output the updated section value as JSON \
matching the input shape (string→string, array→array, object→object).

## Hard rules
- Use only facts from the provided accomplishment data. NEVER fabricate metrics, \
skills, or outcomes.
- Apply the instruction precisely. Change only what was asked.
- Apply chat-history feedback. If the user already corrected a mistake on this \
section, do NOT repeat it.

## Voice (match the section type)
- summary: confident pitch, 50–80 words, makes an argument. No pronouns. No metrics. \
No "Research scientist with N years…" openings.
- selected_research description: 2–3 sentences, 75–100 words. Lead with an action \
verb. State what's different now because of this work. Don't repeat the experience \
bullet for the same accomplishment.
- experience bullet: 1–2 lines, punchy, outcome-led. Lead with what changed. \
Include a metric only if it self-explains to someone outside the field.
- skills bucket: comma-separated, job-relevant first, no skill in more than one bucket.
- tagline / awards: concise, factual, " · " separators.

## Banned
"leveraged", "utilized", "architected", "spearheaded", "responsible for", \
"passionate about", "proven track record". Use direct verbs (built, shipped, \
designed, replaced, reduced, discovered).

## Sentence form
- One idea per sentence. Active voice, subject–verb–object.
- If a human wouldn't say it out loud, rewrite it.

## Use the instruction's intent, not the instruction's phrasing

When the user says "rephrase to highlight X" or "give more details on
Y", that tells you WHAT to emphasize — not HOW to phrase the answer.
Don't use the user's sentence shape as your output's skeleton.

## Match the user's voice from past edits

If a "How you've edited similar passages before" block is included in
the user content, treat those exemplars as the authoritative voice
guide. The user's actual past edits beat any generic rule.
"""


_ASSISTANT_EDIT_PREFIX = "Updated **"  # see _format_edit_response


def _format_edit_response(path: str, new_value: Any) -> str:
    """Render the assistant's chat reply so the actual rewritten text lives in
    history. Without this, follow-up turns can't see what was just produced."""
    if isinstance(new_value, str):
        body = new_value.strip()
    elif isinstance(new_value, list):
        # Bullet array: render as a quoted markdown list
        lines = []
        for item in new_value:
            if isinstance(item, dict):
                lines.append(f"- {item.get('text', '').strip()}")
            elif isinstance(item, str):
                lines.append(f"- {item.strip()}")
        body = "\n".join(lines)
    elif isinstance(new_value, dict):
        body = json.dumps(new_value, indent=2)
    else:
        body = str(new_value)
    quoted = "\n".join(f"> {line}" if line else ">" for line in body.splitlines())
    return f"{_ASSISTANT_EDIT_PREFIX}{path}** to:\n\n{quoted}"


def _focused_profile_for_edit(path: str, resume_json: dict, profile_data: dict) -> str:
    """Return only the profile content the LLM needs to edit this section.

    Replaces the full ~5–7k-token ``build_full_profile_for_resume`` dump with a
    targeted slice. Big signal-to-noise win on focused edits.
    """
    accomplishments = (profile_data.get("complete_profile") or {}).get("accomplishments") or []
    by_id = {a.get("id"): a for a in accomplishments if a.get("id")}

    relevant_ids: list[str] = []
    sr_match = re.match(r"selected_research\.(\d+)", path)
    bullet_idx_match = re.match(r"experience\.([^.]+)\.bullets\.(\d+)", path)
    bullets_arr_match = re.match(r"experience\.([^.]+)\.bullets$", path)

    if sr_match:
        idx = int(sr_match.group(1))
        entries = resume_json.get("selected_research") or []
        if 0 <= idx < len(entries):
            aid = entries[idx].get("accomplishment_id")
            if aid:
                relevant_ids.append(aid)
    elif bullet_idx_match:
        emp_key = bullet_idx_match.group(1)
        bidx = int(bullet_idx_match.group(2))
        block = (resume_json.get("experience") or {}).get(emp_key) or {}
        bullets = block.get("bullets") or []
        if 0 <= bidx < len(bullets) and isinstance(bullets[bidx], dict):
            relevant_ids.extend(bullets[bidx].get("accomplishment_ids") or [])
    elif bullets_arr_match:
        emp_key = bullets_arr_match.group(1)
        block = (resume_json.get("experience") or {}).get(emp_key) or {}
        for b in block.get("bullets") or []:
            if isinstance(b, dict):
                relevant_ids.extend(b.get("accomplishment_ids") or [])

    if relevant_ids:
        seen: set[str] = set()
        parts = ["## Relevant Accomplishments (the only source for this edit)"]
        for aid in relevant_ids:
            if aid in seen or aid not in by_id:
                continue
            seen.add(aid)
            parts.append(_format_accomplishment_compact(by_id[aid]))
        if len(parts) > 1:
            # Append one-liners for the rest of the catalog so the model has a
            # shortlist to pull from if the user's instruction implicitly asks
            # for it ("rewrite to focus on web extraction quality" should be
            # able to draw from CAS scraping even if the current bullet is
            # the FINRA one). The compact slice above is still the *primary*
            # source for the edit.
            other = _other_accomplishments_oneliners(by_id, seen)
            if other:
                parts.append(other)
            return "\n\n".join(parts)

    if path in ("summary", "tagline"):
        return _format_compact_profile_for_summary(profile_data, resume_json)

    if path.startswith("technical_skills"):
        return _format_skills_whitelist(profile_data)

    return _format_compact_profile_for_summary(profile_data, resume_json)


def _other_accomplishments_oneliners(
    by_id: dict[str, dict], exclude_ids: set[str]
) -> str:
    """One-line summary of every accomplishment NOT in `exclude_ids`.

    Format: `[title] one-line impact (id=...)`. Kept tight so the model can
    scan it when the user's edit instruction implies pulling from elsewhere.
    """
    lines: list[str] = []
    for aid, a in by_id.items():
        if aid in exclude_ids:
            continue
        title = a.get("title") or "Untitled"
        impact = (a.get("impact_summary") or "").strip().replace("\n", " ")
        if len(impact) > 180:
            impact = impact[:177] + "…"
        lines.append(f"- [{title}] {impact} (id={aid})")
    if not lines:
        return ""
    return (
        "## Other available accomplishments (one-liners — pull from here only "
        "if the instruction asks for it)\n" + "\n".join(lines)
    )


def _format_accomplishment_compact(a: dict) -> str:
    parts = [f"### {a.get('title', 'Untitled')}"]
    parts.append(f"  ID: {a.get('id', '?')}")
    if a.get("employer"):
        parts.append(f"  Employer: {a['employer']}")
    if a.get("impact_summary"):
        parts.append(f"  Impact: {a['impact_summary'].strip()}")
    if a.get("quantitative_specifics"):
        parts.append(f"  Metrics: {'; '.join(a['quantitative_specifics'])}")
    if a.get("so_what"):
        parts.append(f"  So what: {a['so_what'].strip()}")
    if a.get("hands_on_work"):
        parts.append(f"  Hands-on work: {a['hands_on_work'].strip()}")
    if a.get("skills_demonstrated"):
        parts.append(f"  Skills: {', '.join(a['skills_demonstrated'])}")
    return "\n".join(parts)


def _format_compact_profile_for_summary(profile_data: dict, resume_json: dict) -> str:
    lines: list[str] = []
    plan = resume_json.get("_strategic_plan") or {}
    if plan.get("core_argument"):
        lines.append(f"## Core argument\n{plan['core_argument']}")
    if plan.get("tone"):
        lines.append(f"## Tone\n{plan['tone']}")

    accomplishments = (profile_data.get("complete_profile") or {}).get("accomplishments") or []
    by_id = {a.get("id"): a for a in accomplishments if a.get("id")}
    featured_ids: list[str] = []
    for r in resume_json.get("selected_research") or []:
        aid = r.get("accomplishment_id")
        if aid and aid not in featured_ids:
            featured_ids.append(aid)
    for emp_data in (resume_json.get("experience") or {}).values():
        for b in emp_data.get("bullets") or []:
            if isinstance(b, dict):
                for aid in b.get("accomplishment_ids") or []:
                    if aid not in featured_ids:
                        featured_ids.append(aid)

    if featured_ids:
        lines.append("## Featured accomplishments in this resume")
        for aid in featured_ids:
            a = by_id.get(aid)
            if not a:
                continue
            impact = (a.get("impact_summary") or "").strip().replace("\n", " ")
            lines.append(f"- [{a.get('title', '?')}] {impact[:240]}")

    return "\n\n".join(lines) if lines else ""


def _format_skills_whitelist(profile_data: dict) -> str:
    skills = profile_data.get("skills") or {}
    whitelist: list[str] = []
    for cat in ("technical", "communication", "tools"):
        v = skills.get(cat)
        if isinstance(v, list):
            whitelist.extend(v)
        elif isinstance(v, str):
            whitelist.append(v)
    return f"## Skills whitelist (only use these)\n{', '.join(whitelist)}" if whitelist else ""


async def _grounding_for_edit_path(
    session_factory: Any,
    path: str,
    resume_json: dict,
    profile_data: dict,
) -> str:
    """Fetch + format content_memory grounding for the entity at this path."""
    if not session_factory:
        return ""
    try:
        from app.ai.content_memory_grounding import format_grounding_block
        from app.ai.content_memory_paths import path_to_entity
        from app.services import content_memory_service

        descriptor = path_to_entity(path, resume_json)
        if descriptor is None:
            return ""
        entity_type = descriptor["entity_type"]
        entity_key = descriptor["entity_key"]

        async with session_factory() as session:
            grouped = await content_memory_service.fetch_grounding(
                session,
                entity_type=entity_type,
                entity_keys=[entity_key],
            )
        rows = grouped.get(entity_key, [])
        if not rows:
            return ""
        return format_grounding_block(rows, profile_data=profile_data)
    except Exception:
        logger.exception("grounding fetch failed for path=%s", path)
        return ""


def _other_sections_excerpt(resume_json: dict, path: str) -> str:
    """Render the rest of the resume as anti-redundancy context.

    Per-item excerpt budget: 250 chars when there are 5+ items, otherwise full
    text. The previous 120-char cap was too short for the LLM to actually
    detect overlap with research descriptions.
    """
    rj = resume_json or {}
    if not rj:
        return ""
    items: list[tuple[str, str]] = []
    if rj.get("summary") and not path.startswith("summary"):
        items.append(("Summary", str(rj["summary"])))
    for ri, r in enumerate(rj.get("selected_research") or []):
        rp = f"selected_research.{ri}"
        if not path.startswith(rp):
            items.append(
                (
                    f"Research [{r.get('category_label', '')}]",
                    str(r.get("description", "")),
                )
            )
    for emp, edata in (rj.get("experience") or {}).items():
        for bi, b in enumerate(edata.get("bullets") or []):
            bp = f"experience.{emp}.bullets.{bi}"
            if bp != path and not path.startswith(f"experience.{emp}.bullets"):
                bt = b["text"] if isinstance(b, dict) else b
                items.append((f"Bullet ({emp})", str(bt)))
    if not items:
        return ""
    char_cap = 250 if len(items) >= 5 else 1000
    lines = ["## Rest of Resume (avoid repeating this content)"]
    for label, text in items:
        snippet = text[:char_cap]
        suffix = "…" if len(text) > char_cap else ""
        lines.append(f"- {label}: {snippet}{suffix}")
    return "\n".join(lines) + "\n"


def _previous_attempts_block(chat_history: list[dict], path: str) -> str:
    """Surface prior assistant rewrites of THIS section in the current
    conversation so the LLM sees its own previous attempts alongside user
    feedback. Without this, the LLM only sees the *current* section value
    (= last attempt) and the user's feedback as raw text — it can't tell
    which complaint targets which prior attempt."""
    attempts: list[tuple[str, str]] = []  # (assistant_msg, user_followup)
    last_attempt: str | None = None
    for msg in chat_history:
        role = msg.get("role")
        content = (msg.get("content") or "").strip()
        if role == "assistant" and content.startswith(f"{_ASSISTANT_EDIT_PREFIX}{path}**"):
            last_attempt = content
        elif role == "user" and last_attempt is not None:
            attempts.append((last_attempt, content))
            last_attempt = None
    if not attempts:
        return ""
    # Show up to last 3 attempt+followup pairs
    recent = attempts[-3:]
    lines = [
        "## Previous attempts on this section (with the user's reaction)",
        "Read these carefully. The user's feedback below tells you what NOT to repeat.",
    ]
    for i, (att, follow) in enumerate(recent, 1):
        # Strip the "Updated **path** to:" prefix to keep it tight
        body = att.split("\n\n", 1)[1] if "\n\n" in att else att
        lines.append(f"\n### Attempt {i}")
        lines.append(body)
        lines.append(f"User reaction: {follow}")
    return "\n".join(lines) + "\n"


def _trimmed_chat_history(chat_history: list[dict], path: str, char_budget: int = 1500) -> str:
    """Render the conversation history (excluding the current message) but cap
    by total characters so a long rewrite turn doesn't blow the prompt budget.
    Filters out trivial '/proofread' turns and section-edits we already render
    via _previous_attempts_block."""
    if not chat_history:
        return ""
    past = chat_history[:-1]
    filtered: list[str] = []
    for msg in past:
        role = msg.get("role")
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        # The "previous attempts" block handles same-path edits — skip here to
        # avoid duplication.
        if role == "assistant" and content.startswith(f"{_ASSISTANT_EDIT_PREFIX}{path}**"):
            continue
        label = "You" if role == "assistant" else "User"
        filtered.append(f"{label}: {content}")
    if not filtered:
        return ""
    # Keep the most recent messages until we hit the char budget
    out: list[str] = []
    used = 0
    for line in reversed(filtered):
        if used + len(line) + 1 > char_budget:
            break
        out.insert(0, line)
        used += len(line) + 1
    if not out:
        return ""
    return "## Conversation history\n" + "\n".join(out) + "\n"


async def edit_section(state: AgentState) -> dict:
    """Edit a single section of the resume.

    Sends ONLY the slice of profile content needed to do this edit
    (accomplishment-focused), plus content_memory grounding for the entity,
    plus any prior attempts on the same section in this conversation.
    """
    path = state["target_section_path"]
    current_value = state["target_section_value"]

    if current_value is None:
        return {
            "response_text": f"I couldn't find the section '{path}' in the resume. Could you clarify which section you'd like to edit?",
            "updated_json": None,
            "updated_section_path": None,
        }

    system = _EDIT_SECTION_SYSTEM_BASE
    wm = state.get("writing_memory_text", "")
    if wm:
        system += f"\n\n{wm}"

    section_text = (
        current_value if isinstance(current_value, str) else json.dumps(current_value, indent=2)
    )

    # Section-specific constraint reminder (kept tight).
    section_constraints = ""
    if path == "summary" or path.startswith("summary"):
        section_constraints = (
            "\n## Section constraints\n"
            "50–80 words. Make an argument for why this person should get an interview. "
            "No metrics, no pronouns, no career-summary openings.\n"
        )
    elif "selected_research" in path and "description" in path:
        section_constraints = (
            "\n## Section constraints\n"
            "75–100 words, 2–3 sentences. Lead with an action verb. Don't restate the "
            "experience bullet for the same accomplishment.\n"
        )
    elif "bullets" in path:
        section_constraints = (
            "\n## Section constraints\n"
            "1–2 lines, punchy, outcome-led. One accomplishment per bullet.\n"
        )
    elif path.startswith("technical_skills"):
        section_constraints = (
            "\n## Section constraints\n"
            "Comma-separated, job-relevant first. No skill that already appears in another bucket.\n"
        )

    # Anchor: which accomplishment(s) does this section bind to?
    entry_context = ""
    sr_match = re.match(r"selected_research\.(\d+)\.", path)
    bullet_match = re.match(r"experience\.([^.]+)\.bullets\.(\d+)", path)
    if sr_match:
        idx = int(sr_match.group(1))
        entries = state["resume_json"].get("selected_research", [])
        if idx < len(entries):
            entry = entries[idx]
            entry_context = (
                f"\n## Anchor\n"
                f"This is the selected_research entry for accomplishment "
                f"`{entry.get('accomplishment_id', '?')}` ({entry.get('title', '?')}). "
                f"Edit ONLY this accomplishment's content.\n"
            )
    elif bullet_match:
        emp_key = bullet_match.group(1)
        bullet_idx = int(bullet_match.group(2))
        emp_data = state["resume_json"].get("experience", {}).get(emp_key, {})
        bullets = emp_data.get("bullets", [])
        if bullet_idx < len(bullets):
            b = bullets[bullet_idx]
            acc_ids = b.get("accomplishment_ids", []) if isinstance(b, dict) else []
            if acc_ids:
                entry_context = (
                    f"\n## Anchor\n"
                    f"This bullet binds to accomplishment(s): {', '.join(acc_ids)}. "
                    f"Edit ONLY content from these accomplishments.\n"
                )

    # Strategy / tone (compact)
    strategy_ctx = ""
    plan = state.get("strategic_plan") or {}
    if plan.get("core_argument"):
        strategy_ctx = f"\n## Resume strategy\nCore argument: {plan['core_argument']}\n"
        if plan.get("tone"):
            strategy_ctx += f"Tone: {plan['tone']}\n"

    # Focused profile slice
    focused_profile = _focused_profile_for_edit(
        path,
        state["resume_json"],
        state.get("_profile_data") or {},
    )
    # Fallback: if the focused slice is empty (no _profile_data passed), use
    # the legacy full profile_text — at least the LLM has *something* to draw on.
    if not focused_profile:
        focused_profile = state.get("profile_text", "")

    # Content memory grounding for this entity (huge for voice consistency)
    await _grounding_for_edit_path(
        state.get("_session_factory"),
        path,
        state["resume_json"],
        state.get("_profile_data") or {},
    )

    chat_history = state.get("chat_history") or []
    prior_attempts = _previous_attempts_block(chat_history, path)
    history_text = _trimmed_chat_history(chat_history, path)

    other_sections_ctx = _other_sections_excerpt(state["resume_json"], path)

    exemplars_block = await _fetch_exemplars_block(
        state, section_path=path, instruction=state.get("user_message") or ""
    )

    user_content_parts = [
        history_text,
        prior_attempts,
        f"## Current section: {path}\n\n```json\n{section_text}\n```\n",
        entry_context,
        section_constraints,
        strategy_ctx,
        f"\n## User instruction\n{state['user_message']}\n",
        other_sections_ctx,
        focused_profile + "\n" if focused_profile else "",
        f"\n## Job context\n{state.get('job_context', '')}\n",
        _format_research_context(state),
        exemplars_block,
        f'\nOutput ONLY the updated value for "{path}" as valid JSON. '
        "If the value is a string, output just the string in quotes. "
        "If it's an array or object, output the JSON structure.",
    ]
    user_content = "".join(p for p in user_content_parts if p)

    logger.info(
        "edit_section: path=%s prompt_chars=%d (system=%d, user=%d)",
        path,
        len(system) + len(user_content),
        len(system),
        len(user_content),
    )

    result_text = await _call_openai(system, user_content, max_tokens=1500, temperature=0.5)

    # Parse the result
    try:
        clean = result_text
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1]
            if clean.endswith("```"):
                clean = clean[:-3]
            clean = clean.strip()
        new_value = json.loads(clean)
    except json.JSONDecodeError:
        new_value = result_text.strip().strip('"')

    # Merge update into full resume JSON
    updated = copy.deepcopy(state["resume_json"])
    try:
        _set_nested(updated, path, new_value)
        return {
            "updated_json": updated,
            "updated_section_path": path,
            "response_text": _format_edit_response(path, new_value),
        }
    except (KeyError, IndexError, TypeError) as e:
        logger.warning("Failed to set section %s: %s", path, e)
        return {
            "updated_json": None,
            "updated_section_path": None,
            "response_text": f"I had trouble updating '{path}'. Could you try rephrasing?",
        }


# ---------------------------------------------------------------------------
# Brainstorm path — opinionated free-prose advisor with optional web search
# and action-card emission. See BRAINSTORM_SYSTEM in resume_prompts.py.
# ---------------------------------------------------------------------------

_WEB_SEARCH_ROUTER_SYSTEM = """\
You decide whether a brainstorm message about a job application needs \
LIVE WEB SEARCH to answer well.

Output ONLY "yes" or "no".

Say YES if the answer plausibly requires CURRENT-WORLD info the assistant \
wouldn't already have:
- who specifically to message at a company (team members, recruiters)
- recent posts, news, or product changes from a company
- current job posting language we'd want to mirror
- whether a specific person still works somewhere
- any "look it up" / "search for" / "find me" request

Say NO when the answer is purely about the user's own resume, profile, \
voice, or strategy — even if it references a company name. Examples:
- "is my third bullet on target for Anthropic?" -> no
- "punchier version of this summary" -> no
- "score this resume out of 100" -> no
- "rewrite my MUSE bullet" -> no

When in doubt say NO; web search costs latency and tokens."""


async def _should_use_web_search_llm(
    user_message: str, chat_history: list[dict] | None = None
) -> bool:
    """Tiny-model router replacing the keyword heuristic.

    Uses EXTRACTION_MODEL (cheap/fast). Conservative bias: returns False on
    any error, so a flaky router never silently blocks the brainstorm path.
    """
    msg = (user_message or "").strip()
    if not msg:
        return False
    # One-turn context — last 2 messages help disambiguate follow-ups like
    # "who else?" that depend on the prior message.
    recent = ""
    for m in (chat_history or [])[-2:]:
        content = (m.get("content") or "").strip()
        if content:
            recent += f"{m.get('role', '?')}: {content[:200]}\n"
    user_content = (
        (f"Recent context:\n{recent}\n" if recent else "")
        + f"Latest message: {msg}\n\nyes or no:"
    )
    try:
        client = get_openai_client()
        # gpt-5-mini and similar reasoning models (a) reject custom
        # temperature and (b) consume reasoning tokens before emitting
        # content. 128 leaves headroom for both reasoning and the
        # one-token yes/no answer.
        resp = await client.chat.completions.create(
            model=EXTRACTION_MODEL,
            max_completion_tokens=256,
            messages=[
                {"role": "system", "content": _WEB_SEARCH_ROUTER_SYSTEM},
                {"role": "user", "content": user_content},
            ],
        )
        answer = (resp.choices[0].message.content or "").strip().lower()
        decision = answer.startswith("y")
        logger.info("web-search router: %r -> %s (%r)", msg[:60], decision, answer[:30])
        return decision
    except Exception:
        logger.exception("web-search router LLM call failed; defaulting to no")
        return False


def _company_hint(state: dict) -> str:
    """Best-effort extraction of the company name from cached research or the
    job context's leading lines. Used to disambiguate search queries."""
    research = state.get("company_research") or {}
    if isinstance(research, dict):
        for key in ("company", "company_name", "name"):
            v = research.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    # Fall back to the first line of job_context that looks like "Company: X"
    job_text = state.get("job_context") or ""
    for line in job_text.splitlines()[:8]:
        line = line.strip()
        if line.lower().startswith("company:"):
            return line.split(":", 1)[1].strip()
    return ""


async def _fetch_web_search_context(state: dict) -> str:
    """Run one web search and format results as a block to inject into the
    brainstorm user content. Returns empty string on failure or no result.

    Uses services.web_search_llm.llm_web_search, which defaults to OpenAI's
    Responses API + `web_search` tool when settings.llm_provider is "openai".
    """
    try:
        from app.services.web_search_llm import llm_web_search
    except Exception:
        logger.exception("brainstorm: web_search_llm import failed")
        return ""

    query = state["user_message"]
    company = _company_hint(state)
    if company and company.lower() not in query.lower():
        query = f"{company}: {query}"

    try:
        result = await llm_web_search(query, num_results=5)
    except Exception:
        logger.exception("brainstorm: llm_web_search call failed")
        return ""

    # WebSearchResult.answer (NOT .text) is the prose synthesis; .citations
    # is a list of Citation(title, url, snippet). See web_search_llm.py.
    answer = (getattr(result, "answer", "") or "").strip()
    if not answer:
        return ""

    citation_lines: list[str] = []
    for c in (getattr(result, "citations", None) or [])[:8]:
        url = getattr(c, "url", "") or ""
        title = getattr(c, "title", "") or url
        if url:
            citation_lines.append(f"- {title}: {url}")
    citations_block = ("\n\nSources:\n" + "\n".join(citation_lines)) if citation_lines else ""

    return (
        "\n<web_search_results>\n"
        f"Query: {query}\n\n"
        f"{answer}{citations_block}\n"
        "</web_search_results>\n"
    )


async def _fetch_exemplars_block(
    state: dict, *, section_path: str | None, instruction: str
) -> str:
    """Pull the personalization "convergence record" block from
    `edit_exemplars`. Empty string when there are no exemplars yet, or when
    the lookup fails (non-fatal — handlers still run).

    Gated by the EXEMPLARS_DISABLED env var so we can A/B without redeploys.
    """
    import os

    if os.environ.get("EXEMPLARS_DISABLED", "").strip().lower() in {"1", "true", "yes"}:
        return ""

    session_factory = state.get("_session_factory")
    job_id = state.get("_job_id")
    if not session_factory or not job_id:
        return ""

    try:
        from app.services import edit_exemplars_service

        async with session_factory() as session:
            block = await edit_exemplars_service.retrieve_for_prompt(
                session,
                job_id=job_id,
                section_path=section_path,
                instruction=instruction or "",
            )
        return f"\n{block}\n" if block else ""
    except Exception:
        logger.exception("edit_exemplars retrieval failed (non-fatal)")
        return ""


_ACTION_CARD_FENCE = re.compile(r"```action_card\s*\n(.*?)\n```", re.DOTALL)


def derive_card_kind(section_path: str, proposed_value: str = "") -> str:
    """Infer the action_card `kind` from the section_path shape.

    The model used to be asked for `kind` directly, which led to silly
    mismatches like emitting `add_bullet` for a `experience.X.bullets.N`
    path that's actually a single-bullet rewrite. Backend-derived is
    less error-prone and saves prompt tokens.

    Heuristic:
      - "selected_research.<N>"            -> replace_selected_research
      - "experience.<X>.bullets" (exact)   -> rewrite_section (whole array)
                                             OR add_bullet if proposed_value
                                             looks like a single bullet
      - "experience.<X>.bullets.<N>(.text)?" -> rewrite_section
      - everything else                    -> rewrite_section
    """
    sr_match = re.fullmatch(r"selected_research\.\d+", section_path or "")
    if sr_match:
        return "replace_selected_research"

    bullets_arr_match = re.fullmatch(r"experience\.[^.]+\.bullets", section_path or "")
    if bullets_arr_match:
        # If the proposed_value parses to a single bullet object (not an
        # array), treat as an append. Otherwise rewrite the whole array.
        try:
            parsed = json.loads(proposed_value) if proposed_value else None
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, dict) and "text" in parsed:
            return "add_bullet"
        if isinstance(parsed, str) and parsed.strip():
            return "add_bullet"
        return "rewrite_section"

    return "rewrite_section"


def _extract_action_cards(text: str) -> tuple[str, list[dict]]:
    """Pull fenced action_card JSON blocks out of the brainstorm response.

    Replaces each fence with a `[[ACTION_CARD:<index>]]` marker so the
    frontend can render the card inline. Malformed blocks are dropped with
    a warning. Returns (text_with_markers, cards).

    `kind` is derived backend-side from `section_path`; any model-supplied
    `kind` is ignored. Required fields are now just `section_path` and
    `proposed_value`.
    """
    cards: list[dict] = []

    def repl(match: re.Match) -> str:
        raw = match.group(1).strip()
        try:
            card = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("brainstorm: dropping invalid action_card JSON: %r", raw[:200])
            return ""
        if not isinstance(card, dict):
            logger.warning("brainstorm: action_card not an object: %r", card)
            return ""
        section_path = card.get("section_path")
        if not section_path or not isinstance(section_path, str):
            logger.warning(
                "brainstorm: action_card missing section_path: %r", card
            )
            return ""
        proposed_value = card.get("proposed_value")
        if proposed_value is None:
            logger.warning(
                "brainstorm: action_card missing proposed_value: %r", card
            )
            return ""
        # Coerce proposed_value to a string for downstream storage.
        if not isinstance(proposed_value, str):
            try:
                proposed_value = json.dumps(proposed_value)
            except (TypeError, ValueError):
                proposed_value = str(proposed_value)
            card["proposed_value"] = proposed_value
        # Always derive kind backend-side — overrides any model-supplied
        # value so we never trust a hallucinated kind.
        card["kind"] = derive_card_kind(section_path, proposed_value)
        idx = len(cards)
        cards.append(card)
        return f"[[ACTION_CARD:{idx}]]"

    cleaned = _ACTION_CARD_FENCE.sub(repl, text)
    # Collapse 3+ consecutive newlines to 2, since dropped fences leave gaps
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, cards


async def brainstorm(state: AgentState) -> dict:
    """Strategic-thinking handler. Free prose, opinionated, full profile context.

    Differs from `edit_section` and `answer_question` by:
    - Using BRAINSTORM_SYSTEM (no VOICE_RULES, no JSON schema).
    - Temperature 0.7 instead of 0.3–0.5.
    - Full chat history (last 30) instead of last 6.
    - Full profile_text (every accomplishment) instead of focused slice.
    - Optional one-shot OpenAI web_search injection when the message asks
      about current state (team members, recent posts, etc.).
    - Returning structured `_action_cards` parsed from fenced blocks in the
      prose; the router persists them and emits SSE events.
    """
    user_msg = state["user_message"]
    chat_history = state.get("chat_history") or []

    # Scoped-edit mode triggers ONLY when the classifier explicitly picked
    # scoped_edit (a clear single-section edit directive). A section being
    # anchored via click is just a focus *hint* — when the user asks
    # "change the others" or "what's wrong with this resume?" the classifier
    # picks brainstorm and we let it run unscoped so it can emit multiple
    # cards across sections.
    scoped_path = state.get("target_section_path") or state.get("section_context") or None
    is_scoped_edit = state.get("intent") == "scoped_edit"
    scoped_block = ""
    if is_scoped_edit and scoped_path:
        scoped_block = (
            f"\n## Scoped edit on section: {scoped_path}\n"
            "The user wants a change to this section. Emit one action_card "
            "targeting this section_path. Multiple cards on the same section "
            "are fine if the user asked for variants. Keep the prose tight "
            "(two paragraphs max).\n"
        )
    elif is_scoped_edit:
        # Classifier picked scoped_edit but the user didn't click a section
        # — they implied one in their message ("rewrite my MUSE bullet").
        # Let the model infer the section and emit a card with that path.
        scoped_block = (
            "\n## Scoped edit on a single section\n"
            "The user is asking for a change to ONE section but didn't "
            "click it explicitly. Read the message, identify the section "
            "(from the resume JSON keys), and emit an action_card with the "
            "correct section_path.\n"
        )

    web_search_block = ""
    if await _should_use_web_search_llm(user_msg, chat_history):
        web_search_block = await _fetch_web_search_context(state)

    exemplars_block = await _fetch_exemplars_block(
        state, section_path=scoped_path, instruction=user_msg
    )

    resume_json_str = json.dumps(state["resume_json"], indent=2, default=str)

    # Generous history budget — brainstorm needs to see the thread.
    history_lines: list[str] = []
    char_budget = 8000
    used = 0
    for msg in reversed(chat_history[-30:]):
        role = "Assistant" if msg.get("role") == "assistant" else "User"
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        line = f"{role}: {content}"
        if used + len(line) + 2 > char_budget:
            break
        history_lines.insert(0, line)
        used += len(line) + 2
    history_text = "\n\n".join(history_lines)

    plan = state.get("strategic_plan") or {}
    plan_ctx = ""
    if plan.get("core_argument"):
        plan_ctx = (
            "\n## Resume strategy for this role\n"
            f"Core argument: {plan['core_argument']}\n"
            f"Tone: {plan.get('tone', '?')}\n"
        )

    user_content_parts = [
        "## Current tailored resume (JSON)\n",
        f"```json\n{resume_json_str}\n```\n",
        "\n## Job posting\n",
        state.get("job_context", ""),
        "\n",
        _format_research_context(state),
        plan_ctx,
        "\n## Full profile & accomplishments (the source of truth)\n",
        state.get("profile_text", ""),
        "\n",
        exemplars_block,
        web_search_block,
        scoped_block,
        f"\n## Conversation history\n{history_text}\n" if history_text else "",
        f"\n## Latest user message\n{user_msg}\n",
    ]
    user_content = "".join(p for p in user_content_parts if p)

    logger.info(
        "brainstorm: prompt_chars=%d (scoped=%s, web_search=%s, exemplars=%s, history_msgs=%d)",
        len(user_content),
        bool(scoped_block),
        bool(web_search_block),
        bool(exemplars_block),
        len(history_lines),
    )

    response_text = await _call_openai(
        BRAINSTORM_SYSTEM, user_content, max_tokens=4000, temperature=0.7
    )

    cleaned_text, action_cards = _extract_action_cards(response_text)

    logger.info(
        "brainstorm: response_chars=%d, action_cards=%d",
        len(cleaned_text),
        len(action_cards),
    )

    return {
        "response_text": cleaned_text,
        "updated_json": None,
        "updated_section_path": None,
        "_action_cards": action_cards,
    }


_PROOFREAD_SYSTEM = """\
You proofread a tailored resume and report what you notice. You are READ-ONLY — \
you NEVER make edits or rewrite anything.

## What to flag
- Typos and misspelled words.
- Grammar errors (subject-verb agreement, tense slips, missing articles, run-ons).
- Sentence fragments where a complete sentence is expected.
- Capitalization issues (e.g., "kubernetes" mid-sentence; "AI/ML" inconsistencies).
- Punctuation: missing or doubled commas/periods, mixed quote styles.
- Awkward phrasing that a reader would stumble over.
- Inconsistent formatting (e.g., mixed " · " vs " | " separators within a section, \
mixed bullet endings — some with periods, some without).
- Pronoun slips ("I", "my", "we") — a tailored resume implies first person but should \
not use pronouns.
- Number/metric inconsistencies (e.g., "10 TB" in one place, "10TB" in another).

## What NOT to do
- Do NOT suggest rewrites. The user has not asked you to fix anything.
- Do NOT flag "voice" preferences or stylistic taste calls.
- Do NOT critique strategy, content choices, or section ordering.
- Do NOT speculate about facts or fabricate context.
- If a section reads cleanly, simply omit it from the report.

## Output format
A clean markdown report. Group by section header. Use bullets where each line is:
- **section.path**: "<short quote>" — one-line description of the issue.

Open with a one-sentence summary ("Found N issues across M sections" or "Looks clean — \
no typos or grammar issues found"). Close with NO suggested action — the user will \
fix anything they care about themselves.

Stay terse. The goal is a fast scan they can read in 30 seconds."""


def _resume_for_proofread(resume_json: dict) -> str:
    """Render the resume as plain text labeled by section path so the LLM can quote
    findings precisely. Mirrors the docx layout but keeps machine-friendly path tags."""
    parts: list[str] = []
    if resume_json.get("tagline"):
        parts.append(f"## tagline\n{resume_json['tagline']}")
    if resume_json.get("summary"):
        parts.append(f"## summary\n{resume_json['summary']}")

    research = resume_json.get("selected_research") or []
    for i, entry in enumerate(research):
        parts.append(
            f"## selected_research.{i}\n"
            f"  category_label: {entry.get('category_label', '')}\n"
            f"  title: {entry.get('title', '')}\n"
            f"  description: {entry.get('description', '')}"
        )

    experience = resume_json.get("experience") or {}
    for emp_key, block in experience.items():
        bullets = block.get("bullets") or []
        lines = [f"## experience.{emp_key}.bullets"]
        for j, b in enumerate(bullets):
            text = b.get("text") if isinstance(b, dict) else b
            lines.append(f"  [{j}] {text}")
        parts.append("\n".join(lines))

    skills = resume_json.get("technical_skills") or {}
    for bucket, value in skills.items():
        if value:
            parts.append(f"## technical_skills.{bucket}\n{value}")

    pubs = resume_json.get("publications") or []
    for i, p in enumerate(pubs):
        parts.append(f"## publications.{i}\n{p.get('citation', '')}")

    if resume_json.get("awards"):
        parts.append(f"## awards\n{resume_json['awards']}")

    return "\n\n".join(parts)


async def proofread(state: AgentState) -> dict:
    """Read-only typo / grammar / awkward-phrasing scan. Reports in chat, makes no edits."""
    resume_text = _resume_for_proofread(state["resume_json"])
    user_content = f"""## Resume Content
{resume_text}

---

Proofread the above. Report typos, grammar, awkward phrasing, and inconsistencies. \
Group by section path. Do NOT suggest rewrites — just flag what you noticed."""

    logger.info("proofread: calling LLM with %d char prompt", len(user_content))
    response = await _call_openai(_PROOFREAD_SYSTEM, user_content, max_tokens=1500, temperature=0.2)
    logger.info("proofread: LLM returned %d chars", len(response))
    return {
        "response_text": response,
        "updated_json": None,
        "updated_section_path": None,
    }


async def broad_rewrite(state: AgentState) -> dict:
    """Full resume rewrite for tone/style changes. Reuses the revision flow."""
    system = RESUME_REVISION_SYSTEM

    resume_json_str = json.dumps(state["resume_json"], indent=2)

    # Build chat context
    history_text = ""
    for msg in state["chat_history"][-6:]:
        history_text += f"{msg['role']}: {msg['content']}\n"

    # Include strategic plan if available
    plan_ctx = ""
    plan = state.get("strategic_plan") or {}
    if plan.get("core_argument"):
        plan_ctx = f"\n## Resume Strategy\nCore argument: {plan['core_argument']}\nTone: {plan.get('tone', '?')}\n"

    # Append writing memory preferences
    wm = state.get("writing_memory_text", "")
    memory_section = f"\n\n{wm}" if wm else ""

    user_content = f"""## Current Resume (JSON)

```json
{resume_json_str}
```

---

## Conversation History
{history_text}

## Revision Instruction
{state["user_message"]}

---

## Full Profile & Accomplishments
{state["profile_text"]}

---

## Target Job Posting
{state["job_context"]}
{plan_ctx}{memory_section}
---

Apply the revision instruction. Output the COMPLETE updated resume as valid JSON."""

    # Resume JSON for a 2-page resume regularly exceeds 4K tokens. Give it
    # enough headroom to finish the JSON output without truncation. Tracked
    # by a pre-existing bug surfaced under the unified-chat classifier when
    # broad_rewrite is picked more reliably than before.
    result = await _call_openai_json(system, user_content, max_tokens=8000)
    return {
        "updated_json": result,
        "updated_section_path": None,
        "response_text": "I've rewritten the resume based on your instruction.",
    }


async def save_preference(state: AgentState) -> dict:
    """Extract and persist a writing preference the user explicitly asked to remember."""
    # Use a quick LLM call to extract the structured rule from the user's message
    system = """Extract a writing preference rule from the user's message.
Output ONLY valid JSON (no fences):
{
  "rule_text": "concise rule the AI should follow in all future resume writing",
  "category": "word_choice|tone|structure|content|formatting"
}"""
    user_content = f"User message: {state['user_message']}\n\nExtract the rule:"

    try:
        result = await _call_openai_json(system, user_content, max_tokens=200)
        rule_text = result.get("rule_text", state["user_message"])
        category = result.get("category", "content")
    except Exception:
        # Fallback: use the raw message as the rule
        rule_text = state["user_message"]
        category = "content"

    # Persist the rule — will be done in the chat router after agent completes
    # Store in state so the router can persist it
    return {
        "response_text": f'Got it — I\'ll remember this preference for all future resumes: "{rule_text}"',
        "updated_json": None,
        "updated_section_path": None,
        "_new_preference": {"rule_text": rule_text, "category": category},
    }


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------


def _route_after_intent(state: AgentState) -> str:
    """Conditional edge after route_intent.

    Unified-chat routing:
      - scoped_edit  -> brainstorm handler (which produces a card targeting
        the anchored section; the user accepts via Yes to commit)
      - quick_edit   -> edit_section handler (commits immediately, bypasses
        the card; section path comes from section_context or is inferred by
        identify_section)
      - broad_rewrite, brainstorm, remember_preference, proofread -> as before
    """
    intent = state.get("intent") or "brainstorm"
    if intent == "scoped_edit":
        # Brainstorm w/ section anchor; emits a single action_card for the
        # section, the user accepts via Yes to commit verbatim.
        return "brainstorm"
    if intent == "quick_edit":
        # Direct-commit path. If section_context is set, edit_section uses
        # it; otherwise identify_section runs first to pick the target.
        return "identify_section" if not state.get("target_section_path") else "edit_section"
    if intent == "brainstorm":
        return "brainstorm"
    if intent == "broad_rewrite":
        return "broad_rewrite"
    if intent == "remember_preference":
        return "save_preference"
    if intent == "proofread":
        return "proofread"
    # Defensive fallback
    return "brainstorm"


def build_resume_agent_graph() -> StateGraph:
    """Build and compile the LangGraph decision graph."""
    graph = StateGraph(AgentState)

    # Add nodes
    graph.add_node("check_section_context", check_section_context)
    graph.add_node("route_intent", route_intent)
    graph.add_node("identify_section", identify_section)
    graph.add_node("edit_section", edit_section)
    graph.add_node("brainstorm", brainstorm)
    graph.add_node("broad_rewrite", broad_rewrite)
    graph.add_node("save_preference", save_preference)
    graph.add_node("proofread", proofread)

    # Entry: always annotate section context, then route.
    graph.set_entry_point("check_section_context")
    graph.add_edge("check_section_context", "route_intent")

    graph.add_conditional_edges(
        "route_intent",
        _route_after_intent,
        {
            "identify_section": "identify_section",
            "edit_section": "edit_section",
            "brainstorm": "brainstorm",
            "broad_rewrite": "broad_rewrite",
            "save_preference": "save_preference",
            "proofread": "proofread",
        },
    )

    graph.add_edge("identify_section", "edit_section")

    # Terminal nodes -> END
    graph.add_edge("edit_section", END)
    graph.add_edge("brainstorm", END)
    graph.add_edge("broad_rewrite", END)
    graph.add_edge("save_preference", END)
    graph.add_edge("proofread", END)

    return graph.compile()


# Singleton compiled graph
_agent = None


def get_resume_agent():
    """Return the compiled LangGraph agent (singleton)."""
    global _agent
    if _agent is None:
        _agent = build_resume_agent_graph()
    return _agent
