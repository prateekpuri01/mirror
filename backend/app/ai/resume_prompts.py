"""Prompt templates and builders for AI-powered tailored resume generation."""

RESUME_PROMPT_VERSION = "resume_v1"

RESUME_SYSTEM = """\
You are an expert resume strategist specializing in senior research, AI, and data science roles.

## CRITICAL RULE
You MUST only use facts, accomplishments, skills, and publications from the provided profile \
and accomplishments catalog. NEVER fabricate, invent, or embellish any achievement, metric, \
publication, or skill. If the profile doesn't contain enough relevant material, use fewer \
bullets rather than inventing content.

## Resume Section Specifications

### tagline
Generate 3-4 keywords separated by " · " that mirror the target job's domain language. \
These replace the static tagline on the base resume. Example: "AI Safety Evaluation · LLM Systems · National Security · Policy Research"

### summary
Rewrite the candidate's professional summary in 2-3 sentences to emphasize the intersection \
of the candidate's strongest skills and the target role's requirements. Be specific and \
quantitative where possible.

### selected_research
Pick exactly 3 accomplishments whose skills/domain best overlap the target job. For each:
- `category_label`: 2-4 word label relevant to the job (e.g., "AI SAFETY", "LABOR ECONOMICS"). \
Do NOT use RAND-internal framing like "INSTITUTE CHALLENGE 1". Use job-relevant category labels.
- `title`: The accomplishment title
- `description`: 2-3 sentences highlighting the most job-relevant aspects
- `accomplishment_id`: The id from the accomplishments catalog (for audit trail)

### experience
3 employer blocks with specific bullet counts:
- RAND Corporation: 4-5 bullets
- FINRA: 2 bullets
- UCLA: 1 bullet

Each bullet must:
- Start with a strong, specific action verb (Designed, Built, Led, Deployed, Evaluated, Developed, Analyzed)
- Include quantitative specifics from the `quantitative_specifics` field of accomplishments
- Be 1-2 lines maximum
- Rewrite to emphasize job-relevant aspects WITHOUT changing any facts or numbers
- Include `accomplishment_ids` listing which accomplishments the bullets draw from

### publications
Select 4-6 most relevant publications. Prioritize:
1. First-author publications
2. Topic alignment with the target job
3. Higher relevance_weight
Format each as a complete citation string.

### technical_skills
4 categories: ai_systems, data_science, engineering, communication.
- Reorder within each category to lead with job-relevant skills
- You may omit clearly irrelevant skills but NEVER add skills not in the profile
- Keep the same skill names — don't rename them

### awards
Single line with the most relevant awards separated by " · ". Pick awards that \
reinforce the candidate's fit for the target role.

## Tone and Style
- Strong specific action verbs: Designed, Built, Led, Deployed, Evaluated, Developed, Analyzed
- Quantify everything — always use numbers from `quantitative_specifics`
- Mirror the job posting's language where natural
- Professional and concise. No "passionate about", "leveraged synergies", or filler
- Every bullet must pass the "so what?" test — state the impact, not just the activity

## Length Constraint
The resume MUST fit 2 pages. This means:
- Total 8-10 experience bullets across all employers (RAND 4-5, FINRA 2, UCLA 1)
- Summary: 2-3 sentences max
- Selected Research: exactly 3 entries with short descriptions
- Publications: 4-6 entries
- Technical Skills: 4 compact categories
- Awards: 1 line

## Output Format
Respond with ONLY valid JSON (no markdown fences). Follow the exact schema specified in the user message.
"""


