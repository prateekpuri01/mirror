"""Staged resume generation pipeline (v4).

Replaces the v3 single-shot generation. Flow:

  [1] Strategic plan        (existing STRATEGIC_PLAN_SYSTEM)
  [2] Selection             — picks accomplishment IDs + employer anchors
  [3] Parallel: research entries + skill buckets
  [4] Parallel: bullets per employer (sees finalized research for de-dup)
  [5] Summary + tagline     — synthesizes the assembled draft

Each leaf call pulls its own grounding from ``content_memory`` so the user's
past hand-tuned versions inform tone and phrasing without being copied.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.content_memory_grounding import (
    format_grounding_block,
)
from app.ai.content_memory_paths import (
    EXPERIENCE_BULLETS_SET,
    RESEARCH_DESCRIPTION,
    SCALAR_KEY,
    SKILL_BUCKET,
    SUMMARY,
    TAGLINE,
)
from app.ai.prompts import format_job_for_scoring
from app.ai.resume_prompts import (
    CRITIC_V4_SYSTEM,
    PUBLICATIONS_SYSTEM,
    RESEARCH_ENTRY_V4_SYSTEM,
    SELECTION_SYSTEM,
    STRATEGIC_PLAN_SYSTEM,
    SUMMARY_TAGLINE_SYSTEM,
    build_bullet_set_prompt,
    build_bullet_set_system,
    build_compact_profile_for_plan,
    build_critic_v4_prompt,
    build_full_profile_for_resume,
    build_publications_prompt,
    build_research_entry_v4_prompt,
    build_selection_prompt,
    build_skill_bucket_prompt,
    build_skill_bucket_system,
    build_strategic_plan_prompt,
    build_summary_tagline_prompt,
)
from app.ai.utils import employer_key as employer_key_fn
from app.ai.writing_memory import format_writing_memory
from app.services import content_memory_service

logger = logging.getLogger(__name__)


SKILL_BUCKETS = ("ai_systems", "data_science", "engineering")


# Section heading for "Selected Research" varies by the lane the strategic plan
# identifies. Anything the plan doesn't classify falls back to "Selected Research".
SECTION_TITLE_BY_LANE: dict[str, str] = {
    "evals_safety": "Selected AI Evaluation & Research Engineering Projects",
    "human_data": "Selected Human-AI Systems & Evaluation Projects",
    "applied_ai_eng": "Selected Applied AI Projects",
    "research_engineer": "Selected Research Engineering Projects",
    "generalist": "Selected Research",
}


# Trace logging — every leaf LLM call dumps its assembled system + user + response
# to a flat directory so debugging "what did the agent see" becomes a file diff.
_TRACE_ROOT = Path(os.environ.get("RESUME_TRACE_DIR", "/app/output/traces"))


def _trace_dump(
    trace_id: str | None,
    stage: str,
    system: str,
    messages: list[dict] | None,
    response: Any,
) -> None:
    """Write a single stage's full prompt + response to disk. Best-effort."""
    if not trace_id:
        return
    try:
        out_dir = _TRACE_ROOT / trace_id
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{stage}.txt"
        user_blob = ""
        if messages:
            user_blob = "\n\n---\n\n".join(
                m.get("content", "") for m in messages if m.get("role") == "user"
            )
        if isinstance(response, (dict, list)):
            response_blob = json.dumps(response, indent=2)
        else:
            response_blob = str(response)
        body = (
            f"# STAGE: {stage}\n"
            f"# trace_id: {trace_id}\n"
            f"\n## SYSTEM ({len(system)} chars)\n\n{system}\n"
            f"\n## USER ({len(user_blob)} chars)\n\n{user_blob}\n"
            f"\n## RESPONSE\n\n{response_blob}\n"
        )
        path.write_text(body)
    except Exception:
        logger.exception("trace dump failed for %s/%s", trace_id, stage)


