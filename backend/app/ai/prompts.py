"""Scoring rubrics and prompt templates for AI job evaluation."""

PROMPT_VERSION = "v7"


def build_role_fit_system(profile_data: dict) -> str:
    """Build the role fit scoring system prompt dynamically from profile data."""
    domains = profile_data.get("domains", [])
    domain_str = ", ".join(domains) if domains else "the candidate's stated domains"

    return f"""\
You are an expert HR analyst evaluating how well a candidate's profile matches a job posting.

Score the candidate's FIT FOR ROLE on a 0-100 scale using these sub-dimensions:

| Dimension          | Max Points | What to Evaluate |
|--------------------|------------|------------------|
| Hard Skills Match  | 30         | Required technical skills vs candidate's demonstrated skills and accomplishments |
| Experience Level   | 20         | Seniority alignment, years of relevant experience |
| Domain Relevance   | 30         | Overlap in domain expertise ({domain_str}) |
| Education Fit      | 20         | Degree level/field vs requirements |

Domain scoring precision:
- "AI/ML" as a candidate domain means the candidate RESEARCHES AI/ML — a job that merely USES ML in another industry (healthcare, fintech, e-commerce) is that industry's domain, not AI/ML.
- Domain relevance = overlap in SUBJECT MATTER, not tools. A "Data Scientist at Stripe" doing fraud ML is fintech domain, not AI/ML domain.
- If job's primary domain differs from candidate's stated domains BUT skills/methods overlap heavily, score domain_relevance 18-22/30 (medium overlap). Only cap at 12-15/30 when both domain AND methods are unrelated.
- Policy/writing roles are NOT technical research even if the topic is AI. For non-technical roles where the candidate is a technical researcher, cap domain_relevance at 12/30.

Do NOT consider location, remote policy, or relocation — the candidate will filter on those separately.

If the job description is brief (under ~100 words of substantive detail):
- Missing info = uncertainty. Do NOT invent specific requirements that aren't stated.
- For brief descriptions, lean toward the middle of each calibration band (don't push to the high end), but do NOT impose a hard 50% cap if the basic role/domain are clearly stated.
- "ML Researcher at AI startup" with no specifics does NOT confirm PyTorch, NLP, LLM, or any specific skill.

Calibration anchors (treat as firm guardrails):
- 90-95: Exact skills, exact domain, exact seniority, exact education across the board.
- 80-89: Strong alignment with at most 1-2 minor gaps. Most required skills met, same or very close domain, seniority within range. Use this band liberally for genuinely strong matches — do NOT require perfection.
- 70-79: Same domain, most skills match, seniority within 1 level, possibly some moderate gaps.
- 55-69: Same domain but missing 2-3 key skills, OR adjacent domain with strong skill overlap.
- 40-54: Adjacent domain with partial overlap, OR right domain but wrong work type (e.g., policy writing vs technical research).
- 20-39: Limited overlap on either domain or skills, OR significant over/underqualification.
- 0-19: Unrelated domain AND no meaningful skill overlap.

Seniority rules (STRICT):
- Job targets 3+ levels below candidate (e.g., "Junior Analyst" for PhD + 5yr): experience_level = 0-3.
- Job targets 2 levels below (e.g., mid-level for Senior candidate): experience_level = 4-8.
- Overqualification is a NEGATIVE signal — being too senior means poor retention fit.

Instructions:
1. List specific reasons this job is NOT a perfect fit (up to 3, fewer is fine if there are no significant issues). Consider: missing skills, seniority mismatch, domain gap, overqualification, wrong work type. Be honest — if it really is a strong fit, say so instead of manufacturing criticism.
2. For each dimension, list specific matches AND gaps. If a dimension genuinely has no significant gap, say "no significant gap" — do NOT invent one to justify a deduction.
3. Score using a PENALTY-BASED approach: start at the max for each dimension, then DEDUCT points for each REAL gap. State each deduction explicitly (e.g., "Deduct 10: no LLM experience required"). A dimension with no real gaps should keep its full max — do not deduct just to feel critical.
4. After each sub-score, state in one sentence why this was the score.
5. Sum all sub-scores for the total role_fit_score (0-100).
6. Verify the arithmetic sum matches your reported total.
"""


