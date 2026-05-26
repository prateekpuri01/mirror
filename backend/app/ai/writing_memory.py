"""Writing memory: prompt formatting and LLM-based rule extraction.

This module handles both the *read* path (formatting rules for prompt injection)
and the *write* path (extracting new rules from user edit diffs).
"""

import json
import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import EXTRACTION_MODEL, RESUME_MODEL, get_openai_client
from app.services import writing_memory_service as svc

logger = logging.getLogger(__name__)

# Approximate token budget for the memory block injected into prompts.
# ~1.3 tokens per word; 500 tokens ≈ 385 words.
_MAX_WORDS = 380


async def format_writing_memory(session: AsyncSession, domain: str) -> str:
    """Format active rules as a prompt section.

    Returns an empty string when no rules qualify (zero-cost cold start).
    """
    rules = await svc.get_active_rules(session, domain)
    if not rules:
        return ""

    lines = [
        "## Your Writing Preferences (learned from your past edits — ALWAYS apply these)",
    ]
    word_count = len(lines[0].split())

    for rule in rules:
        bullet = f"- {rule.rule_text}"
        # Include one example if available
        examples = rule.examples_json or []
        if examples:
            ex = examples[0]
            if ex.get("before") and ex.get("after"):
                bullet += f'  (e.g., "{ex["before"]}" → "{ex["after"]}")'

        bullet_words = len(bullet.split())
        if word_count + bullet_words > _MAX_WORDS:
            break
        lines.append(bullet)
        word_count += bullet_words

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Write path: extract rules from edit diffs
# ---------------------------------------------------------------------------

_EXTRACT_SYSTEM = """\
You analyze edits a user made to AI-generated text to extract reusable writing preferences.

## Classification guidance
- UNIVERSAL: word substitutions, structural preferences (shorter bullets, different sentence \
patterns), tone shifts, banned phrases, formatting rules. These apply regardless of the target job.
- JOB_SPECIFIC: adding domain keywords, emphasizing skills matching a specific job description, \
reframing for a specific role type, adding industry-specific terminology.
- When in doubt, classify as JOB_SPECIFIC (safer to under-learn than over-learn).

## Importance assessment
For each rule, judge how important this correction is:
- CRITICAL: obvious error the AI should never repeat — wrong category (soft skills in technical \
list), factual mistakes, embarrassing content, items that clearly don't belong. These need to be \
fixed immediately.
- STRONG: clear stylistic preference — consistent word substitutions, structural choices, tone \
adjustments that any reasonable person would agree improve the text.
- MILD: possible preference that might be job-specific or subjective — could go either way.

## Output
JSON array (may be empty if the edit is purely job-specific or trivial):
[{
  "rule_text": "concise natural-language rule the AI should follow in all future writing",
  "category": "word_choice|tone|structure|content|formatting",
  "scope": "universal|job_specific",
  "importance": "critical|strong|mild",
  "examples": [{"before": "original phrasing", "after": "user's preferred phrasing"}]
}]

Output ONLY valid JSON. No markdown fences. Empty array [] if nothing generalizable."""

# Map importance to initial confidence — critical corrections activate immediately
_IMPORTANCE_CONFIDENCE = {
    "critical": 0.85,
    "strong": 0.65,
    "mild": 0.5,
}