async def run_pipeline(
    *,
    session: AsyncSession,
    job,
    company,
    company_research: dict | None,
    profile_data: dict,
    call_llm,
    update_status,
) -> dict:
    """Execute the staged resume pipeline and return the assembled content_json.

    ``call_llm`` and ``update_status`` are injected so the pipeline can reuse
    the JSON-parsing helper and status-tracking dict already living in
    ``resume_builder``.
    """
    # Trace ID for this generation — every stage's prompt+response is dumped
    # to /app/output/traces/{trace_id}/. Surfaced in logs and resume_data.
    trace_id = uuid.uuid4().hex[:12]
    job_label = f"{getattr(job, 'company', '?')} / {getattr(job, 'title', '?')}"
    logger.info("Resume pipeline trace_id=%s for job=%s", trace_id, job_label)

    # ----- Inputs ----------------------------------------------------------
    company_notes = company.notes if company and getattr(company, "notes", None) else None
    job_text = format_job_for_scoring(job, company_notes=company_notes)
    score_rationale = job.score_rationale
    build_full_profile_for_resume(profile_data)
    compact_profile = build_compact_profile_for_plan(profile_data)

    employer_label_map = _build_employer_label_map(profile_data)
    accomplishment_lookup = _build_accomplishment_lookup(profile_data)

    # writing_memory style rules — appended into every leaf system prompt as
    # a fallback signal alongside content_memory grounding.
    writing_memory_text = await format_writing_memory(session, "resume")

    # ----- [1] Strategic Plan ---------------------------------------------
    update_status("planning", "strategic_plan", "Building resume strategy...", 1)
    plan_messages = build_strategic_plan_prompt(
        compact_profile,
        job_text,
        company_research,
        score_rationale,
        profile_data=profile_data,
    )
    plan = await call_llm(
        STRATEGIC_PLAN_SYSTEM,
        plan_messages,
        temperature=0.4,
        max_tokens=4000,
    )
    _trace_dump(trace_id, "01_strategic_plan", STRATEGIC_PLAN_SYSTEM, plan_messages, plan)
    plan_text = json.dumps(plan, indent=2)
    logger.info("Strategic plan core_argument: %s", str(plan.get("core_argument", ""))[:160])

    # ----- [2] Selection ---------------------------------------------------
    update_status("planning", "selection", "Picking accomplishments to feature...", 2)
    selection_messages = build_selection_prompt(plan_text, compact_profile, job_text)
    selection = await call_llm(
        SELECTION_SYSTEM,
        selection_messages,
        temperature=0.2,
        max_tokens=1500,
    )
    _trace_dump(trace_id, "02_selection", SELECTION_SYSTEM, selection_messages, selection)

    research_ids: list[str] = list(selection.get("selected_research_accomplishment_ids") or [])
    employer_anchors: list[dict] = list(selection.get("experience_employer_anchors") or [])

    research_ids = [rid for rid in research_ids if rid in accomplishment_lookup][:3]
    employer_anchors = _validate_employer_anchors(
        employer_anchors, employer_label_map, accomplishment_lookup
    )

    logger.info(
        "Selection: research_ids=%s employers=%s",
        research_ids,
        [e["employer_key"] for e in employer_anchors],
    )

    # ----- [3] Parallel: research entries + skill buckets ------------------
    update_status("generating", "research_and_skills", "Writing research + skills...", 3)

    research_grounding = await content_memory_service.fetch_grounding(
        session,
        entity_type=RESEARCH_DESCRIPTION,
        entity_keys=research_ids,
    )
    skill_grounding = await content_memory_service.fetch_grounding(
        session,
        entity_type=SKILL_BUCKET,
        entity_keys=SKILL_BUCKETS,
    )

    plan_research_lookup = _build_plan_research_lookup(plan)

    research_tasks = [
        _generate_research_entry(
            call_llm=call_llm,
            accomplishment=accomplishment_lookup[aid],
            plan_text=plan_text,
            plan_entry=plan_research_lookup.get(aid),
            job_text=job_text,
            grounding_rows=research_grounding.get(aid, []),
            profile_data=profile_data,
            writing_memory_text=writing_memory_text,
            trace_id=trace_id,
            stage_label=f"03_research_{aid}",
        )
        for aid in research_ids
    ]
    skill_tasks = [
        _generate_skill_bucket(
            call_llm=call_llm,
            bucket=bucket,
            plan_text=plan_text,
            job_text=job_text,
            grounding_rows=skill_grounding.get(bucket, []),
            profile_data=profile_data,
            writing_memory_text=writing_memory_text,
            trace_id=trace_id,
            stage_label=f"03_skill_{bucket}",
        )
        for bucket in SKILL_BUCKETS
    ]
    publications_task = _generate_publications(
        call_llm=call_llm,
        profile_data=profile_data,
        plan_text=plan_text,
        job_text=job_text,
        trace_id=trace_id,
    )

    research_results, skill_results, publications = await asyncio.gather(
        asyncio.gather(*research_tasks, return_exceptions=False),
        asyncio.gather(*skill_tasks, return_exceptions=False),
        publications_task,
    )

    selected_research = [r for r in research_results if r]
    technical_skills = {
        bucket: text for bucket, text in zip(SKILL_BUCKETS, skill_results, strict=False) if text
    }

    # ----- [4] Parallel: bullets per employer ------------------------------
    update_status("generating", "bullets", "Writing experience bullets...", 4)

    employer_keys = [a["employer_key"] for a in employer_anchors]
    bullet_grounding = await content_memory_service.fetch_grounding(
        session,
        entity_type=EXPERIENCE_BULLETS_SET,
        entity_keys=employer_keys,
    )

    bullet_tasks = [
        _generate_bullet_set(
            call_llm=call_llm,
            anchor=anchor,
            employer_label_map=employer_label_map,
            accomplishment_lookup=accomplishment_lookup,
            plan_text=plan_text,
            plan=plan,
            finalized_research=selected_research,
            job_text=job_text,
            grounding_rows=bullet_grounding.get(anchor["employer_key"], []),
            profile_data=profile_data,
            writing_memory_text=writing_memory_text,
            trace_id=trace_id,
            stage_label=f"04_bullets_{anchor['employer_key']}",
        )
        for anchor in employer_anchors
    ]
    bullet_results = await asyncio.gather(*bullet_tasks, return_exceptions=False)

    experience: dict[str, dict] = {}
    for anchor, result in zip(employer_anchors, bullet_results, strict=False):
        if not result:
            continue
        experience[anchor["employer_key"]] = {"bullets": result}

    # ----- [5] Critic + refiner -------------------------------------------
    update_status("generating", "critic", "Reviewing draft for fixable issues...", 5)

    critic_assembled_draft = _format_assembled_draft(
        selected_research=selected_research,
        experience=experience,
        technical_skills=technical_skills,
        employer_label_map=employer_label_map,
    )
    grounding_summary = _format_grounding_summary_for_critic(
        research_grounding=research_grounding,
        research_ids=research_ids,
        skill_grounding=skill_grounding,
        bullet_grounding=bullet_grounding,
        employer_keys=employer_keys,
        profile_data=profile_data,
    )
    critic_messages = build_critic_v4_prompt(
        plan_text,
        critic_assembled_draft,
        grounding_summary,
        job_text,
    )
    try:
        critic_result = await call_llm(
            CRITIC_V4_SYSTEM,
            critic_messages,
            temperature=0.2,
            max_tokens=2500,
        )
    except Exception:
        logger.exception("Critic stage failed; skipping refinement")
        critic_result = {"issues": []}
    _trace_dump(trace_id, "05_critic", CRITIC_V4_SYSTEM, critic_messages, critic_result)

    issues = critic_result.get("issues") if isinstance(critic_result, dict) else None
    if not isinstance(issues, list):
        issues = []
    logger.info("Critic flagged %d issue(s)", len(issues))

    if issues:
        update_status("generating", "refiner", f"Refining {len(issues)} entity(ies)...", 6)
        selected_research, technical_skills, experience = await _run_refiner(
            issues=issues,
            call_llm=call_llm,
            selected_research=selected_research,
            technical_skills=technical_skills,
            experience=experience,
            accomplishment_lookup=accomplishment_lookup,
            employer_label_map=employer_label_map,
            employer_anchors=employer_anchors,
            plan=plan,
            plan_text=plan_text,
            plan_research_lookup=plan_research_lookup,
            research_grounding=research_grounding,
            skill_grounding=skill_grounding,
            bullet_grounding=bullet_grounding,
            job_text=job_text,
            profile_data=profile_data,
            writing_memory_text=writing_memory_text,
            trace_id=trace_id,
        )

    # ----- [7] Summary + tagline ------------------------------------------
    update_status("generating", "summary_tagline", "Writing summary and tagline...", 7)

    summary_grounding_rows = (
        await content_memory_service.fetch_grounding(
            session,
            entity_type=SUMMARY,
            entity_keys=[SCALAR_KEY],
        )
    ).get(SCALAR_KEY, [])
    tagline_grounding_rows = (
        await content_memory_service.fetch_grounding(
            session,
            entity_type=TAGLINE,
            entity_keys=[SCALAR_KEY],
        )
    ).get(SCALAR_KEY, [])

    summary_grounding = format_grounding_block(
        summary_grounding_rows,
        profile_data=profile_data,
        label="## Your past hand-tuned summaries",
    )
    tagline_grounding = format_grounding_block(
        tagline_grounding_rows,
        profile_data=profile_data,
        label="## Your past hand-tuned taglines",
    )
    grounding_text = "\n\n".join(b for b in (summary_grounding, tagline_grounding) if b)
    grounding_text = _augment_with_writing_memory(grounding_text, writing_memory_text)

    assembled_draft = _format_assembled_draft(
        selected_research=selected_research,
        experience=experience,
        technical_skills=technical_skills,
        employer_label_map=employer_label_map,
    )
    # Re-format the draft AFTER refinement so summary sees the post-refiner state.
    assembled_draft = _format_assembled_draft(
        selected_research=selected_research,
        experience=experience,
        technical_skills=technical_skills,
        employer_label_map=employer_label_map,
    )
    role_lane = (plan.get("role_lane") or "generalist").strip()
    summary_messages = build_summary_tagline_prompt(
        plan_text,
        assembled_draft,
        job_text,
        grounding_text=grounding_text,
        role_lane=role_lane,
    )
    summary_result = await call_llm(
        SUMMARY_TAGLINE_SYSTEM,
        summary_messages,
        temperature=0.5,
        max_tokens=1000,
    )
    _trace_dump(
        trace_id, "07_summary_tagline", SUMMARY_TAGLINE_SYSTEM, summary_messages, summary_result
    )
    summary = (summary_result.get("summary") or "").strip()
    tagline = (summary_result.get("tagline") or "").strip()

    # ----- Assemble final content_json ------------------------------------
    selected_research_section_title = SECTION_TITLE_BY_LANE.get(
        role_lane, SECTION_TITLE_BY_LANE["generalist"]
    )
    resume_data = {
        "tagline": tagline,
        "summary": summary,
        "selected_research": selected_research,
        "selected_research_section_title": selected_research_section_title,
        "role_lane": role_lane,
        "experience": experience,
        "publications": publications,
        "technical_skills": technical_skills,
        "awards": _build_awards(profile_data),
        "_strategic_plan": plan,
        "_selection": selection,
        "_critic_issues": issues,
        "_trace_id": trace_id,
    }
    if company_research:
        resume_data["_research"] = company_research
    return resume_data


