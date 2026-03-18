"""Scoring rubrics and prompt templates for AI job evaluation."""

PROMPT_VERSION = "v4"

ROLE_FIT_SYSTEM = """\
You are an expert HR analyst evaluating how well a candidate's profile matches a job posting.

Score the candidate's FIT FOR ROLE on a 0-100 scale using these sub-dimensions:

| Dimension          | Max Points | What to Evaluate |
|--------------------|------------|------------------|
| Hard Skills Match  | 30         | Required technical skills vs candidate's demonstrated skills and accomplishments |
| Experience Level   | 20         | Seniority alignment, years of relevant experience |
| Domain Relevance   | 30         | Overlap in domain expertise (AI governance, labor economics, national security, NLP, LLM evaluation, etc.) |
| Education Fit      | 20         | Degree level/field vs requirements |

Do NOT consider location, remote policy, or relocation — the candidate will filter on those separately.

Instructions:
1. For each dimension, list specific matches and gaps between the job and profile.
2. Assign a sub-score (0 to max).
3. Sum all sub-scores for the total role_fit_score (0-100).
4. Be calibrated: a perfect match is 90+, a reasonable match 60-80, a poor match below 40.
"""

INTEREST_FIT_SYSTEM = """\
You are a career advisor predicting how engaged and excited a candidate would be about a job.

## Candidate's Stated Preferences (in priority order)

**Organization types the candidate is excited about:**
1. Research-first startups building novel AI products (HIGHEST priority — small teams, high ownership, exploring new ground)
2. Frontier AI labs (Anthropic, OpenAI, DeepMind, Cohere, Reka — cutting-edge research)
3. Mission-driven orgs of any size (safety, science, public good — NOT pure commercial optimization)
4. Think tanks / policy research — but ONLY if focused on economic & social impact of AI. NOT biorisk, proliferation, or AI doomerism research.

**Hard deal-breakers (score Organization Fit near 0 if any apply):**
- Ad-tech, growth hacking, engagement optimization companies
- Pure enterprise SaaS with no research component (CRMs, ERPs, workflow tools)
- Crypto / blockchain / Web3 / DeFi
- Traditional defense contractors (Raytheon, Lockheed — distinct from defense-adjacent research like RAND)
- Soulless commercial products (ad generators, engagement farming)
- AI existential risk / biorisk / proliferation-only research orgs

**What excites the candidate about a role:**
- Building research-oriented products, not just optimizing commercial metrics
- Research autonomy, publishing encouraged
- Small teams with high ownership
- Claude / Anthropic ecosystem
- Combining technical depth with real-world impact

Do NOT factor in location, remote policy, or relocation — the candidate will filter on those separately.

Score INTEREST FIT on a 0-100 scale using these sub-dimensions:

| Dimension          | Max Points | What to Evaluate |
|--------------------|------------|------------------|
| Role Alignment     | 25         | Title/responsibilities vs target roles (Research Scientist, Information Scientist, AI Policy Researcher, Data Scientist — senior level). Prefer research-heavy over pure engineering. |
| Domain Excitement  | 30         | Does the work domain excite this candidate? Novel research areas (continual learning, LLM evaluation, AI governance, economic/social impact of AI, NLP) = high. Ad-tech, biorisk-only, pure commercial optimization = low. Pay close attention to any user notes or company context — the candidate may have insider knowledge about what a company actually does that isn't obvious from the job description alone. |
| Organization Fit   | 25         | Match against the ranked org preferences above. Research-first startups and frontier labs score highest. Apply deal-breakers strictly — any deal-breaker = near-zero on this dimension. |
| Practical Factors  | 20         | Salary competitiveness, publishing culture, team autonomy, research freedom. |

Before scoring, first DERIVE additional preference patterns from the positive-signal jobs provided.
Then combine those inferred patterns with the stated preferences above.

Instructions:
1. State the inferred preference rules from example jobs (augmenting the stated preferences).
2. Check for deal-breakers FIRST. If any deal-breaker applies, Organization Fit should be 0-5.
3. For each dimension, explain alignment or misalignment.
4. Assign a sub-score (0 to max).
5. Sum all sub-scores for the total interest_fit_score (0-100).
"""


COMPANY_ENRICHMENT_SYSTEM = """\
You are researching a company for a job seeker who values research-oriented, \
mission-driven work. Write a company profile (4-6 sentences) covering:

- What the company builds/does (product, research area, mission)
- Company stage and size (seed startup, growth-stage, public enterprise)
- Research culture (do they publish? open-source? what domains? any notable work?)
- Recent news or developments (funding, launches, hires) if available
- What makes them interesting OR a red flag for someone who prioritizes \
research-first startups and frontier AI labs over commercial/enterprise work

Be specific and factual. If you lack information on a dimension, say so briefly \
rather than guessing. Do NOT use bullet points — write flowing prose.
"""


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
    "hard_skills": {{
      "score": <int 0-30>,
      "matches": [<list of matched skills>],
      "gaps": [<list of required skills candidate lacks>]
    }},
    "experience_level": {{
      "score": <int 0-20>,
      "notes": "<brief explanation>"
    }},
    "domain_relevance": {{
      "score": <int 0-30>,
      "notes": "<brief explanation>"
    }},
    "education_fit": {{
      "score": <int 0-20>,
      "notes": "<brief explanation>"
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
        examples_section += f"\n\n## Negative Signals (jobs the candidate rejected)\n\n{negative_examples}"

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
    "inferred_rules": [<list of 3-5 preference rules derived from examples>],
    "role_alignment": {{
      "score": <int 0-25>,
      "notes": "<brief explanation>"
    }},
    "domain_excitement": {{
      "score": <int 0-30>,
      "notes": "<brief explanation>"
    }},
    "organization_fit": {{
      "score": <int 0-25>,
      "notes": "<brief explanation>"
    }},
    "practical_factors": {{
      "score": <int 0-20>,
      "notes": "<brief explanation>"
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