async def extract_rules_from_diff(
    old_value: Any,
    new_value: Any,
    section_path: str,
    job_title: str = "",
    company: str = "",
    domain: str = "resume",
    user_instruction: str = "",
) -> list[dict]:
    """Call a fast model to extract generalizable writing rules from a diff.

    Returns a list of candidate rule dicts (may be empty).
    """
    old_text = old_value if isinstance(old_value, str) else json.dumps(old_value, indent=2)
    new_text = new_value if isinstance(new_value, str) else json.dumps(new_value, indent=2)

    # Skip trivial or identical edits
    if old_text.strip() == new_text.strip():
        return []

    user_parts = [
        f"Section: {section_path}",
        f"Job context: {job_title} at {company}" if job_title or company else "",
    ]
    if user_instruction:
        user_parts.append(f"User's instruction: {user_instruction}")
    user_parts.extend(
        [
            f"\nBEFORE:\n{old_text}",
            f"\nAFTER:\n{new_text}",
        ]
    )
    user_content = "\n".join(p for p in user_parts if p)

    try:
        client = get_openai_client()
        response = await client.chat.completions.create(
            model=EXTRACTION_MODEL,
            messages=[
                {"role": "system", "content": _EXTRACT_SYSTEM},
                {"role": "user", "content": user_content},
            ],
            max_completion_tokens=4096,
            reasoning_effort="minimal",
        )
        text = (response.choices[0].message.content or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        if not text:
            logger.warning(
                "Writing memory extraction returned empty content (finish_reason=%s)",
                response.choices[0].finish_reason,
            )
            return []
        rules = json.loads(text)
        if not isinstance(rules, list):
            return []
        return rules
    except Exception:
        logger.exception("Failed to extract writing memory rules from diff")
        return []


async def persist_extracted_rules(
    session: AsyncSession,
    rules: list[dict],
    source_type: str,
    source_job_id: uuid.UUID | None,
    domain: str,
) -> int:
    """Persist extracted rules with importance-based confidence and smart dedup.

    Returns the number of new rules created (vs reinforcements/merges).
    """
    if not rules:
        return 0

    existing = await svc.get_all_rules(session, domain=domain, is_active=True)
    created = 0

    for candidate in rules:
        rule_text = candidate.get("rule_text", "").strip()
        if not rule_text or len(rule_text) < 5:
            continue

        category = candidate.get("category", "content")
        scope = candidate.get("scope", "job_specific")  # safe default
        importance = candidate.get("importance", "mild")
        examples = candidate.get("examples")
        initial_confidence = _IMPORTANCE_CONFIDENCE.get(importance, 0.5)

        # Two-stage dedup: fast keyword check first, then LLM for close matches
        matched = _find_duplicate_keyword(rule_text, existing)
        if not matched and existing:
            matched = await _find_duplicate_llm(rule_text, existing)

        if matched:
            await svc.reinforce_rule(session, matched.id)
            # If the new observation has higher importance, upgrade confidence
            if matched.confidence < initial_confidence:
                matched.confidence = initial_confidence
                await session.flush()
            # Merge examples
            if examples and matched.examples_json is not None:
                merged_examples = list(matched.examples_json) + examples
                matched.examples_json = merged_examples[:5]  # cap at 5
                await session.flush()
            logger.info(
                "Reinforced existing rule %s (importance=%s): %s",
                matched.id,
                importance,
                matched.rule_text[:60],
            )
        else:
            new_rule = await svc.add_rule(
                session,
                domain=domain,
                rule_text=rule_text,
                category=category,
                scope=scope,
                examples_json=examples,
                source_type=source_type,
                source_job_id=source_job_id,
                confidence=initial_confidence,
            )
            existing.append(new_rule)
            created += 1
            logger.info(
                "Created new writing memory rule (importance=%s, conf=%.2f): %s",
                importance,
                initial_confidence,
                rule_text[:80],
            )

    return created


def _find_duplicate_keyword(rule_text: str, existing: list) -> Any:
    """Fast keyword-overlap dedup. Returns matching rule or None."""
    rule_words = set(rule_text.lower().split())
    best_match = None
    best_overlap = 0.0

    for rule in existing:
        existing_words = set(rule.rule_text.lower().split())
        if not existing_words:
            continue
        overlap = len(rule_words & existing_words) / max(len(rule_words), len(existing_words))
        if overlap > best_overlap:
            best_overlap = overlap
            best_match = rule

    # Threshold: >60% word overlap = definite duplicate
    return best_match if best_overlap > 0.6 else None


async def _find_duplicate_llm(rule_text: str, existing: list) -> Any:
    """LLM-based semantic dedup against existing rules. Returns matching rule or None."""
    if not existing:
        return None

    # Only check the most recent/relevant rules to keep costs down
    candidates = existing[:15]
    numbered = "\n".join(f"{i + 1}. {r.rule_text}" for i, r in enumerate(candidates))

    prompt = f"""I have a list of existing writing rules and a new candidate rule. Tell me: does the new rule mean the same thing as any existing rule?

EXISTING RULES:
{numbered}

NEW RULE: {rule_text}

Answer with ONLY a JSON object. Examples:
- If the new rule matches existing rule 1: {{"match": 1}}
- If the new rule matches existing rule 3: {{"match": 3}}
- If it does not match any: {{"match": null}}

Your answer:"""

    try:
        client = get_openai_client()
        response = await client.chat.completions.create(
            model=RESUME_MODEL,  # full model for reliable JSON output
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=512,
            reasoning_effort="low",
        )
        text = (response.choices[0].message.content or "").strip()
        logger.info("LLM dedup raw response: %r", text)

        if not text:
            return None

        # Try JSON parse first
        try:
            if text.startswith("```"):
                text = text.split("\n", 1)[1]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()
            result = json.loads(text)
            idx = result.get("match")
            if idx is not None and 1 <= idx <= len(candidates):
                logger.info("LLM dedup matched new rule to existing rule #%d", idx)
                return candidates[idx - 1]
        except (json.JSONDecodeError, TypeError, AttributeError):
            # Fallback: look for a plain number in the response
            import re

            match = re.search(r"\d+", text)
            if match:
                idx = int(match.group())
                if 1 <= idx <= len(candidates):
                    logger.info("LLM dedup matched new rule to existing rule #%d", idx)
                    return candidates[idx - 1]
    except Exception:
        logger.warning("LLM dedup failed, treating as no match", exc_info=True)

    return None


async def extract_and_learn(
    session: AsyncSession,
    old_value: Any,
    new_value: Any,
    section_path: str,
    job_id: uuid.UUID | None,
    domain: str = "resume",
    job_title: str = "",
    company: str = "",
    user_instruction: str = "",
) -> int:
    """End-to-end: extract rules from a diff and persist them. Returns count of new rules."""
    rules = await extract_rules_from_diff(
        old_value,
        new_value,
        section_path,
        job_title=job_title,
        company=company,
        domain=domain,
        user_instruction=user_instruction,
    )
    if not rules:
        return 0
    return await persist_extracted_rules(session, rules, f"auto_{domain}_edit", job_id, domain)


# ---------------------------------------------------------------------------
# Consolidation: merge similar rules to keep memory lean
# ---------------------------------------------------------------------------

_CONSOLIDATE_SYSTEM = """\
You distill a user's writing-style preferences into the smallest, clearest set \
that preserves every distinct preference.

## What these rules are for
The rules below were extracted automatically from a user's hand-edits to \
AI-generated resumes. The agent injects them into every future resume \
generation as instructions. Right now the set has accumulated noise: rules \
that say the same thing in different words, near-duplicates from repeated \
similar edits, and overlapping advice that the LLM only needs once.

## Your goal
Produce a compact, distilled set that's easier to scan and easier for an LLM \
to apply. Specifically:

1. **Merge true duplicates.** Two rules are duplicates if a future generation \
   that satisfies one would also satisfy the other.
2. **Merge near-duplicates** that target the same concern (e.g. "use 'built' \
   instead of 'utilized'" and "avoid the word 'utilized'") into a single \
   rule with the broader, more general phrasing — but DON'T over-generalize \
   to the point of losing specifics.
3. **Don't merge rules that look similar but address different concerns.** \
   "Use shorter sentences" and "use active voice" both touch sentence form \
   but enforce different things — keep separate.
4. **Don't fabricate new preferences.** The merged rule must follow from \
   what the constituent rules say.

## When merging, write the merged rule to be:
- **Concrete and actionable** — an LLM should know exactly what to do.
- **Specific over abstract** — "Lead bullets with active verbs (Built, \
  Replaced, Shipped)" beats "Use active voice."
- **Free of hedging** — no "consider", "try to", "where appropriate."

## Output
JSON only (no markdown fences):
{
  "groups": [
    {
      "indices": [1, 4, 7],
      "merged_rule_text": "...",
      "merged_category": "word_choice|tone|structure|content|formatting",
      "reasoning": "one short sentence on why these belong together"
    }
  ]
}

ONLY include groups with 2+ indices (rules being merged). Skip singletons — \
unique rules stay as-is. If nothing is worth merging, return {"groups": []}."""


async def consolidate_rules(session: AsyncSession, domain: str) -> dict:
    """Merge near-duplicate rules for a domain.

    Operates only on the user-visible set: ``scope='universal'`` and
    ``is_active=True``. Returns a structured report so callers can show the
    user exactly what was merged.
    """
    rules = await svc.get_all_rules(
        session,
        domain=domain,
        scope="universal",
        is_active=True,
    )
    if len(rules) <= 3:
        return {
            "merged_count": 0,
            "groups_merged": 0,
            "before": len(rules),
            "after": len(rules),
            "merges": [],
        }

    numbered = "\n".join(f"{i + 1}. {r.rule_text}" for i, r in enumerate(rules))
    user_content = (
        f"You have {len(rules)} active universal rules in domain '{domain}'. "
        f"The system caps active rules at 30 per domain, so a tighter set is genuinely better.\n\n"
        f"## Current rules\n{numbered}\n\n"
        f"Distill into the smallest set that preserves every distinct preference. "
        f"Output JSON with a 'groups' array — only include groups of 2+ to be merged."
    )

    try:
        client = get_openai_client()
        response = await client.chat.completions.create(
            model=EXTRACTION_MODEL,
            messages=[
                {"role": "system", "content": _CONSOLIDATE_SYSTEM},
                {"role": "user", "content": user_content},
            ],
            max_completion_tokens=4096,
            reasoning_effort="minimal",
        )
        text = (response.choices[0].message.content or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        if not text:
            logger.warning(
                "Consolidation returned empty content (finish_reason=%s)",
                response.choices[0].finish_reason,
            )
            return {
                "merged_count": 0,
                "groups_merged": 0,
                "before": len(rules),
                "after": len(rules),
                "merges": [],
            }
        parsed = json.loads(text)
        # Accept either the new {"groups": [...]} or the old [{...}, ...] shape
        if isinstance(parsed, dict):
            groups = parsed.get("groups") or []
        elif isinstance(parsed, list):
            groups = parsed
        else:
            groups = []
    except Exception:
        logger.exception("Consolidation LLM call failed")
        return {
            "merged_count": 0,
            "groups_merged": 0,
            "before": len(rules),
            "after": len(rules),
            "merges": [],
        }

    merged_count = 0
    groups_merged = 0
    merges_report: list[dict] = []
    for group in groups:
        indices = group.get("indices", [])
        if len(indices) < 2:
            continue

        merged_text = group.get("merged_rule_text", "")
        merged_cat = group.get("merged_category", "content")
        if not merged_text:
            continue

        originals = []
        for idx in indices:
            if 1 <= idx <= len(rules):
                originals.append(rules[idx - 1])

        if len(originals) < 2:
            continue

        all_examples: list[dict] = []
        total_count = 0
        max_confidence = 0.0
        for orig in originals:
            total_count += orig.occurrence_count
            max_confidence = max(max_confidence, orig.confidence)
            if orig.examples_json:
                all_examples.extend(orig.examples_json[:2])

        await svc.add_rule(
            session,
            domain=domain,
            rule_text=merged_text,
            category=merged_cat,
            scope="universal",
            examples_json=all_examples[:5] if all_examples else None,
            source_type="consolidation",
            confidence=max_confidence,
        )

        for orig in originals:
            orig.is_active = False
            merged_count += 1
        groups_merged += 1

        merges_report.append(
            {
                "merged_rule_text": merged_text,
                "merged_from": [orig.rule_text for orig in originals],
                "reasoning": group.get("reasoning", ""),
            }
        )

    if merged_count:
        await session.flush()
        logger.info(
            "Consolidated %d rules into %d merged rules for domain=%s",
            merged_count,
            groups_merged,
            domain,
        )

    return {
        "merged_count": merged_count,
        "groups_merged": groups_merged,
        "before": len(rules),
        "after": len(rules) - merged_count + groups_merged,
        "merges": merges_report,
    }