def build_full_profile_for_resume(data: dict) -> str:
    """Build the full profile text for resume generation.

    Unlike _build_compact_profile in scoring.py which sends top-12 accomplishments,
    this sends ALL accomplishments and ALL publications so the LLM can pick the
    best matches for the specific job.
    """
    lines = []

    # Base profile fields
    personal = data.get("personal", {})
    lines.append(f"Name: {personal.get('name', 'Unknown')}")
    lines.append(f"Location: {personal.get('location', 'Unknown')}")
    lines.append(f"Experience: {data.get('experience_years', 'N/A')} years post-PhD")
    lines.append("")

    # Target roles
    target_roles = data.get("target_roles", [])
    if target_roles:
        roles = [f"{r['title']} ({r.get('seniority', '')})".strip() for r in target_roles]
        lines.append(f"Target roles: {', '.join(roles)}")

    # Domains
    domains = data.get("domains", [])
    if domains:
        lines.append(f"Domains: {', '.join(domains)}")
    lines.append("")

    # Skills (full)
    skills = data.get("skills", {})
    if skills.get("technical"):
        tech = skills["technical"]
        if isinstance(tech, list):
            lines.append(f"Technical skills: {', '.join(tech)}")
        else:
            lines.append(f"Technical skills: {tech}")
    if skills.get("communication"):
        comm = skills["communication"]
        if isinstance(comm, list):
            lines.append(f"Communication: {', '.join(comm)}")
        else:
            lines.append(f"Communication: {comm}")
    if skills.get("tools"):
        tools = skills["tools"]
        if isinstance(tools, list):
            lines.append(f"Tools: {', '.join(tools)}")
        else:
            lines.append(f"Tools: {tools}")
    lines.append("")

    # Work history
    lines.append("Work history:")
    for job in data.get("work_history", []):
        end = job.get("end") or "present"
        lines.append(f"  - {job['title']} at {job['employer']} ({job['start']} to {end})")
    lines.append("")

    # Education
    lines.append("Education:")
    for edu in data.get("education", []):
        honors = f" - {edu['honors']}" if edu.get("honors") else ""
        lines.append(
            f"  - {edu['degree']} {edu['field']}, {edu['institution']} ({edu['year']}){honors}"
        )
    lines.append("")

    # Awards (full)
    awards = data.get("awards", [])
    if awards:
        lines.append(f"Awards: {'; '.join(awards)}")
        lines.append("")

    # ALL accomplishments from complete profile
    complete = data.get("complete_profile", {})
    accomplishments = complete.get("accomplishments", [])
    if accomplishments:
        lines.append(f"ALL ACCOMPLISHMENTS ({len(accomplishments)} total):")
        lines.append("")
        for a in accomplishments:
            lines.append(f"  ID: {a.get('id', 'unknown')}")
            lines.append(f"  Employer: {a.get('employer', '')}")
            lines.append(f"  Title: {a.get('title', '')}")
            lines.append(f"  Date: {a.get('date_range', '')}")
            if a.get("impact_summary"):
                summary = a["impact_summary"].strip().replace("\n", " ")
                lines.append(f"  Impact: {summary}")
            quants = a.get("quantitative_specifics", [])
            if quants:
                lines.append(f"  Metrics: {'; '.join(quants)}")
            so_what = a.get("so_what", "")
            if so_what:
                lines.append(f"  So what: {so_what.strip().replace(chr(10), ' ')}")
            skills_demo = a.get("skills_demonstrated", [])
            if skills_demo:
                lines.append(f"  Skills: {', '.join(skills_demo)}")
            lines.append("")

    # ALL publications
    publications = complete.get("publications", [])
    if publications:
        lines.append(f"ALL PUBLICATIONS ({len(publications)} total):")
        lines.append("")
        for p in publications:
            pub_id = p.get("id", p.get("title", "unknown")[:30].lower().replace(" ", "-"))
            fa = " [FIRST AUTHOR]" if p.get("first_author") else ""
            lines.append(f"  ID: {pub_id}")
            lines.append(
                f"  {', '.join(p.get('authors', []))}. "
                f"\"{p.get('title', '')}\" {p.get('venue', '')}, {p.get('year', '')}.{fa}"
            )
            if p.get("abstract"):
                abstract = p["abstract"].strip().replace("\n", " ")[:200]
                lines.append(f"  Abstract: {abstract}")
            if p.get("skills_demonstrated"):
                lines.append(f"  Skills: {', '.join(p['skills_demonstrated'])}")
            lines.append("")

    return "\n".join(lines)


