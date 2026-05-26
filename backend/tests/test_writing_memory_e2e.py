"""End-to-end tests for the writing memory system.

Simulates realistic long-running user sessions: editing resumes across
multiple jobs, accumulating memory, and verifying that learned preferences
affect future generation.

These tests create real DB rows (jobs, documents, memory rules) and exercise
the full pipeline: edit → extract → reinforce → inject → verify.

LLM extraction calls are mocked by default. Tests that need the real LLM
are marked with @pytest.mark.llm and skipped unless --run-llm is passed.
"""

import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.ai import writing_memory as wm_ai
from app.ai.resume_prompts import build_revision_prompt, build_single_shot_system
from app.config import settings
from app.models.documents import DocType, Document
from app.models.jobs import Job, JobSource, JobStatus
from app.models.writing_memory import WritingMemory
from app.services import writing_memory_service as svc
from app.services.document_service import _get_nested

# ---------------------------------------------------------------------------
# Test DB infrastructure (NullPool avoids asyncpg connection state leaks)
# ---------------------------------------------------------------------------

_test_engine = create_async_engine(settings.database_url, echo=False, poolclass=NullPool)
_test_session = async_sessionmaker(_test_engine, class_=AsyncSession, expire_on_commit=False)


@asynccontextmanager
async def fresh_session():
    async with _test_session() as s:
        yield s


async def _cleanup(session: AsyncSession):
    """Remove test fixtures. Runs before and after each test."""
    await session.execute(text("DELETE FROM writing_memory"))
    await session.execute(text("DELETE FROM documents WHERE name LIKE 'E2E Test%'"))
    await session.execute(text("DELETE FROM jobs WHERE url LIKE 'https://e2e-test-%'"))
    await session.commit()


@pytest_asyncio.fixture
async def session():
    async with fresh_session() as s:
        await _cleanup(s)
        yield s
        await _cleanup(s)


# ---------------------------------------------------------------------------
# Helpers: create test jobs and resume documents
# ---------------------------------------------------------------------------

_SAMPLE_RESUME_JSON = {
    "tagline": "AI Systems · NLP · Research",
    "summary": "Builds AI tools that domain experts actually adopt.",
    "selected_research": [
        {
            "category_label": "NLP Systems",
            "title": "MUSE: LLM-Assisted Qualitative Research",
            "description": "Replaced manual coding workflow with AI-assisted system.",
            "accomplishment_id": "rand-muse",
        }
    ],
    "experience": {
        "rand": {
            "bullets": [
                {
                    "text": "Utilized Python frameworks to build NLP pipeline for qualitative analysis.",
                    "accomplishment_ids": ["rand-muse"],
                },
                {
                    "text": "Leveraged cloud infrastructure to deploy ML models at scale.",
                    "accomplishment_ids": ["rand-infra"],
                },
                {
                    "text": "Architected a retrieval-augmented generation system for document Q&A.",
                    "accomplishment_ids": ["rand-rag"],
                },
            ]
        }
    },
    "technical_skills": {
        "ai_systems": "PyTorch, LangChain, RAG",
        "data_science": "Python, R, statistics",
        "engineering": "AWS, Docker, PostgreSQL",
    },
    "awards": "Best Paper Award · Dean's Fellowship",
}


async def _create_test_job(session: AsyncSession, suffix: str = "A") -> Job:
    """Create a minimal job row for testing."""
    job = Job(
        title=f"Research Scientist {suffix}",
        company=f"TestCorp {suffix}",
        description=f"We are looking for an AI research scientist to join team {suffix}.",
        url=f"https://e2e-test-{uuid.uuid4().hex[:8]}",
        source=JobSource.manual,
        status=JobStatus.new,
    )
    session.add(job)
    await session.commit()
    return job


async def _create_test_resume(
    session: AsyncSession,
    job: Job,
    resume_json: dict | None = None,
) -> Document:
    """Create a resume document linked to a job."""
    doc = Document(
        job_id=job.id,
        doc_type=DocType.resume,
        name=f"E2E Test Resume for {job.company}",
        content_json=resume_json or dict(_SAMPLE_RESUME_JSON),
        content_markdown="# Test Resume",
        version=1,
    )
    session.add(doc)
    await session.commit()
    return doc


