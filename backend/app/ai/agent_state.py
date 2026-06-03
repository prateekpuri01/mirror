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
    generation_log: list[dict] | None  # from _generation_log in resume JSON
    strategic_plan: dict | None  # from _strategic_plan in resume JSON
    writing_memory_text: str  # formatted writing preferences from past edits

    # Router outputs
    intent: str | None  # "make_edit" | "ask_question" | "broad_rewrite" | "multiple_changes"
    target_section_path: str | None  # identified section to edit
    target_section_value: Any  # current value of that section

    # Outputs
    response_text: str  # text response to user
    updated_json: dict | None  # updated resume JSON (None if no edits)
    updated_section_path: str | None  # what changed
    _new_preference: dict | None  # set by save_preference node
    # Set by the brainstorm node when the LLM emits ```action_card fences.
    # Each entry is the raw parsed dict {kind, section_path?, rationale?,
    # proposed_value, ...}. Router persists them and emits SSE events.
    _action_cards: list[dict] | None

    # Loaded by the chat router and used by the brainstorm handler to
    # inject cached company research into the prompt and to seed the
    # web-search query with a company hint.
    company_research: dict | None

    # Optional: callable returning an AsyncSession context manager. When set,
    # nodes that need the DB (e.g. fetching content_memory grounding) can use
    # it to open their own session. Injected by the chat router.
    _session_factory: Any  # callable | None
    # Optional: full UserProfile.data dict, used by edit_section to build a
    # focused profile slice rather than dumping the entire profile_text.
    # Falls back to profile_text when missing.
    _profile_data: dict | None
    # The current job_id and doc_id, threaded through so the brainstorm/edit
    # handlers can read & write edit_exemplars without the router needing
    # to wire each call site separately.
    _job_id: Any  # uuid.UUID | None
    _doc_id: Any  # uuid.UUID | None