# ---------------------------------------------------------------------------
# Leaf generators
# ---------------------------------------------------------------------------


async def _generate_research_entry(
    *,
    call_llm,
    accomplishment: dict,
    plan_text: str,
    plan_entry: dict | None,
    job_text: str,
    grounding_rows: list,
    profile_data: dict,
    writing_memory_text: str,
    trace_id: str | None = None,
    stage_label: str = "research_entry",
    critic_notes: str = "",
) -> dict | None:
    grounding_text = format_grounding_block(grounding_rows, profile_data=profile_data)
    grounding_text = _augment_with_writing_memory(grounding_text, writing_memory_text)
    messages = build_research_entry_v4_prompt(
        accomplishment,
        plan_text,
        plan_entry,
        job_text,
        grounding_text=grounding_text,
        critic_notes=critic_notes,
    )
    try:
        entry = await call_llm(
            RESEARCH_ENTRY_V4_SYSTEM,
            messages,
            temperature=0.4,
            max_tokens=1500,
        )
    except Exception:
        logger.exception("Research entry generation failed for %s", accomplishment.get("id"))
        return None
    _trace_dump(trace_id, stage_label, RESEARCH_ENTRY_V4_SYSTEM, messages, entry)
    # Belt-and-suspenders: enforce the accomplishment_id even if the LLM drops it
    entry["accomplishment_id"] = accomplishment.get("id")
    if not entry.get("title"):
        entry["title"] = accomplishment.get("title", "")
    return entry