def _mock_extraction_response(rules: list[dict]) -> MagicMock:
    """Build a mock OpenAI response that returns the given rules."""
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content=json.dumps(rules)))]
    return mock_resp


# ===========================================================================
# Group 1: Multi-Job Edit Accumulation
#
# The core scenario: editing the same pattern across multiple jobs should
# build memory that eventually affects prompt injection.
# ===========================================================================


@pytest.mark.asyncio
async def test_multi_job_accumulation_builds_confidence(session):
    """Editing the same word across 3 jobs should reinforce the rule to injection threshold."""
    job_a = await _create_test_job(session, "A")
    job_b = await _create_test_job(session, "B")
    job_c = await _create_test_job(session, "C")

    await _create_test_resume(session, job_a)
    await _create_test_resume(session, job_b)
    await _create_test_resume(session, job_c)

    # The rule that will be "extracted" from each edit
    extracted_rule = [
        {
            "rule_text": "Use 'built' instead of 'utilized'",
            "category": "word_choice",
            "scope": "universal",
            "examples": [{"before": "Utilized Python", "after": "Built Python tools"}],
        }
    ]

    with patch.object(wm_ai, "get_openai_client") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(
            return_value=_mock_extraction_response(extracted_rule)
        )

        # Edit 1: Job A — rule created at 0.5
        await wm_ai.extract_and_learn(
            session,
            "Utilized Python frameworks to build NLP pipeline.",
            "Built Python tools for NLP pipeline.",
            "experience.rand.bullets.0",
            job_a.id,
            domain="resume",
            job_title=job_a.title,
            company=job_a.company,
        )
        await session.commit()

    rules = await svc.get_all_rules(session, domain="resume", is_active=True)
    assert len(rules) == 1
    rule = rules[0]
    assert rule.confidence == pytest.approx(0.5, abs=0.01)
    assert rule.occurrence_count == 1

    # Should NOT be injected yet (below 0.7)
    memory_text = await wm_ai.format_writing_memory(session, "resume")
    assert memory_text == ""

    with patch.object(wm_ai, "get_openai_client") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(
            return_value=_mock_extraction_response(extracted_rule)
        )

        # Edit 2: Job B — rule reinforced to 0.65
        await wm_ai.extract_and_learn(
            session,
            "Utilized cloud services for deployment.",
            "Built cloud deployment pipeline.",
            "experience.rand.bullets.1",
            job_b.id,
            domain="resume",
            job_title=job_b.title,
            company=job_b.company,
        )
        await session.commit()

    rules = await svc.get_all_rules(session, domain="resume", is_active=True)
    assert rules[0].occurrence_count == 2
    assert rules[0].confidence == pytest.approx(0.65, abs=0.01)

    # Still below threshold
    memory_text = await wm_ai.format_writing_memory(session, "resume")
    assert memory_text == ""

    with patch.object(wm_ai, "get_openai_client") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(
            return_value=_mock_extraction_response(extracted_rule)
        )

        # Edit 3: Job C — rule reinforced to 0.8, NOW above threshold
        await wm_ai.extract_and_learn(
            session,
            "Utilized ML libraries to train models.",
            "Built ML training pipeline.",
            "experience.rand.bullets.2",
            job_c.id,
            domain="resume",
            job_title=job_c.title,
            company=job_c.company,
        )
        await session.commit()

    rules = await svc.get_all_rules(session, domain="resume", is_active=True)
    assert rules[0].occurrence_count == 3
    assert rules[0].confidence >= 0.7

    # NOW it should be injected
    memory_text = await wm_ai.format_writing_memory(session, "resume")
    assert "built" in memory_text.lower()
    assert "utilized" in memory_text.lower()


