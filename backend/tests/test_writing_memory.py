"""Comprehensive test suite for the writing memory system.

Tests are organized into groups covering storage, dedup, cap enforcement,
confidence decay, consolidation, retrieval/formatting, extraction, prompt
injection, API endpoints, and edge cases.

Uses pytest-asyncio with real database sessions where needed, and mocks
for LLM calls.
"""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.models.writing_memory import WritingMemory
from app.services import writing_memory_service as svc
from app.ai import writing_memory as wm_ai


# ---------------------------------------------------------------------------
# Test-specific engine with NullPool to avoid connection state leaks
# ---------------------------------------------------------------------------

_test_engine = create_async_engine(settings.database_url, echo=False, poolclass=NullPool)
_test_session_factory = async_sessionmaker(_test_engine, class_=AsyncSession, expire_on_commit=False)


@asynccontextmanager
async def fresh_session():
    """Create a fresh session with a guaranteed-clean connection."""
    async with _test_session_factory() as s:
        yield s


async def _cleanup():
    async with fresh_session() as s:
        await s.execute(text("DELETE FROM writing_memory"))
        await s.commit()


@pytest_asyncio.fixture
async def session():
    await _cleanup()
    async with fresh_session() as s:
        yield s
    await _cleanup()


@pytest_asyncio.fixture
async def clean_memory():
    """No-op — cleanup is handled by the session fixture."""
    pass


async def _make_rule(
    session: AsyncSession,
    *,
    domain: str = "resume",
    rule_text: str = "Test rule",
    category: str = "word_choice",
    scope: str = "universal",
    source_type: str = "explicit_user",
    confidence: float | None = None,
    is_active: bool = True,
    examples_json: list | None = None,
    source_job_id: uuid.UUID | None = None,
) -> WritingMemory:
    """Helper to quickly create a rule."""
    rule = await svc.add_rule(
        session,
        domain=domain,
        rule_text=rule_text,
        category=category,
        scope=scope,
        source_type=source_type,
        source_job_id=source_job_id,
        confidence=confidence,
        examples_json=examples_json,
    )
    if not is_active:
        rule.is_active = False
    if confidence is not None:
        rule.confidence = confidence
    await session.commit()
    return rule


# ===========================================================================
# Group 1: Memory Storage (CRUD)
# ===========================================================================


@pytest.mark.asyncio
async def test_create_rule_basic(session, clean_memory):
    rule = await svc.add_rule(
        session,
        domain="resume",
        rule_text="Never use 'leveraged'",
        category="word_choice",
        scope="universal",
        source_type="explicit_user",
    )
    assert rule.domain == "resume"
    assert rule.rule_text == "Never use 'leveraged'"
    assert rule.confidence == 0.8  # explicit_user default
    assert rule.occurrence_count == 1
    assert rule.is_active is True


@pytest.mark.asyncio
async def test_create_rule_auto_extracted(session, clean_memory):
    rule = await svc.add_rule(
        session,
        domain="resume",
        rule_text="Keep bullets concise",
        category="structure",
        source_type="auto_inline_edit",
    )
    assert rule.confidence == 0.5  # auto default, not 0.8


@pytest.mark.asyncio
async def test_create_rule_with_examples(session, clean_memory):
    examples = [{"before": "Utilized Python", "after": "Built with Python"}]
    rule = await svc.add_rule(
        session,
        domain="resume",
        rule_text="Avoid 'utilized'",
        category="word_choice",
        source_type="explicit_user",
        examples_json=examples,
    )
    assert rule.examples_json == examples
    # Re-fetch from DB
    fetched = await svc.get_rule(session, rule.id)
    assert fetched.examples_json == examples


@pytest.mark.asyncio
async def test_get_active_rules_filters_by_domain(session, clean_memory):
    await _make_rule(session, domain="resume", rule_text="Resume rule", confidence=0.9)
    await _make_rule(session, domain="answer", rule_text="Answer rule", confidence=0.9)

    resume_rules = await svc.get_active_rules(session, "resume")
    assert len(resume_rules) == 1
    assert resume_rules[0].rule_text == "Resume rule"


@pytest.mark.asyncio
async def test_get_active_rules_filters_by_confidence(session, clean_memory):
    rule = await _make_rule(session, confidence=0.5, rule_text="Low conf")
    rules = await svc.get_active_rules(session, "resume", min_confidence=0.7)
    assert len(rules) == 0

    rule.confidence = 0.8
    await session.flush()
    rules = await svc.get_active_rules(session, "resume", min_confidence=0.7)
    assert len(rules) == 1