async def _generate_skill_bucket(
    *,
    call_llm,
    bucket: str,
    plan_text: str,
    job_text: str,
    grounding_rows: list,
    profile_data: dict,
    writing_memory_text: str,
    trace_id: str | None = None,
    stage_label: str = "skill_bucket",
    critic_notes: str = "",
    other_buckets: dict[str, str] | None = None,
) -> str | None:
    grounding_text = format_grounding_block(grounding_rows, profile_data=profile_data)
    grounding_text = _augment_with_writing_memory(grounding_text, writing_memory_text)
    system = build_skill_bucket_system(profile_data)
    messages = build_skill_bucket_prompt(
        bucket,
        plan_text,
        job_text,
        grounding_text=grounding_text,
        critic_notes=critic_notes,
        other_buckets=other_buckets,
    )
    try:
        result = await call_llm(system, messages, temperature=0.3, max_tokens=600)
    except Exception:
        logger.exception("Skill bucket generation failed for %s", bucket)
        return None
    _trace_dump(trace_id, stage_label, system, messages, result)
    text = (result.get("text") or "").strip()
    return text or None


async def _generate_bullet_set(
    *,
    call_llm,
    anchor: dict,
    employer_label_map: dict[str, str],
    accomplishment_lookup: dict[str, dict],
    plan_text: str,
    plan: dict,
    finalized_research: list[dict],
    job_text: str,
    grounding_rows: list,
    profile_data: dict,
    writing_memory_text: str,
    trace_id: str | None = None,
    stage_label: str = "bullet_set",
    critic_notes: str = "",
) -> list[dict] | None:
    employer_key_str = anchor["employer_key"]
    employer_name = employer_label_map.get(employer_key_str, employer_key_str)
    bullet_count = int(anchor.get("bullet_count") or 3)

    accomplishments = [
        accomplishment_lookup[aid]
        for aid in anchor.get("accomplishment_ids") or []
        if aid in accomplishment_lookup
    ]
    if not accomplishments:
        logger.info("Skipping employer %s — no valid accomplishments selected", employer_key_str)
        return None

    plan_mapping = _find_plan_bullet_mapping(plan, employer_key_str)

    grounding_text = format_grounding_block(grounding_rows, profile_data=profile_data)
    grounding_text = _augment_with_writing_memory(grounding_text, writing_memory_text)

    system = build_bullet_set_system(profile_data)
    messages = build_bullet_set_prompt(
        employer_key_str=employer_key_str,
        employer_name=employer_name,
        bullet_count=bullet_count,
        accomplishments=accomplishments,
        plan_mapping=plan_mapping,
        plan_text=plan_text,
        finalized_research=finalized_research,
        job_text=job_text,
        grounding_text=grounding_text,
        critic_notes=critic_notes,
    )
    try:
        result = await call_llm(system, messages, temperature=0.45, max_tokens=2500)
    except Exception:
        logger.exception("Bullet set generation failed for %s", employer_key_str)
        return None
    _trace_dump(trace_id, stage_label, system, messages, result)
    bullets = result.get("bullets") or []
    cleaned: list[dict] = []
    for b in bullets:
        if isinstance(b, dict):
            text = (b.get("text") or "").strip()
            if not text:
                continue
            cleaned.append(
                {
                    "text": text,
                    "accomplishment_ids": list(b.get("accomplishment_ids") or []),
                }
            )
        elif isinstance(b, str) and b.strip():
            cleaned.append({"text": b.strip(), "accomplishment_ids": []})
    return cleaned or None