@pytest.mark.asyncio
async def test_accumulated_memory_appears_in_generated_prompt(session):
    """Rules that cross the confidence threshold actually show up in resume prompts."""
    # Create a rule above threshold
    await svc.add_rule(
        session,
        domain="resume",
        rule_text="Never use 'leveraged' — use direct verbs like 'built' or 'shipped'",
        category="word_choice",
        scope="universal",
        source_type="explicit_user",
        confidence=0.9,
    )
    await svc.add_rule(
        session,
        domain="resume",
        rule_text="Keep experience bullets under 20 words per sentence",
        category="structure",
        scope="universal",
        source_type="auto_inline_edit",
        confidence=0.8,
    )
    await session.commit()

    # Verify the prompt actually contains these rules
    memory_text = await wm_ai.format_writing_memory(session, "resume")
    assert "leveraged" in memory_text
    assert "20 words" in memory_text

    # Verify it lands in the actual system prompt
    profile_data = {"work_history": [], "skills": {}}
    system_prompt = build_single_shot_system(profile_data, memory_text=memory_text)
    assert "Your Writing Preferences" in system_prompt
    assert "leveraged" in system_prompt
    assert "20 words" in system_prompt


# ===========================================================================
# Group 2: Confidence Lifecycle
#
# Tests the full arc: creation → reinforcement → injection → decay → deactivation
# ===========================================================================


@pytest.mark.asyncio
async def test_confidence_lifecycle_full_arc(session):
    """A rule should go from birth to injection to decay to deactivation."""
    # Step 1: Auto-extracted rule starts at 0.5
    rule = await svc.add_rule(
        session,
        domain="resume",
        rule_text="Avoid trailing participial phrases",
        category="structure",
        scope="universal",
        source_type="auto_inline_edit",
    )
    await session.commit()
    assert rule.confidence == 0.5
    assert rule.is_active is True

    # Not yet injected
    mem = await wm_ai.format_writing_memory(session, "resume")
    assert "participial" not in mem

    # Step 2: First reinforcement → 0.65
    await svc.reinforce_rule(session, rule.id)
    await session.commit()
    fetched = await svc.get_rule(session, rule.id)
    assert fetched.confidence == pytest.approx(0.65, abs=0.01)

    mem = await wm_ai.format_writing_memory(session, "resume")
    assert "participial" not in mem  # still below 0.7

    # Step 3: Second reinforcement → 0.8 (above threshold!)
    await svc.reinforce_rule(session, rule.id)
    await session.commit()
    fetched = await svc.get_rule(session, rule.id)
    assert fetched.confidence >= 0.7

    mem = await wm_ai.format_writing_memory(session, "resume")
    assert "participial" in mem  # NOW injected

    # Step 4: Simulate 100 days passing with no reinforcement
    await session.execute(
        text("UPDATE writing_memory SET updated_at = :ts WHERE id = :id"),
        {"ts": datetime.now(UTC) - timedelta(days=100), "id": rule.id},
    )
    await session.commit()

    decayed, deactivated = await svc.decay_stale_rules(session)
    await session.commit()
    assert decayed >= 1

    fetched = await svc.get_rule(session, rule.id)
    # Confidence should have dropped by 0.1
    assert fetched.confidence < 0.8

    # Step 5: Multiple decay cycles bring it below floor → deactivated
    for _ in range(5):
        await session.execute(
            text("UPDATE writing_memory SET updated_at = :ts WHERE id = :id"),
            {"ts": datetime.now(UTC) - timedelta(days=100), "id": rule.id},
        )
        await session.commit()
        await svc.decay_stale_rules(session)
        await session.commit()

    fetched = await svc.get_rule(session, rule.id)
    assert fetched.is_active is False

    # No longer injected
    mem = await wm_ai.format_writing_memory(session, "resume")
    assert "participial" not in mem


# ===========================================================================
# Group 3: "Remember This" Round-Trip
#
# User tells the chat agent to remember a preference → it persists →
# it appears in the next resume prompt.
# ===========================================================================


