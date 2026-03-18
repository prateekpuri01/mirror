"""LangGraph decision graph for agentic resume editing.

Runs as pure Python inside the FastAPI process — no separate server needed.
"""

import copy
import json
import logging
from typing import Any

from langgraph.graph import END, StateGraph

from app.ai.agent_state import AgentState
from app.ai.client import RESUME_MODEL, get_openai_client
from app.ai.resume_prompts import RESUME_REVISION_SYSTEM
from app.services.document_service import _get_nested, _set_nested

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Section path metadata — maps human-friendly labels to JSON paths
# ---------------------------------------------------------------------------

SECTION_LABELS: dict[str, str] = {
    "tagline": "tagline",
    "summary": "summary",
    "selected_research": "selected_research",
    "experience.rand": "experience.rand",
    "experience.rand.bullets": "experience.rand.bullets",
    "experience.finra": "experience.finra",
    "experience.finra.bullets": "experience.finra.bullets",
    "experience.ucla": "experience.ucla",
    "experience.ucla.bullets": "experience.ucla.bullets",
    "publications": "publications",
    "technical_skills": "technical_skills",
    "technical_skills.ai_systems": "technical_skills.ai_systems",
    "technical_skills.data_science": "technical_skills.data_science",
    "technical_skills.engineering": "technical_skills.engineering",
    "technical_skills.communication": "technical_skills.communication",
    "awards": "awards",
}

# Valid top-level section names for the LLM to identify
VALID_SECTIONS = list(SECTION_LABELS.keys())


async def _call_openai(system: str, user_content: str, max_tokens: int = 2000, temperature: float = 0.3) -> str:
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
    """If section_context is set from UI click, skip routing entirely."""
    if state["section_context"]:
        try:
            value = _get_nested(state["resume_json"], state["section_context"])
        except (KeyError, IndexError, TypeError):
            value = None
        return {
            "intent": "make_edit",
            "target_section_path": state["section_context"],
            "target_section_value": value,
        }
    return {}


async def route_intent(state: AgentState) -> dict:
    """Classify the user's intent via a fast structured-output LLM call."""
    system = """You classify resume editing requests. Output ONLY one of these exact words:
- make_edit: user wants a specific change to one section
- ask_question: user is asking a question, no edits needed
- broad_rewrite: user wants a tone/style change affecting the whole resume
- multiple_changes: user requested 2+ distinct changes at once

Output only the classification word, nothing else."""

    # Include recent chat for context
    history_text = ""
    for msg in state["chat_history"][-4:]:
        history_text += f"{msg['role']}: {msg['content']}\n"

    user_content = f"""Chat history:
{history_text}

Latest message: {state['user_message']}

Classification:"""

    result = await _call_openai(system, user_content, max_tokens=10, temperature=0)
    intent = result.strip().lower().strip('"').strip("'")

    # Normalize to valid intents
    valid = {"make_edit", "ask_question", "broad_rewrite", "multiple_changes"}
    if intent not in valid:
        intent = "make_edit"  # Default to edit

    logger.info("route_intent: classified as %r", intent)
    return {"intent": intent}


async def identify_section(state: AgentState) -> dict:
    """Identify which section the user wants to edit from their message."""
    sections_list = "\n".join(f"- {s}" for s in VALID_SECTIONS)

    system = f"""You identify which resume section a user wants to edit.

Available sections:
{sections_list}

Output ONLY the section path from the list above. If unsure, pick the closest match."""

    # Provide resume structure overview
    resume_keys = list(state["resume_json"].keys())
    user_content = f"""Resume sections present: {', '.join(resume_keys)}

User message: {state['user_message']}

Section path:"""

    result = await _call_openai(system, user_content, max_tokens=20, temperature=0)
    path = result.strip().lower().strip('"').strip("'")

    # Validate the path
    if path not in SECTION_LABELS:
        # Try partial matching
        for valid_path in SECTION_LABELS:
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


async def edit_section(state: AgentState) -> dict:
    """Edit a single section of the resume. Only sends the section text + instruction."""
    path = state["target_section_path"]
    current_value = state["target_section_value"]

    if current_value is None:
        return {
            "response_text": f"I couldn't find the section '{path}' in the resume. Could you clarify which section you'd like to edit?",
            "updated_json": None,
            "updated_section_path": None,
        }

    system = """You are an expert resume editor. You edit ONLY the specific resume section provided.

CRITICAL RULES:
1. NEVER fabricate accomplishments, metrics, or skills not in the provided profile.
2. Apply the instruction precisely. Change only what's asked.
3. Maintain professional tone — strong action verbs, quantitative where possible.
4. Output ONLY the updated section value as valid JSON. No explanations in the JSON.
5. Keep the same data type (string stays string, array stays array, object stays object)."""

    # Format the current section value
    if isinstance(current_value, str):
        section_text = current_value
    else:
        section_text = json.dumps(current_value, indent=2)

    user_content = f"""## Current section: {path}

```json
{section_text}
```

## User instruction
{state['user_message']}

## Full Profile & Accomplishments (for pulling in new content — NEVER fabricate)
{state['profile_text']}

## Job context (for relevance)
{state['job_context']}

Output ONLY the updated value for "{path}" as valid JSON. If the value is a string, output just the string in quotes. If it's an array or object, output the JSON structure."""

    result_text = await _call_openai(system, user_content, max_tokens=1500)

    # Parse the result
    try:
        # Strip markdown fences if present
        clean = result_text
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1]
            if clean.endswith("```"):
                clean = clean[:-3]
            clean = clean.strip()
        new_value = json.loads(clean)
    except json.JSONDecodeError:
        # If it's a plain string that wasn't JSON-quoted
        new_value = result_text.strip().strip('"')

    # Merge update into full resume JSON
    updated = copy.deepcopy(state["resume_json"])
    try:
        _set_nested(updated, path, new_value)
        return {
            "updated_json": updated,
            "updated_section_path": path,
            "response_text": f"I updated **{path}** based on your instruction.",
        }
    except (KeyError, IndexError, TypeError) as e:
        logger.warning("Failed to set section %s: %s", path, e)
        return {
            "updated_json": None,
            "updated_section_path": None,
            "response_text": f"I had trouble updating '{path}'. Could you try rephrasing?",
        }