@pytest.mark.asyncio
async def test_get_active_rules_excludes_inactive(session, clean_memory):
    await _make_rule(session, confidence=1.0, is_active=False, rule_text="Inactive")
    rules = await svc.get_active_rules(session, "resume")
    assert len(rules) == 0


@pytest.mark.asyncio
async def test_get_active_rules_excludes_job_specific(session, clean_memory):
    await _make_rule(session, scope="job_specific", confidence=0.9, rule_text="Job specific")
    rules = await svc.get_active_rules(session, "resume")
    assert len(rules) == 0


@pytest.mark.asyncio
async def test_reinforce_rule(session, clean_memory):
    rule = await _make_rule(session, confidence=0.5, rule_text="To reinforce")
    original_count = rule.occurrence_count

    await svc.reinforce_rule(session, rule.id)
    await svc.reinforce_rule(session, rule.id)

    fetched = await svc.get_rule(session, rule.id)
    assert fetched.occurrence_count == original_count + 2
    assert fetched.confidence == pytest.approx(0.5 + 0.15 * 2, abs=0.01)


@pytest.mark.asyncio
async def test_reinforce_caps_at_one(session, clean_memory):
    rule = await _make_rule(session, confidence=0.95, rule_text="High conf")
    await svc.reinforce_rule(session, rule.id)
    fetched = await svc.get_rule(session, rule.id)
    assert fetched.confidence == 1.0


@pytest.mark.asyncio
async def test_deactivate_rule(session, clean_memory):
    rule = await _make_rule(session, rule_text="To deactivate")
    await svc.deactivate_rule(session, rule.id)
    fetched = await svc.get_rule(session, rule.id)
    assert fetched.is_active is False
    rules = await svc.get_active_rules(session, "resume")
    assert all(r.id != rule.id for r in rules)


@pytest.mark.asyncio
async def test_update_rule_partial(session, clean_memory):
    rule = await _make_rule(session, scope="job_specific", rule_text="To update")
    await svc.update_rule(session, rule.id, scope="universal")
    fetched = await svc.get_rule(session, rule.id)
    assert fetched.scope == "universal"
    assert fetched.rule_text == "To update"  # unchanged


# ===========================================================================
# Group 2: Deduplication
# ===========================================================================


@pytest.mark.asyncio
async def test_dedup_exact_match(session, clean_memory):
    """Exact duplicate text should reinforce, not create new row."""
    r1 = await _make_rule(session, rule_text="Never use 'leveraged'", confidence=0.5)
    original_count = r1.occurrence_count

    # Persist via the extraction flow with same text
    rules = [{"rule_text": "Never use 'leveraged'", "category": "word_choice", "scope": "universal"}]
    created = await wm_ai.persist_extracted_rules(
        session, rules, "auto_inline_edit", None, "resume"
    )
    assert created == 0  # no new rule
    fetched = await svc.get_rule(session, r1.id)
    assert fetched.occurrence_count == original_count + 1


@pytest.mark.asyncio
async def test_dedup_different_rules_not_merged(session, clean_memory):
    await _make_rule(session, rule_text="Keep bullets under 20 words", confidence=0.5)
    rules = [{"rule_text": "Never use passive voice", "category": "tone", "scope": "universal"}]
    created = await wm_ai.persist_extracted_rules(
        session, rules, "auto_inline_edit", None, "resume"
    )
    assert created == 1


@pytest.mark.asyncio
async def test_dedup_across_domains_independent(session, clean_memory):
    await _make_rule(session, domain="resume", rule_text="Never use 'leveraged'", confidence=0.5)
    rules = [{"rule_text": "Never use 'leveraged'", "category": "word_choice", "scope": "universal"}]
    # Different domain — should create new
    created = await wm_ai.persist_extracted_rules(
        session, rules, "auto_answer_edit", None, "answer"
    )
    assert created == 1


# ===========================================================================
# Group 3: Hard Cap Enforcement
# ===========================================================================