@pytest.mark.asyncio
async def test_remember_this_creates_high_confidence_rule(session):
    """Explicit user preferences should be created at high confidence and immediately injectable."""
    from app.ai.resume_agent import save_preference

    state = {
        "user_message": "always keep bullets under 15 words",
        "resume_json": _SAMPLE_RESUME_JSON,
        "job_context": "Research Scientist at TestCorp",
        "profile_text": "Test profile",
        "chat_history": [],
        "section_context": None,
        "generation_log": None,
        "strategic_plan": None,
        "writing_memory_text": "",
        "intent": "remember_preference",
        "target_section_path": None,
        "target_section_value": None,
        "response_text": "",
        "updated_json": None,
        "updated_section_path": None,
        "_new_preference": None,
    }

    # Mock the LLM call inside save_preference
    json.dumps(
        {
            "rule_text": "Keep experience bullets under 15 words per sentence",
            "category": "structure",
        }
    )
    with patch.object(wm_ai, "get_openai_client"):
        # save_preference uses _call_openai_json from resume_agent
        from app.ai import resume_agent

        with patch.object(
            resume_agent,
            "_call_openai_json",
            new=AsyncMock(
                return_value={
                    "rule_text": "Keep experience bullets under 15 words per sentence",
                    "category": "structure",
                }
            ),
        ):
            result = await save_preference(state)

    # The node should return the preference for the router to persist
    assert result["_new_preference"] is not None
    assert "15 words" in result["_new_preference"]["rule_text"]
    assert result["updated_json"] is None  # no resume edit

    # Simulate what the chat router does: persist the rule
    pref = result["_new_preference"]
    rule = await svc.add_rule(
        session,
        domain="resume",
        rule_text=pref["rule_text"],
        category=pref.get("category", "content"),
        scope="universal",
        source_type="explicit_user",
    )
    await session.commit()

    # Should be immediately injectable (confidence=0.8)
    assert rule.confidence == 0.8
    mem = await wm_ai.format_writing_memory(session, "resume")
    assert "15 words" in mem

    # Should appear in an actual resume prompt
    profile_data = {"work_history": [], "skills": {}}
    system_prompt = build_single_shot_system(profile_data, memory_text=mem)
    assert "15 words" in system_prompt


@pytest.mark.asyncio
async def test_remember_this_confirmation_message(session):
    """The save_preference node should return a friendly confirmation."""
    from app.ai import resume_agent

    state = {
        "user_message": "never use the word architected",
        "resume_json": _SAMPLE_RESUME_JSON,
        "job_context": "",
        "profile_text": "",
        "chat_history": [],
        "section_context": None,
        "generation_log": None,
        "strategic_plan": None,
        "writing_memory_text": "",
        "intent": "remember_preference",
        "target_section_path": None,
        "target_section_value": None,
        "response_text": "",
        "updated_json": None,
        "updated_section_path": None,
        "_new_preference": None,
    }

    with patch.object(
        resume_agent,
        "_call_openai_json",
        new=AsyncMock(
            return_value={
                "rule_text": "Never use the word 'architected'",
                "category": "word_choice",
            }
        ),
    ):
        result = await resume_agent.save_preference(state)

    assert (
        "remember" in result["response_text"].lower()
        or "preference" in result["response_text"].lower()
    )
    assert result["updated_json"] is None


# ===========================================================================
# Group 4: Domain Isolation
#
# Resume-domain rules must NOT leak into answer drafting prompts, and vice versa.
# ===========================================================================


@pytest.mark.asyncio
async def test_resume_rules_do_not_leak_into_answer_domain(session):
    """Rules in the 'resume' domain should not appear in answer-domain memory."""
    await svc.add_rule(
        session,
        domain="resume",
        rule_text="Never use 'leveraged' in resume bullets",
        category="word_choice",
        scope="universal",
        source_type="explicit_user",
    )
    await session.commit()

    resume_mem = await wm_ai.format_writing_memory(session, "resume")
    answer_mem = await wm_ai.format_writing_memory(session, "answer")

    assert "leveraged" in resume_mem
    assert answer_mem == ""  # no answer rules exist


@pytest.mark.asyncio
async def test_answer_rules_do_not_leak_into_resume_domain(session):
    """Rules in the 'answer' domain should not appear in resume-domain memory."""
    await svc.add_rule(
        session,
        domain="answer",
        rule_text="Always lead with a specific project example",
        category="structure",
        scope="universal",
        source_type="explicit_user",
    )
    await session.commit()

    resume_mem = await wm_ai.format_writing_memory(session, "resume")
    answer_mem = await wm_ai.format_writing_memory(session, "answer")

    assert resume_mem == ""
    assert "specific project example" in answer_mem