def build_interest_fit_system(profile_data: dict) -> str:
    """Build the interest fit scoring system prompt dynamically from profile data."""
    prefs = profile_data.get("search_preferences", {})
    target_roles = profile_data.get("target_roles", [])
    domains = profile_data.get("domains", [])

    # Build "what candidate is looking for" section — prefer free-text, fall back to legacy
    looking_for = prefs.get("looking_for", "")
    positive_signals = prefs.get("positive_signals", [])
    if looking_for:
        looking_section = looking_for
        if positive_signals:
            looking_section += f"\n\nSupplementary keywords: {', '.join(positive_signals)}"
    else:
        # Legacy fallback
        industries = prefs.get("industries_ranked", [])
        nice_to_haves = prefs.get("nice_to_haves", [])
        parts = []
        if industries:
            for i, ind in enumerate(industries, 1):
                priority = " (HIGHEST priority)" if i == 1 else ""
                parts.append(f"{i}. {ind}{priority}")
        if nice_to_haves:
            parts.append("Excited by: " + ", ".join(nice_to_haves))
        looking_section = "\n".join(parts) if parts else "No specific preferences stated."

    # Build "what candidate wants to AVOID" section — prefer free-text, fall back to legacy
    not_looking_for = prefs.get("not_looking_for", "")
    exclusions = prefs.get("exclusions", [])
    if not_looking_for:
        avoid_section = not_looking_for
        if exclusions:
            avoid_section += f"\n\nHard exclusion keywords: {', '.join(exclusions)}"
    else:
        # Legacy fallback
        deal_breakers = prefs.get("deal_breakers", [])
        anti_patterns = prefs.get("org_anti_patterns", [])
        avoid_parts = [f"- {db}" for db in deal_breakers] + [f"- {ap}" for ap in anti_patterns]
        avoid_section = "\n".join(avoid_parts) if avoid_parts else "No specific avoidances stated."

    # Build target roles description
    if target_roles:
        role_names = [f"{r.get('title', '')} ({r.get('seniority', '')})" for r in target_roles]
        roles_str = ", ".join(role_names)
    else:
        roles_str = "roles matching the candidate's profile"

    # Build domain excitement list
    if domains:
        domain_str = ", ".join(domains)
    else:
        domain_str = "the candidate's stated domains"

    # Build salary benchmark from profile data
    salary_min = prefs.get("salary_minimum")
    if salary_min and isinstance(salary_min, (int, float)) and salary_min > 0:
        competitive = int(salary_min * 1.2)
        below = int(salary_min)
        sig_below = int(salary_min * 0.7)
        salary_guidance = (
            f"Salary competitiveness (benchmark: ${competitive:,}+ is competitive for this candidate, "
            f"${sig_below:,}-${below:,} is below market, <${sig_below:,} is significantly below)"
        )
    else:
        salary_guidance = (
            "Salary competitiveness (no specific salary benchmark provided — "
            "evaluate relative to the role's typical market rate)"
        )

    # Build role alignment guidance from target roles (no hardcoded research preference)
    role_types = [r.get("title", "").lower() for r in target_roles]
    is_research_oriented = any(
        kw in rt for rt in role_types for kw in ("research", "scientist", "analyst")
    )
    if is_research_oriented:
        role_guidance = (
            f"Title/responsibilities vs target roles ({roles_str}). "
            "Research-oriented roles align better with this candidate's targets. "
            "Pure infrastructure/platform roles with no research component score lower."
        )
    else:
        role_guidance = (
            f"Title/responsibilities vs target roles ({roles_str}). "
            "Evaluate alignment with the candidate's stated role targets."
        )

    return f"""\
You are a career advisor predicting how engaged and excited a candidate would be about a job.

## Candidate's Stated Preferences

**What the candidate is looking for:**
{looking_section}

**What the candidate wants to AVOID (score Organization Fit near 0 if any apply):**
{avoid_section}

Do NOT factor in location, remote policy, or relocation — the candidate will filter on those separately.

If the job description is brief (under ~100 words of substantive detail):
- Missing info means you CANNOT confirm excitement. Absence of negative signals ≠ positive signals.
- For brief descriptions, lean toward the middle of each calibration band rather than the high end. Do NOT impose a hard 40% cap if the basic role/domain are clearly stated.
- Do NOT assume a vague posting has publishing culture, research freedom, or competitive salary.

Score INTEREST FIT on a 0-100 scale using these sub-dimensions:

| Dimension          | Max Points | What to Evaluate |
|--------------------|------------|------------------|
| Role Alignment     | 25         | {role_guidance} |
| Domain Excitement  | 30         | Does the work domain excite this candidate? Domains of interest: {domain_str}. Pay close attention to any user notes or company context — the candidate may have insider knowledge about what a company actually does that isn't obvious from the job description alone. |
| Organization Fit   | 25         | Match against the candidate's preferences above. Apply avoidances strictly — any avoidance match = near-zero on this dimension. |
| Practical Factors  | 20         | {salary_guidance}. Consider team autonomy, growth potential, and alignment with the candidate's stated values and nice-to-haves. |

Calibration anchors (treat as firm guardrails):
- 90-95: Exact target role, exact domain, all positive signals, strong practical factors across the board.
- 80-89: Strong alignment across most dimensions, with at most 1-2 minor concerns. Use this band for genuinely exciting roles — do NOT require perfection.
- 70-79: Right domain, close role type, most practical factors positive, possibly some moderate gaps.
- 55-69: Right domain but wrong role type, OR right role but adjacent domain.
- 40-54: Partial domain overlap, mixed signals, some practical concerns.
- 20-39: Limited overlap, weak signals, no clear positive case.
- 0-19: Avoidance/exclusion match, OR completely unrelated field. When skills share essentially nothing, total MUST be in this range.

Before scoring, first DERIVE additional preference patterns from the positive-signal jobs provided.
Then combine those inferred patterns with the stated preferences above.

Instructions:
1. State the inferred preference rules from example jobs.
2. Check avoidances/exclusions FIRST. If any avoidance applies, Organization Fit = 0-5 and set deal_breaker_detected to true. (This rule remains STRICT — do not soften it.)
3. List specific reasons this job would NOT excite this candidate (up to 3, fewer is fine if there are no significant issues). Consider: wrong role type, wrong domain, misaligned values, poor practical factors. Be honest — if it really would excite the candidate, say so instead of manufacturing concerns.
4. For each dimension, explain alignment AND misalignment. If a dimension genuinely has no significant misalignment, say "no significant misalignment" — do NOT invent one.
5. Score using a PENALTY-BASED approach: start at the max, DEDUCT for each REAL misalignment. A dimension with no real concerns should keep its full max.
6. After each sub-score, state in one sentence why this was the score.
7. Sum all sub-scores for the total interest_fit_score (0-100).
8. Verify the arithmetic sum matches your reported total.
"""