@pytest.mark.asyncio
async def test_cap_enforcement_deactivates_weakest(session, clean_memory):
    # Create 30 rules
    for i in range(30):
        await _make_rule(
            session,
            rule_text=f"Rule {i}",
            confidence=0.7 + (i * 0.01),
        )

    # 31st rule should trigger cap enforcement
    await _make_rule(session, rule_text="Rule 30 (new)", confidence=0.5)

    all_active = await svc.get_all_rules(session, domain="resume", is_active=True)
    assert len(all_active) <= svc.MAX_RULES_PER_DOMAIN


@pytest.mark.asyncio
async def test_cap_per_domain(session, clean_memory):
    for i in range(30):
        await _make_rule(session, domain="resume", rule_text=f"Resume rule {i}", confidence=0.8)
    for i in range(30):
        await _make_rule(session, domain="answer", rule_text=f"Answer rule {i}", confidence=0.8)

    # Adding 31st resume rule should only affect resume domain
    await _make_rule(session, domain="resume", rule_text="Extra resume", confidence=0.5)

    resume_active = await svc.get_all_rules(session, domain="resume", is_active=True)
    answer_active = await svc.get_all_rules(session, domain="answer", is_active=True)
    assert len(resume_active) <= svc.MAX_RULES_PER_DOMAIN
    assert len(answer_active) == 30


# ===========================================================================
# Group 4: Confidence Decay
# ===========================================================================


@pytest.mark.asyncio
async def test_decay_stale_rules(session, clean_memory):
    rule = await _make_rule(session, confidence=0.8, rule_text="Stale rule")
    # Manually set updated_at to 100 days ago
    cutoff = datetime.now(timezone.utc) - timedelta(days=100)
    await session.execute(
        text("UPDATE writing_memory SET updated_at = :ts WHERE id = :id"),
        {"ts": cutoff, "id": rule.id},
    )
    await session.flush()

    decayed, deactivated = await svc.decay_stale_rules(session)
    assert decayed >= 1
    fetched = await svc.get_rule(session, rule.id)
    assert fetched.confidence == pytest.approx(0.7, abs=0.01)


@pytest.mark.asyncio
async def test_decay_deactivates_below_threshold(session, clean_memory):
    rule = await _make_rule(session, confidence=0.35, rule_text="Nearly dead")
    cutoff = datetime.now(timezone.utc) - timedelta(days=100)
    await session.execute(
        text("UPDATE writing_memory SET updated_at = :ts WHERE id = :id"),
        {"ts": cutoff, "id": rule.id},
    )
    await session.flush()

    decayed, deactivated = await svc.decay_stale_rules(session)
    assert deactivated >= 1
    fetched = await svc.get_rule(session, rule.id)
    assert fetched.is_active is False


@pytest.mark.asyncio
async def test_decay_ignores_recently_used(session, clean_memory):
    rule = await _make_rule(session, confidence=0.8, rule_text="Fresh rule")
    # updated_at is now, should not decay

    decayed, _ = await svc.decay_stale_rules(session)
    fetched = await svc.get_rule(session, rule.id)
    assert fetched.confidence == 0.8


@pytest.mark.asyncio
async def test_decay_ignores_inactive(session, clean_memory):
    rule = await _make_rule(session, confidence=0.5, is_active=False, rule_text="Dead rule")
    cutoff = datetime.now(timezone.utc) - timedelta(days=200)
    await session.execute(
        text("UPDATE writing_memory SET updated_at = :ts WHERE id = :id"),
        {"ts": cutoff, "id": rule.id},
    )
    await session.flush()

    decayed, _ = await svc.decay_stale_rules(session)
    fetched = await svc.get_rule(session, rule.id)
    assert fetched.confidence == 0.5  # unchanged


# ===========================================================================
# Group 5: Consolidation (mock LLM)
# ===========================================================================


