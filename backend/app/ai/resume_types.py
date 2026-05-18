"""Data structures for the sequential resume generation pipeline.

StrategicPlan: The LLM's plan for how to sell the candidate (Phase 1 output).
GenerationLogEntry: Per-step reasoning record that accumulates through Phase 2.
GenerationContext: Orchestrator state that grows as each component is generated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypedDict


# ---------------------------------------------------------------------------
# Phase 1 output: Strategic Plan
# ---------------------------------------------------------------------------


class AccomplishmentPick(TypedDict):
    accomplishment_id: str
    rationale: str          # Why this accomplishment for this job?
    framing_angle: str      # How should the description be framed?
    reserve_for_bullets: str  # What to NOT say in the description — save for bullets


class BulletMapping(TypedDict):
    employer_key: str
    accomplishment_ids: list[str]  # Which accomplishments to use for this employer
    rationale: str                 # Why these? What job requirements do they address?
    emphasis: str                  # What angle to take for this employer's bullets


class RedundancyWarning(TypedDict):
    accomplishment_id: str
    appears_in: list[str]      # e.g., ["selected_research.0", "experience.rand.bullets.1"]
    differentiation: str       # How to make these two appearances complementary


class StrategicPlan(TypedDict):
    core_argument: str                             # 1-2 sentences: why this person gets an interview
    selected_accomplishments: list[AccomplishmentPick]  # exactly 3
    bullet_mapping: list[BulletMapping]            # one per employer
    summary_angle: str                             # framing direction (NOT the summary text itself)
    tone: str                                      # e.g., "technical builder", "research leader"
    redundancy_warnings: list[RedundancyWarning]   # accomplishments appearing in multiple sections
    pitfalls: list[str]                            # what to avoid


# ---------------------------------------------------------------------------
# Phase 2: Generation Log (accumulates per step)
# ---------------------------------------------------------------------------


class GenerationLogEntry(TypedDict):
    step: str                          # e.g., "research_pick", "research_desc.0", "experience.rand"
    accomplishment_ids: list[str] | None  # which accomplishments this step used
    reasoning: str                     # why these choices were made
    content_preview: str               # first ~100 chars of what was generated
    avoid_downstream: str              # what NOT to repeat in later steps


# ---------------------------------------------------------------------------
# Orchestrator state: grows through Phase 2
# ---------------------------------------------------------------------------


@dataclass
class GenerationContext:
    """Accumulating state for the sequential resume pipeline.

    Each step reads from this context (to see what's been generated so far)
    and writes its output back into it.
    """

    # Set in Phase 1
    strategic_plan: dict = field(default_factory=dict)

    # Grows through Phase 2
    generation_log: list[dict] = field(default_factory=list)
    research_picks: list[dict] = field(default_factory=list)      # [{accomplishment_id, rationale}]
    research_descriptions: list[dict] = field(default_factory=list)  # [{category_label, title, description, accomplishment_id}]
    experience_blocks: dict[str, dict] = field(default_factory=dict)  # employer_key -> {bullets, accomplishment_ids}

    # Set in later Phase 2 steps
    summary: str | None = None
    tagline: str | None = None
    publications: list[dict] | None = None
    technical_skills: dict | None = None
    awards: str | None = None

    # ---------------------------------------------------------------------------
    # Context formatting for LLM calls
    # ---------------------------------------------------------------------------

    def to_resume_so_far(self) -> str:
        """Format everything generated so far as readable text for the next LLM call."""
        parts: list[str] = []

        if self.tagline:
            parts.append(f"TAGLINE: {self.tagline}")

        if self.summary:
            parts.append(f"\nSUMMARY: {self.summary}")

        if self.research_descriptions:
            parts.append("\nSELECTED RESEARCH:")
            for i, rd in enumerate(self.research_descriptions):
                parts.append(
                    f"  [{i}] {rd.get('category_label', '')} — {rd.get('title', '')}\n"
                    f"      {rd.get('description', '')}"
                )

        if self.experience_blocks:
            parts.append("\nEXPERIENCE:")
            for emp_key, emp_data in self.experience_blocks.items():
                parts.append(f"  {emp_key}:")
                for bi, bullet in enumerate(emp_data.get("bullets", [])):
                    parts.append(f"    [{bi}] {bullet}")

        if self.publications:
            parts.append("\nPUBLICATIONS:")
            for pub in self.publications:
                parts.append(f"  - {pub.get('citation', '')}")

        if self.technical_skills:
            parts.append("\nTECHNICAL SKILLS:")
            for cat, skills in self.technical_skills.items():
                parts.append(f"  {cat}: {skills}")

        if self.awards:
            parts.append(f"\nAWARDS: {self.awards}")

        return "\n".join(parts) if parts else "(nothing generated yet)"

    def to_log_text(self) -> str:
        """Format the generation log as text for context injection."""
        if not self.generation_log:
            return "(no generation log yet)"

        parts: list[str] = []
        for entry in self.generation_log:
            parts.append(
                f"[STEP: {entry.get('step', '?')}]\n"
                f"  CHOSE: {', '.join(entry.get('accomplishment_ids', []) or ['N/A'])}\n"
                f"  BECAUSE: {entry.get('reasoning', '?')}\n"
                f"  PREVIEW: {entry.get('content_preview', '')[:100]}\n"
                f"  AVOID DOWNSTREAM: {entry.get('avoid_downstream', 'none')}"
            )
        return "\n\n".join(parts)

    def to_plan_text(self) -> str:
        """Format the strategic plan as text for context injection."""
        plan = self.strategic_plan
        if not plan:
            return "(no plan)"

        parts = [f"CORE ARGUMENT: {plan.get('core_argument', '?')}"]
        parts.append(f"TONE: {plan.get('tone', '?')}")
        parts.append(f"SUMMARY ANGLE: {plan.get('summary_angle', '?')}")

        parts.append("\nSELECTED ACCOMPLISHMENTS:")
        for pick in plan.get("selected_accomplishments", []):
            parts.append(
                f"  - {pick.get('accomplishment_id', '?')}: {pick.get('rationale', '')}\n"
                f"    Frame: {pick.get('framing_angle', '')}\n"
                f"    Reserve for bullets: {pick.get('reserve_for_bullets', 'nothing specific')}"
            )

        parts.append("\nBULLET MAPPING:")
        for bm in plan.get("bullet_mapping", []):
            parts.append(
                f"  - {bm.get('employer_key', '?')}: "
                f"{', '.join(bm.get('accomplishment_ids', []))}\n"
                f"    Emphasis: {bm.get('emphasis', '')}"
            )

        if plan.get("redundancy_warnings"):
            parts.append("\nREDUNDANCY WARNINGS:")
            for rw in plan["redundancy_warnings"]:
                parts.append(
                    f"  - {rw.get('accomplishment_id', '?')} appears in "
                    f"{', '.join(rw.get('appears_in', []))}\n"
                    f"    Differentiate: {rw.get('differentiation', '')}"
                )

        if plan.get("pitfalls"):
            parts.append(f"\nPITFALLS: {'; '.join(plan['pitfalls'])}")

        return "\n".join(parts)

    def append_log(
        self,
        step: str,
        accomplishment_ids: list[str] | None,
        reasoning: str,
        content_preview: str,
        avoid_downstream: str = "",
    ) -> None:
        """Append a generation log entry."""
        self.generation_log.append({
            "step": step,
            "accomplishment_ids": accomplishment_ids,
            "reasoning": reasoning,
            "content_preview": content_preview[:100],
            "avoid_downstream": avoid_downstream,
        })

    # ---------------------------------------------------------------------------
    # Assembly
    # ---------------------------------------------------------------------------

    def assemble_resume_json(self) -> dict:
        """Assemble the final resume JSON from all generated components."""
        resume: dict[str, Any] = {
            "tagline": self.tagline or "",
            "summary": self.summary or "",
            "selected_research": self.research_descriptions,
            "experience": self.experience_blocks,
            "publications": self.publications or [],
            "technical_skills": self.technical_skills or {},
            "awards": self.awards or "",
            # Metadata (underscore = not rendered in markdown/docx)
            "_strategic_plan": self.strategic_plan,
            "_generation_log": self.generation_log,
        }
        return resume