def build_company_enrichment_system(profile_data: dict) -> str:
    """Build the company enrichment system prompt dynamically from profile data."""
    prefs = profile_data.get("search_preferences", {})
    looking_for = prefs.get("looking_for", "")

    if looking_for:
        pref_str = looking_for[:100]
    elif prefs.get("industries_ranked"):
        pref_str = " and ".join(prefs["industries_ranked"][:3])
    else:
        pref_str = "research-oriented, mission-driven work"

    return f"""\
You are researching a company for a job seeker who values {pref_str}. \
Write a company profile (4-6 sentences) covering:

- What the company builds/does (product, research area, mission)
- Company stage and size (seed startup, growth-stage, public enterprise)
- Research culture (do they publish? open-source? what domains? any notable work?)
- Recent news or developments (funding, launches, hires) if available
- What makes them interesting OR a red flag for this candidate's preferences

Be specific and factual. If you lack information on a dimension, say so briefly \
rather than guessing. Do NOT use bullet points — write flowing prose.
"""


# Legacy constants for backward compatibility — callers should migrate to builder functions
ROLE_FIT_SYSTEM = build_role_fit_system({})
INTEREST_FIT_SYSTEM = build_interest_fit_system({})
COMPANY_ENRICHMENT_SYSTEM = build_company_enrichment_system({})


def build_role_fit_prompt(profile_yaml: str, job_text: str) -> list[dict]:
    """Build messages for role fit scoring."""
    return [
        {
            "role": "user",
            "content": f"""## Candidate Profile

{profile_yaml}

---

## Job Posting to Evaluate

{job_text}

---

Evaluate this job against the candidate profile using the role fit rubric.

Respond with ONLY valid JSON (no markdown fences) in this exact structure:
{{
  "role_fit": {{
    "score": <int 0-100>,
    "not_perfect_because": ["<3 reasons this is NOT a perfect fit>"],
    "hard_skills": {{
      "score": <int 0-30>,
      "matches": [<list of matched skills>],
      "gaps": [<list of required skills candidate lacks>],
      "deductions": "<points deducted and why>"
    }},
    "experience_level": {{
      "score": <int 0-20>,
      "notes": "<brief explanation>",
      "deductions": "<points deducted and why>"
    }},
    "domain_relevance": {{
      "score": <int 0-30>,
      "notes": "<brief explanation>",
      "deductions": "<points deducted and why>"
    }},
    "education_fit": {{
      "score": <int 0-20>,
      "notes": "<brief explanation>",
      "deductions": "<points deducted and why>"
    }}
  }}
}}""",
        }
    ]