@pytest.mark.asyncio
async def test_consolidate_merges_similar(session, clean_memory):
    r1 = await _make_rule(session, rule_text="Don't use leveraged", confidence=0.8)
    r2 = await _make_rule(session, rule_text="Don't use utilized", confidence=0.9)
    r3 = await _make_rule(session, rule_text="Don't use architected", confidence=0.7)
    # Need at least 6 rules for consolidation to trigger (threshold is 5)
    for i in range(3):
        await _make_rule(session, rule_text=f"Keep rule {i}", confidence=0.8)

    # get_all_rules returns updated_at DESC, so most recently created first:
    # [Keep rule 2, Keep rule 1, Keep rule 0, Don't use architected, Don't use utilized, Don't use leveraged]
    # Indices 4,5,6 correspond to the "Don't use" rules
    mock_response = json.dumps([
        {"indices": [1], "merged_rule_text": "Keep rule 2", "merged_category": "content"},
        {"indices": [2], "merged_rule_text": "Keep rule 1", "merged_category": "content"},
        {"indices": [3], "merged_rule_text": "Keep rule 0", "merged_category": "content"},
        {
            "indices": [4, 5, 6],
            "merged_rule_text": "Avoid corporate verbs: leveraged, utilized, architected",
            "merged_category": "word_choice",
        },
    ])

    with patch.object(wm_ai, "get_openai_client") as mock_client:
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content=mock_response))]
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_resp)

        merged = await wm_ai.consolidate_rules(session, "resume")

    assert merged == 3  # 3 originals deactivated
    await session.commit()
    # Check originals are deactivated — re-fetch from DB
    for r in [r1, r2, r3]:
        fetched = await svc.get_rule(session, r.id)
        assert fetched.is_active is False


@pytest.mark.asyncio
async def test_consolidate_preserves_unrelated(session, clean_memory):
    await _make_rule(session, rule_text="Don't use leveraged", confidence=0.8)
    await _make_rule(session, rule_text="Keep bullets under 20 words", confidence=0.8)
    for i in range(4):
        await _make_rule(session, rule_text=f"Filler rule {i}", confidence=0.8)

    mock_response = json.dumps([
        {"indices": [1], "merged_rule_text": "Don't use leveraged", "merged_category": "word_choice"},
        {"indices": [2], "merged_rule_text": "Keep bullets under 20 words", "merged_category": "structure"},
        {"indices": [3], "merged_rule_text": "Filler rule 0", "merged_category": "content"},
        {"indices": [4], "merged_rule_text": "Filler rule 1", "merged_category": "content"},
        {"indices": [5], "merged_rule_text": "Filler rule 2", "merged_category": "content"},
        {"indices": [6], "merged_rule_text": "Filler rule 3", "merged_category": "content"},
    ])

    with patch.object(wm_ai, "get_openai_client") as mock_client:
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content=mock_response))]
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_resp)

        merged = await wm_ai.consolidate_rules(session, "resume")

    assert merged == 0  # all single-element groups


# ===========================================================================
# Group 6: Retrieval & Prompt Formatting
# ===========================================================================


@pytest.mark.asyncio
async def test_format_writing_memory_empty(session, clean_memory):
    result = await wm_ai.format_writing_memory(session, "resume")
    assert result == ""


@pytest.mark.asyncio
async def test_format_writing_memory_basic(session, clean_memory):
    await _make_rule(session, rule_text="Rule A", confidence=0.9)
    await _make_rule(session, rule_text="Rule B", confidence=0.8)
    await _make_rule(session, rule_text="Rule C", confidence=0.7)

    result = await wm_ai.format_writing_memory(session, "resume")
    assert "Your Writing Preferences" in result
    assert "Rule A" in result
    assert "Rule B" in result
    assert "Rule C" in result


@pytest.mark.asyncio
async def test_format_writing_memory_orders_by_confidence(session, clean_memory):
    await _make_rule(session, rule_text="Low", confidence=0.7)
    await _make_rule(session, rule_text="High", confidence=0.9)
    await _make_rule(session, rule_text="Mid", confidence=0.8)

    result = await wm_ai.format_writing_memory(session, "resume")
    lines = result.split("\n")
    rule_lines = [l for l in lines if l.startswith("- ")]
    # High confidence first
    assert "High" in rule_lines[0]
    assert "Mid" in rule_lines[1]
    assert "Low" in rule_lines[2]


@pytest.mark.asyncio
async def test_format_writing_memory_respects_domain(session, clean_memory):
    await _make_rule(session, domain="resume", rule_text="Resume only", confidence=0.9)
    await _make_rule(session, domain="answer", rule_text="Answer only", confidence=0.9)

    result = await wm_ai.format_writing_memory(session, "resume")
    assert "Resume only" in result
    assert "Answer only" not in result


@pytest.mark.asyncio
async def test_format_writing_memory_skips_low_confidence(session, clean_memory):
    await _make_rule(session, rule_text="Low conf", confidence=0.5)
    await _make_rule(session, rule_text="High conf", confidence=0.9)

    result = await wm_ai.format_writing_memory(session, "resume")
    assert "High conf" in result
    assert "Low conf" not in result