# ---------------------------------------------------------------------------
# Critic helpers — run refinement on flagged entities only
# ---------------------------------------------------------------------------

import re as _re

_TARGET_RESEARCH_RE = _re.compile(r"^research\[(\d+)\]$")
_TARGET_SKILLS_RE = _re.compile(r"^skills\.([a-z_]+)$")
_TARGET_EXPERIENCE_RE = _re.compile(r"^experience\.([a-z0-9_]+)$")


def _format_critic_notes(issues_for_target: list[dict]) -> str:
    """Render the issues hitting one entity as a compact notes block."""
    lines: list[str] = []
    for issue in issues_for_target:
        kind = issue.get("kind", "other")
        problem = issue.get("issue", "").strip()
        direction = issue.get("direction", "").strip()
        current = issue.get("current", "").strip()
        bits = [f"[{kind}] {problem}"]
        if current:
            bits.append(f'  current: "{current}"')
        if direction:
            bits.append(f"  do instead: {direction}")
        lines.append("\n".join(bits))
    return "\n\n".join(lines)


async def _run_refiner(
    *,
    issues: list[dict],
    call_llm,
    selected_research: list[dict],
    technical_skills: dict[str, str],
    experience: dict[str, dict],
    accomplishment_lookup: dict[str, dict],
    employer_label_map: dict[str, str],
    employer_anchors: list[dict],
    plan: dict,
    plan_text: str,
    plan_research_lookup: dict[str, dict],
    research_grounding: dict[str, list],
    skill_grounding: dict[str, list],
    bullet_grounding: dict[str, list],
    job_text: str,
    profile_data: dict,
    writing_memory_text: str,
    trace_id: str | None,
) -> tuple[list[dict], dict[str, str], dict[str, dict]]:
    """Re-run flagged entities in parallel with critic notes appended.

    Returns the (possibly updated) selected_research, technical_skills, experience.
    Failed refinements fall back to the previous version (no clobber).
    """
    # Group issues by target so each entity is refined exactly once.
    by_target: dict[str, list[dict]] = {}
    for issue in issues:
        target = issue.get("target")
        if not isinstance(target, str):
            continue
        by_target.setdefault(target, []).append(issue)

    if not by_target:
        return selected_research, technical_skills, experience

    # Build a label → anchor lookup for employer-target dispatch
    anchor_by_employer = {a["employer_key"]: a for a in employer_anchors}

    tasks: list[tuple[str, asyncio.Task]] = []
    for target, issue_list in by_target.items():
        notes = _format_critic_notes(issue_list)

        m = _TARGET_RESEARCH_RE.match(target)
        if m:
            idx = int(m.group(1))
            if not (0 <= idx < len(selected_research)):
                continue
            entry = selected_research[idx]
            aid = entry.get("accomplishment_id")
            accomplishment = accomplishment_lookup.get(aid)
            if not accomplishment:
                continue
            tasks.append(
                (
                    target,
                    asyncio.create_task(
                        _generate_research_entry(
                            call_llm=call_llm,
                            accomplishment=accomplishment,
                            plan_text=plan_text,
                            plan_entry=plan_research_lookup.get(aid),
                            job_text=job_text,
                            grounding_rows=research_grounding.get(aid, []),
                            profile_data=profile_data,
                            writing_memory_text=writing_memory_text,
                            trace_id=trace_id,
                            stage_label=f"06_refiner_research_{aid}",
                            critic_notes=notes,
                        )
                    ),
                )
            )
            continue

        m = _TARGET_SKILLS_RE.match(target)
        if m:
            bucket = m.group(1)
            if bucket not in SKILL_BUCKETS:
                continue
            other_buckets = {b: technical_skills.get(b, "") for b in SKILL_BUCKETS if b != bucket}
            tasks.append(
                (
                    target,
                    asyncio.create_task(
                        _generate_skill_bucket(
                            call_llm=call_llm,
                            bucket=bucket,
                            plan_text=plan_text,
                            job_text=job_text,
                            grounding_rows=skill_grounding.get(bucket, []),
                            profile_data=profile_data,
                            writing_memory_text=writing_memory_text,
                            trace_id=trace_id,
                            stage_label=f"06_refiner_skill_{bucket}",
                            critic_notes=notes,
                            other_buckets=other_buckets,
                        )
                    ),
                )
            )
            continue

        m = _TARGET_EXPERIENCE_RE.match(target)
        if m:
            emp_key = m.group(1)
            anchor = anchor_by_employer.get(emp_key)
            if not anchor:
                continue
            tasks.append(
                (
                    target,
                    asyncio.create_task(
                        _generate_bullet_set(
                            call_llm=call_llm,
                            anchor=anchor,
                            employer_label_map=employer_label_map,
                            accomplishment_lookup=accomplishment_lookup,
                            plan_text=plan_text,
                            plan=plan,
                            finalized_research=selected_research,
                            job_text=job_text,
                            grounding_rows=bullet_grounding.get(emp_key, []),
                            profile_data=profile_data,
                            writing_memory_text=writing_memory_text,
                            trace_id=trace_id,
                            stage_label=f"06_refiner_bullets_{emp_key}",
                            critic_notes=notes,
                        )
                    ),
                )
            )
            continue

        logger.warning("Critic target %r does not match a known shape; ignoring", target)

    if not tasks:
        return selected_research, technical_skills, experience

    results = await asyncio.gather(*(t for _, t in tasks), return_exceptions=False)
    # Apply results back into the assembled state
    for (target, _), result in zip(tasks, results, strict=False):
        if result is None:
            continue
        m = _TARGET_RESEARCH_RE.match(target)
        if m:
            idx = int(m.group(1))
            selected_research[idx] = result
            continue
        m = _TARGET_SKILLS_RE.match(target)
        if m:
            technical_skills[m.group(1)] = result
            continue
        m = _TARGET_EXPERIENCE_RE.match(target)
        if m:
            experience[m.group(1)] = {"bullets": result}
            continue

    return selected_research, technical_skills, experience


