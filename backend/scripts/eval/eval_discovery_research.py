"""Test harness for the background-research discovery arm.

Lets us iterate on the prompt that goes to ``llm_web_search`` for
company discovery — the question being studied is "does richer
context (work history, accomplishments) help the agent find better-fit
companies?"

Run a matrix of (scenario × prompt_variant), capture each variant's
company list, dump JSONL + side-by-side markdown. No mutations to the
real hot-search pipeline; this is purely an offline prompt eval.

Usage:
    docker compose exec api python -m scripts.eval.eval_discovery_research
    docker compose exec api python -m scripts.eval.eval_discovery_research \
        --variants research_with_history,research_with_accomplishments \
        --scenarios query_drug_discovery,profile_only

Cost: ~$0.20 per (variant, scenario). Default matrix is 4 × 4 = 16 calls.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

# Make app importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config import settings
from app.database import async_session
from app.services import app_settings_service
from app.services.hot_search.discovery import _load_profile_data
from app.services.web_search_llm import llm_web_search

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scenarios — the four input cases enumerated in our chat
# ---------------------------------------------------------------------------


@dataclass
class Scenario:
    name: str
    guidance: str | None
    locations: list[str] | None = None
    min_salary: int | None = None
    # Optional reference job description text; stands in for thumbed-up jobs
    references: str = ""
    notes: str = ""


SCENARIOS: list[Scenario] = [
    Scenario(
        name="query_drug_discovery",
        guidance="AI for drug discovery",
        notes="Niche query — exactly the case where listicles add the most signal",
    ),
    Scenario(
        name="query_filtered_ai_safety",
        guidance="senior machine learning engineer at AI safety lab",
        locations=["San Francisco", "Remote"],
        min_salary=220000,
        notes="Specific query + tight filters — discovery should bias toward filter-friendly companies",
    ),
    Scenario(
        name="profile_only",
        guidance=None,
        notes="No query, no refs. Profile must carry all the intent.",
    ),
    Scenario(
        name="profile_filtered",
        guidance=None,
        locations=["San Francisco", "Remote"],
        notes="No query, but geo bias. Tests the 'filters as context' principle.",
    ),
    # Pivot scenarios — explicit guidance for a domain that doesn't
    # match the candidate's work history. Tests whether the variant
    # honors the stated intent or hedges back toward the background.
    Scenario(
        name="pivot_ag",
        guidance="AI for agriculture",
        notes="Candidate-stated PIVOT — should surface ag-tech / ag-AI companies regardless of work history.",
    ),
    Scenario(
        name="pivot_drug_discovery",
        guidance="AI for drug discovery and computational biology",
        notes="Candidate-stated PIVOT into biology — see whether non-bio profiles still get bio companies.",
    ),
    Scenario(
        name="pivot_climate",
        guidance="AI for climate and energy",
        notes="Candidate-stated PIVOT into climate-tech. Wide industry — many companies should exist.",
    ),
]


# ---------------------------------------------------------------------------
# Synthetic profiles — diverse personas for testing generalization.
#
# Shape matches what _load_profile_data() returns from the DB so all the
# prompt formatters work identically. Each profile has target_roles +
# domains + skills + search_preferences + work_history (3) +
# complete_profile.{accomplishments, publications}.
# ---------------------------------------------------------------------------

SYNTHETIC_PROFILES: dict[str, dict] = {
    "senior_ml_infra": {
        "target_roles": [
            {"title": "Staff ML Engineer"},
            {"title": "Senior ML Infrastructure Engineer"},
            {"title": "Distinguished Engineer, ML Platform"},
        ],
        "domains": ["ML infrastructure", "distributed training", "LLM serving", "model platforms"],
        "skills": {
            "technical": [
                "Python",
                "PyTorch",
                "Ray",
                "Kubernetes",
                "CUDA",
                "distributed systems",
                "Triton",
                "TensorRT",
                "GPU optimization",
                "TorchServe",
                "vLLM",
                "AWS",
                "Terraform",
                "gRPC",
            ]
        },
        "search_preferences": {
            "looking_for": "staff/principal IC roles building ML platforms at scale",
            "not_looking_for": "management track, early-stage startups without infra needs, crypto",
        },
        "work_history": [
            {
                "title": "Staff ML Engineer",
                "employer": "Lyft",
                "start": "2022-09",
                "end": None,
                "location": "San Francisco, CA",
            },
            {
                "title": "Senior Software Engineer, ML Platform",
                "employer": "Stripe",
                "start": "2019-06",
                "end": "2022-09",
                "location": "San Francisco, CA",
            },
            {
                "title": "Software Engineer",
                "employer": "Cloudera",
                "start": "2015-08",
                "end": "2019-06",
                "location": "Palo Alto, CA",
            },
        ],
        "complete_profile": {
            "accomplishments": [
                {
                    "title": "Scaled distributed training to 800+ H100s with 92% MFU",
                    "tags": ["distributed-training", "ml-infra", "gpu-optimization"],
                },
                {
                    "title": "Cut p99 inference latency 3x by rewriting Triton ensemble",
                    "tags": ["inference", "latency", "triton"],
                },
                {
                    "title": "Designed multi-tenant model-serving platform serving 1B+ daily requests",
                    "tags": ["platform", "serving", "scale"],
                },
                {
                    "title": "Open-sourced vLLM-compatible request router used by 50+ teams",
                    "tags": ["open-source", "vllm", "infrastructure"],
                },
                {
                    "title": "Migrated 200+ models from custom serving to KServe in 6 months",
                    "tags": ["migration", "kserve"],
                },
            ],
            "publications": [],
        },
    },
    "junior_data_scientist": {
        "target_roles": [
            {"title": "Data Scientist"},
            {"title": "Senior Data Analyst"},
        ],
        "domains": ["product analytics", "experimentation", "growth", "B2C metrics"],
        "skills": {
            "technical": [
                "Python",
                "SQL",
                "scikit-learn",
                "Tableau",
                "Looker",
                "statistics",
                "A/B testing",
                "causal inference",
                "pandas",
                "dbt",
            ]
        },
        "search_preferences": {
            "looking_for": "growth/product data science at consumer companies, mid-stage startups",
            "not_looking_for": "ML research roles, infrastructure work, defense, fintech-trading",
        },
        "work_history": [
            {
                "title": "Data Scientist II",
                "employer": "DoorDash",
                "start": "2023-07",
                "end": None,
                "location": "San Francisco, CA",
            },
            {
                "title": "Data Analyst",
                "employer": "Shopify",
                "start": "2022-05",
                "end": "2023-07",
                "location": "Remote",
            },
            {
                "title": "Data Science Intern",
                "employer": "Atlassian",
                "start": "2021-06",
                "end": "2021-09",
                "location": "Mountain View, CA",
            },
        ],
        "complete_profile": {
            "accomplishments": [
                {
                    "title": "Ran 80+ A/B tests on Dasher matching, $14M annual lift",
                    "tags": ["experimentation", "ab-testing", "growth"],
                },
                {
                    "title": "Built Dasher retention model improving 60-day retention 9%",
                    "tags": ["retention-modeling", "ml-product"],
                },
                {
                    "title": "Designed and shipped the Atlassian acquisition-funnel dashboard",
                    "tags": ["dashboards", "saas", "looker"],
                },
                {
                    "title": "Codified the experimentation playbook used by 4 product teams",
                    "tags": ["experimentation", "process"],
                },
            ],
            "publications": [],
        },
    },
    "research_scientist_phd": {
        "target_roles": [
            {"title": "Research Scientist"},
            {"title": "Research Engineer"},
            {"title": "Member of Technical Staff"},
        ],
        "domains": ["AI safety", "alignment", "interpretability", "RLHF", "LLM evaluation"],
        "skills": {
            "technical": [
                "PyTorch",
                "JAX",
                "Transformers",
                "RLHF",
                "interpretability tools",
                "mechanistic interpretability",
                "agent evaluation",
                "research engineering",
            ]
        },
        "search_preferences": {
            "looking_for": "frontier-lab research scientist roles in safety/alignment, IC track",
            "not_looking_for": "applied ML at non-research companies, sales-adjacent roles, defense",
        },
        "work_history": [
            {
                "title": "Research Scientist",
                "employer": "Allen Institute for AI",
                "start": "2024-01",
                "end": None,
                "location": "Seattle, WA",
            },
            {
                "title": "PhD Researcher",
                "employer": "Stanford CS — Hashimoto group",
                "start": "2019-09",
                "end": "2023-12",
                "location": "Stanford, CA",
            },
            {
                "title": "Research Intern",
                "employer": "Anthropic",
                "start": "2022-06",
                "end": "2022-09",
                "location": "San Francisco, CA",
            },
        ],
        "complete_profile": {
            "accomplishments": [
                {
                    "title": "First author on 'Sparse Autoencoders for Feature Discovery in Llama-3-70B' (NeurIPS 2024)",
                    "tags": ["interpretability", "sae", "llm"],
                },
                {
                    "title": "Co-designed BIG-Bench Hard evaluation subset, used by 100+ groups",
                    "tags": ["evals", "benchmark", "research-tooling"],
                },
                {
                    "title": "Released SAE-Lens, 800+ stars, default toolkit for SAE research",
                    "tags": ["open-source", "interpretability"],
                },
                {
                    "title": "Shipped agentic-evaluation pipeline catching jailbreaks in pre-release Claude 3.5",
                    "tags": ["safety", "evaluation", "anthropic"],
                },
            ],
            "publications": [
                {
                    "title": "Sparse Autoencoders for Feature Discovery in Llama-3-70B",
                    "venue": "NeurIPS",
                    "year": 2024,
                    "relevance_weight": 1.0,
                },
                {
                    "title": "Steering Vectors for Controllable Generation",
                    "venue": "ICML",
                    "year": 2024,
                    "relevance_weight": 0.9,
                },
                {
                    "title": "Mechanistic Interpretability of Induction Heads in MoE Models",
                    "venue": "ICLR",
                    "year": 2023,
                    "relevance_weight": 0.85,
                },
                {
                    "title": "Red Teaming with Tree-of-Attacks: Adversarial Probes for LLMs",
                    "venue": "EMNLP",
                    "year": 2023,
                    "relevance_weight": 0.7,
                },
            ],
        },
    },
    "frontend_product_eng": {
        "target_roles": [
            {"title": "Senior Frontend Engineer"},
            {"title": "Product Engineer"},
            {"title": "Founding Engineer"},
        ],
        "domains": ["developer tools", "B2B SaaS", "design systems", "design-engineering"],
        "skills": {
            "technical": [
                "TypeScript",
                "React",
                "Next.js",
                "Tailwind",
                "design systems",
                "Storybook",
                "Vite",
                "tRPC",
                "GraphQL",
                "Figma",
                "Framer Motion",
                "accessibility",
                "WebGL",
            ]
        },
        "search_preferences": {
            "looking_for": "small companies (10-150 ppl), founding/early product engineer, design-leaning",
            "not_looking_for": "FAANG, ad-tech, crypto, primarily-backend or ML roles",
        },
        "work_history": [
            {
                "title": "Senior Frontend Engineer",
                "employer": "Linear",
                "start": "2022-08",
                "end": None,
                "location": "Remote",
            },
            {
                "title": "Frontend Engineer",
                "employer": "Vercel",
                "start": "2019-09",
                "end": "2022-08",
                "location": "San Francisco, CA",
            },
            {
                "title": "Frontend Engineer",
                "employer": "Figma",
                "start": "2017-05",
                "end": "2019-09",
                "location": "San Francisco, CA",
            },
        ],
        "complete_profile": {
            "accomplishments": [
                {
                    "title": "Shipped the Linear inbox redesign, +18% week-2 retention",
                    "tags": ["frontend", "design", "linear"],
                },
                {
                    "title": "Authored Linear's design system migration from styled-components to vanilla-extract",
                    "tags": ["design-system", "migration"],
                },
                {
                    "title": "Built the Vercel deploy-summary UI used by 500k+ developers",
                    "tags": ["dx", "deploy", "vercel"],
                },
                {
                    "title": "Co-designed the Figma plugin runtime sandbox",
                    "tags": ["sandboxing", "plugins", "figma"],
                },
            ],
            "publications": [],
        },
    },
    "computational_bio_ml": {
        "target_roles": [
            {"title": "Senior ML Scientist"},
            {"title": "Computational Biologist"},
            {"title": "ML Research Engineer, Biology"},
        ],
        "domains": [
            "drug discovery",
            "computational biology",
            "protein modeling",
            "RNA biology",
            "single-cell",
        ],
        "skills": {
            "technical": [
                "PyTorch",
                "JAX",
                "ESM/protein language models",
                "AlphaFold2/3",
                "bioinformatics",
                "single-cell RNA-seq",
                "Python",
                "Snakemake",
                "molecular dynamics",
                "diffusion models",
            ]
        },
        "search_preferences": {
            "looking_for": "AI-native biotechs / drug discovery startups (Series A-C), IC research role",
            "not_looking_for": "academic positions, pure software companies without biology focus",
        },
        "work_history": [
            {
                "title": "ML Scientist, Computational Biology",
                "employer": "Genentech",
                "start": "2022-08",
                "end": None,
                "location": "South San Francisco, CA",
            },
            {
                "title": "Postdoctoral Researcher, Computational Biology",
                "employer": "MIT — Berger lab",
                "start": "2020-07",
                "end": "2022-08",
                "location": "Cambridge, MA",
            },
            {
                "title": "PhD Researcher",
                "employer": "Harvard / Broad Institute",
                "start": "2015-09",
                "end": "2020-06",
                "location": "Cambridge, MA",
            },
        ],
        "complete_profile": {
            "accomplishments": [
                {
                    "title": "Trained 600M-parameter protein language model used in 4 internal drug-discovery programs at Genentech",
                    "tags": ["protein-modeling", "esm", "pretraining"],
                },
                {
                    "title": "Co-led integration of AlphaFold-3 into Genentech's structure-based drug design pipeline",
                    "tags": ["alphafold", "structure-based"],
                },
                {
                    "title": "First-author on Nature Methods paper on single-cell perturbation prediction",
                    "tags": ["single-cell", "perturbation", "publication"],
                },
                {
                    "title": "Released SCBert, a foundation model for single-cell RNA-seq, 500+ GitHub stars",
                    "tags": ["single-cell", "open-source", "foundation-models"],
                },
            ],
            "publications": [
                {
                    "title": "Protein Language Models for Multimodal Drug Discovery",
                    "venue": "Nature Methods",
                    "year": 2024,
                    "relevance_weight": 1.0,
                },
                {
                    "title": "Single-Cell Perturbation Prediction with Conditional Diffusion",
                    "venue": "Nature Methods",
                    "year": 2023,
                    "relevance_weight": 0.9,
                },
                {
                    "title": "SCBert: A Foundation Model for Single-Cell Transcriptomics",
                    "venue": "Nature Biotech",
                    "year": 2023,
                    "relevance_weight": 0.85,
                },
            ],
        },
    },
}


# ---------------------------------------------------------------------------
# Prompt variants — each is a function (scenario, profile_data) -> prompt_str
# ---------------------------------------------------------------------------


def _format_profile_basic(profile: dict) -> str:
    """Target roles, domains, top skills — the basic context every
    prompt variant includes."""
    parts: list[str] = []
    roles = [r.get("title", "") for r in profile.get("target_roles", []) if r.get("title")]
    if roles:
        parts.append(f"Target roles: {', '.join(roles[:5])}")
    domains = profile.get("domains", [])
    if domains:
        parts.append(f"Domains of interest: {', '.join(domains[:5])}")
    skills = profile.get("skills", {}).get("technical", [])
    if skills:
        parts.append(f"Strong with: {', '.join(skills[:12])}")
    prefs = profile.get("search_preferences", {})
    not_looking_for = prefs.get("not_looking_for") or ""
    if not_looking_for:
        parts.append(f"Avoiding: {not_looking_for}")
    return "\n".join(parts) if parts else "(no profile data)"


def _format_work_history(profile: dict) -> str:
    """Last 3 jobs as compact lines, most recent first."""
    wh = profile.get("work_history") or []
    if not wh:
        return ""
    # work_history is most-recent-first based on the YAML convention
    lines = []
    for entry in wh[:3]:
        title = entry.get("title", "")
        emp = entry.get("employer", "")
        start = entry.get("start", "")
        end = entry.get("end") or "present"
        loc = entry.get("location", "")
        line = f"  {title} at {emp} ({start} – {end})"
        if loc:
            line += f", {loc}"
        lines.append(line)
    return "Recent work history:\n" + "\n".join(lines)


def _format_accomplishments(profile: dict, n: int = 6) -> str:
    """Top-N accomplishment titles (with a tag or two for context)."""
    cp = profile.get("complete_profile") or {}
    accs = cp.get("accomplishments") or []
    if not accs:
        return ""
    lines = []
    for a in accs[:n]:
        title = a.get("title", "") or a.get("id", "")
        if not title:
            continue
        tags = a.get("tags") or []
        tag_str = f" [{', '.join(tags[:3])}]" if tags else ""
        lines.append(f"  • {title}{tag_str}")
    if not lines:
        return ""
    return "Notable accomplishments:\n" + "\n".join(lines)


def _intent_block(scenario: Scenario, profile: dict) -> str:
    """Compose the INTENT line based on which inputs are present.

    Branching documented in our chat: guidance leads if present;
    references step in when only refs; profile carries all intent
    otherwise."""
    g = (scenario.guidance or "").strip()
    refs = (scenario.references or "").strip()

    if g and refs:
        return f"Primary search: '{g}'\nAlso liked these reference jobs:\n{refs}"
    if g:
        return f"Primary search: '{g}'"
    if refs:
        return f"The candidate liked these reference jobs and wants more like them:\n{refs}"
    # No query, no refs — profile is the query
    return (
        "The candidate hasn't specified a search topic. Use their target "
        "roles + domains below to infer what kinds of companies would "
        "interest them."
    )


def _preference_block(scenario: Scenario) -> str:
    parts = []
    if scenario.locations:
        parts.append(f"Locations (bias, not hard filter): {', '.join(scenario.locations)}")
    if scenario.min_salary:
        parts.append(f"Min salary (bias, not hard filter): ${scenario.min_salary:,}")
    return "\n".join(parts) if parts else "(no location/salary preference)"


# --- Variant 1: current production prompt (URL-focused, avoid listicles)


def variant_baseline_jobs(scenario: Scenario, profile: dict) -> str:
    """Mirror of discovery_v2._build_llm_web_query — the current prompt."""
    parts: list[str] = []
    g = scenario.guidance or "find roles matching the candidate's profile"
    parts.append(f"Find current job openings matching: {g}")
    if scenario.locations:
        parts.append(f"Locations: {', '.join(scenario.locations)}")
    if scenario.min_salary:
        parts.append(f"Minimum salary: ${scenario.min_salary:,}")
    parts.append("")
    parts.append("Return URLs to specific job postings or company careers pages. PREFER URLs from:")
    parts.append("  - boards.greenhouse.io/<company>/...")
    parts.append("  - jobs.lever.co/<company>/...")
    parts.append("  - jobs.ashbyhq.com/<company>/...")
    parts.append("  - company careers pages (e.g. anthropic.com/careers)")
    parts.append("")
    parts.append("AVOID:")
    parts.append("  - LinkedIn, Indeed, Glassdoor")
    parts.append("  - generic 'top 10' listicles")
    parts.append("")
    parts.append("Aim for 8-10 distinct companies. Cite each URL you used.")
    return "\n".join(parts)


# --- Variant 2: research mode (names only, listicles encouraged), minimal context


def variant_research_minimal(scenario: Scenario, profile: dict) -> str:
    return _research_prompt(
        scenario,
        profile,
        include_history=False,
        include_accomplishments=False,
    )


# --- Variant 3: research mode + work history


def variant_research_with_history(scenario: Scenario, profile: dict) -> str:
    return _research_prompt(
        scenario,
        profile,
        include_history=True,
        include_accomplishments=False,
    )


# --- Variant 4: research mode + accomplishments


def variant_research_with_accomplishments(scenario: Scenario, profile: dict) -> str:
    return _research_prompt(
        scenario,
        profile,
        include_history=False,
        include_accomplishments=True,
    )


# --- Variant 5: research mode + history + accomplishments


def variant_research_full(scenario: Scenario, profile: dict) -> str:
    return _research_prompt(
        scenario,
        profile,
        include_history=True,
        include_accomplishments=True,
    )


def _research_prompt(
    scenario: Scenario,
    profile: dict,
    *,
    include_history: bool,
    include_accomplishments: bool,
) -> str:
    """Shared body of the research-mode prompts. Toggle history/accomp
    blocks via flags."""
    sections: list[str] = []

    sections.append("INTENT")
    sections.append(_intent_block(scenario, profile))
    sections.append("")

    sections.append("CANDIDATE CONTEXT")
    sections.append(_format_profile_basic(profile))

    if include_history:
        wh = _format_work_history(profile)
        if wh:
            sections.append("")
            sections.append(wh)

    if include_accomplishments:
        accs = _format_accomplishments(profile)
        if accs:
            sections.append("")
            sections.append(accs)

    sections.append("")
    sections.append("GEOGRAPHIC & COMP PREFERENCE (bias, not gate)")
    sections.append(_preference_block(scenario))
    sections.append("")

    sections.append("TASK")
    sections.append(
        "Find 15-20 companies that plausibly hire for this intent. Use any "
        "sources including industry listicles, VC portfolio pages, news, "
        "Crunchbase summaries, GitHub. You don't need to find job posting "
        "URLs — just identify the companies."
    )
    sections.append("")
    sections.append(
        "Output one company per line, in the format:\n"
        '  "Name — one-line context (industry, what they do)"\n'
        "No numbering, no extra prose."
    )
    sections.append("")
    sections.append(
        "Avoid: consulting firms / agencies that don't build product, "
        "companies in the 'avoiding' list above, and anything that clearly "
        "doesn't match the intent."
    )

    return "\n".join(sections)


def _format_publications(profile: dict, n: int = 5) -> str:
    """Top-N publications by relevance_weight (or most recent if no
    weight). Title + venue + year only; abstracts are too long."""
    cp = profile.get("complete_profile") or {}
    pubs = cp.get("publications") or []
    if not pubs:
        return ""

    # Sort: relevance_weight desc, then year desc
    def _sort_key(p):
        rw = p.get("relevance_weight")
        y = p.get("year")
        return (
            -(rw if isinstance(rw, (int, float)) else 0),
            -(int(y) if str(y).isdigit() else 0),
        )

    top = sorted(pubs, key=_sort_key)[:n]
    lines = []
    for p in top:
        title = (p.get("title") or p.get("id") or "").strip()
        venue = (p.get("venue") or "").strip()
        year = p.get("year") or ""
        if not title:
            continue
        meta = ", ".join(x for x in [venue, str(year) if year else ""] if x)
        lines.append(f"  • {title[:120]}" + (f" ({meta})" if meta else ""))
    if not lines:
        return ""
    return "Selected publications:\n" + "\n".join(lines)


# --- Variant: research mode + work history + publications


def variant_research_with_publications(scenario: Scenario, profile: dict) -> str:
    """research_with_history extended with top 5 publications. Tests
    whether scholarly output helps the agent infer fit for research
    labs / scientific software companies."""
    base = _research_prompt(
        scenario,
        profile,
        include_history=True,
        include_accomplishments=False,
    )
    pubs = _format_publications(profile)
    if not pubs:
        return base
    return base.replace(
        "GEOGRAPHIC & COMP PREFERENCE",
        f"{pubs}\n\nGEOGRAPHIC & COMP PREFERENCE",
        1,
    )


# --- Variant: recruiter-perspective framing.
#
# This variant graduated to production — see
# backend/app/services/hot_search/discovery_v2.py:_build_llm_web_query.
# Eval data behind the choice in commits f4e13e3 + 6060fdc:
#   - 4.55 mean / 94.6% ≥4 across non-pivot scenarios (winner)
#   - 60-80% ≥4 on career pivots for 4 of 5 profiles (still winner)
# A pivot-handling block was tried (research_recruiter_pivot, since
# removed) but actually *hurt* — it pushed the agent so hard into the
# new domain that it lost the skill-transfer reasoning that made plain
# recruiter framing pivot-friendly in the first place.


def variant_research_recruiter(scenario: Scenario, profile: dict) -> str:
    """Reframes the task as a recruiter pitching the candidate, rather
    than a search engine returning companies that match a topic. Tests
    whether the 'agent of the candidate' framing surfaces different
    companies (the kind a human recruiter would think of)."""
    sections: list[str] = []
    sections.append("ROLE")
    sections.append(
        "You are an executive recruiter who specializes in placing this candidate. "
        "Your task is to identify 15 companies that would be most excited to "
        "interview them right now and where they would thrive."
    )
    sections.append("")
    sections.append("CANDIDATE")
    sections.append(_intent_block(scenario, profile))
    sections.append("")
    sections.append(_format_profile_basic(profile))
    wh = _format_work_history(profile)
    if wh:
        sections.append("")
        sections.append(wh)
    accs = _format_accomplishments(profile, n=5)
    if accs:
        sections.append("")
        sections.append(accs)
    sections.append("")
    sections.append("PREFERENCES (bias, not gate)")
    sections.append(_preference_block(scenario))
    sections.append("")
    sections.append("TASK")
    sections.append(
        "Pitch 15 companies that would value this candidate's background and "
        "are likely hiring for roles they would be interested in. For each, "
        "explain in one line why this candidate is a strong fit (e.g. 'their "
        "MUSE work directly transfers to X's qualitative AI platform')."
    )
    sections.append("")
    sections.append(
        "Use any sources — VC portfolios, news, LinkedIn company pages, "
        "industry roundups. You're not looking for job URLs; you're naming "
        "the companies."
    )
    sections.append("")
    sections.append('Output one company per line: "Name — why they\'re a fit."')
    return "\n".join(sections)


# --- Variant: emphasize recently-funded growth-stage companies


def variant_research_funded_recent(scenario: Scenario, profile: dict) -> str:
    """Same shape as research_with_history but with explicit emphasis
    on companies that have raised in the last 18 months. Tests whether
    a growth-stage signal surfaces companies that are aggressively
    hiring (vs companies that are stable but not expanding)."""
    base = _research_prompt(
        scenario,
        profile,
        include_history=True,
        include_accomplishments=False,
    )
    # Inject growth-stage emphasis into the TASK section.
    return base.replace(
        "Find 15-20 companies that plausibly hire for this intent.",
        (
            "Find 15-20 companies that plausibly hire for this intent. "
            "STRONGLY prefer companies that have raised funding in the last "
            "~18 months (Series A through D, or just announced new rounds), "
            "or that are otherwise actively expanding — these are the most "
            "likely to be hiring."
        ),
        1,
    )


# --- Variant: concise / selective — fewer but more carefully chosen


def variant_research_concise(scenario: Scenario, profile: dict) -> str:
    """Shorter prompt, asks for only 8 carefully chosen companies.
    Tests whether forcing selectivity produces higher per-company
    quality than the 15-20 default."""
    sections: list[str] = []
    sections.append("INTENT")
    sections.append(_intent_block(scenario, profile))
    sections.append("")
    sections.append("CANDIDATE")
    sections.append(_format_profile_basic(profile))
    wh = _format_work_history(profile)
    if wh:
        sections.append("")
        sections.append(wh)
    sections.append("")
    sections.append("PREFERENCES (bias)")
    sections.append(_preference_block(scenario))
    sections.append("")
    sections.append("TASK")
    sections.append(
        "Identify exactly 8 companies that are the STRONGEST fits — both "
        "in terms of role fit for this candidate and active hiring. Be "
        "selective. Prefer specific over generic; prefer companies the "
        "candidate would genuinely want over big names of last resort."
    )
    sections.append("")
    sections.append('Output: one company per line, "Name — one-line why they\'re a top fit."')
    return "\n".join(sections)


VARIANTS: dict[str, Callable[[Scenario, dict], str]] = {
    "baseline_jobs": variant_baseline_jobs,
    "research_minimal": variant_research_minimal,
    "research_with_history": variant_research_with_history,
    "research_with_accomplishments": variant_research_with_accomplishments,
    "research_full": variant_research_full,
    "research_with_publications": variant_research_with_publications,
    "research_recruiter": variant_research_recruiter,  # ← shipped to production
    "research_funded_recent": variant_research_funded_recent,
    "research_concise": variant_research_concise,
}


# ---------------------------------------------------------------------------
# Company-name extraction from agent output
# ---------------------------------------------------------------------------


_BULLET_PREFIX_RE = re.compile(r"^\s*[-*•]?\s*")
_LEADING_NUM_RE = re.compile(r"^\s*\d+[.):]\s*")


def extract_companies_from_answer(text: str) -> list[dict]:
    """Pull "Name — context" lines out of a free-text agent response.

    Tolerant of bullets, numbering, and en-dash vs em-dash vs hyphen.
    Returns ``[{"name": str, "context": str}, ...]``.
    """
    out: list[dict] = []
    seen_names: set[str] = set()
    if not text:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        line = _LEADING_NUM_RE.sub("", line)
        line = _BULLET_PREFIX_RE.sub("", line)
        # Split on em-dash / en-dash / hyphen-with-spaces / colon
        m = re.split(r"\s*[—–\-:]\s*", line, maxsplit=1)
        if len(m) == 2 and len(m[0]) >= 2 and len(m[0]) <= 80:
            name = m[0].strip().strip("*_`")
            context = m[1].strip()
            # Filter out obvious section headers ("INTENT", "TASK", etc.)
            if name.isupper() and len(name) <= 20:
                continue
            key = name.lower()
            if key in seen_names:
                continue
            seen_names.add(key)
            out.append({"name": name, "context": context[:300]})
    return out


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


@dataclass
class CellResult:
    variant: str
    scenario: str
    profile_name: str = "real"
    elapsed_sec: float = 0.0
    error: str = ""
    raw_answer: str = ""
    citations: list[dict] = field(default_factory=list)
    companies: list[dict] = field(default_factory=list)
    # Token / tool usage from the llm_web_search call (when available).
    # Keys: input_tokens, output_tokens, reasoning_tokens,
    #       cached_input_tokens, search_calls.
    usage: dict = field(default_factory=dict)
    # Judge fields — populated by _judge_cell after the run
    judge_scores: list[dict] = field(default_factory=list)  # [{i, score, reason}, ...]
    judge_error: str = ""


# Per-token rates ($/M tokens) and per-search-call rates ($/call). Used for
# rough cost estimates in the report — change these if your account's
# rates differ. Constants chosen to match OpenAI's published list-prices
# as of the eval date; treat the cost numbers as ±30% accurate.
_RATE_GPT55_INPUT_PER_M = 1.25
_RATE_GPT55_OUTPUT_PER_M = 10.0
_RATE_GPT55_CACHED_INPUT_PER_M = 0.125
_RATE_WEB_SEARCH_CALL = 0.025


def estimate_usd(usage: dict) -> float:
    """Apply per-tier rates to a usage dict, return USD cost.

    Reasoning tokens are billed as output tokens — they're already
    included in usage.output_tokens. Cached input tokens (when prompt
    caching kicked in) are billed at ~10% of standard input.
    """
    if not usage:
        return 0.0
    in_tok = usage.get("input_tokens", 0) or 0
    cached = usage.get("cached_input_tokens", 0) or 0
    uncached = max(0, in_tok - cached)
    out_tok = usage.get("output_tokens", 0) or 0
    search = usage.get("search_calls", 0) or 0
    cost = (
        uncached * (_RATE_GPT55_INPUT_PER_M / 1_000_000)
        + cached * (_RATE_GPT55_CACHED_INPUT_PER_M / 1_000_000)
        + out_tok * (_RATE_GPT55_OUTPUT_PER_M / 1_000_000)
        + search * _RATE_WEB_SEARCH_CALL
    )
    return cost


# ---------------------------------------------------------------------------
# LLM-as-judge
# ---------------------------------------------------------------------------


_JUDGE_SYSTEM = """\
You evaluate whether company recommendations are a strong career fit for a \
specific job candidate. Score each company 1-5:

  5 = strong fit — matches role family, domain, and likely-hiring profile
  4 = good fit — one of (role / domain / stage) slightly off but still attractive
  3 = adjacent fit — interesting and somewhat related, but not directly aligned
  2 = weak fit — wrong role family OR wrong domain OR clearly off-stage
  1 = bad fit — irrelevant, doesn't exist, or known not to hire this kind of role