@pytest.mark.asyncio
async def test_format_writing_memory_skips_job_specific(session, clean_memory):
    await _make_rule(session, scope="universal", rule_text="Universal", confidence=0.9)
    await _make_rule(session, scope="job_specific", rule_text="Specific", confidence=0.9)

    result = await wm_ai.format_writing_memory(session, "resume")
    assert "Universal" in result
    assert "Specific" not in result


@pytest.mark.asyncio
async def test_format_writing_memory_includes_examples(session, clean_memory):
    examples = [{"before": "Utilized tools", "after": "Built tools"}]
    await _make_rule(
        session, rule_text="Avoid utilized", confidence=0.9, examples_json=examples
    )

    result = await wm_ai.format_writing_memory(session, "resume")
    assert "Utilized tools" in result
    assert "Built tools" in result


@pytest.mark.asyncio
async def test_format_writing_memory_token_cap(session, clean_memory):
    # Create 40 rules with long text
    for i in range(40):
        await _make_rule(
            session,
            rule_text=f"This is a verbose rule number {i} that contains many words to test the token budget cap mechanism " * 3,
            confidence=0.9,
        )

    result = await wm_ai.format_writing_memory(session, "resume")
    word_count = len(result.split())
    assert word_count <= wm_ai._MAX_WORDS + 50  # small margin for header


# ===========================================================================
# Group 7: Extraction from Diffs (mock LLM)
# ===========================================================================


@pytest.mark.asyncio
async def test_extract_from_word_substitution():
    mock_response = json.dumps([
        {
            "rule_text": "Use 'built' instead of 'utilized'",
            "scope": "universal",
            "category": "word_choice",
            "examples": [{"before": "Utilized Python", "after": "Built with Python"}],
        }
    ])

    with patch.object(wm_ai, "get_openai_client") as mock_client:
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content=mock_response))]
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_resp)

        rules = await wm_ai.extract_rules_from_diff(
            "Utilized Python frameworks to process data",
            "Built Python tools that process data",
            "experience.rand.bullets.0",
        )

    assert len(rules) == 1
    assert rules[0]["scope"] == "universal"
    assert rules[0]["category"] == "word_choice"


@pytest.mark.asyncio
async def test_extract_from_job_specific_edit():
    mock_response = json.dumps([
        {
            "rule_text": "Emphasize ML pipeline experience",
            "scope": "job_specific",
            "category": "content",
        }
    ])

    with patch.object(wm_ai, "get_openai_client") as mock_client:
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content=mock_response))]
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_resp)

        rules = await wm_ai.extract_rules_from_diff(
            "Built AI systems for research",
            "Built ML pipelines for production inference",
            "experience.rand.bullets.0",
            job_title="ML Engineer",
            company="Acme Corp",
        )

    assert len(rules) == 1
    assert rules[0]["scope"] == "job_specific"


@pytest.mark.asyncio
async def test_extract_returns_empty_for_trivial_edit():
    """Identical text (after strip) returns empty without calling LLM."""
    rules = await wm_ai.extract_rules_from_diff(
        "Built systems",
        "Built systems",
        "summary",
    )
    assert rules == []


@pytest.mark.asyncio
async def test_extract_returns_empty_on_llm_error():
    with patch.object(wm_ai, "get_openai_client") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(side_effect=Exception("API error"))

        rules = await wm_ai.extract_rules_from_diff(
            "Old text", "New text", "summary"
        )
    assert rules == []


@pytest.mark.asyncio
async def test_extract_multiple_rules_from_one_diff():
    mock_response = json.dumps([
        {"rule_text": "Rule one", "category": "word_choice", "scope": "universal"},
        {"rule_text": "Rule two", "category": "structure", "scope": "universal"},
    ])

    with patch.object(wm_ai, "get_openai_client") as mock_client:
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content=mock_response))]
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_resp)

        rules = await wm_ai.extract_rules_from_diff(
            "Long old text with issues",
            "Shorter new text fixed",
            "experience.rand.bullets.0",
        )
    assert len(rules) == 2


# ===========================================================================
# Group 8: Prompt Injection Correctness
# ===========================================================================