@pytest.mark.asyncio
async def test_both_domains_independent(session):
    """Both domains can have rules simultaneously without cross-contamination."""
    await svc.add_rule(
        session,
        domain="resume",
        rule_text="Resume-only rule: avoid passive voice",
        category="tone",
        scope="universal",
        source_type="explicit_user",
    )
    await svc.add_rule(
        session,
        domain="answer",
        rule_text="Answer-only rule: be conversational",
        category="tone",
        scope="universal",
        source_type="explicit_user",
    )
    await session.commit()

    resume_mem = await wm_ai.format_writing_memory(session, "resume")
    answer_mem = await wm_ai.format_writing_memory(session, "answer")

    assert "passive voice" in resume_mem
    assert "conversational" not in resume_mem

    assert "conversational" in answer_mem
    assert "passive voice" not in answer_mem


@pytest.mark.asyncio
async def test_job_specific_rules_never_injected(session):
    """Job-specific rules should exist in the DB but never appear in prompts."""
    await svc.add_rule(
        session,
        domain="resume",
        rule_text="Emphasize ML pipeline experience for this ML Engineer role",
        category="content",
        scope="job_specific",
        source_type="auto_inline_edit",
        confidence=1.0,  # even at max confidence
    )
    await session.commit()

    mem = await wm_ai.format_writing_memory(session, "resume")
    assert mem == ""  # job_specific rules are never injected

    # But it exists in the DB for audit trail
    all_rules = await svc.get_all_rules(session, domain="resume")
    assert len(all_rules) == 1
    assert all_rules[0].scope == "job_specific"


# ===========================================================================
# Group 5: Cap + Consolidation Under Load
#
# Simulates heavy usage: many edits, cap enforcement, and consolidation.
# ===========================================================================


@pytest.mark.asyncio
async def test_cap_enforced_at_30_active_rules(session):
    """Creating more than 30 active rules per domain should deactivate the weakest."""
    for i in range(32):
        await svc.add_rule(
            session,
            domain="resume",
            rule_text=f"Rule number {i}: some writing preference",
            category="word_choice",
            scope="universal",
            source_type="auto_inline_edit",
            confidence=0.5 + (i * 0.01),
        )
    await session.commit()

    active = await svc.get_all_rules(session, domain="resume", is_active=True)
    assert len(active) <= svc.MAX_RULES_PER_DOMAIN


@pytest.mark.asyncio
async def test_prompt_stays_within_token_budget_under_load(session):
    """Even with 30 active high-confidence rules, the formatted output respects the word cap."""
    for i in range(30):
        await svc.add_rule(
            session,
            domain="resume",
            rule_text=f"Writing preference {i}: " + "avoid this very specific pattern " * 5,
            category="word_choice",
            scope="universal",
            source_type="explicit_user",
            confidence=0.9,
        )
    await session.commit()

    mem = await wm_ai.format_writing_memory(session, "resume")
    word_count = len(mem.split())
    # Should be capped at roughly _MAX_WORDS (380) + small margin for header
    assert word_count <= wm_ai._MAX_WORDS + 50
    # But should contain some rules (not empty)
    assert "Writing preference" in mem


@pytest.mark.asyncio
async def test_consolidation_reduces_rule_count(session):
    """Consolidation should merge similar rules and reduce active count."""
    # Create a bunch of similar word-choice rules
    word_rules = [
        "Don't use 'leveraged' in bullets",
        "Avoid the word 'utilized'",
        "Never say 'architected'",
        "Replace 'spearheaded' with direct verbs",
        "Don't use 'synergized'",
        "Avoid 'paradigm-shifting'",
    ]
    for text in word_rules:
        await svc.add_rule(
            session,
            domain="resume",
            rule_text=text,
            category="word_choice",
            scope="universal",
            source_type="auto_inline_edit",
            confidence=0.8,
        )
    await session.commit()

    active_before = await svc.get_all_rules(session, domain="resume", is_active=True)
    assert len(active_before) == 6

    # Mock the consolidation LLM to merge all into one group
    mock_response = json.dumps(
        [
            {
                "indices": [1, 2, 3, 4, 5, 6],
                "merged_rule_text": "Avoid corporate buzzwords: leveraged, utilized, architected, spearheaded, synergized, paradigm-shifting. Use direct verbs instead.",
                "merged_category": "word_choice",
            },
        ]
    )

    with patch.object(wm_ai, "get_openai_client") as mock_client:
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content=mock_response))]
        mock_client.return_value.chat.completions.create = AsyncMock(return_value=mock_resp)

        merged_count = await wm_ai.consolidate_rules(session, "resume")

    await session.commit()
    assert merged_count == 6  # all 6 originals deactivated

    active_after = await svc.get_all_rules(session, domain="resume", is_active=True)
    assert len(active_after) == 1
    assert "corporate buzzwords" in active_after[0].rule_text