Be strict but fair. Anchor on whether THIS candidate would plausibly want to \
work there AND whether the company would plausibly hire them. Brand name alone \
(Google, Apple) is not a 5 unless their role/domain match is direct.

Output ONLY valid JSON in this exact shape:
  {"results": [
    {"i": <1-indexed company number>, "score": <1-5>, "reason": "<≤15 words>"},
    ...
  ]}
No prose, no markdown fences, one object per input company in input order."""


def _format_profile_for_judge(profile: dict) -> str:
    """Concise candidate summary used as judge context."""
    parts: list[str] = []
    roles = [r.get("title", "") for r in profile.get("target_roles", []) if r.get("title")]
    if roles:
        parts.append(f"Target roles: {', '.join(roles[:5])}")
    domains = profile.get("domains", [])
    if domains:
        parts.append(f"Domains: {', '.join(domains[:5])}")
    skills = profile.get("skills", {}).get("technical", [])
    if skills:
        parts.append(f"Skills: {', '.join(skills[:10])}")
    prefs = profile.get("search_preferences", {})
    if prefs.get("looking_for"):
        parts.append(f"Looking for: {prefs['looking_for']}")
    if prefs.get("not_looking_for"):
        parts.append(f"Avoiding: {prefs['not_looking_for']}")
    wh = profile.get("work_history") or []
    if wh:
        parts.append("Recent work history:")
        for entry in wh[:3]:
            t = entry.get("title", "")
            e = entry.get("employer", "")
            parts.append(f"  - {t} at {e}")
    return "\n".join(parts)


async def judge_companies(
    scenario: Scenario,
    profile: dict,
    companies: list[dict],
) -> tuple[list[dict], str]:
    """Score each company 1-5 in one batched LLM call.

    Returns ``(scores, error_str)``. ``scores`` is a list of
    ``{i, score, reason}`` dicts, indexed 1..len(companies). If the call
    failed the error is non-empty and scores is empty.
    """
    if not companies:
        return [], ""

    from app.ai.client import SCORING_MODEL, get_openai_client

    parts: list[str] = []
    parts.append("CANDIDATE")
    parts.append(_format_profile_for_judge(profile))
    parts.append("")
    parts.append("SEARCH INTENT")
    if scenario.guidance:
        parts.append(f'  guidance: "{scenario.guidance}"')
    else:
        parts.append("  (no explicit guidance — profile is the query)")
    if scenario.locations:
        parts.append(f"  locations: {', '.join(scenario.locations)}")
    if scenario.min_salary:
        parts.append(f"  min salary: ${scenario.min_salary:,}")
    parts.append("")
    parts.append(f"Now score these {len(companies)} companies. Return JSON.")
    parts.append("")
    parts.append("Companies:")
    for i, c in enumerate(companies, start=1):
        name = c.get("name", "?")
        ctx = (c.get("context") or "").replace("\n", " ").strip()[:200]
        parts.append(f"{i}. {name} — {ctx}")
    user_prompt = "\n".join(parts)

    client = get_openai_client()
    try:
        resp = await client.chat.completions.create(
            model=SCORING_MODEL,
            messages=[
                {"role": "system", "content": _JUDGE_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            max_completion_tokens=8000,
        )
    except Exception as e:
        return [], f"API error: {str(e)[:200]}"

    raw = (resp.choices[0].message.content or "").strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    if not raw:
        return [], "empty content"

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return [], f"unparseable JSON: {raw[:120]}"
        try:
            parsed = json.loads(m.group(0))
        except Exception:
            return [], f"reparse failed: {raw[:120]}"

    results = []
    if isinstance(parsed, dict) and isinstance(parsed.get("results"), list):
        for entry in parsed["results"]:
            if not isinstance(entry, dict):
                continue
            try:
                i = int(entry.get("i", 0))
                score = int(entry.get("score", 0))
            except (TypeError, ValueError):
                continue
            if 1 <= i <= len(companies) and 1 <= score <= 5:
                results.append(
                    {
                        "i": i,
                        "score": score,
                        "reason": str(entry.get("reason", ""))[:200],
                    }
                )
    if not results:
        return [], f"no valid entries in: {raw[:120]}"
    return results, ""


async def run_one(
    variant_name: str,
    scenario: Scenario,
    profile: dict,
    profile_name: str = "real",
    num_results: int = 10,
    judge: bool = True,
) -> CellResult:
    prompt_fn = VARIANTS[variant_name]
    prompt = prompt_fn(scenario, profile)

    t0 = time.monotonic()
    try:
        res = await llm_web_search(prompt, num_results=num_results)
    except Exception as e:
        return CellResult(
            variant=variant_name,
            scenario=scenario.name,
            profile_name=profile_name,
            elapsed_sec=time.monotonic() - t0,
            error=str(e)[:200],
        )
    if res is None:
        return CellResult(
            variant=variant_name,
            scenario=scenario.name,
            profile_name=profile_name,
            elapsed_sec=time.monotonic() - t0,
            error="llm_web_search returned None",
        )

    companies = extract_companies_from_answer(res.answer)
    citations = [{"title": c.title, "url": c.url} for c in res.citations[:num_results]]
    result = CellResult(
        variant=variant_name,
        scenario=scenario.name,
        profile_name=profile_name,
        elapsed_sec=time.monotonic() - t0,
        raw_answer=res.answer,
        citations=citations,
        companies=companies,
        usage=dict(res.usage) if getattr(res, "usage", None) else {},
    )

    if judge and companies:
        scores, jerr = await judge_companies(scenario, profile, companies)
        result.judge_scores = scores
        result.judge_error = jerr

    return result


def _cell_summary(r: CellResult) -> dict:
    """Pull aggregate numbers from a cell. % scoring 4+ is the headline."""
    if not r.judge_scores:
        return {
            "n": len(r.companies),
            "n_scored": 0,
            "mean": None,
            "pct_geq_4": None,
            "pct_geq_3": None,
        }
    scores = [s["score"] for s in r.judge_scores]
    n = len(scores)
    mean = sum(scores) / n
    pct_geq_4 = 100 * sum(1 for s in scores if s >= 4) / n
    pct_geq_3 = 100 * sum(1 for s in scores if s >= 3) / n
    return {
        "n": len(r.companies),
        "n_scored": n,
        "mean": round(mean, 2),
        "pct_geq_4": round(pct_geq_4, 1),
        "pct_geq_3": round(pct_geq_3, 1),
    }


def build_markdown(
    results: list[CellResult],
    scenarios: list[Scenario],
    variants: list[str],
    profile_names: list[str],
) -> str:
    lines: list[str] = []
    lines.append("# Discovery research-arm prompt eval\n")
    lines.append(f"Profiles tested: {', '.join(profile_names)}\n")

    # Per-profile aggregate — the main "which variant wins" table
    for pname in profile_names:
        lines.append(f"## Aggregate (judge mean / % score≥4) — profile: `{pname}`\n")
        header = "| scenario \\\\ variant | " + " | ".join(variants) + " |"
        sep = "|---" * (len(variants) + 1) + "|"
        lines.append(header)
        lines.append(sep)
        for s in scenarios:
            cells = []
            for v in variants:
                cell = next(
                    (
                        r
                        for r in results
                        if r.variant == v and r.scenario == s.name and r.profile_name == pname
                    ),
                    None,
                )
                if cell is None:
                    cells.append("—")
                elif cell.error:
                    cells.append("_err_")
                else:
                    summ = _cell_summary(cell)
                    if summ["mean"] is None:
                        cells.append(f"{summ['n']}co _no judge_")
                    else:
                        cells.append(
                            f"{summ['n']}co · mean {summ['mean']} · {summ['pct_geq_4']}%≥4"
                        )
            lines.append(f"| {s.name} | " + " | ".join(cells) + " |")
        lines.append("")

    # Cross-profile variant ranking — mean of means
    if len(profile_names) >= 2:
        lines.append("## Variant ranking across all profiles + scenarios\n")
        lines.append(
            "Aggregated mean judge score and aggregate % score≥4 per variant. Higher is better.\n"
        )
        lines.append(
            "| variant | mean judge score | mean % score≥4 | total companies judged | cells |"
        )
        lines.append("|---|---|---|---|---|")
        rows = []
        for v in variants:
            ms = []
            pcts = []
            total_n = 0
            cells = 0
            for r in results:
                if r.variant != v or r.error:
                    continue
                summ = _cell_summary(r)
                if summ["mean"] is None:
                    continue
                ms.append(summ["mean"])
                pcts.append(summ["pct_geq_4"])
                total_n += summ["n_scored"]
                cells += 1
            if not ms:
                continue
            rows.append(
                (
                    v,
                    round(sum(ms) / len(ms), 2),
                    round(sum(pcts) / len(pcts), 1),
                    total_n,
                    cells,
                )
            )
        rows.sort(key=lambda r: r[1], reverse=True)
        for v, mm, mp, tn, c in rows:
            lines.append(f"| {v} | {mm} | {mp}% | {tn} | {c} |")
        lines.append("")

    # Per-cell company lists w/ judge scores
    lines.append("## Detail per cell\n")
    for pname in profile_names:
        lines.append(f"### profile: `{pname}`\n")
        for s in scenarios:
            lines.append(f"#### scenario: `{s.name}`")
            intent_summary = []
            if s.guidance:
                intent_summary.append(f"guidance: '{s.guidance}'")
            if s.locations:
                intent_summary.append(f"locations: {s.locations}")
            if s.min_salary:
                intent_summary.append(f"min_salary: ${s.min_salary:,}")
            if not intent_summary:
                intent_summary = ["(profile-only)"]
            lines.append(f"_{'  '.join(intent_summary)}_\n")
            for v in variants:
                cell = next(
                    (
                        r
                        for r in results
                        if r.variant == v and r.scenario == s.name and r.profile_name == pname
                    ),
                    None,
                )
                lines.append(f"##### variant: `{v}`")
                if cell is None or cell.error:
                    lines.append(f"_no result_  ({cell.error if cell else 'missing'})\n")
                    continue
                summ = _cell_summary(cell)
                if summ["mean"] is not None:
                    lines.append(
                        f"_{cell.elapsed_sec:.0f}s · {summ['n']} co · mean {summ['mean']} · "
                        f"{summ['pct_geq_4']}%≥4 · {summ['pct_geq_3']}%≥3_\n"
                    )
                else:
                    lines.append(
                        f"_{cell.elapsed_sec:.0f}s · {summ['n']} co · no judge ({cell.judge_error})_\n"
                    )
                # Build score map
                score_map = {j["i"]: (j["score"], j["reason"]) for j in cell.judge_scores}
                for idx, c in enumerate(cell.companies[:25], start=1):
                    s_info = score_map.get(idx)
                    if s_info is None:
                        lines.append(f"- **{c['name']}** — {c['context']}")
                    else:
                        sc, reason = s_info
                        lines.append(
                            f"- **{c['name']}** [`score={sc}`] — {c['context']}  ↳ _{reason}_"
                        )
                lines.append("")
        lines.append("")
    return "\n".join(lines) + "\n"


# Original signature kept for back-compat with any existing callers (none in repo).
def build_markdown_legacy(
    results: list[CellResult], scenarios: list[Scenario], variants: list[str]
) -> str:
    return build_markdown(results, scenarios, variants, ["real"])

    # Per-cell company lists
    lines.append("## Detail per cell\n")
    for s in scenarios:
        lines.append(f"### scenario: `{s.name}`")
        intent_summary = []
        if s.guidance:
            intent_summary.append(f"guidance: '{s.guidance}'")
        if s.locations:
            intent_summary.append(f"locations: {s.locations}")
        if s.min_salary:
            intent_summary.append(f"min_salary: ${s.min_salary:,}")
        if not intent_summary:
            intent_summary = ["(profile-only)"]
        lines.append(f"_{'  '.join(intent_summary)}_\n")
        if s.notes:
            lines.append(f"> {s.notes}\n")
        for v in variants:
            cell = next(
                (r for r in results if r.variant == v and r.scenario == s.name),
                None,
            )
            lines.append(f"#### variant: `{v}`")
            if cell is None or cell.error:
                lines.append(f"_no result_  ({cell.error if cell else 'missing'})\n")
                continue
            lines.append(f"_{cell.elapsed_sec:.0f}s, {len(cell.companies)} companies_\n")
            for c in cell.companies[:25]:
                lines.append(f"- **{c['name']}** — {c['context']}")
            lines.append("")

    # Pairwise overlap matrix per scenario (Jaccard on names)
    lines.append("## Pairwise overlap per scenario (Jaccard on name sets)\n")
    for s in scenarios:
        sets: dict[str, set] = {}
        for v in variants:
            cell = next(
                (r for r in results if r.variant == v and r.scenario == s.name),
                None,
            )
            if cell and cell.companies:
                sets[v] = {c["name"].lower().strip() for c in cell.companies}
        if len(sets) < 2:
            continue
        lines.append(f"### `{s.name}`")
        header = "| " + " | ".join(["—"] + list(sets.keys())) + " |"
        sep = "|---" * (len(sets) + 1) + "|"
        lines.append(header)
        lines.append(sep)
        for vi in sets:
            row = [vi]
            for vj in sets:
                ai = sets[vi]
                aj = sets[vj]
                if not ai or not aj:
                    row.append("—")
                    continue
                inter = len(ai & aj)
                union = len(ai | aj)
                row.append(f"{inter}/{union} = {inter / union:.2f}")
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")

    return "\n".join(lines) + "\n"


async def _hydrate_settings():
    async with async_session() as s:
        await app_settings_service.load_into_settings(s, settings)
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY missing — configure via /setup first")


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--variants", default=None, help="Comma-separated variant names; default all"
    )
    parser.add_argument(
        "--scenarios", default=None, help="Comma-separated scenario names; default all"
    )
    parser.add_argument(
        "--profiles",
        default="real",
        help=(
            "Comma-separated profile names. 'real' uses the live DB profile. "
            "Synthetic options: " + ",".join(SYNTHETIC_PROFILES.keys()) + ". "
            "Use 'all' for real + all synthetic."
        ),
    )
    parser.add_argument("--num-results", type=int, default=10)
    parser.add_argument("--no-judge", action="store_true", help="Skip LLM judge calls")
    parser.add_argument("--output-dir", default=str(Path(__file__).resolve().parent / "output"))
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    await _hydrate_settings()

    # Resolve which profiles to run
    if args.profiles.strip().lower() == "all":
        profile_names = ["real"] + list(SYNTHETIC_PROFILES.keys())
    else:
        profile_names = [p.strip() for p in args.profiles.split(",") if p.strip()]

    profiles: dict[str, dict] = {}
    for pname in profile_names:
        if pname == "real":
            p = await _load_profile_data()
            print(
                f"Loaded 'real' profile — work_history={len(p.get('work_history', []))}, "
                f"accomplishments={len((p.get('complete_profile') or {}).get('accomplishments', []))}"
            )
            profiles[pname] = p
        elif pname in SYNTHETIC_PROFILES:
            profiles[pname] = SYNTHETIC_PROFILES[pname]
            print(f"Loaded synthetic profile '{pname}'")
        else:
            print(f"WARN: unknown profile '{pname}' — skipping", file=sys.stderr)
    if not profiles:
        print("No valid profiles selected.", file=sys.stderr)
        return

    scen_keep = {s.strip() for s in args.scenarios.split(",")} if args.scenarios else None
    var_keep = {v.strip() for v in args.variants.split(",")} if args.variants else None
    selected_scenarios = [s for s in SCENARIOS if not scen_keep or s.name in scen_keep]
    selected_variants = [v for v in VARIANTS if not var_keep or v in var_keep]
    if not selected_scenarios or not selected_variants:
        print("No scenarios/variants matched.", file=sys.stderr)
        return

    n_cells = len(profiles) * len(selected_scenarios) * len(selected_variants)
    print(
        f"\nRunning {len(profiles)} profiles × {len(selected_scenarios)} scenarios × "
        f"{len(selected_variants)} variants = {n_cells} cells"
    )
    print(f"  profiles:  {list(profiles.keys())}")
    print(f"  scenarios: {[s.name for s in selected_scenarios]}")
    print(f"  variants:  {selected_variants}")
    print(f"  judge:     {'OFF' if args.no_judge else 'ON'}")
    print()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = out_dir / "discovery_research_eval.jsonl"
    md_path = out_dir / "discovery_research_eval.md"

    results: list[CellResult] = []
    with jsonl_path.open("w") as fh:
        # Within a (profile, scenario), variants run in parallel.
        # Across (profile, scenario) we run sequentially to keep
        # rate-limit pressure off third-party APIs.
        for pname, profile in profiles.items():
            for s in selected_scenarios:
                print(f"=== profile={pname}  scenario={s.name} ===")
                tasks = [
                    run_one(
                        v,
                        s,
                        profile,
                        profile_name=pname,
                        num_results=args.num_results,
                        judge=not args.no_judge,
                    )
                    for v in selected_variants
                ]
                t0 = time.monotonic()
                for coro in asyncio.as_completed(tasks):
                    r = await coro
                    results.append(r)
                    fh.write(
                        json.dumps(
                            {
                                "profile_name": r.profile_name,
                                "variant": r.variant,
                                "scenario": r.scenario,
                                "elapsed_sec": r.elapsed_sec,
                                "error": r.error,
                                "n_companies": len(r.companies),
                                "n_citations": len(r.citations),
                                "companies": r.companies,
                                "citations": r.citations,
                                "usage": r.usage,
                                "est_usd": estimate_usd(r.usage),
                                "judge_scores": r.judge_scores,
                                "judge_error": r.judge_error,
                                "raw_answer": r.raw_answer,
                            }
                        )
                        + "\n"
                    )
                    fh.flush()
                    if r.error:
                        summary = f"ERR: {r.error[:40]}"
                    else:
                        summ = _cell_summary(r)
                        cost = estimate_usd(r.usage)
                        cost_str = f"${cost:.3f}" if cost else "—"
                        if summ["mean"] is None:
                            summary = (
                                f"{summ['n']:2d} co · {r.elapsed_sec:5.1f}s · {cost_str} · no judge"
                            )
                        else:
                            summary = (
                                f"{summ['n']:2d} co · {r.elapsed_sec:5.1f}s · "
                                f"{cost_str} · "
                                f"mean {summ['mean']} · {summ['pct_geq_4']}%≥4"
                            )
                    print(f"  {r.variant:36s} → {summary}")
                print(f"  cell wall: {time.monotonic() - t0:.0f}s\n")

    md = build_markdown(
        results,
        selected_scenarios,
        selected_variants,
        list(profiles.keys()),
    )
    md_path.write_text(md)
    print(f"\nWrote {jsonl_path}")
    print(f"Wrote {md_path}")


if __name__ == "__main__":
    asyncio.run(main())