def test_resume_generation_includes_memory():
    """build_single_shot_system with memory_text injects it into the prompt."""
    from app.ai.resume_prompts import build_single_shot_system

    profile_data = {"work_history": [], "skills": {}}
    memory = "## Your Writing Preferences\n- Never use 'leveraged'"

    result = build_single_shot_system(profile_data, memory_text=memory)
    assert "Your Writing Preferences" in result
    assert "Never use 'leveraged'" in result


def test_resume_generation_without_memory():
    from app.ai.resume_prompts import build_single_shot_system

    profile_data = {"work_history": [], "skills": {}}
    result = build_single_shot_system(profile_data)
    assert "Your Writing Preferences" not in result


def test_revision_prompt_includes_memory():
    from app.ai.resume_prompts import build_revision_prompt

    messages = build_revision_prompt(
        current_resume_json='{"summary": "test"}',
        instruction="Make it shorter",
        profile_text="Profile text",
        job_text="Job text",
        memory_text="## Your Writing Preferences\n- Be concise",
    )
    content = messages[0]["content"]
    assert "Your Writing Preferences" in content
    assert "Be concise" in content


def test_revision_prompt_without_memory():
    from app.ai.resume_prompts import build_revision_prompt

    messages = build_revision_prompt(
        current_resume_json='{"summary": "test"}',
        instruction="Make it shorter",
        profile_text="Profile text",
        job_text="Job text",
    )
    content = messages[0]["content"]
    assert "Your Writing Preferences" not in content


# ===========================================================================
# Group 9: _find_duplicate
# ===========================================================================


def test_find_duplicate_high_overlap():
    """Rules with >60% word overlap should match."""
    class FakeRule:
        def __init__(self, text, id=None):
            self.rule_text = text
            self.id = id or uuid.uuid4()

    existing = [FakeRule("Never use 'leveraged' in resume bullets")]
    result = wm_ai._find_duplicate_keyword("Never use 'leveraged' in resume writing", existing)
    assert result is not None


def test_find_duplicate_no_overlap():
    class FakeRule:
        def __init__(self, text, id=None):
            self.rule_text = text
            self.id = id or uuid.uuid4()

    existing = [FakeRule("Keep bullets under 20 words")]
    result = wm_ai._find_duplicate_keyword("Never use passive voice", existing)
    assert result is None


# ===========================================================================
# Group 10: Edge Cases
# ===========================================================================


@pytest.mark.asyncio
async def test_rule_with_empty_examples(session, clean_memory):
    rule = await _make_rule(session, rule_text="No examples", examples_json=[])
    result = await wm_ai.format_writing_memory(session, "resume")
    # Should not crash
    if rule.confidence >= svc.MIN_INJECTION_CONFIDENCE:
        assert "No examples" in result


@pytest.mark.asyncio
async def test_unicode_in_rules(session, clean_memory):
    rule = await _make_rule(
        session,
        rule_text="Avoid 'résumé' — use 'resume'",
        confidence=0.9,
    )
    fetched = await svc.get_rule(session, rule.id)
    assert "résumé" in fetched.rule_text

    result = await wm_ai.format_writing_memory(session, "resume")
    assert "résumé" in result


@pytest.mark.asyncio
async def test_very_long_rule_text(session, clean_memory):
    long_text = "x " * 250  # 500 characters
    rule = await _make_rule(session, rule_text=long_text, confidence=0.9)
    fetched = await svc.get_rule(session, rule.id)
    assert len(fetched.rule_text) == len(long_text)


@pytest.mark.asyncio
async def test_persist_skips_short_rule_text(session, clean_memory):
    """Rules with very short text (<5 chars) are skipped."""
    rules = [{"rule_text": "hi", "category": "tone", "scope": "universal"}]
    created = await wm_ai.persist_extracted_rules(
        session, rules, "auto_inline_edit", None, "resume"
    )
    assert created == 0


@pytest.mark.asyncio
async def test_persist_defaults_scope_to_job_specific(session, clean_memory):
    """When scope is missing from extracted rule, default to job_specific."""
    rules = [{"rule_text": "Some ambiguous rule without scope", "category": "content"}]
    created = await wm_ai.persist_extracted_rules(
        session, rules, "auto_inline_edit", None, "resume"
    )
    assert created == 1
    all_rules = await svc.get_all_rules(session, domain="resume")
    assert all_rules[0].scope == "job_specific"