def build_resume_prompt(
    profile_text: str,
    job_text: str,
    score_rationale: dict | None = None,
    company_notes: str | None = None,
) -> list[dict]:
    """Build the user message for resume generation."""
    parts = [
        "## Candidate Profile & Accomplishments\n",
        profile_text,
        "\n---\n",
        "## Target Job Posting\n",
        job_text,
    ]

    if company_notes:
        parts.append(f"\n\n## Company Context\n{company_notes}")

    if score_rationale:
        # Include scoring rationale to give the LLM a head start on what matters
        role_fit = score_rationale.get("role_fit", {})
        interest_fit = score_rationale.get("interest_fit", {})
        parts.append("\n\n## AI Scoring Context (from prior evaluation)")
        if role_fit:
            hard_skills = role_fit.get("hard_skills", {})
            if hard_skills.get("matches"):
                parts.append(f"Skill matches: {', '.join(hard_skills['matches'])}")
            if hard_skills.get("gaps"):
                parts.append(f"Skill gaps: {', '.join(hard_skills['gaps'])}")
            domain = role_fit.get("domain_relevance", {})
            if domain.get("notes"):
                parts.append(f"Domain relevance: {domain['notes']}")
        if interest_fit:
            domain_exc = interest_fit.get("domain_excitement", {})
            if domain_exc.get("notes"):
                parts.append(f"Domain excitement: {domain_exc['notes']}")

    parts.append("""

---

Generate a tailored resume for this candidate targeting the above job posting.

Respond with ONLY valid JSON (no markdown fences) in this exact structure:
{
  "tagline": "Keyword1 · Keyword2 · Keyword3 · Keyword4",
  "summary": "2-3 sentence summary emphasizing fit for this specific role",
  "selected_research": [
    {
      "category_label": "CATEGORY LABEL",
      "title": "Accomplishment title",
      "description": "2-3 sentences highlighting job-relevant aspects",
      "accomplishment_id": "id-from-catalog"
    }
  ],
  "experience": {
    "rand": {
      "bullets": ["bullet 1", "bullet 2", "bullet 3", "bullet 4"],
      "accomplishment_ids": ["id1", "id2"]
    },
    "finra": {
      "bullets": ["bullet 1", "bullet 2"],
      "accomplishment_ids": ["id1"]
    },
    "ucla": {
      "bullets": ["bullet 1"],
      "accomplishment_ids": ["id1"]
    }
  },
  "publications": [
    {"citation": "Authors. Title. Venue, Year.", "publication_id": "pub-id-or-title-slug"}
  ],
  "technical_skills": {
    "ai_systems": "LLM evaluation & fine-tuning, RAG pipelines, ...",
    "data_science": "...",
    "engineering": "...",
    "communication": "..."
  },
  "awards": "Award1 · Award2 · ...",
  "tailoring_rationale": "Brief explanation of tailoring choices made for this job"
}""")

    return [{"role": "user", "content": "\n".join(parts)}]


# ---------------------------------------------------------------------------
# Revision prompt
# ---------------------------------------------------------------------------

RESUME_REVISION_SYSTEM = """\
You are an expert resume strategist revising a tailored resume based on user feedback.

## CRITICAL RULES
1. You MUST only use facts, accomplishments, skills, and publications from the provided \
profile and accomplishments catalog. NEVER fabricate.
2. Apply the user's revision instruction precisely. Change only what they ask for — \
preserve everything else.
3. If the user asks to swap accomplishments, pull replacements from the full catalog provided.
4. Maintain the same JSON output schema as the original resume.
5. The resume MUST still fit 2 pages after revisions.

## Output Format
Respond with ONLY valid JSON (no markdown fences) using the same schema as the original resume.
"""


def build_revision_prompt(
    current_resume_json: str,
    instruction: str,
    profile_text: str,
    job_text: str,
) -> list[dict]:
    """Build messages for resume revision."""
    return [
        {
            "role": "user",
            "content": f"""## Current Resume (JSON)

```json
{current_resume_json}
```

---

## Revision Instruction

{instruction}

---

## Full Profile & Accomplishments (for pulling in new content)

{profile_text}

---

## Target Job Posting (for context)

{job_text}

---

Apply the revision instruction to the current resume. Output the COMPLETE updated resume \
as valid JSON (no markdown fences) using the exact same schema. Only change what the \
instruction asks for — preserve everything else.""",
        },
    ]
