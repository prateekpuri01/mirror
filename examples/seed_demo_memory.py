"""Seed three fictional 'past resumes' into content_memory + writing_memory.

Why: cold-start UX. Without seeded data, a first-time user sees an empty
"Past versions ▼" dropdown and an empty Writing Style tab — the very
features that make Mirror feel different from a generic AI resume tool.
This script inserts demo data so those features are populated on first
boot.

What it inserts:

  - 3 ``Document`` rows (one per fictional past resume), with
    ``job_id=NULL`` and a ``name`` prefixed ``Demo past resume — `` so
    they're easy to identify and prune.
  - ~30 ``content_memory`` rows tied to those Documents:
      • 3 research_description rows per resume (one per featured accomplishment)
      • 3 experience_bullets_set rows per resume (one per employer)
      • 3 skill_bucket rows per resume (ai_systems / data_science / engineering)
      • 1 summary row per resume
      • 1 tagline row per resume
  - 5 ``writing_memory`` rules with ``source_type='demo_seed'`` so the
    Writing Style tab is non-empty.

Idempotent: checks for any existing demo Document rows before inserting.
Pass ``--reset`` to delete all demo rows first.

Usage:
    docker compose exec api python examples/seed_demo_memory.py
    docker compose exec api python examples/seed_demo_memory.py --reset

Anchors to the fictional Sam Rivera profile in
docs/profile.yaml.example + docs/profile_complete.yaml.example. If
those files have been replaced with the user's real data, the script
will still run but the grounding examples will be Sam Rivera's prose
on top of the user's own accomplishment IDs — useful for inspecting
the system but not stylistically consistent with the user's voice. In
that case, prune the demo rows (see examples/README.md).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from pathlib import Path

# Make ``app`` importable across both layouts: the host repo (where the
# package lives at ``backend/app``) and the api container (where the
# package is at ``/app/app``, since the Dockerfile copies ``backend/*``
# into ``/app/``).
_HERE = Path(__file__).resolve().parent
_CANDIDATES = [
    _HERE.parent / "backend",   # host: examples/ → repo/backend/
    _HERE.parent,               # container: /app/examples/ → /app/
]
for _root in _CANDIDATES:
    if (_root / "app" / "__init__.py").exists():
        if str(_root) not in sys.path:
            sys.path.insert(0, str(_root))
        break

from sqlalchemy import delete, select  # noqa: E402

from app.database import async_session  # noqa: E402
from app.models import (  # noqa: E402
    ContentMemory,
    Document,
    DocType,
    UserProfile,
    WritingMemory,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("seed_demo_memory")


DEMO_DOC_PREFIX = "Demo past resume — "
DEMO_RULE_SOURCE = "demo_seed"


# ---------------------------------------------------------------------------
# Fictional past-resume data
#
# Each entry mimics the final state of a hand-tuned resume Sam Rivera wrote
# for a particular role. The voice across the three resumes is consistent
# (active verbs, outcome-led, no fabrication relative to the example
# accomplishments) but tuned for different role flavors.
#
# IDs match docs/profile_complete.yaml.example. Update both if you change
# accomplishment IDs in the example profile.
# ---------------------------------------------------------------------------

DEMO_RESUMES: list[dict] = [
    {
        "name": "Anthropic — Research Engineer",
        "job_context": {
            "job_title": "Research Engineer",
            "company_name": "Anthropic",
            "score_excerpt": '{"role_fit":{"hard_skills":{"matches":["LLM evaluation","RAG"]}}}',
        },
        "tagline": "LLM Evaluation · Production Pipelines · Research Engineering · Applied ML",
        "summary": (
            "Builds the evaluation infrastructure that gates model releases. "
            "Especially strong at translating research-team intuitions into "
            "production-grade harnesses with rigorous methodology — "
            "experimental design, golden-set curation, and judge-prompt rigor."
        ),
        "research": [
            {
                "id": "helio-eval-harness",
                "label": "EVALUATION INFRASTRUCTURE",
                "title": "Helio Eval Harness: Production LLM evaluation infrastructure",
                "description": (
                    "Designed and built the evaluation harness that now gates every "
                    "model release at Helio Labs, combining task-specific golden sets, "
                    "LLM-as-judge scoring, and a regression dashboard tied directly "
                    "to training runs. Cut eval-cycle wall time by ~6x versus the "
                    "prior ad-hoc setup and now catches roughly 80% of regressions "
                    "before they reach internal dogfood."
                ),
            },
            {
                "id": "helio-multi-turn-agent",
                "label": "AGENT DESIGN",
                "title": "Multi-turn refinement agent for content authoring",
                "description": (
                    "Shipped an agent that iteratively improves long-form writing by "
                    "treating user feedback as structured edit instructions rather "
                    "than free-form regeneration prompts. Cut tone-related rejections "
                    "in half post-launch and dropped median sessions-to-acceptance "
                    "from 4.2 to 2.1 turns by classifying feedback intent before "
                    "rewriting."
                ),
            },
            {
                "id": "brightline-triage",
                "label": "APPLIED NLP",
                "title": "Patient-message triage classifier",
                "description": (
                    "Replaced a long-standing rules-based triage system in a "
                    "regulated healthcare setting with an NLP classifier that "
                    "improved top-1 accuracy from 72% to 88% and halved calibration "
                    "error on rare-but-urgent categories. Owned data pipeline, "
                    "labeling protocol, model training, and the rollout plan that "
                    "kept clinical staff in the loop."
                ),
            },
        ],
        "experience": {
            "helio_labs": [
                {
                    "text": (
                        "Shipped the eval harness used at every model-release decision; "
                        "cut eval-cycle wall time ~6x and surfaced ~80% of regressions "
                        "before internal dogfood."
                    ),
                    "accomplishment_ids": ["helio-eval-harness"],
                },
                {
                    "text": (
                        "Designed a multi-turn refinement agent that classifies user "
                        "feedback before rewriting; cut tone-rejection rate by half and "
                        "halved median sessions-to-acceptance."
                    ),
                    "accomplishment_ids": ["helio-multi-turn-agent"],
                },
                {
                    "text": (
                        "Cut per-request inference cost 40% and P95 latency 25% via "
                        "caching, model tiering, and selective prompt caching — zero "
                        "quality regression across the eval gates."
                    ),
                    "accomplishment_ids": ["helio-cost-cutting"],
                },
            ],
            "brightline_health": [
                {
                    "text": (
                        "Replaced a long-standing rules-based triage system with an NLP "
                        "classifier; lifted top-1 accuracy 72% → 88% and halved "
                        "calibration error on rare-but-urgent categories."
                    ),
                    "accomplishment_ids": ["brightline-triage"],
                },
                {
                    "text": (
                        "Built a clinical-event feature store with point-in-time "
                        "correctness defaults, standardizing ~30 features across 4 ML "
                        "teams and cutting feature-engineering duplication ~3x."
                    ),
                    "accomplishment_ids": ["brightline-feature-store"],
                },
            ],
            "marlin_systems": [
                {
                    "text": (
                        "Rebuilt a fragile batch ETL flow as a real-time streaming "
                        "pipeline, cutting end-to-end latency from ~1 hour to ~15 seconds."
                    ),
                    "accomplishment_ids": ["marlin-realtime-pipeline"],
                },
            ],
        },
        "skills": {
            "ai_systems": "LLM evaluation, RAG pipelines, LLM-as-judge methodology, agent design, prompt engineering",
            "data_science": "Python, PyTorch, scikit-learn, NLP, calibration, statistical analysis",
            "engineering": "FastAPI, PostgreSQL, Docker, distributed pipelines, streaming systems",
        },
    },

    {
        "name": "Cohere — Lead Data Scientist",
        "job_context": {
            "job_title": "Lead Data Scientist",
            "company_name": "Cohere",
            "score_excerpt": '{"role_fit":{"hard_skills":{"matches":["production ML","cost optimization"]}}}',
        },
        "tagline": "Applied ML · Production Systems · Evaluation · Cross-Functional Delivery",
        "summary": (
            "Ships applied ML inside compliance-heavy and research-heavy "
            "environments. Especially strong at the connective tissue work — "
            "feature stores, evaluation harnesses, A/B frameworks — that turns "
            "research experiments into reliable product decisions."
        ),
        "research": [
            {
                "id": "helio-eval-harness",
                "label": "EVALUATION INFRASTRUCTURE",
                "title": "Helio Eval Harness: Production LLM evaluation infrastructure",
                "description": (
                    "Replaced an ad-hoc release-gating workflow with the canonical "
                    "evaluation harness across all of Helio's research teams within six "
                    "months of launch. The system pairs task-specific golden sets with "
                    "LLM-as-judge scoring and a regression dashboard wired directly to "
                    "training runs, cutting eval-cycle wall time by ~6x and catching "
                    "roughly 80% of regressions before internal dogfood."
                ),
            },
            {
                "id": "brightline-triage",
                "label": "REGULATED-DOMAIN ML",
                "title": "Patient-message triage classifier",
                "description": (
                    "Replaced a years-old hand-tuned rules-based triage system with an "
                    "NLP classifier in a regulated clinical setting, lifting top-1 "
                    "accuracy from 72% to 88% and halving calibration error on rare-"
                    "but-urgent categories. Delivering ML in this environment required "
                    "stakeholder trust-building alongside the technical work — owned "
                    "labeling protocol, model training, calibration, and rollout."
                ),
            },
            {
                "id": "helio-cost-cutting",
                "label": "COST OPTIMIZATION",
                "title": "Inference-cost reduction across the product surface",
                "description": (
                    "Cut per-request LLM inference cost by 40% and P95 latency by 25% "
                    "without quality regression by introducing caching, model tiering, "
                    "and selective prompt caching across the main product surface. "
                    "An A/B framework proved the no-regression claim across every "
                    "eval-harness gate."
                ),
            },
        ],
        "experience": {
            "helio_labs": [
                {
                    "text": (
                        "Built the eval harness now used at every model-release "
                        "decision; six-month adoption across all research teams."
                    ),
                    "accomplishment_ids": ["helio-eval-harness"],
                },
                {
                    "text": (
                        "Cut per-request inference cost 40% via caching, model tiering, "
                        "and selective prompt caching — zero quality regression."
                    ),
                    "accomplishment_ids": ["helio-cost-cutting"],
                },
                {
                    "text": (
                        "Designed a multi-turn refinement agent that classifies feedback "
                        "before rewriting; cut tone-rejection rate by half."
                    ),
                    "accomplishment_ids": ["helio-multi-turn-agent"],
                },
            ],
            "brightline_health": [
                {
                    "text": (
                        "Replaced a long-standing rules-based triage system with an NLP "
                        "classifier — accuracy 72% → 88%, calibration error halved on "
                        "the long tail."
                    ),
                    "accomplishment_ids": ["brightline-triage"],
                },
                {
                    "text": (
                        "Designed a clinical-event feature store with point-in-time "
                        "correctness defaults, standardizing features across four ML "
                        "teams and cutting duplication ~3x."
                    ),
                    "accomplishment_ids": ["brightline-feature-store"],
                },
                {
                    "text": (
                        "Owned data pipeline, labeling protocol, and rollout plan for "
                        "the triage classifier in a regulated environment requiring "
                        "clinical-staff trust-building."
                    ),
                    "accomplishment_ids": ["brightline-triage"],
                },
            ],
            "marlin_systems": [
                {
                    "text": (
                        "Rebuilt a brittle batch ETL flow as a real-time streaming "
                        "pipeline, cutting latency 1 hour → 15 seconds."
                    ),
                    "accomplishment_ids": ["marlin-realtime-pipeline"],
                },
            ],
        },
        "skills": {
            "ai_systems": "LLM evaluation, RAG, LLM-as-judge, prompt engineering, agent orchestration",
            "data_science": "Python, PyTorch, scikit-learn, NLP, A/B testing, calibration, feature engineering",
            "engineering": "FastAPI, PostgreSQL, Docker, distributed systems, streaming, REST APIs",
        },
    },

    {
        "name": "OpenAI — Forward Deployed Engineer",
        "job_context": {
            "job_title": "Forward Deployed Engineer",
            "company_name": "OpenAI",
            "score_excerpt": '{"role_fit":{"hard_skills":{"matches":["customer-facing","applied AI"]}}}',
        },
        "tagline": "Applied AI · Customer-Facing Engineering · Deployment · Evaluation",
        "summary": (
            "Ships applied AI systems in environments where stakeholder trust "
            "matters as much as model accuracy. Brings a track record of "
            "replacing fragile inherited systems — rules engines, batch ETLs, "
            "ad-hoc eval workflows — with sturdier ones that domain experts "
            "actually adopt."
        ),
        "research": [
            {
                "id": "helio-multi-turn-agent",
                "label": "AGENT DESIGN",
                "title": "Multi-turn refinement agent for content authoring",
                "description": (
                    "Shipped an agent that improves long-form writing through "
                    "iterative user feedback by classifying feedback intent ('tighten' "
                    "vs 'change angle' vs 'fix factual') and dispatching the right "
                    "sub-prompt. Cut tone-related rejections in half within the first "
                    "month after rollout and dropped median sessions-to-acceptance "
                    "from 4.2 to 2.1 turns."
                ),
            },
            {
                "id": "brightline-triage",
                "label": "DEPLOYED ML",
                "title": "Patient-message triage classifier",
                "description": (
                    "Replaced a years-old hand-tuned rules-based triage system with "
                    "an NLP classifier deployed in a regulated clinical setting. Top-1 "
                    "accuracy moved from 72% to 88% and calibration error halved on "
                    "rare-but-urgent categories. Delivering this required as much "
                    "trust-building with clinical staff as it did model work."
                ),
            },
            {
                "id": "helio-eval-harness",
                "label": "EVALUATION RIGOR",
                "title": "Helio Eval Harness: Production LLM evaluation infrastructure",
                "description": (
                    "Designed the evaluation harness that gates every model release "
                    "at Helio. The system combines task-specific golden sets, LLM-as-"
                    "judge scoring, and a regression dashboard tied to training runs "
                    "— used across all research teams within six months and catching "
                    "roughly 80% of regressions before internal dogfood."
                ),
            },
        ],
        "experience": {
            "helio_labs": [
                {
                    "text": (
                        "Designed and shipped the multi-turn refinement agent that "
                        "halved tone-rejection rate by routing user feedback through an "
                        "intent classifier before rewriting."
                    ),
                    "accomplishment_ids": ["helio-multi-turn-agent"],
                },
                {
                    "text": (
                        "Built the eval harness used across all of Helio's research "
                        "teams; gates every release decision."
                    ),
                    "accomplishment_ids": ["helio-eval-harness"],
                },
                {
                    "text": (
                        "Cut per-request inference cost 40% across the main product "
                        "surface — zero quality regression across the release gates."
                    ),
                    "accomplishment_ids": ["helio-cost-cutting"],
                },
            ],
            "brightline_health": [
                {
                    "text": (
                        "Replaced a hand-tuned rules-based triage system with an NLP "
                        "classifier in a regulated clinical setting; clinical-staff "
                        "trust-building required as much attention as the model itself."
                    ),
                    "accomplishment_ids": ["brightline-triage"],
                },
                {
                    "text": (
                        "Built a clinical-event feature store with point-in-time "
                        "correctness defaults; standardized features across 4 ML teams."
                    ),
                    "accomplishment_ids": ["brightline-feature-store"],
                },
            ],
            "marlin_systems": [
                {
                    "text": (
                        "Rebuilt a batch ETL flow as a real-time streaming pipeline; "
                        "1-hour latency dropped to ~15 seconds end-to-end."
                    ),
                    "accomplishment_ids": ["marlin-realtime-pipeline"],
                },
            ],
        },
        "skills": {
            "ai_systems": "LLM evaluation, RAG, prompt engineering, agent design, production LLM deployment",
            "data_science": "Python, PyTorch, scikit-learn, NLP, calibration, A/B testing",
            "engineering": "FastAPI, PostgreSQL, Docker, distributed systems, REST APIs",
        },
    },
]


DEMO_WRITING_RULES: list[dict] = [
    {
        "rule_text": "Lead bullets with active verbs (Built, Shipped, Replaced, Cut, Designed). Avoid passive constructions and capability lists.",
        "category": "structure",
        "examples_json": [{"before": "Was responsible for shipping X.", "after": "Shipped X."}],
    },
    {
        "rule_text": "Avoid 'leveraged'. Use 'used' or 'applied'.",
        "category": "word_choice",
        "examples_json": [{"before": "Leveraged scikit-learn to build…", "after": "Used scikit-learn to build…"}],
    },
    {
        "rule_text": "No participial tails (', demonstrating X', ', enabling Y'). Split into a new sentence or delete.",
        "category": "structure",
        "examples_json": [{"before": "Cut latency 25%, demonstrating production rigor.", "after": "Cut latency 25%."}],
    },
    {
        "rule_text": "Translate jargon metrics (e.g. 'Cohen's kappa = 0.71' → 'matched trained human coders, kappa 0.71') or drop them.",
        "category": "content",
        "examples_json": None,
    },
    {
        "rule_text": "Each technical skill belongs in EXACTLY ONE bucket — no skill duplicated across ai_systems / data_science / engineering.",
        "category": "formatting",
        "examples_json": None,
    },
]


# ---------------------------------------------------------------------------
# Insert / reset
# ---------------------------------------------------------------------------


async def _has_existing_demo(session) -> bool:
    result = await session.execute(
        select(Document.id).where(Document.name.like(f"{DEMO_DOC_PREFIX}%")).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def _reset(session) -> None:
    """Delete all rows previously inserted by this script."""
    # content_memory has FK on documents with ON DELETE CASCADE — wipe docs
    # and the rows go with them.
    result = await session.execute(
        select(Document.id).where(Document.name.like(f"{DEMO_DOC_PREFIX}%"))
    )
    doc_ids = [row[0] for row in result.all()]
    if doc_ids:
        await session.execute(delete(Document).where(Document.id.in_(doc_ids)))
        logger.info("Reset: removed %d demo Document row(s) (cascade drops content_memory)", len(doc_ids))
    res2 = await session.execute(
        delete(WritingMemory).where(WritingMemory.source_type == DEMO_RULE_SOURCE)
    )
    logger.info("Reset: removed %d demo WritingMemory row(s)", res2.rowcount or 0)


async def seed(reset: bool = False) -> None:
    async with async_session() as session:
        # Verify a profile exists so accomplishment_ids actually map to something.
        prof = (await session.execute(select(UserProfile).limit(1))).scalar_one_or_none()
        if prof is None:
            logger.error("No UserProfile in DB — copy docs/profile.yaml.example to docs/profile.yaml and restart the api first.")
            return

        if reset:
            await _reset(session)
            await session.commit()

        if await _has_existing_demo(session):
            logger.info("Demo rows already present — nothing to do. Use --reset to re-seed.")
            return

        # ---- Past resumes ----------------------------------------------------
        total_docs = 0
        total_memory = 0
        for resume in DEMO_RESUMES:
            doc = Document(
                job_id=None,
                doc_type=DocType.resume,
                name=f"{DEMO_DOC_PREFIX}{resume['name']}",
                content_markdown=None,
                content_docx_path=None,
                content_json={
                    "tagline": resume["tagline"],
                    "summary": resume["summary"],
                    "selected_research": [
                        {
                            "category_label": r["label"],
                            "title": r["title"],
                            "description": r["description"],
                            "accomplishment_id": r["id"],
                        }
                        for r in resume["research"]
                    ],
                    "experience": {
                        emp_key: {"bullets": bullets}
                        for emp_key, bullets in resume["experience"].items()
                    },
                    "technical_skills": resume["skills"],
                    "_demo_seed": True,
                },
                version=1,
            )
            session.add(doc)
            await session.flush()
            total_docs += 1
            jc = resume["job_context"]

            # research_description rows
            for entry in resume["research"]:
                session.add(ContentMemory(
                    entity_type="research_description",
                    entity_key=entry["id"],
                    source_doc_id=doc.id,
                    user_text=entry["description"],
                    job_context=jc,
                ))
                total_memory += 1

            # experience_bullets_set rows (one per employer in this resume)
            for emp_key, bullets in resume["experience"].items():
                session.add(ContentMemory(
                    entity_type="experience_bullets_set",
                    entity_key=emp_key,
                    source_doc_id=doc.id,
                    user_payload_json=bullets,
                    job_context=jc,
                ))
                total_memory += 1

            # skill_bucket rows
            for bucket, value in resume["skills"].items():
                session.add(ContentMemory(
                    entity_type="skill_bucket",
                    entity_key=bucket,
                    source_doc_id=doc.id,
                    user_text=value,
                    job_context=jc,
                ))
                total_memory += 1

            # summary + tagline
            session.add(ContentMemory(
                entity_type="summary",
                entity_key="__scalar__",
                source_doc_id=doc.id,
                user_text=resume["summary"],
                job_context=jc,
            ))
            session.add(ContentMemory(
                entity_type="tagline",
                entity_key="__scalar__",
                source_doc_id=doc.id,
                user_text=resume["tagline"],
                job_context=jc,
            ))
            total_memory += 2

            logger.info("Seeded %s — %d research + %d employer × bullets + %d skills + summary + tagline",
                        resume["name"],
                        len(resume["research"]),
                        len(resume["experience"]),
                        len(resume["skills"]))

        # ---- Writing memory rules -------------------------------------------
        rule_count = 0
        for rule in DEMO_WRITING_RULES:
            session.add(WritingMemory(
                domain="resume",
                rule_text=rule["rule_text"],
                category=rule["category"],
                scope="universal",
                examples_json=rule.get("examples_json"),
                source_type=DEMO_RULE_SOURCE,
                confidence=0.85,
                occurrence_count=1,
                is_active=True,
            ))
            rule_count += 1

        await session.commit()

        print()
        print("=" * 68)
        print(f"Seeded {total_docs} demo resume(s), {total_memory} content_memory rows, {rule_count} writing_memory rules.")
        print("=" * 68)
        print()
        print("Next steps:")
        print("  1. Open the app and import any job URL")
        print("  2. Generate a resume — the agent will ground on the demo past versions")
        print("  3. Inspect /profile → Writing Style — 5 starter rules now visible")
        print("  4. To remove demo data later: python examples/seed_demo_memory.py --reset")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Seed Mirror with fictional past-resume content_memory rows.")
    p.add_argument(
        "--reset",
        action="store_true",
        help="Delete all existing demo rows before re-seeding.",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(seed(reset=args.reset))
