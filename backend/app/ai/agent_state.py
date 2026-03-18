"""State definition for the resume editing LangGraph agent."""

from typing import Any, TypedDict


class AgentState(TypedDict):
    # Input from the user/UI
    user_message: str
    section_context: str | None  # UI-selected section path (None = unspecified)

    # Context loaded before graph runs
    resume_json: dict  # current resume JSON
    job_context: str  # formatted job description
    profile_text: str  # full profile for accomplishment lookups
    chat_history: list[dict]  # prior messages [{role, content}]

    # Router outputs
    intent: str | None  # "make_edit" | "ask_question" | "broad_rewrite" | "multiple_changes"
    target_section_path: str | None  # identified section to edit
    target_section_value: Any  # current value of that section

    # Outputs
    response_text: str  # text response to user
    updated_json: dict | None  # updated resume JSON (None if no edits)
    updated_section_path: str | None  # what changed