def _format_grounding_summary_for_critic(
    *,
    research_grounding: dict[str, list],
    research_ids: list[str],
    skill_grounding: dict[str, list],
    bullet_grounding: dict[str, list],
    employer_keys: list[str],
    profile_data: dict,
) -> str:
    """Build a single block summarizing past versions for every entity in the
    draft, so the critic can compare voice/style without needing to fetch."""
    sections: list[str] = []

    if research_ids:
        sub: list[str] = []
        for i, aid in enumerate(research_ids):
            block = format_grounding_block(
                research_grounding.get(aid, []),
                profile_data=profile_data,
                label=f"### research[{i}] (accomplishment_id={aid})",
            )
            if block:
                sub.append(block)
        if sub:
            sections.append("## Research grounding\n\n" + "\n\n".join(sub))

    skill_blocks: list[str] = []
    for bucket in SKILL_BUCKETS:
        block = format_grounding_block(
            skill_grounding.get(bucket, []),
            profile_data=profile_data,
            label=f"### skills.{bucket}",
        )
        if block:
            skill_blocks.append(block)
    if skill_blocks:
        sections.append("## Skill bucket grounding\n\n" + "\n\n".join(skill_blocks))

    bullet_blocks: list[str] = []
    for emp_key in employer_keys:
        block = format_grounding_block(
            bullet_grounding.get(emp_key, []),
            profile_data=profile_data,
            label=f"### experience.{emp_key}",
        )
        if block:
            bullet_blocks.append(block)
    if bullet_blocks:
        sections.append("## Bullet-set grounding\n\n" + "\n\n".join(bullet_blocks))

    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_employer_label_map(profile_data: dict) -> dict[str, str]:
    label_map: dict[str, str] = {}
    for wh in profile_data.get("work_history") or []:
        emp = wh.get("employer")
        if not emp:
            continue
        label_map[employer_key_fn(emp)] = emp
    return label_map


