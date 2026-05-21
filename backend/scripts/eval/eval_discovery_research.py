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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

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
]


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
        scenario, profile,
        include_history=False, include_accomplishments=False,
    )


# --- Variant 3: research mode + work history

def variant_research_with_history(scenario: Scenario, profile: dict) -> str:
    return _research_prompt(
        scenario, profile,
        include_history=True, include_accomplishments=False,
    )


# --- Variant 4: research mode + accomplishments

def variant_research_with_accomplishments(scenario: Scenario, profile: dict) -> str:
    return _research_prompt(
        scenario, profile,
        include_history=False, include_accomplishments=True,
    )


# --- Variant 5: research mode + history + accomplishments

def variant_research_full(scenario: Scenario, profile: dict) -> str:
    return _research_prompt(
        scenario, profile,
        include_history=True, include_accomplishments=True,
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


VARIANTS: dict[str, Callable[[Scenario, dict], str]] = {
    "baseline_jobs": variant_baseline_jobs,
    "research_minimal": variant_research_minimal,
    "research_with_history": variant_research_with_history,
    "research_with_accomplishments": variant_research_with_accomplishments,
    "research_full": variant_research_full,
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
    elapsed_sec: float = 0.0
    error: str = ""
    raw_answer: str = ""
    citations: list[dict] = field(default_factory=list)
    companies: list[dict] = field(default_factory=list)


async def run_one(variant_name: str, scenario: Scenario, profile: dict, num_results: int = 10) -> CellResult:
    prompt_fn = VARIANTS[variant_name]
    prompt = prompt_fn(scenario, profile)

    t0 = time.monotonic()
    try:
        res = await llm_web_search(prompt, num_results=num_results)
    except Exception as e:
        return CellResult(
            variant=variant_name, scenario=scenario.name,
            elapsed_sec=time.monotonic() - t0, error=str(e)[:200],
        )
    if res is None:
        return CellResult(
            variant=variant_name, scenario=scenario.name,
            elapsed_sec=time.monotonic() - t0, error="llm_web_search returned None",
        )

    companies = extract_companies_from_answer(res.answer)
    citations = [
        {"title": c.title, "url": c.url} for c in res.citations[:num_results]
    ]
    return CellResult(
        variant=variant_name, scenario=scenario.name,
        elapsed_sec=time.monotonic() - t0,
        raw_answer=res.answer,
        citations=citations,
        companies=companies,
    )


def build_markdown(results: list[CellResult], scenarios: list[Scenario], variants: list[str]) -> str:
    lines: list[str] = []
    lines.append("# Discovery research-arm prompt eval\n")

    # Summary table: variant x scenario, # companies extracted
    lines.append("## Companies extracted (per cell)\n")
    header = "| scenario \\\\ variant | " + " | ".join(variants) + " |"
    sep = "|---" * (len(variants) + 1) + "|"
    lines.append(header)
    lines.append(sep)
    for s in scenarios:
        cells = []
        for v in variants:
            cell = next(
                (r for r in results if r.variant == v and r.scenario == s.name),
                None,
            )
            if cell is None:
                cells.append("—")
            elif cell.error:
                cells.append(f"_err: {cell.error[:30]}_")
            else:
                cells.append(f"{len(cell.companies)} ({cell.elapsed_sec:.0f}s)")
        lines.append(f"| {s.name} | " + " | ".join(cells) + " |")
    lines.append("")

    # Per-cell company lists
    lines.append("## Detail per cell\n")
    for s in scenarios:
        lines.append(f"### scenario: `{s.name}`")
        intent_summary = []
        if s.guidance: intent_summary.append(f"guidance: '{s.guidance}'")
        if s.locations: intent_summary.append(f"locations: {s.locations}")
        if s.min_salary: intent_summary.append(f"min_salary: ${s.min_salary:,}")
        if not intent_summary: intent_summary = ["(profile-only)"]
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
                row.append(f"{inter}/{union} = {inter/union:.2f}")
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
    parser.add_argument("--variants", default=None, help="Comma-separated variant names; default all")
    parser.add_argument("--scenarios", default=None, help="Comma-separated scenario names; default all")
    parser.add_argument("--num-results", type=int, default=10)
    parser.add_argument("--output-dir", default=str(Path(__file__).resolve().parent / "output"))
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    await _hydrate_settings()
    profile = await _load_profile_data()
    print(f"Loaded profile — work_history={len(profile.get('work_history',[]))}, "
          f"accomplishments={len((profile.get('complete_profile') or {}).get('accomplishments', []))}")

    scen_keep = (
        {s.strip() for s in args.scenarios.split(",")}
        if args.scenarios else None
    )
    var_keep = (
        {v.strip() for v in args.variants.split(",")}
        if args.variants else None
    )
    selected_scenarios = [s for s in SCENARIOS if not scen_keep or s.name in scen_keep]
    selected_variants = [v for v in VARIANTS if not var_keep or v in var_keep]
    if not selected_scenarios or not selected_variants:
        print("No scenarios/variants matched.", file=sys.stderr)
        return

    print(f"Running {len(selected_scenarios)} scenarios × {len(selected_variants)} variants = "
          f"{len(selected_scenarios)*len(selected_variants)} cells")
    print(f"  scenarios: {[s.name for s in selected_scenarios]}")
    print(f"  variants:  {selected_variants}")
    print()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = out_dir / "discovery_research_eval.jsonl"
    md_path = out_dir / "discovery_research_eval.md"

    results: list[CellResult] = []
    with jsonl_path.open("w") as fh:
        # Within a scenario, run variants in parallel — they don't share state.
        # Across scenarios run sequentially so we don't ping the same Ashby / etc.
        for s in selected_scenarios:
            print(f"=== scenario: {s.name} ===")
            tasks = [run_one(v, s, profile, num_results=args.num_results) for v in selected_variants]
            t0 = time.monotonic()
            for coro in asyncio.as_completed(tasks):
                r = await coro
                results.append(r)
                fh.write(json.dumps({
                    "variant": r.variant,
                    "scenario": r.scenario,
                    "elapsed_sec": r.elapsed_sec,
                    "error": r.error,
                    "n_companies": len(r.companies),
                    "n_citations": len(r.citations),
                    "companies": r.companies,
                    "citations": r.citations,
                    "raw_answer": r.raw_answer,
                }) + "\n")
                fh.flush()
                summary = f"{len(r.companies):2d} co · {r.elapsed_sec:5.1f}s"
                if r.error:
                    summary = f"ERR: {r.error[:40]}"
                print(f"  {r.variant:36s} → {summary}")
            print(f"  scenario wall: {time.monotonic()-t0:.0f}s\n")

    md = build_markdown(results, selected_scenarios, selected_variants)
    md_path.write_text(md)
    print(f"\nWrote {jsonl_path}")
    print(f"Wrote {md_path}")


if __name__ == "__main__":
    asyncio.run(main())