async def answer_question(state: AgentState) -> AgentState:
    """Answer a question about the resume without making edits."""
    system = """You are a helpful resume assistant. You have access to the candidate's full resume,
their complete professional profile with all accomplishments, and the target job posting.

Answer questions about:
- How well the resume targets the job
- Which accomplishments could be swapped in/out
- What's missing or could be strengthened
- Strategy for tailoring specific sections

Be concise and specific. Reference specific accomplishments by name when relevant."""

    resume_text = json.dumps(state["resume_json"], indent=2)

    # Include recent conversation for context
    history_text = ""
    for msg in state["chat_history"][-6:]:
        history_text += f"{msg['role'].upper()}: {msg['content']}\n"

    user_content = f"""## Current Resume (JSON)
{resume_text}

## Full Profile & Accomplishments
{state['profile_text']}

## Target Job Posting
{state['job_context']}

## Conversation History
{history_text}

## Current Question
{state['user_message']}"""

    logger.info("answer_question: calling LLM with %d char prompt", len(user_content))
    response = await _call_openai(system, user_content, max_tokens=800, temperature=0.5)
    logger.info("answer_question: LLM returned %d chars: %r", len(response), response[:100])
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

    user_content = f"""## Current Resume (JSON)

```json
{resume_json_str}
```

---

## Conversation History
{history_text}

## Revision Instruction
{state['user_message']}

---

## Full Profile & Accomplishments
{state['profile_text']}

---

## Target Job Posting
{state['job_context']}

---

Apply the revision instruction. Output the COMPLETE updated resume as valid JSON."""

    result = await _call_openai_json(system, user_content, max_tokens=4000)
    return {
        "updated_json": result,
        "updated_section_path": None,
        "response_text": "I've rewritten the resume based on your instruction.",
    }


async def reject_multiple(state: AgentState) -> dict:
    """Politely reject multi-change requests."""
    return {
        "response_text": (
            "I can handle one change at a time for accuracy. "
            "Which change would you like me to make first?"
        ),
        "updated_json": None,
        "updated_section_path": None,
    }


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------


def _route_after_context_check(state: AgentState) -> str:
    """Conditional edge after check_section_context."""
    if state.get("section_context"):
        return "edit_section"
    return "route_intent"


def _route_after_intent(state: AgentState) -> str:
    """Conditional edge after route_intent."""
    intent = state.get("intent", "make_edit")
    if intent == "make_edit":
        return "identify_section"
    elif intent == "ask_question":
        return "answer_question"
    elif intent == "broad_rewrite":
        return "broad_rewrite"
    elif intent == "multiple_changes":
        return "reject_multiple"
    return "identify_section"


def build_resume_agent_graph() -> StateGraph:
    """Build and compile the LangGraph decision graph."""
    graph = StateGraph(AgentState)

    # Add nodes
    graph.add_node("check_section_context", check_section_context)
    graph.add_node("route_intent", route_intent)
    graph.add_node("identify_section", identify_section)
    graph.add_node("edit_section", edit_section)
    graph.add_node("answer_question", answer_question)
    graph.add_node("broad_rewrite", broad_rewrite)
    graph.add_node("reject_multiple", reject_multiple)

    # Set entry point
    graph.set_entry_point("check_section_context")

    # Conditional edges from check_section_context
    graph.add_conditional_edges(
        "check_section_context",
        _route_after_context_check,
        {
            "edit_section": "edit_section",
            "route_intent": "route_intent",
        },
    )

    # Conditional edges from route_intent
    graph.add_conditional_edges(
        "route_intent",
        _route_after_intent,
        {
            "identify_section": "identify_section",
            "answer_question": "answer_question",
            "broad_rewrite": "broad_rewrite",
            "reject_multiple": "reject_multiple",
        },
    )

    # identify_section -> edit_section
    graph.add_edge("identify_section", "edit_section")

    # Terminal nodes -> END
    graph.add_edge("edit_section", END)
    graph.add_edge("answer_question", END)
    graph.add_edge("broad_rewrite", END)
    graph.add_edge("reject_multiple", END)

    return graph.compile()


# Singleton compiled graph
_agent = None


def get_resume_agent():
    """Return the compiled LangGraph agent (singleton)."""
    global _agent
    if _agent is None:
        _agent = build_resume_agent_graph()
    return _agent