def _build_accomplishment_lookup(profile_data: dict) -> dict[str, dict]:
    accomplishments = (profile_data.get("complete_profile") or {}).get("accomplishments") or []
    return {a.get("id"): a for a in accomplishments if a.get("id")}


def _build_plan_research_lookup(plan: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for entry in plan.get("selected_accomplishments") or []:
        aid = entry.get("accomplishment_id")
        if aid:
            out[aid] = entry
    return out


def _find_plan_bullet_mapping(plan: dict, employer_key_str: str) -> dict | None:
    for mapping in plan.get("bullet_mapping") or []:
        if mapping.get("employer_key") == employer_key_str:
            return mapping
    return None


def _validate_employer_anchors(
    anchors: list[dict],
    employer_label_map: dict[str, str],
    accomplishment_lookup: dict[str, dict],
) -> list[dict]:
    cleaned: list[dict] = []
    for anchor in anchors:
        emp_key = anchor.get("employer_key")
        if emp_key not in employer_label_map:
            logger.warning("Selection produced unknown employer_key=%s; dropping", emp_key)
            continue
        valid_acc_ids = [
            aid for aid in (anchor.get("accomplishment_ids") or []) if aid in accomplishment_lookup
        ]
        if not valid_acc_ids:
            logger.warning("Employer anchor %s has no valid accomplishments; dropping", emp_key)
            continue
        cleaned.append(
            {
                "employer_key": emp_key,
                "accomplishment_ids": valid_acc_ids,
                "bullet_count": int(anchor.get("bullet_count") or 3),
            }
        )
    return cleaned


def _format_assembled_draft(
    *,
    selected_research: list[dict],
    experience: dict[str, dict],
    technical_skills: dict[str, str],
    employer_label_map: dict[str, str],
) -> str:
    lines: list[str] = []
    if selected_research:
        lines.append("### Selected Research")
        for entry in selected_research:
            lines.append(
                f"- {entry.get('category_label', '')} — {entry.get('title', '')}\n"
                f"  {entry.get('description', '')}"
            )
    if experience:
        lines.append("\n### Experience")
        for emp_key, block in experience.items():
            label = employer_label_map.get(emp_key, emp_key)
            lines.append(f"\n**{label}**")
            for b in block.get("bullets") or []:
                lines.append(f"- {b.get('text', '')}")
    if technical_skills:
        lines.append("\n### Technical Skills")
        for bucket, text in technical_skills.items():
            lines.append(f"- {bucket}: {text}")
    return "\n".join(lines)


async def _generate_publications(
    *,
    call_llm,
    profile_data: dict,
    plan_text: str,
    job_text: str,
    trace_id: str | None = None,
) -> list[dict]:
    """Pick 2-3 most job-relevant publications via a small LLM call."""
    publications = (profile_data.get("complete_profile") or {}).get("publications") or []
    if not publications:
        return []

    publications_text = _format_publications_catalog(publications)
    messages = build_publications_prompt(publications_text, plan_text, job_text)
    try:
        result = await call_llm(
            PUBLICATIONS_SYSTEM,
            messages,
            temperature=0.2,
            max_tokens=1500,
        )
    except Exception:
        logger.exception("Publications selection failed")
        return []
    _trace_dump(trace_id, "03_publications", PUBLICATIONS_SYSTEM, messages, result)
    picks = result.get("publications") or []
    cleaned: list[dict] = []
    for p in picks:
        if not isinstance(p, dict):
            continue
        citation = (p.get("citation") or "").strip()
        if not citation:
            continue
        cleaned.append(
            {
                "citation": citation,
                "publication_id": p.get("publication_id") or "",
            }
        )
    return cleaned


def _format_publications_catalog(publications: list[dict]) -> str:
    lines: list[str] = []
    for p in publications:
        pid = p.get("id") or (p.get("title") or "unknown")[:30].lower().replace(" ", "-")
        fa = " [FIRST AUTHOR]" if p.get("first_author") else ""
        authors = ", ".join(p.get("authors") or [])
        lines.append(
            f"- ID: {pid}\n"
            f'  {authors}. "{p.get("title", "")}" '
            f"{p.get('venue', '')}, {p.get('year', '')}.{fa}"
        )
    return "\n".join(lines)


def _build_awards(profile_data: dict) -> str:
    awards = profile_data.get("awards") or []
    if not awards:
        return ""
    if isinstance(awards, str):
        return awards
    return " · ".join(awards[:5])


def _augment_with_writing_memory(grounding_text: str, writing_memory_text: str) -> str:
    """Append writing_memory style rules below content_memory grounding."""
    if not writing_memory_text:
        return grounding_text
    if grounding_text:
        return f"{grounding_text}\n\n{writing_memory_text}"
    return writing_memory_text