@pytest.mark.asyncio
async def test_extraction_plus_dedup_across_many_edits(session):
    """Simulating 10 edits that all trigger the same rule should reinforce, not duplicate."""
    extracted_rule = [
        {
            "rule_text": "Prefer shorter sentences in bullet points",
            "category": "structure",
            "scope": "universal",
        }
    ]

    with patch.object(wm_ai, "get_openai_client") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(
            return_value=_mock_extraction_response(extracted_rule)
        )

        for i in range(10):
            job = await _create_test_job(session, f"Dedup-{i}")
            await wm_ai.extract_and_learn(
                session,
                f"Built a complex system that handles multiple data sources and produces comprehensive outputs for stakeholder review (edit {i}).",
                f"Built a data pipeline for stakeholder reports (edit {i}).",
                "experience.rand.bullets.0",
                job.id,
                domain="resume",
            )
            await session.commit()

    # Should have exactly 1 rule, reinforced 10 times
    all_rules = await svc.get_all_rules(session, domain="resume", is_active=True)
    assert len(all_rules) == 1
    assert all_rules[0].occurrence_count == 10
    # After 9 reinforcements from 0.5: min(1.0, 0.5 + 9*0.15) = 1.0
    assert all_rules[0].confidence == 1.0


# ===========================================================================
# Group 6: Inline Edit → Memory → Prompt Injection (integration)
#
# Simulates the actual endpoint flow: PATCH section → background learning →
# memory appears in next resume generation prompt.
# ===========================================================================


@pytest.mark.asyncio
async def test_inline_edit_to_prompt_injection_pipeline(session):
    """Full pipeline: inline edit creates a rule that later appears in prompts."""
    job = await _create_test_job(session, "Pipeline")
    await _create_test_resume(session, job)

    old_value = "Utilized Python frameworks to build NLP pipeline for qualitative analysis."
    new_value = "Built Python tools for NLP-powered qualitative analysis."

    extracted_rule = [
        {
            "rule_text": "Replace 'utilized X to build Y' with 'built Y' — more direct",
            "category": "word_choice",
            "scope": "universal",
            "examples": [{"before": old_value, "after": new_value}],
        }
    ]

    with patch.object(wm_ai, "get_openai_client") as mock_client:
        mock_client.return_value.chat.completions.create = AsyncMock(
            return_value=_mock_extraction_response(extracted_rule)
        )

        # Simulate what the documents router does
        count = await wm_ai.extract_and_learn(
            session,
            old_value,
            new_value,
            "experience.rand.bullets.0",
            job.id,
            domain="resume",
            job_title=job.title,
            company=job.company,
        )
        await session.commit()

    assert count == 1

    # Rule exists but at 0.5 — not yet in prompts
    mem = await wm_ai.format_writing_memory(session, "resume")
    assert mem == ""

    # Boost to injection threshold (simulating repeated edits)
    rules = await svc.get_all_rules(session, domain="resume", is_active=True)
    await svc.reinforce_rule(session, rules[0].id)
    await svc.reinforce_rule(session, rules[0].id)
    await session.commit()

    # Now it should appear in prompts
    mem = await wm_ai.format_writing_memory(session, "resume")
    assert "utilized" in mem.lower()

    # And in the actual system prompt
    profile_data = {"work_history": [], "skills": {}}
    system_prompt = build_single_shot_system(profile_data, memory_text=mem)
    assert "Your Writing Preferences" in system_prompt

    # And in the revision prompt
    messages = build_revision_prompt(
        current_resume_json='{"summary": "test"}',
        instruction="Make it shorter",
        profile_text="Profile",
        job_text="Job",
        memory_text=mem,
    )
    assert "utilized" in messages[0]["content"].lower()