def build_interest_fit_prompt(
    profile_yaml: str,
    job_text: str,
    positive_examples: str,
    negative_examples: str | None = None,
) -> list[dict]:
    """Build messages for interest fit scoring."""
    examples_section = f"## Positive Interest Signals (jobs the candidate actively sought out)\n\n{positive_examples}"
    if negative_examples:
        examples_section += (
            f"\n\n## Negative Signals (jobs the candidate rejected)\n\n{negative_examples}"
        )

    return [
        {
            "role": "user",
            "content": f"""## Candidate Profile & Preferences

{profile_yaml}

---

{examples_section}

---

## Job Posting to Evaluate

{job_text}

---

First derive preference rules from the example jobs, then evaluate this job's interest fit.

Respond with ONLY valid JSON (no markdown fences) in this exact structure:
{{
  "interest_fit": {{
    "score": <int 0-100>,
    "deal_breaker_detected": <bool>,
    "deal_breaker_reason": "<string or null — which avoidance/exclusion matched>",
    "not_exciting_because": ["<3 reasons this would NOT excite the candidate>"],
    "inferred_rules": [<list of 3-5 preference rules derived from examples>],
    "role_alignment": {{
      "score": <int 0-25>,
      "notes": "<brief explanation>",
      "deductions": "<points deducted and why>"
    }},
    "domain_excitement": {{
      "score": <int 0-30>,
      "notes": "<brief explanation>",
      "deductions": "<points deducted and why>"
    }},
    "organization_fit": {{
      "score": <int 0-25>,
      "notes": "<brief explanation>",
      "deductions": "<points deducted and why>"
    }},
    "practical_factors": {{
      "score": <int 0-20>,
      "notes": "<brief explanation>",
      "deductions": "<points deducted and why>"
    }}
  }}
}}""",
        }
    ]


def _truncate_description(text: str, max_chars: int = 4000) -> str:
    """Truncate long descriptions, keeping first 3500 + last 500 chars."""
    if len(text) <= max_chars:
        return text
    return text[:3500] + "\n\n[...truncated...]\n\n" + text[-500:]


def format_job_for_scoring(job, company_notes: str | None = None) -> str:
    """Format a Job ORM object into text for the scoring prompt.

    Includes user_notes and company notes as additional context that may
    contain insider knowledge about the company/role not in the description.
    """
    display_title = getattr(job, "display_title", None) or job.title
    display_company = getattr(job, "display_company", None) or job.company
    parts = [
        f"Title: {display_title}",
        f"Company: {display_company}",
    ]
    # Include team name if extracted (helps contextualize roles at large orgs)
    team_name = (job.extra_metadata or {}).get("team_name")
    if team_name:
        parts.append(f"Team: {team_name}")
    if job.salary_min or job.salary_max:
        salary = ""
        if job.salary_min:
            salary += f"${job.salary_min:,}"
        if job.salary_max:
            salary += f" - ${job.salary_max:,}"
        parts.append(f"Salary: {salary}")
    description = _truncate_description(job.description) if job.description else ""
    parts.append(f"\nDescription:\n{description}")

    # Include user notes — these are the candidate's own observations about
    # the company/role from conversations, blog posts, talks, etc.
    if job.user_notes:
        parts.append(f"\nCandidate's notes (insider context — weight heavily):\n{job.user_notes}")

    # Include company-level notes if available
    if company_notes:
        parts.append(f"\nCompany context:\n{company_notes}")

    return "\n".join(parts)


def format_example_jobs(jobs: list) -> str:
    """Format a list of thumbed jobs as few-shot examples.

    Includes existing scores when available for calibration context.
    """
    if not jobs:
        return "No example jobs available."
    examples = []
    for i, job in enumerate(jobs, 1):
        parts = [f"{i}. {job.title} at {job.company}"]
        if job.location:
            parts[0] += f" ({job.location})"
        if job.salary_min or job.salary_max:
            salary = ""
            if job.salary_min:
                salary += f"${job.salary_min:,}"
            if job.salary_max:
                salary += f" - ${job.salary_max:,}"
            parts.append(f"   Salary: {salary}")
        if job.remote:
            parts.append("   Remote: Yes")
        # Include existing scores for calibration
        if job.role_fit_score is not None or job.interest_fit_score is not None:
            score_parts = []
            if job.role_fit_score is not None:
                score_parts.append(f"role_fit={job.role_fit_score}")
            if job.interest_fit_score is not None:
                score_parts.append(f"interest_fit={job.interest_fit_score}")
            parts.append(f"   Prior scores: {', '.join(score_parts)}")
        # Include a brief description snippet
        desc = job.description[:300] if job.description else ""
        if desc:
            parts.append(f"   Description snippet: {desc}...")
        if job.user_notes:
            parts.append(f"   Candidate notes: {job.user_notes}")
        examples.append("\n".join(parts))
    return "\n\n".join(examples)
